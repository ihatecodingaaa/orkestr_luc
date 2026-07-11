"""Offline smoke for cent-exact settlement rounding (P0.1).

Exercises core.session.recompute_settlement directly (no server needed) across
the non-divisible cases that broke the old `round(total/len(ids))` math:
$401/5, $100/3, $0.01/3, total==0, single payer, multiple payers. Asserts that
money reconciles EXACTLY in integer cents — shares sum to the total, net sums to
zero, and applying the transfers zeroes every member's balance.

Stdlib only. Exit 0 = all pass, 1 = any failure.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

from core import db, session as S
from core.meal_split import _to_cents

_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def _make_session(sid, member_ids, expenses):
    """Build a bare session with explicit members + expenses in the store."""
    db.set_session(sid, {
        "session_id": sid,
        "name": sid,
        "phase": "plan_ready",
        "members": [{"id": m} for m in member_ids],
        "negotiation": [],
        "plan": None,
        "handshake": None,
        "approvals": [],
        "expenses": list(expenses),
        "settlement": None,
    })


def run_case(name, member_ids, expenses):
    sid = "TEST-RND-" + name
    try:
        _make_session(sid, member_ids, expenses)
        st = S.recompute_settlement(sid)

        fronted_c = {p: _to_cents(v) for p, v in st["fronted"].items()}
        shares_c = {p: _to_cents(v) for p, v in st["shares"].items()}
        net_c = {p: _to_cents(v) for p, v in st["net"].items()}
        total_c = sum(fronted_c.values())

        # 1. shares sum to the exact total (no penny dropped).
        check(f"[{name}] sum(shares) == total ({total_c}c)",
              sum(shares_c.values()) == total_c,
              f"shares={sum(shares_c.values())}c total={total_c}c")

        # 2. net sums to exactly zero.
        check(f"[{name}] sum(net) == 0",
              sum(net_c.values()) == 0, f"sum={sum(net_c.values())}c")

        # 3. net == fronted - share, per member.
        check(f"[{name}] net == fronted - share per member",
              all(net_c[p] == fronted_c[p] - shares_c[p] for p in member_ids),
              str({p: (fronted_c[p], shares_c[p], net_c[p]) for p in member_ids}))

        # 4. transfers reconcile: applying them zeroes every net balance exactly.
        # `from` is the debtor (negative net) paying DOWN their debt; `to` is the
        # creditor (positive net) being paid, so from += amt and to -= amt.
        bal = dict(net_c)
        for t in st["transfers"]:
            amt = _to_cents(t["amount"])
            bal[t["from"]] = bal.get(t["from"], 0) + amt
            bal[t["to"]] = bal.get(t["to"], 0) - amt
        check(f"[{name}] transfers zero every balance exactly",
              all(v == 0 for v in bal.values()),
              str({p: v for p, v in bal.items() if v != 0}))

        # 5. no transfer moves a non-positive amount.
        check(f"[{name}] all transfers > 0",
              all(_to_cents(t["amount"]) > 0 for t in st["transfers"]),
              str([t["amount"] for t in st["transfers"]]))
    finally:
        db.delete_session(sid)


print("Settlement rounding smoke (offline, integer-cent reconciliation)")
print("=" * 60)

# $401 / 5 — the classic "$1 vanishes" case (old round() dropped it).
run_case("401over5", ["A", "B", "C", "D", "E"], [{"paid_by": "A", "amount": 401}])
# $100 / 3 — remainder 1 cent distributed by largest remainder.
run_case("100over3", ["A", "B", "C"], [{"paid_by": "A", "amount": 100}])
# $0.01 / 3 — a single cent split three ways.
run_case("penny_over3", ["A", "B", "C"], [{"paid_by": "A", "amount": 0.01}])
# total == 0 — no expenses: zero shares, zero net, no transfers.
run_case("zero_total", ["A", "B", "C"], [])
# single payer, even 2-way split.
run_case("single_payer", ["A", "B"], [{"paid_by": "A", "amount": 50}])
# multiple payers, non-divisible total ($101 / 3).
run_case("multi_payer", ["A", "B", "C"],
         [{"paid_by": "A", "amount": 20}, {"paid_by": "B", "amount": 30},
          {"paid_by": "C", "amount": 51}])

# Determinism: the remainder always lands on the same members across runs.
_make_session("TEST-RND-det", ["C", "A", "B"], [{"paid_by": "A", "amount": 100}])
s1 = S.recompute_settlement("TEST-RND-det")["shares"]
_make_session("TEST-RND-det", ["C", "A", "B"], [{"paid_by": "A", "amount": 100}])
s2 = S.recompute_settlement("TEST-RND-det")["shares"]
db.delete_session("TEST-RND-det")
check("[determinism] identical shares across two runs", s1 == s2, f"{s1} vs {s2}")
# $100/3: extra cent goes to the lowest id (A), tie-break id ascending.
check("[determinism] remainder cent lands on lowest id (A)",
      _to_cents(s1["A"]) == 3334 and _to_cents(s1["B"]) == 3333 and _to_cents(s1["C"]) == 3333,
      str(s1))

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
