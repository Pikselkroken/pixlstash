import { defineStore } from "pinia";
import { ref } from "vue";

// Central notice/snackbar queue (frontend_refactoring_plan.md §3 Phase 2;
// issue #459 alignment rule 2). One store owns every transient notice so
// failures surface consistently instead of vanishing into per-component catch
// blocks (per the repo's "no silent failures" rule).
//
// SCAFFOLD STATUS: this is the state machine only. The single visible host that
// renders the queue (a snackbar/toast surface) is deliberately NOT built here —
// that is design work owned by the maintainer's design pass. Until the host
// mounts, pushing a notice is a harmless no-op on screen; the store is already
// unit-tested and ready for adoption so call sites can migrate now (see the
// first adoption in SmartScoreSection / ProjectEditor).

const LEVELS = new Set(["info", "success", "warning", "error"]);

// Default auto-dismiss (ms) per level. Errors persist until dismissed so a
// failure is never lost; timeouts can be overridden per push.
const DEFAULT_TIMEOUTS = {
  info: 4000,
  success: 3000,
  warning: 6000,
  error: 0, // 0 = sticky (manual dismiss)
};

export const useNoticeStore = defineStore("notice", () => {
  // Queue of active notices: { id, level, text, timeout, action }.
  const notices = ref([]);

  let nextId = 1;
  const timers = new Map();

  function clearTimer(id) {
    const handle = timers.get(id);
    if (handle != null) {
      clearTimeout(handle);
      timers.delete(id);
    }
  }

  /**
   * Push a notice onto the queue.
   * @param {Object} opts
   * @param {'info'|'success'|'warning'|'error'} [opts.level='info']
   * @param {string} opts.text - the message to show.
   * @param {number} [opts.timeout] - auto-dismiss ms; 0 = sticky. Defaults per level.
   * @param {{label:string, handler:Function}} [opts.action] - optional action button.
   * @returns {number} the notice id (for manual dismiss).
   */
  function push({ level = "info", text = "", timeout, action = null } = {}) {
    const safeLevel = LEVELS.has(level) ? level : "info";
    const id = nextId++;
    const resolvedTimeout =
      typeof timeout === "number" ? timeout : DEFAULT_TIMEOUTS[safeLevel];
    notices.value.push({
      id,
      level: safeLevel,
      text: String(text ?? ""),
      timeout: resolvedTimeout,
      action,
    });
    if (resolvedTimeout > 0 && typeof setTimeout === "function") {
      const handle = setTimeout(() => dismiss(id), resolvedTimeout);
      timers.set(id, handle);
    }
    return id;
  }

  // Level convenience wrappers — the common call shape at adoption sites.
  const info = (text, opts = {}) => push({ ...opts, level: "info", text });
  const success = (text, opts = {}) => push({ ...opts, level: "success", text });
  const warning = (text, opts = {}) => push({ ...opts, level: "warning", text });
  const error = (text, opts = {}) => push({ ...opts, level: "error", text });

  /** Dismiss a single notice by id. */
  function dismiss(id) {
    clearTimer(id);
    const idx = notices.value.findIndex((n) => n.id === id);
    if (idx !== -1) notices.value.splice(idx, 1);
  }

  /** Clear every active notice. */
  function clear() {
    for (const n of notices.value) clearTimer(n.id);
    notices.value = [];
  }

  return { notices, push, info, success, warning, error, dismiss, clear };
});
