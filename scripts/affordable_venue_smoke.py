"""Smoke for budget-aware venue queries (Fix 1).

Offline / unit-level: asserts the query builder in ai/venue_search.py biases the
Places textQuery by the per-head budget band, so a $10-20 budget searches for
affordable hawker/food-court/casual venues instead of dead-ending on sit-down
"restaurant" queries:

  * budget_max <= 20 -> affordable vocabulary (hawker centre / food court /
    casual eatery), a SMALL SET of queries (>1, capped at 3), no "restaurant";
  * budget_max <= 40 -> "casual ... restaurant" (single query);
  * higher / no budget -> the original "<cuisine> restaurant in <loc>" phrasing;
  * build_text_query stays the primary (first) query and defaults unchanged.

No network, no API key, no payment. Exit 0 = all pass, 1 = any failure.

Optionally, if GOOGLE_PLACES_API_KEY is present, a single live check confirms a
low-budget query returns at least one real venue; skipped cleanly otherwise.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai import venue_search as V

_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


print("Affordable venue query smoke (Fix 1)")
print("=" * 60)

LOC = "Singapore"
CZ = "korean"

# 1. Low band (<= 20): affordable vocabulary, multi-query, no "restaurant".
low = V.build_text_queries(LOC, CZ, 18)
low_blob = " || ".join(low).lower()
check("<=20 issues a small set (>1) of queries", 1 < len(low) <= 3, f"n={len(low)} {low}")
check("<=20 uses 'hawker' vocabulary", "hawker" in low_blob, low_blob)
check("<=20 uses 'food court' vocabulary", "food court" in low_blob, low_blob)
check("<=20 uses 'casual' + 'eatery' vocabulary",
      "casual" in low_blob and "eatery" in low_blob, low_blob)
check("<=20 does NOT dead-end on 'restaurant'", "restaurant" not in low_blob, low_blob)
check("<=20 boundary (==20) is still affordable",
      "hawker" in " ".join(V.build_text_queries(LOC, CZ, 20)).lower())

# 2. Mid band (<= 40): 'casual ... restaurant', single query.
mid = V.build_text_queries(LOC, CZ, 35)
mid_blob = " || ".join(mid).lower()
check("<=40 is a single query", len(mid) == 1, str(mid))
check("<=40 uses 'casual' vocabulary", "casual" in mid_blob, mid_blob)
check("<=40 still a restaurant (sit-down casual)", "restaurant" in mid_blob, mid_blob)

# 3. High band (> 40) and no budget: original '<cuisine> restaurant in <loc>'.
high = V.build_text_queries(LOC, CZ, 80)
check("high band single query", len(high) == 1, str(high))
check("high band == '<cuisine> restaurant in <loc>'",
      high[0] == f"{CZ} restaurant in {LOC}", high[0])
check("high band has no 'casual'/'hawker'",
      "casual" not in high[0].lower() and "hawker" not in high[0].lower(), high[0])

none_budget = V.build_text_queries(LOC, CZ, None)
check("no budget == '<cuisine> restaurant in <loc>'",
      none_budget == [f"{CZ} restaurant in {LOC}"], str(none_budget))

# 4. build_text_query is the PRIMARY (first) query; default arg unchanged.
check("build_text_query returns the first band query",
      V.build_text_query(LOC, CZ, 18) == low[0], V.build_text_query(LOC, CZ, 18))
check("build_text_query default (no budget) unchanged",
      V.build_text_query(LOC, CZ) == f"{CZ} restaurant in {LOC}", V.build_text_query(LOC, CZ))

# 5. Defensive: missing location/cuisine still yields sane strings, never crashes.
d = V.build_text_queries(None, None, 15)
check("missing loc/cuisine still builds affordable queries", len(d) > 1 and all(d), str(d))

# 6. Optional live check — only if a real key is present; skip cleanly otherwise.
api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
if api_key:
    venues, _photo, est = V.search_venues(LOC, CZ, 18, [], 5, api_key)
    check("live low-budget search returns >=1 venue", bool(venues),
          f"got {len(venues) if venues else 0}")
    check("live search returns a per-head estimate tuple element",
          est is None or isinstance(est, (int, float)), str(est))
else:
    print("  [SKIP] live Places check — GOOGLE_PLACES_API_KEY not set")

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
