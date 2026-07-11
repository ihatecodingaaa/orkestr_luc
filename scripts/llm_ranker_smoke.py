"""Smoke for grounded LLM occasion ranking (Stage C). OFFLINE — the OpenAI call
(venue_ranker._llm_complete) is monkeypatched; the real OpenAI API is NEVER called.

Proves the anti-hallucination law:
  * a valid in-set chosen id -> that REAL candidate is chosen (venue facts from the
    candidate record, not the model);
  * an OFF-LIST id or malformed JSON -> rejected, deterministic fallback, no
    fabricated venue;
  * occasion keywords shift the deterministic ordering;
  * flag/key OFF -> deterministic path, the model seam is never called;
  * end to end (Places HTTP + LLM both mocked) the LLM-chosen REAL venue wins and
    grounded why-reasons + occasion ride on the plan.

Exit 0 = all pass, 1 = any failure.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

from ai import venue_ranker as R

_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def cands():
    return [
        {"id": "gp_a", "google_place_id": "PLACE_A", "name": "Hawker Alpha",
         "tags": ["halal", "food_court"], "price_per_head": 15, "price_confidence": "approximate", "rating": 4.1},
        {"id": "gp_b", "google_place_id": "PLACE_B", "name": "Beta Group Dining",
         "tags": ["halal", "vegetarian", "group_seating"], "price_per_head": 18,
         "price_confidence": "approximate", "rating": 4.6},
        {"id": "gp_c", "google_place_id": "PLACE_C", "name": "Gamma Cafe",
         "tags": ["vegetarian"], "price_per_head": 20, "price_confidence": "confirmed", "rating": 3.9},
    ]


MEMBERS = [
    {"id": "P-1", "constraints": {"dietary": ["halal"], "budget_max": 20}, "freeform": ""},
    {"id": "P-2", "constraints": {"dietary": ["vegetarian"], "budget_max": 25}, "freeform": ""},
]

_orig_complete = R._llm_complete
_calls = {"n": 0}


def set_llm(return_value):
    def _fake(system, user):
        _calls["n"] += 1
        return return_value
    R._llm_complete = _fake


print("LLM ranker smoke (Stage C) — OpenAI mocked, never called for real")
print("=" * 64)

# 1. Occasion detection (deterministic, pure).
occ = R.detect_occasion("It's Carol's birthday, we need group seating for everyone")
check("detect_occasion finds celebration + group", "celebration" in occ["labels"] and "group" in occ["labels"], str(occ))
check("detect_occasion yields real-Places query terms", bool(occ["query_terms"]), str(occ["query_terms"]))
check("detect_occasion empty on plain note", R.detect_occasion("just dinner")["labels"] == [])

# 2. Gating.
os.environ.pop("USE_LLM_RANKER", None)
os.environ.pop("OPENAI_API_KEY", None)
check("ranker disabled when flag/key absent", R.is_llm_ranker_enabled() is False)
os.environ["USE_LLM_RANKER"] = "true"
os.environ["OPENAI_API_KEY"] = "sk-dummy-mock-not-used"
check("ranker enabled when flag + key present", R.is_llm_ranker_enabled() is True)

FORBIDDEN_UNGROUNDED = ["karaoke", "rooftop", "live music", "michelin", "waterfront", "pool", "buffet"]


def all_grounded(reasons, cand, members, notes):
    """Every displayed reason must be one the fact-assembler would produce for THIS
    real candidate (i.e. maps to a real field), and contain no ungrounded amenity."""
    allowed = set(R.reasons_for(cand, members, notes))
    for r in reasons:
        if r not in allowed:
            return False, f"reason not fact-assembled: {r!r}"
        if any(bad in r.lower() for bad in FORBIDDEN_UNGROUNDED):
            return False, f"ungrounded word in: {r!r}"
    return True, ""


# 3. Valid in-set choice -> REAL candidate chosen; reasons FACT-ASSEMBLED even when
#    the model tries to inject an ungrounded reason ("private karaoke room").
_calls["n"] = 0
set_llm(json.dumps({
    "chosen_place_id": "gp_b", "order": ["gp_b", "gp_a", "gp_c"],
    "emphasize": ["occasion", "dietary"],
    # The model tries to author prose with an amenity the venue does NOT have:
    "reasons": ["Beta has a private karaoke room and a rooftop with live music"],
}))
res = R.rank_venues(cands(), MEMBERS, "birthday, group seating")
_b = next(c for c in cands() if c["id"] == "gp_b")
check("valid choice -> source=llm", res["source"] == "llm", res["source"])
check("valid choice -> chosen is the in-set id", res["chosen_id"] == "gp_b", str(res["chosen_id"]))
check("valid choice -> chosen candidate is first, populated from record",
      res["candidates"][0]["id"] == "gp_b" and res["candidates"][0]["name"] == "Beta Group Dining")
check("valid choice -> grounded reasons surfaced", len(res["reasons"]) >= 1)
_ok, _why = all_grounded(res["reasons"], _b, MEMBERS, "birthday, group seating")
check("every displayed reason maps to a real candidate field", _ok, _why)
check("model's ungrounded 'karaoke/rooftop/live music' NEVER displayed",
      not any(bad in " ".join(res["reasons"]).lower() for bad in ("karaoke", "rooftop", "live music")),
      str(res["reasons"]))
check("model seam called exactly once", _calls["n"] == 1, str(_calls["n"]))

# 4. OFF-LIST id -> rejected, deterministic fallback, NO fabricated venue.
set_llm(json.dumps({"chosen_place_id": "PLACE_DOES_NOT_EXIST", "order": ["x"], "reasons": ["nope"]}))
res = R.rank_venues(cands(), MEMBERS, "birthday")
check("off-list id -> source=deterministic (rejected)", res["source"] == "deterministic", res["source"])
_ids = {c["id"] for c in res["candidates"]}
check("off-list id -> no fabricated venue (all real inputs)", _ids == {"gp_a", "gp_b", "gp_c"}, str(_ids))
check("off-list id -> chosen is a REAL input id", res["chosen_id"] in _ids, str(res["chosen_id"]))

# 5. Malformed JSON -> rejected, deterministic.
set_llm("this is not json {")
res = R.rank_venues(cands(), MEMBERS, "birthday")
check("malformed JSON -> deterministic fallback", res["source"] == "deterministic", res["source"])

# 6. Occasion shifts deterministic ordering (group-seating venue rises).
os.environ.pop("USE_LLM_RANKER", None)  # force deterministic
plain = R.rank_venues(cands(), MEMBERS, "just a normal dinner")
party = R.rank_venues(cands(), MEMBERS, "birthday party, need group seating")
check("occasion ranking puts the group-seating venue on top",
      party["candidates"][0]["id"] == "gp_b",
      f"party_top={party['candidates'][0]['id']} plain_top={plain['candidates'][0]['id']}")

# 7. Flag/key OFF -> deterministic, model seam NEVER called.
_calls["n"] = 0
os.environ.pop("USE_LLM_RANKER", None)
set_llm(json.dumps({"chosen_place_id": "gp_a"}))
res = R.rank_venues(cands(), MEMBERS, "birthday")
check("disabled -> deterministic and model NOT called", res["source"] == "deterministic" and _calls["n"] == 0,
      f"source={res['source']} calls={_calls['n']}")

# 7b. Deterministic (flag/key off) shows the SAME grounded, fact-assembled reasons.
_chosen_det = R._find_by_id(res["candidates"], res["chosen_id"])
check("deterministic reasons == fact-assembled reasons_for (grounded floor)",
      res["reasons"] == R.reasons_for(_chosen_det, MEMBERS, "birthday"),
      f"{res['reasons']} vs {R.reasons_for(_chosen_det, MEMBERS, 'birthday')}")
_okd, _whyd = all_grounded(res["reasons"], _chosen_det, MEMBERS, "birthday")
check("deterministic displayed reasons all grounded", _okd, _whyd)

# 8. Anti-hallucination invariant: chosen is ALWAYS one of the real inputs.
for note in ("", "birthday", "anniversary private room", "walkable quiet"):
    r = R.rank_venues(cands(), MEMBERS, note)
    check(f"chosen always in real input set (note={note!r})",
          r["chosen_id"] in {"gp_a", "gp_b", "gp_c"}, str(r["chosen_id"]))

R._llm_complete = _orig_complete

# 9. End to end: Places HTTP + LLM both mocked -> the LLM-chosen REAL venue wins,
#    with grounded why_reasons + occasion on the plan, venue populated from Places.
os.environ["USE_GOOGLE_PLACES"] = "true"
os.environ["GOOGLE_PLACES_API_KEY"] = "dummy-mock-key"
os.environ["STRICT_REAL_VENUES"] = "true"
os.environ["ALLOW_SAMPLE_VENUE_FALLBACK"] = "false"
os.environ["USE_EXA"] = "false"
os.environ["USE_LLM_RANKER"] = "true"
os.environ["OPENAI_API_KEY"] = "sk-dummy-mock"

import agents.google_places as GP  # noqa: E402
from core import session as S  # noqa: E402

_B = {"id": "PLACE_B", "displayName": {"text": "Beta Group Dining"},
      "formattedAddress": "5 Beta Rd, Singapore 100200",
      "googleMapsUri": "https://maps.google.com/?cid=b", "businessStatus": "OPERATIONAL",
      "types": ["restaurant", "food"], "primaryType": "restaurant", "rating": 4.6, "userRatingCount": 900}
_A = {"id": "PLACE_A", "displayName": {"text": "Hawker Alpha"},
      "formattedAddress": "1 Alpha St, Singapore 111222",
      "googleMapsUri": "https://maps.google.com/?cid=a", "businessStatus": "OPERATIONAL",
      "types": ["food_court", "food"], "primaryType": "food_court", "rating": 4.1, "userRatingCount": 5000}


def _fake_places(query, api_key, limit):
    return {"places": [_A, _B]}


GP._places_search_text = _fake_places


def _pick_B(system, user):
    # The real Google place ids appear as candidate ids "gp_" + normalized place id.
    payload = json.loads(user)
    ids = [c["id"] for c in payload["candidates"]]
    beta = next((i for i in ids if "placeb" in i.lower()), ids[0])
    return json.dumps({"chosen_place_id": beta, "order": [beta],
                       "reasons": ["Group seating suits the birthday", "Halal-friendly", "Highly rated"]})


R._llm_complete = _pick_B

_s = S.new_live_session()
_sid = _s["session_id"]
for _m in _s["members"]:
    S.add_constraints(_sid, {"id": _m["id"], "name": _m.get("name", _m["id"]),
        "constraints": {"available_days": ["FRI"], "budget_max": 25, "dietary": ["halal"]},
        "freeform": "It's a birthday — please get group seating for everyone"})
S.compute_plan(_sid)
_s = S.get(_sid)
_plan = _s.get("plan") or {}
_venue = _plan.get("venue") or {}

check("e2e resolves to a real verified venue", (_venue.get("verification") or {}).get("verification_status") == "verified",
      str((_venue.get("verification") or {}).get("verification_status")))
check("e2e LLM-chosen venue (Beta Group Dining) won", _venue.get("name") == "Beta Group Dining",
      str(_venue.get("name")))
check("e2e venue populated from the Places record (real address)",
      "Singapore" in str(_venue.get("address")), str(_venue.get("address")))
check("e2e grounded why_reasons on the plan", isinstance(_plan.get("why_reasons"), list) and len(_plan["why_reasons"]) >= 1,
      str(_plan.get("why_reasons")))
def _template_shaped(r):
    # Every fact-assembled reason is one of these fixed templates filled from a real
    # field — nothing free-authored can match.
    return (r.startswith("Covers ") or r.startswith("About $") or r.startswith("Rated ")
            or r.startswith("Has ") or r.endswith(" cuisine") or r.endswith("works for the whole group"))


check("e2e every displayed reason is a fact-assembled template (maps to a real field)",
      all(_template_shaped(r) for r in (_plan.get("why_reasons") or [])), str(_plan.get("why_reasons")))
check("e2e no ungrounded amenity words in reasons",
      not any(bad in " ".join(_plan.get("why_reasons") or []).lower() for bad in FORBIDDEN_UNGROUNDED),
      str(_plan.get("why_reasons")))
check("e2e occasion detected on the plan", "celebration" in (_plan.get("occasion") or []) or "group" in (_plan.get("occasion") or []),
      str(_plan.get("occasion")))
check("e2e ranker_source recorded", _plan.get("ranker_source") in ("llm", "deterministic"), str(_plan.get("ranker_source")))

R._llm_complete = _orig_complete

# 10. Occasion widens the REAL Places query set (agents/google_places.build_queries).
_qs = GP.build_queries([{"constraints": {"budget_max": 20, "dietary": ["halal"], "available_days": ["FRI"]},
                         "freeform": "birthday, group seating please"}])
check("occasion adds a group/seating query to the real search",
      any("group" in q.lower() or "seating" in q.lower() for q in _qs), str(_qs))

print("\n" + "=" * 64)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
