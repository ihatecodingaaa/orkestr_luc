"""Offline smoke: auth boundaries + enumeration + PII (Stage C).

Asserts the access rules a public launch requires:
  (a) mutating endpoints without a token -> 403;
  (b) member A's token cannot act as member B -> 403;
  (c) a participant token cannot trigger presenter actions (draft / orders /
      real-plan / clone / fronted / fresh proof) -> 403 (or the proof gate's 404);
  (d) /public leaks NO budgets / dietary / freeform notes to unauthenticated
      callers; a member token unlocks own detail only; the host token unlocks all;
  (e) valid-token happy paths still work (guest join stays one tap);
  (f) ORK-001 (seeded public sandbox) still functions without tokens;
  (g) room ids are high-entropy (not the old 4-hex enumerable space);
  (h) repeated unknown-room probes trip the per-IP enumeration rate limit (429).

Runs fully in-process via TestClient (no server, no network, no payment).
Exit 0 = all pass, 1 = any failure.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

# Offline determinism + a tiny rate-limit window so (h) is testable quickly.
os.environ["OPENAI_API_KEY"] = ""
os.environ["STRIPE_SECRET_KEY"] = ""
os.environ["USE_EXA"] = "false"
os.environ["ORK_RL_MISS_LIMIT"] = "12"
os.environ["ORK_RL_WINDOW_SECONDS"] = "60"
os.environ["ORK_RL_BLOCK_SECONDS"] = "60"

import agents.discovery
import agents.google_places

agents.discovery.exa_venue_supplements = lambda *a, **k: []
agents.google_places.search_verified_venues = lambda *a, **k: []

from fastapi.testclient import TestClient

from api.main import app
from core import session as S

client = TestClient(app)
_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def section(title):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def tok(t):
    return {"X-Orkestr-Token": t} if t else {}


def submit(sid, m, headers=None):
    return client.post("/api/constraints", headers=headers or {}, json={
        "session_id": sid, "id": m["id"], "name": m["name"],
        "constraints": {"available_days": ["FRI"], "budget_max": 55, "dietary": ["halal"]},
        "freeform": "birthday dinner, quiet corner please",
    })


print("Auth boundary smoke (Stage C)")
print("=" * 60)

live = client.post("/api/session/live", json={}).json()
SID = live["session_id"]
HOST = live.get("host_token") or ""
members = live["members"]
M0, M1 = members[0], members[1]

# ---------------------------------------------------------------------------
section("(g) ROOM IDS are high-entropy, not enumerable")
# ---------------------------------------------------------------------------
suffix = SID.removeprefix("ORK-")
check("id keeps the ORK- display prefix", SID.startswith("ORK-"), SID)
check("id suffix >= 16 chars of URL-safe randomness", len(suffix) >= 16, SID)
check("host token minted at creation", len(HOST) >= 24, f"len={len(HOST)}")

# Claim two member slots like real guests (one tap — token comes back invisibly).
r0 = submit(SID, M0).json()
T0 = r0.get("member_token") or ""
r1 = submit(SID, M1).json()
T1 = r1.get("member_token") or ""
check("first claim mints a member token (M0)", len(T0) >= 24, str(r0))
check("first claim mints a member token (M1)", len(T1) >= 24, str(r1))

# ---------------------------------------------------------------------------
section("(a) MUTATIONS WITHOUT A TOKEN -> 403")
# ---------------------------------------------------------------------------
cases = [
    ("ready", client.post(f"/api/ready/{SID}", json={"person_id": M0["id"]})),
    ("approve", client.post(f"/api/approve/{SID}", json={"person_id": M0["id"]})),
    ("expense", client.post(f"/api/expense/{SID}", json={"paid_by": M0["id"], "amount": 10})),
    ("settle person_id", client.post(f"/api/settle/{SID}", json={"person_id": M0["id"]})),
    ("settle fronted", client.post(f"/api/settle/{SID}", json={"fronted": {M0["id"]: 10}})),
    ("draft", client.post(f"/api/session/{SID}/draft")),
    ("split orders", client.post(f"/api/split/{SID}/orders", json={"items": []})),
    ("real-plan trigger", client.post(f"/api/session/plan/{SID}")),
    ("clone", client.post(f"/api/session/clone/{SID}")),
    ("re-submit claimed slot", submit(SID, M0)),
]
for name, resp in cases:
    check(f"no token: {name} -> 403", resp.status_code == 403, f"got {resp.status_code}")

# ---------------------------------------------------------------------------
section("(b) MEMBER A CANNOT ACT AS MEMBER B -> 403")
# ---------------------------------------------------------------------------
cases = [
    ("ready as B", client.post(f"/api/ready/{SID}", headers=tok(T0),
                               json={"person_id": M1["id"]})),
    ("approve as B", client.post(f"/api/approve/{SID}", headers=tok(T0),
                                 json={"person_id": M1["id"]})),
    ("expense as B", client.post(f"/api/expense/{SID}", headers=tok(T0),
                                 json={"paid_by": M1["id"], "amount": 25})),
    ("mark B's transfer paid", client.post(f"/api/settle/{SID}", headers=tok(T0),
                                           json={"person_id": M1["id"]})),
    ("overwrite B's constraints", submit(SID, M1, headers=tok(T0))),
]
for name, resp in cases:
    check(f"A's token: {name} -> 403", resp.status_code == 403, f"got {resp.status_code}")

# ---------------------------------------------------------------------------
section("(c) PARTICIPANT TOKEN CANNOT TRIGGER PRESENTER ACTIONS -> 403")
# ---------------------------------------------------------------------------
cases = [
    ("draft", client.post(f"/api/session/{SID}/draft", headers=tok(T0))),
    ("split orders", client.post(f"/api/split/{SID}/orders", headers=tok(T0),
                                 json={"items": []})),
    ("real-plan trigger", client.post(f"/api/session/plan/{SID}", headers=tok(T0))),
    ("clone", client.post(f"/api/session/clone/{SID}", headers=tok(T0))),
    ("fronted override", client.post(f"/api/settle/{SID}", headers=tok(T0),
                                     json={"fronted": {M0["id"]: 10}})),
]
for name, resp in cases:
    check(f"participant token: {name} -> 403", resp.status_code == 403,
          f"got {resp.status_code}")
# Fresh x402 proof stays secret-gated regardless of room tokens (404 hidden when
# disabled; 403 on a bad secret when enabled) — a participant can never fire it.
r = client.post("/api/x402/demo-receipt/run", headers=tok(T0),
                json={"confirm": "PAY_0.001_USDC"})
check("participant token: fresh proof run -> 403/404 (secret gate holds)",
      r.status_code in (403, 404), f"got {r.status_code}")

# ---------------------------------------------------------------------------
section("(d) /public LEAKS NO PII TO STRANGERS; tokens unlock scoped detail")
# ---------------------------------------------------------------------------
pub = client.get(f"/api/session/{SID}/public").json()
blob = json.dumps(pub)
check("unauth: no budget_max anywhere", '"budget_max"' not in blob, blob[:300])
check("unauth: no dietary anywhere", '"dietary"' not in blob, blob[:300])
check("unauth: no freeform note text", "birthday dinner" not in blob, blob[:300])
check("unauth: constraints null for every member",
      all(m.get("constraints") is None for m in pub.get("members", [])), str(pub.get("members"))[:300])
check("unauth: still shows names + submitted/ready flags",
      all("name" in m and "submitted" in m and "ready" in m for m in pub.get("members", [])),
      str(pub.get("members"))[:300])
check("unauth: no room tokens in the payload",
      HOST not in blob and T0 not in blob and T1 not in blob, "token leaked")

pub_m = client.get(f"/api/session/{SID}/public", headers=tok(T0)).json()
mine = next(m for m in pub_m["members"] if m["id"] == M0["id"])
other = next(m for m in pub_m["members"] if m["id"] == M1["id"])
check("member token: own constraint detail visible",
      (mine.get("constraints") or {}).get("budget_max") == 55, str(mine))
check("member token: other members' detail still hidden",
      other.get("constraints") is None and not other.get("freeform"), str(other))

pub_h = client.get(f"/api/session/{SID}/public", headers=tok(HOST)).json()
check("host token: full constraint detail for submitted members",
      all((m.get("constraints") or {}).get("budget_max") == 55
          for m in pub_h["members"] if m["submitted"]), str(pub_h["members"])[:300])

# ---------------------------------------------------------------------------
section("(e) VALID-TOKEN HAPPY PATHS still work (join stays one tap)")
# ---------------------------------------------------------------------------
check("ready with own token -> 200",
      client.post(f"/api/ready/{SID}", headers=tok(T0),
                  json={"person_id": M0["id"]}).status_code == 200)
check("re-submit own constraints with own token -> 200",
      submit(SID, M0, headers=tok(T0)).status_code == 200)
check("approve with own token -> 200",
      client.post(f"/api/approve/{SID}", headers=tok(T0),
                  json={"person_id": M0["id"]}).status_code == 200)
check("expense with own token -> 200",
      client.post(f"/api/expense/{SID}", headers=tok(T0),
                  json={"paid_by": M0["id"], "amount": 30}).status_code == 200)
check("mark own transfer paid with own token -> 200",
      client.post(f"/api/settle/{SID}", headers=tok(T0),
                  json={"person_id": M0["id"]}).status_code == 200)
check("host token acts for a member (presenter room control) -> 200",
      client.post(f"/api/ready/{SID}", headers=tok(HOST),
                  json={"person_id": M1["id"]}).status_code == 200)
check("fronted override with host token -> 200",
      client.post(f"/api/settle/{SID}", headers=tok(HOST),
                  json={"fronted": {M0["id"]: 30, M1["id"]: 0}}).status_code == 200)
check("split orders with host token -> 200",
      client.post(f"/api/split/{SID}/orders", headers=tok(HOST),
                  json={"items": []}).status_code == 200)
cl = client.post(f"/api/session/clone/{SID}", headers=tok(HOST))
cl_body = cl.json() if cl.status_code == 200 else {}
check("clone with host token -> 200 + fresh host token + long id",
      cl.status_code == 200 and len((cl_body.get("host_token") or "")) >= 24
      and len((cl_body.get("session_id") or "").removeprefix("ORK-")) >= 16,
      f"{cl.status_code} {cl_body}")

# ---------------------------------------------------------------------------
section("(f) ORK-001 public sandbox still functions without tokens")
# ---------------------------------------------------------------------------
check("ORK-001 approve without token -> 200",
      client.post("/api/approve/ORK-001", json={"person_id": "P-002"}).status_code == 200)
check("ORK-001 expense without token -> 200",
      client.post("/api/expense/ORK-001",
                  json={"paid_by": "P-001", "amount": 5}).status_code == 200)
check("ORK-001 mark-paid without token -> 200",
      client.post("/api/settle/ORK-001", json={"person_id": "P-004"}).status_code == 200)
check("ORK-001 plan still 200 (per_person 80)",
      client.get("/api/plan/ORK-001").json().get("per_person") == 80)
# Leave the sandbox pristine for whoever runs next.
S.delete_session("ORK-001")
S.seed_demo()

# ---------------------------------------------------------------------------
section("(h) ENUMERATION RATE LIMIT: repeated unknown-room probes -> 429")
# ---------------------------------------------------------------------------
# Must run LAST — tripping the limiter blocks this client IP for API paths.
saw_404 = saw_429 = False
for i in range(25):
    r = client.get(f"/api/status/ORK-ENUM-{i:04d}")
    if r.status_code == 404:
        saw_404 = True
    if r.status_code == 429:
        saw_429 = True
        break
check("probes initially 404 (unknown room)", saw_404)
check("sustained probing trips the per-IP limit (429)", saw_429,
      "no 429 within 25 probes")
check("blocked IP stays blocked on the next probe",
      client.get("/api/status/ORK-ENUM-XXXX").status_code == 429)

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
