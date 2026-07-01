// Demo-facing display data: personas, friendly copy, the verified (public) x402
// receipt, a static fallback plan for resilient demoing, and the demo groups.
// Nothing here is secret — wallet addresses and the tx hash are public chain data.

import type { Constraints, Plan } from "@/lib/orkestr-api";

export const DAY_OPTIONS = ["FRI", "SAT", "SUN"] as const;

export const DIET_OPTIONS: { id: string; label: string }[] = [
  { id: "halal", label: "Halal" },
  { id: "vegetarian", label: "Vegetarian" },
  { id: "no_raw_fish", label: "No raw fish" },
  { id: "vegan", label: "Vegan" },
];

export type Persona = {
  id: string;
  name: string;
  initial: string;
  colorVar: string; // friend accent CSS variable
  vibe: string; // short, human one-liner
  wants: string; // headline preference shown on the card
  demo: Constraints; // backend-valid constraints used by "Ready all 5"
};

// Flavor matches the brief; every persona keeps FRI so the group converges on a
// real plan during the live pipeline. Dietary values are backend enums.
export const PERSONAS: Persona[] = [
  {
    id: "P-001",
    name: "Alice",
    initial: "A",
    colorVar: "--friend-a",
    vibe: "Down for anything fun",
    wants: "Halal · up to $60 · Friday",
    demo: { days: ["FRI", "SAT"], budget: "60", dietary: ["halal"], freeform: "happy to book the table" },
  },
  {
    id: "P-002",
    name: "Bob",
    initial: "B",
    colorVar: "--friend-b",
    vibe: "Keeping it wallet-friendly",
    wants: "Vegetarian · near an MRT",
    demo: { days: ["FRI", "SAT"], budget: "55", dietary: ["vegetarian"], freeform: "somewhere near an MRT please" },
  },
  {
    id: "P-003",
    name: "Carol",
    initial: "C",
    colorVar: "--friend-c",
    vibe: "Will travel for a good time",
    wants: "No raw fish · up to $80",
    demo: { days: ["FRI"], budget: "80", dietary: ["no_raw_fish"], freeform: "loves a good arcade after" },
  },
  {
    id: "P-004",
    name: "Dave",
    initial: "D",
    colorVar: "--friend-d",
    vibe: "Here for the grill",
    wants: "Korean BBQ · group seating",
    demo: { days: ["FRI", "SAT"], budget: "100", dietary: [], freeform: "Korean BBQ with group seating" },
  },
  {
    id: "P-005",
    name: "Eve",
    initial: "E",
    colorVar: "--friend-e",
    vibe: "Plant-based and close by",
    wants: "Vegan option · not too far",
    demo: { days: ["FRI", "SAT"], budget: "80", dietary: ["vegan"], freeform: "vegan-friendly and walkable" },
  },
];

const PERSONA_BY_ID: Record<string, Persona> = Object.fromEntries(
  PERSONAS.map((p) => [p.id, p]),
);

export function personaById(id?: string): Persona | undefined {
  return id ? PERSONA_BY_ID[id] : undefined;
}

export function personName(id?: string): string {
  return personaById(id)?.name ?? id ?? "Agent";
}

