"""In-memory session store + state machine for the full Orkestr webapp.

Seeded with a default demo session (ORK-001) so every screen has data on boot.
Hackathon approach: REAL endpoints, SEEDED data underneath — swap the stubs for
real negotiation/constraint logic per the Build Guide without touching the API.

Owner: Lucas (session plumbing) + Jaydon (the agent calls).
"""
import json
import logging
import os
import threading
import time
import uuid

from fastapi import HTTPException

from agents.convener import (generate_candidates, run_negotiation, strike_booking,
                             _seeded_messages)
from core import db
from core.settlement import compute_net, simplify
from payments.x402 import stamp

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
PHASES = ["negotiating", "plan_ready", "booking", "confirmed"]

LOGGER = logging.getLogger(__name__)


def _load(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


def get(sid):
    return db.get_session(sid)


def delete_session(sid):
    """Remove a session entirely from the store (used by the dev reseed route)."""
    db.delete_session(sid)


def _members(s):
    return s["members"] or _load("personas.json")


def new_session(name=None, date_range=None):
    sid = "ORK-" + uuid.uuid4().hex[:4].upper()
    s = {
        "session_id": sid,
        "name": name or "Friday plan",
        "date_range": date_range,
        "phase": "negotiating",
        "members": [],
        "negotiation": [],
        "plan": None,
        "handshake": None,
        "approvals": [],
        "expenses": [],
        "settlement": None,
    }
    db.set_session(sid, s)  # write-through AFTER building the new session
    return s


def new_live_session(name=None, date_range=None):
    """A real (non-seeded) session pre-populated with 5 member *identities* only.

    The group exists from the start (names/avatars), but every constraint is
    entered live and each member must press find-our-plan before the pipeline
    runs. Marked live=True so the status gate waits for all 5 (see all_ready).
    """
    s = new_session(name or "Live plan", date_range)
    shells = []
    for p in _load("personas.json")[:5]:
        shells.append({
            "id": p["id"],
            "name": p.get("name", p["id"]),
            "avatar": p.get("avatar"),
            "constraints": {},
            "freeform": "",
        })
    s["members"] = shells
    s["live"] = True
    s["ready"] = []
    db.set_session(s["session_id"], s)  # write-through AFTER mutation
    return s


def clone_session(sid, name=None, date_range=None):
    """Clone a settled session's crew into a fresh live session for the next outing.

    Copies ONLY the member id + name (+ avatar) list — NOT their constraints. A
    returning member's constraints come from group memory when they re-submit, so
    the crew is preserved but everyone plans fresh. The new session behaves like a
    live session (empty constraints, ready=[]). ORK-001 is refused (frozen demo);
    an unknown sid 404s. Returns the new session dict.
    """
    if sid == "ORK-001":
        raise HTTPException(
            status_code=400,
            detail="ORK-001 is the frozen seeded demo; cloning is disabled for it")
    src = db.get_session(sid)
    if src is None:
        raise HTTPException(status_code=404, detail="session not found")
    s = new_session(name or src.get("name"), date_range or src.get("date_range"))
    shells = []
    for m in src.get("members", []):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        shells.append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "avatar": m.get("avatar"),
            "constraints": {},  # fresh plan — constraints come from group memory
            "freeform": "",
        })
    s["members"] = shells
    s["live"] = True
    s["ready"] = []
    db.set_session(s["session_id"], s)  # write-through AFTER mutation
    return s


def mark_ready(sid, person_id):
    """Record that one member pressed find-our-plan."""
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not person_id:
        raise HTTPException(status_code=400, detail="person_id required")
    if person_id not in [m["id"] for m in s["members"]]:
        raise HTTPException(status_code=400, detail="unknown person_id")
    if person_id not in s.setdefault("ready", []):
        s["ready"].append(person_id)
    db.set_session(sid, s)  # write-through AFTER mutation
    return s


def all_ready(s):
    """True once every member of a live session has pressed find-our-plan."""
    member_ids = {m["id"] for m in s.get("members", [])}
    if not member_ids:
        return False
    return member_ids.issubset(set(s.get("ready", [])))


