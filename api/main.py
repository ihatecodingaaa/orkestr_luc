"""Orkestr API — FastAPI. Owner: Lucas.

Implements the frontend contract in ORKESTR_FRONTEND.md §4/§6, backed by an
in-memory session store (core/session.py). Seeded session ORK-001 is populated
on startup so every screen has data immediately.

Run from the repo root:
    uvicorn api.main:app --reload --port 8000
"""
import asyncio
import hmac
import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core import session as S


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Modern replacement for the deprecated @app.on_event("startup"). Idempotently
    # seeds ORK-001 on startup; the import-time seed below also runs it, so ORK-001
    # exists on EVERY start path — uvicorn (lifespan runs) and TestClient used
    # without a context manager (lifespan does NOT run; the import-time seed
    # covers it). seed_demo() no-ops if ORK-001 already exists, so it seeds exactly
    # once. The P0.4 mandate back-cosign lives inside seed_demo() and is unaffected.
    S.seed_demo()
    yield


app = FastAPI(title="Orkestr API", version="0.2.0", lifespan=_lifespan)

_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def _load(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


def _require_session(sid: str) -> dict:
    s = S.get(sid)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


# ---------------------------------------------------------------------------
# Room auth (Stage C). person_id used to be self-asserted — anyone with a room id
# could approve/spend/settle as anyone. Now every MUTATING endpoint verifies the
# caller's token (X-Orkestr-Token) against the identity it claims:
#   * member token  — minted once when a guest claims their slot (join stays one
#     tap: the token lives in browser storage, invisible to the user)
#   * host token    — minted at room creation; required for presenter actions and
#     accepted for member actions (presenter room control / Demo Mode simulation)
# ORK-001 is the public seeded sandbox (reseedable, no real PII) and is exempt.
# Reads never need a token; the room id itself is the view capability.
# ---------------------------------------------------------------------------

def _room_auth(s: dict) -> dict:
    auth = s.get("_auth")
    return auth if isinstance(auth, dict) else {}


def _is_host(s: dict, token: str | None) -> bool:
    host = _room_auth(s).get("host")
    return bool(token and host and hmac.compare_digest(str(token), str(host)))


def _is_member(s: dict, token: str | None, person_id: str | None) -> bool:
    mt = (_room_auth(s).get("members") or {}).get(person_id or "")
    return bool(token and mt and hmac.compare_digest(str(token), str(mt)))


def _require_actor(s: dict, sid: str, token: str | None, person_id: str | None) -> None:
    """403 unless the caller's token proves they are `person_id` (or the host)."""
    if sid == "ORK-001":
        return  # frozen public demo sandbox — reseedable, no real PII
    if _is_host(s, token) or _is_member(s, token, person_id):
        return
    raise HTTPException(status_code=403,
                        detail="member token required (you can only act as yourself)")


def _require_host(s: dict, sid: str, token: str | None) -> None:
    """403 unless the caller holds this room's host (presenter) token."""
    if sid == "ORK-001":
        return
    if _is_host(s, token):
        return
    raise HTTPException(status_code=403, detail="host token required")


# ---------------------------------------------------------------------------
# Enumeration guard: per-IP counter over API 404s ("misses"). Legit users
# almost never miss (their links carry real room ids); an enumerator misses on
# nearly every probe. Too many misses in the window -> 429 for API paths. The
# 22-char room ids are the real defense; this makes probing loud and slow too.
# In-memory per process (fine for the single-process deploy).
# ---------------------------------------------------------------------------
_RL_MISS_LIMIT = int(os.environ.get("ORK_RL_MISS_LIMIT", "60"))
_RL_WINDOW_SECONDS = int(os.environ.get("ORK_RL_WINDOW_SECONDS", "300"))
_RL_BLOCK_SECONDS = int(os.environ.get("ORK_RL_BLOCK_SECONDS", "300"))
_RL_STATE: dict = {}  # ip -> {"misses": [timestamps], "blocked_until": float}


def _rl_scoped(path: str) -> bool:
    return path.startswith("/api/") or path.startswith("/receipt/")


@app.middleware("http")
async def _enumeration_guard(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    path = request.url.path
    now = time.time()
    entry = _RL_STATE.setdefault(ip, {"misses": [], "blocked_until": 0.0})
    if _rl_scoped(path) and now < entry["blocked_until"]:
        return JSONResponse(status_code=429,
                            content={"detail": "too many attempts — slow down"})
    response = await call_next(request)
    if _rl_scoped(path) and response.status_code == 404:
        entry["misses"] = [t for t in entry["misses"] if now - t < _RL_WINDOW_SECONDS]
        entry["misses"].append(now)
        if len(entry["misses"]) > _RL_MISS_LIMIT:
            entry["blocked_until"] = now + _RL_BLOCK_SECONDS
    return response


def _require_field(body: dict, field: str) -> None:
    if field not in body or body[field] is None:
        raise HTTPException(status_code=400, detail=f"{field} required")


def _not_ready(s: dict) -> JSONResponse:
    """409 body for a read endpoint whose plan hasn't been computed yet (P1.1).

    The plan/negotiation/handshake GETs are PURE READS — they never run the
    pipeline. /api/status is the single controlled compute gate, so a direct GET or
    a browser refresh can never advance an unready live room. fail_reason is echoed
    when the room failed so the caller can render a failure state without a second
    round-trip; it is already public (also on /api/status and /public)."""
    ready = s.get("ready", []) or []
    body = {
        "status": "not_ready",
        "phase": s.get("phase"),
        "ready_count": len(ready),
        "members_total": len(s.get("members", []) or []),
        "message": "Plan is not ready yet.",
    }
    if s.get("phase") == "failed":
        body["booking_failed"] = bool(s.get("booking_failed"))
        body["fail_reason"] = s.get("fail_reason")
    return JSONResponse(status_code=409, content=body)


# Seed at import time so ORK-001 exists however the app is started (uvicorn, and
# TestClient used without a `with` block, where the lifespan handler never runs).
# seed_demo() is idempotent; the lifespan handler above also calls it.
S.seed_demo()


# ---- utility ----
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/personas")
def personas():
    return _load("personas.json")


@app.get("/venues")
def venues():
    return _load("venues.json")


# ---- x402: the merchant agent's payment-protected booking resource ----
# Faithful x402 HTTP 402 handshake (Path B), handled MANUALLY at route level so
# the project keeps fastapi==0.111.0 (the x402 FastAPI middleware would force
# fastapi>=0.115.0). Sync def -> runs in the threadpool, so the blocking
# facilitator call never blocks the event loop. Inert (404) unless USE_REAL_X402
# is on AND merchant config is present. Never mutates sessions; never touches
# ORK-001. Only this single route is payment-gated; CORS/other routes untouched.
@app.post("/api/x402/merchant/book")
def x402_merchant_book(payload: dict = Body(default={}),
                       payment_signature: str = Header(default=None, alias="PAYMENT-SIGNATURE"),
                       x_payment: str = Header(default=None, alias="X-PAYMENT")):
    from payments import x402_protocol as XP

    # The official x402 client retries a signed V2 payment in the PAYMENT-SIGNATURE
    # header (V1 used X-PAYMENT). Read V2 first, fall back to the legacy V1 header
    # so both client versions settle. decode_payment_signature_header handles both.
    payment_header = payment_signature or x_payment
    status_code, body_out, headers = XP.handle_merchant_payment(payment_header, payload)
    return JSONResponse(status_code=status_code, content=body_out, headers=headers)


# ---- x402: protected paid smoke (Railway-only, off by default) ----
# Triggers ONE real $0.001 Base-Sepolia USDC transfer (buyer -> merchant) so the
# faithful x402 settlement can be proven on-chain. Three independent gates run
# before any USDC moves:
#   1. ENABLE_X402_PAID_SMOKE must be "true"                -> else 404 (hidden)
#   2. X-Orkestr-Smoke-Secret must equal X402_SMOKE_SECRET  -> else 403
#   3. body {"confirm":"PAY_0.001_USDC"} exact              -> else 400
# Never mutates sessions; never touches ORK-001; returns only sanitized,
# secret-free fields (public addresses, tx hash, amount).
@app.post("/api/x402/paid-smoke")
def x402_paid_smoke(payload: dict = Body(default={}),
                    x_smoke_secret: str = Header(default=None, alias="X-Orkestr-Smoke-Secret")):
    import hmac

    from payments import x402_protocol as XP

    if not XP.is_paid_smoke_enabled():
        raise HTTPException(status_code=404, detail="Not Found")

    expected = os.environ.get("X402_SMOKE_SECRET", "").strip()
    provided = (x_smoke_secret or "").strip()
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="forbidden")

    if not isinstance(payload, dict) or payload.get("confirm") != "PAY_0.001_USDC":
        raise HTTPException(status_code=400,
                            detail='confirmation required: {"confirm":"PAY_0.001_USDC"}')

    result = XP.run_paid_smoke()
    status_code = 200 if result.get("ok") else 502
    return JSONResponse(status_code=status_code, content=result)


# ---- x402: presenter-only "fresh proof" demo receipt (Railway-only) ----
# A SEPARATE, deliberately-triggered path from the normal live flow, paid-smoke,
# participant flow, plan computation, and split. The run endpoint sends ONE real
# $0.001 USDC transfer only after five gates pass (feature flag + secret header +
# exact confirm phrase + amount==1000 + cooldown/in-flight). status/latest are
# read-only and NEVER spend. All three return only sanitized, secret-free fields.
@app.get("/api/x402/demo-receipt/status")
def x402_demo_receipt_status():
    from payments import x402_demo_receipt as XDR

    return XDR.demo_receipt_status()


@app.get("/api/x402/demo-receipt/latest")
def x402_demo_receipt_latest():
    from payments import x402_demo_receipt as XDR

    return XDR.latest_receipt()


def _demo_receipt_mock_fallback(reason: str) -> dict:
    """Build a mock demo-receipt body when the live run times out or errors.

    Honesty rule: no settlement happened, so NO hash of any kind is emitted —
    txHash is null (a random placeholder hex would be a fabricated transaction).
    payment_mode="mock" + fallback_reason make the non-settlement explicit, and
    real_tx_hash/explorer_url are null so no Basescan link is ever claimed.
    Mirrors the fresh-receipt shape so the frontend can render it unchanged.
    """
    from payments import x402_demo_receipt as XDR

    amount = XDR.get_amount()
    return {
        "ok": True,
        "payment_mode": "mock",
        "network": XDR.SAVED_RECEIPT["network"],
        "chain": XDR.DISPLAY_NETWORK,
        "asset": XDR.SAVED_RECEIPT["asset"],
        "amount": amount,
        "amount_display": XDR._amount_display(amount),
        "buyer": None,
        "merchant": None,
        "txHash": None,
        "blockExplorerUrl": None,
        "real_tx_hash": None,
        "explorer_url": None,
        "source": "mock_fallback",
        "label": "Mock fallback receipt",
        "fallback_reason": reason,
    }


@app.post("/api/x402/demo-receipt/run")
async def x402_demo_receipt_run(payload: dict = Body(default={}),
                                x_demo_secret: str = Header(default=None, alias="X-Orkestr-Demo-Secret")):
    from payments import x402_demo_receipt as XDR

    confirm = payload.get("confirm") if isinstance(payload, dict) else None

    # Run the (synchronous, never-raising) gated real-payment orchestrator in a
    # worker thread with an 8s ceiling so a slow/hung facilitator can never make
    # this request hang. On timeout or any unexpected error we DO NOT 502 — we
    # return an honest hash-free mock body (payment_mode="mock", txHash null) so
    # the endpoint always answers in <~8s. The upstream payment modules are untouched.
    loop = asyncio.get_running_loop()
    try:
        status_code, body_out = await asyncio.wait_for(
            loop.run_in_executor(None, XDR.run_demo_receipt, x_demo_secret, confirm),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        return JSONResponse(status_code=200,
                            content=_demo_receipt_mock_fallback("timeout"))
    except Exception:  # defensive: run_demo_receipt is documented never to raise
        return JSONResponse(status_code=200,
                            content=_demo_receipt_mock_fallback("error"))

    # Additive aliases on the SUCCESS body only. Gate rejections (404/403/400/500/
    # 429/409) and the payment-failure (502) body are returned byte-identical to
    # before, so the disabled endpoint still behaves exactly as if it did not exist.
    if status_code == 200 and isinstance(body_out, dict):
        body_out = dict(body_out)
        body_out["real_tx_hash"] = body_out.get("txHash")
        body_out["explorer_url"] = body_out.get("blockExplorerUrl")
        # Map the upstream payment_mode ("x402_real") to the judge-facing label.
        body_out["payment_mode"] = "live" if body_out.get("payment_mode") == "x402_real" else "mock"

    return JSONResponse(status_code=status_code, content=body_out)


# ---- Shareable group-mandate receipt + story-format share card ----
# A judge opens GET /receipt/{sid} on a phone after the demo. Read-only: no login,
# no JavaScript, no external stylesheet/CDN, inline CSS only. Unknown sid -> 404
# JSON. The payment proof reads latest_receipt() (saved verified or fresh, never
# triggers a payment). GET /receipt/{sid}/card is a fixed story-format share card.
# A venue photo, when present, loads via the /api/venue/photo proxy so the Google
# Places API key NEVER reaches the browser.
def _receipt_context(s):
    """Build the shared, escaped HTML fragments used by both receipt views."""
    import html as _html

    from payments import x402_demo_receipt as XDR

    plan = s.get("plan") or {}
    hs = s.get("handshake") or {}
    members = s.get("members") or []
    receipt = XDR.latest_receipt() or {}

    def esc(v):
        return _html.escape("" if v is None else str(v))

    session_name = esc(s.get("name") or s.get("session_id") or "—")
    date_range = s.get("date_range") or []
    date_str = esc(" – ".join(date_range)) if isinstance(date_range, list) and date_range else ""
    date_block = (f'<div style="color:#666;font-size:14px;margin-top:2px;">{date_str}</div>'
                  if date_str else "")

    names = [esc(m.get("name") or m.get("id")) for m in members if isinstance(m, dict)]
    signatories = ", ".join(n for n in names if n) or "—"

    venue = (plan.get("venue") or {}) if isinstance(plan, dict) else {}
    venue_name = esc(venue.get("name") or plan.get("title") or "—")
    day = esc(plan.get("day") or "")
    time_ = esc(plan.get("time") or "")
    when = " · ".join(x for x in (day, time_) if x) or "—"
    per_person = plan.get("per_person")
    per_person_str = f"${esc(per_person)}" if per_person is not None else "—"

    booking_ref = esc(hs.get("booking_ref") or plan.get("booking_ref") or "—")
    status = (hs.get("status") or plan.get("mandate_status") or "").strip()
    status_disp = esc(status or "—")
    cosigned = status == "co-signed"
    badge_css = ("color:#1a7f37;background:#e6f4ea;" if cosigned
                 else "color:#555;background:#eee;")

    # Venue photo via the proxy (relative URL, no API key). Absent for ORK-001.
    photo_url = plan.get("venue_photo_url") if isinstance(plan, dict) else None
    photo_html = (
        f'<img src="{esc(photo_url)}" alt="{venue_name}" '
        f'style="width:100%;height:180px;object-fit:cover;border-radius:8px;margin-bottom:16px;">'
        if photo_url else "")

    # Product-truth (same rule as the live app card): only a FRESH, real on-chain
    # settlement is the CURRENT payment proof. A saved fallback is shown ONLY as a
    # clearly-labelled PREVIOUS proof (never as this session's payment); a mock is
    # never rendered as a real receipt. latest_receipt() itself never returns a
    # mock, but we branch defensively so every receipt surface obeys one rule.
    rsource = receipt.get("source") if isinstance(receipt, dict) else None
    rmode = receipt.get("payment_mode") if isinstance(receipt, dict) else None
    tx = receipt.get("txHash") if isinstance(receipt, dict) else None
    explorer = receipt.get("blockExplorerUrl") if isinstance(receipt, dict) else None
    amount_disp = esc(receipt.get("amount_display") or "0.001 USDC") if isinstance(receipt, dict) else "0.001 USDC"
    settled_at = (receipt.get("settled_at") or receipt.get("created_at")) if isinstance(receipt, dict) else None
    is_mock = rsource == "mock_fallback" or rmode == "mock"
    is_fresh = rsource == XDR.FRESH_SOURCE and bool(tx)

    frontend = os.environ.get("FRONTEND_URL", "").strip()
    settle_href = esc((frontend.rstrip("/") + "/settle") if frontend else "/settle")
    _btn = ("display:block;box-sizing:border-box;min-height:44px;margin-top:10px;"
            "padding:14px 16px;text-align:center;text-decoration:none;font-weight:600;"
            "border-radius:12px;color:#fff;")

    def _tx_box(tx_value):
        tx_s = str(tx_value)
        disp = f"{tx_s[:10]}…{tx_s[-6:]}" if len(tx_s) > 16 else tx_s
        return (f'<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
                f'font-size:15px;color:#111;word-break:break-all;padding:12px 14px;'
                f'background:#f6f7f9;border-radius:10px;">{esc(disp)}</div>')

    if is_mock:
        # A mock/fallback body is NOT a real settlement — no tx hash, no Basescan.
        proof_html = (
            '<div style="padding:14px 16px;background:#fff7ed;color:#9a3412;'
            'border-radius:10px;font-size:15px;">x402 did not settle a real payment. '
            'No payment was sent.</div>'
        )
    elif is_fresh:
        tx_s = str(tx)
        explorer_esc = esc(explorer or ("https://sepolia.basescan.org/tx/" + tx_s))
        settled_line = (f'<div style="color:#1a7f37;font-size:13px;margin-top:6px;">'
                        f'Settled {esc(settled_at)}</div>' if settled_at else '')
        proof_html = (
            f'<div style="display:inline-block;background:#e6f4ea;color:#1a7f37;'
            f'font-size:12px;font-weight:700;padding:6px 12px;border-radius:999px;'
            f'margin-bottom:10px;">Fresh x402 testnet receipt · {amount_disp}</div>'
            f'{_tx_box(tx)}'
            f'{settled_line}'
            f'<a href="{explorer_esc}" target="_blank" rel="noopener" '
            f'style="{_btn}background:#0052FF;margin-top:12px;">View on Basescan ↗</a>'
            f'<a href="{settle_href}" target="_blank" rel="noopener" '
            f'style="{_btn}background:#888;">View live settlement →</a>'
        )
    else:
        # Saved fallback (or nothing) -> NEVER shown as this session's payment. Say
        # so plainly, and offer the prior verified tx only as a labelled PREVIOUS proof.
        proof_html = (
            '<div style="padding:14px 16px;background:#fff7ed;color:#9a3412;'
            'border-radius:10px;font-size:15px;">No current x402 payment receipt for '
            'this session yet.</div>'
        )
        if tx:
            tx_s = str(tx)
            explorer_esc = esc(explorer or ("https://sepolia.basescan.org/tx/" + tx_s))
            proof_html += (
                f'<div style="margin-top:14px;padding:14px 16px;border:1px dashed #ddd;'
                f'border-radius:10px;background:#fafafa;">'
                f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:0.04em;'
                f'color:#888;margin-bottom:8px;">Previous verified testnet proof</div>'
                f'{_tx_box(tx)}'
                f'<div style="color:#666;font-size:13px;margin-top:8px;">This proves the '
                f'x402 payment rail has worked before, but it is not the current session '
                f'payment.</div>'
                f'<a href="{explorer_esc}" target="_blank" rel="noopener" '
                f'style="{_btn}background:#888;margin-top:12px;">View previous proof on Basescan ↗</a>'
                f'</div>'
            )

    return {
        "session_name": session_name, "date_block": date_block,
        "signatories": signatories, "venue_name": venue_name, "when": when,
        "per_person_str": per_person_str, "booking_ref": booking_ref,
        "status_disp": status_disp, "badge_css": badge_css,
        "photo_html": photo_html, "proof_html": proof_html,
    }


def _receipt_card_body(ctx):
    """The inner white-card HTML shared by the receipt page and the share card.

    ctx['photo_html'] is the top venue <img> (empty string when no photo)."""
    return f"""      {ctx['photo_html']}
      <div style="font-size:13px;letter-spacing:0.04em;text-transform:uppercase;color:#666;">Orkestr · group mandate receipt</div>
      <h1 style="margin:6px 0 0;font-size:22px;line-height:1.25;">{ctx['session_name']}</h1>
      {ctx['date_block']}

      <div style="margin:16px 0 4px;">
        <span style="display:inline-block;background:#FF6B4A;color:#fff;font-size:12px;font-weight:700;letter-spacing:0.03em;padding:6px 12px;border-radius:999px;">AP2 Group Mandate</span>
        <span style="display:inline-block;margin-left:6px;font-size:12px;font-weight:600;padding:6px 12px;border-radius:999px;{ctx['badge_css']}">{ctx['status_disp']}</span>
      </div>

      <div style="margin-top:20px;">
        <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.04em;color:#888;margin-bottom:6px;">Signatories</div>
        <div style="font-size:16px;line-height:1.5;">{ctx['signatories']}</div>
      </div>

      <div style="margin-top:16px;border:1px solid #eee;border-radius:12px;padding:16px;">
        <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.04em;color:#888;margin-bottom:6px;">Venue</div>
        <div style="font-size:18px;font-weight:600;">{ctx['venue_name']}</div>
        <div style="color:#444;font-size:15px;margin-top:4px;">{ctx['when']}</div>
        <div style="color:#444;font-size:15px;margin-top:2px;">{ctx['per_person_str']} / person</div>
      </div>

      <div style="margin-top:16px;display:flex;justify-content:space-between;align-items:center;gap:12px;">
        <span style="font-size:12px;text-transform:uppercase;letter-spacing:0.04em;color:#888;">Booking handoff ref</span>
        <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:15px;font-weight:600;text-align:right;">{ctx['booking_ref']}</span>
      </div>

      <div style="margin-top:20px;">
        <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.04em;color:#888;margin-bottom:8px;">Payment proof</div>
        {ctx['proof_html']}
      </div>

      <div style="margin-top:24px;padding-top:16px;border-top:1px solid #eee;text-align:center;color:#999;font-size:12px;">verified on Base Sepolia · built at Build2026</div>"""


@app.get("/api/venue/photo")
def venue_photo(ref: str = ""):
    """Proxy a Google Places photo so the API key never reaches the browser.

    `ref` is the Places photo resource name (e.g. "places/AAA/photos/BBB"). Reads
    GOOGLE_PLACES_API_KEY from env, fetches the media server-side, and streams the
    bytes back with Google's Content-Type. 400 if ref missing; 503 if the key is
    unset; 502 on any fetch error. The key never appears in any client response.
    """
    import urllib.request

    from fastapi.responses import Response

    if not ref:
        raise HTTPException(status_code=400, detail="ref required")
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="photo service unavailable")
    url = f"https://places.googleapis.com/v1/{ref}/media?maxWidthPx=800&key={key}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            content = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
    except Exception:
        raise HTTPException(status_code=502, detail="failed to fetch venue photo")
    return Response(content=content, media_type=content_type)


@app.get("/receipt/{sid}")
def receipt_page(sid: str):
    from fastapi.responses import HTMLResponse

    s = _require_session(sid)  # raises 404 {"detail":"session not found"}
    ctx = _receipt_context(s)
    body = _receipt_card_body(ctx)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orkestr receipt · {ctx['session_name']}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f7;color:#111;-webkit-text-size-adjust:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:480px;margin:0 auto;padding:20px 16px 40px;">
    <div style="background:#fff;border-radius:16px;padding:22px 20px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
{body}
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=page, status_code=200)


