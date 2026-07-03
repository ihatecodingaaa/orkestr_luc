"""Presenter-only "fresh x402 proof" — one deliberate, real $0.001 USDC transfer.

The static verified receipt (0x7044…62dd) stays the safe fallback. This module
adds a SEPARATE, deliberately-triggered path so the presenter can create ONE new
real Base-Sepolia USDC x402 transaction live during judging and show the fresh
tx hash + Basescan link. It is completely independent of the normal live booking
flow, the paid-smoke endpoint, plan computation, and the split calculation.

Safety contract (mirrors the paid-smoke gates, plus cooldown + in-flight lock):
  * ENABLE_X402_DEMO_RECEIPT must be EXACTLY "true"     -> else the run is 404.
  * X402_DEMO_RECEIPT_SECRET must match (constant-time)  -> else 403 Unauthorized.
  * body {"confirm":"PAY_0.001_USDC"} exact              -> else 400.
  * X402_DEMO_RECEIPT_AMOUNT must be exactly 1000        -> else 500 (misconfig).
  * A cooldown blocks repeat runs; an in-flight lock blocks double-clicks (429/409).
The real on-chain work is delegated to the audited x402_protocol.run_paid_smoke
(same buyer/merchant, same never-pay-yourself guard, same sanitized output). No
secret, private key, or signed payload is ever returned, logged, or printed.

In-memory state (in-flight flag, last-success time, latest fresh receipt) is
per-process and RESETS on a Railway restart — acceptable for a demo.

Owner: Lucas (x402 plumbing).
"""

import hmac
import logging
import os
import threading
import time
from datetime import datetime, timezone

from payments import x402_protocol as XP

LOGGER = logging.getLogger(__name__)

# The one exact amount this feature is allowed to move: 1000 base units = 0.001 USDC.
REQUIRED_AMOUNT_BASE_UNITS = 1000
CONFIRM_PHRASE = "PAY_0.001_USDC"

FRESH_SOURCE = "fresh_demo_receipt"
FRESH_LABEL = "Fresh x402 testnet receipt"
SAVED_SOURCE = "saved_verified_receipt"
SAVED_LABEL = "Saved verified receipt"

DISPLAY_NETWORK = "Base Sepolia"

# The static, previously-settled verified receipt — the safe fallback that the
# UI shows until (and unless) the presenter deliberately runs a fresh proof.
# These are PUBLIC on-chain values (addresses + tx hash); nothing here is secret.
SAVED_RECEIPT = {
    "payment_mode": "x402_real",
    "network": "eip155:84532",
    "chain": DISPLAY_NETWORK,
    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    "amount": REQUIRED_AMOUNT_BASE_UNITS,
    "amount_display": "0.001 USDC",
    "buyer": "0x851b9DeCa5BE1d143422B70459bD8843cD870BDa",
    "merchant": "0xd850A5ac28bd3648eB6c08cCa416291ee0141EFc",
    "txHash": "0x7044fa48cf844251bc4f815ff0464c5255393fb398259c12bb11d2ef5eeb62dd",
    "blockExplorerUrl": (
        "https://sepolia.basescan.org/tx/"
        "0x7044fa48cf844251bc4f815ff0464c5255393fb398259c12bb11d2ef5eeb62dd"
    ),
    "source": SAVED_SOURCE,
    "label": SAVED_LABEL,
    "created_at": None,
    "settled_at": None,
}


# ---------------------------------------------------------------------------
# In-memory state (per process; resets on restart)
# ---------------------------------------------------------------------------

# _RUN_LOCK is the in-flight guard: acquired non-blocking around the real payment
# so a second concurrent run gets a clean 409 instead of a double-spend. _STATE
# holds the cooldown clock + latest fresh receipt, guarded by _STATE_LOCK.
_RUN_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_last_success_monotonic = None  # time.monotonic() of the last successful run
_latest_fresh_receipt = None    # sanitized public receipt dict, or None


def _reset_state_for_tests():
    """Test-only: clear cooldown + latest receipt (never used in production)."""
    global _last_success_monotonic, _latest_fresh_receipt
    with _STATE_LOCK:
        _last_success_monotonic = None
        _latest_fresh_receipt = None
    if _RUN_LOCK.locked():  # best-effort; only a leaked test lock would be held
        try:
            _RUN_LOCK.release()
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Flags & config (no secrets in any return value)
# ---------------------------------------------------------------------------

