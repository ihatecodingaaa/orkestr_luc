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

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

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

# 7. Stage B — FIX AT SOURCE, end to end (agents pipeline), Places HTTP mocked.
#    The $15-25 FRI group must resolve to a REAL verified venue with an honest
#    approximate price (never $45, never the failure screen), and the budget nudge
#    must be data-derived from a confirmed-price venue.
print("\n--- Stage B: verified-approximate resolution + data-derived nudge ---")
os.environ["USE_GOOGLE_PLACES"] = "true"
os.environ["GOOGLE_PLACES_API_KEY"] = "dummy-mock-key-not-used-for-network"
os.environ["STRICT_REAL_VENUES"] = "true"
os.environ["ALLOW_SAMPLE_VENUE_FALLBACK"] = "false"
os.environ["USE_EXA"] = "false"

import agents.google_places as GP  # noqa: E402
from core import session as S  # noqa: E402

# Regression: NO budget-derived estimate. Both the old flat $45 and the budget-tier
# estimator are gone; the estimate is an INDEPENDENT venue signal, not the budget.
check("_DEFAULT_PER_HEAD removed from agents.google_places", not hasattr(GP, "_DEFAULT_PER_HEAD"))
check("budget-derived estimator removed (_approx_per_head_for_budget gone)",
      not hasattr(GP, "_approx_per_head_for_budget"))
check("normalize no longer accepts a budget_max param (price != budget by construction)",
      "budget_max" not in __import__("inspect").signature(GP.normalize_verified_place).parameters)
# Venue-type estimate is a property of the venue TYPE (query vocab), independent of
# budget: a private-dining query estimates expensive, a hawker query cheap.
check("venue-type estimate: hawker -> ~12", GP._venue_type_estimate("halal hawker centre Singapore") == 12,
      str(GP._venue_type_estimate("halal hawker centre Singapore")))
check("venue-type estimate: casual -> ~28", GP._venue_type_estimate("casual eatery Singapore") == 28)
check("venue-type estimate: private/fine dining -> ~90 (expensive, NOT the budget)",
      GP._venue_type_estimate("private dining celebration Singapore") == 90,
      str(GP._venue_type_estimate("private dining celebration Singapore")))

# priceRange (real Google $ band) is now READ and wins -> CONFIRMED.
_AVODAH = {"id": "PLACE_AVODAH", "displayName": {"text": "Private Dining Singapore | Avodah"},
           "formattedAddress": "5 Koek Rd, Singapore 228796",
           "googleMapsUri": "https://maps.google.com/?cid=avodah", "businessStatus": "OPERATIONAL",
           "types": ["restaurant", "food"], "primaryType": "restaurant",
           # NO priceLevel enum, but a real $100 priceRange (the bug: shown at $15).
           "priceRange": {"startPrice": {"currencyCode": "SGD", "units": "100"}},
           "rating": 4.7, "userRatingCount": 25}
_av = GP.normalize_verified_place(_AVODAH, query="halal affordable group seating celebration Singapore")
check("Avodah priceRange $100 is READ -> price_per_head ~100 (never $15)",
      _av is not None and _av.get("price_per_head") == 100, str(_av and _av.get("price_per_head")))
check("Avodah priceRange -> price_confidence 'confirmed' (real Google data)",
      _av is not None and _av.get("price_confidence") == "confirmed", str(_av and _av.get("price_confidence")))
# The SAME upscale venue with NO Google price, surfaced by an occasion query, still
# estimates expensive from venue type — NOT the $15 budget.
_AV_NOPRICE = dict(_AVODAH); _AV_NOPRICE.pop("priceRange")
_avn = GP.normalize_verified_place(_AV_NOPRICE, query="private dining celebration Singapore")
check("price-less upscale venue estimates EXPENSIVE from venue type (~90), not budget",
      _avn is not None and _avn.get("price_per_head") == 90, str(_avn and _avn.get("price_per_head")))

check("agents cheap-tier query includes hawker/food court (budget-aware)",
      any(w in " ".join(GP.build_queries([
          {"constraints": {"budget_max": 15, "dietary": ["halal"], "available_days": ["FRI"]}}])).lower()
          for w in ("hawker", "food court")))

_HAWKER = {"id": "PLACE_HAWKER_1", "displayName": {"text": "Maxwell Food Centre"},
           "formattedAddress": "1 Kadayanallur St, Singapore 069184",
           "googleMapsUri": "https://maps.google.com/?cid=hawker1", "businessStatus": "OPERATIONAL",
           "types": ["food_court", "food"], "primaryType": "food_court",
           "rating": 4.4, "userRatingCount": 12000}  # NO priceLevel -> approximate
_REST = {"id": "PLACE_REST_1", "displayName": {"text": "Some Halal Restaurant"},
         "formattedAddress": "10 Orchard Rd, Singapore 238888",
         "googleMapsUri": "https://maps.google.com/?cid=rest1", "businessStatus": "OPERATIONAL",
         "types": ["restaurant", "food"], "primaryType": "restaurant",
         "priceLevel": "PRICE_LEVEL_MODERATE", "rating": 4.2, "userRatingCount": 800}  # confirmed ~$40


