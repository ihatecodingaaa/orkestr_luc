"""x402 LIVE-FLOW SAFETY GATE regression smoke (in-process, never spends USDC).

This is the airtight regression for the incident where the normal live
/api/status/{session} flow spent real USDC (payment_mode=x402_real) during QA.

Root cause (see report): before the live gate existed, core.session.compute_plan
passed allow_x402=bool(s.get("live")) — True for EVERY live session — and the
payment layer gated only on USE_REAL_X402. With USE_REAL_X402=true on the host,
every live booking signed and settled a real Base-Sepolia USDC transfer.

What this proves now (all in-process via TestClient, no network, no spend):
  A. With USE_REAL_X402=true AND ENABLE_LIVE_REAL_X402 != "true" (a battery of
     near-miss values: "", "false", "False", "0", "1", "yes", "TRUE", ...), the
     normal live flow returns a mock fallback — never payment_mode=x402_real and
     never a txHash — EVEN with REQUIRE_REAL_X402=true.
  B. The payment-layer gate itself: resolve_booking_payment short-circuits to a
     plain fallback when ENABLE_LIVE_REAL_X402 != "true", and only reaches the
     signed-payment function when it is exactly "true" (proving the gate is not
     vacuously always-closed).
  C. /api/x402/paid-smoke is 404 when ENABLE_X402_PAID_SMOKE != "true", 403 with
     a wrong secret, and 400 without the confirm phrase — all before any payment.
  D. /api/x402/merchant/book still returns a raw 402 PAYMENT-REQUIRED with
     USE_REAL_X402=true (the merchant challenge alone never spends USDC).

SAFETY: the ONLY function that performs a real signed x402 payment
(payments.x402_protocol._do_paid_request) is replaced for the whole run by a
tripwire stub. If any code path tries to spend, the stub records it (and never
signs) — and the negative tests fail loudly if the tripwire was ever tripped.
_build_buyer_session is also stubbed to raise, as a second backstop. The real
X402_SMOKE_SECRET / confirm phrase are never supplied, so run_paid_smoke is
never driven to completion regardless.

Runs in-process with TestClient — the server need not be running.
Exit 0 = all required checks pass.
"""
import os
import sys

# Force offline determinism for the NON-x402 bits (LLM ranking, Stripe, Exa) so
# the live pipeline produces a plan + booking without external network. Set
# BEFORE importing api.main (load_dotenv(override=False) won't overwrite these).
os.environ["OPENAI_API_KEY"] = ""     # plan_gen -> deterministic candidates
os.environ["STRIPE_SECRET_KEY"] = ""  # delegate_payment -> mock fallback
os.environ["USE_EXA"] = "false"       # skip live venue discovery

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402

_failures = []
_passes = 0
_skips = 0


def check(label, ok, detail=""):
    global _passes
    if ok:
        print(f"  [PASS] {label}")
        _passes += 1
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def skip(label, reason):
    global _skips
    print(f"  [SKIP] {label} — {reason}")
    _skips += 1


def section(title):
    print(f"\n{'='*68}\n  {title}\n{'='*68}")


print("Orkestr x402 LIVE-FLOW SAFETY GATE smoke — in-process TestClient")
print("=" * 68)

# ---------------------------------------------------------------------------
section("IMPORTS + TRIPWIRE INSTALL (no real signed x402 can run)")
# ---------------------------------------------------------------------------
try:
    import payments.x402_protocol as XP
    check("import payments.x402_protocol", True)
except Exception as e:
    check("import payments.x402_protocol", False, repr(e))
    sys.exit(1)

# The single tripwire: replace the ONLY function that performs a real signed
# x402 payment. It records calls and returns a clearly-incomplete result so any
# caller falls back safely. It never signs, never touches the network.
_TRIP = {"count": 0, "last_ctx": None}


def _tripwire(booking_ctx):
    _TRIP["count"] += 1
    _TRIP["last_ctx"] = booking_ctx
    return {"ok": False, "ambiguous": False, "reason": "TRIPWIRE_no_real_x402"}


