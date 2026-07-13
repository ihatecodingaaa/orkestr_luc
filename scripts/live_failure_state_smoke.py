"""Smoke for the live failure state (P1.2 backend side).

Drives a live session into `failed` (strict venue grounding with no verified
venue) and asserts the read endpoints expose the failure SAFELY and never hand
back a fake plan:
  * /api/status      -> phase "failed" + fail_reason
  * /api/session/{}/public -> phase failed, booking_failed, fail_reason, no plan
  * /api/plan/{}      -> 409 not_ready (NOT a sample/blank plan)
  * /api/negotiation, /api/handshake -> 409 not_ready
  * no venue/secret leaks in the public payload

Runs fully in-process via TestClient (no server, no network, no payment).
Stdlib + FastAPI TestClient only. Exit 0 = all pass, 1 = any failure.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

# Strict grounding + offline discovery -> the planner fails closed (no real venue).
os.environ["STRICT_REAL_VENUES"] = "true"

import agents.discovery
import agents.google_places

agents.discovery.exa_venue_supplements = lambda *a, **k: []
agents.google_places.search_verified_venues = lambda *a, **k: []

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


print("Live failure state smoke (strict grounding -> failed)")
print("=" * 60)

# 1. Create a live session and drive every member ready with constraints.
live = client.post("/api/session/live", json={}).json()
sid = live["session_id"]
members = live["members"]
for m in members:
    r = client.post("/api/constraints", json={
        "session_id": sid, "id": m["id"], "name": m["name"],
        "constraints": {"available_days": ["FRI"], "budget_max": 60, "dietary": []},
        "freeform": "",
    }).json()
    # Stage C: the first submit mints the member token; ready requires it.
    client.post(f"/api/ready/{sid}", json={"person_id": m["id"]},
                headers={"X-Orkestr-Token": r.get("member_token") or ""})

# 2. Status is the compute gate -> strict + nothing grounded -> failed.
st = client.get(f"/api/status/{sid}").json()
check("status phase == failed", st.get("phase") == "failed", str(st))
check("status carries fail_reason", bool(st.get("fail_reason")), str(st.get("fail_reason")))
check("status booking_failed true", st.get("booking_failed") is True, str(st.get("booking_failed")))

# 3. Public snapshot exposes the failure safely, with NO plan.
pub = client.get(f"/api/session/{sid}/public").json()
check("public phase == failed", pub.get("phase") == "failed", str(pub.get("phase")))
check("public booking_failed true", pub.get("booking_failed") is True, str(pub.get("booking_failed")))
check("public exposes fail_reason", bool(pub.get("fail_reason")), str(pub.get("fail_reason")))
check("public plan_ready false", pub.get("plan_ready") is False, str(pub.get("plan_ready")))
check("public plan is None (no fake plan)", pub.get("plan") is None, str(pub.get("plan")))
check("public venue_status == no_verified_venue",
      pub.get("venue_status") == "no_verified_venue", str(pub.get("venue_status")))
# No secret-ish leakage in the public payload.
blob = str(pub).lower()
check("public leaks no secret material",
      not any(k in blob for k in ["private_key", "evm_private", "secret", "vault_token"]),
      "unexpected sensitive key in public payload")

# 4. Pure-read endpoints refuse to hand back a fake/blank plan -> 409 not_ready.
for ep in ["plan", "negotiation", "handshake"]:
    r = client.get(f"/api/{ep}/{sid}")
    body = r.json()
    ok = r.status_code == 409 and isinstance(body, dict) and body.get("status") == "not_ready"
    check(f"GET /api/{ep}/{sid} -> 409 not_ready", ok, f"got {r.status_code} {body}")
    # A failed room's 409 echoes the failure so the UI can render it in one round-trip.
    if ep == "plan":
        check("plan 409 echoes failed phase + fail_reason",
              body.get("phase") == "failed" and bool(body.get("fail_reason")), str(body))
        check("plan 409 carries NO venue/plan data",
              "venue" not in body and "per_person" not in body, str(body))

# 5. ORK-001 is unaffected by strict mode (precomputed, seeded).
ork = client.get("/api/plan/ORK-001")
check("ORK-001 plan still 200 (unaffected)", ork.status_code == 200, str(ork.status_code))
check("ORK-001 venue Seoul Garden intact",
      (ork.json().get("venue") or {}).get("name") == "Seoul Garden",
      str((ork.json().get("venue") or {}).get("name")))

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
