"""Faithful x402 HTTP 402 booking payment for Orkestr.

The Convener (buyer) agent pays the merchant agent's x402-protected booking
endpoint before the merchant returns a booking receipt. Settlement happens on
Base Sepolia via the x402 facilitator.

Design (verified against x402 2.14.0):
  * Merchant 402 is handled MANUALLY at route level (no x402 FastAPI middleware),
    so the project keeps fastapi==0.111.0 and no other route/CORS is affected.
  * Buyer uses the sync requests wrapper with an eth_account signer.

Safety contract:
  * All x402 imports are lazy — the app boots fine when x402 is absent and
    USE_REAL_X402 is false.
  * USE_REAL_X402=false  -> x402 is never invoked (callers use the ACP path).
  * REQUIRE_REAL_X402 governs whether a failed x402 attempt falls back
    (false, default) or becomes a controlled failure (true).
  * No-double-payment: a successful x402 200 is treated as paid even if the
    X-PAYMENT-RESPONSE header fails to parse, and the caller must NOT fall back
    to Stripe/mock after it (see resolve_booking_payment "action").
  * Secrets (EVM_PRIVATE_KEY) are never printed, logged, or returned.

Owner: Lucas (x402 plumbing).
"""

import logging
import os
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - dotenv is a hard dep, but never crash import
    pass

LOGGER = logging.getLogger(__name__)

MERCHANT_BOOK_PATH = "/api/x402/merchant/book"
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# Lazily-built, cached merchant server (built once when first needed).
_MERCHANT_SERVER = None
_MERCHANT_READY = False


# ---------------------------------------------------------------------------
# Flags & config (no secrets in any return value)
# ---------------------------------------------------------------------------

def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("true", "1", "yes")


def is_real_x402_enabled() -> bool:
    return _flag("USE_REAL_X402")


def is_real_x402_required() -> bool:
    return _flag("REQUIRE_REAL_X402")


def is_paid_smoke_enabled() -> bool:
    """Gate for the protected /api/x402/paid-smoke endpoint (off by default).

    Railway-only opt-in. When false the endpoint behaves as if it does not exist
    (HTTP 404) so it can never spend USDC by accident.
    """
    return _flag("ENABLE_X402_PAID_SMOKE")


def is_live_real_x402_enabled() -> bool:
    """Gate for the NORMAL live booking flow attempting a real x402 payment.

    Independent of USE_REAL_X402 (which only governs the merchant 402 challenge)
    and ENABLE_X402_PAID_SMOKE (the protected proof endpoint). When false
    (default), every live booking uses the ACP fallback/mock/Stripe path even
    when USE_REAL_X402 is true — so turning the merchant 402 on never silently
    starts spending USDC in the demo flow.
    """
    return _flag("ENABLE_LIVE_REAL_X402")


def _private_key_present() -> bool:
    return bool(os.environ.get("EVM_PRIVATE_KEY", "").strip())


def _private_key_valid_format() -> bool:
    return bool(_PRIVATE_KEY_RE.match(os.environ.get("EVM_PRIVATE_KEY", "").strip()))


def get_x402_config() -> dict:
    """Non-secret config used by both the merchant and the buyer."""
    return {
        "network": os.environ.get("X402_NETWORK", "eip155:84532").strip(),
        "facilitator_url": os.environ.get(
            "X402_FACILITATOR_URL", "https://x402.org/facilitator").strip(),
        "price": os.environ.get("X402_BOOKING_PRICE", "$0.001").strip(),
        "pay_to": os.environ.get("X402_MERCHANT_PAY_TO", "").strip(),
        "resource_base_url": os.environ.get(
            "X402_RESOURCE_BASE_URL", "http://localhost:8000").strip().rstrip("/"),
        "timeout_seconds": float(os.environ.get("X402_HTTP_TIMEOUT", "20") or "20"),
    }


def _x402_importable() -> bool:
    try:
        import x402  # noqa: F401
        return True
    except Exception:
        return False


def x402_config_status() -> dict:
    """Diagnostic status. Secrets are reported as presence/format ONLY."""
    cfg = get_x402_config()
    return {
        "use_real_x402": is_real_x402_enabled(),
        "require_real_x402": is_real_x402_required(),
        "enable_live_real_x402": is_live_real_x402_enabled(),
        "enable_paid_smoke": is_paid_smoke_enabled(),
        "network": cfg["network"],
        "facilitator_url": cfg["facilitator_url"],
        "price": cfg["price"],
        "merchant_pay_to": cfg["pay_to"] or "(unset)",  # public address, safe
        "merchant_pay_to_present": "yes" if cfg["pay_to"] else "no",
        "resource_base_url": cfg["resource_base_url"],
        "evm_private_key_present": "yes" if _private_key_present() else "no",
        "evm_private_key_valid_format": _private_key_valid_format(),
        "x402_sdk_importable": _x402_importable(),
    }


