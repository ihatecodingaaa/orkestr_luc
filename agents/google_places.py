"""Google Places verification for Orkestr (server-side only).

Uses the Google Places API (New) Text Search to confirm that a candidate venue
is a REAL, operational restaurant in Singapore. Only runs when USE_GOOGLE_PLACES
is true AND GOOGLE_PLACES_API_KEY is present.

HARD RULES honored here:
    * The API key is read from the environment, sent only in the request header,
      and NEVER logged, printed, or returned to any caller/response.
    * Only the useful fields are requested via an explicit field mask.
    * Never raises to callers — any failure returns an empty result.
    * All flags default OFF, so the existing MVP is unaffected.

A venue is treated as Google-verified only when it has a place id, a display
name, a formatted address, a Google Maps URI, is located in Singapore, is
OPERATIONAL (if businessStatus is returned), and its types indicate a place you
actually eat at (restaurant/food/cafe/meal service) — not a mall, region, blog,
or directory page.
"""

import json
import os
import urllib.error
import urllib.request

from agents.venue_grounding import blank_candidate, source_domain
from ai.venue_search import budget_venue_terms  # single shared budget->vocabulary helper
from ai.venue_ranker import detect_occasion  # occasion signals for occasion-aware queries

_PLACES_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

# Exactly the fields we use — keeps the request cheap and avoids over-collecting.
_FIELD_MASK = ",".join((
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.googleMapsUri",
    "places.websiteUri",
    "places.businessStatus",
    "places.types",
    "places.primaryType",
    "places.priceLevel",
    "places.priceRange",   # real per-person price band (startPrice/endPrice) — the
                           # $ figure Google shows; upscale venues often have this
                           # even when priceLevel is blank.
    "places.rating",
    "places.userRatingCount",
))

# Types that indicate a place you eat/drink at.
_FOOD_TYPES = frozenset({
    "restaurant", "food", "cafe", "bakery", "bar", "meal_takeaway",
    "meal_delivery", "food_court", "fast_food_restaurant", "coffee_shop",
    "vegetarian_restaurant", "vegan_restaurant", "korean_restaurant",
    "japanese_restaurant", "sushi_restaurant", "halal_restaurant",
    "asian_restaurant", "chinese_restaurant", "indian_restaurant",
    "seafood_restaurant", "barbecue_restaurant", "buffet_restaurant",
})

# Types that mean "this is not a single dining venue".
_NON_VENUE_TYPES = frozenset({
    "shopping_mall", "locality", "sublocality", "neighborhood",
    "political", "postal_code", "route", "administrative_area_level_1",
    "administrative_area_level_2", "country", "travel_agency",
    "real_estate_agency", "lodging",
})

_PRICE_LEVEL_HINT = {
    "PRICE_LEVEL_FREE": "$",
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}

# Rough SGD per-head estimate by price level so budget filtering still works for
# a freshly-discovered venue (Places does not return a per-head price). A venue
# that carries one of these price levels is CONFIRMED-price.
_PRICE_LEVEL_PER_HEAD = {
    "PRICE_LEVEL_FREE": 15,
    "PRICE_LEVEL_INEXPENSIVE": 20,
    "PRICE_LEVEL_MODERATE": 40,
    "PRICE_LEVEL_EXPENSIVE": 70,
    "PRICE_LEVEL_VERY_EXPENSIVE": 110,
}


# Venue-TYPE per-head price bands (SGD): an INDEPENDENT signal about the venue,
# inferred from the query vocabulary that surfaced it (a proxy for venue type). This
# is NEVER the group's budget — a $90 private-dining place estimates $90 even for a
# $15 group, so the affordability filter can then correctly drop it. Ordered most-
# specific first (hawker before restaurant; upscale before casual).
_VENUE_TYPE_PER_HEAD = (
    (("hawker", "food court", "food centre", "kopitiam"), 12),
    (("private dining", "fine dining", "fine-dining", "omakase", "degustation",
      "chef's table", "tasting menu", "upscale", "premium", "luxury"), 90),
    (("casual", "eatery", "cafe", "diner", "bistro", "kopi"), 28),
    (("restaurant", "group seating", "celebration", "group dining", "birthday",
      "anniversary"), 45),
)
_DEFAULT_TYPE_PER_HEAD = 35


