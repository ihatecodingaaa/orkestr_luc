"""Harness safety guard: force EVERY real-x402 spend path OFF, in-process.

Import this FIRST in any in-process smoke — before `api.main`, `core.session`,
`agents.discovery`, or `payments.*` — e.g. right after the `sys.path.insert(...)`
line:

    import paidpath_guard  # noqa: F401  (force x402 spend paths off)

Why it works: the app loads config via `load_dotenv()` with the default
`override=False`, so any key ALREADY present in `os.environ` wins over the dev
`.env`. This module sets the spend flags to safe values at import time, so no
smoke can reach a signed payment even on a host whose `.env` has the live flags
on. Setting values the app treats as "off" (exact-"true" gates) — never secrets.

This is a belt for the whole suite; individual smokes may still add their own
stronger backstops (monkeypatching the payment seam / tripwiring the signer).
"""
import os

# Each of these gates the app open ONLY on the exact string "true"; anything else
# is treated as off. We set explicit off-values so the intent is unmistakable.
_SPEND_FLAGS_OFF = {
    "USE_REAL_X402": "false",          # x402_protocol.is_real_x402_enabled()
    "REQUIRE_REAL_X402": "false",      # never hard-require a live payment in tests
    "ENABLE_LIVE_REAL_X402": "",       # live-booking spend gate (empty != "true")
    "ENABLE_X402_DEMO_RECEIPT": "false",  # auto/demo receipt daemon -> paid smoke
    "ENABLE_X402_PAID_SMOKE": "false",    # protected /api/x402/paid-smoke endpoint
}

for _k, _v in _SPEND_FLAGS_OFF.items():
    os.environ[_k] = _v

# Public-readiness per-IP quotas (Stage P1b) are per-day and PERSISTED in
# sessions.db. The in-process suite shares ONE TestClient IP and one db file across
# runs, so a real quota would accumulate and 429 unrelated smokes. Disable by
# default for tests; public_limits_smoke.py sets this to "false" BEFORE importing
# this guard (setdefault won't override) to exercise the real limits. Production is
# unaffected — this env var is never set there. The paid-API SPEND caps need no
# such switch: offline smokes make no real Places/OpenAI calls, so those counters
# stay at 0 and never trip.
os.environ.setdefault("ORK_DISABLE_PUBLIC_LIMITS", "true")
