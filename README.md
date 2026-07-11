# Orkestr

**Google finds places. Orkestr gets groups to agree.**

Orkestr is a mobile-first, AI group-dining planner. A host opens a room; friends join by
link/QR as **guests — no account, no wallet, no app install**; everyone submits constraints;
agents negotiate a plan; the venue is verified against Google Places / Exa; a **Merchant
Adapter** prepares a real booking handoff; a **cent-exact itemized split** is computed; and a
**presenter-confirmed x402 USDC testnet payment proof** can be shown. Friends never touch a
wallet — only the Orkestr Agent Wallet → Merchant Wallet leg uses x402.

Built for 'Sup **Build2026**.

---

## 1. What is Orkestr

Split apps start *after* the meal. Orkestr starts *before* the decision and carries through
to venue, handoff, split, and payment proof — in one shared room:

- **Group agreement** — each friend's constraints (days, budget, dietary, vibe) feed an agent
  negotiation that produces a plan the group can accept.
- **Verified venue evidence** — Exa web discovery + Google Places verification, with a strict
  mode that **fails closed** rather than showing an unverifiable place as real.
- **Merchant Adapter** — most restaurants don't run agents yet, so Orkestr verifies the venue
  and prepares a booking handoff (Google Maps / website / call) **today**, with a clearly
  labelled **sandbox merchant agent** showing the future agent-commerce rail.
- **Fair split** — an itemized, cent-exact split (shared dishes split only among the friends
  who joined them; service charge + GST allocated proportionally).
- **Payment proof** — the Orkestr Agent Wallet pays the merchant once over x402 on Base
  Sepolia; a presenter can run a fresh on-chain proof on demand. Friends settle internally.

## 2. Product flow

1. **Open a room** (`/`) → a live session is created; the presenter URL is shareable.
2. **Invite friends** — each gets a personal link/QR; they join as guests and submit
   constraints in under 30 seconds.
3. **Quorum planning** — the presenter can **wait for everyone**, or **start a draft** once a
   quorum (default 3 of 5) has replied. A draft is labelled "Draft based on N of M replies"; if
   more friends reply, a "More friends replied → Update draft" banner folds them in.
4. **Reveal** — the plan card shows the verified venue, the Merchant Adapter, the itemized
   split, and the x402 payment proof. If the group can't be satisfied (no verified venue /
   no agreement / mandate integrity fail), a polished **failure screen** appears — never a
   fake plan in live mode.
5. **Settle** — friends see their share and settle with their usual method (PayNow / Venmo /
   "mark as paid"). No wallet, no crypto for participants.

## 3. Architecture

```
src/  ──────────────►  Next.js (App Router) frontend
  │                    one route (/) with query-param views; talks to the backend
  │                    through a same-origin rewrite: /orkestr-api/* → NEXT_PUBLIC_API_URL
  ▼
api/main.py  ───────►  FastAPI backend (the REST contract)
  │
  ▼
core/session.py  ───►  session state machine (negotiation → plan → handshake → settle)
core/db.py       ───►  SQLite-backed session store + per-process in-memory cache
agents/**, ai/**  ──►  negotiation, venue discovery (Exa) + verification (Google Places)
payments/**      ───►  AP2 mandate mock + x402 (mock + real Base-Sepolia testnet paths)
```

Seeded demo session **ORK-001** is populated on boot so every screen has data immediately.
Deployment: **backend on Railway**, **frontend on Vercel**.

## 4. Frontend routes

The live app is a single route with query-param views:

| URL | View |
|---|---|
| `/` | Group Hub — open or pick a room |
| `/?room=ORK-XXXX` | Presenter command center (shareable, reload-safe) |
| `/?room=ORK-XXXX&member=P-001` | That friend's phone-first participant view |

Old pre-refactor screens (`/create-event`, `/event/[id]`, `/negotiation/[id]`,
`/preferences/[id]`, `/settled`, `/split-detail`) and the design-system gallery
(`/buttons`, `/cards`, …) are **redirected to `/`** in production (see `next.config.ts`). The
gallery is reachable locally only with `NEXT_PUBLIC_SHOW_DEV_GALLERY=true` at build time.

## 5. Backend API overview

