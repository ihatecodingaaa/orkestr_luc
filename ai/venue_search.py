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
import os
import urllib.error
import urllib.request

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
        "_rating": place.get("rating") or 0,  # internal sort key ONLY
        "_photo_name": photo_name,            # internal photo ref ONLY
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
    body = json.dumps({"textQuery": text_query, "maxResultCount": 5}).encode()
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
        return _fallback_venues(), None

    mapped = [_map_place(p) for p in places if isinstance(p, dict)]
    if not mapped:
        return _fallback_venues(), None
    # Sort by rating desc, capture the top venue's photo, then strip the internal
    # _rating/_photo_name so only the frozen 5 keys remain in each returned dict.
    mapped.sort(key=lambda v: v.get("_rating") or 0, reverse=True)
    photo_name = mapped[0].get("_photo_name")
    for v in mapped:
        v.pop("_rating", None)
        v.pop("_photo_name", None)
    return mapped, photo_name
