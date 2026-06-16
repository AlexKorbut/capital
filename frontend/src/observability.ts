// Frontend error/perf monitoring. Opt-in: a no-op unless VITE_SENTRY_DSN is set,
// so dev stays quiet by default. Prod injects the DSN at build time (Vite env).
import * as Sentry from "@sentry/react";

export function initObservability(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn) return;

  Sentry.init({
    dsn,
    environment: import.meta.env.MODE,
    release: import.meta.env.VITE_GIT_SHA,
    integrations: [Sentry.browserTracingIntegration()],
    // Trace a slice of navigations; tune per traffic. No session replay (privacy).
    tracesSampleRate: 0.1,
    // Never capture portfolio values typed into inputs.
    sendDefaultPii: false,
  });
}

// Privacy-friendly, cookieless analytics (Plausible). Opt-in: injects the
// script tag only when VITE_PLAUSIBLE_DOMAIN is set, so dev/self-hosters stay
// tracking-free by default. No PII, no cookies — aligns with the privacy promise.
export function initAnalytics(): void {
  const domain = import.meta.env.VITE_PLAUSIBLE_DOMAIN;
  if (!domain || typeof document === "undefined") return;
  const src = import.meta.env.VITE_PLAUSIBLE_SRC ?? "https://plausible.io/js/script.js";
  if (document.querySelector(`script[data-domain="${domain}"]`)) return;
  const el = document.createElement("script");
  el.defer = true;
  el.setAttribute("data-domain", domain);
  el.src = src;
  document.head.appendChild(el);
}

export { Sentry };
