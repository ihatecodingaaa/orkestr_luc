"""Grounded occasion-aware ranking of REAL venue candidates. Owner: Lucas.

ANTI-HALLUCINATION LAW (absolute):
  * Retrieval (Google Places / Exa) sources the venues. This module only RANKS the
    candidates it is given — it never sources, invents, or edits a venue.
  * The LLM may return ONLY a chosen id that already exists in the candidate set,
    a ranked order over those ids, and short reasoning strings. Its chosen id is
    validated against the input set; an off-list id or malformed JSON is REJECTED
    and the deterministic ranker is used instead (logged). The venue card is always
    populated from the Places record, never from model output.
  * The LLM path runs ONLY when USE_LLM_RANKER=true AND OPENAI_API_KEY is present.
    Any error/timeout falls back to the deterministic ranker — never a blank plan.

Stdlib only for the deterministic path; the OpenAI client is imported lazily inside
_llm_complete so the module (and the whole app) import cleanly without the SDK or a
key. OPENAI_API_KEY is read from the environment only — never logged or returned.
"""
import json
import logging
import os

LOGGER = logging.getLogger(__name__)

# Occasion signal -> (normalized label, extra real-Places query terms). Detected in
# the group's free-text "anything else" notes and used BOTH to bias ranking and to
# widen the real search so occasion-appropriate venues are actually in the pool.
_OCCASION_SIGNALS = (
    (("birthday", "bday", "anniversary", "celebrat", "graduation", "farewell"),
     "celebration", "group seating celebration"),
    (("group", "large", "party", "squad", "everyone", "table for"),
     "group", "large group dining"),
    (("private", "quiet", "intimate"),
     "private", "private dining room"),
    (("walkable", "walk", "near mrt", "close by", "nearby"),
     "walkable", ""),
)


def detect_occasion(text) -> dict:
    """Detect occasion labels + real-Places query terms from free-text notes.

    Pure/deterministic. Returns {"labels": [...], "query_terms": [...]}. Empty when
    nothing is detected.
    """
    blob = str(text or "").lower()
    labels, terms = [], []
    for keys, label, query in _OCCASION_SIGNALS:
        if any(k in blob for k in keys):
            if label not in labels:
                labels.append(label)
            if query and query not in terms:
                terms.append(query)
    return {"labels": labels, "query_terms": terms}


def is_llm_ranker_enabled() -> bool:
    """LLM ranking runs ONLY when explicitly enabled AND a key is configured."""
    flag = os.environ.get("USE_LLM_RANKER", "").strip().lower() in ("true", "1", "yes")
    return flag and bool(os.environ.get("OPENAI_API_KEY", "").strip())


# ---------------------------------------------------------------------------
# Candidate helpers
# ---------------------------------------------------------------------------

def _cand_ids(c) -> set:
    """Every id a candidate may be referenced by (plan id + Google place id)."""
    if not isinstance(c, dict):
        return set()
    return {str(v) for v in (c.get("id"), c.get("google_place_id"), c.get("_venue_id")) if v}


def _find_by_id(candidates, chosen) -> dict | None:
    chosen = str(chosen or "")
    if not chosen:
        return None
    for c in candidates:
        if chosen in _cand_ids(c):
            return c
    return None


def _required_dietary(members) -> set:
    out = set()
    for m in members or []:
        for d in ((m.get("constraints") or {}).get("dietary") or []):
            d = str(d).lower()
            if d in ("halal", "vegetarian", "vegan"):
                out.add("vegetarian" if d == "vegan" else d)
    return out


def _group_budget_min(members):
    budgets = []
    for m in members or []:
        b = (m.get("constraints") or {}).get("budget_max")
        if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0:
            budgets.append(float(b))
    return min(budgets) if budgets else None


def _occasion_match(c, occasion_terms) -> int:
    """Ranking signal only (NOT a displayed claim): does this candidate look
    occasion-appropriate? Loose name/tag match is fine for ordering preference."""
    if not occasion_terms:
        return 0
    text = f"{c.get('name', '')} {' '.join(c.get('tags') or [])}".lower()
    keys = ("group", "seating", "private", "celebrat", "party", "birthday", "function")
    return 1 if any(k in text for k in keys) else 0


def _group_day(members):
    """The best common available day across the group (real constraint input), or
    None. Used only for an honest 'FRI works for everyone' fact reason."""
    common = None
    for m in members or []:
        d = {str(x).upper() for x in ((m.get("constraints") or {}).get("available_days") or [])}
        if not d:
            continue
        common = d if common is None else (common & d)
    if not common:
        return None
    for pref in ("FRI", "SAT", "SUN"):
        if pref in common:
            return pref
    return sorted(common)[0]