@app.get("/receipt/{sid}/card")
def receipt_card(sid: str):
    """Fixed 480x850 story-format share card: same content as /receipt/{sid} plus a
    'Planned by Orkestr' watermark. Optimised for screenshot-and-share (no scroll)."""
    from fastapi.responses import HTMLResponse

    s = _require_session(sid)
    ctx = _receipt_context(s)
    body = _receipt_card_body(ctx)

    # Share controls (below the Basescan proof button): native share sheet, a
    # WhatsApp fallback when navigator.share is unavailable, and copy-link. All
    # self-contained inline HTML+JS — no CDN, no dependency. The share text carries
    # the (raw, un-HTML-escaped) venue name, made JS-safe via json.dumps; the "</"
    # replace prevents any "</script>" breakout from an odd venue name.
    plan = s.get("plan") if isinstance(s.get("plan"), dict) else {}
    venue = (plan.get("venue") or {}) if isinstance(plan, dict) else {}
    raw_venue = venue.get("name") or plan.get("title") or "our spot"
    title_js = json.dumps("Planned by Orkestr").replace("</", "<\\/")
    text_js = json.dumps(
        f"We just had dinner at {raw_venue} — planned in 90 seconds").replace("</", "<\\/")
    _sbtn = ("box-sizing:border-box;min-height:44px;padding:14px 16px;text-align:center;"
             "font-weight:600;border-radius:12px;font-size:15px;")
    share_html = ("""      <div style="margin-top:16px;display:flex;flex-direction:column;gap:10px;">
        <button id="ork-share" type="button" style="display:none;%(b)sborder:none;color:#fff;background:#111;cursor:pointer;">Share this receipt</button>
        <a id="ork-wa" href="#" target="_blank" rel="noopener" style="display:none;%(b)stext-decoration:none;color:#fff;background:#25D366;">Share on WhatsApp</a>
        <button id="ork-copy" type="button" style="%(b)sborder:1px solid #ddd;color:#111;background:#fff;cursor:pointer;">Copy link</button>
      </div>
      <script>
(function(){
  var shareTitle = %(title)s;
  var shareText = %(text)s;
  var url = window.location.href;
  var shareBtn = document.getElementById('ork-share');
  var waLink = document.getElementById('ork-wa');
  var copyBtn = document.getElementById('ork-copy');
  if (navigator.share) {
    shareBtn.style.display = 'block';
    shareBtn.addEventListener('click', function(){
      navigator.share({ title: shareTitle, text: shareText, url: url }).catch(function(){});
    });
  } else {
    waLink.style.display = 'block';
    waLink.href = 'https://wa.me/?text=' + encodeURIComponent(shareText + ' ' + url);
  }
  copyBtn.addEventListener('click', function(){
    var label = copyBtn.textContent;
    var done = function(){ copyBtn.textContent = 'Copied!'; setTimeout(function(){ copyBtn.textContent = label; }, 1600); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done).catch(done);
    } else { done(); }
  });
})();
      </script>""" % {"b": _sbtn, "title": title_js, "text": text_js})

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orkestr card · {ctx['session_name']}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f7;color:#111;-webkit-text-size-adjust:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="width:480px;min-height:850px;position:relative;overflow:hidden;margin:0 auto;background:#fff;box-sizing:border-box;padding:24px 22px;">
{body}
{share_html}
      <div style="position:absolute;bottom:12px;right:16px;font-size:11px;color:#888;">Planned by Orkestr</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=page, status_code=200)


