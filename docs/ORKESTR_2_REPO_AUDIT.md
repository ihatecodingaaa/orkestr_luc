# ORKESTR 2.0 — Repository Audit

> **HISTORICAL DOCUMENT - SUPERSEDED DIRECTION.**
> Historical repository audit dated 10 Aug 2026. Its Concierge Alpha product
> direction is not the current Alibaba × Atlas hackathon build plan. The
> codebase findings remain useful historical evidence.
>
> The current hackathon work is Orkestr Travel, in the separate `orkestr-travel`
> repository. See its `docs/STARTUP_BOUNDARY.md` for how the startup and the
> hackathon vertical relate.


**Stage:** Audit only. No application code was modified, no hosting changed, no environment variables altered, nothing deleted.
**Audited commit:** `9e78f63` (branch `main`)
**Date of audit:** 10 August 2026
**Method:** direct inspection of the working tree. Where a claim could not be established from the files, it is marked `UNKNOWN` rather than inferred.

---

## 1. Executive summary

The repository is a **single repo containing two separate runtimes** that talk over HTTP: a Next.js 16 frontend (`src/`, 54 files / 8,001 lines TS/TSX) deployed to Vercel, and a FastAPI backend (`agents/ ai/ api/ core/ payments/`, 8,474 lines Python) deployed to Railway via `Procfile`. A further 5,508 lines of Python live in `scripts/`. There is no monorepo tooling; the two simply coexist.

Five findings matter more than the rest.

**1. The frontend contains no server-side code at all.** There is no `src/app/api/` directory, no `route.ts`, and no `"use server"` anywhere. `src/app/page.tsx` is `"use client"`. Every piece of server logic in Orkestr today is Python. This means Concierge Alpha's server layer is new code no matter which host is chosen — there is nothing to "move".

**2. The existing backend genuinely requires a single long-running process.** This is not an assumption; it is a documented design constraint (`README.md` §13) implemented in code: a module-level cache in `core/db.py` that returns session dicts *by reference* so callers can mutate in place, one shared `sqlite3` connection guarded by one `threading.Lock`, process-stable per-session compute locks in `core/session.py`, two daemon-thread spawners, and a SQLite file on local disk.

**3. Concierge Alpha does not need that backend.** The Alpha surface — create plan, join by link, submit and correct a constraint, read a result page, founder admin — is request/response CRUD with no pipeline, no background work, and no cross-request in-memory state. None of the constraints in finding 2 apply to it.

**4. x402 is not isolated in `payments/`.** 49 files reference x402, Base Sepolia, USDC or chain concepts, including `core/session.py`, `core/db.py`, `core/meal_split.py`, `api/main.py`, and 11 of the 25 `src/components/live/` components. Archiving x402 is not a directory move. Anything that reuses the existing session pipeline inherits the coupling.

**5. Two existing subsystems are real assets and one is a real gap.** The token-gated constraint visibility in `GET /api/session/{sid}/public` is a working implementation of Master Plan §11's private-constraint tier. The venue grounding schema in `agents/venue_grounding.py` is a working provenance discipline. The constraint model itself is a flat dict with no hard/soft split, no ownership, no confirmation state and no provenance — it needs redesign, exactly as Master Plan §54 predicted.

**Hosting recommendation: OPTION A (Vercel + Neon) for Concierge Alpha.** Not by migrating the Python service — by not needing it. Evidence in §14.

---

## 2. Current architecture

```
Browser
  │
  ├── Next.js 16 (Vercel)  ── src/
  │     one production route: "/"
  │     "/?room=<sid>"            presenter view
  │     "/?room=<sid>&member=<id>" participant view
  │     polls every 2500 ms
  │
  └──> rewrite /orkestr-api/* → NEXT_PUBLIC_API_URL
             │
             ▼
       FastAPI (Railway)  ── api/main.py
             │
             ├── core/       session lifecycle, split, settlement, audit, usage
             ├── agents/     convener, discovery, google_places, venue_grounding, persona
             ├── ai/         plan_gen, venue_ranker, venue_search
             ├── payments/   x402_protocol, x402_demo_receipt, mandate, acp_*
             │
             └── sessions.db (SQLite, local disk, single process)
```

External services reached from the backend: Google Places, Exa, OpenAI, Stripe, Base Sepolia RPC. The frontend additionally embeds QR images from `api.qrserver.com` (see §15, risk 3).

