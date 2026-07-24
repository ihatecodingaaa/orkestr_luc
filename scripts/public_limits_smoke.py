"""Stage P1 smoke: public cost + abuse controls, honest degradation, lifecycle.

Covers the P1 acceptance tests:
  1. per-IP room-creation quota -> 429 at the cap;
  2. Places spend cap hit (strict room) -> `capacity_limited` fail_reason, NO search
     call made, and NEVER a "no verified restaurant" message;
  3. LLM spend cap hit -> deterministic ranker (model NOT called), plan still resolves;
  4. an aged room is scrubbed to a PII-free tombstone; its link returns an honest 410;
  5. ORK-001 survives cleanup (frozen demo, exempt);
  6. daily counters are SQLite-persisted (survive a simulated restart);
  7. the frontend renders capacity honestly (guard check).

Runs in-process via TestClient (no server, no network, no payment). We set low caps
and RE-ENABLE public limits BEFORE importing paidpath_guard (which disables them for
the rest of the suite). A random per-run client IP avoids cross-run counter bleed.
Exit 0 = all pass, 1 = any failure.
"""
import os
import random
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Exercise the REAL limits (paidpath_guard.setdefault won't override this) and pin
# low caps. These module-level caps in api.main are read at import, so set first.
os.environ["ORK_DISABLE_PUBLIC_LIMITS"] = "false"
os.environ["IP_ROOM_CREATE_DAILY_CAP"] = "3"
os.environ["IP_COMPUTE_DAILY_CAP"] = "2"
os.environ["PLACES_DAILY_CALL_CAP"] = "5"
os.environ["LLM_DAILY_CALL_CAP"] = "4"
os.environ["ROOM_TTL_DAYS"] = "14"

import paidpath_guard  # noqa: F401,E402  (force real-x402 spend paths off)

from datetime import datetime, timedelta, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from core import db, session as S, usage  # noqa: E402
from ai import venue_ranker  # noqa: E402

client = TestClient(app)
_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))
        _failures.append(label)


def _fresh_ip():
    # TEST-NET-1 (192.0.2.0/24) — a fresh random IP per run keeps the persisted
    # per-IP counter from bleeding across runs.
    return {"X-Forwarded-For": f"192.0.2.{random.randint(1, 254)}"}


print("Public limits smoke (Stage P1)")
print("=" * 60)

# --- 1. Per-IP room-creation quota -> 429 at the cap (cap=3). ----------------
ip = _fresh_ip()
codes = [client.post("/api/session/live", json={}, headers=ip).status_code for _ in range(4)]
check("first 3 creations from one IP succeed", codes[:3] == [200, 200, 200], str(codes[:3]))
check("4th creation from same IP -> 429", codes[3] == 429, str(codes[3]))
# A DIFFERENT IP is unaffected (per-IP, not global).
other = client.post("/api/session/live", json={}, headers=_fresh_ip())
check("a different IP is not rate-limited", other.status_code == 200, str(other.status_code))

# --- 2. Places cap -> honest capacity state, NO search call, no "no results". -
made = client.post("/api/session/live", json={}, headers=_fresh_ip()).json()
sid = made["session_id"]

# Tripwire the real search entry points: they must NOT be called when capped.
import agents.google_places as GP  # noqa: E402
import agents.discovery as DISC  # noqa: E402

called = {"places": False, "exa": False}
_orig_gp = GP.search_verified_venues
_orig_exa = getattr(DISC, "exa_venue_supplements", None)
GP.search_verified_venues = lambda *a, **k: called.__setitem__("places", True) or []
if _orig_exa is not None:
    DISC.exa_venue_supplements = lambda *a, **k: called.__setitem__("exa", True) or []

_orig_places_available = usage.places_available
os.environ["STRICT_REAL_VENUES"] = "true"  # this room REQUIRES a verified venue
usage.places_available = lambda: False      # simulate the day's Places cap hit
try:
    S.compute_plan(sid)
finally:
    usage.places_available = _orig_places_available
    GP.search_verified_venues = _orig_gp
    if _orig_exa is not None:
        DISC.exa_venue_supplements = _orig_exa
    os.environ["STRICT_REAL_VENUES"] = ""

capped = client.get(f"/api/status/{sid}").json()
check("Places-capped room fails as capacity (venue_status)",
      capped.get("venue_status") == "capacity_limited", str(capped.get("venue_status")))
fr = (capped.get("fail_reason") or "").lower()
check("capacity fail_reason mentions capacity", "capacity" in fr, capped.get("fail_reason"))
check("capacity fail_reason is NOT disguised as 'no verified restaurant'",
      "no verified" not in fr and "no restaurant" not in fr and "no restaurants" not in fr,
      capped.get("fail_reason"))
check("NO paid search call was made when capped",
      called["places"] is False and called["exa"] is False, str(called))

