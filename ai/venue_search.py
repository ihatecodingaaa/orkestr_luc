"""Real venue search via the Google Places API (New). Owner: Lucas.

Powers the real-session plan path (a separate path that BYPASSES the seeded
convener). Maps Google Places Text Search results to Orkestr's FROZEN venue
shape — {name, address, lat, lng, tags} — and nothing else.

Falls back to data/venues.json (same 5-key shape) whenever the API key is absent
or the API errors, so the demo always yields a venue and never 500s. Stdlib only
(urllib.request); no new dependencies. GOOGLE_PLACES_API_KEY is passed in by the
caller (read from os.environ) — never hardcoded here.
"""
import json
import logging
import os
import urllib.error
import urllib.request

LOGGER = logging.getLogger(__name__)

DATA = os.path.join(os.path.dirname(__file__), "..", "data")

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.displayName,places.formattedAddress,places.location,"
    "places.types,places.priceLevel,places.rating,places.photos"
)
HTTP_TIMEOUT = 8

# Google Places (New) priceLevel enum -> our coarse budget tag. Integer forms
# (older shape) handled defensively too.
_PRICE_TAG = {
    "PRICE_LEVEL_INEXPENSIVE": "budget",
    "PRICE_LEVEL_MODERATE": "mid",
    "PRICE_LEVEL_EXPENSIVE": "upscale",
    "PRICE_LEVEL_VERY_EXPENSIVE": "upscale",
    1: "budget", 2: "mid", 3: "upscale", 4: "upscale",
}

# Rough per-head SGD estimate for each Google price level — used ONLY to filter
# out venues above the group's budget after the API returns. Integer forms of the
# enum (older shape) handled defensively too. Kept consistent with the request
# thresholds in _price_levels_for_budget (INEXPENSIVE<=20, MODERATE<=40, ...).
_PRICE_LEVEL_ESTIMATE_SGD = {
    "PRICE_LEVEL_INEXPENSIVE": 15,
    "PRICE_LEVEL_MODERATE": 35,
    "PRICE_LEVEL_EXPENSIVE": 60,
    "PRICE_LEVEL_VERY_EXPENSIVE": 100,
    1: 15, 2: 35, 3: 60, 4: 100,
}


def _price_levels_for_budget(budget_max):
    """Google Places priceLevels to REQUEST for a per-head SGD budget.

    Returns None (no filter -> any price level) when there is no usable budget or
    the budget is high (> 70 SGD/head).
    """
    if not isinstance(budget_max, (int, float)) or isinstance(budget_max, bool):
        return None
    if budget_max <= 20:
        return ["PRICE_LEVEL_INEXPENSIVE"]
    if budget_max <= 40:
        return ["PRICE_LEVEL_INEXPENSIVE", "PRICE_LEVEL_MODERATE"]
    if budget_max <= 70:
        return ["PRICE_LEVEL_INEXPENSIVE", "PRICE_LEVEL_MODERATE", "PRICE_LEVEL_EXPENSIVE"]
    return None  # > 70 SGD/head -> any price level


def _estimate_per_head(price_level):
    """Estimated per-head SGD for a Google price level, or None if unknown."""
    return _PRICE_LEVEL_ESTIMATE_SGD.get(price_level)


def _filter_in_budget(venues, budget_max):
    """Keep venues whose estimated per-head price is within budget_max.

    A venue with an UNKNOWN price level (estimate None) is kept — we can't prove it
    is over budget, and Google returning it under a priceLevels filter is a signal
    it fits. With no usable budget, every venue is 'in budget'.
    """
    if not isinstance(budget_max, (int, float)) or isinstance(budget_max, bool):
        return list(venues)
    out = []
    for v in venues:
        est = _estimate_per_head(v.get("_price_level"))
        if est is None or est <= budget_max:
            out.append(v)
    return out


def build_text_query(location, cuisine):
    """The exact textQuery string sent to Places: '<cuisine> restaurant in <loc>'."""
    return f"{cuisine or 'restaurant'} restaurant in {location or 'Singapore'}"


def _load_seed_venues():
    with open(os.path.join(DATA, "venues.json")) as f:
        return json.load(f)


def _seed_tags(v):
    tags = []
    if v.get("halal"):
        tags.append("halal")
    if v.get("vegetarian"):
        tags.append("vegetarian")
    return tags


