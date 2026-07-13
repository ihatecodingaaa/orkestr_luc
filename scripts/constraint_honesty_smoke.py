"""Offline smoke: the constraint layer is honest — no stub pretending to score.

Asserts, against the ONE real engine (agents/persona.stance -> convener
run_negotiation/_annotate_plan):
  * the lying modules are GONE (core/constraints.py `return True` stub,
    ai/narrate.py's "locked in" copy) and nothing imports them;
  * no live constraint function is a constant-success stub (repo grep);
  * an unmet dietary need / blown budget / unavailable day each make that member
    UNSATISFIED — the happy count really drops below full;
  * an UNKNOWN dietary need fails closed (supported only via an explicit tag);
  * all-constraints-met -> satisfied; the count equals the real satisfied set;
  * ORK-001's 5/5 comes from real evaluation of the frozen plan, not a stamp.

Stdlib + in-process only. No server, no network, no payment.
Exit 0 = all pass, 1 = any failure.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def section(title):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


print("Constraint honesty smoke")
print("=" * 60)

# ---------------------------------------------------------------------------
section("1. THE LYING MODULES ARE GONE (and nothing imports them)")
# ---------------------------------------------------------------------------
check("core/constraints.py deleted",
      not os.path.exists(os.path.join(REPO, "core", "constraints.py")))
check("ai/narrate.py deleted",
      not os.path.exists(os.path.join(REPO, "ai", "narrate.py")))

importers = []
stub_hits = []
for d in ("api", "core", "agents", "ai", "payments", "scripts", "tests"):
    root_dir = os.path.join(REPO, d)
    if not os.path.isdir(root_dir):
        continue
    for root, _dirs, files in os.walk(root_dir):
        if "__pycache__" in root:
            continue
        for fn in files:
            if not fn.endswith(".py") or fn == "constraint_honesty_smoke.py":
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8", errors="replace") as f:
                src = f.read()
            rel = os.path.relpath(path, REPO)
            if re.search(r"(from|import)\s+(core\.constraints|ai\.narrate)\b", src):
                importers.append(rel)
            # A function whose whole body is `return True` under a def named like
            # a checker — the exact stub class this smoke exists to ban.
            if d in ("api", "core", "agents", "ai", "payments") and re.search(
                    r"def (satisfies|score_plan|best_plan)\(", src):
                stub_hits.append(rel)
check("nothing imports core.constraints or ai.narrate", not importers, str(importers))
check("no satisfies/score_plan/best_plan stubs anywhere in live code",
      not stub_hits, str(stub_hits))

from agents import persona as P
from agents.convener import run_negotiation, _annotate_plan
from agents.persona import stance

# ---------------------------------------------------------------------------
section("2. PER-CONSTRAINT TRUTH — each unmet constraint drops the happy count")
# ---------------------------------------------------------------------------
VENUE = {
    "id": "test_venue", "name": "Test Garden", "halal": True, "vegetarian": False,
    "tags": ["halal", "meat"],
}
PLAN = {
    "plan_id": "PLAN-T", "title": "Dinner at Test Garden", "day": "FRI",
    "time": "19:30", "venue": VENUE, "activity": {"name": "Walk"},
    "per_person": 50, "total_cost": 250, "satisfies": [], "conflicts": [],
}


def member(mid, days=("FRI",), budget=100, dietary=(), freeform=""):
    return {"id": mid, "name": mid, "freeform": freeform,
            "constraints": {"available_days": list(days), "budget_max": budget,
                            "dietary": list(dietary)}}


ok_member = member("M-OK")
veg_member = member("M-VEG", dietary=["vegetarian"])       # venue has no veg option
broke_member = member("M-BROKE", budget=30)                # 50/head > $30 cap
sat_member = member("M-SAT", days=("SAT",))                # can't do FRI
unknown_member = member("M-KOSHER", dietary=["kosher"])    # need not verifiable

evals = [stance(m, PLAN) for m in
         (ok_member, veg_member, broke_member, sat_member, unknown_member)]
by_agent = {e["agent"]: e for e in evals}

check("all-met member is satisfied", by_agent["M-OK"]["stance"] == "accept",
      str(by_agent["M-OK"]))
check("unmet dietary (vegetarian) -> unsatisfied, with the reason",
      by_agent["M-VEG"]["stance"] == "object"
      and by_agent["M-VEG"]["constraint_ref"] == "dietary", str(by_agent["M-VEG"]))
check("budget below per-head -> unsatisfied, with the reason",
      by_agent["M-BROKE"]["stance"] == "object"
      and by_agent["M-BROKE"]["constraint_ref"] == "budget_max", str(by_agent["M-BROKE"]))
check("unavailable day -> unsatisfied, with the reason",
      by_agent["M-SAT"]["stance"] == "object"
      and by_agent["M-SAT"]["constraint_ref"] == "available_days", str(by_agent["M-SAT"]))
check("UNKNOWN dietary need fails closed (no silent pass)",
      by_agent["M-KOSHER"]["stance"] == "object"
      and by_agent["M-KOSHER"]["constraint_ref"] == "dietary", str(by_agent["M-KOSHER"]))
check("unknown need IS satisfied when the venue explicitly tags it",
      P._venue_supports({"venue": {"tags": ["kosher"]}}, "kosher") is True)

annotated = _annotate_plan(PLAN, evals)
check("happy count == real satisfied set (1 of 5)",
      annotated["satisfies"] == ["M-OK"] and len(annotated["conflicts"]) == 4,
      f"satisfies={annotated['satisfies']} conflicts={len(annotated['conflicts'])}")

# ---------------------------------------------------------------------------
section("3. FULL PIPELINE — run_negotiation reports < full when needs unmet")
# ---------------------------------------------------------------------------
group = [ok_member, member("M-OK2"), member("M-OK3"), veg_member]
result = run_negotiation([dict(PLAN)], group)
plan_out = result["plan"]
check("pipeline: 3 of 4 satisfied (veg need honestly unmet)",
      sorted(plan_out.get("satisfies") or []) == ["M-OK", "M-OK2", "M-OK3"],
      str(plan_out.get("satisfies")))
check("pipeline: the conflict names the member and the constraint",
      any(c.get("agent") == "M-VEG" and c.get("constraint_ref") == "dietary"
          for c in plan_out.get("conflicts") or []), str(plan_out.get("conflicts")))
check("pipeline: quorum still honest (3/4 >= half)", result.get("quorum") is True,
      str(result.get("quorum")))

# ---------------------------------------------------------------------------
section("4. ORK-001 — 5/5 comes from REAL evaluation of the frozen plan")
# ---------------------------------------------------------------------------
from core import db, session as S

S.delete_session("ORK-001")  # re-seed fresh so we test the CURRENT seed contract
S.seed_demo()
ork = db.get_session("ORK-001")
ork_plan = (ork or {}).get("plan") or {}
member_ids = sorted(m["id"] for m in ork.get("members", []) if isinstance(m, dict))
stored = sorted(ork_plan.get("satisfies") or [])
check("ORK-001 satisfies == all 5 members", stored == member_ids and len(stored) == 5,
      f"stored={stored}")
# Independent recomputation with the real engine must reproduce the stored list —
# proving the 5/5 is evaluated, not stamped (Bob's cap genuinely covers $80/head).
recomputed = sorted(e["agent"] for e in
                    (stance(m, ork_plan) for m in ork.get("members", []))
                    if e["stance"] == "accept")
check("ORK-001 satisfies matches independent real evaluation", recomputed == stored,
      f"recomputed={recomputed} stored={stored}")
check("ORK-001 conflicts empty under real evaluation",
      (ork_plan.get("conflicts") or []) == [], str(ork_plan.get("conflicts")))
check("ORK-001 frozen contract intact (Seoul Garden, $80, 10 messages)",
      (ork_plan.get("venue") or {}).get("name") == "Seoul Garden"
      and ork_plan.get("per_person") == 80
      and len(ork.get("negotiation") or []) == 10,
      f"venue={(ork_plan.get('venue') or {}).get('name')} pp={ork_plan.get('per_person')} "
      f"msgs={len(ork.get('negotiation') or [])}")
# And the frozen plan is not vacuously satisfiable: a $40-cap member WOULD object.
probe = stance(member("M-PROBE", budget=40), ork_plan)
check("frozen plan still objects for a genuinely-broke probe member ($40 cap)",
      probe["stance"] == "object" and probe["constraint_ref"] == "budget_max",
      str(probe))

# ---------------------------------------------------------------------------
section("5. generate_real_plan path evaluates too (no stamped satisfies)")
# ---------------------------------------------------------------------------
sid = "TEST-CH-1"
db.set_session(sid, {
    "session_id": sid, "name": sid, "phase": "negotiating",
    "members": [member("M-OK"), member("M-VEG", dietary=["vegetarian"])],
    "negotiation": [], "plan": None, "handshake": None,
    "approvals": [], "expenses": [], "settlement": None,
})
try:
    plan = S.generate_real_plan(sid)  # offline -> falls back to sample venues
    sat = plan.get("satisfies") or []
    con = plan.get("conflicts") or []
    veg_ok = P._venue_supports(plan, "vegetarian")
    if veg_ok:
        expected = ["M-OK", "M-VEG"]
    else:
        expected = ["M-OK"]
    check("real-plan path: satisfies matches real evaluation (not everyone-stamped)",
          sorted(sat) == expected,
          f"sat={sat} veg_supported={veg_ok} conflicts={con}")
finally:
    db.delete_session(sid)

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
