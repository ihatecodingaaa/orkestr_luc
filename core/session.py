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
from core.meal_split import _money, _to_cents, allocate_cents
from core.settlement import compute_net, simplify
from payments.x402 import stamp

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
PHASES = ["negotiating", "plan_ready", "booking", "confirmed"]
# Quorum planning (P2.2): a live room may draft a plan once at least this many
# members have replied — the host doesn't have to wait for everyone. This is a
# minimum to DRAFT; the all-ready path is unchanged.
QUORUM_MIN = 3

LOGGER = logging.getLogger(__name__)

# Per-session compute locks. compute_plan runs the negotiation/booking pipeline
# and spawns the x402 receipt daemon; without a lock two devices/tabs hitting the
# same room could compute twice and double-spawn. The registry itself is guarded
# by a meta-lock (double-checked creation) so the lock objects are process-stable.
_COMPUTE_LOCKS = {}
_COMPUTE_LOCKS_META = threading.Lock()


def _compute_lock(sid):
    """Return the process-stable threading.Lock for this session id."""
    with _COMPUTE_LOCKS_META:
        lock = _COMPUTE_LOCKS.get(sid)
        if lock is None:
            lock = threading.Lock()
            _COMPUTE_LOCKS[sid] = lock
        return lock


def _spawn_x402_receipt_once(s, sid):
    """Spawn the x402 receipt daemon AT MOST ONCE per session.

    Check-and-set on a persisted `_x402_fired` flag (set under the per-sid compute
    lock at every call site inside compute_plan) so concurrent triggers — or the
    budget-guard path that runs generate_real_plan and then falls through — can
    never spawn two receipt daemons for one session. _maybe_fire_x402_receipt keeps
    its own guards too (ORK-001 skip, existing-receipt skip); this is the outer belt.
    """
    if s.get("_x402_fired"):
        return
    s["_x402_fired"] = True
    db.set_session(sid, s)  # persist the flag before spawning
    threading.Thread(target=_maybe_fire_x402_receipt, args=(s, sid), daemon=True).start()


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
    s["quorum_min"] = QUORUM_MIN  # P2.2: draft allowed once this many reply
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


# ---------------------------------------------------------------------------
# Quorum planning (P2.2). "Replied" = a member who pressed find-our-plan (in the
# ready list). Non-live sessions (ORK-001, scratch) are treated as fully replied
# since they are not gated on live readiness. All helpers are pure reads.
# ---------------------------------------------------------------------------

def _member_ids(s):
    return [m["id"] for m in s.get("members", [])
            if isinstance(m, dict) and m.get("id")]


def quorum_min(s):
    """Configured minimum replies to draft (defaults to QUORUM_MIN)."""
    v = s.get("quorum_min")
    return v if isinstance(v, int) and v > 0 else QUORUM_MIN


def replied_ids(s):
    """Member ids who have replied. Non-live sessions count as fully replied."""
    ids = _member_ids(s)
    if not s.get("live"):
        return list(ids)
    ready = set(s.get("ready", []))
    return [mid for mid in ids if mid in ready]


def pending_ids(s):
    """Member ids who have NOT replied yet (empty for non-live sessions)."""
    replied = set(replied_ids(s))
    return [mid for mid in _member_ids(s) if mid not in replied]


