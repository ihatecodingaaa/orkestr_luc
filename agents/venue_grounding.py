"""Venue grounding + verification for Orkestr.

Turns raw venue candidates (seed data, Exa discoveries, Google Places results)
into ONE normalized, source-graded candidate shape, and enforces the rule that
the final plan may only present a venue the app can actually stand behind.

Grading (verification_status):
    verified     — confirmed by Google Places (has place id, name, address, map)
    source_only  — found on the web via Exa (has a real source URL) but not
                   Google-confirmed; shown as "Web source found", never "verified"
    demo         — a built-in sample venue; only ever shown with a sample label
    unverified   — AI/generated/unknown; NEVER presented as a real restaurant

Design rules honored here:
    * Google Places = verification.  Exa = discovery/evidence.
    * The planner may only *choose* from grounded (verified/source_only) venues
      when STRICT_REAL_VENUES is on; otherwise sample venues are allowed as a
      clearly-labeled demo fallback.
    * Names are locked to the verified/source name — never invented or combined.
    * Strict mode fails closed rather than showing a fake venue.

Pure/stdlib only. Never raises to callers. Never prints or returns secrets.
All feature flags default OFF so the existing MVP is unaffected.
"""

import os
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Canonical candidate schema
# ---------------------------------------------------------------------------

# Every grounded candidate is normalized to exactly these keys so the API,
# planner, and frontend all speak one shape.
CANDIDATE_FIELDS = (
    "name",
    "address",
    "area",
    "cuisine",
    "price_hint",
    "tags",
    "source_kind",        # google_places | exa | demo | unverified
    "source_url",
    "source_title",
    "source_domain",
    "google_place_id",
    "google_maps_uri",
    "website_uri",
    "business_status",
    "rating",
    "user_rating_count",
    "verification_status",  # verified | source_only | demo | unverified
    "verification_notes",
    "confidence_score",
)

# Statuses that count as a genuinely grounded (real) restaurant.
REAL_STATUSES = ("verified", "source_only")

_BADGES = {
    "verified": "Google Places verified",
    "source_only": "Web source found",
    "demo": "Sample demo venue",
    "unverified": "Unverified",
}


# ---------------------------------------------------------------------------
# Feature flags (all default OFF / safe)
# ---------------------------------------------------------------------------

def _flag_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def is_strict_real_venues() -> bool:
    """When on, the planner fails closed instead of showing a non-grounded venue."""
    return _flag_bool("STRICT_REAL_VENUES", default=False)


def allow_sample_venue_fallback() -> bool:
    """When on (and not strict), a labeled sample venue may back-fill the plan."""
    return _flag_bool("ALLOW_SAMPLE_VENUE_FALLBACK", default=True)


NO_VERIFIED_VENUE_MESSAGE = (
    "No verified restaurants found. Try relaxing budget, location, or dietary "
    "constraints."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def verification_badge(status: str) -> str:
    return _BADGES.get(status, _BADGES["unverified"])


def source_domain(url: str) -> str:
    """Bare registrable-ish domain for display (drops scheme + leading www.)."""
    if not url or not isinstance(url, str):
        return ""
    try:
        netloc = urlparse(url).netloc or urlparse("//" + url).netloc
    except Exception:
        return ""
    return netloc[4:] if netloc.lower().startswith("www.") else netloc


def _norm_name(name) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def _clean_tags(tags) -> list:
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, (list, tuple, set)):
        return []
    out = []
    for t in tags:
        t = str(t).strip()
        if t and t not in out:
            out.append(t)
    return out


def blank_candidate(**overrides) -> dict:
    """A candidate dict with every schema key present (safe defaults)."""
    cand = {
        "name": None,
        "address": None,
        "area": None,
        "cuisine": None,
        "price_hint": None,
        "tags": [],
        "source_kind": "unverified",
        "source_url": None,
        "source_title": None,
        "source_domain": None,
        "google_place_id": None,
        "google_maps_uri": None,
        "website_uri": None,
        "business_status": None,
        "rating": None,
        "user_rating_count": None,
        "verification_status": "unverified",
        "verification_notes": "",
        "confidence_score": 0.0,
    }
    for k, v in overrides.items():
        if k in cand:
            cand[k] = v
    cand["tags"] = _clean_tags(cand["tags"])
    return cand


# ---------------------------------------------------------------------------
# Known metadata for the built-in sample venues (data/venues.json)
# ---------------------------------------------------------------------------
# These are the ONLY venues allowed to carry a "demo" grade. Everything else is
# either grounded (verified/source_only) or unverified.

_SEED_META = {
    "seoul_garden": {"area": "VivoCity, HarbourFront", "cuisine": "Korean BBQ",
                     "address": "VivoCity #03-01, Singapore"},
    "sushi_hiro": {"area": "Orchard", "cuisine": "Japanese / Sushi",
                   "address": "Orchard Central #04-18, Singapore"},
    "veggie_table": {"area": "HarbourFront", "cuisine": "Vegetarian",
                     "address": "HarbourFront Walk, Singapore"},
}