def add_constraints(sid, person):
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    member_id = person.get("id")
    if not member_id:
        raise HTTPException(status_code=400, detail="id required")
    # Group memory pre-population: a returning member who is NEW to this session
    # and submits NO constraints is auto-filled from their stored prefs, so they
    # only confirm instead of re-entering. A member who submits real constraints
    # is left untouched — fresh input always wins over stale memory.
    is_new_to_session = member_id not in [m["id"] for m in s["members"]]
    if is_new_to_session and not person.get("constraints"):
        remembered = db.get_member_prefs(member_id)
        if remembered:
            person["constraints"] = remembered.get("constraints") or {}
            if not person.get("name"):
                person["name"] = remembered.get("name")
            if not person.get("freeform"):
                person["freeform"] = remembered.get("freeform") or ""
    s["members"] = [m for m in s["members"] if m["id"] != member_id] + [person]
    # New constraints invalidate any prior failure so an "Adjust constraints &
    # retry" actually recomputes. Without this the phase stays "failed" and
    # /api/status returns the stale fail_reason no matter what changed.
    if s.get("phase") == "failed":
        s["phase"] = "negotiating"
        s["booking_failed"] = False
        s["fail_reason"] = None
        s["plan"] = None
        s["negotiation"] = None
    db.set_session(sid, s)  # write-through AFTER mutation
    # Persist this member's (possibly just-populated) constraints to group memory.
    db.save_member_prefs(member_id, person.get("name"),
                         person.get("constraints") or {}, person.get("freeform") or "")
    return s


def get_member_prefs(member_id):
    """Stored group-memory prefs for a member (or None). Used by the prefs API."""
    return db.get_member_prefs(member_id)


def _first_constraint_value(members, key, fallback=None):
    """First non-empty constraints[key] across ALL members, else fallback."""
    for m in members:
        if not isinstance(m, dict):
            continue
        val = (m.get("constraints") or {}).get(key)
        if val:
            return val
    return fallback


def _maybe_fire_x402_receipt(s, sid):
    """Best-effort: fire ONE real x402 demo receipt after a successful booking and
    store it as s["x402_receipt"]. Fully guarded and exception-safe — a payment
    failure NEVER crashes the plan. Designed to run in a daemon thread: because it
    executes AFTER the caller has already returned its response, it writes the
    receipt through itself (db.set_session) rather than relying on the caller's
    write. Guards (skip silently if any fails):
      1. sid != "ORK-001"                                (never the frozen demo)
      2. ENABLE_X402_DEMO_RECEIPT == exactly "true"
      3. X402_DEMO_RECEIPT_SECRET set and non-empty
      4. plan succeeded (s["plan"] set, booking_failed is not True)
      5. no valid x402_receipt already stored              (don't double-fire)
    """
    try:
        if sid == "ORK-001":
            return
        if not s.get("plan") or s.get("booking_failed") is True:
            return
        existing = s.get("x402_receipt")
        if isinstance(existing, dict) and existing.get("txHash"):
            return
        from payments import x402_demo_receipt as XDR
        if not XDR.is_demo_receipt_enabled():          # guard 2 (exact "true")
            return
        secret = os.environ.get("X402_DEMO_RECEIPT_SECRET", "").strip()
        if not secret:                                 # guard 3
            return
        status_code, body = XDR.run_demo_receipt(secret, XDR.CONFIRM_PHRASE)
        # 429 = cooldown active (a prior session just fired). We are in a daemon
        # thread AFTER the response was sent, so we can wait out the cooldown and
        # retry ONCE — this makes back-to-back sessions each get a real receipt
        # without weakening the cooldown protection itself.
        if status_code == 429:
            remaining = body.get("cooldown_remaining_seconds") if isinstance(body, dict) else None
            if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
                if remaining > 59:  # guard: never sleep longer than ~a minute
                    LOGGER.warning("x402 auto-receipt: cooldown %ss too long, skipping for %s",
                                   remaining, sid)
                    return
                wait = int(remaining) + 1
            else:
                wait = 35  # cooldown_remaining not provided — fixed safe fallback
            LOGGER.info("x402 auto-receipt: cooldown active, retrying once in %ss for %s",
                        wait, sid)
            time.sleep(wait)
            status_code, body = XDR.run_demo_receipt(secret, XDR.CONFIRM_PHRASE)  # retry ONCE
        if status_code == 200 and isinstance(body, dict) and body.get("txHash"):
            s["x402_receipt"] = body
            # Runs after the caller's response/write — persist the receipt ourselves.
            db.set_session(sid, s)
        else:
            LOGGER.warning("x402 auto-receipt not stored (status=%s) for %s", status_code, sid)
    except Exception as e:
        LOGGER.warning("x402 auto-receipt failed for %s: %s", sid, e)


