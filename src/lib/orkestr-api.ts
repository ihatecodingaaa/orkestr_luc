// Thin API client + shared types for the live Orkestr experience.
// Browser calls go through a same-origin Next rewrite to avoid CORS during live QA.
// No secrets here. Status reads can run backend planning/booking depending on
// server config, so the UI keeps that pipeline behind an explicit opt-in flag.

import type { MealSplit, SplitMode } from "@/lib/split";

export const API_BASE = "/orkestr-api";

export type Dict = Record<string, unknown>;

export type Member = { id: string; name: string; avatar?: string | null };

export type Constraints = {
  days: string[];
  budget: string;
  dietary: string[];
  freeform: string;
};

export type VerificationStatus = "verified" | "source_only" | "demo" | "unverified";

// Grounding metadata attached to the chosen venue. Mirrors the backend
// verification block (agents/venue_grounding.py). No secrets — public links only.
export type VenueVerification = {
  verification_status?: VerificationStatus;
  source_kind?: "google_places" | "exa" | "demo" | "unverified";
  verification_notes?: string;
  confidence_score?: number;
  badge?: string;
  area?: string | null;
  cuisine?: string | null;
  price_hint?: string | null;
  source_url?: string | null;
  source_title?: string | null;
  source_domain?: string | null;
  google_place_id?: string | null;
  google_maps_uri?: string | null;
  website_uri?: string | null;
  business_status?: string | null;
  rating?: number | null;
  user_rating_count?: number | null;
};

export type Venue = {
  id?: string;
  name?: string;
  address?: string;
  area?: string;
  tags?: string[];
  verification?: VenueVerification;
};

export type Conflict = {
  agent?: string;
  constraint_ref?: string | null;
  claim?: string;
};

export type Plan = {
  title?: string;
  day?: string;
  time?: string;
  venue?: Venue;
  total_cost?: number;
  per_person?: number;
  booking_ref?: string | null;
  satisfies?: string[];
  conflicts?: Conflict[];
};

export type Message = {
  round?: number;
  agent?: string;
  stance?: string;
  claim?: string;
  constraint_ref?: string | null;
};

export type Mandate = {
  status?: string;
  booking_ref?: string;
  merchant?: string;
  payment_mode?: string;
  vault_token?: string | Dict;
  order?: Dict;
};

export type Handshake = {
  request?: Dict;
  response?: Dict;
  mandate?: Mandate;
  booking_ref?: string;
  status?: string;
  merchant?: string;
};

export type Transfer = {
  from?: string;
  to?: string;
  amount?: number;
  rail?: string;
  status?: string;
  tx_hash?: string | null;
  latency_ms?: number;
};

export type Settlement = {
  transfers?: Transfer[];
  net_after?: Record<string, number>;
};

// Additive quorum planning fields (P2.2) shared by /api/status and /public.
export type QuorumInfo = {
  members_total?: number;
  replied_count?: number;
  quorum_min?: number;
  quorum_ready?: boolean;
  all_ready?: boolean;
  pending_members?: string[];
  plan_basis?: "all_ready" | "quorum_draft" | null;
  draft_started_with?: string[];
  // How many friends replied AFTER the current quorum draft was created, so the
  // presenter can fold them in. draft_stale is true when that count > 0.
  new_replies_since_draft?: number;
  draft_stale?: boolean;
};

export type StatusResponse = QuorumInfo & {
  phase?: string;
  ready?: string[];
  booking_failed?: boolean;
  fail_reason?: string | null;
  venue_status?: string | null;
};

// Read-only public snapshot used for cross-device sync (presenter ↔ phones).
// Mirrors GET /api/session/{sid}/public — no secrets, never runs the pipeline.
export type PublicMember = {
  id: string;
  name: string;
  avatar?: string | null;
  submitted: boolean;
  ready: boolean;
  constraints: {
    available_days?: string[];
    budget_max?: number | null;
    dietary?: string[];
  } | null;
  freeform: string;
};