Read-only reads never run the pipeline; `/api/status` is the single lazy compute gate.

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/session/live` | Create a live room (5 member shells) → `{session_id, invite_url, members}` |
| POST | `/api/session/start` | Create a bare session → `{session_id, invite_url}` |
| GET | `/api/session/{sid}/invite` · `/qr` | Invite URL + QR (via `_invite_and_qr`) |
| POST | `/api/constraints` · `/api/ready/{sid}` | Submit a member's constraints / mark ready |
| GET | `/api/status/{sid}` | Phase + quorum report; **the only lazy compute gate** |
| POST | `/api/session/{sid}/draft` | Presenter quorum draft / re-draft (explicit; never a GET) |
| GET | `/api/session/{sid}/public` | Read-only cross-device snapshot (never computes) |
| GET | `/api/plan/{sid}` · `/api/negotiation/{sid}` · `/api/handshake/{sid}` | **Pure reads** — 409 `not_ready` until computed |
| GET | `/api/split/{sid}` · POST `/api/split/{sid}/orders` | Session-linked itemized split + order edits |
| GET | `/api/settlement/{sid}` · POST `/api/settle/{sid}` | Net balances / minimal transfers |
| GET/POST | `/api/x402/demo-receipt/{status,latest,run}` | Presenter fresh x402 proof (gated) |
| POST | `/api/dev/reseed` | Restore ORK-001 (gated: flag + secret header) |

Phases: `negotiating → plan_ready → booking → confirmed`, with `failed` as an out-of-band
status. Response keys are additive-only.

## 6. Environment variables

> Verified against the code (`api/main.py`, `payments/x402_demo_receipt.py`,
> `payments/x402_protocol.py`, `agents/**`, `core/**`, `src/lib/*`). All feature flags default
> OFF/safe; there is **no required production env var** beyond the API URL.

### Frontend (Vercel) — build-time `NEXT_PUBLIC_*` only (never secrets)

| Var | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend base for the `/orkestr-api/*` rewrite |
| `NEXT_PUBLIC_APP_MODE` | `demo` | `live` = block sample fallbacks / block on backend-down |
| `NEXT_PUBLIC_ENABLE_LIVE_PIPELINE` | unset (off) | `true` = drive the real backend planning pipeline |
| `NEXT_PUBLIC_SHOW_PRESENTER_TOOLS` | unset | `true` = show presenter tools in live mode (always on in demo) |
| `NEXT_PUBLIC_SHOW_DEV_GALLERY` | unset | `true` (local build only) = keep the design gallery reachable |

Demo-day frontend:
```
NEXT_PUBLIC_API_URL=https://orkestr-luc.up.railway.app
NEXT_PUBLIC_ENABLE_LIVE_PIPELINE=true
NEXT_PUBLIC_APP_MODE=live
NEXT_PUBLIC_SHOW_PRESENTER_TOOLS=true
```

### Backend (Railway)

| Var | Default | Purpose |
|---|---|---|
| `FRONTEND_URL` | `https://orkestr-luc.vercel.app` | Invite-link origin (`/?room=…`) |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |
| `USE_EXA` / `USE_EXA_IN_PLANNING` | off | Exa web discovery of venues |
| `EXA_API_KEY` | — | Exa key (server-side only) |
| `USE_GOOGLE_PLACES` | off | Google Places verification |
| `GOOGLE_PLACES_API_KEY` | — | **Server-side only. Never `NEXT_PUBLIC_*`, never logged.** |
| `USE_REAL_SEARCH` | off | Budget-aware real venue search fallback |
| `STRICT_REAL_VENUES` | `false` | Fail closed if nothing is grounded (recommended for the live demo) |
| `ALLOW_SAMPLE_VENUE_FALLBACK` | `true` | Labeled sample fallback (ignored when strict) |
| `USE_REAL_X402` | `false` | Enable the faithful x402 booking handshake |
| `REQUIRE_REAL_X402` | `false` | Treat a failed x402 as a controlled failure (no fallback) |
| `ENABLE_LIVE_REAL_X402` | unset | **Keep unset/false.** Gate for auto real USDC in the live flow |
| `ENABLE_X402_PAID_SMOKE` | unset | Protected real $0.001 paid smoke (Railway-only) |
| `ENABLE_X402_DEMO_RECEIPT` | unset | Presenter fresh-proof endpoint (exact `"true"` to enable) |
| `X402_DEMO_RECEIPT_SECRET` | — | **Server-side only.** Sent in `X-Orkestr-Demo-Secret` |
| `X402_DEMO_RECEIPT_AMOUNT` | `1000` | Base units — must be `1000` (= 0.001 USDC) or it fails safe |
| `X402_DEMO_RECEIPT_COOLDOWN_SECONDS` | `60` | Cooldown between fresh proofs |
| `ENABLE_DEV_RESEED` | `false` | Enable `POST /api/dev/reseed` (exact `"true"`) |
| `DEV_RESEED_SECRET` | — | Sent in `X-Orkestr-Reseed-Secret`; compared constant-time |
| `WEB_CONCURRENCY` | `1` | **Must be 1** — see §13 |

See `.env.example` for the full template (placeholders only — never commit real values).

## 7. Local development

```bash
# Backend (repo root)
python -m venv .venv && . .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000              # ORK-001 seeds on boot

# Frontend
npm install
npm run dev                                            # http://localhost:3000
```

The frontend calls the backend through a same-origin rewrite (`/orkestr-api/*`), so set
`NEXT_PUBLIC_API_URL=http://localhost:8000` locally (default already points there).

## 8. Demo vs live mode

- **Demo mode** (`NEXT_PUBLIC_APP_MODE=demo`, default): sample fallbacks are allowed so the app
  always renders a polished end state, even offline. Presenter tools ("Demo Cast") are always
  available.
- **Live mode** (`NEXT_PUBLIC_APP_MODE=live`): behaves like a real product — no sample venue as
  a result, no stale receipt as the current proof; a backend outage blocks with a real error;
  a failed room shows the failure screen. Presenter tools are hidden unless
  `NEXT_PUBLIC_SHOW_PRESENTER_TOOLS=true`.

## 9. x402 safety notes

- **No real money by default.** `ENABLE_LIVE_REAL_X402` stays unset/false; the normal live flow
  never spends USDC. Participants can never trigger a payment.
- The **presenter-confirmed fresh proof** (`/api/x402/demo-receipt/run`) is the only in-app path
  that can move testnet USDC, and only after five gates (feature flag + secret header + exact
  confirm phrase + amount == 1000 + cooldown/in-flight).
- Secrets (`GOOGLE_PLACES_API_KEY`, `X402_DEMO_RECEIPT_SECRET`, `EVM_PRIVATE_KEY`,
  `DEV_RESEED_SECRET`) are **server-side only** and must never be `NEXT_PUBLIC_*`.
- **After the demo:** set `ENABLE_X402_DEMO_RECEIPT` and `ENABLE_DEV_RESEED` back to off.

## 10. Google Places / Exa setup

- **Exa** (discovery): set `EXA_API_KEY`, `USE_EXA=true`, `USE_EXA_IN_PLANNING=true`.
- **Google Places** (verification): set `USE_GOOGLE_PLACES=true` and `GOOGLE_PLACES_API_KEY`
  (server-side). Venue photos are proxied through `/api/venue/photo?ref=…` so the key never
  reaches the browser.
- For a strict live demo: `STRICT_REAL_VENUES=true`, `ALLOW_SAMPLE_VENUE_FALLBACK=false` — the
  planner then fails closed instead of showing an unverifiable venue.

## 11. Testing

Stdlib + FastAPI `TestClient` smokes (no external services, no real payments). Start a local
backend with the reseed gate enabled for `smoke.py`:

```powershell
$env:ENABLE_DEV_RESEED="true"; $env:DEV_RESEED_SECRET="local-test-only"
uvicorn api.main:app --port 8000
```

```bash
npm run lint && npm run build
python -m compileall api core agents ai payments scripts

$env:BASE_URL="http://localhost:8000"; python scripts/smoke.py
python scripts/settlement_rounding_smoke.py     # cent-exact settlement
python scripts/plan_lock_smoke.py               # per-session compute lock + one-shot x402
python scripts/tamper_enforcement_smoke.py      # AP2 mandate verify enforcement
python scripts/x402_demo_receipt_guard_smoke.py # fresh-proof gates + tripwires
python scripts/split_calculation_smoke.py       # split engine
python scripts/pure_read_gets_smoke.py          # GET plan/negotiation/handshake are pure reads
python scripts/live_failure_state_smoke.py      # failure state exposes reason, no fake plan
python scripts/split_from_session_smoke.py      # session-linked split + reassignment
python scripts/invite_link_smoke.py             # invite URL consistency
python scripts/quorum_planning_smoke.py         # quorum draft + stale re-draft (payment-neutral)
```

Do **not** run paid-smoke or real x402. `scripts/exa_smoke.py` and
`scripts/venue_grounding_smoke.py` are optional (need network/keys).

## 12. Deployment

- **Backend → Railway.** `Procfile`: `web: uvicorn api.main:app --host 0.0.0.0 --port $PORT`.
  Set `WEB_CONCURRENCY=1` (single worker — see §13). Set `FRONTEND_URL` + `CORS_ORIGINS` to the
  Vercel domain.
- **Frontend → Vercel.** Set the `NEXT_PUBLIC_*` vars from §6. Never put a secret in a
  `NEXT_PUBLIC_*` var.

## 13. Known limitations

- **Single worker only.** The session store uses a per-process in-memory cache in front of a
  SQLite file, plus in-process locks and cooldowns. It must run as **one worker / one replica**
  (`WEB_CONCURRENCY=1`); multiple workers would split-brain the cache and locks.
- Quorum draft settlement splits the booking expense among the drafted subset; late joiners are
  folded in via re-draft.
- Merchant "agent acceptance" is a **sandbox simulation**, always labelled as such — real
  restaurants are handled via the booking handoff.

## 14. Future production architecture (not built)

- **Postgres** for durable sessions (replacing the SQLite/in-memory cache).
- **Redis** for cross-worker locks, cooldowns, and idempotency (replacing in-process locks) —
  enabling horizontal scale beyond one worker.
- **Durable receipt storage** and an async **queue** for background work (receipts, discovery).
- **Real booking-partner integrations** to upgrade the Merchant Adapter's sandbox agent to live
  agent-commerce rails as they're adopted.

---

## The ORK-001 frozen demo

`ORK-001` is a contractually frozen seed used across the demo and tests: **Seoul Garden**,
`per_person = 80`, **3 transfers netting to zero**, a 10-message negotiation, a **co-signed +
verified** AP2 handshake, and 3 seeded expenses. `POST /api/dev/reseed` (when enabled) restores
exactly these values.