# --- 3. LLM cap -> deterministic ranker (model NOT called), plan resolves. ----
os.environ["USE_LLM_RANKER"] = "true"
os.environ["OPENAI_API_KEY"] = "sk-dummy-not-used"
llm_called = {"n": 0}
_orig_llm = venue_ranker._llm_complete
venue_ranker._llm_complete = lambda *a, **k: (llm_called.__setitem__("n", llm_called["n"] + 1), None)[1]
cands = [{"id": "g1", "name": "Halal BBQ House", "tags": ["halal"], "price_per_head": 22,
          "price_confidence": "confirmed", "rating": 4.3},
         {"id": "g2", "name": "Veggie Table", "tags": ["vegetarian"], "price_per_head": 18,
          "price_confidence": "confirmed", "rating": 4.5}]
members = [{"id": "P-001", "constraints": {"budget_max": 40, "dietary": ["halal"]}}]

_orig_llm_available = usage.llm_available
usage.llm_available = lambda: False  # day's LLM cap hit
try:
    res_capped = venue_ranker.rank_venues(cands, members, "team dinner")
finally:
    usage.llm_available = _orig_llm_available
check("LLM-capped ranking uses deterministic source",
      res_capped.get("source") == "deterministic", str(res_capped.get("source")))
check("LLM-capped ranking never called the model",
      llm_called["n"] == 0, f'model called {llm_called["n"]}x')
check("LLM-capped ranking still resolves candidates (plan can build)",
      len(res_capped.get("candidates") or []) == len(cands), str(len(res_capped.get("candidates") or [])))

# Control: with the cap AVAILABLE, the model IS consulted (proves the gate is the cap).
usage.llm_available = lambda: True
try:
    venue_ranker.rank_venues(cands, members, "team dinner")
finally:
    usage.llm_available = _orig_llm_available
    venue_ranker._llm_complete = _orig_llm
    os.environ["USE_LLM_RANKER"] = ""
    os.environ["OPENAI_API_KEY"] = ""
check("with LLM budget available, the model IS attempted (gate is the cap)",
      llm_called["n"] >= 1, f'model called {llm_called["n"]}x')

# --- 4/5. Room expiry: PII scrubbed to a tombstone; ORK-001 exempt. ----------
victim = client.post("/api/session/live", json={}, headers=_fresh_ip()).json()
vsid = victim["session_id"]
# Real PII lands on the room (a member name + freeform note).
client.post("/api/constraints", json={
    "session_id": vsid, "id": "P-001", "name": "Priya Realname",
    "constraints": {"budget_max": 40}, "freeform": "allergic to shellfish please"})
raw = db.get_session(vsid)
before_blob = str(raw)
check("room holds PII before expiry", "Priya Realname" in before_blob, "no PII seeded")
# Age it past the TTL and run cleanup.
raw["last_active_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
db.set_session(vsid, raw)
expired_n = S.cleanup_expired_rooms(ttl_days=14)
check("cleanup expired at least this room", expired_n >= 1, str(expired_n))
tomb = db.get_session(vsid)
check("expired room is a tombstone", isinstance(tomb, dict) and tomb.get("expired") is True, str(tomb))
check("expired room PII is gone (name absent)", "Priya Realname" not in str(tomb), str(tomb))
check("expired room note is gone", "shellfish" not in str(tomb), str(tomb))
r410 = client.get(f"/api/status/{vsid}")
check("expired room link returns honest 410", r410.status_code == 410, str(r410.status_code))
check("410 body says expired (honest page)", "expired" in (r410.json().get("detail") or "").lower(),
      str(r410.json()))
# ORK-001 is exempt and survives.
ork = db.get_session("ORK-001")
check("ORK-001 survives cleanup (not expired)",
      isinstance(ork, dict) and not ork.get("expired") and ork.get("plan"), "ORK-001 damaged")

# --- 6. Counters persist (SQLite-backed; no in-memory counter cache). ---------
key = f"api:test:{random.randint(10**6, 10**7)}"
day = usage.today()
db.incr_counter(day, key, 7)
check("counter persisted and re-read from SQLite", db.get_counter(day, key) == 7,
      str(db.get_counter(day, key)))
db.incr_counter(day, key, 3)
check("counter accumulates (survives a simulated restart via disk read)",
      db.get_counter(day, key) == 10, str(db.get_counter(day, key)))

# --- 7. Frontend renders capacity honestly (guard check). --------------------
root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
with open(os.path.join(root, "src/components/live/LiveFailureCard.tsx"), "r", encoding="utf-8") as fh:
    fail_card = fh.read()
check("LiveFailureCard has a capacity_limited branch",
      "capacity_limited" in fail_card and "at capacity right now" in fail_card,
      "no capacity branch")
# The capacity branch must sit ABOVE the venue/restaurant branch (its message says
# "venue-search", which the venue branch would otherwise capture).
check("capacity branch precedes the no-verified-venue branch",
      fail_card.index("capacity_limited") < fail_card.index('venueStatus === "no_verified_venue"'),
      "ordering wrong")

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