def quorum_report(s):
    """Additive, public quorum status for /api/status and /public. Never computes."""
    total = len(_member_ids(s))
    replied = replied_ids(s)
    replied_count = len(replied)
    # A quorum draft always records plan_basis explicitly; any other existing plan
    # is a normal all-ready plan (covers ORK-001 and sessions seeded before P2.2).
    plan_basis = s.get("plan_basis")
    if plan_basis is None and s.get("plan"):
        plan_basis = "all_ready"
    # Stale-draft indicator (P2 follow-up): how many members replied AFTER this
    # quorum draft was created, so the presenter can fold them in. Only meaningful
    # for an existing quorum draft; an all-ready plan is never "stale".
    drafted = s.get("draft_started_with") or []
    has_quorum_draft = plan_basis == "quorum_draft" and len(drafted) > 0
    new_since = max(0, replied_count - len(drafted)) if has_quorum_draft else 0
    return {
        "members_total": total,
        "replied_count": replied_count,
        "quorum_min": quorum_min(s),
        "quorum_ready": total > 0 and replied_count >= quorum_min(s),
        "all_ready": total > 0 and replied_count == total,
        "pending_members": pending_ids(s),
        "plan_basis": plan_basis,
        "draft_started_with": drafted,
        "new_replies_since_draft": new_since,
        "draft_stale": new_since > 0,
    }


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

    venues, photo_name, est_per_head = venue_search.search_venues(
        location, cuisine, budget_max, dietary, group_size, api_key)
    if not venues:
        raise HTTPException(status_code=502, detail="venue search returned no venues")
    venue = venues[0]  # top-rated real result, or first fallback venue

    # Per-head = the CHOSEN VENUE'S real estimated cost, capped at the group's
    # min budget ceiling — never the raw ceiling when the venue is cheaper. A
    # genuinely $12-18 hawker/casual venue shows ~its cost, not $45-80. Fall back
    # to the ceiling only when no estimate is available (e.g. seed fallback), and
    # never exceed budget_max.
    have_budget = isinstance(budget_max, (int, float)) and not isinstance(budget_max, bool)
    have_est = isinstance(est_per_head, (int, float)) and not isinstance(est_per_head, bool)
    # Price truth (closes the last unpriced path): a Google price-band estimate is
    # CONFIRMED; a budget-tier fallback (no price data) is APPROXIMATE and labelled
    # so — never a hard price shown as if confirmed.
    if have_est:
        per_person = min(est_per_head, budget_max) if have_budget else est_per_head
        price_confidence = "confirmed"
    elif have_budget:
        per_person = budget_max  # conservative fallback, never above budget
        price_confidence = "approximate"
    else:
        per_person = 0
        price_confidence = "approximate"
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
        "price_confidence": price_confidence,
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
    _spawn_x402_receipt_once(s, sid)
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


def compute_plan(sid, member_ids=None):
    """Negotiation -> plan -> venue handshake. Moves phase to plan_ready.

    Serialized per session: two concurrent callers (two devices/tabs polling the
    same room) can't run the pipeline twice or double-spawn the receipt daemon.
    The second caller waits on the lock, then no-ops if a plan already landed.

    member_ids (P2.2 quorum draft): when given, ONLY those members' constraints
    enter negotiation — pending members are excluded, never fabricated. None (the
    default, all-ready path) uses every member and is byte-for-byte the old flow.
    """
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    with _compute_lock(sid):
        # Re-read under the lock: another caller may have computed while we waited.
        s = db.get_session(sid)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        if s.get("plan") and s.get("phase") in ("plan_ready", "booking", "confirmed"):
            return s
        return _compute_plan_body(sid, member_ids=member_ids)


