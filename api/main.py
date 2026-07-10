"""Orkestr API — FastAPI. Owner: Lucas.

Implements the frontend contract in ORKESTR_FRONTEND.md §4/§6, backed by an
in-memory session store (core/session.py). Seeded session ORK-001 is populated
on startup so every screen has data immediately.

Run from the repo root:
    uvicorn api.main:app --reload --port 8000
"""
import asyncio
import json
import os

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core import session as S

app = FastAPI(title="Orkestr API", version="0.2.0")

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


def _require_field(body: dict, field: str) -> None:
    if field not in body or body[field] is None:
        raise HTTPException(status_code=400, detail=f"{field} required")


@app.on_event("startup")
def _startup():
    S.seed_demo()


# Also seed at import time so ORK-001 exists however the app is started
# (uvicorn, TestClient, etc.). seed_demo() is idempotent.
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


def _demo_receipt_mock_fallback(mock_module, reason: str) -> dict:
    """Build a mock demo-receipt body when the live run times out or errors.

    Uses the FROZEN mock stamp() (payments/x402.py) for a placeholder hash and
    marks the body payment_mode="mock" + fallback_reason so a judge (and the
    frontend) can see it was NOT a real on-chain settlement. No Basescan link is
    claimed for the mock hash — real_tx_hash/explorer_url are null on this path.
    Mirrors the fresh-receipt shape so the frontend can render it unchanged.
    """
    from payments import x402_demo_receipt as XDR

    mock_hash = mock_module.stamp()
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
        "txHash": mock_hash,
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
    from payments import x402 as MOCK_X402
    from payments import x402_demo_receipt as XDR

    confirm = payload.get("confirm") if isinstance(payload, dict) else None

    # Run the (synchronous, never-raising) gated real-payment orchestrator in a
    # worker thread with an 8s ceiling so a slow/hung facilitator can never make
    # this request hang. On timeout or any unexpected error we DO NOT 502 — we
    # fall back to the frozen mock stamp() and flag payment_mode="mock" so the
    # endpoint always answers in <~8s. The upstream payment modules are untouched.
    loop = asyncio.get_running_loop()
    try:
        status_code, body_out = await asyncio.wait_for(
            loop.run_in_executor(None, XDR.run_demo_receipt, x_demo_secret, confirm),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        return JSONResponse(status_code=200,
                            content=_demo_receipt_mock_fallback(MOCK_X402, "timeout"))
    except Exception:  # defensive: run_demo_receipt is documented never to raise
        return JSONResponse(status_code=200,
                            content=_demo_receipt_mock_fallback(MOCK_X402, "error"))

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

    tx = receipt.get("txHash") if isinstance(receipt, dict) else None
    explorer = receipt.get("blockExplorerUrl") if isinstance(receipt, dict) else None
    frontend = os.environ.get("FRONTEND_URL", "").strip()
    settle_href = esc((frontend.rstrip("/") + "/settle") if frontend else "/settle")
    _btn = ("display:block;box-sizing:border-box;min-height:44px;margin-top:10px;"
            "padding:14px 16px;text-align:center;text-decoration:none;font-weight:600;"
            "border-radius:12px;color:#fff;")
    if tx:
        tx_s = str(tx)
        tx_disp = f"{tx_s[:10]}…{tx_s[-6:]}" if len(tx_s) > 16 else tx_s
        explorer_esc = esc(explorer or ("https://sepolia.basescan.org/tx/" + tx_s))
        proof_html = (
            f'<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
            f'font-size:15px;color:#111;word-break:break-all;padding:12px 14px;'
            f'background:#f6f7f9;border-radius:10px;">{esc(tx_disp)}</div>'
            f'<a href="{explorer_esc}" target="_blank" rel="noopener" '
            f'style="{_btn}background:#0052FF;margin-top:12px;">View on Basescan ↗</a>'
            f'<a href="{settle_href}" target="_blank" rel="noopener" '
            f'style="{_btn}background:#888;">View live settlement →</a>'
        )
    else:
        proof_html = (
            '<div style="padding:14px 16px;background:#fff7ed;color:#9a3412;'
            'border-radius:10px;font-size:15px;">payment pending — receipt not yet fired</div>'
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
        <span style="font-size:12px;text-transform:uppercase;letter-spacing:0.04em;color:#888;">Booking reference</span>
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
    return {"session_id": s["session_id"],
            "invite_url": f"https://orkestr.app/join/{s['session_id']}"}


# ---- QR join: a scannable invite that turns a judge's phone into a live node ----
# No dependency/library: the QR image is rendered by api.qrserver.com. The
# invite_url matches the format returned by /api/session/start above.
def _invite_and_qr(sid: str):
    """Return (invite_url, qr_url) for a session id. qr_url is a ready-to-embed
    api.qrserver.com PNG encoding the invite_url (URL-encoded into the data param)."""
    from urllib.parse import quote

    invite_url = f"{os.environ.get('FRONTEND_URL', 'https://orkestr-luc.vercel.app')}/join/{sid}"
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
    return {
        "session_id": s["session_id"],
        "invite_url": f"https://orkestr.app/join/{s['session_id']}",
        "members": [{"id": m["id"], "name": m["name"], "avatar": m.get("avatar")}
                    for m in s["members"]],
    }


@app.post("/api/session/clone/{sid}")
def session_clone(sid: str):
    """Clone a settled session's crew into a fresh session for the next outing.

    "Friday coming up? Plan another dinner with this crew." Copies only the member
    id + name list (constraints come from group memory when members re-submit).
    Unknown sid -> 404; ORK-001 -> 400 (frozen demo, never cloned). Returns
    {session_id, invite_url} for the freshly created session.
    """
    s = S.clone_session(sid)
    invite_url, _ = _invite_and_qr(s["session_id"])
    return {"session_id": s["session_id"], "invite_url": invite_url}


@app.post("/api/ready/{sid}")
def ready(sid: str, body: dict = Body(...)):
    s = _require_session(sid)
    _require_field(body, "person_id")
    S.mark_ready(sid, body["person_id"])
    return {"ready": s.get("ready", []),
            "members_total": len(s["members"]),
            "all_ready": S.all_ready(s)}


@app.post("/api/constraints")
def constraints(person: dict = Body(...)):
    _require_field(person, "session_id")
    _require_session(person["session_id"])
    _require_field(person, "id")
    S.add_constraints(person["session_id"], person)
    return {"ok": True}


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
    if s["phase"] == "negotiating":
        # Live sessions wait until all 5 members press find-our-plan; everyone
        # else (incl. ORK-001) keeps the original lazy-compute behaviour.
        if s.get("live") and not S.all_ready(s):
            return {"phase": s["phase"],
                    "ready": s.get("ready", []),
                    "members_total": len(s["members"])}
        S.compute_plan(sid)  # lazily produce the plan -> plan_ready
    if s["phase"] == "failed":
        return {"phase": "failed", "booking_failed": True,
                "fail_reason": s.get("fail_reason"),
                "venue_status": s.get("venue_status")}
    return {"phase": s["phase"], "venue_status": s.get("venue_status")}


@app.get("/api/session/{sid}/public")
def session_public(sid: str):
    """Read-only public snapshot of a room — safe for any device to poll.

    Powers cross-device sync for the presenter command center and the mobile
    participant views (who has joined / submitted, readiness, phase). This NEVER
    runs the planning/booking pipeline (no compute_plan), so polling it can never
    trigger a real x402 payment. Exposes only public demo state: member ids,
    display names, avatars, each member's own stated demo preferences, readiness,
    phase, and a public payment_mode *label* if a plan already exists. No private
    keys, no vault tokens, no secrets of any kind are returned.
    """
    s = _require_session(sid)
    ready = list(s.get("ready", []))
    members_out = []
    for m in s.get("members", []):
        c = m.get("constraints") or {}
        submitted = bool(c)
        mid = m.get("id")
        members_out.append({
            "id": mid,
            "name": m.get("name"),
            "avatar": m.get("avatar"),
            "submitted": submitted,
            "ready": mid in ready,
            # The member's own constraints are demo preferences they entered — not
            # secrets. Summarised to the public-facing fields only.
            "constraints": {
                "available_days": c.get("available_days", []),
                "budget_max": c.get("budget_max"),
                "dietary": c.get("dietary", []),
            } if submitted else None,
            "freeform": (m.get("freeform") or "") if submitted else "",
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
        "venue_status": s.get("venue_status"),
        "venue_verification": venue_verification,
        "plan": plan_summary,
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
def session_plan(sid: str):
    return S.generate_real_plan(sid)


@app.get("/api/plan/{sid}")
def plan(sid: str):
    s = _require_session(sid)
    if not s["plan"]:
        S.compute_plan(sid)
    return s["plan"]


@app.get("/api/negotiation/{sid}")
def negotiation(sid: str):
    """NEW — feeds the negotiation RING on /waiting (Act 1 money shot)."""
    s = _require_session(sid)
    if not s["negotiation"]:
        S.compute_plan(sid)
    return s["negotiation"]


@app.post("/api/approve/{sid}")
def approve(sid: str, body: dict = Body(...)):
    s = _require_session(sid)
    _require_field(body, "person_id")
    member_ids = {m["id"] for m in (s["members"] or _load("personas.json"))}
    if body["person_id"] not in member_ids:
        raise HTTPException(status_code=400, detail="unknown person_id")
    S.approve_plan(sid, body["person_id"])
    return {"ok": True}


@app.get("/api/handshake/{sid}")
def handshake(sid: str):
    """Full request -> counter -> mandate, so /confirmed can show the exchange (Act 2)."""
    s = _require_session(sid)
    if not s["handshake"]:
        S.compute_plan(sid)
    return s["handshake"]


# ---- Phase 2: at the meal ----
@app.get("/api/expenses/{sid}")
def expenses(sid: str):
    s = _require_session(sid)
    return s["expenses"]


@app.post("/api/expense/{sid}")
def expense(sid: str, body: dict = Body(...)):
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
def settle_ep(sid: str, body: dict = Body(default={})):
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
    # All validation passed — apply changes
    if has_fronted:
        S.set_fronted(sid, body["fronted"])        # S11 confirm fronted
    if has_person_id:
        S.approve_transfer(sid, body["person_id"]) # S13 approve transfer
    return {"ok": True}


@app.get("/api/split/{sid}")
def split(sid: str, mode: str = "itemized"):
    """Smart itemized meal split for the session (demo-state).

    Orkestr pays the merchant ONCE over x402; this is the internal settlement —
    each friend's share from what they actually ordered, shared dishes split only
    among the friends who joined them, service charge + GST allocated fairly.
    Never triggers a payment. mode = "itemized" (default) or "equal".
    """
    _require_session(sid)
    if mode not in ("itemized", "equal"):
        raise HTTPException(status_code=400, detail="mode must be 'itemized' or 'equal'")
    from core.meal_split import demo_meal_split

    return demo_meal_split(mode=mode)


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
