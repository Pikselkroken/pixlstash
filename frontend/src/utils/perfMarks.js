/**
 * Minimal local startup/interaction timing on top of the native User Timing
 * API (`performance.mark` / `performance.measure`). Marks show up in
 * devtools' Performance panel; `markEnd` also logs the duration to the
 * console. Dev-only (a no-op in a production build) and never sent
 * anywhere — this is a diagnostic aid, not the consent-gated telemetry
 * pipeline in `./telemetryPayload.js`. Whether any of this ships as opt-in
 * aggregate telemetry is a separate product decision.
 */
const DEV = import.meta.env.DEV;

/** Start timing `name`. Pair with {@link markEnd}. */
export function markStart(name) {
  if (!DEV || typeof performance === "undefined") return;
  performance.mark(`${name}-start`);
}

/** Finish timing `name` and log its duration. No-op if `markStart` wasn't called. */
export function markEnd(name) {
  if (!DEV || typeof performance === "undefined") return;
  try {
    performance.mark(`${name}-end`);
    const { duration } = performance.measure(name, `${name}-start`, `${name}-end`);
    console.debug(`[perf] ${name}: ${duration.toFixed(1)}ms`);
  } catch {
    // The start mark is missing (e.g. a hot-reload landed mid-flight) —
    // nothing meaningful to report.
  }
}

/**
 * Wrap a click/interaction handler so its duration is timed under `name`.
 * Works for both sync and async (promise-returning) handlers.
 */
export function measureInteraction(name, fn) {
  return function measured(...args) {
    markStart(name);
    const result = fn.apply(this, args);
    if (result && typeof result.then === "function") {
      return result.finally(() => markEnd(name));
    }
    markEnd(name);
    return result;
  };
}
