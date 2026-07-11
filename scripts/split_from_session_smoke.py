"""Smoke for session-linked meal split (P1.3).

Asserts /api/split is tied to the room's members + orders (not global canned
data), carries an honest split_source, reconciles exactly, and responds to order
edits:
  * two different sessions produce different splits;
  * a fresh session returns a backend_session_draft that reconciles to the cent;
  * reassigning items (POST /api/split/{sid}/orders) changes the totals and flips
    the source to backend_live_orders;
  * shared items are split ONLY among the selected members;
  * a participant's share (their per_person row) matches the backend exactly.

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


def cents(x):
    return int(round(float(x) * 100))


print("Split from session smoke (P1.3)")
print("=" * 60)

# 1. Two distinct sessions -> session-linked drafts that differ.
a = client.post("/api/session/live", json={}).json()["session_id"]
b = client.post("/api/session/live", json={}).json()["session_id"]
sa = client.get(f"/api/split/{a}").json()
sb = client.get(f"/api/split/{b}").json()

check("A source == backend_session_draft", sa.get("split_source") == "backend_session_draft", str(sa.get("split_source")))
check("B source == backend_session_draft", sb.get("split_source") == "backend_session_draft", str(sb.get("split_source")))
check("A reconciles exactly (totals_reconcile)", sa.get("totals_reconcile") is True, str(sa.get("totals_reconcile")))
check("A sum(per_person.total) == total exactly",
      sum(cents(p["total"]) for p in sa["per_person"]) == cents(sa["total"]),
      f'{sum(cents(p["total"]) for p in sa["per_person"])} vs {cents(sa["total"])}')
a_totals = [p["total"] for p in sa["per_person"]]
b_totals = [p["total"] for p in sb["per_person"]]
check("two different sessions produce different splits", a_totals != b_totals,
      f"A={a_totals} B={b_totals}")

# 2. Draft is STABLE (re-reading doesn't regenerate/rotate).
sa2 = client.get(f"/api/split/{a}").json()
check("re-read of A is stable", [p["total"] for p in sa2["per_person"]] == a_totals,
      "draft changed on re-read")

# 3. Reassign orders -> totals change + source flips to backend_live_orders.
members = [p["member_id"] for p in sa["per_person"]]
m0, m1, m2 = members[0], members[1], members[2]
items = [
    {"id": "steak", "name": "Ribeye", "price": 44.0, "quantity": 1,
     "assigned_to": [m0], "shared_by": [], "category": "personal"},
    {"id": "salad", "name": "Garden salad", "price": 13.0, "quantity": 1,
     "assigned_to": [m1], "shared_by": [], "category": "personal"},
    {"id": "platter", "name": "Sharing platter", "price": 50.0, "quantity": 1,
     "assigned_to": [], "shared_by": [m0, m1], "category": "shared"},
]
edited = client.post(f"/api/split/{a}/orders", json={"items": items}).json()
check("edit source == backend_live_orders", edited.get("split_source") == "backend_live_orders", str(edited.get("split_source")))
check("edit changed the total", edited["total"] != sa["total"], f'{sa["total"]} -> {edited["total"]}')
check("edit reconciles exactly", edited.get("totals_reconcile") is True, str(edited.get("totals_reconcile")))
check("edit sum(per_person.total) == total exactly",
      sum(cents(p["total"]) for p in edited["per_person"]) == cents(edited["total"]),
      f'{sum(cents(p["total"]) for p in edited["per_person"])} vs {cents(edited["total"])}')

# 4. Shared item split ONLY among the selected members; others pay nothing.
by_id = {p["member_id"]: p for p in edited["per_person"]}
non_ordering = [mid for mid in members if mid not in (m0, m1)]
check("members not on any item owe 0",
      all(by_id[mid]["total"] == 0 for mid in non_ordering),
      str({mid: by_id[mid]["total"] for mid in non_ordering}))
check("m2 (unassigned) did NOT pay for the shared platter",
      by_id[m2]["items_subtotal"] == 0, str(by_id[m2]["items_subtotal"]))

# 5. Participant "your share" matches the backend row exactly. m0 has the $44
#    steak + half the $50 platter = $69 subtotal before tax/service.
check("m0 items_subtotal == 44 + 25 (steak + half platter)",
      cents(by_id[m0]["items_subtotal"]) == cents(69.0), str(by_id[m0]["items_subtotal"]))
check("m1 items_subtotal == 13 + 25 (salad + half platter)",
      cents(by_id[m1]["items_subtotal"]) == cents(38.0), str(by_id[m1]["items_subtotal"]))
# The participant endpoint is the SAME /api/split, so a member's row is authoritative.
m0_again = next(p for p in client.get(f"/api/split/{a}").json()["per_person"] if p["member_id"] == m0)
check("participant share for m0 matches backend row",
      m0_again["total"] == by_id[m0]["total"], f'{m0_again["total"]} vs {by_id[m0]["total"]}')

# 6. Unknown-member assignments are dropped (validation), not trusted.
bad = client.post(f"/api/split/{a}/orders", json={"items": [
    {"id": "x", "name": "Ghost", "price": 10, "quantity": 1,
     "assigned_to": ["P-NOPE"], "shared_by": [], "category": "personal"}]}).json()
check("unknown assignee dropped -> item unassigned, total 0",
      bad.get("total") == 0 and "Ghost" in (bad.get("unassigned_items") or []),
      f'total={bad.get("total")} unassigned={bad.get("unassigned_items")}')

# 7. Bad body -> 400.
r = client.post(f"/api/split/{a}/orders", json={"items": "nope"})
check("non-list items -> 400", r.status_code == 400, str(r.status_code))

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