**Line counts (excluding `node_modules`, `.next`):** 55 Python files / 13,982 lines total — 8,474 in application packages (`agents/ ai/ api/ core/ payments/ tests/`) and 5,508 in `scripts/`. 54 TS/TSX files under `src/` / 8,001 lines.

**Git state at audit time.** Branch `main`, remote `origin` → `github.com/ihatecodingaaa/orkestr_luc`, **no tags exist**. `git status` reports 129 modified files, but `git diff --ignore-all-space --ignore-blank-lines --stat` returns empty and the diff stat is exactly symmetric (34,878 insertions / 34,878 deletions). **The entire working-tree delta is line-ending churn, not content.** There are no untracked files. `.env` and `sessions.db` are both correctly gitignored and untracked.

---

## 3. Frontend architecture

| Aspect | Finding |
| --- | --- |
| Framework | Next.js `16.2.9`, React `19.2.4`, App Router, TypeScript `strict: true` |
| Package name | `splitwise-mock` — a leftover, cosmetic |
| Routing | **One** production route. `next.config.ts` redirects `/create-event`, `/settled`, `/split-detail`, `/event/:id`, `/negotiation/:id`, `/preferences/:id` and seven design-gallery routes to `/`. Files retained, not deleted. |
| Route dispatch | Query params in `src/app/page.tsx`: no param → `GroupHub`; `?room=` → `GroupRoom`; `?room=&member=` → `ParticipantView` |
| Rendering | Fully client-side. No API routes, no route handlers, no server actions, no server components doing data work |
| State | React hooks. `src/lib/useRoom.ts` polls `GET /api/session/{sid}/public` every 2500 ms (`usePublicSession`). No WebSockets |
| API client | `src/lib/orkestr-api.ts` (621 lines) — typed client, `API_BASE = "/orkestr-api"`, proxied by the Next rewrite |
| Auth | `X-Orkestr-Token` header. Host and member tokens stored in **`localStorage`** (`ork_host_<sid>`, `ork_member_<sid>_<pid>`) |
| Component weight | `GroupRoom.tsx` 1,278 lines (monolith); `ParticipantView.tsx` 407; `GroupHub.tsx` 57 |
| Design primitives | `src/components/orkestr/` — `Button`, `Card`, `Input`, `Badge`, `Amount`, `NavTabs`. Small, self-contained |
| Payment UI | `PaymentProofCard`, `SettlementCard`, `SmartSplitCard`, `ParticipantSplitCard`, `FreshProofPanel`, `TrustPanel`, `MerchantAdapterCard` — legacy thesis |
| Invite/QR | `InvitePanel.tsx` renders `<img>` from a third-party QR service, with a deterministic faux-QR CSS fallback |

**Consequence of localStorage tokens.** A participant who clears storage, uses a different browser, or opens the link on a second device loses their member token and therefore loses access to their own constraint. Master Plan v1.1 §37 requires participants to be able to **view, correct or remove information they supplied**. The current token model cannot satisfy that across devices. This is a design decision Alpha must make deliberately.

---

## 4. Backend architecture

| Aspect | Finding |
| --- | --- |
| Framework | FastAPI `0.111.0`, uvicorn `0.30.1` |
| Entry point | `api/main.py`, 1,285 lines, 37 routes, one module |
| Process model | `Procfile`: `web: uvicorn api.main:app --host 0.0.0.0 --port $PORT` |
| Lifespan | `_lifespan` seeds `ORK-001`, runs `cleanup_expired_rooms()`, then starts a background cleanup thread |
| Domain core | `core/session.py`, 1,368 lines — session lifecycle, quorum, constraints, plan compute, approvals, expenses, settlement, split |
| Concurrency | `threading.Lock` in `core/db.py`; per-session compute locks in `_COMPUTE_LOCKS_META` (`core/session.py:79`) |
| Background work | `start_cleanup_timer()` → `threading.Thread(daemon=True)` loop (`core/session.py:191`); `_spawn_x402_receipt_once` → fire-and-forget `threading.Thread` (`core/session.py:105`) |
| Middleware | CORS from `CORS_ORIGINS`; a custom `_enumeration_guard` rate limiter |
| Auth | Room id = `"ORK-" + secrets.token_urlsafe(16)` (128-bit capability URL). Host token `token_urlsafe(24)` returned once. Member tokens minted on first slot claim via `mint_member_token`, re-claim refused. Comparison uses `hmac.compare_digest`. **No user accounts anywhere.** |
| Abuse controls | Per-IP daily quotas and paid-API spend caps, persisted in SQLite (`daily_counters`) |

