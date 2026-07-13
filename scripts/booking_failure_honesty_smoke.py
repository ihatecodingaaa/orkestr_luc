"""Offline smoke: the booking "confirmed" lie is dead (Stage B).

Forces every booking failure mode through the REAL live pipeline and asserts the
system tells the truth each time:
  * venue API error / payment declined / timeout / mandate verify fail
    -> phase == "failed", a SPECIFIC fail_reason, NO handshake, NO booking_ref,
       NO order.status == "confirmed", and NO x402 receipt daemon spawn.
  * the happy path still produces a booking HANDOFF — a verified venue with a
    real Maps/website link — and the sandbox merchant acceptance is marked
    simulated (never presented as a real reservation).
  * ORK-001 keeps its seeded (demo-labelled) contract.

Runs fully in-process via TestClient (no server, no network, no payment).
Exit 0 = all pass, 1 = any failure.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

# Offline determinism: no LLM, no Stripe, no Exa; sample pool for failure cases.
os.environ["OPENAI_API_KEY"] = ""
os.environ["STRIPE_SECRET_KEY"] = ""
os.environ["USE_EXA"] = "false"
os.environ["STRICT_REAL_VENUES"] = "false"
os.environ["USE_GOOGLE_PLACES"] = "false"

from fastapi.testclient import TestClient

import api.main as M
import payments.mandate as mandate_mod
from agents import google_places as GP
from core import db, session as S
from payments import acp_checkout, acp_payment

client = TestClient(M.app)
_failures = []

# Tripwire: the x402 receipt daemon must NEVER spawn on a failure path.
_SPAWN = {"count": 0}
_orig_spawn = S._spawn_x402_receipt_once
S._spawn_x402_receipt_once = lambda s, sid: _SPAWN.__setitem__("count", _SPAWN["count"] + 1)


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def section(title):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def _new_live_ready():
    """Create a live session with all 5 members submitted + ready."""
    live = client.post("/api/session/live", json={}).json()
    sid = live["session_id"]
    for m in live["members"]:
        r = client.post("/api/constraints", json={
            "session_id": sid, "id": m["id"], "name": m["name"],
            "constraints": {"available_days": ["FRI"], "budget_max": 80, "dietary": []},
            "freeform": "",
        }).json()
        # Stage C: the first submit mints the member token; ready requires it.
        client.post(f"/api/ready/{sid}", json={"person_id": m["id"]},
                    headers={"X-Orkestr-Token": r.get("member_token") or ""})
    return sid


def run_failure_case(name, apply_patch, remove_patch, expect_substr):
    _SPAWN["count"] = 0
    apply_patch()
    try:
        sid = _new_live_ready()
        st = client.get(f"/api/status/{sid}").json()  # the single compute gate
    finally:
        remove_patch()

    check(f"[{name}] phase == failed", st.get("phase") == "failed", str(st))
    reason = st.get("fail_reason") or ""
    check(f"[{name}] specific fail_reason ('{expect_substr}')",
          expect_substr.lower() in reason.lower(), f"fail_reason={reason!r}")
    check(f"[{name}] booking_failed true", st.get("booking_failed") is True, str(st))

    r = client.get(f"/api/handshake/{sid}")
    check(f"[{name}] handshake read -> 409 not_ready (no fake handshake)",
          r.status_code == 409, f"got {r.status_code} {r.json()}")

    sess = db.get_session(sid) or {}
    check(f"[{name}] session handshake is None", sess.get("handshake") is None,
          str(type(sess.get("handshake"))))
    plan_ref = (sess.get("plan") or {}).get("booking_ref")
    hs_obj = sess.get("handshake")
    hs_ref = hs_obj.get("booking_ref") if isinstance(hs_obj, dict) else None
    check(f"[{name}] NO booking_ref anywhere", not plan_ref and not hs_ref,
          f"plan_ref={plan_ref!r} handshake_ref={hs_ref!r}")
    check(f"[{name}] NO order.status == 'confirmed' in the session",
          '"status": "confirmed"' not in json.dumps(sess), "confirmed order found")
    check(f"[{name}] x402 receipt daemon NOT spawned", _SPAWN["count"] == 0,
          f"spawns={_SPAWN['count']}")

    pub = client.get(f"/api/session/{sid}/public").json()
    check(f"[{name}] public shows failed + fail_reason, no booking_ref",
          pub.get("phase") == "failed" and bool(pub.get("fail_reason"))
          and not pub.get("booking_ref"),
          f"phase={pub.get('phase')} ref={pub.get('booking_ref')}")


print("Booking failure honesty smoke (offline)")
print("=" * 60)

# ---------------------------------------------------------------------------
section("1. VENUE API ERROR -> honest failure")
# ---------------------------------------------------------------------------
_orig_create = acp_checkout.create_session


def _venue_boom(*_a, **_k):
    raise RuntimeError("forced venue API outage")


run_failure_case(
    "venue error",
    lambda: setattr(acp_checkout, "create_session", _venue_boom),
    lambda: setattr(acp_checkout, "create_session", _orig_create),
    "Venue booking service error",
)

# ---------------------------------------------------------------------------
section("2. PAYMENT DECLINED -> honest failure")
# ---------------------------------------------------------------------------
_orig_delegate = acp_payment.delegate_payment


def _payment_boom(*_a, **_k):
    raise RuntimeError("forced payment decline")


run_failure_case(
    "payment declined",
    lambda: setattr(acp_payment, "delegate_payment", _payment_boom),
    lambda: setattr(acp_payment, "delegate_payment", _orig_delegate),
    "Payment failed",
)

# ---------------------------------------------------------------------------
section("3. TIMEOUT -> honest failure")
# ---------------------------------------------------------------------------
_orig_complete = acp_checkout.complete_session


def _timeout_boom(*_a, **_k):
    raise TimeoutError("forced booking timeout")


run_failure_case(
    "timeout",
    lambda: setattr(acp_checkout, "complete_session", _timeout_boom),
    lambda: setattr(acp_checkout, "complete_session", _orig_complete),
    "timed out",
)

# ---------------------------------------------------------------------------
section("4. MANDATE VERIFY FAIL -> honest failure (tamper gate)")
# ---------------------------------------------------------------------------
_orig_verify = mandate_mod.verify

run_failure_case(
    "mandate verify fail",
    lambda: setattr(mandate_mod, "verify", lambda _m: False),
    lambda: setattr(mandate_mod, "verify", _orig_verify),
    "mandate integrity",
)

# ---------------------------------------------------------------------------
section("5. HAPPY PATH -> handoff with real Maps/booking links (strict+canned)")
# ---------------------------------------------------------------------------
CANNED = {
    "places": [{
        "id": "ChIJHappyPathKBBQ_place_id",
        "displayName": {"text": "Happy Path KBBQ", "languageCode": "en"},
        "formattedAddress": "1 HarbourFront Walk, Singapore 098585",
        "googleMapsUri": "https://maps.google.com/?cid=42424242",
        "websiteUri": "https://happypathkbbq.example.sg/",
        "businessStatus": "OPERATIONAL",
        "types": ["korean_restaurant", "restaurant", "food"],
        "primaryType": "korean_restaurant",
        "priceLevel": "PRICE_LEVEL_MODERATE",
        "rating": 4.4,
        "userRatingCount": 512,
    }]
}
os.environ["STRICT_REAL_VENUES"] = "true"
os.environ["USE_GOOGLE_PLACES"] = "true"
os.environ["GOOGLE_PLACES_API_KEY"] = "FAKE_TEST_KEY_never_leaks"
_orig_transport = GP._places_search_text
GP._places_search_text = lambda query, api_key, limit: CANNED
_SPAWN["count"] = 0
try:
    sid = _new_live_ready()
    st = client.get(f"/api/status/{sid}").json()
finally:
    GP._places_search_text = _orig_transport
    os.environ["STRICT_REAL_VENUES"] = "false"
    os.environ["USE_GOOGLE_PLACES"] = "false"
    os.environ["GOOGLE_PLACES_API_KEY"] = ""

check("[happy] phase == plan_ready", st.get("phase") == "plan_ready", str(st))
plan = client.get(f"/api/plan/{sid}").json()
ver = ((plan.get("venue") or {}).get("verification") or {})
handoff_link = ver.get("google_maps_uri") or ver.get("website_uri")
check("[happy] venue is Google-verified",
      ver.get("verification_status") == "verified", str(ver.get("verification_status")))
check("[happy] handoff carries a real Maps/booking link",
      isinstance(handoff_link, str) and handoff_link.startswith("http"), str(handoff_link))
hs = client.get(f"/api/handshake/{sid}").json()
check("[happy] handshake co-signed", hs.get("status") == "co-signed", str(hs.get("status")))
order = ((hs.get("mandate") or {}).get("order") or {})
check("[happy] sandbox acceptance is marked simulated (not a real reservation)",
      order.get("simulated") is True, str(order))
check("[happy] receipt daemon scheduled exactly once (gated off in env)",
      _SPAWN["count"] == 1, f"spawns={_SPAWN['count']}")

# ---------------------------------------------------------------------------
section("6. ORK-001 seeded demo intact + honestly labelled")
# ---------------------------------------------------------------------------
# Re-seed fresh so the badge assertion tests the CURRENT seed contract, not a
# stale row persisted in sessions.db by an older build.
S.delete_session("ORK-001")
S.seed_demo()
ork_plan = client.get("/api/plan/ORK-001").json()
check("[ORK-001] plan 200 + Seoul Garden",
      (ork_plan.get("venue") or {}).get("name") == "Seoul Garden", str(ork_plan.get("venue")))
check("[ORK-001] per_person == 80 (frozen)", ork_plan.get("per_person") == 80,
      str(ork_plan.get("per_person")))
check("[ORK-001] keeps its seeded booking_ref (demo-labelled in the UI)",
      ork_plan.get("booking_ref") == "SG-2026-0627-77", str(ork_plan.get("booking_ref")))
ork_ver = ((ork_plan.get("venue") or {}).get("verification") or {})
check("[ORK-001] badge is the honest seeded-demo label",
      ork_ver.get("badge") == "Seeded demo venue · sample data", str(ork_ver.get("badge")))

# restore the spawn seam for cleanliness
S._spawn_x402_receipt_once = _orig_spawn

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
