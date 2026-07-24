"""Daily paid-API usage counters + spend caps (Stage P1a — public cost control).

Every public room a stranger creates spends the owner's money on Google Places and
OpenAI calls. This module keeps a per-UTC-day counter for each provider, persisted
in SQLite via core.db so a process restart never resets it (a reset would re-open
the day's spend). The pipeline consults the cheap `*_available()` checks BEFORE an
outbound call and degrades HONESTLY when a cap is hit:
  * LLM over cap  -> deterministic ranker (rooms still resolve, reasons grounded)
  * Places over cap -> an explicit `capacity_limited` room state, NEVER a fake
    "no verified restaurant" result (that would disguise a spend cap as a search).

Counters are incremented at the actual outbound-call site (so retries count too).
The x402 payment layer is never touched by this module.
"""
import logging
import os
from datetime import datetime, timezone

from core import db

LOGGER = logging.getLogger(__name__)

PLACES = "places"
LLM = "llm"

_DEFAULT_CAPS = {PLACES: 500, LLM: 200}
_ENV_CAP = {PLACES: "PLACES_DAILY_CALL_CAP", LLM: "LLM_DAILY_CALL_CAP"}
_SOFT_WARN = 0.8  # log a warning once usage crosses this fraction of the cap


def today():
    """UTC date string (YYYY-MM-DD) — the counter bucket key component."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _counter_key(provider):
    return f"api:{provider}"


def cap_for(provider):
    """Env-configured daily cap for a provider (0 or absent => unlimited/disabled)."""
    raw = os.environ.get(_ENV_CAP.get(provider, ""), "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            LOGGER.warning("usage: invalid %s=%r — using default", _ENV_CAP[provider], raw)
    return _DEFAULT_CAPS.get(provider, 0)


def usage_today(provider, day=None):
    """How many outbound calls to `provider` have been recorded today."""
    return db.get_counter(day or today(), _counter_key(provider))


def record_call(provider, n=1):
    """Count an ACTUAL outbound call. Invoked at the HTTP call site so retries are
    counted. Logs a one-time soft-warn at the threshold and again at the cap. Never
    raises — a counter failure must never break a live room."""
    try:
        count = db.incr_counter(today(), _counter_key(provider), n)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("usage: record_call(%s) failed: %s", provider, type(e).__name__)
        return 0
    cap = cap_for(provider)
    if cap > 0:
        before = count - n
        warn_at = int(cap * _SOFT_WARN)
        if before < cap <= count:
            LOGGER.warning("usage: %s daily cap reached (%s/%s) — degrading honestly",
                           provider, count, cap)
        elif before < warn_at <= count:
            LOGGER.warning("usage: %s at %d%%+ of daily cap (%s/%s)",
                           provider, int(_SOFT_WARN * 100), count, cap)
    return count


def over_cap(provider, day=None):
    """True when today's usage has reached the cap (the next call must be blocked).
    A cap of 0/absent means the guard is disabled (unlimited)."""
    cap = cap_for(provider)
    if cap <= 0:
        return False
    return usage_today(provider, day) >= cap


def places_available():
    return not over_cap(PLACES)


def llm_available():
    return not over_cap(LLM)


def snapshot(day=None):
    """Read-only usage/cap summary for the gated admin status endpoint (Stage P2)."""
    day = day or today()
    return {p: {"used": usage_today(p, day), "cap": cap_for(p)} for p in (PLACES, LLM)}