def generate_real_plan(sid):
    """Real-venue plan path for a non-seeded session — BYPASSES the convener.

    Searches Google Places for a venue matching the group's location/cuisine and
    builds a plan carrying the FROZEN venue shape {name, address, lat, lng, tags}.
    Never touches ORK-001 (refused with 400) or compute_plan. Returns the plan.
    """
    if sid == "ORK-001":
        raise HTTPException(
            status_code=400,
            detail="ORK-001 is the frozen seeded demo; real venue search is disabled for it")
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    members = s.get("members") or []
    if not members:
        raise HTTPException(status_code=400, detail="session has no members with constraints yet")

    # Location priority: constraints.location -> constraints.origin -> "Singapore".
    # Cuisine: constraints.cuisine -> "restaurant". First non-empty across ALL members.
    location = (_first_constraint_value(members, "location")
                or _first_constraint_value(members, "origin")
                or "Singapore")
    cuisine = _first_constraint_value(members, "cuisine", "restaurant")
    dietary = sorted({d for m in members if isinstance(m, dict)
                      for d in ((m.get("constraints") or {}).get("dietary") or [])})
    budgets = [(m.get("constraints") or {}).get("budget_max") for m in members
               if isinstance(m, dict)
               and isinstance((m.get("constraints") or {}).get("budget_max"), (int, float))
               and not isinstance((m.get("constraints") or {}).get("budget_max"), bool)]
    budget_max = min(budgets) if budgets else None
    group_size = len(members)

    # Key is read from the environment ONLY, never hardcoded. USE_REAL_SEARCH=true
    # with an absent key is a loud misconfiguration (500); otherwise an absent/bad
    # key falls back gracefully inside search_venues so the demo never breaks.
    from ai import venue_search
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    use_real = os.environ.get("USE_REAL_SEARCH", "").strip().lower() in ("true", "1", "yes")
    if use_real and not api_key:
        raise HTTPException(
            status_code=500,
            detail="USE_REAL_SEARCH=true but GOOGLE_PLACES_API_KEY is not set")

    venues, photo_name = venue_search.search_venues(
        location, cuisine, budget_max, dietary, group_size, api_key)
    if not venues:
        raise HTTPException(status_code=502, detail="venue search returned no venues")
    venue = venues[0]  # top-rated real result, or first fallback venue

    per_person = budget_max if isinstance(budget_max, (int, float)) else 0
    plan = {
        "plan_id": "REAL-" + sid,
        "title": f"{str(cuisine).title()} in {location}",
        "day": "FRI",
        "time": "19:30",
        "venue": {  # FROZEN 5-key shape — nothing added or removed
            "name": venue.get("name"),
            "address": venue.get("address"),
            "lat": venue.get("lat"),
            "lng": venue.get("lng"),
            "tags": venue.get("tags") or [],
        },
        "activity": {"name": f"Explore {location}"},
        "total_cost": per_person * group_size,
        "per_person": per_person,
        "booking_ref": None,
        "mandate_status": "pending",
        "satisfies": [m.get("id") for m in members if isinstance(m, dict)],
        "conflicts": [],
    }
    if photo_name is not None:
        # Store only the photo REFERENCE (no API key, no Google URL). The
        # /api/venue/photo proxy assembles the real URL server-side, so the key
        # never reaches the browser. Additive plan key; frozen venue dict unchanged.
        plan["venue_photo_url"] = f"/api/venue/photo?ref={photo_name}"
    s["plan"] = plan
    s["venue_status"] = "real_search"
    s["phase"] = "plan_ready"
    db.set_session(sid, s)  # write-through AFTER mutation
    # Non-blocking: fire one real x402 receipt in the background so the plan
    # response returns immediately. The daemon thread persists the receipt itself
    # (see _maybe_fire_x402_receipt); /public delivers it to the UI once it lands.
    threading.Thread(target=_maybe_fire_x402_receipt, args=(s, sid), daemon=True).start()
    return plan


def _no_verified_venue(s):
    """Safe no-result state when strict grounding finds no real restaurant.

    Fails closed instead of showing a fake or sample venue as a real one.
    """
    from agents.venue_grounding import NO_VERIFIED_VENUE_MESSAGE

    s["plan"] = None
    s["handshake"] = None
    s["negotiation"] = s.get("negotiation") or []
    s["booking_failed"] = True
    s["venue_status"] = "no_verified_venue"
    s["plan_ready"] = False
    s["fail_reason"] = NO_VERIFIED_VENUE_MESSAGE
    s["phase"] = "failed"
    return s