# ---- Provable AP2 tampered-cart catch (presenter demo; NOT a live gate) ----
# POST /api/demo/tamper-check re-signs the incoming mandate (computes cart_digest
# from its own items — the "merchant signs the cart" moment), then either verifies
# it clean or tampers one item's amount and verifies the altered copy. It shows
# mandate.verify() catch a post-signing edit. Standalone capability only: verify()
# is NOT wired into the booking or payment path.
@app.post("/api/demo/tamper-check")
def demo_tamper_check(payload: dict = Body(default=None)):
    from payments.mandate import cosign, verify

    if (not isinstance(payload, dict)
            or not isinstance(payload.get("mandate"), dict)
            or not isinstance(payload.get("tamper"), bool)):
        raise HTTPException(
            status_code=400,
            detail='body requires "mandate" (object) and "tamper" (boolean)')

    mandate_in = payload["mandate"]
    tamper = payload["tamper"]

    # Re-sign so the mandate carries a valid cart_digest from its own items. Works
    # even on the seeded ORK-001 mandate, which is built without cosign() and so
    # has no cart_digest of its own.
    signed = cosign(mandate_in, mandate_in.get("merchant", "unknown"))

    original_amount = None
    tampered_amount = None
    tamper_applied = False

    items = signed.get("items") or []
    if (tamper and items and isinstance(items[0], dict)
            and isinstance(items[0].get("amount"), (int, float))
            and not isinstance(items[0].get("amount"), bool)):
        # Deep-copy via JSON round-trip (the mandate is JSON-safe) so `signed` is
        # never mutated; bump the first item's amount by 1 AFTER signing.
        tampered_copy = json.loads(json.dumps(signed))
        original_amount = tampered_copy["items"][0]["amount"]
        tampered_amount = original_amount + 1
        tampered_copy["items"][0]["amount"] = tampered_amount
        verified = verify(tampered_copy)
        tamper_applied = True
    else:
        # tamper=false, or nothing numeric to alter -> verify the clean signed one.
        verified = verify(signed)

    caught = tamper_applied and not verified
    detail = ("mandate integrity confirmed" if verified
              else "cart digest mismatch — venue altered item price after signing")
    return {
        "verified": verified,
        "caught": caught,
        "tamper_applied": tamper_applied,
        "detail": detail,
        "original_amount": original_amount,
        "tampered_amount": tampered_amount,
    }


