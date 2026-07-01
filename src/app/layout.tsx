import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Orkestr — Plan dinner without the group chat chaos",
  description:
    "Friends set their constraints, Orkestr's AI agents find the compromise, book it, and prove payment with x402 USDC on Base.",
  applicationName: "Orkestr",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "Orkestr",
    statusBarStyle: "black-translucent",
  },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  themeColor: "#070A12",
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