def is_demo_receipt_enabled() -> bool:
    """True ONLY when ENABLE_X402_DEMO_RECEIPT is EXACTLY the string "true".

    Intentionally strict (like the live-spend gate, not the permissive _flag):
    a near-miss value ("True", "1", "yes", "", ...) keeps the run endpoint 404
    so it can never spend USDC by accident.
    """
    return os.environ.get("ENABLE_X402_DEMO_RECEIPT", "").strip() == "true"


def _configured_secret() -> str:
    return (os.environ.get("X402_DEMO_RECEIPT_SECRET") or "").strip()


def get_amount() -> int:
    """Configured amount in base units. Defaults to 1000; never raises."""
    raw = (os.environ.get("X402_DEMO_RECEIPT_AMOUNT") or "").strip()
    if not raw:
        return REQUIRED_AMOUNT_BASE_UNITS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1  # clearly-invalid sentinel -> fails the == 1000 config check


def get_cooldown_seconds() -> int:
    raw = (os.environ.get("X402_DEMO_RECEIPT_COOLDOWN_SECONDS") or "").strip()
    if not raw:
        return 60
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 60


def _amount_display(base_units: int) -> str:
    """Render a USDC base-unit amount (6 decimals) as e.g. "0.001 USDC"."""
    try:
        return f"{XP._format_units(int(base_units), 6)} USDC"
    except Exception:
        return "0.001 USDC"


def _cooldown_remaining_seconds() -> int:
    with _STATE_LOCK:
        last = _last_success_monotonic
    if last is None:
        return 0
    elapsed = time.monotonic() - last
    remaining = get_cooldown_seconds() - elapsed
    return int(remaining) + 1 if remaining > 0 else 0


def _public_asset() -> str:
    cfg = XP.get_x402_config()
    return ((os.environ.get("X402_BASE_SEPOLIA_USDC") or "").strip()
            or SAVED_RECEIPT["asset"])


# ---------------------------------------------------------------------------
# Public read endpoints (never trigger a payment, never return secrets)
# ---------------------------------------------------------------------------

def demo_receipt_status() -> dict:
    """Public-safe status for the presenter panel. Never spends, never leaks."""
    cfg = XP.get_x402_config()
    amount = get_amount()
    with _STATE_LOCK:
        has_fresh = _latest_fresh_receipt is not None
    return {
        "enabled": is_demo_receipt_enabled(),
        "cooldown_seconds": get_cooldown_seconds(),
        "cooldown_remaining_seconds": _cooldown_remaining_seconds(),
        "in_progress": _RUN_LOCK.locked(),
        "amount": amount,
        "amount_display": _amount_display(amount),
        "network": DISPLAY_NETWORK,
        "network_id": cfg["network"],
        "asset": _public_asset(),
        "merchant": cfg["pay_to"] or None,
        "confirm_phrase": CONFIRM_PHRASE,
        "has_latest_fresh_receipt": has_fresh,
    }


def latest_receipt() -> dict:
    """Latest fresh receipt if one exists this process, else the saved fallback.

    Public-safe. Never triggers a payment; never returns secrets/signed payloads.
    """
    with _STATE_LOCK:
        if _latest_fresh_receipt is not None:
            return dict(_latest_fresh_receipt)
    return dict(SAVED_RECEIPT)


# ---------------------------------------------------------------------------
# Payment seam — the ONLY place a real signed x402 payment is initiated
# ---------------------------------------------------------------------------

def _run_real_x402_payment() -> dict:
    """Perform the real on-chain x402 payment via the audited paid-smoke path.

    Isolated so the guard smoke can monkeypatch THIS function and never spend
    real USDC. A tripwire on x402_protocol._do_paid_request / _build_buyer_session
    is the backstop that fails the test if real signing is ever reached.
    """
    return XP.run_paid_smoke()


