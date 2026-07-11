import type { NextConfig } from "next";

const apiBase = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

// P3.1 — production surface guarding. The live app is ONE route: `/` (GroupHub,
// `/?room=ORK-XXXX` presenter, `/?room=ORK-XXXX&member=P-001` participant). The
// pages below are pre-refactor leftovers and are redirected to `/` so a production
// user can never land on a half-built screen. The files are left in place
// (redirect, not delete) so no stray import breaks.
const ORPHAN_ROUTES = ["/create-event", "/settled", "/split-detail"];
const ORPHAN_DYNAMIC = ["/event/:id", "/negotiation/:id", "/preferences/:id"];

// Design-system gallery pages — developer-only. Hidden (redirected to `/`) unless
// NEXT_PUBLIC_SHOW_DEV_GALLERY === "true" at BUILD time (local dev only). This adds
// NO new *required* env var: unset — or any value other than "true" — is the safe
// default and redirects the gallery away.
const GALLERY_ROUTES = [
  "/avatars",
  "/badges",
  "/buttons",
  "/cards",
  "/inputs",
  "/navigation",
  "/typography",
];

const showDevGallery = process.env.NEXT_PUBLIC_SHOW_DEV_GALLERY === "true";

const nextConfig: NextConfig = {
  async redirects() {
    const toRoot = (source: string) => ({ source, destination: "/", permanent: false });
    const orphans = [...ORPHAN_ROUTES.map(toRoot), ...ORPHAN_DYNAMIC.map(toRoot)];
    const gallery = showDevGallery ? [] : GALLERY_ROUTES.map(toRoot);
    return [...orphans, ...gallery];
  },
  async rewrites() {
    return [
      {
        source: "/orkestr-api/:path*",
        destination: `${apiBase}/:path*`,
      },
    ];
  },
};

export default nextConfig;
