"""In-memory session store + state machine for the full Orkestr webapp.

Seeded with a default demo session (ORK-001) so every screen has data on boot.
Hackathon approach: REAL endpoints, SEEDED data underneath — swap the stubs for
real negotiation/constraint logic per the Build Guide without touching the API.

Owner: Lucas (session plumbing) + Jaydon (the agent calls).
"""
import json
import os
import uuid

from fastapi import HTTPException

from agents.convener import (generate_candidates, run_negotiation, strike_booking,
                             _seeded_messages)
from core import db
from core.settlement import compute_net, simplify
from payments.x402 import stamp

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
PHASES = ["negotiating", "plan_ready", "booking", "confirmed"]


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
    if not person.get("id"):
        raise HTTPException(status_code=400, detail="id required")
    s["members"] = [m for m in s["members"] if m["id"] != person.get("id")] + [person]
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
    return s


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
