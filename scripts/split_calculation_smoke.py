"""Smart itemized split smoke tests for Orkestr.

Verifies the settlement math WITHOUT a server and WITHOUT any payment:

    * itemized split gives DIFFERENT totals for different orders
    * per-person totals sum to the bill total EXACTLY (cent-perfect)
    * shared items are split only among the selected members
    * service charge + GST are allocated proportionally (and sum exactly)
    * equal split still works when selected
    * budget warnings appear only when someone is over budget

Stdlib only. Exit 0 = all required checks pass, 1 = at least one failed.

Usage:
    python scripts/split_calculation_smoke.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_failures: list = []
_passes: int = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passes
    if ok:
        print(f"  [PASS] {label}")
        _passes += 1
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def cents(x) -> int:
    return int(round(float(x) * 100))


print("Orkestr smart-split smoke tests")
print("=" * 60)

# ---------------------------------------------------------------------------
section("IMPORTS (no server, no network, no payment)")
# ---------------------------------------------------------------------------
try:
    from core.meal_split import allocate_cents, compute_split, demo_meal_split
    check("import core.meal_split", True)
except Exception as e:  # pragma: no cover
    check("import core.meal_split", False, str(e))
    print("\nRESULT: cannot continue")
    sys.exit(1)

check(
    "split import does NOT load a payment module",
    not any(m.startswith("payments") for m in sys.modules),
    "a payments.* module was imported",
)

# ---------------------------------------------------------------------------
section("CENT-EXACT ALLOCATION")
# ---------------------------------------------------------------------------
# $10.00 shared 3 ways must sum back to exactly 1000 cents (334 + 333 + 333).
alloc = allocate_cents([1, 1, 1], 1000)
check("3-way of $10.00 sums exactly", sum(alloc) == 1000, str(alloc))
check("3-way split is 334/333/333", sorted(alloc) == [333, 333, 334], str(alloc))
# Weighted allocation sums exactly.
w = allocate_cents([4490, 1840, 4440, 2240, 1990], 1485)
check("weighted allocation sums exactly", sum(w) == 1485, str(w))

# ---------------------------------------------------------------------------
section("ITEMIZED SPLIT — different totals, cent-exact")
# ---------------------------------------------------------------------------
it = demo_meal_split(mode="itemized")
totals = [p["total"] for p in it["per_person"]]
check("5 people in the split", len(it["per_person"]) == 5, str(len(it["per_person"])))
check("itemized totals are NOT all equal", len(set(totals)) > 1, str(totals))
check("per-person totals sum to bill total exactly",
      sum(cents(p["total"]) for p in it["per_person"]) == cents(it["total"]),
      f"sum={sum(cents(p['total']) for p in it['per_person'])} total={cents(it['total'])}")
check("totals_reconcile flag is True", it["totals_reconcile"] is True)
check("rounding residual is 0.00", it["rounding"] == 0.0, str(it["rounding"]))
check("total == subtotal + service + gst",
      cents(it["total"]) == cents(it["subtotal"]) + cents(it["service_charge"]) + cents(it["gst"]))
check("no unassigned items in demo", it["unassigned_items"] == [], str(it["unassigned_items"]))
print(f"  subtotal={it['subtotal']} service={it['service_charge']} gst={it['gst']} total={it['total']}")
for p in it["per_person"]:
    print(f"    {p['name']:6} total={p['total']:.2f}  (sub={p['items_subtotal']:.2f})")

# ---------------------------------------------------------------------------
section("SHARED ITEM — only among the selected members")
# ---------------------------------------------------------------------------
by_name = {p["name"]: p for p in it["per_person"]}
def has_item(person, needle):
    return any(needle.lower() in i["name"].lower() for i in person["items"])

# Korean BBQ platter shared by Alice, Carol, Dave only (see demo data).
check("Alice shares the BBQ platter", has_item(by_name["Alice"], "BBQ"))
check("Carol shares the BBQ platter", has_item(by_name["Carol"], "BBQ"))
check("Dave shares the BBQ platter", has_item(by_name["Dave"], "BBQ"))
check("Bob does NOT share the BBQ platter", not has_item(by_name["Bob"], "BBQ"))
check("Eve does NOT share the BBQ platter", not has_item(by_name["Eve"], "BBQ"))
# Each sharer pays exactly $60 / 3 = $20.00 for that line.
bbq_shares = [i["amount"] for i in by_name["Alice"]["items"] if "BBQ" in i["name"]]
check("BBQ share is $60/3 = $20.00", bbq_shares == [20.0], str(bbq_shares))
# The shared line records how it was split.
bbq_item = next(i for i in by_name["Alice"]["items"] if "BBQ" in i["name"])
check("BBQ marked shared with 3 people", bbq_item["kind"] == "shared" and bbq_item["shared_count"] == 3)

# ---------------------------------------------------------------------------
section("SERVICE CHARGE + GST — proportional and exact")
# ---------------------------------------------------------------------------
sc_sum = sum(cents(p["service_charge"]) for p in it["per_person"])
gst_sum = sum(cents(p["gst"]) for p in it["per_person"])
check("per-person service charges sum to total service charge", sc_sum == cents(it["service_charge"]),
      f"{sc_sum} vs {cents(it['service_charge'])}")
check("per-person GST sums to total GST", gst_sum == cents(it["gst"]), f"{gst_sum} vs {cents(it['gst'])}")
# Proportionality: a bigger subtotal never pays less service charge.
alice, bob = by_name["Alice"], by_name["Bob"]
check("bigger subtotal pays >= service charge (Alice vs Bob)",
      (alice["items_subtotal"] > bob["items_subtotal"]) == (alice["service_charge"] >= bob["service_charge"]))
# Global rates hold.
expected_sc = round(it["service_charge_rate"] * it["subtotal"], 2)
check("service charge == rate * subtotal", cents(it["service_charge"]) == cents(expected_sc),
      f"{it['service_charge']} vs {expected_sc}")

# ---------------------------------------------------------------------------
section("EQUAL SPLIT — still works when selected")
# ---------------------------------------------------------------------------
eq = demo_meal_split(mode="equal")
eq_totals = [p["total"] for p in eq["per_person"]]
check("equal totals are all identical", len(set(eq_totals)) == 1, str(eq_totals))
check("equal totals sum to bill total exactly",
      sum(cents(p["total"]) for p in eq["per_person"]) == cents(eq["total"]))
check("equal total == itemized total (same bill)", cents(eq["total"]) == cents(it["total"]))

# ---------------------------------------------------------------------------
section("BUDGET WARNINGS — only when over budget")
# ---------------------------------------------------------------------------
over = [p for p in it["per_person"] if p.get("over_budget")]
check("at least one over-budget warning exists", len(over) >= 1, f"{len(over)} warnings")
check("Alice is flagged over her $50 budget", by_name["Alice"].get("over_budget") is True)
check("Alice warning text is human-readable",
      "budget" in (by_name["Alice"].get("warning") or "").lower(), by_name["Alice"].get("warning"))
check("under-budget member has NO warning", by_name["Bob"].get("over_budget") is False)
for p in over:
    print(f"    {p['warning']}")

# A member who orders nothing pays $0 (custom scenario).
section("MEMBER WITH NO ORDERS PAYS $0")
scenario = {
    "members": [{"member_id": "A", "name": "Ann"}, {"member_id": "B", "name": "Ben"}],
    "items": [{"id": "i1", "name": "Solo main", "price": 30.0, "quantity": 1,
               "assigned_to": ["A"], "shared_by": [], "category": "personal"}],
}
sc = compute_split(scenario, mode="itemized")
ben = next(p for p in sc["per_person"] if p["member_id"] == "B")
ann = next(p for p in sc["per_person"] if p["member_id"] == "A")
check("Ben (no orders) pays $0.00", ben["total"] == 0.0, str(ben["total"]))
check("Ann pays the full item + tax", ann["total"] > 30.0, str(ann["total"]))
check("2-person scenario reconciles exactly",
      sum(cents(p["total"]) for p in sc["per_person"]) == cents(sc["total"]))

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}")
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S) / {_passes + len(_failures)} checks")
    for f in _failures:
        print(f"    - {f}")
    print("=" * 60)
    sys.exit(1)
print(f"  RESULT: ALL {_passes} CHECKS PASS")
print("=" * 60)
sys.exit(0)