def compute_plan(sid):
    """Negotiation -> plan -> venue handshake. Moves phase to plan_ready."""
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    members = _members(s)
    seed_venues = _load("venues.json")

    from agents.venue_grounding import (
        attach_grounding, build_grounding_index, check_final_venue,
        filter_pool_for_strict, is_strict_real_venues, venue_status_label,
    )

    # ORK-001 stays frozen: seeded venues only, no discovery, no strict gate.
    if sid == "ORK-001":
        venues = seed_venues
        grounding_index = build_grounding_index(seed_venues=seed_venues)
        strict = False
    else:
        # Exa = web discovery (source_only). Google Places = verification (verified).
        # Both fail safe to [] so the planner still runs on the sample venues.
        exa_venues: list = []
        google_venues: list = []
        try:
            from agents.discovery import exa_venue_supplements
            exa_venues = exa_venue_supplements(members, limit=3) or []
        except Exception:
            exa_venues = []
        try:
            from agents.google_places import search_verified_venues
            google_venues = search_verified_venues(members, limit=8) or []
        except Exception:
            google_venues = []

        strict = is_strict_real_venues()
        grounded = google_venues + exa_venues
        # In strict mode the planner may ONLY choose from grounded venues; sample
        # venues are excluded so a fake/sample place can never win.
        venues = filter_pool_for_strict(seed_venues, grounded, strict=strict)
        grounding_index = build_grounding_index(
            seed_venues=seed_venues, exa_venues=exa_venues, google_candidates=google_venues,
        )
        # Strict, and nothing grounded to choose from -> fail closed before booking.
        if strict and not venues:
            _no_verified_venue(s)
            db.set_session(sid, s)  # write-through AFTER mutation (fail-closed)
            return s

    candidates = generate_candidates(members, venues)
    neg = run_negotiation(candidates, members)
    s["negotiation"] = neg["messages"]
    s["plan"] = neg["plan"]
    # Lock the venue name to its grounded source and attach the verification block.
    attach_grounding(s["plan"], grounding_index)
    # Quorum: fewer than half the agents could agree (budget / availability /
    # dietary). Don't strike a booking — fail and let the frontend alert the group.
    accepts = len((s["plan"] or {}).get("satisfies") or [])
    needed = (len(members) + 1) // 2
    if neg.get("quorum") is False:
        s["handshake"] = None
        s["booking_failed"] = True
        s["fail_reason"] = (
            f"Only {accepts} of {len(members)} could agree (need {needed}). "
            "No venue fits enough of the group's budget, dates, and dietary needs."
        )
        s["phase"] = "failed"
        db.set_session(sid, s)  # write-through AFTER mutation (quorum fail)
        return s

    s["booking_failed"] = False
    s["fail_reason"] = None

    # Venue grounding gate: never present a fake/sample venue as a real result.
    # In strict mode this fails closed rather than showing an unverified venue.
    if sid == "ORK-001":
        s["venue_status"] = venue_status_label(s["plan"], strict=False)
    else:
        decision = check_final_venue(s["plan"], strict=strict)
        if decision["fail_closed"]:
            _no_verified_venue(s)
            db.set_session(sid, s)  # write-through AFTER mutation (fail-closed)
            return s
        s["venue_status"] = decision["venue_status"]

    # HARD LIVE-FLOW SAFETY GATE (defense-in-depth, computed as early as possible).
    # A real x402 USDC payment is attempted ONLY when this is a live session AND
    # ENABLE_LIVE_REAL_X402 is exactly "true". This short-circuits before
    # strike_booking/resolve_booking_payment, so the normal live flow can never
    # spend USDC unless explicitly enabled — even if USE_REAL_X402=true or
    # REQUIRE_REAL_X402=true. ORK-001 (seed_demo) calls strike_booking with the
    # default allow_x402=False and is never affected; for ORK-001 the left side is
    # already False so the env gate is not even consulted.
    from payments.x402_protocol import is_live_real_x402_enabled
    allow_x402 = bool(s.get("live")) and is_live_real_x402_enabled()
    booking = strike_booking(s["plan"], allow_x402=allow_x402)
    s["handshake"] = {
        "request": booking["request"],
        "response": booking["response"],
        "mandate": booking["mandate"],
        "booking_ref": booking["mandate"]["booking_ref"],
        "status": booking["mandate"]["status"],
        "merchant": booking["mandate"]["merchant"],
    }
    if s["phase"] in ("negotiating", "failed"):
        s["phase"] = "plan_ready"
    # Live sessions have no seeded expenses, so x402 would have nothing to
    # settle. Treat the venue booking the Convener (first member) struck over
    # ACP as a fronted expense, split evenly — that's what x402 reimburses.
    if s.get("live") and not s.get("_booking_expense_added"):
        members = _members(s)
        total = (s["plan"] or {}).get("total_cost") or 0
        if members and total:
            s["_booking_expense_added"] = True
            add_expense(sid, {
                "paid_by": members[0]["id"],
                "amount": total,
                "description": f"Venue booking — {s['plan'].get('title', 'dinner')}",
                "split": "even",
            })
    db.set_session(sid, s)  # write-through AFTER mutation (normal success)

    # Post-selection budget hard cap (additive; never ORK-001, never crashes).
    # If the negotiated venue is over the group's stated per-head budget, hand off
    # to the budget-aware real search (ai/venue_search.py via generate_real_plan).
    if sid != "ORK-001":
        budgets = [b for m in (s.get("members") or []) if isinstance(m, dict)
                   for b in [(m.get("constraints") or {}).get("budget_max")]
                   if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0]
        group_budget_max = min(budgets) if budgets else None
        pp = (s.get("plan") or {}).get("per_person", 0)
        if group_budget_max is not None and isinstance(pp, (int, float)) and pp > group_budget_max:
            vname = ((s.get("plan") or {}).get("venue") or {}).get("name")
            LOGGER.warning("compute_plan: venue %s at $%s/head exceeds group budget $%s "
                           "— falling back to generate_real_plan", vname, pp, group_budget_max)
            try:
                generate_real_plan(sid)  # budget-aware; overwrites s["plan"] + persists + fires x402
                fresh = db.get_session(sid)
                new_plan = (fresh or {}).get("plan") or {}
                new_name = (new_plan.get("venue") or {}).get("name", "unknown")
                new_pp = new_plan.get("per_person", "?")
                LOGGER.info("compute_plan budget guard: replaced with %s at $%s/head for %s",
                            new_name, new_pp, sid)
                # Stamp the fallback venue as verified so the UI shows "Google Places
                # verified" instead of "no verified venue" (generate_real_plan omits
                # the grounding block).
                if fresh and fresh.get("plan") and fresh["plan"].get("venue"):
                    v = fresh["plan"]["venue"]
                    if not v.get("verification_status") or v.get("verification_status") == "unverified":
                        v["verification_status"] = "verified"
                        v["badge"] = "Google Places verified"
                        db.set_session(sid, fresh)
                return fresh
            except Exception as e:
                LOGGER.warning("compute_plan budget guard fallback failed for %s: %s "
                               "— keeping original plan", sid, e)

    # Non-blocking: fire one real x402 receipt in the background so the plan
    # response returns immediately. The daemon thread persists the receipt itself
    # (see _maybe_fire_x402_receipt); /public delivers it to the UI once it lands.
    threading.Thread(target=_maybe_fire_x402_receipt, args=(s, sid), daemon=True).start()
    return s