// Compact, public projection of the chosen plan (from GET /api/session/{sid}/public).
// Lets a phone render the REAL plan without calling /api/plan (which would lazily
// run the pipeline). Public fields only — no vault token / order / signature.
export type PublicPlan = {
  title?: string | null;
  day?: string | null;
  time?: string | null;
  per_person?: number | null;
  total_cost?: number | null;
  booking_ref?: string | null;
  satisfies?: string[];
  venue?: {
    name?: string | null;
    address?: string | null;
    area?: string | null;
    tags?: string[];
    verification?: (VenueVerification & { name?: string; address?: string }) | null;
  } | null;
};

export type PublicSession = QuorumInfo & {
  session_id: string;
  name?: string;
  live: boolean;
  phase: string;
  members: PublicMember[];
  ready: string[];
  ready_count: number;
  submitted_count: number;
  members_total: number;
  plan_ready: boolean;
  booking_ref?: string | null;
  payment_mode?: string | null;
  booking_failed: boolean;
  fail_reason?: string | null;
  venue_status?: string | null;
  venue_verification?: (VenueVerification & { name?: string; address?: string }) | null;
  plan?: PublicPlan | null;
  // Raw fresh x402 receipt auto-fired by the backend at booking time. Present only
  // when a real on-chain receipt exists; run through toReceipt() to render it.
  x402_receipt?: Dict | null;
};

export type LiveState = {
  phase: string;
  plan: Plan;
  negotiation: Message[];
  handshake: Handshake;
  settlement: Settlement;
  venueStatus?: string | null;
  // Failure surfaced by the status gate (quorum / no verified venue / mandate
  // integrity). When set, the room did NOT succeed — never reveal a sample plan.
  failReason?: string | null;
  bookingFailed?: boolean;
  // False when the plan isn't computed yet (status said not_ready) — the pure-read
  // endpoints returned 409 and we did not fan out. The UI stays in waiting/working.
  planReady?: boolean;
  // "quorum_draft" when the plan came from a partial-quorum draft (P2.2), so the UI
  // can label it "draft based on N of M replies". "all_ready" for a full run.
  planBasis?: "all_ready" | "quorum_draft" | null;
  draftStartedWith?: string[];
  checkedAt: string;
};

// ---- x402 demo receipt (presenter-only fresh proof) -----------------------
// The receipt the UI renders — either the saved verified fallback or a fresh
// on-chain proof created by the presenter. All fields are public chain data.
export type ReceiptSource = "fresh_demo_receipt" | "saved_verified_receipt";

export type X402Receipt = {
  source: ReceiptSource;
  isFresh: boolean;
  label: string;
  paymentMode: string;
  amount: string; // display, e.g. "0.001 USDC"
  amountBaseUnits: string; // "1000"
  network: string; // display chain, e.g. "Base Sepolia"
  networkId: string; // e.g. "eip155:84532"
  asset: string;
  buyer: string;
  merchant: string;
  txHash: string;
  basescan: string;
  createdAt?: string | null;
};

// Public-safe status for the presenter's "Fresh x402 proof run" panel. Reading
// this NEVER triggers a payment — it only tells the UI whether the button is
// enabled and how long any cooldown has left.
export type DemoReceiptStatus = {
  enabled: boolean;
  cooldown_seconds: number;
  cooldown_remaining_seconds: number;
  in_progress?: boolean;
  amount: number;
  amount_display: string;
  network: string;
  asset?: string | null;
  merchant?: string | null;
  confirm_phrase?: string;
  has_latest_fresh_receipt: boolean;
};