def _merchant_config_complete() -> bool:
    cfg = get_x402_config()
    return all([cfg["pay_to"], cfg["network"], cfg["facilitator_url"], cfg["price"]])


def _derive_buyer_address():
    """Public buyer address derived from EVM_PRIVATE_KEY, or None.

    Lazily imports eth_account so the module still imports on hosts where the
    EVM stack is unavailable. The private key is NEVER returned, printed, or
    logged — only the public address (safe to expose) is produced.
    """
    try:
        from eth_account import Account
        return Account.from_key(os.environ["EVM_PRIVATE_KEY"].strip()).address
    except Exception:
        return None


def buyer_address_safe() -> dict:
    """Derive the buyer's public address and assert buyer != merchant payTo.

    Protects against accidentally paying yourself. Returns:
      {"ok": bool, "buyer": <addr|None>, "merchant": <addr>, "error": <str|None>}
    Never returns or logs the private key (only public addresses).
    """
    cfg = get_x402_config()
    merchant = cfg["pay_to"]
    buyer = _derive_buyer_address()
    if not buyer:
        return {"ok": False, "buyer": None, "merchant": merchant,
                "error": "buyer_address_derivation_failed"}
    if merchant and buyer.lower() == merchant.lower():
        return {"ok": False, "buyer": buyer, "merchant": merchant,
                "error": "buyer_wallet_equals_merchant_wallet"}
    return {"ok": True, "buyer": buyer, "merchant": merchant, "error": None}


# ---------------------------------------------------------------------------
# Merchant side — manual 402 (no x402 FastAPI middleware)
# ---------------------------------------------------------------------------

def _build_merchant_server():
    """Build (once) the cached x402 resource server + EVM server scheme.

    The merchant needs NO private key — verify/settle are delegated to the
    facilitator. Raises on real config/SDK problems (callers handle it).
    """
    global _MERCHANT_SERVER, _MERCHANT_READY
    if _MERCHANT_READY and _MERCHANT_SERVER is not None:
        return _MERCHANT_SERVER

    from x402 import x402ResourceServerSync
    from x402.http import HTTPFacilitatorClientSync
    from x402.mechanisms.evm.exact import register_exact_evm_server

    cfg = get_x402_config()
    facilitator = HTTPFacilitatorClientSync({"url": cfg["facilitator_url"]})
    server = x402ResourceServerSync(facilitator)
    register_exact_evm_server(server, networks=cfg["network"])
    server.initialize()

    _MERCHANT_SERVER = server
    _MERCHANT_READY = True
    return server


def _merchant_requirements(server):
    from x402 import ResourceConfig
    cfg = get_x402_config()
    config = ResourceConfig(
        scheme="exact",
        payTo=cfg["pay_to"],
        price=cfg["price"],
        network=cfg["network"],
    )
    return server.build_payment_requirements(config)


def _booking_receipt(body: dict) -> dict:
    cfg = get_x402_config()
    body = body or {}
    return {
        "ok": True,
        "booking_ref": body.get("booking_ref") or "X402-BOOKING",
        "merchant": body.get("merchant") or "Merchant agent",
        "resource": "venue_booking",
        "paid_by": "convener_agent",
        "payment_protocol": "x402",
        "network": cfg["network"],
        "price": cfg["price"],
    }