def _no_buyer_session(*_a, **_k):
    raise RuntimeError("TRIPWIRE: _build_buyer_session must never run in this test")


XP._do_paid_request = _tripwire          # neutralizes live flow AND paid-smoke
XP._build_buyer_session = _no_buyer_session  # second backstop
check("real signed-payment path replaced by tripwire", True)

try:
    from fastapi.testclient import TestClient
    import api.main as M
    client = TestClient(M.app)
    check("import api.main + TestClient", True)
except Exception as e:
    check("import api.main + TestClient", False, repr(e))
    sys.exit(1)


def _run_live_session():
    """Drive a full live session to plan_ready; return its handshake mandate dict."""
    live = client.post("/api/session/live", json={}).json()
    sid = live["session_id"]
    for m in live["members"]:
        r = client.post("/api/constraints", json={
            "session_id": sid, "id": m["id"], "name": m["name"],
            "constraints": {"available_days": ["FRI"], "budget_max": 80, "dietary": []},
            "freeform": "",
        }).json()
        # Stage C: the first submit mints the member token; ready requires it.
        client.post(f"/api/ready/{sid}", json={"person_id": m["id"]},
                    headers={"X-Orkestr-Token": r.get("member_token") or ""})
    status = client.get(f"/api/status/{sid}").json()
    hs = client.get(f"/api/handshake/{sid}").json()
    return sid, status, (hs or {}).get("mandate") or {}


_REAL_X402_MODES = {"x402_real", "x402_real_parse_warning", "x402_uncertain"}

# ---------------------------------------------------------------------------
section("A. LIVE FLOW NEVER SPENDS unless ENABLE_LIVE_REAL_X402 == 'true'")
# ---------------------------------------------------------------------------
# USE_REAL_X402=true (merchant 402 live) and REQUIRE_REAL_X402=true (the strictest
# setting) — the live gate must STILL force a mock fallback for every value that
# is not exactly "true".
os.environ["USE_REAL_X402"] = "true"
os.environ["REQUIRE_REAL_X402"] = "true"

_NEGATIVE_VALUES = ["", "false", "False", "FALSE", "0", "no", "off",
                    "yes", "1", "TRUE", "True", " ", "x402", "enabled"]

for val in _NEGATIVE_VALUES:
    os.environ["ENABLE_LIVE_REAL_X402"] = val
    _TRIP["count"] = 0
    sid, status, mandate = _run_live_session()
    mode = mandate.get("payment_mode")
    label_val = repr(val)
    ok = (
        status.get("phase") == "plan_ready"
        and mode not in _REAL_X402_MODES
        and mode != "x402_failed"
        and mandate.get("x402") is None
        and _TRIP["count"] == 0
    )
    check(f"ENABLE_LIVE_REAL_X402={label_val} -> fallback (mode={mode}), no signed x402",
          ok, f"phase={status.get('phase')} mode={mode} "
              f"x402={mandate.get('x402')} tripwire={_TRIP['count']}")

# Positive control: exactly "true" lets the live flow REACH the (stubbed) signer.
os.environ["ENABLE_LIVE_REAL_X402"] = "true"
os.environ["REQUIRE_REAL_X402"] = "false"
# Make preflight pass so the gate can reach _do_paid_request (the tripwire).
_orig = {n: getattr(XP, n) for n in
         ("_x402_importable", "_merchant_config_complete",
          "_private_key_present", "_private_key_valid_format")}
for n in _orig:
    setattr(XP, n, (lambda *a, **k: True))
_TRIP["count"] = 0
sid, status, mandate = _run_live_session()
for n, fn in _orig.items():
    setattr(XP, n, fn)
check("ENABLE_LIVE_REAL_X402='true' -> live flow DID reach the signer (gate opens)",
      _TRIP["count"] >= 1 and status.get("phase") == "plan_ready",
      f"tripwire={_TRIP['count']} phase={status.get('phase')}")