# ---- Phase 1: before the meal ----
@app.post("/api/session/start")
def session_start(payload: dict = Body(default={})):
    s = S.new_session(payload.get("name"), payload.get("date_range"))
    invite_url, _ = _invite_and_qr(s["session_id"])  # single helper (P1.4)
    # host_token is returned ONCE, here, to the room creator only.
    return {"session_id": s["session_id"], "invite_url": invite_url,
            "host_token": (s.get("_auth") or {}).get("host")}


# ---- QR join: a scannable invite that turns a judge's phone into a live node ----
# No dependency/library: the QR image is rendered by api.qrserver.com. The
# invite_url matches the format returned by /api/session/start above.
def _frontend_base() -> str:
    """The configured frontend origin (no trailing slash). Defaults to the real
    Vercel domain — NEVER the dead orkestr.app placeholder (P1.4)."""
    return os.environ.get("FRONTEND_URL", "https://orkestr-luc.vercel.app").rstrip("/")


def _invite_and_qr(sid: str):
    """Return (invite_url, qr_url) for a session id — the SINGLE source of truth for
    every invite link (start, live, invite, QR, clone). The invite URL uses the
    real app's query-param route (`/?room=<sid>`), matching the frontend router and
    presenterLink(); the old `/join/<sid>` path had no page. qr_url is a
    ready-to-embed api.qrserver.com PNG encoding the invite_url."""
    from urllib.parse import quote

    invite_url = f"{_frontend_base()}/?room={quote(sid, safe='')}"
    qr_url = ("https://api.qrserver.com/v1/create-qr-code/?data="
              + quote(invite_url, safe="") + "&size=300x300&format=png")
    return invite_url, qr_url