def _venue_type_estimate(query):
    """Independent per-head estimate for a venue Google gave NO price for, derived
    from the query vocabulary that matched it (venue TYPE) — never the group budget.
    The caller marks it price_confidence="approximate"."""
    q = str(query or "").lower()
    for keys, price in _VENUE_TYPE_PER_HEAD:
        if any(k in q for k in keys):
            return price
    return _DEFAULT_TYPE_PER_HEAD


def _price_from_range(place):
    """Per-head SGD from the Places (New) priceRange (startPrice/endPrice units), or
    None. This is Google's REAL price band — a confirmed fact about the venue. For a
    one-sided "$100+" range (startPrice only) we use that value; a two-sided range
    uses the midpoint so a $50-100 place reads ~$75, not $50."""
    pr = place.get("priceRange")
    if not isinstance(pr, dict):
        return None

    def _units(side):
        v = pr.get(side)
        if not isinstance(v, dict):
            return None
        try:
            return int(str(v.get("units")))
        except (TypeError, ValueError):
            return None

    lo, hi = _units("startPrice"), _units("endPrice")
    if lo is not None and hi is not None:
        return int(round((lo + hi) / 2))
    return hi if hi is not None else lo


def _group_budget_min(personas):
    """Lowest stated per-head budget across the group, or None."""
    budgets = []
    for p in personas or []:
        b = ((p or {}).get("constraints") or {}).get("budget_max")
        if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0:
            budgets.append(float(b))
    return min(budgets) if budgets else None


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

def _flag_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("true", "1", "yes")


def is_enabled() -> bool:
    """True only when explicitly enabled AND a key is configured (server-side)."""
    return _flag_bool("USE_GOOGLE_PLACES") and bool(os.environ.get("GOOGLE_PLACES_API_KEY", "").strip())


# ---------------------------------------------------------------------------
# Default discovery queries (server may still pass tailored ones)
# ---------------------------------------------------------------------------

_DEFAULT_QUERIES = (
    "halal vegetarian restaurant Singapore",
    "Korean BBQ restaurant Singapore group seating",
    "vegan restaurant near MRT Singapore",
    "restaurant HarbourFront Singapore vegetarian",
    "restaurant Singapore CBD halal vegetarian",
    "group dinner restaurant Singapore halal vegetarian",
)


def build_queries(personas=None, limit: int = 4) -> list:
    """Tailor a few text-search queries from the group's real constraints, BUDGET-
    AWARE. A low-budget group ($15-25) searches hawker/food-court/casual venues (via
    the shared budget_venue_terms helper) instead of only sit-down "restaurant" — so
    Places actually returns affordable, verifiable venues. Dietary + area are always
    preserved. One "restaurant"-tier query is always included so the budget nudge
    has real confirmed-price data even for a cheap group.
    """
    if not personas:
        return list(_DEFAULT_QUERIES[:limit])

    dietary: set = set()
    areas: set = set()
    for p in personas:
        c = (p or {}).get("constraints") or {}
        for d in c.get("dietary") or []:
            d = str(d).lower()
            if d in ("halal", "vegetarian", "vegan"):
                dietary.add(d)
            elif d == "no_raw_fish":
                dietary.add("cooked")
        for key in ("area", "location", "near", "origin"):
            val = c.get(key)
            if val:
                areas.add(str(val))

    budget_min = _group_budget_min(personas)
    diet_part = " ".join(sorted(dietary)) if dietary else ""
    area_part = f"near {sorted(areas)[0]}" if areas else ""
    # Occasion signals from the group's free-text notes -> extra real-Places query
    # terms so occasion-appropriate venues (group seating, private room) are in the
    # pool. Still real results only; the ranker later prefers them.
    _notes = " ".join(str((p or {}).get("freeform") or "") for p in personas)
    occasion_terms = detect_occasion(_notes).get("query_terms") or []

    # Budget-tier venue vocabulary (hawker/food-court/casual for a cheap group).
    terms = list(budget_venue_terms(budget_min))
    # Always keep one sit-down "restaurant" tier query so the budget nudge can be
    # derived from real CONFIRMED-price venues even when the group is low-budget.
    if not any("restaurant" in t for t in terms):
        terms.append("restaurant")

    queries = []
    for term in terms:
        parts = [diet_part, term, area_part, "Singapore"]
        q = " ".join(part for part in parts if part).strip()
        if q:
            queries.append(q)
    # One occasion-flavoured query (real search) when an occasion was detected. It
    # INHERITS the group's budget band: occasion refines WITHIN budget, it never
    # overrides it. A cheap group gets an affordable/casual celebration query (so an
    # upscale private-dining venue is not even surfaced), a high-budget group can
    # reach upscale occasion venues. The budget filter is re-applied to the result
    # regardless, using the venue's real/independent price.
    if occasion_terms:
        if budget_min is not None and budget_min <= 20:
            modifier = "affordable"
        elif budget_min is not None and budget_min <= 40:
            modifier = "casual"
        else:
            modifier = ""  # high budget may reach upscale occasion venues
        occ_q = " ".join(part for part in [diet_part, modifier, occasion_terms[0], "Singapore"]
                         if part).strip()
        if occ_q and occ_q not in queries:
            queries.append(occ_q)
    # Top up with defaults only when we still have room AND the group is not low-
    # budget (so a cheap group is never dragged back to sit-down defaults).
    if budget_min is None or budget_min > 20:
        for q in _DEFAULT_QUERIES:
            if len(queries) >= max(1, limit):
                break
            if q not in queries:
                queries.append(q)
    # De-dupe, preserve order.
    seen, out = set(), []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    # An occasion query is ADDITIVE — allow one extra slot so it is never crowded
    # out by the base venue-tier + nudge queries.
    cap = max(1, limit) + (1 if occasion_terms else 0)
    return out[:cap]