# Structured occasion-amenity tags — the ONLY basis for a displayed occasion
# amenity claim. A venue's name or a ranking heuristic is NOT enough; the amenity
# must be a real structured field on the candidate record.
_OCCASION_AMENITY_TAGS = {
    "group seating": ("group_seating", "group_dining", "large_group"),
    "a private dining area": ("private_dining", "private_room", "private_dining_room"),
}


def _deterministic_order(candidates, members, occasion) -> list:
    """Deterministic ranking: budget fit -> dietary coverage -> occasion -> rating.

    Stable and pure — the guaranteed grounded fallback. Never drops a candidate; it
    only re-orders REAL candidates.
    """
    gmin = _group_budget_min(members)
    req = _required_dietary(members)
    occ_terms = (occasion or {}).get("labels") or []

    def keyfn(c):
        pph = c.get("price_per_head")
        in_budget = 1 if (gmin is None or (isinstance(pph, (int, float)) and pph <= gmin)) else 0
        tags = {str(t).lower() for t in (c.get("tags") or [])}
        diet_cov = len(req & tags)
        occ = _occasion_match(c, occ_terms)
        rating = c.get("rating") if isinstance(c.get("rating"), (int, float)) else 0
        return (-in_budget, -diet_cov, -occ, -rating)

    return sorted([c for c in candidates if isinstance(c, dict)], key=keyfn)


def _fact_reasons(c, members, occasion) -> list:
    """(key, text) reason tuples assembled STRICTLY from the candidate's real fields
    (cuisine, price + price_confidence, dietary tags, rating) plus the group's own
    inputs (occasion labels from their notes, common available day). Every sentence
    is a fixed template filled from a real value — NO free text, and NO amenity is
    asserted unless it is literally a structured field on the candidate. This is why
    a model can never inject an ungrounded claim ("rooftop", "karaoke room").
    """
    out = []
    tags = {str(t).lower() for t in (c.get("tags") or [])}
    labels = (occasion or {}).get("labels") or []

    cuisine = c.get("cuisine")
    if cuisine:
        out.append(("cuisine", f"{cuisine} cuisine"))

    req = _required_dietary(members)
    base = req if req else {"halal", "vegetarian"}
    covered = sorted(base & tags)
    if covered:
        out.append(("dietary", "Covers " + ", ".join(covered) + " diners"))

    pph = c.get("price_per_head")
    if isinstance(pph, (int, float)) and not isinstance(pph, bool):
        approx = c.get("price_confidence") == "approximate"
        out.append(("price", f"About ${int(pph)}/head" + (" (price approximate)" if approx else "")))

    # Occasion AMENITY claim only when a structured amenity tag is really present.
    if labels:
        for amenity, keys in _OCCASION_AMENITY_TAGS.items():
            if tags & set(keys):
                out.append(("occasion", f"Has {amenity}"))
                break

    r = c.get("rating")
    if isinstance(r, (int, float)) and not isinstance(r, bool) and r > 0:
        out.append(("rating", f"Rated {r} on Google"))

    day = _group_day(members)
    if day:
        out.append(("day", f"{day} works for the whole group"))

    return out


# ---------------------------------------------------------------------------
# LLM path (isolated so tests monkeypatch _llm_complete; never called in the
# default offline suite)
# ---------------------------------------------------------------------------