### Does it require a long-running process?

**Yes, as currently written.** Five independent pieces of evidence:

1. `core/db.py` `_CACHE` is a module-level dict. Its own docstring: *"Returns the SAME object on repeat calls (by reference) so the existing in-place mutation pattern in core/session.py keeps working unchanged."* Callers mutate the cached object and then call `set_session` to write through.
2. One shared `sqlite3.connect(..., check_same_thread=False)` connection guarded by a single module-level `_LOCK`.
3. Per-session compute locks are process-stable and provide no mutual exclusion across instances.
4. Two daemon threads outlive any single request.
5. `sessions.db` is a file on local disk at repo root.

`README.md` §13 states this directly: *"It must run as one worker / one replica (`WEB_CONCURRENCY=1`); multiple workers would split-brain the cache and locks."* `README.md` §14 already names Postgres and Redis as the unbuilt production replacement.

This is a fact about the **legacy pipeline**, not about Concierge Alpha. See §14.

---

## 5. Database architecture

**Technology:** SQLite via stdlib `sqlite3`. No ORM, no migration tool, no schema versioning. Tables are created with `CREATE TABLE IF NOT EXISTS` at import time in `core/db.py`.

**Tables**

| Table | Columns | Purpose |
| --- | --- | --- |
| `sessions` | `id TEXT PK`, `data TEXT` | Whole session serialised as a JSON blob |
| `member_prefs` | `member_id TEXT PK`, `name`, `constraints` (JSON), `freeform`, `updated_at` | Cross-session "group memory" per member |
| `daily_counters` | `(day, key) PK`, `count` | Spend guards and per-IP abuse quotas |

**Local file at audit time:** `sessions.db`, 2.0 MB, 496 rows in `sessions`, 5 in `member_prefs`, 6 in `daily_counters`. **Row contents were not inspected** — see §15, risk 1.

**Session document shape** (from `new_session`, `core/session.py:198`):

```
session_id, name, date_range, phase, members[], negotiation[], plan,
handshake, approvals[], expenses[], settlement,
created_at, last_active_at,
_auth { host, members{member_id: token} }     # never returned by any read endpoint
```

**Member shape:** `{ id, name, avatar, constraints{}, freeform }`
**Constraint shape** (from `data/personas.json` and `session_public`): `{ available_days[], budget_max, dietary[], max_travel_min, origin }`

### Mapping to Orkestr 2.0 domain concepts

| 2.0 concept | Present today? | Evidence |
| --- | --- | --- |
| Plans | Partial — "session" carries plan intent | `new_session` |
| Groups | No first-class group; members live inside a session | `s["members"]` |
| Participants | Yes | member dicts + `_auth.members` tokens |
| Preferences | Yes, flat | `constraints{}` |
| **Constraints (hard/soft)** | **No** | no classification field anywhere |
| **Constraint ownership / confirmation state** | **No** | no `confirmed_by`, no `UNCONFIRMED` state |
| **Constraint visibility class** | Partial | binary token gate in `session_public`, no aggregate tier |
| **Evidence / provenance** | Yes, for venues only | `agents/venue_grounding.py` |
| Venues | Yes | `data/venues.json` — **3 records**, seed only |
| Sessions | Yes | `sessions` table |
| Invitations | Yes | `_invite_and_qr`, `mint_member_token` |
| Payments / mandates / receipts / x402 / settlements | Yes, extensively | `payments/`, `core/settlement.py`, `core/meal_split.py` |
| **Conflicts / Compromises / Commitment** | **No** | not modelled |
| **Founder action / rescue logs** | **No** | `core/audit.py` is 14 lines; not this |
| **Capability freshness** | **No** | no observed-at or TTL on any venue field |

---

## 6. Deployment and infrastructure

| File | Role |
| --- | --- |
| `Procfile` | Railway backend start command |
| `vercel.json` | `installCommand`, `buildCommand`, `outputDirectory: .next` — frontend only |
| `next.config.ts` | Route redirects + `/orkestr-api/*` → `NEXT_PUBLIC_API_URL` rewrite |
| `requirements.txt` | Python deps, pinned |
| `package.json` | Node deps |
| `.env.example` | Documented env names (incomplete — see §7) |
| `.claude/launch.json` | Local dev launch config |

**No CI configuration exists.** No `.github/`, no workflow YAML anywhere in the tree.
**No `.gitattributes` exists** — the cause of the line-ending churn in §2.

