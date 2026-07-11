"""Smoke for the pure-read GET contract (P1.1, re-verified under P2).

/api/plan, /api/negotiation, /api/handshake are PURE READS: they never run the
pipeline. A fresh live room returns 409 {"status":"not_ready"} from all three and
stays un-computed no matter how many times they're polled; /api/status is the only
lazy compute gate. ORK-001 (precomputed) returns 200. Unknown sessions 404.

Runs in-process via TestClient (no server, no network, no payment).
Exit 0 = all pass, 1 = any failure.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

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


print("Pure-read GET contract smoke (P1.1)")
print("=" * 60)

# Fresh live room, nobody ready — the pure-read endpoints must 409 not_ready.
sid = client.post("/api/session/live", json={}).json()["session_id"]

for ep in ("plan", "negotiation", "handshake"):
    r = client.get(f"/api/{ep}/{sid}")
    body = r.json()
    check(f"GET /api/{ep}/{sid} -> 409", r.status_code == 409, f"got {r.status_code}")
    check(f"GET /api/{ep}/{sid} body status == not_ready",
          isinstance(body, dict) and body.get("status") == "not_ready", str(body))

# Poll them repeatedly — still must not compute.
for _ in range(4):
    for ep in ("plan", "negotiation", "handshake"):
        client.get(f"/api/{ep}/{sid}")
pub = client.get(f"/api/session/{sid}/public").json()
check("repeated GET polling did NOT compute a plan", pub.get("plan") is None, str(pub.get("plan")))
check("repeated GET polling left phase negotiating", pub.get("phase") == "negotiating", str(pub.get("phase")))

# ORK-001 is precomputed -> 200 with real data.
check("GET /api/plan/ORK-001 -> 200", client.get("/api/plan/ORK-001").status_code == 200)
check("GET /api/negotiation/ORK-001 -> 200", client.get("/api/negotiation/ORK-001").status_code == 200)
check("GET /api/handshake/ORK-001 -> 200", client.get("/api/handshake/ORK-001").status_code == 200)
check("ORK-001 plan per_person 80",
      client.get("/api/plan/ORK-001").json().get("per_person") == 80)

# Unknown session -> 404 (checked before the not_ready path).
for ep in ("plan", "negotiation", "handshake"):
    check(f"GET /api/{ep}/NOPE -> 404", client.get(f"/api/{ep}/NOPE").status_code == 404)

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