def handle_merchant_payment(x_payment_header, body):
    """The merchant agent's manual 402 state machine.

    Returns (status_code, response_body: dict, response_headers: dict).
      * USE_REAL_X402 false / config incomplete -> 404 (endpoint inert)
      * no X-PAYMENT          -> 402 + payment requirements (no X-PAYMENT-RESPONSE)
      * X-PAYMENT invalid     -> 402 + error
      * verified + settled    -> 200 + booking receipt + X-PAYMENT-RESPONSE
    Never raises; never leaks secrets.
    """
    if not is_real_x402_enabled():
        return 404, {"ok": False, "error": "x402_disabled"}, {}
    if not _merchant_config_complete():
        return 404, {"ok": False, "error": "x402_not_configured"}, {}

    try:
        server = _build_merchant_server()
        requirements = _merchant_requirements(server)
    except Exception as exc:
        LOGGER.warning("x402 merchant setup failed: %s", exc.__class__.__name__)
        return 503, {"ok": False, "error": "x402_setup_failed"}, {}

    from x402.http import (X_PAYMENT_RESPONSE_HEADER, decode_payment_signature_header,
                           encode_payment_response_header)

    # --- unpaid: issue the 402 challenge ---
    if not x_payment_header:
        try:
            pr = server.create_payment_required_response(requirements)
            body_out = pr.model_dump(mode="json")
        except Exception as exc:
            LOGGER.warning("x402 build 402 failed: %s", exc.__class__.__name__)
            return 503, {"ok": False, "error": "x402_challenge_failed"}, {}
        return 402, body_out, {}

    # --- paid attempt: verify then settle via the facilitator ---
    try:
        payload = decode_payment_signature_header(x_payment_header)
    except Exception as exc:
        LOGGER.warning("x402 bad X-PAYMENT header: %s", exc.__class__.__name__)
        return 402, {"ok": False, "error": "invalid_payment_header"}, {}

    try:
        verify = server.verify_payment(payload, requirements[0])
    except Exception as exc:
        LOGGER.warning("x402 verify error: %s", exc.__class__.__name__)
        return 402, {"ok": False, "error": "verify_failed"}, {}

    if not getattr(verify, "is_valid", False):
        reason = getattr(verify, "invalid_reason", None) or "invalid_payment"
        return 402, {"ok": False, "error": str(reason)}, {}

    try:
        settle = server.settle_payment(payload, requirements[0])
    except Exception as exc:
        LOGGER.warning("x402 settle error: %s", exc.__class__.__name__)
        return 402, {"ok": False, "error": "settle_failed"}, {}

    if not getattr(settle, "success", False):
        reason = getattr(settle, "error_reason", None) or "settlement_failed"
        return 402, {"ok": False, "error": str(reason)}, {}

    receipt = _booking_receipt(body)
    headers = {}
    try:
        headers[X_PAYMENT_RESPONSE_HEADER] = encode_payment_response_header(settle)
    except Exception as exc:  # settlement succeeded; header is best-effort
        LOGGER.warning("x402 encode response header failed: %s", exc.__class__.__name__)
    return 200, receipt, headers


# ---------------------------------------------------------------------------
# Buyer side — Convener pays the merchant endpoint
# ---------------------------------------------------------------------------

def parse_payment_response_header(value):
    """Decode an X-PAYMENT-RESPONSE header into a plain dict, or None."""
    if not value:
        return None
    try:
        from x402.http import decode_payment_response_header
        settle = decode_payment_response_header(value)
        return settle.model_dump(mode="json")
    except Exception:
        return None


def _build_buyer_session():
    """Returns a requests.Session that auto-handles the x402 402 handshake.

    Raises on missing/invalid key or SDK problems — callers preflight first.
    Never logs the private key.
    """
    from eth_account import Account
    from x402 import x402ClientSync
    from x402.http.clients.requests import x402_requests
    from x402.mechanisms.evm import EthAccountSigner
    from x402.mechanisms.evm.exact import register_exact_evm_client

    cfg = get_x402_config()
    account = Account.from_key(os.environ["EVM_PRIVATE_KEY"].strip())
    signer = EthAccountSigner(account)
    client = x402ClientSync()
    register_exact_evm_client(client, signer, networks=cfg["network"])
    return x402_requests(client)


def _do_paid_request(booking_ctx: dict) -> dict:
    """Make the signed x402 booking request. Never raises.

    Returns one of:
      {"ok": True, "mode": "x402_real"|"x402_real_parse_warning", "tx_hash",
       "receipt", "payment_response", "parse_warning"}
      {"ok": False, "ambiguous": bool, "reason": <no-secret str>}
    """
    cfg = get_x402_config()
    url = cfg["resource_base_url"] + MERCHANT_BOOK_PATH
    try:
        session = _build_buyer_session()
    except Exception as exc:
        # Before any signed payment is submitted -> clearly incomplete.
        return {"ok": False, "ambiguous": False,
                "reason": f"buyer_init_failed:{exc.__class__.__name__}"}

    try:
        resp = session.post(url, json=booking_ctx, timeout=cfg["timeout_seconds"])
    except Exception as exc:
        name = exc.__class__.__name__
        # A timeout may occur mid-settlement -> ambiguous (never double-charge).
        ambiguous = "Timeout" in name
        return {"ok": False, "ambiguous": ambiguous,
                "reason": f"request_failed:{name}"}

    # No-double-payment rule (Amendment 2): a 200 means the merchant accepted the
    # signed payment and returned a receipt — treat as paid even if the
    # X-PAYMENT-RESPONSE header fails to parse.
    if resp.status_code == 200:
        try:
            receipt = resp.json()
        except Exception:
            receipt = None
        from x402.http import X_PAYMENT_RESPONSE_HEADER
        hdr = resp.headers.get(X_PAYMENT_RESPONSE_HEADER)
        payment_response = parse_payment_response_header(hdr)
        tx_hash = None
        parse_warning = None
        mode = "x402_real"
        if payment_response is None:
            parse_warning = "missing_or_unparseable_payment_response"
            mode = "x402_real_parse_warning"
        else:
            tx_hash = payment_response.get("transaction")
        return {"ok": True, "mode": mode, "tx_hash": tx_hash,
                "receipt": receipt, "payment_response": payment_response,
                "parse_warning": parse_warning}

    # 402 here means the signed retry did not result in settlement; other codes
    # are merchant/transport errors. Either way payment was not confirmed.
    return {"ok": False, "ambiguous": False,
            "reason": f"merchant_status_{resp.status_code}"}


