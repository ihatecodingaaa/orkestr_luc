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

import json
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


def _merchant_resource_info():
    """The canonical paid-resource descriptor for the 402 challenge.

    A spec-complete x402 PaymentRequired carries a non-null `resource` (the
    official client uses resource.url as the paywall/return URL). We advertise
    the exact paid resource URL: {resource_base_url}/api/x402/merchant/book.
    """
    from x402 import ResourceInfo
    cfg = get_x402_config()
    return ResourceInfo(
        url=cfg["resource_base_url"] + MERCHANT_BOOK_PATH,
        description="Orkestr x402 venue booking",
        mime_type="application/json",
    )


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

    from x402.http import (PAYMENT_REQUIRED_HEADER, PAYMENT_RESPONSE_HEADER,
                           X_PAYMENT_RESPONSE_HEADER, decode_payment_signature_header,
                           encode_payment_required_header, encode_payment_response_header)

    # --- unpaid: issue the 402 challenge ---
    # The official x402 client (2.14.0) accepts a 402 ONLY via the V2
    # PAYMENT-REQUIRED header or a V1 (x402Version==1) body; a bare V2 JSON body
    # is rejected with ValueError("Invalid payment required response"). We speak
    # V2, so we MUST emit the PAYMENT-REQUIRED header (base64 of the
    # PaymentRequired). The JSON body is kept for the raw-curl proof and human
    # readability — but the header is what makes the SDK client proceed to sign.
    if not x_payment_header:
        try:
            pr = server.create_payment_required_response(
                requirements, resource=_merchant_resource_info(),
                error="Payment required")
            body_out = pr.model_dump(mode="json")
            challenge_headers = {
                PAYMENT_REQUIRED_HEADER: encode_payment_required_header(pr)}
        except Exception as exc:
            LOGGER.warning("x402 build 402 failed: %s", exc.__class__.__name__)
            return 503, {"ok": False, "error": "x402_challenge_failed"}, {}
        return 402, body_out, challenge_headers

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
        encoded = encode_payment_response_header(settle)
        # V2 clients read PAYMENT-RESPONSE; keep X-PAYMENT-RESPONSE for V1 back-compat.
        headers[PAYMENT_RESPONSE_HEADER] = encoded
        headers[X_PAYMENT_RESPONSE_HEADER] = encoded
    except Exception as exc:  # settlement succeeded; header is best-effort
        LOGGER.warning("x402 encode response header failed: %s", exc.__class__.__name__)
    return 200, receipt, headers


# ---------------------------------------------------------------------------
# Safe diagnostics — reveal WHY an x402 payment failed, never leaking secrets
# ---------------------------------------------------------------------------
#
# Every string that can carry SDK/facilitator/attacker-controlled content is run
# through _sanitize before it is returned or logged. _sanitize:
#   * replaces any current env secret VALUE (EVM_PRIVATE_KEY, X402_SMOKE_SECRET
#     and every *_SECRET / *_KEY / *_TOKEN / PRIVATE / ... env value) on sight,
#   * redacts 0x + 64-or-more hex (private keys, EIP-712 signatures, hashes)
#     while preserving public 42-char (0x + 40 hex) addresses,
#   * redacts long base64/token runs (>= 44 chars), e.g. an encoded X-PAYMENT.
# Full signed payloads and X-PAYMENT / PAYMENT-SIGNATURE / Authorization header
# values are NEVER captured — only sanitized, length-capped, scalar-only views.

_SECRET_NAME_RE = re.compile(
    r"SECRET|PASSWORD|PASSWD|PRIVATE|PRIVKEY|MNEMONIC|SEED|"
    r"API[_-]?KEY|ACCESS[_-]?KEY|AUTH|TOKEN|SIGNING|CREDENTIAL",
    re.IGNORECASE,
)
_ALWAYS_SECRET_VARS = ("EVM_PRIVATE_KEY", "X402_SMOKE_SECRET")
_HEX_SECRET_RE = re.compile(r"0x[0-9a-fA-F]{64,}")
# A long base64/token RUN (>= 44 chars) plus optional trailing '=' padding. '='
# is intentionally NOT allowed mid-run: otherwise a "label=<addr>" pair would be
# glued into one long match and a public 42-char address would be redacted.
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_-]{44,}={0,2}")
_REDACTED = "[REDACTED]"
_MAX_DIAG_TEXT = 2000