**Dependency map**

- **Vercel** — frontend build and hosting only. Reads `NEXT_PUBLIC_*` at build time.
- **Railway** — the entire backend: API, business logic, SQLite file, background threads, all paid-API calls, all chain calls.
- **Database provider** — none. SQLite lives on the Railway container's disk.
- **Base / Base Sepolia** — `payments/x402_protocol.py`, `payments/x402_demo_receipt.py`, invoked from `core/session.py`.
- **x402** — `x402[evm,requests]==2.14.0`; the merchant 402 is handled manually at route level (see the comment in `requirements.txt`).
- **Wallets** — a buyer signer keyed by `EVM_PRIVATE_KEY`.
- **External APIs** — Google Places, Exa, OpenAI, Stripe, and `api.qrserver.com`.

---

## 7. Environment variable names

**Names only. No values were read, printed or copied. The local `.env` file was not opened.**

**Backend (Python)**

```
ALLOW_SAMPLE_VENUE_FALLBACK   BASE_URL                      CORS_ORIGINS
DEV_RESEED_SECRET             ENABLE_DEV_RESEED             ENABLE_LIVE_REAL_X402
ENABLE_X402_DEMO_RECEIPT      ENABLE_X402_PAID_SMOKE        EVM_PRIVATE_KEY
EXA_API_KEY                   EXA_CACHE_TTL_SECONDS         EXA_MAX_RESULTS
FRONTEND_URL                  GOOGLE_PLACES_API_KEY         IP_COMPUTE_DAILY_CAP
IP_ROOM_CREATE_DAILY_CAP      LLM_DAILY_CALL_CAP            LLM_RANKER_MODEL
MAX_CONSTRAINT_NOTE_LEN       MAX_MEMBERS_PER_ROOM          OPENAI_API_KEY
OPENAI_MODEL                  ORK_DISABLE_PUBLIC_LIMITS     ORK_RL_BLOCK_SECONDS
ORK_RL_MISS_LIMIT             ORK_RL_WINDOW_SECONDS         PLACES_DAILY_CALL_CAP
REQUIRE_REAL_X402             ROOM_CLEANUP_INTERVAL_SECONDS ROOM_TTL_DAYS
RUN_X402_PAID_SMOKE           STRICT_REAL_VENUES            STRIPE_SECRET_KEY
USE_EXA                       USE_EXA_IN_PLANNING           USE_GOOGLE_PLACES
USE_LLM_RANKER                USE_REAL_SEARCH               USE_REAL_X402
X402_BASE_SEPOLIA_RPC_URL     X402_BASE_SEPOLIA_USDC        X402_BOOKING_PRICE
X402_DEMO_RECEIPT_AMOUNT      X402_DEMO_RECEIPT_COOLDOWN_SECONDS
X402_DEMO_RECEIPT_SECRET      X402_FACILITATOR_URL          X402_HTTP_TIMEOUT
X402_MERCHANT_PAY_TO          X402_NETWORK                  X402_RESOURCE_BASE_URL
X402_SMOKE_SECRET
```

**Frontend (build-time public)**

```
NEXT_PUBLIC_API_URL   NEXT_PUBLIC_APP_MODE   NEXT_PUBLIC_ENABLE_LIVE_PIPELINE
NEXT_PUBLIC_SHOW_DEV_GALLERY   NEXT_PUBLIC_SHOW_PRESENTER_TOOLS
```

**Documentation gap.** `.env.example` documents 21 names. At least these are referenced in code but absent from it: `GOOGLE_PLACES_API_KEY`, `USE_GOOGLE_PLACES`, `STRICT_REAL_VENUES`, `ROOM_TTL_DAYS`, `ROOM_CLEANUP_INTERVAL_SECONDS`, `MAX_MEMBERS_PER_ROOM`, `FRONTEND_URL`, `BASE_URL`, and the `IP_*` / `ORK_RL_*` / `*_DAILY_CALL_CAP` families. Not urgent, but `.env.example` is not currently a reliable description of what the service reads.

---

## 8. Existing tests

**There is effectively no unit test suite.**