// "Alice" · "Alice and Bob" · "Alice, Bob, and Carol" — never "A and B and C".
export function formatNames(names: string[]): string {
  if (names.length === 0) return "";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

export function personInitial(id?: string): string {
  return personaById(id)?.initial ?? (id ? id.slice(0, 1) : "?");
}

export function friendColorVar(id?: string): string {
  return personaById(id)?.colorVar ?? "--muted";
}

export function emptyConstraints(): Constraints {
  return { days: [], budget: "", dietary: [], freeform: "" };
}

// Deep copy so a prefilled persona form can be edited without mutating defaults.
export function cloneDemoConstraints(c: Constraints): Constraints {
  return { ...c, days: [...c.days], dietary: [...c.dietary] };
}

// ---- shareable room links -------------------------------------------------
// Deep links use query params on the existing page (Option A): the presenter
// command center is /?room=ORK-XXXX and each friend's phone view is
// /?room=ORK-XXXX&member=P-00N. Built from the live origin so a copied link
// or scanned QR opens straight into that member's mobile experience.

function originBase(): string {
  if (typeof window !== "undefined") return window.location.origin;
  return "";
}

// Offline/local fallback room id — used when the backend can't mint one so the
// presenter still gets shareable demo links and the flow never dead-ends.
export function localRoomId(): string {
  const rand = Math.random().toString(36).slice(2, 6).toUpperCase();
  return `ORK-${rand}`;
}

export function presenterLink(roomId: string): string {
  return `${originBase()}/?room=${encodeURIComponent(roomId)}`;
}

// `origin` is threaded in from a mounted client so server and first client
// render agree (avoids a hydration mismatch on the invite links / QR src).
export function participantLink(
  roomId: string,
  memberId: string,
  origin: string = originBase(),
): string {
  return `${origin}/?room=${encodeURIComponent(roomId)}&member=${encodeURIComponent(memberId)}`;
}

// Remote QR image (real, scannable) with a CSS placeholder fallback baked into
// the card UI. No build dependency, no install risk — just an <img> of a URL
// that contains only the public room link (never a secret).
export function qrImageUrl(data: string, size = 168): string {
  const params = new URLSearchParams({
    size: `${size}x${size}`,
    data,
    margin: "0",
    bgcolor: "FFFDF5",
    color: "0D530E",
    qzone: "1",
  });
  return `https://api.qrserver.com/v1/create-qr-code/?${params.toString()}`;
}

export function isReadyToSubmit(c: Constraints): boolean {
  return c.days.length > 0 && Number(c.budget) > 0;
}

// ---- the verified, public x402 receipt (the WOW card) ---------------------
// These are public on-chain values. Displayed as a demo receipt — the UI never
// triggers another real USDC payment.

export const X402_PROOF = {
  status: "Paid",
  paymentMode: "x402_real",
  amount: "0.001 USDC",
  amountBaseUnits: "1000",
  network: "Base Sepolia",
  networkId: "eip155:84532",
  protocol: "x402",
  asset: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  buyer: "0x851b9DeCa5BE1d143422B70459bD8843cD870BDa",
  buyerLabel: "Orkestr Agent Wallet",
  merchant: "0xd850A5ac28bd3648eB6c08cCa416291ee0141EFc",
  merchantLabel: "Restaurant / Merchant Agent Wallet",
  txHash: "0x7044fa48cf844251bc4f815ff0464c5255393fb398259c12bb11d2ef5eeb62dd",
  basescan:
    "https://sepolia.basescan.org/tx/0x7044fa48cf844251bc4f815ff0464c5255393fb398259c12bb11d2ef5eeb62dd",
  verified: [
    "HTTP 402 challenge received",
    "PAYMENT-REQUIRED header verified",
    "PAYMENT-SIGNATURE settled on-chain",
  ],
} as const;

// ---- static fallback plan -------------------------------------------------
// Mirrors the seeded happy-path so demo-control jumps and a slow/unreachable
// backend still render a beautiful final state. Real data is preferred whenever
// the live pipeline returns.

export const DEMO_PLAN: Plan = {
  title: "Seoul Garden",
  day: "FRI",
  time: "19:30",
  venue: {
    name: "Seoul Garden",
    address: "Tanjong Pagar, Singapore",
    tags: ["korean_bbq", "halal_options", "vegetarian_options", "group_seating"],
  },
  total_cost: 400,
  per_person: 80,
  booking_ref: "ORK-BOOK-2F4A",
  satisfies: ["P-001", "P-002", "P-003", "P-004", "P-005"],
  conflicts: [],
};

export const WHY_THIS_WON = [
  "Halal and vegetarian menus under one roof",
  "Group seating for five, booked for Friday 19:30",
  "$80/head lands inside everyone's budget",
];

// ---- agent timeline -------------------------------------------------------

export type TimelineStep = {
  key: string;
  title: string;
  caption: string;
  tag: string;
};

export const TIMELINE_STEPS: TimelineStep[] = [
  {
    key: "prefs",
    title: "Preferences collected",
    caption: "Everyone's constraints are in and validated.",
    tag: "Group",
  },
  {
    key: "discovery",
    title: "Finding places that fit everyone",
    caption: "Searching real venues for diet, budget, and location.",
    tag: "Exa · venue discovery",
  },
  {
    key: "negotiate",
    title: "Negotiating the compromise",
    caption: "Agents trade off day, budget, diet, and vibe.",
    tag: "Agents · negotiation",
  },
  {
    key: "booking",
    title: "Booking with the merchant agent",
    caption: "Agent-to-agent handshake locks the table.",
    tag: "ACP · merchant booking",
  },
  {
    key: "payment",
    title: "USDC payment verified",
    caption: "Orkestr's agent pays the merchant through x402 on Base.",
    tag: "x402 · programmable USDC",
  },
];

// ---- waiting room content -------------------------------------------------

export const PREPARING = [
  { title: "Building venue search", caption: "Turning everyone's vibe into search terms." },
  { title: "Checking diet conflicts", caption: "Halal, vegetarian, vegan, and no-raw-fish all matter." },
  { title: "Estimating budget fit", caption: "Finding a per-head price that works for all five." },
];

export type ChatSeed = { who: string; text: string };

export const SEED_CHAT: ChatSeed[] = [
  { who: "P-001", text: "Korean BBQ if there are halal options?" },
  { who: "P-003", text: "Need veg options 🙏" },
  { who: "P-002", text: "Somewhere near an MRT would be great" },
  { who: "P-005", text: "Vegan-friendly + walkable is perfect" },
];

// ---- demo groups (the hub) ------------------------------------------------

export type GroupMode = "live" | "ready" | "paid";

export type DemoGroup = {
  id: string;
  name: string;
  emoji: string;
  tagline: string;
  memberCount: number;
  status: string;
  mode: GroupMode;
  nextPlan: string;
  accentVar: string;
};

export const DEMO_GROUPS: DemoGroup[] = [
  {
    id: "friday",
    name: "Friday Dinner Crew",
    emoji: "🍜",
    tagline: "The usual five, deciding where to eat tonight.",
    memberCount: 5,
    status: "Live now",
    mode: "live",
    nextPlan: "Deciding tonight",
    accentVar: "--primary",
  },
  {
    id: "hackathon",
    name: "Hackathon Supper",
    emoji: "🍕",
    tagline: "A quick fuel run between commits.",
    memberCount: 4,
    status: "Plan ready",
    mode: "ready",
    nextPlan: "Pizza · Fri 20:00",
    accentVar: "--primary-2",
  },
  {
    id: "birthday",
    name: "Birthday Hotpot",
    emoji: "🍲",
    tagline: "Carol's birthday — booked and paid with x402.",
    memberCount: 6,
    status: "Booked · paid",
    mode: "paid",
    nextPlan: "Hotpot · Sat 19:30",
    accentVar: "--money",
  },
];

// Friendly, centralized copy (also keeps apostrophes out of JSX text).
export const COPY = {
  heroTitle: "Dinner plans without the group chat chaos.",
  heroSub:
    "Everyone shares constraints. Orkestr's agents find the compromise, book the table, and show verified x402 payment proof.",
  walletExplainer:
    "Friends don't need wallets. Orkestr's payment agent pays the merchant agent once, then the group splits internally.",
  // Shown while the group is still filling in (ready_count < members_total).
  agentsStartHint:
    "Agents start once everyone submits. You can nudge friends or continue with Demo Cast.",
  // Shown the moment everyone is in (ready_count == members_total, plan pending).
  agentsWorkingHint:
    "Orkestr is discovering venues, checking trade-offs, and preparing the booking.",
  // Participant-facing wait note — no presenter-only Demo Cast mention.
  participantWaitHint:
    "Your agent is negotiating on your behalf. The plan and payment proof appear here the moment everyone's in.",
} as const;
