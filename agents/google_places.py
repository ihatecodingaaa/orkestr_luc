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
# a freshly-discovered venue (Places does not return a per-head price).
_PRICE_LEVEL_PER_HEAD = {
    "PRICE_LEVEL_FREE": 15,
    "PRICE_LEVEL_INEXPENSIVE": 20,
    "PRICE_LEVEL_MODERATE": 40,
    "PRICE_LEVEL_EXPENSIVE": 70,
    "PRICE_LEVEL_VERY_EXPENSIVE": 110,
}
_DEFAULT_PER_HEAD = 45


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
    """Tailor a few text-search queries from the group's real constraints."""
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

    diet_part = " ".join(sorted(dietary)) if dietary else ""
    queries = []
    if areas:
        for area in sorted(areas):
            queries.append(f"{diet_part} restaurant near {area} Singapore group dinner".strip())
    queries.append(f"{diet_part} restaurant Singapore group seating dinner".strip())
    queries.append(f"{diet_part} restaurant Singapore".strip())
    # Top up with defaults so we always have a few varied queries.
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
    return out[:max(1, limit)]


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
    shape (so it can flow into the planner pool). Returns None if not verified."""
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
    per_head = _PRICE_LEVEL_PER_HEAD.get(price_level, _DEFAULT_PER_HEAD)

    cand = blank_candidate(
        name=name,
        address=address,
        area=address.split(",")[0] if address else None,
        cuisine=None,
        price_hint=_PRICE_LEVEL_HINT.get(price_level),
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
    """Verified venues for one query. Never raises. [] when disabled/unavailable."""
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