def resolve_booking_payment(booking_ctx: dict) -> dict:
    """Decide how the Convener pays for the booking.

    Returns a decision dict with one of these "action" values:
      "use_x402"           -> x402 paid; do NOT fall back. mode=x402_real[_parse_warning]
      "proceed_uncertain"  -> ambiguous after signing; do NOT fall back, do NOT
                              double-charge. mode=x402_uncertain
      "controlled_failure" -> REQUIRE_REAL_X402=true and x402 did not complete.
      "fallback"           -> use the existing ACP Stripe/mock path.
    Never raises; never leaks secrets.
    """
    if not is_real_x402_enabled():
        return {"action": "fallback", "mode": None, "reason": "USE_REAL_X402 not true"}

    # Live-flow safety switch: even with USE_REAL_X402 on (merchant 402 live),
    # the demo booking flow only attempts a real payment when explicitly enabled.
    # This is a plain fallback (never a controlled_failure) — the operator chose
    # not to spend, so REQUIRE_REAL_X402 must not turn that choice into a crash.
    if not is_live_real_x402_enabled():
        return {"action": "fallback", "mode": None,
                "reason": "ENABLE_LIVE_REAL_X402 not true"}

    required = is_real_x402_required()

    # Preflight: clearly-incomplete causes (no signed payment is ever sent).
    if not _x402_importable():
        return _incomplete(required, "x402_sdk_unavailable")
    if not _merchant_config_complete():
        return _incomplete(required, "merchant_config_incomplete")
    if not _private_key_present():
        return _incomplete(required, "evm_private_key_missing")
    if not _private_key_valid_format():
        return _incomplete(required, "evm_private_key_invalid_format")

    result = _do_paid_request(booking_ctx)
    if result.get("ok"):
        return {
            "action": "use_x402",
            "mode": result["mode"],
            "tx_hash": result.get("tx_hash"),
            "receipt": result.get("receipt"),
            "payment_response": result.get("payment_response"),
            "parse_warning": result.get("parse_warning"),
            "reason": None,
        }

    if result.get("ambiguous"):
        # Prefer safe reporting over double-charging (Amendment 2).
        if required:
            return {"action": "controlled_failure", "mode": "x402_failed",
                    "reason": result.get("reason")}
        return {"action": "proceed_uncertain", "mode": "x402_uncertain",
                "reason": result.get("reason")}

    # Clearly incomplete (no payment submitted/confirmed).
    return _incomplete(required, result.get("reason", "x402_incomplete"))


def _incomplete(required: bool, reason: str) -> dict:
    if required:
        return {"action": "controlled_failure", "mode": "x402_failed", "reason": reason}
    return {"action": "fallback", "mode": "x402_fallback", "reason": reason}


# ---------------------------------------------------------------------------
# Protected paid smoke — one real $0.001 USDC transfer on demand (Railway only)
# ---------------------------------------------------------------------------

BASESCAN_TX_BASE = "https://sepolia.basescan.org/tx/"


def _fetch_unpaid_challenge() -> dict:
    """Make an UNPAID request to the merchant endpoint and confirm a real 402.

    Returns {"ok": True, "asset", "amount", "payTo"} when the response is HTTP
    402 carrying accepts[], else {"ok": False, "error": <no-secret str>}.
    Uses a plain (unsigned) request so no payment is ever submitted here.
    """
    cfg = get_x402_config()
    url = cfg["resource_base_url"] + MERCHANT_BOOK_PATH
    try:
        import requests
        resp = requests.post(url, json={"booking_ref": "X402-PAID-SMOKE-CHALLENGE"},
                             timeout=cfg["timeout_seconds"])
    except Exception as exc:
        return {"ok": False, "error": f"challenge_request_failed:{exc.__class__.__name__}"}

    if resp.status_code != 402:
        return {"ok": False, "error": f"challenge_status_{resp.status_code}"}
    try:
        body = resp.json()
    except Exception:
        return {"ok": False, "error": "challenge_unparseable"}

    accepts = body.get("accepts") if isinstance(body, dict) else None
    if not isinstance(accepts, list) or not accepts or not isinstance(accepts[0], dict):
        return {"ok": False, "error": "challenge_missing_accepts"}
    req0 = accepts[0]
    amount = req0.get("amount")
    if amount is None:
        amount = req0.get("maxAmountRequired")
    return {"ok": True,
            "asset": req0.get("asset"),
            "amount": str(amount) if amount is not None else None,
            "payTo": req0.get("payTo")}