def _fallback_venues():
    """data/venues.json mapped to the FROZEN 5-key shape (source has no address)."""
    return [{
        "name": v.get("name"),
        "address": "TBC",
        "lat": v.get("lat"),
        "lng": v.get("lng"),
        "tags": _seed_tags(v),
    } for v in _load_seed_venues()]


def _clean_name(text):
    """First segment of a Google Places displayName — drop '| …' and ', …' tails
    (e.g. 'Saute san | City Square Mall | …' -> 'Saute san'). Preserves None."""
    if not text:
        return text
    first = str(text).split("|")[0].split(",")[0].strip()
    return first or str(text).strip()


def _map_place(place):
    """One Places result -> frozen dict plus internal _rating/_photo_name keys.

    _rating (sort key) and _photo_name (first photo resource name) are internal
    ONLY and are stripped before search_venues returns, so the returned dicts
    keep exactly the frozen 5 keys.
    """
    loc = place.get("location") or {}
    types = place.get("types") or []
    tags = list(types[:3])  # first 3 of types
    price_tag = _PRICE_TAG.get(place.get("priceLevel"))
    if price_tag:
        tags.append(price_tag)
    photos = place.get("photos") or []
    photo_name = photos[0].get("name") if photos and isinstance(photos[0], dict) else None
    return {
        "name": _clean_name((place.get("displayName") or {}).get("text")),
        "address": place.get("formattedAddress"),
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "tags": tags,
        "_rating": place.get("rating") or 0,      # internal sort key ONLY
        "_photo_name": photo_name,                # internal photo ref ONLY
        "_price_level": place.get("priceLevel"),  # internal budget filter ONLY
    }


def search_venues(location, cuisine_prefs, budget_max, dietary, group_size, api_key):
    """Return (venues, photo_name): a list of frozen-5-key venue dicts plus the
    top venue's Places photo resource name (or None).

    Real Google Places (New) Text Search when api_key is present; otherwise, or on
    ANY error/empty result, falls back to data/venues.json with photo_name=None.
    Results are sorted by Google rating DESCending; rating and photo name are
    internal only and are stripped so each returned dict contains exactly the
    frozen 5 keys {name, address, lat, lng, tags}.

    budget_max/dietary/group_size are accepted for query context and future
    filtering; they never alter the returned shape.
    """
    if not api_key:
        return _fallback_venues(), None

    text_query = build_text_query(location, cuisine_prefs)
    # Budget-aware: constrain the API to price levels the group can afford. None
    # (no usable budget, or > 70 SGD/head) leaves the search unfiltered.
    levels = _price_levels_for_budget(budget_max)
    body_dict = {"textQuery": text_query, "maxResultCount": 5}
    if levels:
        body_dict["priceLevels"] = levels
    body = json.dumps(body_dict).encode()
    req = urllib.request.Request(
        PLACES_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except Exception:
        # URLError, HTTPError (bad key -> 400/403), timeout, JSON error -> fallback.
        return _fallback_venues(), None

    places = data.get("places") if isinstance(data, dict) else None
    if not places:
        LOGGER.info("venue_search: no results for budget levels %s query '%s', using fallback",
                    levels, text_query)
        return _fallback_venues(), None

    mapped = [_map_place(p) for p in places if isinstance(p, dict)]
    if not mapped:
        return _fallback_venues(), None
    # Sort by rating desc, then keep only venues within budget. Fall back to the
    # over-budget (real) results ONLY when none fit — better a real venue than a
    # seed one. Capture the chosen top venue's photo, then strip the internal
    # _rating/_photo_name/_price_level so only the frozen 5 keys remain.
    mapped.sort(key=lambda v: v.get("_rating") or 0, reverse=True)
    in_budget = _filter_in_budget(mapped, budget_max)
    chosen = in_budget if in_budget else mapped
    if not in_budget:
        LOGGER.info("venue_search: no in-budget venues (<= %s SGD/head) for '%s', "
                    "using best-rated over-budget result", budget_max, text_query)
    photo_name = chosen[0].get("_photo_name")
    for v in chosen:
        v.pop("_rating", None)
        v.pop("_photo_name", None)
        v.pop("_price_level", None)
    return chosen, photo_name