- `tests/` contains exactly one file: `tests/test_mandate.py`, 55 lines, stdlib `unittest`, covering the AP2 tampered-cart mandate in `payments/mandate.py`. This is legacy-thesis code.
- No pytest, no `conftest.py`, no `pyproject.toml`, no `pytest.ini`, no CI.
- Real coverage lives in **`scripts/` — 31 files (27 Python, 2 `.mjs`, 1 shell), 5,508 lines of Python** — described in `README.md` §11 as *"Stdlib + FastAPI TestClient smokes"*, each run manually as `python scripts/<name>.py`. `README.md` §11 lists ten of them as the routine set.
- `scripts/paidpath_guard.py` is a genuine safety harness that forces every real-x402 spend path off at import time. It is well-constructed and worth keeping regardless of thesis.

**Coverage by subsystem, approximated from file names and the README description rather than execution:** settlement/split, x402/payments, venue/discovery, session/auth/quorum, and honesty/truth guards each have roughly four to six scripts. This is a rough grouping, not a measured coverage figure — no coverage tool is configured in this repository.

**Reusability: `UNCERTAIN`.** `auth_boundary_smoke.py`, `invite_link_smoke.py`, `pure_read_gets_smoke.py` and `constraint_honesty_smoke.py` have names suggesting relevance to Alpha, but their bodies were not read in this audit and the audit brief explicitly warns against assuming reusability from names. They should be read individually before any are claimed as reusable.

**Nothing was executed during this audit.** No smoke script was run.

---

## 9. KEEP / ADAPT / ARCHIVE / UNCERTAIN

No module is marked DELETE. Everything remains in Git.

| Subsystem | Class | Reasoning |
| --- | --- | --- |
| No-account guest access model (`token_urlsafe` room id, host token, `mint_member_token`, `hmac.compare_digest`) | **KEEP (concept)** | Exactly the model Alpha needs. The *pattern* is sound; the code lives inside x402-coupled modules |
| Token-gated constraint visibility (`session_public`, `api/main.py:934`) | **ADAPT** | Working implementation of §11's private tier. Missing the aggregate tier ("one participant's budget is exceeded") |
| Venue grounding / provenance schema (`agents/venue_grounding.py`) | **ADAPT** | 20-field canonical shape with `verification_status`, `source_kind`, `source_url`, `price_confidence`. Missing observed-at, TTL, `CONFLICTING`, `UNKNOWN` |
| Invite link + QR (`_invite_and_qr`, `InvitePanel.tsx`) | **ADAPT** | Link generation is sound. Third-party QR service is a decision to revisit (§15) |
| Design primitives (`src/components/orkestr/`) | **KEEP** | Small, self-contained, thesis-neutral |
| Polling sync (`useRoom.ts`) | **ADAPT** | Correct pattern for Alpha; endpoint shape will change |
| SQLite persistence layer (`core/db.py`) | **ARCHIVE** | Single-process by design. Not viable for a managed-Postgres Alpha |
| Session lifecycle (`core/session.py`) | **ARCHIVE** | 1,368 lines fused to the x402 pipeline (`_spawn_x402_receipt_once`, `_maybe_fire_x402_receipt`) and to settlement |
| API surface (`api/main.py`) | **ARCHIVE** | ~40 routes, most legacy-thesis |
| Constraint model (flat `constraints{}`) | **ARCHIVE** | No hard/soft, ownership, confirmation or provenance. Redesign, not adaptation |
| Plan/recommendation pipeline (`agents/convener.py`, `ai/venue_ranker.py`, `ai/plan_gen.py`, `agents/discovery.py`) | **ARCHIVE** | "preferences → score → recommendation". Alpha needs "feasibility → conflict → compromise" and does it manually anyway |
| Google Places integration (`agents/google_places.py`) | **UNCERTAIN** | Potentially useful for founder-side venue lookup during manual verification. Not needed for the shell |
| Exa discovery (`agents/discovery.py`) | **ARCHIVE** | Not required for Alpha (Master Plan §25) |
| `payments/` (all five modules) | **ARCHIVE** | Legacy thesis. Preserve in Git |
| Settlement / split (`core/settlement.py`, `core/meal_split.py`) | **ARCHIVE** | Explicitly dropped from core |
| Payment UI components (7 files listed in §3) | **ARCHIVE** | Legacy thesis |
| `GroupRoom.tsx` (1,278 lines) | **ARCHIVE** | Monolith built around the demo pipeline |
| `scripts/paidpath_guard.py` | **KEEP** | Safety harness, cheap to retain |
| Smoke scripts (`scripts/`, 31 files) | **UNCERTAIN** | Bodies not read; see §8 |
| `tests/test_mandate.py` | **ARCHIVE** | Tests legacy mandate code |
| `data/venues.json` | **ARCHIVE** | 3 seed records. Alpha needs manually verified real venues with provenance |
| `docs/` (`FEATURES.md`, `GIT_WORKFLOW.md`, `Orkestr_Build_Guide_v2.md`), `ORKESTR_FRONTEND.md`, `README.md` | **ARCHIVE (retain)** | Accurate for 1.0. Do not delete; they document what Railway is doing |