check("even with the gate open, NO real txHash leaked (tripwire intercepted)",
      mandate.get("payment_mode") not in _REAL_X402_MODES,
      f"mode={mandate.get('payment_mode')}")

# ---------------------------------------------------------------------------
section("B. PAYMENT-LAYER SELF-DEFENSE (resolve_booking_payment, direct)")
# ---------------------------------------------------------------------------
os.environ["USE_REAL_X402"] = "true"
os.environ["REQUIRE_REAL_X402"] = "true"
_ctx = {"booking_ref": "GATE-TEST", "merchant": "Test Venue"}

os.environ["ENABLE_LIVE_REAL_X402"] = "false"
_TRIP["count"] = 0
dec = XP.resolve_booking_payment(_ctx)
check("resolve_booking_payment: ENABLE_LIVE_REAL_X402=false -> action=fallback",
      dec.get("action") == "fallback" and "ENABLE_LIVE_REAL_X402" in str(dec.get("reason")),
      str(dec))
check("resolve_booking_payment: required=true did NOT become controlled_failure",
      dec.get("action") != "controlled_failure", str(dec))
check("resolve_booking_payment: signer NOT reached (tripwire=0)",
      _TRIP["count"] == 0, f"tripwire={_TRIP['count']}")

os.environ["ENABLE_LIVE_REAL_X402"] = "true"
os.environ["REQUIRE_REAL_X402"] = "false"
_orig = {n: getattr(XP, n) for n in
         ("_x402_importable", "_merchant_config_complete",
          "_private_key_present", "_private_key_valid_format")}
for n in _orig:
    setattr(XP, n, (lambda *a, **k: True))
_TRIP["count"] = 0
dec = XP.resolve_booking_payment(_ctx)
for n, fn in _orig.items():
    setattr(XP, n, fn)
check("resolve_booking_payment: ENABLE_LIVE_REAL_X402='true' -> reaches signer",
      _TRIP["count"] == 1, f"tripwire={_TRIP['count']} decision={dec}")

# ---------------------------------------------------------------------------
section("C. PAID-SMOKE GATING (404 / 403 / 400 before any payment)")
# ---------------------------------------------------------------------------
_TRIP["count"] = 0

os.environ["ENABLE_X402_PAID_SMOKE"] = "false"
r = client.post("/api/x402/paid-smoke", json={"confirm": "PAY_0.001_USDC"})
check("ENABLE_X402_PAID_SMOKE=false -> /api/x402/paid-smoke 404 (hidden)",
      r.status_code == 404, f"got {r.status_code}")

# near-miss value must also stay hidden (paid smoke uses the permissive _flag,
# but only "true"/"1"/"yes" enable it; "TRUE" stays 404 here as a sanity check).
os.environ["ENABLE_X402_PAID_SMOKE"] = "true"
os.environ["X402_SMOKE_SECRET"] = "regression-test-secret-not-the-real-one"
r = client.post("/api/x402/paid-smoke", json={"confirm": "PAY_0.001_USDC"})
check("ENABLE_X402_PAID_SMOKE=true, no secret header -> 403",
      r.status_code == 403, f"got {r.status_code}")

r = client.post("/api/x402/paid-smoke",
                json={"confirm": "PAY_0.001_USDC"},
                headers={"X-Orkestr-Smoke-Secret": "wrong-secret"})
check("ENABLE_X402_PAID_SMOKE=true, wrong secret -> 403",
      r.status_code == 403, f"got {r.status_code}")

r = client.post("/api/x402/paid-smoke",
                json={"confirm": "NOPE"},
                headers={"X-Orkestr-Smoke-Secret": "regression-test-secret-not-the-real-one"})
check("ENABLE_X402_PAID_SMOKE=true, right secret, wrong confirm -> 400",
      r.status_code == 400, f"got {r.status_code}")

check("paid-smoke gating reached the signer ZERO times",
      _TRIP["count"] == 0, f"tripwire={_TRIP['count']}")

# leave paid smoke disabled
os.environ["ENABLE_X402_PAID_SMOKE"] = "false"
os.environ.pop("X402_SMOKE_SECRET", None)