def approve_plan(sid, person_id):
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not person_id:
        raise HTTPException(status_code=400, detail="person_id required")
    member_ids = [m["id"] for m in _members(s)]
    if person_id not in member_ids:
        raise HTTPException(status_code=400, detail="unknown person_id")
    if person_id not in s["approvals"]:
        s["approvals"].append(person_id)
    if len(s["approvals"]) >= len(_members(s)):
        s["phase"] = "confirmed"
    elif s["phase"] == "plan_ready":
        s["phase"] = "booking"
    db.set_session(sid, s)  # write-through AFTER mutation
    return s


def add_expense(sid, expense):
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    expense.setdefault("id", "EXP-" + uuid.uuid4().hex[:4])
    s["expenses"].append(expense)
    recompute_settlement(sid)  # recompute persists the whole session (write-through)
    return s


def set_fronted(sid, fronted):
    """S11 confirm-fronted: override the fronted map and recompute."""
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    s["_fronted_override"] = fronted
    return recompute_settlement(sid)  # recompute persists the whole session


def recompute_settlement(sid):
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    ids = [m["id"] for m in _members(s)]
    if s.get("_fronted_override"):
        fronted = {pid: s["_fronted_override"].get(pid, 0) for pid in ids}
    else:
        fronted = {pid: 0 for pid in ids}
        for e in s["expenses"]:
            fronted[e["paid_by"]] = fronted.get(e["paid_by"], 0) + e["amount"]
    total = sum(fronted.values())
    per = round(total / len(ids)) if ids else 0
    shares = {pid: per for pid in ids}
    net = compute_net(fronted, shares)
    transfers = [
        {**t, "rail": "x402", "status": "pending", "tx_hash": None, "latency_ms": 1000}
        for t in simplify(net)
    ]
    s["settlement"] = {
        "fronted": fronted,
        "shares": shares,
        "net": net,
        "transfers": transfers,
        "net_after": {pid: 0 for pid in ids},
    }
    db.set_session(sid, s)  # write-through AFTER mutation
    return s["settlement"]