@app.get("/api/session/{sid}/qr")
def session_qr(sid: str):
    """302-redirect to a scannable QR PNG pointing at the session invite_url."""
    from fastapi.responses import RedirectResponse

    _require_session(sid)  # 404 if the session does not exist
    _, qr_url = _invite_and_qr(sid)
    return RedirectResponse(qr_url, status_code=302)


@app.get("/api/session/{sid}/invite")
def session_invite(sid: str):
    """JSON {invite_url, qr_url} so the frontend can embed qr_url as an <img src>."""
    _require_session(sid)  # 404 if the session does not exist
    invite_url, qr_url = _invite_and_qr(sid)
    return {"invite_url": invite_url, "qr_url": qr_url}


@app.post("/api/session/live")
def session_live(payload: dict = Body(default={})):
    """Fresh, unseeded session with 5 member identities and empty constraints.

    The frontend renders one card per member; each member enters constraints
    live and presses find-our-plan. The pipeline runs only once all 5 are ready.
    """
    s = S.new_live_session(payload.get("name"), payload.get("date_range"))
    invite_url, _ = _invite_and_qr(s["session_id"])  # single helper (P1.4)
    return {
        "session_id": s["session_id"],
        "invite_url": invite_url,
        # host_token is returned ONCE, here, to the room creator (presenter) only.
        "host_token": (s.get("_auth") or {}).get("host"),
        "members": [{"id": m["id"], "name": m["name"], "avatar": m.get("avatar")}
                    for m in s["members"]],
    }


