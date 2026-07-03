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

export type StatusResponse = {
  phase?: string;
  ready?: string[];
  members_total?: number;
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

export type PublicSession = {
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
  venue_status?: string | null;
  venue_verification?: (VenueVerification & { name?: string; address?: string }) | null;
};

export type LiveState = {
  phase: string;
  plan: Plan;
  negotiation: Message[];
  handshake: Handshake;
  settlement: Settlement;
  venueStatus?: string | null;
  checkedAt: string;
};

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

// Reads the whole live picture in one shot once the plan is ready.
export async function readLiveState(sessionId: string): Promise<LiveState> {
  const [status, negotiation, plan, handshake, settlement] = await Promise.all([
    fetchJson<StatusResponse>(`/api/status/${sessionId}`),
    fetchJson<Message[]>(`/api/negotiation/${sessionId}`),
    fetchJson<Plan>(`/api/plan/${sessionId}`),
    fetchJson<Handshake>(`/api/handshake/${sessionId}`),
    fetchJson<Settlement>(`/api/settlement/${sessionId}`),
  ]);

  return {
    phase: status.phase ?? "unknown",
    plan,
    negotiation: Array.isArray(negotiation) ? negotiation : [],
    handshake,
    settlement,
    venueStatus: status.venue_status ?? null,
    checkedAt: new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    }),
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