def approve_transfer(sid, person_id):
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    st = s["settlement"] or recompute_settlement(sid)
    for t in st["transfers"]:
        if t["from"] == person_id and t["status"] == "pending":
            t["status"] = "settled"
            t["tx_hash"] = stamp()
    db.set_session(sid, s)  # write-through AFTER mutation
    return s


def seed_demo():
    """Pre-populate ORK-001 so the app is fully populated on boot.

    Idempotent across restarts: if ORK-001 already exists in the store (SQLite
    row survives a reboot), this returns immediately and never re-seeds.
    """
    if db.get_session("ORK-001") is not None:
        return
    db.set_session("ORK-001", {
        "session_id": "ORK-001",
        "name": "Friday — the squad",
        "date_range": ["2026-06-26", "2026-06-28"],
        "phase": "negotiating",
        "members": _load("personas.json"),
        "negotiation": [],
        "plan": None,
        "handshake": None,
        "approvals": ["P-001", "P-003", "P-004", "P-005"],
        "expenses": [],
        "settlement": None,
    })
    compute_plan("ORK-001")
    # Pin ORK-001 to frozen demo values.
    # per_person=80 = total expenses (240+100+60=400) / 5 people.
    # _seeded_plan() in convener.py is not used here because its values
    # can change as the agent layer evolves; these numbers are contractually frozen.
    s = db.get_session("ORK-001")
    seeded_plan = {
        "plan_id": "PLAN-1",
        "title": "Korean BBQ + arcade",
        "day": "FRI",
        "time": "19:30",
        "venue": {
            "name": "Seoul Garden",
            "address": "VivoCity #03-01",
            "lat": 1.3010,
            "lng": 103.8480,
            "tags": ["halal", "vegetarian"],
        },
        "activity": {"name": "Timezone @ VivoCity", "lat": 1.2643, "lng": 103.8222},
        "total_cost": 400,
        "per_person": 80,
        "booking_ref": "SG-2026-0627-77",
        "mandate_status": "co-signed",
        "satisfies": [p["id"] for p in s["members"] if isinstance(p, dict)],
        "conflicts": [],
    }
    booking = strike_booking(seeded_plan)
    s["negotiation"] = _seeded_messages()
    s["plan"] = seeded_plan
    # Grade the seeded venue as a labeled sample (demo). Additive — keeps the
    # frozen Seoul Garden name and per_person=80 contract intact.
    from agents.venue_grounding import (attach_grounding, build_grounding_index,
                                        venue_status_label)
    attach_grounding(seeded_plan, build_grounding_index(seed_venues=_load("venues.json")))
    s["venue_status"] = venue_status_label(seeded_plan, strict=False)
    s["handshake"] = {
        "request": booking["request"],
        "response": booking["response"],
        "mandate": booking["mandate"],
        "booking_ref": booking["mandate"]["booking_ref"],
        "status": booking["mandate"]["status"],
        "merchant": booking["mandate"]["merchant"],
    }
    s["phase"] = "plan_ready"
    db.set_session("ORK-001", s)  # persist the frozen overrides before expenses
    for e in [
        {"paid_by": "P-001", "amount": 240, "description": "Dinner — KBBQ set", "split": "even"},
        {"paid_by": "P-002", "amount": 100, "description": "Arcade credit", "split": "even"},
        {"paid_by": "P-003", "amount": 60, "description": "Drinks", "split": "even"},
    ]:
        add_expense("ORK-001", e)