# ---------------------------------------------------------------------------
# Low-level request (isolated so tests can inject a canned response)
# ---------------------------------------------------------------------------

def _places_search_text(query: str, api_key: str, limit: int) -> dict:
    """POST to Places Text Search. Returns parsed JSON. May raise (caller guards).

    The API key is placed ONLY in the X-Goog-Api-Key header — never in the URL,
    body, logs, or the returned data.
    """
    body = json.dumps({
        "textQuery": query,
        "maxResultCount": max(1, min(int(limit), 20)),
        "regionCode": "SG",
        "languageCode": "en",
    }).encode("utf-8")
    req = urllib.request.Request(
        _PLACES_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Verification + normalization
# ---------------------------------------------------------------------------

def _text_of(field) -> str:
    """displayName is {"text": "...", "languageCode": "en"}."""
    if isinstance(field, dict):
        return str(field.get("text") or "").strip()
    return str(field or "").strip()


def verify_place(place: dict, target_area: str = "singapore") -> tuple:
    """Return (is_verified, notes). Applies the full verification criteria."""
    if not isinstance(place, dict):
        return False, "not a place object"

    place_id = str(place.get("id") or "").strip()
    name = _text_of(place.get("displayName"))
    address = str(place.get("formattedAddress") or "").strip()
    maps_uri = str(place.get("googleMapsUri") or "").strip()

    if not place_id:
        return False, "missing place id"
    if not name:
        return False, "missing display name"
    if not address:
        return False, "missing formatted address"
    if not maps_uri:
        return False, "missing Google Maps URI"

    # In Singapore / target area.
    target = (target_area or "singapore").lower()
    if "singapore" not in address.lower() and target not in address.lower():
        return False, "address is not in Singapore / target area"

    # Operational if businessStatus is returned.
    status = str(place.get("businessStatus") or "").strip()
    if status and status.upper() != "OPERATIONAL":
        return False, f"businessStatus is {status}, not OPERATIONAL"

    # Must look like a dining venue, and must not be a mall/region/etc.
    types = {str(t).lower() for t in (place.get("types") or [])}
    primary = str(place.get("primaryType") or "").lower()
    if types & _NON_VENUE_TYPES and not (types & _FOOD_TYPES) and primary not in _FOOD_TYPES:
        return False, "type indicates a mall/region, not a dining venue"
    is_food = bool(types & _FOOD_TYPES) or primary in _FOOD_TYPES
    if not is_food:
        return False, "types/primaryType do not indicate a restaurant/food/cafe"

    return True, "Google Places verified: place id, name, address, and map URI confirmed."


def _infer_dietary(query: str, name: str) -> dict:
    """Best-effort dietary flags from the search intent + name (never over-claims
    beyond what was explicitly searched for)."""
    text = f"{query} {name}".lower()
    return {
        "halal": "halal" in text,
        "vegetarian": any(w in text for w in ("vegetarian", "vegan", "plant-based")),
    }


def normalize_verified_place(place: dict, query: str = "") -> dict | None:
    """Turn a verified place into a candidate that ALSO carries the convener venue
    shape (so it can flow into the planner pool). Returns None if not verified.

    Price truth (anti-hallucination) — resolved from INDEPENDENT venue signals, in
    order, NEVER from the group's budget:
      (a) Google priceRange (real $ band)  -> CONFIRMED
      (b) Google priceLevel enum           -> CONFIRMED (mapped estimate)
      (c) neither                          -> APPROXIMATE, estimated from the venue
                                              TYPE implied by the matched query.
    So a $100 private-dining place reads ~$100 and gets filtered on a $15 budget,
    instead of being relabelled to the budget number.
    """
    ok, notes = verify_place(place)
    if not ok:
        return None

    name = _text_of(place.get("displayName"))
    place_id = str(place.get("id") or "").strip()
    address = str(place.get("formattedAddress") or "").strip()
    maps_uri = str(place.get("googleMapsUri") or "").strip()
    website = str(place.get("websiteUri") or "").strip() or None
    price_level = str(place.get("priceLevel") or "").strip()
    rating = place.get("rating")
    urc = place.get("userRatingCount")
    diet = _infer_dietary(query, name)
    range_price = _price_from_range(place)
    if range_price is not None:
        per_head = range_price
        price_confidence = "confirmed"
    elif price_level in _PRICE_LEVEL_PER_HEAD:
        per_head = _PRICE_LEVEL_PER_HEAD[price_level]
        price_confidence = "confirmed"
    else:
        per_head = _venue_type_estimate(query)  # independent of budget
        price_confidence = "approximate"

    cand = blank_candidate(
        name=name,
        address=address,
        area=address.split(",")[0] if address else None,
        cuisine=None,
        price_hint=_PRICE_LEVEL_HINT.get(price_level),
        price_confidence=price_confidence,
        tags=[t for t, on in (("halal", diet["halal"]), ("vegetarian", diet["vegetarian"])) if on],
        source_kind="google_places",
        source_url=maps_uri,
        source_title=name,
        source_domain=source_domain(website) if website else "maps.google.com",
        google_place_id=place_id,
        google_maps_uri=maps_uri,
        website_uri=website,
        business_status=str(place.get("businessStatus") or "OPERATIONAL"),
        rating=rating if isinstance(rating, (int, float)) else None,
        user_rating_count=urc if isinstance(urc, int) else None,
        verification_status="verified",
        verification_notes=notes,
        confidence_score=0.95,
    )

    # Convener venue-shape keys (mirrors data/venues.json) + a stable id.
    venue_id = "gp_" + "".join(ch for ch in place_id.lower() if ch.isalnum())[:32]
    cand["_venue_id"] = venue_id
    cand.update({
        "id": venue_id,
        "halal": diet["halal"],
        "vegetarian": diet["vegetarian"],
        "price_per_head": per_head,
        "capacity": 6,
        "opens": "11:00",
        "closes": "22:00",
        "near_arcade": False,
        "lat": None,
        "lng": None,
    })
    return cand


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def text_search(query: str, limit: int = 8) -> list:
    """Verified venues for one query. Never raises. [] when disabled/unavailable.

    The per-head estimate for a price-less venue is derived from THIS query's venue
    vocabulary (venue type), independent of the group budget.
    """
    if not is_enabled():
        return []
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key or not query:
        return []
    try:
        data = _places_search_text(query, api_key, limit)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TypeError, OSError):
        return []
    except Exception:
        return []

    places = data.get("places") if isinstance(data, dict) else None
    if not isinstance(places, list):
        return []

    out = []
    for place in places:
        cand = normalize_verified_place(place, query=query)
        if cand is not None:
            out.append(cand)
    return out


def search_verified_venues(personas=None, queries=None, limit: int = 8) -> list:
    """Discover Google-verified venues for the group. Never raises. [] when off.

    Returns venue dicts that carry BOTH the convener venue shape and the grounded
    candidate fields (verification_status == "verified").
    """
    if not is_enabled():
        return []
    query_list = queries if queries else build_queries(personas, limit=4)

    seen_ids: set = set()
    verified: list = []
    for q in query_list:
        for cand in text_search(q, limit=limit):
            pid = cand.get("google_place_id")
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            verified.append(cand)
    return verified


def presence_report() -> dict:
    """Yes/no presence only — never the key value itself."""
    return {
        "USE_GOOGLE_PLACES": os.environ.get("USE_GOOGLE_PLACES", "unset"),
        "GOOGLE_PLACES_API_KEY": "yes" if os.environ.get("GOOGLE_PLACES_API_KEY", "").strip() else "no",
        "enabled": is_enabled(),
    }
