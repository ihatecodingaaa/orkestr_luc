"""Smoke for quorum planning (P2.2).

Asserts a live room can DRAFT a plan once enough friends reply, without waiting
for everyone, and that this never regresses the pure-read / compute-gate rules:
  * 0/5 and 2/5 replied  -> POST /draft refused (409), no plan, GETs stay 409.
  * 3/5 replied          -> draft allowed ONLY via the explicit POST; result has
                            plan_basis="quorum_draft" and draft_started_with = the
                            3 repliers; the 2 non-repliers stay pending and are
                            absent from the plan.
  * pure-read GET plan/negotiation/handshake NEVER compute at any quorum level.
  * 5/5 replied          -> the existing all-ready path still works
                            (plan_basis="all_ready").
  * ORK-001 reports all_ready / pending_members=[] and stays pristine.

Runs in-process via TestClient (no server, no network, no payment).
Exit 0 = all pass, 1 = any failure.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

import agents.discovery
import agents.google_places

agents.discovery.exa_venue_supplements = lambda *a, **k: []
agents.google_places.search_verified_venues = lambda *a, **k: []

# Count x402 receipt-daemon spawns so we can assert a re-draft is payment-neutral
# (the P0 one-shot guard must hold across every recompute).
import core.session as _S

_spawns = {"n": 0}
_S._maybe_fire_x402_receipt = lambda s, sid: _spawns.__setitem__("n", _spawns["n"] + 1)

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" â€” {detail}" if detail else ""))
        _failures.append(label)


# Stage C auth: first constraints submit mints the member token; ready/draft
# require it (or the host token). Tokens captured here like a real client would.
TOKENS = {}  # (sid, member_id) -> member token
HOSTS = {}   # sid -> host token


def reply(sid, m):
    r = client.post("/api/constraints", json={
        "session_id": sid, "id": m["id"], "name": m["name"],
        "constraints": {"available_days": ["FRI"], "budget_max": 60, "dietary": []},
        "freeform": "",
    }).json()
    if r.get("member_token"):
        TOKENS[(sid, m["id"])] = r["member_token"]
    client.post(f"/api/ready/{sid}", json={"person_id": m["id"]},
                headers={"X-Orkestr-Token": TOKENS.get((sid, m["id"]), "")})


def draft(sid):
    return client.post(f"/api/session/{sid}/draft",
                       headers={"X-Orkestr-Token": HOSTS.get(sid, "")})


def gets_are_not_ready(sid):
    return all(client.get(f"/api/{ep}/{sid}").status_code == 409
               for ep in ("plan", "negotiation", "handshake"))


print("Quorum planning smoke (P2.2)")
print("=" * 60)

live = client.post("/api/session/live", json={}).json()
sid = live["session_id"]
members = live["members"]
HOSTS[sid] = live.get("host_token") or ""

# --- 0/5 replied -------------------------------------------------------------
st = client.get(f"/api/status/{sid}").json()
check("0/5: quorum_min == 3", st.get("quorum_min") == 3, str(st.get("quorum_min")))
check("0/5: quorum_ready False", st.get("quorum_ready") is False, str(st.get("quorum_ready")))
check("0/5: draft -> 409 (cannot compute)", draft(sid).status_code == 409)
check("0/5: pure-read GETs stay 409 (no compute)", gets_are_not_ready(sid))

# --- 2/5 replied (below quorum) ---------------------------------------------
reply(sid, members[0])
reply(sid, members[1])
st = client.get(f"/api/status/{sid}").json()
check("2/5: replied_count == 2", st.get("replied_count") == 2, str(st.get("replied_count")))
check("2/5: quorum_ready False", st.get("quorum_ready") is False, str(st.get("quorum_ready")))
check("2/5: draft -> 409 (below quorum)", draft(sid).status_code == 409)
check("2/5: pure-read GETs stay 409 (no compute)", gets_are_not_ready(sid))
check("2/5: no plan computed", client.get(f"/api/session/{sid}/public").json().get("plan") is None)

# --- 3/5 replied (quorum reached) -------------------------------------------
reply(sid, members[2])
replied3 = sorted([members[0]["id"], members[1]["id"], members[2]["id"]])
pending2 = sorted([members[3]["id"], members[4]["id"]])
st = client.get(f"/api/status/{sid}").json()
check("3/5: quorum_ready True", st.get("quorum_ready") is True, str(st.get("quorum_ready")))
check("3/5: all_ready still False", st.get("all_ready") is False, str(st.get("all_ready")))
check("3/5: pending_members == the 2 non-repliers", sorted(st.get("pending_members") or []) == pending2, str(st.get("pending_members")))
# status must NOT have computed just by reporting quorum readiness
check("3/5: pure-read GETs STILL 409 before explicit draft", gets_are_not_ready(sid))
check("3/5: no plan before the explicit draft", client.get(f"/api/session/{sid}/public").json().get("plan") is None)

# explicit host trigger
draft_resp = draft(sid)
db = draft_resp.json()
check("3/5: draft POST -> 200", draft_resp.status_code == 200, str(draft_resp.status_code))
check("3/5: plan_basis == quorum_draft", db.get("plan_basis") == "quorum_draft", str(db.get("plan_basis")))
check("3/5: draft_started_with == the 3 repliers", sorted(db.get("draft_started_with") or []) == replied3, str(db.get("draft_started_with")))
plan = client.get(f"/api/plan/{sid}")
check("3/5: plan now readable (200)", plan.status_code == 200, str(plan.status_code))
check("3/5: plan satisfies ONLY the 3 repliers (pending excluded)",
      sorted(plan.json().get("satisfies") or []) == replied3, str(plan.json().get("satisfies")))
pub = client.get(f"/api/session/{sid}/public").json()
check("3/5: public plan_basis quorum_draft", pub.get("plan_basis") == "quorum_draft", str(pub.get("plan_basis")))
check("3/5: pending members absent from draft_started_with",
      not (set(pending2) & set(pub.get("draft_started_with") or [])), str(pub.get("draft_started_with")))
check("3/5: pending members still pending after draft",
      sorted(pub.get("pending_members") or []) == pending2, str(pub.get("pending_members")))

# --- RE-DRAFT cycle (P2 follow-up) ------------------------------------------
import time as _t

_t.sleep(0.2)
spawns_after_first_draft = _spawns["n"]
check("re-draft: exactly one x402 spawn after the first draft", spawns_after_first_draft == 1,
      f"spawns={spawns_after_first_draft}")

# 4th friend replies AFTER the draft -> the draft is now stale.
reply(sid, members[3])
st = client.get(f"/api/status/{sid}").json()
check("stale: replied_count == 4", st.get("replied_count") == 4, str(st.get("replied_count")))
check("stale: draft_started_with still 3 (not auto-updated)",
      len(st.get("draft_started_with") or []) == 3, str(st.get("draft_started_with")))
check("stale: draft_stale == True", st.get("draft_stale") is True, str(st.get("draft_stale")))
check("stale: new_replies_since_draft == 1", st.get("new_replies_since_draft") == 1,
      str(st.get("new_replies_since_draft")))
# pure reads still don't compute anything new
check("stale: plan still readable, unchanged satisfies (3)",
      sorted(client.get(f"/api/plan/{sid}").json().get("satisfies") or []) == replied3)

# Re-draft folds in the 4th.
rd = draft(sid).json()
replied4 = sorted([members[i]["id"] for i in range(4)])
check("re-draft: draft_started_with == 4", sorted(rd.get("draft_started_with") or []) == replied4,
      str(rd.get("draft_started_with")))
check("re-draft: still quorum_draft", rd.get("plan_basis") == "quorum_draft", str(rd.get("plan_basis")))
check("re-draft: pending == 1", len(rd.get("pending_members") or []) == 1, str(rd.get("pending_members")))
check("re-draft: draft no longer stale", rd.get("draft_stale") is False, str(rd.get("draft_stale")))
check("re-draft: plan now satisfies the 4",
      sorted(client.get(f"/api/plan/{sid}").json().get("satisfies") or []) == replied4)
# payment-neutral: no new receipt daemon spawned on recompute
_t.sleep(0.2)
check("re-draft: NO duplicate x402 spawn (still 1)", _spawns["n"] == spawns_after_first_draft,
      f"spawns={_spawns['n']}")
# mandate integrity still holds -> not spuriously failed
check("re-draft: phase stays plan_ready (mandate verify passed)",
      client.get(f"/api/status/{sid}").json().get("phase") == "plan_ready",
      str(client.get(f"/api/status/{sid}").json().get("phase")))
check("re-draft: no x402 receipt fired (public)",
      client.get(f"/api/session/{sid}/public").json().get("x402_receipt") is None)

# 5th replies -> re-draft promotes to all_ready.
reply(sid, members[4])
rd2 = draft(sid).json()
check("re-draft full: plan_basis == all_ready", rd2.get("plan_basis") == "all_ready", str(rd2.get("plan_basis")))
check("re-draft full: draft_started_with == 5", len(rd2.get("draft_started_with") or []) == 5, str(rd2.get("draft_started_with")))
check("re-draft full: pending empty", (rd2.get("pending_members") or []) == [], str(rd2.get("pending_members")))
check("re-draft full: not stale", rd2.get("draft_stale") is False, str(rd2.get("draft_stale")))

# No new repliers -> re-draft is a no-op (no recompute, still one spawn).
_t.sleep(0.2)
before_spawns = _spawns["n"]
plan_before = client.get(f"/api/plan/{sid}").json()
rd3 = draft(sid).json()
_t.sleep(0.2)
check("re-draft no-op: plan_basis unchanged (all_ready)", rd3.get("plan_basis") == "all_ready", str(rd3.get("plan_basis")))
check("re-draft no-op: no extra x402 spawn", _spawns["n"] == before_spawns, f"spawns={_spawns['n']}")
check("re-draft no-op: plan unchanged",
      client.get(f"/api/plan/{sid}").json().get("booking_ref") == plan_before.get("booking_ref"))

# --- 5/5 replied: the all-ready path still works -----------------------------
live2 = client.post("/api/session/live", json={}).json()
sid2 = live2["session_id"]
for m in live2["members"]:
    reply(sid2, m)
st2 = client.get(f"/api/status/{sid2}").json()  # all-ready -> lazy compute
check("5/5: all_ready True", st2.get("all_ready") is True, str(st2.get("all_ready")))
check("5/5: plan_basis == all_ready", st2.get("plan_basis") == "all_ready", str(st2.get("plan_basis")))
check("5/5: pending_members empty", (st2.get("pending_members") or []) == [], str(st2.get("pending_members")))

# --- ORK-001 pristine + all_ready -------------------------------------------
ork = client.get("/api/status/ORK-001").json()
check("ORK-001: all_ready True", ork.get("all_ready") is True, str(ork.get("all_ready")))
check("ORK-001: quorum_ready True", ork.get("quorum_ready") is True, str(ork.get("quorum_ready")))
check("ORK-001: pending_members empty", (ork.get("pending_members") or []) == [], str(ork.get("pending_members")))
check("ORK-001: plan_basis all_ready", ork.get("plan_basis") == "all_ready", str(ork.get("plan_basis")))
orkplan = client.get("/api/plan/ORK-001").json()
check("ORK-001: per_person still 80", orkplan.get("per_person") == 80, str(orkplan.get("per_person")))
check("ORK-001: venue Seoul Garden", (orkplan.get("venue") or {}).get("name") == "Seoul Garden",
      str((orkplan.get("venue") or {}).get("name")))
check("ORK-001: draft POST refused (not a live session)",
      client.post("/api/session/ORK-001/draft").status_code == 400,
      str(client.post("/api/session/ORK-001/draft").status_code))

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
