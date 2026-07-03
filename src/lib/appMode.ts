// Central app-mode helper — the single source of truth for Demo vs Live behaviour.
//
// Demo Mode (default): sample fallbacks are allowed so the app always renders a
// beautiful end state even offline. Presenter tools ("Demo Cast") are available.
//
// Live Mode: the app behaves like a real product. Sample fallbacks are BLOCKED —
// if the backend is down it blocks with a real error instead of a fake room, and
// it never shows a sample venue or an old receipt as the live result. Presenter
// tools are hidden unless explicitly enabled, and clearly labelled as simulation.
//
// Both flags are build-time public env (NEXT_PUBLIC_*). Nothing secret lives here.

export type AppMode = "demo" | "live";

const rawMode = (process.env.NEXT_PUBLIC_APP_MODE ?? "demo").trim().toLowerCase();

export const APP_MODE: AppMode = rawMode === "live" ? "live" : "demo";
export const IS_LIVE_MODE = APP_MODE === "live";
export const IS_DEMO_MODE = APP_MODE === "demo";

// Presenter tools flag. In Demo Mode the presenter cast is always available (the
// demo needs it); in Live Mode it is hidden unless this is explicitly "true".
export const SHOW_PRESENTER_TOOLS_FLAG = process.env.NEXT_PUBLIC_SHOW_PRESENTER_TOOLS === "true";
export const PRESENTER_TOOLS_VISIBLE = IS_DEMO_MODE || SHOW_PRESENTER_TOOLS_FLAG;

// The live pipeline (real backend planning/booking) — required for a real plan in
// Live Mode. Re-exported here so mode logic reads from one place.
export const ENABLE_LIVE_PIPELINE = process.env.NEXT_PUBLIC_ENABLE_LIVE_PIPELINE === "true";

// Live Mode blocks sample fallbacks; Demo Mode allows them.
export const ALLOW_SAMPLE_FALLBACK = IS_DEMO_MODE;

// Whether the configured backend base URL looks real (not the localhost default).
// Presenter-only diagnostics use this; never contains a secret.
export const CONFIGURED_API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").trim();
export const API_URL_CONFIGURED = CONFIGURED_API_URL.length > 0;

// Mode-aware copy so components don't sprinkle demo/live wording inline.
export const MODE_COPY = {
  // The presenter cast panel.
  presenterToolsTitle: IS_LIVE_MODE ? "Presenter tools" : "Demo Cast",
  presenterToolsSub: IS_LIVE_MODE
    ? "Simulation only — for presenting without live phones."
    : "Running solo? Simulate friends joining from their phones.",
  presenterToolsToggle: IS_LIVE_MODE ? "Presenter tools" : "Demo Cast",
  // The ambient payment-safety banner.
  safetyOkTitle: IS_LIVE_MODE ? "No automatic payments" : "Demo-safe payment mode",
  safetyOkBody: IS_LIVE_MODE
    ? "Creating a room never moves USDC. A live x402 payment runs only when the presenter deliberately triggers it."
    : "No live USDC is spent here — the verified x402 receipt is shown as proof.",
} as const;