---

## 10. Railway dependency analysis

**What Railway is doing today:** running the entire backend. Specifically —

1. The FastAPI process (`Procfile`), which must be a single worker.
2. The **SQLite database file**, on the container's disk.
3. All background threads (room TTL cleanup, x402 receipt firing).
4. All calls to Google Places, Exa, OpenAI, Stripe.
5. All Base Sepolia interaction, holding `EVM_PRIVATE_KEY`.
6. Invite-link generation (`FRONTEND_URL`) and QR redirects.
7. The persisted spend/abuse counters.

**What breaks the moment Railway stops:** the deployed frontend loses its entire API. Every screen falls back to demo data (`ALLOW_SAMPLE_FALLBACK` is true in demo mode) or blocks in live mode.

**Before Railway is cancelled, one question must be answered:** what is in Railway's copy of `sessions.db`? The local copy holds 496 session rows. If the deployed copy holds real people's names and constraints from live demos, cancelling the service destroys data that may need a deliberate retention decision (Master Plan §50) rather than an accidental one. **Status: `UNKNOWN` — not inspectable from this repository.**

There is no technical reason Concierge Alpha needs Railway. There is a data-handling reason not to cancel it blindly.

---

## 11. Vercel dependency analysis

Minimal and clean. `vercel.json` sets install/build/output only. The frontend needs five build-time `NEXT_PUBLIC_*` variables, one of which (`NEXT_PUBLIC_API_URL`) points at Railway.

**Nothing in the current Vercel configuration blocks Concierge Alpha.** Because the frontend has no server-side code today, adding Next.js route handlers or server actions for Alpha is additive — it does not conflict with anything that exists.

The `next.config.ts` redirect list is worth noting as prior art: it is the mechanism already used to hide half-built routes from production users, and Alpha can use the same mechanism to keep the legacy `/` experience reachable or hidden as needed.

---

## 12. Concierge Alpha reuse opportunities

Ranked by what is genuinely reusable, on inspection rather than name.

**Strong — reuse the pattern, port the code**

1. **Capability-URL access model.** `"ORK-" + secrets.token_urlsafe(16)` room ids, a host token returned once, member tokens minted on first claim and refused on re-claim, compared with `hmac.compare_digest`. Roughly 40 lines of ideas across `new_session` and `mint_member_token`. This is precisely Alpha's "join by link, no account".
2. **Asymmetric constraint visibility.** `session_public` already implements: host sees all detail, member sees own detail, stranger sees names and status only. Needs one new tier — group-visible aggregate without attribution.
3. **Provenance discipline.** `venue_grounding.py`'s rule *"Google Places = verification. Exa = discovery/evidence"*, its refusal to present ungrounded venues, and its normalised candidate shape are the right shape for §17. They need observed-at timestamps, TTL, and the `CONFLICTING` / `UNKNOWN` states added.
4. **Polling sync.** `usePublicSession` at 2500 ms is the right mechanism for a multi-device Alpha and needs no WebSockets.
5. **Design primitives.** `src/components/orkestr/` ports directly.

**Weak — do not reuse**

- Session lifecycle, constraint model, plan pipeline, settlement, payments, `GroupRoom.tsx`. Each is either fused to the legacy thesis or built on the wrong abstraction ("preferences → score" rather than "feasibility → conflict → compromise").

**Explicitly absent and therefore all new**

Conflicts, Compromises, Commitment, ConstraintVisibility, hard/soft classification, owner confirmation state, Founder Action Log, Founder Rescue Log, Capability Freshness observations, venue capability records with per-field provenance.

---

## 13. Minimal new Alpha requirements

**Schema — the smallest set that supports the shell.** Provisional; the audit brief says the production schema emerges from pilot evidence (Master Plan §16).