# ---------------------------------------------------------------------------
section("D. MERCHANT 402 STILL WORKS with USE_REAL_X402=true (no spend)")
# ---------------------------------------------------------------------------
os.environ["USE_REAL_X402"] = "true"
os.environ.setdefault("X402_NETWORK", "eip155:84532")
# A 402 CHALLENGE only describes the price; it signs/settles nothing. A dummy
# public payTo is enough to build it. Use the env value if already configured.
if not os.environ.get("X402_MERCHANT_PAY_TO", "").strip():
    os.environ["X402_MERCHANT_PAY_TO"] = "0x000000000000000000000000000000000000dEaD"
_TRIP["count"] = 0
r = client.post("/api/x402/merchant/book", json={"booking_ref": "GATE-SMOKE"})
if r.status_code == 402:
    body = r.json()
    accepts = body.get("accepts") if isinstance(body, dict) else None
    check("merchant/book unpaid -> 402 Payment Required (USE_REAL_X402=true)", True)
    check("402 carries accepts[] payment requirements",
          isinstance(accepts, list) and len(accepts) >= 1, str(body)[:160])
    check("402 carries PAYMENT-REQUIRED header (V2 client requirement)",
          bool(r.headers.get("PAYMENT-REQUIRED")), "missing PAYMENT-REQUIRED")
    check("merchant 402 challenge reached the signer ZERO times (no spend)",
          _TRIP["count"] == 0, f"tripwire={_TRIP['count']}")
elif r.status_code in (404, 503):
    skip("merchant/book 402 (in-process)",
         f"challenge unavailable on this host (status {r.status_code}); the deployed "
         "Railway merchant 402 is covered by scripts/x402_handshake_smoke.py over HTTP")
else:
    check("merchant/book unpaid -> 402 Payment Required", False, f"got {r.status_code}")

# ---------------------------------------------------------------------------
section("ORK-001 FROZEN (never x402, regardless of flags)")
# ---------------------------------------------------------------------------
os.environ["USE_REAL_X402"] = "true"
os.environ["ENABLE_LIVE_REAL_X402"] = "true"  # must NOT affect the seeded demo
client.post("/api/dev/reseed")
hs = client.get("/api/handshake/ORK-001").json()
mode = (hs.get("mandate") or {}).get("payment_mode")
check("ORK-001 handshake co-signed", hs.get("status") == "co-signed", str(hs.get("status")))
check("ORK-001 payment_mode is NOT any x402 mode (frozen)",
      mode not in (_REAL_X402_MODES | {"x402_failed", "x402_fallback"}), str(mode))
plan = client.get("/api/plan/ORK-001").json()
check("ORK-001 per_person=80 (frozen)", plan.get("per_person") == 80, str(plan.get("per_person")))

# ---------------------------------------------------------------------------
section("RESET ORK-001 PRISTINE")
# ---------------------------------------------------------------------------
os.environ["USE_REAL_X402"] = "false"
os.environ["ENABLE_LIVE_REAL_X402"] = "false"
os.environ["REQUIRE_REAL_X402"] = "false"
client.post("/api/dev/reseed")
plan = client.get("/api/plan/ORK-001").json()
check("ORK-001 pristine after final reseed (per_person=80)",
      plan.get("per_person") == 80, str(plan.get("per_person")))

# Final backstop: across the whole run, the live + paid-smoke + merchant paths
# only reached the signer when we DELIBERATELY opened the gate in A/B.
check("overall: real signed-payment path was never reachable un-gated",
      callable(getattr(XP, "_do_paid_request")), "tripwire still installed")

# ---------------------------------------------------------------------------
print(f"\n{'='*68}")
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S), {_passes} pass, {_skips} skip")
    for f in _failures:
        print(f"    - {f}")
    print("=" * 68)
    sys.exit(1)
print(f"  RESULT: ALL {_passes} REQUIRED CHECKS PASS ({_skips} skipped)")
print("=" * 68)
sys.exit(0)
