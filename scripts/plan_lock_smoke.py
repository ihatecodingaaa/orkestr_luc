"""Offline smoke for the per-session compute lock + one-shot x402 daemon (P0.2).

Fires N concurrent compute_plan() triggers at a single session and asserts:
  * exactly ONE pipeline computation ran (the lock serialized; the rest no-op'd),
  * exactly ONE receipt daemon spawned (the check-and-set guard held),
  * zero exceptions across all threads,
  * repeated post-compute triggers never recompute or re-spawn.

External venue discovery is stubbed to [] so the test is fully offline and fast;
the receipt daemon is stubbed with a counter so nothing touches a payment path.

Stdlib only. Exit 0 = all pass, 1 = any failure.
"""
import json
import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

import agents.discovery
import agents.google_places
from core import db, session as S

_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


# --- offline + payment-safe stubs -----------------------------------------
# No network discovery (would hang/slow the test), no real receipt daemon.
agents.discovery.exa_venue_supplements = lambda *a, **k: []
agents.google_places.search_verified_venues = lambda *a, **k: []

_lock = threading.Lock()
_spawns = {"n": 0}
_body_calls = {"n": 0}


def _fake_fire(s, sid):
    with _lock:
        _spawns["n"] += 1


_orig_body = S._compute_plan_body


def _counting_body(sid, member_ids=None):
    with _lock:
        _body_calls["n"] += 1
    return _orig_body(sid, member_ids=member_ids)


S._maybe_fire_x402_receipt = _fake_fire   # counted, never touches payments
S._compute_plan_body = _counting_body     # count real pipeline executions


def _make_session(sid):
    personas = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "personas.json")))
    db.set_session(sid, {
        "session_id": sid,
        "name": sid,
        "date_range": ["2026-06-26", "2026-06-28"],
        "phase": "negotiating",
        "members": personas,
        "negotiation": [],
        "plan": None,
        "handshake": None,
        "approvals": [],
        "expenses": [],
        "settlement": None,
        "live": True,
        "ready": [p["id"] for p in personas],
    })


print("Plan lock smoke (offline, concurrent compute triggers)")
print("=" * 60)

SID = "TEST-LOCK-1"
N = 12
_make_session(SID)

errors = []
barrier = threading.Barrier(N)


def worker():
    try:
        barrier.wait()          # release all threads at once -> maximal contention
        S.compute_plan(SID)
    except Exception:
        errors.append(traceback.format_exc())


threads = [threading.Thread(target=worker) for _ in range(N)]
for t in threads:
    t.start()
for t in threads:
    t.join()

# Let the (stubbed) daemon threads run to completion.
deadline = time.time() + 3
while time.time() < deadline:
    time.sleep(0.05)
    with _lock:
        if _spawns["n"] >= 1 and _body_calls["n"] >= 1:
            break
time.sleep(0.2)

s = db.get_session(SID)
check(f"{N} concurrent compute triggers -> zero exceptions", not errors,
      (errors[0] if errors else ""))
check("exactly ONE pipeline computation ran", _body_calls["n"] == 1,
      f"body_calls={_body_calls['n']}")
check("exactly ONE receipt daemon spawned", _spawns["n"] == 1,
      f"spawns={_spawns['n']}")
check("plan was produced (phase plan_ready, plan set)",
      s.get("phase") == "plan_ready" and bool(s.get("plan")),
      f"phase={s.get('phase')} plan={'set' if s.get('plan') else 'none'}")
check("_x402_fired flag set exactly once", s.get("_x402_fired") is True,
      str(s.get("_x402_fired")))

# Repeated triggers after the plan exists must not recompute or re-spawn.
for _ in range(5):
    S.compute_plan(SID)
time.sleep(0.2)
check("post-compute triggers do NOT recompute", _body_calls["n"] == 1,
      f"body_calls={_body_calls['n']}")
check("post-compute triggers do NOT re-spawn the daemon", _spawns["n"] == 1,
      f"spawns={_spawns['n']}")

db.delete_session(SID)

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