```
Plan                 id, title, organiser_token, created_at, status, notes
Participant          id, plan_id, display_name, join_token, state
Constraint           id, plan_id, participant_id, field, value,
                     classification (hard|soft|unknown),
                     confirmation (confirmed|unconfirmed),
                     visibility (public|private|sensitive),
                     source_quote, created_at
Venue                id, name, address, area, booking_method
VenueCapability      id, venue_id, field, value,
                     evidence_state (verified|inferred|conflicting|unknown),
                     source, source_identity, observed_at, notes
Option               id, plan_id, venue_id, rank, compromise_note
Conflict             id, plan_id, description, blocking_constraint_ids
Commitment           id, plan_id, participant_id, committed_at
FounderAction        id, plan_id, action, reason, minutes, automatable, created_at
FounderRescue        id, plan_id, problem, action, why, product_could_do_this,
                     without_rescue, created_at
```

Note the deliberate split: `Constraint.confirmation` exists so a model-proposed constraint can sit as `unconfirmed` until its owner confirms it, and `VenueCapability.evidence_state` is separate from `Constraint.confirmation` so Amendment 1's rule holds — a participant's allergy can be `confirmed` while the venue's ability to accommodate it remains an attributed claim, never `verified`.

**UI — five surfaces, nothing more**

| Surface | Contents |
| --- | --- |
| `/create` | Free-text intent + minimal fields. Returns organiser link |
| `/join/:planId` | Pilot notice (Amendment 3) shown *before* any input, current plan assumptions, "I'm good" / "I need to change something" |
| `/p/:token` | Private participant view: correct inferred info, add a constraint, confirm own consequential constraints, **view / correct / remove own data** |
| `/result/:planId` | Confirmed requirements, unresolved conflicts, up to three options, compromises, commitment state |
| `/admin` | Founder-only manual entry for everything: participants, venue capabilities, evidence, conflicts, compromises, sources, action log, rescue log, freshness observations |

**Not in Alpha:** recommendation engine, OpenRouter, scrapers, merchant APIs, payments, x402, automated booking.

---

## 14. Recommended hosting direction

### Recommendation: **OPTION A — Vercel + Neon**

Not by migrating the existing backend. By not depending on it.

**The evidence, stated separately for the two different questions.**

**Question 1: can the *existing* FastAPI service run on Vercel server-side functions?**
No, not without rewriting its persistence layer. Five documented reasons in §4 (by-reference mutable cache, single shared SQLite connection under one lock, process-stable compute locks, two daemon threads, local database file), plus `README.md` §13 stating single-worker-only as an explicit design constraint. If the goal were to keep this service running, that is Option B territory.

**Question 2: does *Concierge Alpha* need any of that?**
No. Alpha's operations are: create a plan row, create participant rows, write and read constraint rows, write venue capability rows from the admin surface, render a result page. Every one is a request-scoped read or write against a database. There is no pipeline, no background job, no cross-request in-memory state, no long-lived connection. Polling at 2500 ms against a stateless read endpoint is well within the serverless model.

**Question 3: is there existing server-side code that migration would preserve?**
No. There is no `src/app/api/`, no route handler, no server action, no `"use server"` in the repository. Alpha's server layer is new code on any host. Choosing Koyeb would not save existing work; it would only add a service to operate.

**Therefore:** build Concierge Alpha as Next.js server-side code on Vercel with Neon Postgres. Leave the Python service exactly where it is — running, paused, or archived, as a separate decision governed by §10's open data question rather than by Alpha's needs.

**When Option B would become correct.** If a later phase reintroduces long-running work — a real feasibility engine that must hold state across requests, scheduled venue re-verification, queued merchant enquiries, or a revived x402 machine-to-machine layer — a persistent process becomes justified. Master Plan §56 puts all of those after the five runs. Adding Koyeb now buys nothing and costs an extra deployment target to maintain.

**One caveat stated plainly.** This recommendation is about Concierge Alpha only. It is not a claim that Vercel + Neon is the right production architecture for Orkestr v1. That decision belongs after the five pilots, per Master Plan §56, and should be made from what the pilots reveal about the real feasibility and compromise engines.

---

## 15. Risks

**1. Personal data in `sessions.db` — needs a decision, not a default.**
The local file holds 496 session rows. Row contents were deliberately not inspected during this audit. Railway's copy is `UNKNOWN`. Session rows carry member names, dietary constraints, budgets and freeform notes. Master Plan §50 and Amendment 3 make retention a deliberate act. Decide what happens to both copies *before* Railway is cancelled, and independently of Alpha.