def _llm_complete(system_prompt: str, user_prompt: str) -> str | None:
    """Call OpenAI for a strict-JSON completion. Returns the raw text or None on any
    error. The key is read from the environment only and never logged/returned."""
    try:
        from openai import OpenAI  # lazy: SDK optional
    except Exception:
        return None
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    try:
        client = OpenAI(api_key=key, timeout=8.0)
        resp = client.chat.completions.create(
            model=os.environ.get("LLM_RANKER_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=400,
        )
        return resp.choices[0].message.content
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("venue_ranker: OpenAI call failed (%s) — deterministic fallback", type(e).__name__)
        return None


def _candidate_digest(candidates) -> list:
    """The ONLY venue facts sent to the model — all from the real Places record."""
    out = []
    for c in candidates:
        out.append({
            "id": str(c.get("id") or c.get("google_place_id") or ""),
            "name": c.get("name"),
            "tags": [str(t) for t in (c.get("tags") or [])][:6],
            "price_per_head": c.get("price_per_head"),
            "price_confidence": c.get("price_confidence"),
            "rating": c.get("rating"),
        })
    return out


def _llm_choose(candidates, members, occasion, notes) -> dict | None:
    """Ask the model to pick + rank among the REAL candidates. Returns
    {chosen_id, order, reasons} or None if disabled/failed/off-list (rejected)."""
    ids = set()
    for c in candidates:
        ids |= _cand_ids(c)
    if not ids:
        return None
    digest = _candidate_digest(candidates)
    # The model may ONLY choose + order + pick which real facts to emphasize. It must
    # NOT write sentences or describe the venue — the displayed reasons are assembled
    # from the candidate record, so the model can never inject an ungrounded claim.
    system = (
        "You rank restaurant candidates for a group. Choose ONLY from the provided "
        "candidate ids and pick which of the REAL fact keys to emphasize. Never "
        "invent a venue, id, fact, or sentence. Return STRICT JSON: "
        '{"chosen_place_id": "<one id from the list>", "order": ["<ids...>"], '
        '"emphasize": ["<=3 keys from: cuisine, dietary, price, occasion, rating, day"]}.'
    )
    user = json.dumps({
        "constraints": {
            "dietary": sorted(_required_dietary(members)),
            "budget_min": _group_budget_min(members),
        },
        "occasion": (occasion or {}).get("labels") or [],
        "notes": str(notes or "")[:500],
        "fact_keys": ["cuisine", "dietary", "price", "occasion", "rating", "day"],
        "candidates": digest,
    })
    raw = _llm_complete(system, user)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        LOGGER.warning("venue_ranker: LLM returned non-JSON — rejected, deterministic fallback")
        return None
    chosen = str(data.get("chosen_place_id") or data.get("chosen_id") or "")
    # ANTI-HALLUCINATION GATE: the chosen id MUST exist in the candidate set.
    if chosen not in ids:
        LOGGER.warning("venue_ranker: LLM chose off-list id %r — rejected, deterministic fallback",
                       chosen[:40])
        return None
    order = [str(x) for x in (data.get("order") or []) if str(x) in ids]
    valid_keys = {"cuisine", "dietary", "price", "occasion", "rating", "day"}
    emphasize = [str(k).lower() for k in (data.get("emphasize") or []) if str(k).lower() in valid_keys]
    # Any model-authored prose (e.g. a stray "reasons" field) is IGNORED by design.
    return {"chosen_id": chosen, "order": order, "emphasize": emphasize}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _assemble_reasons(candidate, members, occasion, emphasize=None) -> list:
    """Fact-assembled reason strings for ONE real candidate, optionally reordered by
    a list of emphasize keys (the ONLY thing a model may influence). Max 3."""
    facts = _fact_reasons(candidate, members, occasion)
    if emphasize:
        keys = [k for k in emphasize]
        facts.sort(key=lambda kv: keys.index(kv[0]) if kv[0] in keys else len(keys))
    return [text for _key, text in facts][:3]


def reasons_for(candidate, members, notes="") -> list:
    """Grounded, fact-assembled 'why this place won' bullets for ONE real candidate.
    Used when negotiation selects a real venue other than the ranker's top pick, so
    the reasons always describe the venue actually shown. Never invents facts, never
    uses model prose — every string is a template filled from a real candidate field
    (or the group's own occasion/day input)."""
    if not isinstance(candidate, dict):
        return []
    return _assemble_reasons(candidate, members, detect_occasion(notes))


def rank_venues(candidates, members, notes="") -> dict:
    """Rank REAL candidates by dietary/budget/occasion fit. Returns:

        {"candidates": [...reordered real candidates, chosen first...],
         "chosen_id": str|None, "reasons": [...], "occasion": [...], "source": ...}

    source is "llm" only when the model returned a VALID in-set choice; otherwise
    "deterministic". The returned candidate list is always the same real objects,
    only re-ordered — nothing is invented or dropped.
    """
    reals = [c for c in (candidates or []) if isinstance(c, dict)]
    occasion = detect_occasion(notes)
    if not reals:
        return {"candidates": [], "chosen_id": None, "reasons": [],
                "occasion": occasion["labels"], "source": "deterministic"}

    det = _deterministic_order(reals, members, occasion)

    if is_llm_ranker_enabled():
        choice = _llm_choose(reals, members, occasion, notes)
        if choice:
            chosen = _find_by_id(reals, choice["chosen_id"])
            if chosen is not None:
                # Reorder: model order first (validated ids), then any remainder in
                # deterministic order. Chosen is guaranteed first.
                ordered = []
                for oid in [choice["chosen_id"]] + choice["order"]:
                    c = _find_by_id(reals, oid)
                    if c is not None and c not in ordered:
                        ordered.append(c)
                for c in det:
                    if c not in ordered:
                        ordered.append(c)
                # Reasons are FACT-ASSEMBLED from the chosen real venue; the model's
                # emphasize only reorders them. No model-authored sentence is shown.
                reasons = _assemble_reasons(chosen, members, occasion, choice.get("emphasize"))
                return {"candidates": ordered, "chosen_id": choice["chosen_id"],
                        "reasons": reasons, "occasion": occasion["labels"], "source": "llm"}

    top = det[0] if det else None
    return {"candidates": det,
            "chosen_id": (str(top.get("id") or top.get("google_place_id") or "") if top else None),
            "reasons": _assemble_reasons(top, members, occasion) if top else [],
            "occasion": occasion["labels"], "source": "deterministic"}