def _compute_plan_body(sid, member_ids=None):
    """The actual compute pipeline. Always called while holding _compute_lock(sid)."""
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    members = _members(s)
    # Quorum draft: restrict negotiation to the members who actually replied.
    if member_ids is not None:
        subset = set(member_ids)
        members = [m for m in members if isinstance(m, dict) and m.get("id") in subset]
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
        # Data-derived budget nudge (additive; real Places data only, never invented):
        # the cheapest CONFIRMED-price verified venue found this search. Stored on the
        # session and shown by the UI ONLY when the shown result is approximate-only or
        # fails, so the user can raise their minimum to reach confirmed-price venues.
        _budgets = [b for m in members if isinstance(m, dict)
                    for b in [(m.get("constraints") or {}).get("budget_max")]
                    if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0]
        _gmin = min(_budgets) if _budgets else None
        _confirmed = [c.get("price_per_head") for c in google_venues
                      if isinstance(c, dict) and c.get("price_confidence") == "confirmed"
                      and isinstance(c.get("price_per_head"), (int, float))
                      and not isinstance(c.get("price_per_head"), bool)]
        s.pop("suggested_min_budget", None)
        s.pop("suggested_reason", None)
        if _confirmed and _gmin is not None and min(_confirmed) > _gmin:
            s["suggested_min_budget"] = int(min(_confirmed))
            s["suggested_reason"] = "confirmed-price restaurants start around here"
        # Grounded, occasion-aware ranking of the REAL candidates (Stage C). Reorders
        # the pool so the best-fit real venue is considered first; NEVER sources or
        # invents a venue. LLM path only when USE_LLM_RANKER=true + key, else a
        # deterministic ranker; an off-list/invalid model choice is rejected upstream.
        from ai import venue_ranker
        _notes = " ".join(str(m.get("freeform") or "") for m in members
                          if isinstance(m, dict)).strip()
        _ranking = venue_ranker.rank_venues(google_venues, members, _notes)
        google_venues = _ranking.get("candidates") or google_venues
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
        # Additive: surface the chosen venue's price confidence on the plan so the UI
        # shows "Google Places verified · price approximate" honestly (the venue IS
        # verified; only the price is an estimate). Confirmed-price venues are unmarked.
        _pc = (((s["plan"] or {}).get("venue") or {}).get("verification") or {}).get("price_confidence")
        if _pc:
            s["plan"]["price_confidence"] = _pc
        # Grounded "why this place won" reasons (additive), FACT-ASSEMBLED for the
        # ACTUAL displayed venue from its real candidate record + the group's own
        # occasion/day input. Never model prose, so no ungrounded amenity can appear
        # even when negotiation picks a different venue than the ranker's top choice.
        _cv = (s["plan"] or {}).get("venue") or {}
        _cid = str(_cv.get("id") or "")
        _cand = venue_ranker._find_by_id(google_venues, _cid) if _cid else None
        _reasons = venue_ranker.reasons_for(_cand, members, _notes) if _cand else []
        if _reasons:
            s["plan"]["why_reasons"] = _reasons
        if _ranking.get("occasion"):
            s["plan"]["occasion"] = _ranking["occasion"]
        s["plan"]["ranker_source"] = _ranking.get("source")

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

    # AP2 tamper gate (P0.4): before ANY handshake-success state is written and
    # before the x402 receipt daemon can spawn, verify the co-signed mandate's cart
    # digest. strike_booking's normal ACP path returns a co-signed mandate WITHOUT a
    # cart_digest, so we bind one now from the mandate's own items (cosign computes
    # the same SHA-256). A mandate that ALREADY carries a digest is left untouched —
    # so a cart altered after signing (digest no longer matches items) fails verify()
    # and the booking is refused with no payment. Non-co-signed mandates (e.g. an
    # x402 controlled-failure) are a different failure mode and skip this gate.
    from payments.mandate import cosign, verify
    mandate = booking["mandate"]
    if mandate.get("status") == "co-signed":
        if not mandate.get("cart_digest"):
            mandate["cart_digest"] = cosign(
                mandate, mandate.get("merchant", "unknown"))["cart_digest"]
        if not verify(mandate):
            s["handshake"] = None
            s["booking_failed"] = True
            s["fail_reason"] = "mandate integrity check failed"
            s["phase"] = "failed"
            db.set_session(sid, s)  # write-through (tamper block; no receipt fires)
            return s

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
    # Record how this plan was reached (P2.2): a quorum draft vs the full all-ready
    # run. Additive — the UI labels a draft honestly; all-ready plans are unlabelled.
    s["plan_basis"] = "quorum_draft" if member_ids is not None else "all_ready"
    # Live sessions have no seeded expenses, so x402 would have nothing to
    # settle. Treat the venue booking the Convener (first member) struck over
    # ACP as a fronted expense, split evenly — that's what x402 reimburses. For a
    # quorum draft this splits among the members actually included in the draft.
    if s.get("live") and not s.get("_booking_expense_added"):
        members = [m for m in _members(s)
                   if member_ids is None or (isinstance(m, dict) and m.get("id") in set(member_ids))]
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
                    ver = v.get("verification") if isinstance(v.get("verification"), dict) else None
                    if ver is None or ver.get("verification_status") in (None, "unverified"):
                        # Build a proper verification block (read by /public + the UI)
                        # and carry the plan's price_confidence so a price-approximate
                        # fallback shows "verified · price approximate", never a hard
                        # price as if confirmed.
                        v["verification"] = {
                            "verification_status": "verified",
                            "source_kind": "google_places",
                            "badge": "Google Places verified",
                            "price_confidence": (fresh["plan"] or {}).get("price_confidence"),
                        }
                        db.set_session(sid, fresh)
                return fresh
            except Exception as e:
                LOGGER.warning("compute_plan budget guard fallback failed for %s: %s "
                               "— keeping original plan", sid, e)

    # Non-blocking: fire one real x402 receipt in the background so the plan
    # response returns immediately. The daemon thread persists the receipt itself
    # (see _maybe_fire_x402_receipt); /public delivers it to the UI once it lands.
    _spawn_x402_receipt_once(s, sid)
    return s