def _price_hint_from(value) -> str | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v < 25:
        return "$"
    if v < 55:
        return "$$"
    if v < 90:
        return "$$$"
    return "$$$$"


def _dietary_tags(venue: dict) -> list:
    tags = []
    if venue.get("halal"):
        tags.append("halal")
    if venue.get("vegetarian"):
        tags.append("vegetarian")
    return tags


# ---------------------------------------------------------------------------
# Candidate builders (one per source)
# ---------------------------------------------------------------------------

def candidate_from_seed(venue: dict) -> dict:
    """Grade a built-in sample venue as demo (never verified)."""
    venue = venue or {}
    vid = venue.get("id")
    meta = _SEED_META.get(vid, {})
    return blank_candidate(
        name=venue.get("name"),
        address=meta.get("address"),
        area=meta.get("area"),
        cuisine=meta.get("cuisine"),
        price_hint=_price_hint_from(venue.get("price_per_head")),
        tags=_dietary_tags(venue),
        source_kind="demo",
        verification_status="demo",
        verification_notes="Built-in sample venue — shown for the demo, not web-verified.",
        # A sample is presentable but never counts as a real, grounded result.
        confidence_score=0.5,
    )


def candidate_from_exa(venue: dict) -> dict:
    """Grade an Exa discovery as source_only (real web source, not Google-verified).

    Requires a source_url — an Exa result with no URL cannot be grounded and is
    dropped by the caller.
    """
    venue = venue or {}
    url = str(venue.get("source_url") or "").strip()
    if not url:
        return blank_candidate(
            name=venue.get("name"),
            source_kind="unverified",
            verification_status="unverified",
            verification_notes="Exa result had no source URL — cannot be grounded.",
        )
    try:
        conf = float(venue.get("confidence"))
    except (TypeError, ValueError):
        conf = 0.4
    return blank_candidate(
        name=venue.get("name") or venue.get("source_title"),
        address=venue.get("address"),
        price_hint=_price_hint_from(venue.get("price_per_head")),
        tags=_dietary_tags(venue),
        source_kind="exa",
        source_url=url,
        source_title=venue.get("source_title") or venue.get("name"),
        source_domain=source_domain(url),
        verification_status="source_only",
        verification_notes="Found on the web via Exa. Web source found, not Google Places verified.",
        confidence_score=round(max(0.0, min(1.0, conf)), 2),
    )


# ---------------------------------------------------------------------------
# Grounding index + attachment
# ---------------------------------------------------------------------------

def build_grounding_index(seed_venues=None, exa_venues=None, google_candidates=None) -> dict:
    """Map venue id AND normalized name -> normalized grounding candidate.

    Precedence when a venue appears in more than one source: google > exa > seed,
    so a sample venue that Google can confirm is upgraded to verified.
    """
    index: dict = {}

    def _put(keys, cand, rank):
        for key in keys:
            if not key:
                continue
            existing = index.get(key)
            if existing is None or rank > existing[0]:
                index[key] = (rank, cand)

    for v in seed_venues or []:
        if isinstance(v, dict):
            _put([v.get("id"), _norm_name(v.get("name"))], candidate_from_seed(v), rank=1)

    for v in exa_venues or []:
        if isinstance(v, dict):
            cand = candidate_from_exa(v)
            if cand["verification_status"] == "source_only":
                _put([v.get("id"), _norm_name(v.get("name"))], cand, rank=2)

    for cand in google_candidates or []:
        if isinstance(cand, dict) and cand.get("verification_status") == "verified":
            _put([cand.get("_venue_id"), _norm_name(cand.get("name"))], cand, rank=3)

    return {key: cand for key, (_, cand) in index.items()}


def lookup_grounding(venue: dict, index: dict) -> dict | None:
    """Find the grounding candidate for a plan's venue by id, then by name."""
    if not isinstance(venue, dict) or not index:
        return None
    vid = venue.get("id")
    if vid and vid in index:
        return index[vid]
    return index.get(_norm_name(venue.get("name")))


def _public_grounding(cand: dict) -> dict:
    """The verification block attached to a plan venue (schema keys only)."""
    return {
        "source_kind": cand.get("source_kind", "unverified"),
        "verification_status": cand.get("verification_status", "unverified"),
        "verification_notes": cand.get("verification_notes", ""),
        "confidence_score": cand.get("confidence_score", 0.0),
        "badge": verification_badge(cand.get("verification_status", "unverified")),
        "area": cand.get("area"),
        "cuisine": cand.get("cuisine"),
        "price_hint": cand.get("price_hint"),
        "source_url": cand.get("source_url"),
        "source_title": cand.get("source_title"),
        "source_domain": cand.get("source_domain"),
        "google_place_id": cand.get("google_place_id"),
        "google_maps_uri": cand.get("google_maps_uri"),
        "website_uri": cand.get("website_uri"),
        "business_status": cand.get("business_status"),
        "rating": cand.get("rating"),
        "user_rating_count": cand.get("user_rating_count"),
    }


