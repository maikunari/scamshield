"use client";

import { useEffect } from "react";

/** Registers the service worker in production builds (skipped in `next dev` to avoid stale caches). */
export function PwaSetup() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* offline shell is a nicety; the app works without it */
    });
  }, []);
  return null;
}