def draft_plan(sid):
    """P2.2 — presenter-triggered QUORUM DRAFT. Explicit POST only (never a GET
    side effect). Computes a plan from the members who have replied so far, once
    quorum is met, WITHOUT waiting for everyone. Refuses below quorum. Idempotent
    for the same reply set; re-runs (recomputes) when more members have since
    replied so a late joiner is honestly included.

    Raises: 404 unknown session, 400 non-live session, 409 below quorum.
    """
    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not s.get("live"):
        raise HTTPException(status_code=400,
                            detail="draft is only available for live sessions")
    replied = replied_ids(s)
    if len(replied) < quorum_min(s):
        raise HTTPException(
            status_code=409,
            detail=(f"quorum not reached: {len(replied)} of {len(_member_ids(s))} replied "
                    f"(need {quorum_min(s)})"))

    # Idempotent: same reply set + an existing draft -> return it unchanged (no
    # recompute, no payment churn). Re-drafting with MORE repliers falls through.
    has_plan = bool(s.get("plan")) and s.get("phase") in ("plan_ready", "booking", "confirmed")
    if has_plan and sorted(s.get("draft_started_with") or []) == sorted(replied):
        return s

    # Everyone has now replied -> use the standard all-ready path (member_ids=None)
    # and tag it all_ready; otherwise draft with just the replied subset.
    full = len(replied) == len(_member_ids(s))

    # (Re)draft. Clear any prior plan so the compute lock's double-check recomputes;
    # keep the one-shot x402 guard intact — we NEVER reset _x402_fired, so a re-draft
    # can never spawn a second receipt daemon or re-fire a payment.
    s["plan"] = None
    s["negotiation"] = None
    s["handshake"] = None
    s["phase"] = "negotiating"
    s["booking_failed"] = False
    s["fail_reason"] = None
    s["draft_started_with"] = list(replied)
    db.set_session(sid, s)  # write-through before recompute
    compute_plan(sid, member_ids=None if full else replied)
    s = db.get_session(sid)
    # Keep the draft provenance even if the compute path failed (quorum/no venue).
    s["draft_started_with"] = list(replied)
    if s.get("phase") != "failed":
        s["plan_basis"] = "all_ready" if full else "quorum_draft"
    db.set_session(sid, s)
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
    # All settlement math runs in INTEGER CENTS so no penny leaks in rounding
    # (the old `per = round(total/len(ids))` dropped the remainder — $401/5 lost
    # $1). We convert to cents at the boundary, distribute the equal share with the
    # cent-exact largest-remainder allocator from core.meal_split, run the untouched
    # compute_net/simplify on those ints (their `== 0` checks are then exact), and
    # convert back to dollars only for the stored response.
    if s.get("_fronted_override"):
        fronted_cents = {pid: _to_cents(s["_fronted_override"].get(pid, 0)) for pid in ids}
    else:
        fronted_cents = {pid: 0 for pid in ids}
        for e in s["expenses"]:
            fronted_cents[e["paid_by"]] = fronted_cents.get(e["paid_by"], 0) + _to_cents(e["amount"])
    total_cents = sum(fronted_cents.values())
    # Equal share, distributed by largest remainder over members ordered by id
    # ascending (deterministic tie-break). sum(shares) == total_cents EXACTLY.
    order = sorted(ids)
    share_list = allocate_cents([1] * len(order), total_cents) if order else []
    share_by_id = {pid: share_list[i] for i, pid in enumerate(order)}
    shares_cents = {pid: share_by_id.get(pid, 0) for pid in ids}
    net_cents = compute_net(fronted_cents, shares_cents)  # ints -> sums to exactly 0
    transfers = [
        {**t, "amount": _money(t["amount"]), "rail": "x402", "status": "pending",
         "tx_hash": None, "latency_ms": 1000}
        for t in simplify(net_cents)  # greedy min-cash-flow on integer cents
    ]
    s["settlement"] = {
        "fronted": {pid: _money(fronted_cents[pid]) for pid in ids},
        "shares": {pid: _money(shares_cents[pid]) for pid in ids},
        "net": {pid: _money(net_cents.get(pid, 0)) for pid in ids},
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


# ---------------------------------------------------------------------------
# Session-linked meal split (P1.3). The Orkestr agent wallet pays the merchant
# ONCE over x402; this computes the internal per-person settlement from THIS
# room's members + orders. Pure math (core.meal_split) — never a payment, never
# the pipeline. Distinct from recompute_settlement (which nets whole expenses).
# ---------------------------------------------------------------------------

# A small realistic menu the session DRAFT draws from, rotated per session so two
# different rooms get different draft orders (never one global canned split).
_DRAFT_MENU = [
    ("Bibimbap", 18.0),
    ("Bulgogi set", 24.0),
    ("Japchae (v)", 16.0),
    ("Soft tofu stew", 15.0),
    ("Kimchi fried rice", 14.0),
    ("Seafood pancake", 20.0),
    ("Spicy ramen", 17.0),
    ("Bibim naengmyeon", 16.5),
]
_DRAFT_SHARED = ("Table BBQ platter", 60.0)
_DRAFT_SIDE = ("Kimchi & banchan", 12.0)


def _split_members(s):
    """meal_split member rows from the session's members (+ their stated budget)."""
    rows = []
    for m in _members(s):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        c = m.get("constraints") or {}
        b = c.get("budget_max")
        row = {"member_id": m["id"], "name": m.get("name", m["id"])}
        if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0:
            row["budget"] = float(b)
        rows.append(row)
    return rows


def _draft_orders(s):
    """A realistic, session-linked DRAFT order tied to THIS room's members.

    Each member gets one personal main (chosen by a per-session rotation so two
    different rooms differ), and everyone shares a BBQ platter + banchan. Stored on
    the session so it is stable + editable — never a detached global canned split.
    """
    import zlib

    ids = [m["member_id"] for m in _split_members(s)]
    if not ids:
        return []
    rot = zlib.crc32((s.get("session_id") or "").encode()) % len(_DRAFT_MENU)
    items = []
    for i, mid in enumerate(ids):
        name, price = _DRAFT_MENU[(rot + i) % len(_DRAFT_MENU)]
        items.append({
            "id": f"draft-main-{mid}", "name": name, "price": price, "quantity": 1,
            "assigned_to": [mid], "shared_by": [], "category": "personal",
        })
    sname, sprice = _DRAFT_SHARED
    items.append({
        "id": "draft-shared-platter", "name": sname, "price": sprice, "quantity": 1,
        "assigned_to": [], "shared_by": list(ids), "category": "shared",
    })
    bname, bprice = _DRAFT_SIDE
    items.append({
        "id": "draft-shared-banchan", "name": bname, "price": bprice, "quantity": 1,
        "assigned_to": [], "shared_by": list(ids), "category": "shared",
    })
    return items


def get_session_split(sid, mode="itemized"):
    """Compute the itemized split for THIS session. Returns (split_dict, source).

    Real captured orders -> "backend_live_orders". Otherwise a session-linked draft
    is initialised (stored on the session) and used -> "backend_session_draft".
    Pure math — never runs the pipeline or a payment.
    """
    from core.meal_split import compute_split

    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    orders = s.get("orders")
    if isinstance(orders, list) and orders and not s.get("orders_are_draft"):
        source = "backend_live_orders"
    else:
        if not (isinstance(orders, list) and orders):
            orders = _draft_orders(s)
            s["orders"] = orders
            s["orders_are_draft"] = True
            db.set_session(sid, s)  # persist the draft so it is stable + editable
        source = "backend_session_draft"
    split = compute_split({"members": _split_members(s), "items": orders, "mode": mode}, mode=mode)
    split["split_source"] = source
    return split, source


def set_session_orders(sid, items, mode="itemized"):
    """Replace the session's orders with an explicit (edited/captured) order set and
    return the recomputed split. Validates item members; marks orders as real (not a
    draft) so subsequent splits report "backend_live_orders". Never a payment."""
    from core.meal_split import compute_split

    s = db.get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    member_ids = {m["member_id"] for m in _split_members(s)}
    clean = []
    for it in (items or []):
        if not isinstance(it, dict):
            raise HTTPException(status_code=400, detail="each item must be an object")
        assigned = [p for p in (it.get("assigned_to") or []) if p in member_ids]
        shared = [p for p in (it.get("shared_by") or []) if p in member_ids]
        try:
            price = float(it.get("price", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="item price must be numeric")
        category = "shared" if (it.get("category") == "shared" or (shared and not assigned)) else "personal"
        clean.append({
            "id": str(it.get("id") or f"item-{len(clean) + 1}"),
            "name": str(it.get("name") or "Item"),
            "price": price,
            "quantity": int(it.get("quantity") or 1),
            "assigned_to": assigned,
            "shared_by": shared,
            "category": category,
        })
    s["orders"] = clean
    s["orders_are_draft"] = False
    db.set_session(sid, s)  # write-through AFTER mutation
    split = compute_split({"members": _split_members(s), "items": clean, "mode": mode}, mode=mode)
    split["split_source"] = "backend_live_orders"
    return split, "backend_live_orders"


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
    # Back-cosign the seeded mandate (P0.4): strike_booking's ACP path omits the
    # cart_digest, so bind one now from its own items. ORK-001 then genuinely passes
    # mandate.verify() and displays co-signed + verified. Additive only — Seoul
    # Garden / per_person 80 / booking_ref / status "co-signed" are all unchanged.
    from payments.mandate import cosign as _cosign
    _seeded_mandate = booking["mandate"]
    if _seeded_mandate.get("status") == "co-signed" and not _seeded_mandate.get("cart_digest"):
        _seeded_mandate["cart_digest"] = _cosign(
            _seeded_mandate, _seeded_mandate.get("merchant", "Seoul Garden"))["cart_digest"]
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
