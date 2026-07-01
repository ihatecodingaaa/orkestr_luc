import type { MetadataRoute } from "next";

// Mobile-first PWA-style positioning: friends open a room link on their phones,
// no app install needed. Kept lightweight — no service worker / offline support.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Orkestr — plan dinner without the group chat chaos",
    short_name: "Orkestr",
    description:
      "Friends set their constraints, Orkestr's AI agents find the compromise, book it, and prove payment with x402 USDC on Base.",
    start_url: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#FFF9E8",
    theme_color: "#FFF9E8",
    categories: ["food", "social", "productivity"],
    icons: [{ src: "/favicon.ico", sizes: "any", type: "image/x-icon" }],
  };
}
