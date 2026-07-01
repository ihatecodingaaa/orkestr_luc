// Thin API client + shared types for the live Orkestr experience.
// Browser calls go through a same-origin Next rewrite to avoid CORS during live QA.
// No secrets here. Status reads can run backend planning/booking depending on
// server config, so the UI keeps that pipeline behind an explicit opt-in flag.

export const API_BASE = "/orkestr-api";

export type Dict = Record<string, unknown>;

export type Member = { id: string; name: string; avatar?: string | null };

export type Constraints = {
  days: string[];
  budget: string;
  dietary: string[];
  freeform: string;
};

export type Venue = {
  id?: string;
  name?: string;
  address?: string;
  tags?: string[];
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
};

export type LiveState = {
  phase: string;
  plan: Plan;
  negotiation: Message[];
  handshake: Handshake;
  settlement: Settlement;
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
    checkedAt: new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    }),
  };
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