@app.post("/api/session/clone/{sid}")
def session_clone(sid: str,
                  x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    """Clone a settled session's crew into a fresh session for the next outing.

    "Friday coming up? Plan another dinner with this crew." Copies only the member
    id + name list (constraints come from group memory when members re-submit).
    Host-token gated: only the source room's presenter can clone its crew.
    Unknown sid -> 404; ORK-001 -> 400 (frozen demo, never cloned). Returns
    {session_id, invite_url, host_token} for the freshly created session.
    """
    src = _require_session(sid)
    _require_host(src, sid, x_orkestr_token)
    s = S.clone_session(sid)
    invite_url, _ = _invite_and_qr(s["session_id"])
    return {"session_id": s["session_id"], "invite_url": invite_url,
            "host_token": (s.get("_auth") or {}).get("host")}


@app.post("/api/ready/{sid}")
def ready(sid: str, body: dict = Body(...),
          x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    s = _require_session(sid)
    _require_field(body, "person_id")
    _require_actor(s, sid, x_orkestr_token, body["person_id"])
    S.mark_ready(sid, body["person_id"])
    return {"ready": s.get("ready", []),
            "members_total": len(s["members"]),
            "all_ready": S.all_ready(s)}


@app.post("/api/constraints")
def constraints(person: dict = Body(...),
                x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    _require_field(person, "session_id")
    sid = person["session_id"]
    s = _require_session(sid)
    _require_field(person, "id")
    member_id = person["id"]
    minted = None
    if sid != "ORK-001":
        # Join-time identity bind: the FIRST submission for a slot mints that
        # member's token (returned once, kept in the guest's browser — join stays
        # one tap, no account). A later submission for an already-claimed slot
        # must present the member token (or the host token) -> else 403, so no
        # one can overwrite another member's constraints.
        claimed = member_id in (( _room_auth(s).get("members")) or {})
        if claimed:
            _require_actor(s, sid, x_orkestr_token, member_id)
        else:
            minted, _ = S.mint_member_token(sid, member_id)
    S.add_constraints(sid, person)
    out = {"ok": True}
    if minted:
        out["member_token"] = minted
    return out


# ---- Group memory: a returning member's stored constraints (pre-fill form) ----
@app.get("/api/member/{member_id}/prefs")
def member_prefs(member_id: str):
    """Return a member's stored group-memory prefs so the frontend can pre-fill
    their constraint form. 404 if this member has never submitted before.
    """
    prefs = S.get_member_prefs(member_id)
    if prefs is None:
        raise HTTPException(status_code=404, detail="no stored prefs for member")
    return prefs


@app.get("/api/status/{sid}")
def status(sid: str):
    s = _require_session(sid)
    # Additive quorum report (P2.2) — pure read, never computes. Lets the presenter
    # see quorum readiness without drafting; the explicit POST /draft is the only
    # quorum compute trigger.
    q = S.quorum_report(s)
    if s["phase"] == "negotiating":
        # Live sessions wait until ALL members press find-our-plan before the lazy
        # auto-compute; a quorum draft is a separate, explicit presenter action
        # (POST /api/session/{sid}/draft) and never auto-fires here. Everyone else
        # (incl. ORK-001) keeps the original lazy-compute behaviour.
        if s.get("live") and not S.all_ready(s):
            return {"phase": s["phase"],
                    "ready": s.get("ready", []),
                    **q}
        S.compute_plan(sid)  # lazily produce the plan -> plan_ready
        q = S.quorum_report(s)  # refresh plan_basis now that a plan exists
    # Additive, data-derived budget nudge (real Places data only; never invented).
    nudge = {}
    if s.get("suggested_min_budget") is not None:
        nudge["suggested_min_budget"] = s.get("suggested_min_budget")
    if s.get("suggested_reason"):
        nudge["suggested_reason"] = s.get("suggested_reason")
    if s["phase"] == "failed":
        return {"phase": "failed", "booking_failed": True,
                "fail_reason": s.get("fail_reason"),
                "venue_status": s.get("venue_status"), **nudge, **q}
    return {"phase": s["phase"], "venue_status": s.get("venue_status"), **nudge, **q}


@app.post("/api/session/{sid}/draft")
def session_draft(sid: str,
                  x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    """P2.2 — presenter starts a QUORUM DRAFT once enough friends have replied,
    instead of waiting for everyone. Explicit trigger only (GETs never compute).
    HOST-ONLY (participants cannot trigger presenter actions). 400 if not a live
    session; 409 if quorum isn't reached yet. Idempotent for the same reply set.
    Runs under the P0 per-session compute lock."""
    s0 = _require_session(sid)
    _require_host(s0, sid, x_orkestr_token)
    s = S.draft_plan(sid)
    return {"ok": True, "phase": s.get("phase"),
            "plan_basis": s.get("plan_basis"),
            "draft_started_with": s.get("draft_started_with") or [],
            **S.quorum_report(s)}


@app.get("/api/session/{sid}/public")
def session_public(sid: str,
                   x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    """Read-only snapshot of a room — safe for any device to poll.

    Powers cross-device sync for the presenter command center and the mobile
    participant views (who has joined / submitted, readiness, phase). This NEVER
    runs the planning/booking pipeline (no compute_plan), so polling it can never
    trigger a real x402 payment.

    PII rule (Stage C): an unauthenticated caller gets only what a viewer needs —
    names/avatars, submitted/ready flags, plan/venue/status. Members' budgets,
    dietary constraints, and freeform notes are NEVER returned to strangers:
      * host token   -> full constraint detail for every member
      * member token -> that member's own constraint detail only
      * no/invalid token -> no constraint detail at all
    (ORK-001 is the seeded sandbox with fictional personas — full demo detail.)
    No private keys, no vault tokens, no room tokens are ever returned.
    """
    s = _require_session(sid)
    ready = list(s.get("ready", []))
    # Who is asking? Decides how much constraint detail each member row carries.
    viewer_is_host = sid == "ORK-001" or _is_host(s, x_orkestr_token)
    auth_members = (_room_auth(s).get("members") or {})
    viewer_member_id = None
    if not viewer_is_host and x_orkestr_token:
        for mid_, tok in auth_members.items():
            if hmac.compare_digest(str(x_orkestr_token), str(tok)):
                viewer_member_id = mid_
                break
    members_out = []
    for m in s.get("members", []):
        c = m.get("constraints") or {}
        submitted = bool(c)
        mid = m.get("id")
        include_detail = submitted and (viewer_is_host or mid == viewer_member_id)
        members_out.append({
            "id": mid,
            "name": m.get("name"),
            "avatar": m.get("avatar"),
            "submitted": submitted,
            "ready": mid in ready,
            # Budgets / dietary needs / notes are personal — token-gated (above).
            "constraints": {
                "available_days": c.get("available_days", []),
                "budget_max": c.get("budget_max"),
                "dietary": c.get("dietary", []),
            } if include_detail else None,
            "freeform": (m.get("freeform") or "") if include_detail else "",
        })

    plan = s.get("plan") if isinstance(s.get("plan"), dict) else None
    handshake = s.get("handshake") if isinstance(s.get("handshake"), dict) else {}
    mandate = (handshake.get("mandate") or {}) if isinstance(handshake, dict) else {}
    # Public payment_mode label only (e.g. "mock" / "x402" / "x402_real").
    # Never the vault token / order / signature material.
    payment_mode = mandate.get("payment_mode") if isinstance(mandate, dict) else None

    # Public venue verification summary — status/badge and public links only, so
    # phones can show the grounding badge without running the pipeline. No secrets.
    venue_verification = None
    plan_summary = None
    if plan:
        pv = plan.get("venue") if isinstance(plan.get("venue"), dict) else {}
        ver = pv.get("verification") if isinstance(pv, dict) else None
        if isinstance(ver, dict):
            venue_verification = {
                "name": pv.get("name"),
                "address": pv.get("address"),
                "verification_status": ver.get("verification_status"),
                "source_kind": ver.get("source_kind"),
                "badge": ver.get("badge"),
                # Price truth: "confirmed" (Google priceLevel) vs "approximate"
                # (verified venue, price estimated) so the UI can label it honestly.
                "price_confidence": ver.get("price_confidence"),
                "source_domain": ver.get("source_domain"),
                "source_url": ver.get("source_url"),
                "google_maps_uri": ver.get("google_maps_uri"),
                "google_place_id": ver.get("google_place_id"),
                "website_uri": ver.get("website_uri"),
                "rating": ver.get("rating"),
                "user_rating_count": ver.get("user_rating_count"),
            }
        # Compact, public plan summary so a phone (participant) can render the REAL
        # chosen plan without calling /api/plan (which would lazily run the
        # pipeline). Read-only projection of the already-computed plan — public
        # fields only (day/time/price/booking + the venue block already summarised
        # above). No vault token, order, signature, or any secret is included.
        plan_summary = {
            "title": plan.get("title"),
            "day": plan.get("day"),
            "time": plan.get("time"),
            "per_person": plan.get("per_person"),
            "total_cost": plan.get("total_cost"),
            "price_confidence": plan.get("price_confidence"),
            "why_reasons": plan.get("why_reasons") or [],
            "occasion": plan.get("occasion") or [],
            "booking_ref": plan.get("booking_ref"),
            "satisfies": plan.get("satisfies") or [],
            "venue": {
                "name": pv.get("name"),
                "address": pv.get("address"),
                "area": pv.get("area"),
                "tags": pv.get("tags") or [],
                "verification": venue_verification,
            } if isinstance(pv, dict) else None,
        }

    resp = {
        "session_id": s.get("session_id"),
        "name": s.get("name"),
        "live": bool(s.get("live")),
        "phase": s.get("phase"),
        "members": members_out,
        "ready": ready,
        "ready_count": sum(1 for m in members_out if m["ready"]),
        "submitted_count": sum(1 for m in members_out if m["submitted"]),
        "members_total": len(members_out),
        "plan_ready": bool(plan),
        "booking_ref": plan.get("booking_ref") if plan else None,
        "payment_mode": payment_mode,
        "booking_failed": bool(s.get("booking_failed")),
        # Public, user-safe failure reason so a phone can render the failure state
        # without a second round-trip (already exposed on /api/status). No secrets.
        "fail_reason": s.get("fail_reason"),
        "venue_status": s.get("venue_status"),
        "venue_verification": venue_verification,
        # Additive, data-derived budget nudge (real Places data only; never invented).
        "suggested_min_budget": s.get("suggested_min_budget"),
        "suggested_reason": s.get("suggested_reason"),
        "plan": plan_summary,
        # Additive quorum state (P2.2) so the waiting room can show honest progress
        # ("3 of 5 replied") and label a quorum-based plan as a draft. No secrets.
        **S.quorum_report(s),
    }
    # Public fresh x402 receipt auto-fired at booking time (core.session
    # _maybe_fire_x402_receipt). Additive: only present when a real receipt with a
    # tx hash exists. Public chain fields only — no secrets. Reading NEVER spends.
    receipt = s.get("x402_receipt")
    if isinstance(receipt, dict) and receipt.get("txHash"):
        resp["x402_receipt"] = receipt
    return resp


# ---- Real venue search: generate a plan from live Google Places results ----
# Separate path that bypasses the seeded convener. Searches Google Places for a
# real venue matching the group's location/cuisine, sets session["plan"], and
# returns it. Refuses ORK-001 (400, frozen); unknown session -> 404; falls back
# to data/venues.json if the API key is absent/invalid so it never 500s on that.
@app.post("/api/session/plan/{sid}")
def session_plan(sid: str,
                 x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    s = _require_session(sid)
    _require_host(s, sid, x_orkestr_token)  # presenter-only compute trigger
    return S.generate_real_plan(sid)


@app.get("/api/plan/{sid}")
def plan(sid: str):
    """PURE READ (P1.1): never computes. 409 not_ready if the plan isn't ready.
    /api/status is the compute gate; ORK-001 is precomputed so it returns 200."""
    s = _require_session(sid)
    if not s.get("plan"):
        return _not_ready(s)
    return s["plan"]


@app.get("/api/negotiation/{sid}")
def negotiation(sid: str):
    """Negotiation transcript. PURE READ (P1.1): never computes; 409 not_ready
    until the plan is computed via /api/status."""
    s = _require_session(sid)
    if not s.get("negotiation"):
        return _not_ready(s)
    return s["negotiation"]


@app.post("/api/approve/{sid}")
def approve(sid: str, body: dict = Body(...),
            x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    s = _require_session(sid)
    _require_field(body, "person_id")
    member_ids = {m["id"] for m in (s["members"] or _load("personas.json"))}
    if body["person_id"] not in member_ids:
        raise HTTPException(status_code=400, detail="unknown person_id")
    _require_actor(s, sid, x_orkestr_token, body["person_id"])
    S.approve_plan(sid, body["person_id"])
    return {"ok": True}


@app.get("/api/handshake/{sid}")
def handshake(sid: str):
    """Full request -> counter -> mandate, so /confirmed can show the exchange (Act 2).
    PURE READ (P1.1): never computes; 409 not_ready until computed via /api/status."""
    s = _require_session(sid)
    if not s.get("handshake"):
        return _not_ready(s)
    return s["handshake"]


# ---- Phase 2: at the meal ----
@app.get("/api/expenses/{sid}")
def expenses(sid: str):
    s = _require_session(sid)
    return s["expenses"]


@app.post("/api/expense/{sid}")
def expense(sid: str, body: dict = Body(...),
            x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    s = _require_session(sid)
    _require_field(body, "paid_by")
    _require_field(body, "amount")
    if not isinstance(body["amount"], (int, float)) or isinstance(body["amount"], bool):
        raise HTTPException(status_code=400, detail="amount must be numeric")
    if body["amount"] <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    member_ids = {m["id"] for m in (s["members"] or _load("personas.json"))}
    if body["paid_by"] not in member_ids:
        raise HTTPException(status_code=400, detail="paid_by is not a current member")
    # You can only log an expense as YOURSELF (or as the host running the room).
    _require_actor(s, sid, x_orkestr_token, body["paid_by"])
    S.add_expense(sid, body)
    return {"ok": True}


# ---- Phase 3: after the meal ----
@app.get("/api/settlement/{sid}")
def settlement(sid: str):
    s = _require_session(sid)
    if not s["settlement"]:
        S.recompute_settlement(sid)
    return s["settlement"]


@app.post("/api/settle/{sid}")
def settle_ep(sid: str, body: dict = Body(default={}),
              x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    s = _require_session(sid)
    has_fronted = "fronted" in body
    has_person_id = "person_id" in body
    if not has_fronted and not has_person_id:
        raise HTTPException(status_code=400, detail="person_id or fronted required")
    member_ids = {m["id"] for m in (s["members"] or _load("personas.json"))}
    if has_fronted:
        if not isinstance(body["fronted"], dict):
            raise HTTPException(status_code=400, detail="fronted must be a JSON object")
        bad_keys = [k for k in body["fronted"] if k not in member_ids]
        if bad_keys:
            raise HTTPException(status_code=400, detail="fronted contains unknown member id")
        bad_vals = [v for v in body["fronted"].values()
                    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0]
        if bad_vals:
            raise HTTPException(status_code=400, detail="fronted values must be numeric >= 0")
    if has_person_id and body["person_id"] not in member_ids:
        raise HTTPException(status_code=400, detail="unknown person_id")
    # Auth: overriding the group's fronted map is a host action; marking a
    # transfer paid requires being THAT member (or the host).
    if has_fronted:
        _require_host(s, sid, x_orkestr_token)
    if has_person_id:
        _require_actor(s, sid, x_orkestr_token, body["person_id"])
    # All validation passed — apply changes
    if has_fronted:
        S.set_fronted(sid, body["fronted"])        # S11 confirm fronted
    if has_person_id:
        S.mark_transfer_paid(sid, body["person_id"])  # S13 mark transfer as paid
    return {"ok": True}


@app.get("/api/split/{sid}")
def split(sid: str, mode: str = "itemized"):
    """Smart itemized meal split for THIS session (P1.3 — session-linked).

    Orkestr pays the merchant ONCE over x402; this is the internal settlement —
    each friend's share from what they actually ordered, shared dishes split only
    among the friends who joined them, service charge + GST allocated fairly.
    Computed from the room's own members + orders (a session-linked draft is
    initialised on first read if none exist), NOT global canned data. The response
    carries an additive `split_source` so the UI can label it honestly. Never
    triggers a payment. mode = "itemized" (default) or "equal".
    """
    _require_session(sid)
    if mode not in ("itemized", "equal"):
        raise HTTPException(status_code=400, detail="mode must be 'itemized' or 'equal'")
    split_data, _ = S.get_session_split(sid, mode)
    return split_data


@app.post("/api/split/{sid}/orders")
def split_orders(sid: str, body: dict = Body(default={}), mode: str = "itemized",
                 x_orkestr_token: str = Header(default=None, alias="X-Orkestr-Token")):
    """Replace THIS session's orders (presenter edit / captured order) and return the
    recomputed split. HOST-ONLY (rewrites the whole group's order set). Body:
    {"items": [{id,name,price,quantity,assigned_to,shared_by,category}]}. Item
    members are validated against the room; the split then reports
    split_source="backend_live_orders". Pure math — never triggers a payment."""
    s = _require_session(sid)
    _require_host(s, sid, x_orkestr_token)
    if mode not in ("itemized", "equal"):
        raise HTTPException(status_code=400, detail="mode must be 'itemized' or 'equal'")
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items (list) required")
    split_data, _ = S.set_session_orders(sid, items, mode)
    return split_data


@app.post("/api/discovery/venues")
def discovery_venues(body: dict = Body(default={})):
    """Exa venue discovery preview — never mutates sessions, never affects ORK-001."""
    from agents import discovery as D

    raw_limit = body.get("limit", 5)
    if raw_limit is not None:
        if not isinstance(raw_limit, (int, float)) or isinstance(raw_limit, bool):
            raise HTTPException(status_code=400, detail="limit must be numeric")
        if raw_limit <= 0:
            raise HTTPException(status_code=400, detail="limit must be > 0")

    query = body.get("query") or None
    constraints = body.get("constraints") or None
    limit = min(int(raw_limit), 10)

    if not query and not constraints:
        query = "Singapore group dinner venue restaurant halal vegetarian options"

    return D.discover_venues(query=query, constraints=constraints, limit=limit)


@app.post("/api/dev/reseed")
def dev_reseed(x_reseed_secret: str = Header(default=None, alias="X-Orkestr-Reseed-Secret")):
    """Restore ORK-001 to its frozen seeded values. Gated exactly like the x402
    demo endpoints so it is not a public unauthenticated reset in production:
      1. ENABLE_DEV_RESEED must be exactly "true"       -> else 404 (hidden)
      2. X-Orkestr-Reseed-Secret must equal DEV_RESEED_SECRET (constant-time) -> else 403
    The secret is never logged or echoed.
    """
    import hmac

    if os.environ.get("ENABLE_DEV_RESEED", "").strip() != "true":
        raise HTTPException(status_code=404, detail="Not Found")

    expected = os.environ.get("DEV_RESEED_SECRET", "").strip()
    provided = (x_reseed_secret or "").strip()
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="forbidden")

    S.delete_session("ORK-001")  # drop from cache + SQLite, then re-seed fresh
    S.seed_demo()
    return {"ok": True}