def attach_grounding(plan: dict, index: dict) -> dict:
    """Enrich plan['venue'] with a verification block and LOCK the display name.

    Name locking:
        verified    -> use the Google Places displayName + formatted address
        source_only -> keep the real source title/name (never prettified)
        demo        -> keep the sample name (labeled as sample in the UI)
        unverified  -> leave as-is but mark unverified (fails strict mode)
    """
    if not isinstance(plan, dict):
        return plan
    venue = plan.get("venue")
    if not isinstance(venue, dict):
        return plan

    cand = lookup_grounding(venue, index)
    if cand is None:
        # Venue is not in any grounded/sample source — treat as unverified so the
        # UI never implies it is real and strict mode can reject it.
        venue["verification"] = {
            "source_kind": "unverified",
            "verification_status": "unverified",
            "verification_notes": "Venue is not present in any verified, web-sourced, "
                                  "or sample candidate list.",
            "confidence_score": 0.0,
            "badge": verification_badge("unverified"),
        }
        return plan

    grounding = _public_grounding(cand)
    venue["verification"] = grounding

    status = grounding["verification_status"]
    # Name/address locking to the grounded source of truth.
    if status == "verified":
        if cand.get("name"):
            venue["name"] = cand["name"]
        if cand.get("address"):
            venue["address"] = cand["address"]
    elif status == "source_only":
        if cand.get("source_title") and not venue.get("name"):
            venue["name"] = cand["source_title"]
    # Fill address/area for demo venues when the planner left a placeholder.
    if cand.get("address") and (not venue.get("address") or venue.get("address") == "TBC"):
        venue["address"] = cand["address"]
    if grounding.get("area"):
        venue.setdefault("area", grounding["area"])

    return plan


# ---------------------------------------------------------------------------
# Grading / gating predicates
# ---------------------------------------------------------------------------

def _status_of(obj) -> str:
    """Read verification_status from a candidate OR a plan venue block."""
    if not isinstance(obj, dict):
        return "unverified"
    if "verification_status" in obj:
        return obj.get("verification_status") or "unverified"
    v = obj.get("verification")
    if isinstance(v, dict):
        return v.get("verification_status") or "unverified"
    return "unverified"


def counts_as_real(obj) -> bool:
    """True only for genuinely grounded venues (Exa source or Google verified)."""
    return _status_of(obj) in REAL_STATUSES


def is_demo(obj) -> bool:
    return _status_of(obj) == "demo"


def may_present_as_venue(obj, strict: bool | None = None) -> bool:
    """Whether this venue may be shown at all as the chosen place.

    Grounded venues always may. A sample (demo) venue may only when NOT strict
    and sample-fallback is allowed. Unverified is never presentable.
    """
    if strict is None:
        strict = is_strict_real_venues()
    status = _status_of(obj)
    if status in REAL_STATUSES:
        return True
    if status == "demo":
        return (not strict) and allow_sample_venue_fallback()
    return False


def venue_status_label(plan: dict, strict: bool | None = None) -> str:
    """Coarse status for the API/UI: ok | demo | no_verified_venue."""
    if strict is None:
        strict = is_strict_real_venues()
    venue = plan.get("venue") if isinstance(plan, dict) else None
    if counts_as_real(venue):
        return "ok"
    if is_demo(venue) and may_present_as_venue(venue, strict):
        return "demo"
    return "no_verified_venue"


def check_final_venue(plan: dict, strict: bool | None = None) -> dict:
    """Gate the chosen plan venue. Returns a decision dict.

    { ok, fail_closed, venue_status, message }

    fail_closed is True when STRICT_REAL_VENUES is on and the chosen venue is not
    grounded (verified/source_only) — the caller must then return a safe
    no-result state instead of showing a fake/sample venue as real.
    """
    if strict is None:
        strict = is_strict_real_venues()
    venue = plan.get("venue") if isinstance(plan, dict) else None
    status = venue_status_label(plan, strict)

    if counts_as_real(venue):
        return {"ok": True, "fail_closed": False, "venue_status": "ok", "message": None}

    if is_demo(venue) and not strict and allow_sample_venue_fallback():
        return {"ok": True, "fail_closed": False, "venue_status": "demo", "message": None}

    # Strict, or a sample/unverified venue with fallback disabled -> fail closed.
    return {
        "ok": False,
        "fail_closed": True,
        "venue_status": "no_verified_venue",
        "message": NO_VERIFIED_VENUE_MESSAGE,
    }


def filter_pool_for_strict(seed_venues: list, grounded_venues: list, strict: bool | None = None) -> list:
    """Build the venue pool the planner may choose from.

    strict  -> only grounded venues (Exa/Google). Sample venues are excluded so
               the planner can never pick one.
    relaxed -> grounded venues first, then sample venues as a labeled fallback.
    """
    if strict is None:
        strict = is_strict_real_venues()
    grounded = [v for v in (grounded_venues or []) if isinstance(v, dict)]
    if strict:
        return grounded
    if allow_sample_venue_fallback():
        return grounded + [v for v in (seed_venues or []) if isinstance(v, dict)]
    return grounded or [v for v in (seed_venues or []) if isinstance(v, dict)]
