"""Smoke for budget-truthful per-head pricing (Fix 2, backend).

A live computed plan must show the CHOSEN VENUE'S estimated per-head cost capped
at the group's min budget — never the raw budget ceiling when the venue is
cheaper. Asserts, in-process (no network, no payment):

  * a $20 group + a real ~$15 venue -> plan per_person == 15 (reflects estimate,
    NOT == budget_max, and <= budget_max);
  * a $12 group + a ~$15 venue -> per_person capped at 12 (<= budget_max);
  * no estimate available -> conservative fallback to the ceiling, still
    <= budget_max (never above budget);
  * ORK-001's seeded per_person stays exactly 80 (frozen demo untouched).

venue_search.search_venues is monkeypatched so the check is deterministic and
offline (no Google Places key needed). Exit 0 = all pass, 1 = any failure.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

from ai import venue_search as V
from core import session as S

# SAFETY: this smoke computes live plans, which normally spawn the best-effort
# x402 auto-receipt daemon. Neutralise it so the test can NEVER reach the paid
# path — we assert only pricing math here, no payment is ever involved.
S._spawn_x402_receipt_once = lambda *a, **k: None

_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def _fake_search(estimate):
    """A stand-in search_venues returning one real venue and a fixed estimate."""
    venue = {"name": "Maxwell Food Centre", "address": "1 Kadayanallur St",
             "lat": 1.280, "lng": 103.844, "tags": ["food_court", "budget"]}
    return lambda *a, **k: ([venue], None, estimate)


def _live_plan_with(budget_max, estimate):
    """Build a live session where everyone sets budget_max, patch the venue search
    to return `estimate`, compute the real plan, and return its per_person."""
    s = S.new_live_session()
    sid = s["session_id"]
    for m in s["members"]:
        S.add_constraints(sid, {
            "id": m["id"], "name": m.get("name", m["id"]),
            "constraints": {"budget_max": budget_max, "available_days": ["FRI"],
                            "dietary": [], "location": "Singapore", "cuisine": "korean"},
            "freeform": "",
        })
    orig = V.search_venues
    V.search_venues = _fake_search(estimate)
    try:
        S.generate_real_plan(sid)
    finally:
        V.search_venues = orig
    return (S.get(sid) or {}).get("plan", {}).get("per_person"), budget_max


print("Per-head estimate smoke (Fix 2)")
print("=" * 60)

# 1. $20 group, ~$15 venue -> per_person reflects the estimate, capped at budget.
pp, budget = _live_plan_with(20, 15)
check("cheap venue shows its estimate (per_person == 15)", pp == 15, f"per_person={pp}")
check("per_person <= budget_max", isinstance(pp, (int, float)) and pp <= budget, f"{pp} vs {budget}")
check("per_person is NOT the raw ceiling ($20)", pp != budget, f"per_person={pp}")

# 2. $12 group, ~$15 venue -> capped at the ceiling (<= budget_max).
pp2, budget2 = _live_plan_with(12, 15)
check("over-budget estimate capped at ceiling ($12)", pp2 == 12, f"per_person={pp2}")
check("capped per_person <= budget_max", pp2 <= budget2, f"{pp2} vs {budget2}")

# 3. No estimate available -> conservative fallback to ceiling, never above budget.
pp3, budget3 = _live_plan_with(25, None)
check("no estimate -> fallback <= budget_max", isinstance(pp3, (int, float)) and pp3 <= budget3,
      f"{pp3} vs {budget3}")

# 4. ORK-001 seeded per_person stays exactly 80 (frozen; never live-computed).
S.seed_demo()
ork = (S.get("ORK-001") or {}).get("plan", {}).get("per_person")
check("ORK-001 per_person stays 80 (frozen)", ork == 80, f"per_person={ork}")

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
