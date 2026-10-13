import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { PwaSetup } from "@/components/PwaSetup";

// Atkinson Hyperlegible Next (Braille Institute, SIL OFL 1.1; licence in app/fonts/OFL.txt):
// a typeface designed for readers with low vision. Self-hosted so the export builds offline.
const atkinson = localFont({
  src: [
    { path: "./fonts/AtkinsonHyperlegibleNext-latin.woff2", weight: "200 800", style: "normal" },
    { path: "./fonts/AtkinsonHyperlegibleNext-latin-ext.woff2", weight: "200 800", style: "normal" },
  ],
  variable: "--font-atkinson",
  display: "swap",
  fallback: ["system-ui", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
});

export const metadata: Metadata = {
  title: "ScamShield",
  description: "Paste or share a suspicious message and get a clear answer: Scam, Careful or Safe.",
  applicationName: "ScamShield",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, statusBarStyle: "default", title: "ScamShield" },
  icons: {
    icon: [{ url: "/icons/icon.svg", type: "image/svg+xml" }, { url: "/icons/icon-192.png", sizes: "192x192" }],
    apple: "/icons/apple-touch-icon.png",
  },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#1F3A5F",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={atkinson.variable}>
      <body>
        {children}
        <PwaSetup />
      </body>
    </html>
  );
}