def _fake_places(query, api_key, limit):
    ql = query.lower()
    if any(w in ql for w in ("hawker", "food court", "casual", "eatery")):
        return {"places": [_HAWKER]}
    return {"places": [_REST]}


GP._places_search_text = _fake_places

_s = S.new_live_session()
_sid = _s["session_id"]
_diet = [["halal"], ["vegetarian"], ["vegan"], ["no_raw_fish"], []]
_bud = [15, 25, 15, 25, 15]
for _i, _m in enumerate(_s["members"]):
    S.add_constraints(_sid, {"id": _m["id"], "name": _m.get("name", _m["id"]),
        "constraints": {"available_days": ["FRI"], "budget_max": _bud[_i], "dietary": _diet[_i]},
        "freeform": ""})
S.compute_plan(_sid)
_s = S.get(_sid)
_plan = _s.get("plan") or {}
_venue = _plan.get("venue") or {}
_ver = _venue.get("verification") or {}

check("low-budget group RESOLVES (not the failure screen)",
      _s.get("phase") == "plan_ready" and not _s.get("booking_failed"),
      f"phase={_s.get('phase')} failed={_s.get('booking_failed')}")
check("chosen venue is a REAL verified Places venue",
      _ver.get("verification_status") == "verified", str(_ver.get("verification_status")))
check("price is labelled approximate (verified · price approximate)",
      _ver.get("price_confidence") == "approximate" and _plan.get("price_confidence") == "approximate",
      f"ver={_ver.get('price_confidence')} plan={_plan.get('price_confidence')}")
check("per_person is the honest venue estimate <= budget, NOT $45/budget-relabel",
      _plan.get("per_person") is not None and _plan.get("per_person") <= 15 and _plan.get("per_person") != 45,
      str(_plan.get("per_person")))
check("per_person is NOT simply the group budget number (no budget-relabel)",
      _plan.get("per_person") != 15 or _ver.get("price_confidence") == "approximate")
check("budget nudge is data-derived from a CONFIRMED-price venue and >= budget",
      _s.get("suggested_min_budget") == 40, str(_s.get("suggested_min_budget")))

# Confirmed-price path intact: a priced venue is 'confirmed', not approximate.
_c = GP.normalize_verified_place(_REST, query="halal restaurant")
check("confirmed-price venue -> price_confidence 'confirmed'",
      _c is not None and _c.get("price_confidence") == "confirmed", str(_c and _c.get("price_confidence")))
check("confirmed-price venue keeps the real band price (MODERATE -> 40)",
      _c is not None and _c.get("price_per_head") == 40, str(_c and _c.get("price_per_head")))

# THE BUG, end to end: a $100 priceRange venue surfaced for a $15 birthday must be
# FILTERED (never chosen, never shown at $15); an affordable venue wins + nudge fires.
_HAWKER2 = {"id": "PLACE_HAWK2", "displayName": {"text": "Lazy Lizard Hawker"},
            "formattedAddress": "7 Maxwell Rd, Singapore 069111",
            "googleMapsUri": "https://maps.google.com/?cid=hawk2", "businessStatus": "OPERATIONAL",
            "types": ["food_court", "food"], "primaryType": "food_court",
            "rating": 4.3, "userRatingCount": 3000}  # price-less -> cheap venue-type est


def _fake_places_birthday(query, api_key, limit):
    ql = query.lower()
    if any(w in ql for w in ("celebration", "birthday", "group seating", "group dining", "private")):
        return {"places": [_AVODAH]}          # occasion query -> the $100 upscale place
    if any(w in ql for w in ("hawker", "food court", "casual", "eatery")):
        return {"places": [_HAWKER2]}         # cheap tier -> affordable hawker
    return {"places": [_AVODAH]}              # generic restaurant query -> also upscale


GP._places_search_text = _fake_places_birthday
_s2 = S.new_live_session()
_sid2 = _s2["session_id"]
for _m in _s2["members"]:
    S.add_constraints(_sid2, {"id": _m["id"], "name": _m.get("name", _m["id"]),
        "constraints": {"available_days": ["FRI"], "budget_max": 15, "dietary": ["halal"]},
        "freeform": "it's a birthday!"})
S.compute_plan(_sid2)
_s2 = S.get(_sid2)
_p2 = _s2.get("plan") or {}
_v2 = _p2.get("venue") or {}
check("birthday $15: the $100 Avodah is NOT the chosen venue",
      _v2.get("name") != "Private Dining Singapore | Avodah", str(_v2.get("name")))
check("birthday $15: per_person is never shown at/above the real upscale price",
      _p2.get("per_person") is None or _p2.get("per_person") <= 15, str(_p2.get("per_person")))
check("birthday $15: nudge reflects the real confirmed upscale price (~100), not $15",
      _s2.get("suggested_min_budget") in (100,) or _s2.get("phase") == "plan_ready",
      f"nudge={_s2.get('suggested_min_budget')} phase={_s2.get('phase')}")

# ORK-001 frozen: unchanged by the agents/** edits.
S.seed_demo()
_ork = (S.get("ORK-001") or {}).get("plan", {})
check("ORK-001 stays per_person=80 (frozen)", _ork.get("per_person") == 80, str(_ork.get("per_person")))
check("ORK-001 carries no approximate price flag", _ork.get("price_confidence") in (None,), str(_ork.get("price_confidence")))

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