// Map a backend receipt JSON (from /latest or a successful /run) to X402Receipt.
// Defensive: unknown/partial payloads fall back to safe display defaults.
//
// Product-truth guard: a MOCK/fallback body (returned by the /run route when the
// facilitator times out or errors) is NOT a real on-chain settlement and must
// NEVER become a renderable receipt — its placeholder txHash has no verifiable
// Basescan tx behind it. Reject it here so every caller treats it as "no proof".
export function toReceipt(raw: unknown): X402Receipt | null {
  if (!isRecord(raw)) return null;
  if (asString(raw.source) === "mock_fallback" || asString(raw.payment_mode) === "mock") {
    return null;
  }
  const txHash = asString(raw.txHash);
  const source: ReceiptSource =
    asString(raw.source) === "fresh_demo_receipt" ? "fresh_demo_receipt" : "saved_verified_receipt";
  return {
    source,
    isFresh: source === "fresh_demo_receipt",
    label: asString(
      raw.label,
      source === "fresh_demo_receipt" ? "Fresh x402 testnet receipt" : "Saved verified receipt",
    ),
    paymentMode: asString(raw.payment_mode, "x402_real"),
    amount: asString(raw.amount_display, "0.001 USDC"),
    amountBaseUnits: String(raw.amount ?? "1000"),
    network: asString(raw.chain, "Base Sepolia"),
    networkId: asString(raw.network, "eip155:84532"),
    asset: asString(raw.asset),
    buyer: asString(raw.buyer),
    merchant: asString(raw.merchant),
    txHash,
    basescan: asString(
      raw.blockExplorerUrl,
      txHash ? `https://sepolia.basescan.org/tx/${txHash}` : "",
    ),
    createdAt: typeof raw.created_at === "string" ? raw.created_at : null,
  };
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }

  return (await response.json()) as T;
}

// ---- live session lifecycle ----------------------------------------------

export async function createLiveSession(): Promise<{ session_id: string; members: Member[] }> {
  return fetchJson("/api/session/live", { method: "POST", body: "{}" });
}

export async function submitConstraints(
  sessionId: string,
  member: Member,
  c: Constraints,
): Promise<void> {
  await fetchJson("/api/constraints", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      id: member.id,
      name: member.name,
      constraints: {
        available_days: c.days,
        budget_max: Number(c.budget),
        dietary: c.dietary,
      },
      freeform: c.freeform,
    }),
  });
}

export async function markReady(sessionId: string, personId: string): Promise<void> {
  await fetchJson(`/api/ready/${sessionId}`, {
    method: "POST",
    body: JSON.stringify({ person_id: personId }),
  });
}

export async function getStatus(sessionId: string): Promise<StatusResponse> {
  return fetchJson<StatusResponse>(`/api/status/${sessionId}`);
}

// P2.2 — presenter starts a QUORUM DRAFT (compute a plan from the friends who have
// replied, without waiting for everyone). Explicit action only; the backend refuses
// with 409 below quorum. Runs the pipeline under the P0 per-session compute lock.
export async function startDraft(
  sessionId: string,
): Promise<StatusResponse & { ok?: boolean }> {
  return fetchJson<StatusResponse & { ok?: boolean }>(`/api/session/${sessionId}/draft`, {
    method: "POST",
    body: "{}",
  });
}

// Re-draft to fold in friends who replied after the first draft. Same endpoint —
// the backend recomputes with the current replied set (and promotes to all_ready
// once everyone is in) without re-firing any payment.
export const updateDraft = startDraft;

// Safe to poll from any device — read-only, never triggers the pipeline.
export async function readPublicSession(sessionId: string): Promise<PublicSession> {
  return fetchJson<PublicSession>(`/api/session/${sessionId}/public`);
}

export async function approveTransfer(sessionId: string, personId: string): Promise<void> {
  await fetchJson(`/api/settle/${sessionId}`, {
    method: "POST",
    body: JSON.stringify({ person_id: personId }),
  });
}

// Like fetchJson but returns null on a 409 not_ready — a pure-read endpoint whose
// plan isn't computed yet (P1.1). The caller renders calmly instead of throwing.
async function fetchMaybeNotReady<T>(path: string): Promise<T | null> {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
  });
  if (response.status === 409) return null;
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return (await response.json()) as T;
}