def _secret_values() -> list:
    """Current env secret values (len >= 8) to redact on sight, longest-first."""
    vals = set()
    for name in _ALWAYS_SECRET_VARS:
        v = (os.environ.get(name) or "").strip()
        if len(v) >= 8:
            vals.add(v)
    for name, raw in os.environ.items():
        v = (raw or "").strip()
        if len(v) >= 8 and _SECRET_NAME_RE.search(name):
            vals.add(v)
    return sorted(vals, key=len, reverse=True)


def _sanitize(value):
    """Redact secret material from a value's string form. Never raises."""
    if value is None:
        return None
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return "[unstringable]"
    for secret in _secret_values():
        if secret and secret in text:
            text = text.replace(secret, _REDACTED)
    text = _HEX_SECRET_RE.sub(_REDACTED, text)
    text = _LONG_TOKEN_RE.sub(_REDACTED, text)
    if len(text) > _MAX_DIAG_TEXT:
        text = text[:_MAX_DIAG_TEXT] + "...[truncated]"
    return text


def _safe_scalar(value):
    """Pass scalars through, sanitize strings, reduce any object to '<type>'."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize(value)
    return f"<{type(value).__name__}>"


def _x402_version():
    """Installed x402 package version, or None. Never raises."""
    try:
        import x402
        v = getattr(x402, "__version__", None)
        if v:
            return v
    except Exception:
        pass
    try:
        from importlib.metadata import version
        return version("x402")
    except Exception:
        return None


def _facilitator_error_from_text(text):
    """Extract common error code/message fields from a JSON error body (sanitized)."""
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    for key in ("error", "errorCode", "error_code", "code", "message",
                "invalidReason", "invalid_reason", "reason", "errorReason",
                "error_reason", "detail", "description"):
        if data.get(key) is not None:
            out[key] = _safe_scalar(data[key])
    return out or None


def _safe_response_diag(resp) -> dict:
    """Status code, header NAMES (never values), and sanitized body of a response."""
    out = {}
    status = getattr(resp, "status_code", None)
    if status is None:
        status = getattr(resp, "status", None)
    if status is not None:
        out["status_code"] = status
    try:
        headers = getattr(resp, "headers", None)
        if headers is not None:
            out["header_names"] = sorted(str(k) for k in headers.keys())
    except Exception:
        pass
    text = None
    try:
        t = getattr(resp, "text", None)
        text = t if isinstance(t, str) else None
    except Exception:
        text = None
    if text is None:
        try:
            raw = getattr(resp, "content", None)
            if isinstance(raw, (bytes, bytearray)):
                text = raw.decode("utf-8", "replace")
        except Exception:
            text = None
    if isinstance(text, str):
        out["body"] = _sanitize(text)
        fac = _facilitator_error_from_text(text)
        if fac:
            out["facilitator_error"] = fac
    return out


def _safe_exception_diag(exc, depth: int = 0) -> dict:
    """Sanitized, recursion-bounded projection of an exception + its cause chain.

    Surfaces exactly what explains an x402 PaymentError: class name, sanitized
    str/repr, __dict__ key names, scalar-only __dict__ values, the known x402
    error fields (invalid_reason / error_reason / scheme / network / ...), any
    attached HTTP response, and the underlying __cause__ — and nothing else.
    """
    diag = {"class": exc.__class__.__name__}
    try:
        diag["str"] = _sanitize(str(exc))
    except Exception:
        diag["str"] = "[unstringable]"
    try:
        diag["repr"] = _sanitize(repr(exc))
    except Exception:
        diag["repr"] = "[unreprable]"

    d = getattr(exc, "__dict__", None)
    if isinstance(d, dict) and d:
        diag["dict_keys"] = sorted(str(k) for k in d.keys())
        safe = {}
        for k, v in d.items():
            try:
                safe[str(k)] = _safe_scalar(v)
            except Exception:
                safe[str(k)] = "[unreadable]"
        diag["dict_values_safe"] = safe

    fields = {}
    for attr in ("invalid_reason", "invalid_message", "error_reason",
                 "error_message", "reason", "scheme", "network", "code",
                 "message", "transaction", "payer"):
        try:
            if hasattr(exc, attr):
                val = getattr(exc, attr)
                if val is not None and not callable(val):
                    fields[attr] = _safe_scalar(val)
        except Exception:
            pass
    if fields:
        diag["x402_error_fields"] = fields

    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            diag["response"] = _safe_response_diag(resp)
        except Exception:
            pass

    if depth < 4:
        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        if cause is not None and cause is not exc:
            diag["cause"] = _safe_exception_diag(cause, depth + 1)
    return diag


# --- buyer Base Sepolia balances (read-only JSON-RPC; never blocks the smoke) ---

_BASE_SEPOLIA_DEFAULT_RPC = "https://sepolia.base.org"
_BASE_SEPOLIA_USDC_DEFAULT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_ERC20_BALANCEOF_SELECTOR = "0x70a08231"  # keccak256("balanceOf(address)")[:4]


def _format_units(raw: int, decimals: int) -> str:
    """Render an integer base-unit amount as a decimal string (no float error)."""
    sign = "-" if raw < 0 else ""
    digits = str(abs(raw)).rjust(decimals + 1, "0")
    whole, frac = digits[:-decimals], digits[-decimals:]
    frac = frac.rstrip("0")
    return f"{sign}{whole}.{frac}" if frac else f"{sign}{whole}"


def _hex_to_int(value) -> int:
    if not value or value == "0x":
        return 0
    return int(value, 16)


def _rpc_result(rpc_url: str, method: str, params: list, timeout: float):
    """One JSON-RPC call. Raises on transport/node error (the caller catches).

    Deliberately raises plain, message-free errors so the RPC URL (which may
    embed a provider key) can never leak through an exception string.
    """
    import requests
    resp = requests.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=timeout,
    )
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("rpc_non_object_response")
    if data.get("error") is not None:
        raise ValueError("rpc_node_error")
    return data.get("result")


def buyer_base_sepolia_balances(buyer: str) -> dict:
    """Buyer Base Sepolia ETH + USDC balances. Never raises; never leaks the RPC URL.

    Uses X402_BASE_SEPOLIA_RPC_URL when set, else https://sepolia.base.org. The
    RPC URL (which may embed a provider key) is never returned — only whether an
    override was used. Each balance is fetched independently; a failure is
    reported as the exception class name so the rest still renders.
    """
    if not buyer:
        return {"balance_check_error": "no_buyer_address"}
    cfg = get_x402_config()
    rpc_env = (os.environ.get("X402_BASE_SEPOLIA_RPC_URL") or "").strip()
    rpc_url = rpc_env or _BASE_SEPOLIA_DEFAULT_RPC
    usdc = ((os.environ.get("X402_BASE_SEPOLIA_USDC") or "").strip()
            or _BASE_SEPOLIA_USDC_DEFAULT)
    timeout = min(cfg["timeout_seconds"], 8.0)
    out = {"rpc_source": "env" if rpc_env else "default", "usdc_contract": usdc}

    try:
        wei = _hex_to_int(
            _rpc_result(rpc_url, "eth_getBalance", [buyer, "latest"], timeout))
        out["eth_wei"] = str(wei)
        out["eth"] = _format_units(wei, 18)
    except Exception as exc:
        out["eth_balance_error"] = exc.__class__.__name__

    try:
        calldata = (_ERC20_BALANCEOF_SELECTOR
                    + buyer.lower().replace("0x", "").rjust(64, "0"))
        raw = _hex_to_int(_rpc_result(
            rpc_url, "eth_call", [{"to": usdc, "data": calldata}, "latest"], timeout))
        out["usdc_base_units"] = str(raw)
        out["usdc"] = _format_units(raw, 6)
    except Exception as exc:
        out["usdc_balance_error"] = exc.__class__.__name__

    if "eth" not in out and "usdc" not in out:
        out["balance_check_error"] = "rpc_unavailable"
    return out


def _failure_diagnostics(buyer, merchant, challenge, payment_diag) -> dict:
    """Assemble the secret-free diagnostics block for a failed paid smoke."""
    cfg = get_x402_config()
    diag = {
        "buyer": buyer,
        "merchant": merchant,
        "x402_facilitator_url": cfg["facilitator_url"],
        "x402_network": cfg["network"],
        "x402_booking_price": cfg["price"],
        "x402_package_version": _x402_version(),
        "challenge_fetched_ok": bool(isinstance(challenge, dict)
                                     and challenge.get("ok")),
    }
    if isinstance(challenge, dict):
        if challenge.get("ok"):
            diag["challenge"] = {
                "scheme": challenge.get("scheme"),
                "network": challenge.get("network"),
                "asset": challenge.get("asset"),
                "amount": challenge.get("amount"),
                "payTo": challenge.get("payTo"),
                "resource": challenge.get("resource"),
                "payment_required_header_present":
                    challenge.get("payment_required_header_present"),
            }
        elif challenge.get("error"):
            diag["challenge_error"] = challenge.get("error")
    if payment_diag:
        diag["payment_attempt"] = payment_diag
    diag["buyer_balances"] = buyer_base_sepolia_balances(buyer)
    return diag


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
                "reason": f"buyer_init_failed:{exc.__class__.__name__}",
                "diagnostics": {"stage": "buyer_init",
                                "exception": _safe_exception_diag(exc)}}

    try:
        resp = session.post(url, json=booking_ctx, timeout=cfg["timeout_seconds"])
    except Exception as exc:
        name = exc.__class__.__name__
        # A timeout may occur mid-settlement -> ambiguous (never double-charge).
        ambiguous = "Timeout" in name
        # The x402 client wraps the real cause as PaymentError(... ) from <cause>;
        # _safe_exception_diag walks that __cause__ chain to surface the reason.
        return {"ok": False, "ambiguous": ambiguous,
                "reason": f"request_failed:{name}",
                "diagnostics": {"stage": "signed_request",
                                "exception": _safe_exception_diag(exc)}}

    # No-double-payment rule (Amendment 2): a 200 means the merchant accepted the
    # signed payment and returned a receipt — treat as paid even if the
    # X-PAYMENT-RESPONSE header fails to parse.
    if resp.status_code == 200:
        try:
            receipt = resp.json()
        except Exception:
            receipt = None
        from x402.http import PAYMENT_RESPONSE_HEADER, X_PAYMENT_RESPONSE_HEADER
        hdr = (resp.headers.get(PAYMENT_RESPONSE_HEADER)
               or resp.headers.get(X_PAYMENT_RESPONSE_HEADER))
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
    # are merchant/transport errors. Either way payment was not confirmed. The
    # merchant's body carries the verify/settle reason (sanitized) for diagnosis.
    return {"ok": False, "ambiguous": False,
            "reason": f"merchant_status_{resp.status_code}",
            "diagnostics": {"stage": "merchant_response",
                            "response": _safe_response_diag(resp)}}


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
    # resource is a serialized ResourceInfo object ({"url": ...}); surface its url.
    res = body.get("resource")
    resource_url = res.get("url") if isinstance(res, dict) else res
    # The V2 client requires the PAYMENT-REQUIRED header; report its presence
    # (requests' headers are case-insensitive). Plain HTTP probe — no x402 import.
    return {"ok": True,
            "scheme": req0.get("scheme"),
            "network": req0.get("network"),
            "asset": req0.get("asset"),
            "amount": str(amount) if amount is not None else None,
            "payTo": req0.get("payTo"),
            "resource": resource_url,
            "payment_required_header_present": bool(resp.headers.get("PAYMENT-REQUIRED"))}


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
                "from": buyer, "to": merchant,
                "diagnostics": _failure_diagnostics(buyer, merchant, challenge, None)}
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
                "from": buyer, "to": merchant,
                "diagnostics": _failure_diagnostics(
                    buyer, merchant, challenge, paid.get("diagnostics"))}

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
