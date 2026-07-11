"""Offline smoke for AP2 mandate tamper enforcement in the money path (P0.4).

Asserts that compute_plan wires mandate.verify() into booking:
  * a CLEAN booking (real strike_booking) verifies -> handshake success, the
    receipt daemon is allowed to spawn, ORK-001-style co-signed + verified;
  * a TAMPERED mandate (co-signed, then an item price altered after signing so the
    cart_digest no longer matches) is REFUSED -> phase "failed",
    fail_reason "mandate integrity check failed", handshake is None, and NO receipt
    daemon spawns (no payment can fire);
  * the seeded ORK-001 mandate genuinely passes verify().

Discovery is stubbed to [] (offline) and the receipt daemon is stubbed with a
counter, so nothing touches a real payment path.

Stdlib only. Exit 0 = all pass, 1 = any failure.
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

import agents.discovery
import agents.google_places
from core import db, session as S
from payments.mandate import cosign, verify

_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


# --- offline + payment-safe stubs -----------------------------------------
agents.discovery.exa_venue_supplements = lambda *a, **k: []
agents.google_places.search_verified_venues = lambda *a, **k: []

_lock = threading.Lock()
_spawns = {"n": 0}


def _fake_fire(s, sid):
    with _lock:
        _spawns["n"] += 1


S._maybe_fire_x402_receipt = _fake_fire
_REAL_STRIKE = S.strike_booking


def _make_session(sid):
    personas = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "personas.json")))
    db.set_session(sid, {
        "session_id": sid, "name": sid, "date_range": ["2026-06-26", "2026-06-28"],
        "phase": "negotiating", "members": personas, "negotiation": [], "plan": None,
        "handshake": None, "approvals": [], "expenses": [], "settlement": None,
        "live": True, "ready": [p["id"] for p in personas],
    })


print("Tamper enforcement smoke (offline)")
print("=" * 60)

# --- Case 1: CLEAN booking verifies and proceeds --------------------------
with _lock:
    _spawns["n"] = 0
S.strike_booking = _REAL_STRIKE
CLEAN = "TEST-TAMPER-CLEAN"
_make_session(CLEAN)
S.compute_plan(CLEAN)
time.sleep(0.3)
sc = db.get_session(CLEAN)
hs = sc.get("handshake") or {}
mandate = hs.get("mandate") or {}
check("[clean] phase plan_ready", sc.get("phase") == "plan_ready", str(sc.get("phase")))
check("[clean] handshake present + co-signed", hs.get("status") == "co-signed", str(hs.get("status")))
check("[clean] mandate carries cart_digest", bool(mandate.get("cart_digest")), "no digest")
check("[clean] mandate passes verify()", verify(mandate) is True, str(verify(mandate)))
check("[clean] not booking_failed", not sc.get("booking_failed"), str(sc.get("booking_failed")))
check("[clean] receipt daemon spawned (>=1)", _spawns["n"] >= 1, f"spawns={_spawns['n']}")
db.delete_session(CLEAN)


# --- Case 2: TAMPERED mandate is refused, no receipt ----------------------
def _tampered_strike(plan, allow_x402=False):
    """A co-signed mandate whose first item was altered AFTER signing."""
    cart = {"cart_id": "CART-TAMPER",
            "items": [{"desc": "dinner - table", "amount": 400}],
            "booking_ref": "SG-TAMPER-01"}
    m = cosign(cart, "Seoul Garden")     # valid cart_digest over amount 400
    m["items"][0]["amount"] = 401        # tamper: price bumped after signing
    return {"request": {}, "response": {}, "mandate": m}


with _lock:
    _spawns["n"] = 0
S.strike_booking = _tampered_strike
TAMP = "TEST-TAMPER-BAD"
_make_session(TAMP)
S.compute_plan(TAMP)
time.sleep(0.3)
st = db.get_session(TAMP)
check("[tampered] phase == failed", st.get("phase") == "failed", str(st.get("phase")))
check("[tampered] booking_failed True", st.get("booking_failed") is True, str(st.get("booking_failed")))
check("[tampered] fail_reason is integrity check",
      st.get("fail_reason") == "mandate integrity check failed", str(st.get("fail_reason")))
check("[tampered] handshake is None (no success state)", st.get("handshake") is None, str(st.get("handshake")))
check("[tampered] NO receipt daemon spawned", _spawns["n"] == 0, f"spawns={_spawns['n']}")
check("[tampered] no x402_receipt stored", not st.get("x402_receipt"), str(st.get("x402_receipt")))
check("[tampered] _x402_fired not set", not st.get("_x402_fired"), str(st.get("_x402_fired")))
db.delete_session(TAMP)
S.strike_booking = _REAL_STRIKE

# --- Case 3: seeded ORK-001 genuinely verifies ----------------------------
db.delete_session("ORK-001")
S.seed_demo()
ork = db.get_session("ORK-001")
om = (ork.get("handshake") or {}).get("mandate") or {}
check("[ORK-001] handshake co-signed", (ork.get("handshake") or {}).get("status") == "co-signed",
      str((ork.get("handshake") or {}).get("status")))
check("[ORK-001] mandate passes verify()", verify(om) is True, str(verify(om)))
check("[ORK-001] venue Seoul Garden / per_person 80",
      ork["plan"]["venue"]["name"] == "Seoul Garden" and ork["plan"]["per_person"] == 80,
      str(ork["plan"].get("per_person")))

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