def _build_fresh_receipt(smoke: dict) -> dict:
    """Shape a successful run_paid_smoke() result into the public fresh receipt.

    Only non-secret, public fields are copied through. The amount is taken from
    the validated config (== 1000) so the receipt copy is exact; the tx hash /
    addresses / Basescan link come from the on-chain settlement.
    """
    amount = get_amount()
    tx_hash = smoke.get("txHash")
    receipt = {
        "payment_mode": "x402_real",
        "network": smoke.get("network") or XP.get_x402_config()["network"],
        "chain": DISPLAY_NETWORK,
        "asset": smoke.get("asset") or _public_asset(),
        "amount": amount,
        "amount_display": _amount_display(amount),
        "buyer": smoke.get("from"),
        "merchant": smoke.get("to"),
        "txHash": tx_hash,
        "blockExplorerUrl": smoke.get("blockExplorerUrl")
        or (XP.BASESCAN_TX_BASE + str(tx_hash) if tx_hash else None),
        "source": FRESH_SOURCE,
        "label": FRESH_LABEL,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    pr = smoke.get("paymentResponse")
    if isinstance(pr, dict):
        # Only the boolean success flag — never payer/tx internals beyond txHash.
        receipt["paymentResponse"] = {"success": pr.get("success")}
    if smoke.get("parse_warning"):
        receipt["parse_warning"] = smoke["parse_warning"]
    return receipt


# ---------------------------------------------------------------------------
# The run orchestrator — all gating in one place, returns (status_code, body)
# ---------------------------------------------------------------------------

def run_demo_receipt(secret_provided, confirm) -> tuple:
    """Gate, throttle, and (only if everything passes) run ONE fresh x402 proof.

    Returns (status_code, body_dict). The caller (the FastAPI route) turns this
    into a JSONResponse verbatim. No secret material is ever placed in the body,
    logged, or raised. Ordering of the gates is deliberate:
      404 disabled -> 403 secret -> 400 confirm -> 500 amount misconfig ->
      429 cooldown -> 409 in-flight -> (run) -> 200 receipt / 502 payment failed.
    """
    global _last_success_monotonic, _latest_fresh_receipt

    # 1. Feature flag — behave as if the endpoint does not exist when off.
    if not is_demo_receipt_enabled():
        return 404, {"detail": "Not Found"}

    # 2. Secret (constant-time). A missing/empty configured secret cannot match.
    expected = _configured_secret()
    provided = (secret_provided or "").strip()
    if not expected or not hmac.compare_digest(provided, expected):
        # Generic message only — never reveal whether the secret was set/close.
        return 403, {"detail": "Unauthorized"}

    # 3. Exact confirmation phrase.
    if confirm != CONFIRM_PHRASE:
        return 400, {"detail": f'confirmation required: {{"confirm":"{CONFIRM_PHRASE}"}}'}

    # 4. Amount config must be exactly 1000 base units (fail safe, do not spend).
    if get_amount() != REQUIRED_AMOUNT_BASE_UNITS:
        LOGGER.warning("x402 demo receipt refused: X402_DEMO_RECEIPT_AMOUNT misconfigured")
        return 500, {"error": "amount_misconfigured",
                     "detail": "X402_DEMO_RECEIPT_AMOUNT must be exactly 1000 (0.001 USDC)."}

    # 5. Cooldown — block repeat spends within the window.
    remaining = _cooldown_remaining_seconds()
    if remaining > 0:
        return 429, {"error": "cooldown_active",
                     "cooldown_remaining_seconds": remaining,
                     "detail": f"Cooldown active. Try again in {remaining}s."}

    # 6. In-flight lock — a second concurrent run gets 409, never a double-spend.
    if not _RUN_LOCK.acquire(blocking=False):
        return 409, {"error": "run_in_progress",
                     "detail": "A demo receipt run is already in progress."}

    try:
        smoke = _run_real_x402_payment()
        if not isinstance(smoke, dict) or not smoke.get("ok"):
            # Payment did not confirm — surface a calm, secret-free reason. The
            # detailed (already-sanitized) diagnostics stay server-side.
            reason = (smoke or {}).get("error", "payment_not_confirmed") \
                if isinstance(smoke, dict) else "payment_not_confirmed"
            LOGGER.warning("x402 demo receipt payment not confirmed: %s", reason)
            return 502, {"ok": False, "error": str(reason),
                         "detail": "Fresh x402 payment did not settle. See server logs."}

        receipt = _build_fresh_receipt(smoke)
        with _STATE_LOCK:
            _last_success_monotonic = time.monotonic()
            _latest_fresh_receipt = receipt
        return 200, dict(receipt)
    finally:
        _RUN_LOCK.release()
