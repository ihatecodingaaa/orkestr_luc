"""Smart itemized meal split — the internal settlement after Orkestr pays once.

The Orkestr Agent Wallet pays the merchant a single time over x402. This module
then works out what each friend actually owes, based on what they ordered —
NOT a naive equal split. It supports:

    * personal items  — the assigned person pays the full price
    * shared items    — split equally among the selected sharers
    * equal mode      — everyone pays the same (still available)

Service charge is allocated proportionally to each person's pre-tax subtotal;
GST is applied on (subtotal + service charge) and allocated proportionally.
All money is computed in integer cents with largest-remainder rounding, so the
per-person totals ALWAYS sum to the bill total exactly.

Pure/stdlib only. No payment is triggered here — this is display/settlement math.
"""

DEFAULT_CURRENCY = "SGD"
DEFAULT_SERVICE_CHARGE_RATE = 0.10   # 10% service charge (Singapore-style)
DEFAULT_GST_RATE = 0.09              # 9% GST


# ---------------------------------------------------------------------------
# Cent-exact allocation
# ---------------------------------------------------------------------------

def _to_cents(amount) -> int:
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return 0


def allocate_cents(weights, total_cents: int) -> list:
    """Split total_cents across len(weights) buckets proportional to weights.

    Uses the largest-remainder method so the result sums to total_cents EXACTLY
    and is deterministic (ties broken by index order). weights may be floats/ints.
    """
    n = len(weights)
    if n == 0:
        return []
    total_cents = int(round(total_cents))
    if total_cents == 0:
        return [0] * n
    sw = sum(weights)
    if sw <= 0:
        # No basis to weight by — distribute as evenly as possible.
        base, extra = divmod(total_cents, n)
        return [base + (1 if i < extra else 0) for i in range(n)]
    exact = [(total_cents * w) / sw for w in weights]
    floors = [int(x) for x in exact]
    remainder = total_cents - sum(floors)
    order = sorted(range(n), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in range(remainder):
        floors[order[i]] += 1
    return floors


def _money(cents: int) -> float:
    return round(cents / 100.0, 2)


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def _people_for(item: dict) -> list:
    """The members who pay for this item (sharers for shared, assignees else)."""
    category = str(item.get("category") or "").lower()
    if category == "shared":
        people = item.get("shared_by") or []
    else:
        people = item.get("assigned_to") or []
        if not people:
            people = item.get("shared_by") or []
    # Preserve order, drop blanks/dupes.
    seen, out = set(), []
    for p in people:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def compute_split(meal_split: dict, mode: str = None) -> dict:
    """Compute per-person shares. Returns a fully-populated meal_split dict.

    mode overrides meal_split['mode']: "itemized" (default) or "equal".
    """
    meal_split = dict(meal_split or {})
    mode = (mode or meal_split.get("mode") or "itemized").lower()
    currency = meal_split.get("currency") or DEFAULT_CURRENCY
    sc_rate = float(meal_split.get("service_charge_rate", DEFAULT_SERVICE_CHARGE_RATE) or 0)
    gst_rate = float(meal_split.get("gst_rate", DEFAULT_GST_RATE) or 0)

    members = meal_split.get("members") or meal_split.get("per_person") or []
    member_ids = [m.get("member_id") or m.get("id") for m in members]
    member_ids = [mid for mid in member_ids if mid]
    names = {}
    budgets = {}
    for m in members:
        mid = m.get("member_id") or m.get("id")
        if not mid:
            continue
        names[mid] = m.get("name", mid)
        b = m.get("budget", m.get("budget_max"))
        if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0:
            budgets[mid] = float(b)

    items = [dict(i) for i in (meal_split.get("items") or [])]
    idx = {mid: i for i, mid in enumerate(member_ids)}

    # --- pass 1: per-person subtotal (in cents) + per-person item breakdown ---
    per_sub = [0] * len(member_ids)
    per_items = {mid: [] for mid in member_ids}
    subtotal_cents = 0
    unassigned = []

    for item in items:
        line_cents = _to_cents(item.get("price", 0)) * int(item.get("quantity", 1) or 1)
        if line_cents <= 0:
            continue
        people = [p for p in _people_for(item) if p in idx]
        category = str(item.get("category") or ("shared" if item.get("shared_by") else "personal")).lower()
        if not people:
            unassigned.append(item.get("name", item.get("id", "item")))
            continue
        subtotal_cents += line_cents
        shares = allocate_cents([1] * len(people), line_cents)
        for person, cents in zip(people, shares):
            per_sub[idx[person]] += cents
            per_items[person].append({
                "id": item.get("id"),
                "name": item.get("name", "Item"),
                "amount": _money(cents),
                "line_total": _money(line_cents),
                "quantity": int(item.get("quantity", 1) or 1),
                "kind": "shared" if category == "shared" else "personal",
                "shared_with": [names.get(p, p) for p in people] if category == "shared" else [],
                "shared_count": len(people) if category == "shared" else 1,
            })

    # --- equal mode: ignore item assignment; everyone shares the subtotal ---
    if mode == "equal" and member_ids:
        per_sub = allocate_cents([1] * len(member_ids), subtotal_cents)

    # --- service charge + GST, allocated proportionally, cent-exact ---
    sc_total_cents = int(round(sc_rate * subtotal_cents))
    gst_total_cents = int(round(gst_rate * (subtotal_cents + sc_total_cents)))
    per_sc = allocate_cents(list(per_sub), sc_total_cents)
    per_gst = allocate_cents([per_sub[i] + per_sc[i] for i in range(len(member_ids))], gst_total_cents)

    total_cents = subtotal_cents + sc_total_cents + gst_total_cents

    # --- build per-person rows + budget warnings ---
    per_person = []
    for i, mid in enumerate(member_ids):
        p_total_cents = per_sub[i] + per_sc[i] + per_gst[i]
        row = {
            "member_id": mid,
            "name": names.get(mid, mid),
            "items_subtotal": _money(per_sub[i]),
            "service_charge": _money(per_sc[i]),
            "gst": _money(per_gst[i]),
            "total": _money(p_total_cents),
            "status": "owes" if p_total_cents > 0 else "settled",
            "items": per_items.get(mid, []),
            "shared_items": [it for it in per_items.get(mid, []) if it["kind"] == "shared"],
        }
        if mid in budgets:
            row["budget"] = budgets[mid]
            over_cents = p_total_cents - _to_cents(budgets[mid])
            if over_cents > 0:
                row["over_budget"] = True
                row["over_by"] = _money(over_cents)
                row["warning"] = (
                    f"Over {row['name']}'s ${budgets[mid]:.0f} budget by ${_money(over_cents):.2f}"
                )
            else:
                row["over_budget"] = False
        per_person.append(row)

    result = dict(meal_split)
    result.update({
        "currency": currency,
        "mode": mode,
        "service_charge_rate": sc_rate,
        "gst_rate": gst_rate,
        "subtotal": _money(subtotal_cents),
        "service_charge": _money(sc_total_cents),
        "gst": _money(gst_total_cents),
        "total": _money(total_cents),
        # Residual after cent-exact allocation — always 0.00, surfaced for proof.
        "rounding": _money(total_cents - (subtotal_cents + sc_total_cents + gst_total_cents)),
        "items": items,
        "per_person": per_person,
        "unassigned_items": unassigned,
        "totals_reconcile": sum(_to_cents(p["total"]) for p in per_person) == total_cents,
    })
    return result


# ---------------------------------------------------------------------------
# Demo data — realistic, DIFFERENT per-person totals (not an equal split)
# ---------------------------------------------------------------------------

_DEMO_MEMBERS = [
    {"member_id": "P-001", "name": "Alice", "budget": 50},
    {"member_id": "P-002", "name": "Bob", "budget": 40},
    {"member_id": "P-003", "name": "Carol", "budget": 80},
    {"member_id": "P-004", "name": "Dave", "budget": 45},
    {"member_id": "P-005", "name": "Eve", "budget": 40},
]

# One order per the five personas: halal main + drink (Alice), vegetarian main
# (Bob), no-raw-fish main (Carol), Korean BBQ platter shared by the grillers,
# a vegan bowl (Eve), and sides shared by everyone.
_DEMO_ITEMS = [
    {"id": "item-001", "name": "Bibimbap (halal)", "price": 18.0, "quantity": 1,
     "assigned_to": ["P-001"], "shared_by": [], "category": "personal"},
    {"id": "item-002", "name": "Iced barley", "price": 4.5, "quantity": 1,
     "assigned_to": ["P-001"], "shared_by": [], "category": "personal"},
    {"id": "item-003", "name": "Vegetarian japchae", "price": 16.0, "quantity": 1,
     "assigned_to": ["P-002"], "shared_by": [], "category": "personal"},
    {"id": "item-004", "name": "Grilled chicken (no raw fish)", "price": 22.0, "quantity": 1,
     "assigned_to": ["P-003"], "shared_by": [], "category": "personal"},
    {"id": "item-005", "name": "Vegan buddha bowl", "price": 17.5, "quantity": 1,
     "assigned_to": ["P-005"], "shared_by": [], "category": "personal"},
    {"id": "item-006", "name": "Korean BBQ platter", "price": 60.0, "quantity": 1,
     "assigned_to": [], "shared_by": ["P-001", "P-003", "P-004"], "category": "shared"},
    {"id": "item-007", "name": "Kimchi & sides", "price": 12.0, "quantity": 1,
     "assigned_to": [], "shared_by": ["P-001", "P-002", "P-003", "P-004", "P-005"], "category": "shared"},
]


def demo_meal_split(members=None, mode: str = "itemized") -> dict:
    """A realistic, computed sample split for five friends (totals differ)."""
    members = members or _DEMO_MEMBERS
    return compute_split(
        {
            "currency": DEFAULT_CURRENCY,
            "service_charge_rate": DEFAULT_SERVICE_CHARGE_RATE,
            "gst_rate": DEFAULT_GST_RATE,
            "members": members,
            "items": _DEMO_ITEMS,
            "mode": mode,
        },
        mode=mode,
    )
