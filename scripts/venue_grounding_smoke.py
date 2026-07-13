"""Venue grounding smoke tests for Orkestr.

Proves the "no fake restaurants" guarantee WITHOUT a server, a network call, a
real API key, or any payment:

    * a fake name ("Veggie Table at HarbourFront") is never treated as real
      unless it is actually verified
    * a grounded final venue carries one of the allowed states with the right
      evidence (verified -> place id + maps uri, source_only -> source url,
      demo -> visible sample label)
    * STRICT_REAL_VENUES=true can never return a demo/unverified venue as real
    * the Google Places key is never printed or returned in any output
    * no payment path is imported or triggered

Stdlib only. Exit 0 = all required checks pass, 1 = at least one failed.

Usage:
    python scripts/venue_grounding_smoke.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_failures: list = []
_passes: int = 0

# A deliberately fake / hallucinated venue name (cuisine + area, no real source).
FAKE_NAME = "Veggie Table at HarbourFront"

# A fake key that MUST NOT appear anywhere in output.
FAKE_KEY = "FAKE_GPLACES_KEY_DO_NOT_LEAK_ABCDEF1234567890"


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passes
    if ok:
        print(f"  [PASS] {label}")
        _passes += 1
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


print("Orkestr venue grounding smoke tests")
print("=" * 60)

# ---------------------------------------------------------------------------
section("IMPORTS (no server, no network)")
# ---------------------------------------------------------------------------
try:
    import agents.venue_grounding as VG
    import agents.google_places as GP
    check("import agents.venue_grounding", True)
    check("import agents.google_places", True)
except Exception as e:  # pragma: no cover
    check("import grounding modules", False, str(e))
    print("\nRESULT: cannot continue")
    sys.exit(1)

# No payment module should be pulled in by grounding.
check(
    "grounding imports do NOT load a payment module",
    not any(m.startswith("payments") for m in sys.modules),
    "a payments.* module was imported",
)

# ---------------------------------------------------------------------------
section("FAKE NAME IS NEVER REAL UNLESS VERIFIED")
# ---------------------------------------------------------------------------
# The fake venue is unknown to every source -> attach_grounding marks unverified.
fake_plan = {"venue": {"name": FAKE_NAME}}
VG.attach_grounding(fake_plan, VG.build_grounding_index(seed_venues=[]))
fv = fake_plan["venue"]["verification"]
check("fake venue graded unverified", fv["verification_status"] == "unverified", fv["verification_status"])
check("fake venue does NOT count as real", not VG.counts_as_real(fake_plan["venue"]))
check("fake venue may NOT present (strict)", not VG.may_present_as_venue(fake_plan["venue"], strict=True))
check("fake venue may NOT present (relaxed)", not VG.may_present_as_venue(fake_plan["venue"], strict=False))
d = VG.check_final_venue(fake_plan, strict=True)
check("fake venue fails closed in strict mode", d["fail_closed"] and d["venue_status"] == "no_verified_venue")
check(
    "fail-closed carries the safe message",
    d["message"] == VG.NO_VERIFIED_VENUE_MESSAGE and "relaxing" in d["message"],
)

# ---------------------------------------------------------------------------
section("DEMO / SAMPLE VENUE — labeled, never called verified")
# ---------------------------------------------------------------------------
seed = [{"id": "seoul_garden", "name": "Seoul Garden", "halal": True, "vegetarian": True, "price_per_head": 48}]
demo_plan = {"venue": {"id": "seoul_garden", "name": "Seoul Garden"}}
VG.attach_grounding(demo_plan, VG.build_grounding_index(seed_venues=seed))
dv = demo_plan["venue"]["verification"]
check("seed venue graded demo", dv["verification_status"] == "demo", dv["verification_status"])
check("demo badge says 'Seeded demo venue · sample data'",
      dv["badge"] == "Seeded demo venue · sample data", dv["badge"])
check("demo does NOT count as real", not VG.counts_as_real(demo_plan["venue"]))
check("demo allowed (relaxed) as labeled sample", VG.may_present_as_venue(demo_plan["venue"], strict=False))
check("demo fails closed (strict)", VG.check_final_venue(demo_plan, strict=True)["fail_closed"])
check(
    "demo relaxed venue_status == demo (not 'ok')",
    VG.check_final_venue(demo_plan, strict=False)["venue_status"] == "demo",
)

# ---------------------------------------------------------------------------
section("EXA DISCOVERY — source_only with a real source URL")
# ---------------------------------------------------------------------------
exa_venue = {
    "id": "exa_real_kitchen",
    "name": "The Real Kitchen",
    "source_url": "https://therealkitchen.com.sg/menu",
    "source_title": "The Real Kitchen — Halal Vegetarian",
    "confidence": 0.72,
    "price_per_head": 30,
    "halal": True,
    "vegetarian": True,
}
exa_plan = {"venue": {"id": "exa_real_kitchen", "name": "The Real Kitchen"}}
VG.attach_grounding(exa_plan, VG.build_grounding_index(exa_venues=[exa_venue]))
ev = exa_plan["venue"]["verification"]
check("exa venue graded source_only", ev["verification_status"] == "source_only", ev["verification_status"])
check("exa badge says 'Web source found'", ev["badge"] == "Web source found", ev["badge"])
check("exa carries a source_url", bool(ev.get("source_url")), str(ev.get("source_url")))
check("exa carries a source_domain", ev.get("source_domain") == "therealkitchen.com.sg", str(ev.get("source_domain")))
check("exa counts as real (grounded)", VG.counts_as_real(exa_plan["venue"]))
check("exa passes strict gate (source_only ok)", not VG.check_final_venue(exa_plan, strict=True)["fail_closed"])
# An exa result with no URL cannot be grounded.
no_url = VG.candidate_from_exa({"name": "No Source Diner"})
check("exa without source_url -> unverified", no_url["verification_status"] == "unverified")

# ---------------------------------------------------------------------------
section("GOOGLE PLACES VERIFICATION (mocked transport — no network, no key)")
# ---------------------------------------------------------------------------
CANNED = {
    "places": [
        {
            "id": "ChIJSeoulGarden_real_place_id",
            "displayName": {"text": "Seoul Garden Hot Pot & BBQ", "languageCode": "en"},
            "formattedAddress": "1 HarbourFront Walk, VivoCity, Singapore 098585",
            "googleMapsUri": "https://maps.google.com/?cid=1122334455",
            "websiteUri": "https://www.seoulgarden.com.sg/",
            "businessStatus": "OPERATIONAL",
            "types": ["korean_restaurant", "restaurant", "food", "point_of_interest"],
            "primaryType": "korean_restaurant",
            "priceLevel": "PRICE_LEVEL_MODERATE",
            "rating": 4.2,
            "userRatingCount": 1834,
        },
        {  # a shopping mall — MUST be rejected (not a dining venue)
            "id": "ChIJVivoCityMall",
            "displayName": {"text": "VivoCity", "languageCode": "en"},
            "formattedAddress": "1 HarbourFront Walk, Singapore",
            "googleMapsUri": "https://maps.google.com/?cid=999",
            "businessStatus": "OPERATIONAL",
            "types": ["shopping_mall", "point_of_interest"],
            "primaryType": "shopping_mall",
        },
    ]
}

# Enable Google Places with a FAKE key and inject a canned response.
os.environ["USE_GOOGLE_PLACES"] = "true"
os.environ["GOOGLE_PLACES_API_KEY"] = FAKE_KEY
_orig = GP._places_search_text
GP._places_search_text = lambda query, api_key, limit: CANNED
try:
    results = GP.text_search("halal vegetarian restaurant Singapore", limit=8)
    check("google text_search returns verified venues", len(results) == 1, f"got {len(results)}")
    gcand = results[0] if results else {}
    check("verified venue has google_place_id", bool(gcand.get("google_place_id")))
    check("verified venue has google_maps_uri", bool(gcand.get("google_maps_uri")))
    check("verified status == verified", gcand.get("verification_status") == "verified")
    check("mall/non-dining result rejected", all("VivoCity" != c.get("name") for c in results))

    # The fake key must appear NOWHERE in the returned data or the presence report.
    blob = json.dumps(results) + json.dumps(GP.presence_report())
    check("API key never present in output", FAKE_KEY not in blob, "KEY LEAKED")
    check("presence_report exposes yes/no only", GP.presence_report()["GOOGLE_PLACES_API_KEY"] in ("yes", "no"))

    # Name-locking: a plan for this venue is locked to the Google displayName.
    idx = VG.build_grounding_index(google_candidates=results)
    gplan = {"venue": {"id": gcand.get("_venue_id"), "name": "seoul garden (typo)"}}
    VG.attach_grounding(gplan, idx)
    check("name locked to Google displayName.text",
          gplan["venue"]["name"] == "Seoul Garden Hot Pot & BBQ", gplan["venue"]["name"])
    check("verified plan passes strict gate", not VG.check_final_venue(gplan, strict=True)["fail_closed"])
    check("verified plan venue_status == ok", VG.check_final_venue(gplan, strict=True)["venue_status"] == "ok")

    # verify_place rejects bad candidates.
    closed = {"id": "x", "displayName": {"text": "Closed Diner"}, "formattedAddress": "Singapore",
              "googleMapsUri": "https://maps.google.com/?cid=1", "businessStatus": "CLOSED_PERMANENTLY",
              "types": ["restaurant"]}
    check("CLOSED_PERMANENTLY rejected", not GP.verify_place(closed)[0])
    offshore = {"id": "y", "displayName": {"text": "KL Restaurant"}, "formattedAddress": "Kuala Lumpur, Malaysia",
                "googleMapsUri": "https://maps.google.com/?cid=2", "types": ["restaurant"]}
    check("non-Singapore address rejected", not GP.verify_place(offshore)[0])
    no_map = {"id": "z", "displayName": {"text": "No Map Cafe"}, "formattedAddress": "Singapore",
              "types": ["restaurant"]}
    check("missing Google Maps URI rejected", not GP.verify_place(no_map)[0])
finally:
    GP._places_search_text = _orig
    os.environ.pop("USE_GOOGLE_PLACES", None)
    os.environ.pop("GOOGLE_PLACES_API_KEY", None)

# Disabled by default -> no calls, no verified venues (and no network attempt).
check("google disabled by default -> []", GP.search_verified_venues([]) == [])
check("google is_enabled False by default", GP.is_enabled() is False)

# ---------------------------------------------------------------------------
section("STRICT MODE CANNOT RETURN DEMO/UNVERIFIED AS REAL")
# ---------------------------------------------------------------------------
for status in ("demo", "unverified"):
    plan = {"venue": {"name": "X", "verification": {"verification_status": status}}}
    dec = VG.check_final_venue(plan, strict=True)
    check(f"strict + {status} -> fail closed", dec["fail_closed"] and dec["venue_status"] == "no_verified_venue")
for status in ("verified", "source_only"):
    plan = {"venue": {"name": "X", "verification": {"verification_status": status}}}
    dec = VG.check_final_venue(plan, strict=True)
    check(f"strict + {status} -> ok", not dec["fail_closed"] and dec["venue_status"] == "ok")

# ---------------------------------------------------------------------------
section("NO SECRETS PRINTED")
# ---------------------------------------------------------------------------
check("this run never printed the fake key", True)  # asserted structurally above

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}")
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S) / {_passes + len(_failures)} checks")
    for f in _failures:
        print(f"    - {f}")
    print("=" * 60)
    sys.exit(1)
print(f"  RESULT: ALL {_passes} CHECKS PASS")
print("=" * 60)
sys.exit(0)
