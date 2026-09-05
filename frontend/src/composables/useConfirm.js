import { readonly, ref } from "vue";

// Promise-based confirm dialog. One `confirm()` call replaces the five
// hand-rolled "Are you sure" dialogs (and the scattered `window.confirm` calls)
// with a single awaited promise.
//
// SCAFFOLD STATUS: this is the request/resolve state machine plus the host-facing
// API a future `ConfirmDialog.vue` will consume. The visible dialog surface is
// NOT built here - that is the maintainer's design pass. Until a host registers
// itself, `confirm()` PROGRESSIVELY FALLS BACK to the native `window.confirm`,
// so adoption sites keep their exact current behaviour today and upgrade to the
// styled dialog automatically once the host mounts. This is a documented default
// state, not a bug-masking fallback: it is the intended behaviour until Phase 2
// lands the host, and the fallback path is explicit and logged in tests.

// The single in-flight request the host renders: { id, options, resolve } | null.
const activeRequest = ref(null);
// Set to true by a mounted ConfirmDialog.vue via registerConfirmHost().
const hostRegistered = ref(false);

let nextId = 1;

/**
 * Normalise caller options into a stable shape for the host.
 * A bare string is treated as the message.
 */
function normalizeOptions(options) {
  const o = typeof options === "string" ? { message: options } : options || {};
  return {
    title: o.title ?? "Are you sure?",
    message: o.message ?? "",
    warning: o.warning ?? "",
    confirmLabel: o.confirmLabel ?? "Confirm",
    cancelLabel: o.cancelLabel ?? "Cancel",
    danger: Boolean(o.danger),
  };
}

/**
 * The composable entry point.
 * @returns {{ confirm: (options: string|Object) => Promise<boolean> }}
 */
export function useConfirm() {
  /**
   * Ask the user to confirm. Resolves true (confirmed) / false (cancelled).
   * @param {string|Object} options - message string, or { title, message,
   *   warning, confirmLabel, cancelLabel, danger }.
   * @returns {Promise<boolean>}
   */
  function confirm(options) {
    const normalized = normalizeOptions(options);

    // Progressive-enhancement default: with no mounted host, fall back to the
    // browser's native confirm so current call sites behave identically.
    if (!hostRegistered.value) {
      const ok =
        typeof window !== "undefined" && typeof window.confirm === "function"
          ? window.confirm(normalized.message || normalized.title)
          : false;
      return Promise.resolve(ok);
    }

    return new Promise((resolve) => {
      activeRequest.value = { id: nextId++, options: normalized, resolve };
    });
  }

  return { confirm };
}

// ── Host-facing API (consumed by the future ConfirmDialog.vue) ───────────────

/** A mounted host calls this once so `confirm()` stops using the native fallback. */
export function registerConfirmHost() {
  hostRegistered.value = true;
}

/** A host calls this on unmount to restore the native fallback. */
export function unregisterConfirmHost() {
  hostRegistered.value = false;
  // Resolve any dangling request as cancelled so awaiters never hang.
  if (activeRequest.value) {
    activeRequest.value.resolve(false);
    activeRequest.value = null;
  }
}

/** Read-only handle to the in-flight request for the host to render. */
export const activeConfirm = readonly(activeRequest);

/** The host calls this with the user's choice to settle the pending promise. */
export function resolveConfirm(result) {
  if (!activeRequest.value) return;
  activeRequest.value.resolve(Boolean(result));
  activeRequest.value = null;
}