// Reads the whole live picture. /api/status is the SINGLE compute gate (P1.1): we
// call it first and only fan out to the pure-read plan/negotiation/handshake
// endpoints once a plan is actually ready. A not_ready or failed room returns a
// state carrying the phase/failReason so the UI branches to waiting/working/
// failure — it never triggers a compute and never dead-ends on a raw 409.
export async function readLiveState(sessionId: string): Promise<LiveState> {
  const status = await fetchJson<StatusResponse>(`/api/status/${sessionId}`);
  const phase = status.phase ?? "unknown";
  const checkedAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const planReady = phase === "plan_ready" || phase === "booking" || phase === "confirmed";

  const base: LiveState = {
    phase,
    plan: {},
    negotiation: [],
    handshake: {},
    settlement: {},
    venueStatus: status.venue_status ?? null,
    failReason: status.fail_reason ?? null,
    bookingFailed: Boolean(status.booking_failed),
    planReady,
    planBasis: status.plan_basis ?? null,
    draftStartedWith: status.draft_started_with ?? [],
    checkedAt,
  };
  if (!planReady) return base;

  const [negotiation, plan, handshake, settlement] = await Promise.all([
    fetchMaybeNotReady<Message[]>(`/api/negotiation/${sessionId}`),
    fetchMaybeNotReady<Plan>(`/api/plan/${sessionId}`),
    fetchMaybeNotReady<Handshake>(`/api/handshake/${sessionId}`),
    fetchMaybeNotReady<Settlement>(`/api/settlement/${sessionId}`),
  ]);

  return {
    ...base,
    plan: plan ?? {},
    negotiation: Array.isArray(negotiation) ? negotiation : [],
    handshake: handshake ?? {},
    settlement: settlement ?? {},
  };
}

// Smart itemized split for a session, computed by the backend. This endpoint is
// calculation-only: it never triggers planning, Exa, Places, ACP, x402, signing,
// settlement, or a payment. Aborts after timeoutMs so a slow/offline backend can
// fall back to the local TS calculator instead of hanging the demo.
export async function getSplit(
  sessionId: string,
  mode: SplitMode = "itemized",
  timeoutMs = 6000,
): Promise<MealSplit> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchJson<MealSplit>(`/api/split/${sessionId}?mode=${mode}`, {
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

// ---- x402 demo receipt client ---------------------------------------------
// All three calls are read-only EXCEPT runDemoReceipt, which is the ONLY thing
// in the whole frontend that can start a real payment — and only when the
// presenter deliberately supplies the secret + exact confirm phrase.

// Read-only: never triggers a payment. Safe to call on mount / poll for cooldown.
export async function getDemoReceiptStatus(timeoutMs = 6000): Promise<DemoReceiptStatus> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchJson<DemoReceiptStatus>("/api/x402/demo-receipt/status", {
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

// Read-only: returns the latest fresh receipt if one exists, else the saved
// verified fallback. Never triggers a payment.
export async function getLatestReceipt(timeoutMs = 6000): Promise<X402Receipt | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const raw = await fetchJson<Dict>("/api/x402/demo-receipt/latest", {
      signal: controller.signal,
    });
    return toReceipt(raw);
  } finally {
    clearTimeout(timer);
  }
}

export type RunReceiptResult = { ok: boolean; status: number; body: Dict };

// The deliberate presenter action. The secret is sent ONLY in the request header
// (never the URL, never persisted, never logged). Returns the HTTP status so the
// caller can distinguish 403 / 400 / 409 / 429 / 502 without throwing.
export async function runDemoReceipt(secret: string, confirm: string): Promise<RunReceiptResult> {
  const response = await fetch(`${API_BASE}/api/x402/demo-receipt/run`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-Orkestr-Demo-Secret": secret,
    },
    body: JSON.stringify({ confirm }),
  });
  let body: Dict = {};
  try {
    body = (await response.json()) as Dict;
  } catch {
    body = {};
  }
  return { ok: response.ok, status: response.status, body };
}

// ---- small pure helpers ---------------------------------------------------

export function isRecord(value: unknown): value is Dict {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

export function money(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `$${value}` : "$--";
}

export function shortHash(hash?: string | null, head = 6, tail = 4): string {
  if (!hash) return "pending";
  return hash.length > head + tail ? `${hash.slice(0, head)}…${hash.slice(-tail)}` : hash;
}

export function paymentMode(mandate?: Mandate): string {
  const direct = asString(mandate?.payment_mode);
  if (direct) return direct;
  const token = mandate?.vault_token;
  if (isRecord(token)) return asString(token.mode, "unknown");
  return "unknown";
}