**2. `EVM_PRIVATE_KEY` is a live secret in the deployment.**
Base Sepolia is a testnet and the exposure is low-value, but the key exists in the Railway environment and in the local `.env`. When Railway is decommissioned, drain and rotate rather than simply deleting the service. The local `.env` was not opened during this audit.

**3. The invite URL is sent to a third-party QR service.**
`_invite_and_qr` builds `https://api.qrserver.com/v1/create-qr-code/?data=<invite_url>`. The invite URL contains the room id, and the room id **is** the access capability for that room. Every QR render puts a working room link into a third party's request logs. Acceptable for a hackathon demo; worth a deliberate decision before real pilot participants' plans are behind those links. Generating QR codes locally removes the issue entirely.

**4. No tags exist. Phase 1 preservation has not been done.**
`git tag` returns nothing. The pre-renewal state is currently protected only by branch history.

**5. Line-ending churn makes every future diff unreadable.**
129 files show as modified with zero content change. Without a `.gitattributes`, any future review of "what changed in the renewal" will be buried in 34,878 phantom line changes.

**6. The Orkestr 2.0 strategy documents are not in this repository.**
`docs/` contains `FEATURES.md`, `GIT_WORKFLOW.md`, `Orkestr_Build_Guide_v2.md`. There is no Handover Brief, no Master Plan, no v1.1 amendments. Any future audit or build instruction that tells an agent to "read the Master Plan in the repository" will not find one.

**7. No CI and effectively no unit tests.**
One 55-line test file for legacy code, plus 24 manually-run smoke scripts. Alpha does not need extensive testing, but it should not inherit the assumption that a test suite exists.

**8. x402 coupling is deeper than the directory structure suggests.**
49 files. Any plan that reuses `core/session.py` inherits it. This is the strongest technical argument for Alpha being new code rather than an adaptation.

---

## 16. Open questions

1. **What is in Railway's `sessions.db`?** Real participant data or demo data only? This gates how Railway can be shut down. Not answerable from this repository.
2. **How should a participant re-access their own data on a second device?** localStorage tokens cannot satisfy Amendment 3's view/correct/remove requirement across devices. Options include emailing the private link, a short re-entry code, or accepting single-device access and saying so in the notice. This is a product decision, not a technical one.
3. **Does the founder admin surface need auth beyond a capability URL?** It will hold every participant's private constraints across all five pilots.
4. **Should the legacy `/` experience remain reachable on the deployed Vercel site during Alpha,** or be redirected away like the other orphan routes?
5. **Is `agents/google_places.py` worth keeping as a founder-side lookup tool** during manual venue verification, separate from the archived pipeline?
6. **Which of the 24 smoke scripts are actually reusable?** Requires reading them individually.
7. **Same repository or new one?** Alpha as a new directory in this repo keeps history in one place; a new repo gives a clean tree. Not decided here.

---

## 17. Proposed implementation sequence

**Proposed only. Nothing in this section has been executed.**

**Phase 1 — preserve (minutes, no code change)**
1. Add a `.gitattributes` normalising line endings; commit the resulting normalisation as one isolated commit so it never pollutes a review diff again.
2. Tag the current state, e.g. `orkestr-v1-x402-final`. Push tags.
3. Commit the three Orkestr 2.0 strategy documents into `docs/`.
4. Branch for the new work.

**Phase 2 — decide the two things Alpha cannot proceed without**
5. Answer open question 1 (Railway's data) and open question 2 (participant re-access). Both are decisions, not builds.

**Phase 3 — Alpha shell (Option A)**
6. Provision Neon; add the connection string to Vercel and locally. *(Explicit infrastructure checkpoint — do not start before the audit is reviewed.)*
7. Create the schema in §13 as new tables. Touch nothing in `core/` or `api/`.
8. Build the five surfaces in §13 as Next.js route handlers plus pages, porting the capability-URL access model and the visibility rules from §12.
9. Put the Amendment 3 pilot notice on `/join` and `/p` **before** the first participant input, with a real operator name and monitored email.
10. Build the admin surface last and most completely — during Alpha it is the actual product.

**Phase 4 — run**
11. Operate all five pilots manually behind the shell, logging founder actions and rescues as they happen.

**Phase 5 — decide**
12. Evidence review against the kill criteria and positive thresholds. Only then does full renewal begin, per Master Plan §56.

**Recruiting for Pilot 1 does not depend on any step above and should not wait for them.**

---

*Audit complete. No application code was modified. No hosting was migrated. No environment variables were altered. Nothing was deleted. No secret values were read or printed. No smoke scripts were executed.*