def run_paid_smoke() -> dict:
    """Execute ONE real x402 paid booking and return a sanitized, secret-free result.

    Intended to be reachable only behind the protected /api/x402/paid-smoke gate
    (feature flag + secret header + explicit confirmation body). The flow:
      1. Preflight: USE_REAL_X402 on, SDK present, merchant config + key valid.
      2. Derive the buyer public address and HARD-FAIL if buyer == merchant payTo.
      3. Unpaid request -> confirm HTTP 402 + accepts[] (asset/amount/payTo).
      4. Signed retry via the official x402 sync client -> settle on Base Sepolia.

    Never raises; never returns the private key or any secret. The demo is capped
    to the configured price ($0.001 USDC) — this function only ever asks the
    merchant for its single advertised requirement and signs that exact amount.
    """
    cfg = get_x402_config()
    asset_env = os.environ.get("X402_BASE_SEPOLIA_USDC", "").strip()

    # --- 1. preflight (no signed payment is ever sent on these paths) ---
    if not is_real_x402_enabled():
        return {"ok": False, "payment_mode": None, "error": "use_real_x402_disabled"}
    if not _x402_importable():
        return {"ok": False, "payment_mode": None, "error": "x402_sdk_unavailable"}
    if not _merchant_config_complete():
        return {"ok": False, "payment_mode": None, "error": "merchant_config_incomplete"}
    if not _private_key_present():
        return {"ok": False, "payment_mode": None, "error": "evm_private_key_missing"}
    if not _private_key_valid_format():
        return {"ok": False, "payment_mode": None, "error": "evm_private_key_invalid_format"}

    # --- 2. derive buyer address + never-pay-yourself guard ---
    guard = buyer_address_safe()
    if not guard["ok"]:
        return {"ok": False, "payment_mode": None, "error": guard["error"],
                "from": guard.get("buyer"), "to": guard.get("merchant")}
    buyer = guard["buyer"]
    merchant = guard["merchant"]

    # --- 3. confirm the unpaid challenge is a real 402 with accepts[] ---
    challenge = _fetch_unpaid_challenge()
    if not challenge.get("ok"):
        return {"ok": False, "payment_mode": None, "error": challenge.get("error"),
                "from": buyer, "to": merchant}
    asset = challenge.get("asset") or asset_env or None
    amount = challenge.get("amount")

    # --- 4. signed retry via the official x402 sync client ---
    paid = _do_paid_request({"booking_ref": "X402-PAID-SMOKE",
                             "merchant": "Orkestr paid smoke"})
    if not paid.get("ok"):
        return {"ok": False, "payment_mode": "x402_failed",
                "error": paid.get("reason", "payment_not_confirmed"),
                "ambiguous": bool(paid.get("ambiguous")),
                "network": cfg["network"], "asset": asset, "amount": amount,
                "from": buyer, "to": merchant}

    payment_response = paid.get("payment_response")
    tx_hash = paid.get("tx_hash")
    result = {
        "ok": True,
        "payment_mode": "x402_real",
        "network": cfg["network"],
        "asset": asset,
        "amount": amount,
        "from": buyer,
        "to": merchant,
        "txHash": tx_hash,
        "paymentResponsePresent": payment_response is not None,
    }
    if tx_hash:
        result["blockExplorerUrl"] = BASESCAN_TX_BASE + str(tx_hash)
    if payment_response:
        # Include only non-secret settle fields.
        result["paymentResponse"] = {
            "success": payment_response.get("success"),
            "network": payment_response.get("network"),
            "payer": payment_response.get("payer"),
            "transaction": payment_response.get("transaction"),
        }
    if paid.get("parse_warning"):
        # Payment settled (merchant returned 200) but the X-PAYMENT-RESPONSE
        # header could not be parsed -> no tx hash to claim. Still ok=true.
        result["parse_warning"] = paid["parse_warning"]
    return result
