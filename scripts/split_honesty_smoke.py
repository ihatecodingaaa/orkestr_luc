"""Stage 0 smoke: split presentation honesty.

The itemized split used to render a rotating DRAFT menu (a sample keyed off the
session id) as if it were the restaurant's real food. Stage 0 makes the UI honest:

  * a live room defaults to the EQUAL split (real, cent-exact, reconciling) and
    never shows draft/sample dish names as if real;
  * demo mode may still showcase itemized, but only under a persistent
    "Sample items" label;
  * the backend split_source is the single flag the UI keys off — only
    backend_live_orders is a REAL captured order; every other source
    (backend_session_draft, local_demo_preview, ...) is a sample.

This asserts the backend CONTRACT the UI relies on, plus the frontend guard
constants that enforce it. Runs in-process via TestClient (no server, no network,
no payment). Exit 0 = all pass, 1 = any failure.
"""
import os
import re
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
        print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))
        _failures.append(label)


def cents(x):
    return int(round(float(x) * 100))


print("Split honesty smoke (Stage 0)")
print("=" * 60)

# --- A fresh live room: the split is a session DRAFT, not real captured orders. ---
made = client.post("/api/session/live", json={}).json()
sid = made["session_id"]
host = {"X-Orkestr-Token": made.get("host_token") or ""}

# 1. The default draft split is a SAMPLE source, never backend_live_orders. This
#    is the exact flag the UI uses to suppress/label sample dish names.
draft = client.get(f"/api/split/{sid}").json()
check("fresh room split_source is a sample (backend_session_draft, not live orders)",
      draft.get("split_source") == "backend_session_draft",
      str(draft.get("split_source")))
check("fresh room split_source is NOT backend_live_orders (would read as real)",
      draft.get("split_source") != "backend_live_orders",
      str(draft.get("split_source")))

# 2. The EQUAL split (the live-room default) is real and reconciles to the cent.
eq = client.get(f"/api/split/{sid}", params={"mode": "equal"}).json()
check("equal split mode == equal", eq.get("mode") == "equal", str(eq.get("mode")))
check("equal split reconciles (totals_reconcile)", eq.get("totals_reconcile") is True,
      str(eq.get("totals_reconcile")))
check("equal split: sum(per_person.total) == total exactly",
      sum(cents(p["total"]) for p in eq["per_person"]) == cents(eq["total"]),
      f'{sum(cents(p["total"]) for p in eq["per_person"])} vs {cents(eq["total"])}')
# Equal shares differ by at most 1 cent (largest-remainder), so no one is stiffed.
eq_totals = sorted(cents(p["total"]) for p in eq["per_person"])
check("equal split: shares differ by <= 1 cent",
      (eq_totals[-1] - eq_totals[0]) <= 1,
      f"spread={eq_totals[-1] - eq_totals[0]}c")

# 3. Equal split totals match the itemized total exactly (same bill, fair reallocation).
it = client.get(f"/api/split/{sid}", params={"mode": "itemized"}).json()
check("equal and itemized share the same bill total",
      cents(eq["total"]) == cents(it["total"]),
      f'{eq["total"]} vs {it["total"]}')

# --- Real captured orders: the ONLY source the UI shows as real dish names. ---
members = [p["member_id"] for p in draft["per_person"]]
m0, m1 = members[0], members[1]
real_items = [
    {"id": "a", "name": "Ribeye", "price": 40.0, "quantity": 1,
     "assigned_to": [m0], "shared_by": [], "category": "personal"},
    {"id": "b", "name": "Salad", "price": 12.0, "quantity": 1,
     "assigned_to": [m1], "shared_by": [], "category": "personal"},
]
real = client.post(f"/api/split/{sid}/orders", headers=host, json={"items": real_items}).json()
check("entered orders flip split_source to backend_live_orders (real)",
      real.get("split_source") == "backend_live_orders",
      str(real.get("split_source")))
real_eq = client.get(f"/api/split/{sid}", params={"mode": "equal"}).json()
check("real orders: equal split still reconciles",
      real_eq.get("totals_reconcile") is True and
      sum(cents(p["total"]) for p in real_eq["per_person"]) == cents(real_eq["total"]),
      str(real_eq.get("totals_reconcile")))

# --- Frontend guard: the UI enforces the same contract off split_source. ---
root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
        return fh.read()


split_ts = _read("src/lib/split.ts")
check("frontend defines SAMPLE_ITEMS_LABEL (persistent sample label)",
      "SAMPLE_ITEMS_LABEL" in split_ts and "not this restaurant" in split_ts,
      "missing label constant")
check("frontend isSampleSplit treats only backend_live_orders as real",
      re.search(r'isSampleSplit[\s\S]{0,200}backend_live_orders', split_ts) is not None,
      "isSampleSplit not keyed on backend_live_orders")

smart = _read("src/components/live/SmartSplitCard.tsx")
check("SmartSplitCard hides item names in a live room (liveRoom + sample)",
      "hideItemNames" in smart and "liveRoom" in smart,
      "no live-room suppression")
check("SmartSplitCard gates the itemized tab on allowItemized",
      "allowItemized" in smart,
      "itemized tab not gated")

part = _read("src/components/live/ParticipantView.tsx")
check("ParticipantView requests the equal split (never draft dishes as real)",
      'useSplit(roomId, "equal"' in part,
      "participant still requests itemized")

group = _read("src/components/live/GroupRoom.tsx")
check("GroupRoom defaults live rooms to the equal split",
      'useState<SplitMode>(isLive ? "equal" : "itemized")' in group,
      "live default not equal")

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
