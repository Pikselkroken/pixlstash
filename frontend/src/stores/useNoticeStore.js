import { defineStore } from "pinia";
import { computed, ref } from "vue";

// Central notice/snackbar queue (issue #459 alignment rule 2). One store owns
// every transient notice so failures surface consistently instead of vanishing
// into per-component catch blocks (per the repo's "no silent failures" rule).
//
// The visible host is `components/widgets/NoticeHost.vue`, built to
// `docs/design/notice-surface.md`. This file implements §9 of that spec:
//
//   §9.1 coalescing key   - repeats collapse into one card with a ×N count
//   §9.2 cap + pending    - only `maxVisible` render; timers start on PROMOTION,
//                           never at push time, so a queued notice can't expire
//                           unseen (the very bug this store exists to prevent)
//   §9.3 pause/resume     - WCAG 2.2.1, with remaining-time bookkeeping
//   §9.4 action contract  - invoking an action dismisses unless it returns
//                           false; any notice with an action is sticky
//   §9.5 empty text       - refused and logged, not rendered as a blank card
//
// Everything the store deliberately does NOT grow: a title, a second action, or
// per-notice styling. Those break the spec's one-sentence rule and turn the
// surface into a second dialog system.

const LEVELS = new Set(["info", "success", "warning", "error"]);

// Default auto-dismiss (ms) per level (spec §6). Errors persist until dismissed
// so a failure is never lost; timeouts can be overridden per push.
export const DEFAULT_TIMEOUTS = {
  info: 4000,
  success: 3000,
  warning: 6000,
  error: 0, // 0 = sticky (manual dismiss)
};

// Spec §5: beyond this the surface stops being a notification and becomes a
// wall. The host drops it to 2 below 600px via `setMaxVisible`.
const DEFAULT_MAX_VISIBLE = 3;

// Spec §6 rule 2 - reading-time floor. A 110-character success message does not
// get 3 seconds.
const READ_TIME_BASE_MS = 2000;
const READ_TIME_PER_CHAR_MS = 60;
const READ_TIME_CEILING_MS = 12000;

/**
 * Effective auto-dismiss for a notice, in ms. `0` means sticky.
 * Exported for unit tests and for callers that need to reason about it.
 *
 * @param {Object} options
 * @param {number} options.baseTimeout - the level default or an explicit override.
 * @param {string} options.text - the message (drives the reading-time floor).
 * @param {boolean} options.hasAction - an action makes a notice sticky by DEFAULT.
 * @param {boolean} options.explicit - `baseTimeout` came from the caller, not from
 *   the level default. An explicit window outranks the action rule and is not
 *   capped by the reading-time ceiling - see below.
 * @returns {number} milliseconds, or 0 for sticky.
 */
export function resolveTimeout({
  baseTimeout,
  text = "",
  hasAction = false,
  explicit = false,
}) {
  // An explicit 0 still means sticky, whoever asked for it.
  if (explicit && (!baseTimeout || baseTimeout <= 0)) return 0;
  // §6 rule 1: a 3s window to hit "Undo" fails WCAG 2.2.1 and common sense, so
  // an action defaults to sticky. It is a DEFAULT, not a law: a card whose
  // sentence is about the current state should not outlive that state, and a
  // long window with the hover/focus pause (§9.3) satisfies 2.2.1 on its own.
  // The caller has to say so deliberately.
  if (hasAction && !explicit) return 0;
  if (!baseTimeout || baseTimeout <= 0) return 0;
  // The ceiling caps the COMPUTED reading time. It must not cap a window the
  // caller chose on purpose - that is how a deliberate 30s instruction card
  // silently became 12s.
  const readingTime = Math.min(
    READ_TIME_CEILING_MS,
    READ_TIME_BASE_MS + READ_TIME_PER_CHAR_MS * text.length,
  );
  return Math.max(baseTimeout, readingTime);
}

export const useNoticeStore = defineStore("notice", () => {
  // Every live notice, in push order. Only the first `maxVisible` are rendered;
  // the rest are pending (see `visible` / `pending`).
  const notices = ref([]);
  const maxVisible = ref(DEFAULT_MAX_VISIBLE);

  let nextId = 1;
  // id → { handle, remaining, startedAt, paused }
  const timers = new Map();
  // Global pause (document.hidden). Applied on top of per-notice pauses.
  const globallyPaused = ref(false);

  /** The notices the host renders - newest last (nearest the bottom edge). */
  const visible = computed(() => notices.value.slice(0, maxVisible.value));
  /** Notices waiting for a slot. Their timers have not started. */
  const pending = computed(() => notices.value.slice(maxVisible.value));

  function clearTimer(id) {
    const entry = timers.get(id);
    if (entry?.handle != null) clearTimeout(entry.handle);
    timers.delete(id);
  }

  function isVisible(id) {
    return visible.value.some((n) => n.id === id);
  }

  /**
   * Start (or restart) a visible notice's countdown from its banked `remaining`.
   * No-op for sticky notices, paused notices, and notices that aren't visible.
   */
  function startTimer(id) {
    const notice = notices.value.find((n) => n.id === id);
    if (!notice || notice.timeout <= 0) return;
    if (!isVisible(id)) return; // §9.2 - never run a timer off-screen.
    const entry = timers.get(id) ?? {
      handle: null,
      remaining: notice.timeout,
      startedAt: 0,
      paused: false,
    };
    if (entry.paused || globallyPaused.value) {
      entry.handle = null;
      timers.set(id, entry);
      return;
    }
    if (entry.handle != null) clearTimeout(entry.handle);
    entry.startedAt = Date.now();
    entry.handle = setTimeout(() => dismiss(id), entry.remaining);
    timers.set(id, entry);
  }

  /** Bank the time a running countdown has left and stop it. */
  function freeze(id) {
    const entry = timers.get(id);
    if (!entry || entry.handle == null) return entry;
    clearTimeout(entry.handle);
    const elapsed = Date.now() - entry.startedAt;
    entry.remaining = Math.max(0, entry.remaining - elapsed);
    entry.handle = null;
    timers.set(id, entry);
    return entry;
  }

  /** Freeze a notice's countdown (hover / focus-within). WCAG 2.2.1. */
  function pause(id) {
    const entry = freeze(id) ?? timers.get(id);
    if (!entry) return;
    entry.paused = true;
    timers.set(id, entry);
  }

  /** Resume a paused countdown from where it stopped. */
  function resume(id) {
    const entry = timers.get(id);
    if (!entry || !entry.paused) return;
    entry.paused = false;
    timers.set(id, entry);
    startTimer(id);
  }

  /** Pause every countdown (the tab went hidden). */
  function pauseAll() {
    if (globallyPaused.value) return;
    globallyPaused.value = true;
    for (const notice of notices.value) freeze(notice.id);
  }

  /** Resume every countdown not individually paused (hover / focus). */
  function resumeAll() {
    if (!globallyPaused.value) return;
    globallyPaused.value = false;
    for (const notice of visible.value) {
      const entry = timers.get(notice.id);
      if (entry && !entry.paused) startTimer(notice.id);
    }
  }

  /**
   * Reconcile every notice's timer against the visible window. Called after any
   * change to that window, in BOTH directions (§9.2):
   *
   *   promoted  → start the countdown at the moment it becomes visible;
   *   demoted   → stop the countdown and restore its FULL window.
   *
   * The demotion half matters because the cap is not constant: `NoticeHost`
   * drops it from 3 to 2 when the viewport crosses 600px, so a resize or a
   * tablet rotation pushes a visible notice back into the queue. Leaving its
   * timer running would expire it off-screen - §9.2's bug in the other
   * direction. The window is RESET rather than banked because a notice that was
   * pulled off-screen part-way through was never actually read.
   */
  function reconcileTimers() {
    const visibleIds = new Set(visible.value.map((n) => n.id));
    for (const notice of notices.value) {
      if (notice.timeout <= 0) continue;
      const entry = timers.get(notice.id);

      if (visibleIds.has(notice.id)) {
        if (!entry) {
          timers.set(notice.id, {
            handle: null,
            remaining: notice.timeout,
            startedAt: 0,
            paused: false,
          });
          startTimer(notice.id);
        } else if (
          entry.handle == null &&
          !entry.paused &&
          !globallyPaused.value
        ) {
          startTimer(notice.id);
        }
        continue;
      }

      // Off-screen: never let a countdown run where nobody can see it.
      if (!entry) continue;
      if (entry.handle != null) clearTimeout(entry.handle);
      entry.handle = null;
      entry.startedAt = 0;
      entry.remaining = notice.timeout;
      timers.set(notice.id, entry);
    }
  }

  /**
   * Spec §5 "errors outrank": an error is never queued behind a success.
   *
   * Appending the error and freeing a slot does NOT achieve that - `notices`
   * holds the pending queue too, so the freed slot goes to the next notice in
   * push order while the error, appended last, stays queued. The error has to be
   * placed INTO the visible window directly.
   *
   * The displaced notice is DEMOTED to the front of the pending queue, not
   * dismissed. Spec §5's wording is "the oldest non-error is dismissed
   * immediately to make room", but destroying a message is not what buys the
   * room here - the insert does. Demotion satisfies the same rule (the error is
   * visible now) while honouring §5's other half, "overflow waits in the store".
   * Losing a bystander message would be a second, quieter version of exactly the
   * bug this surface exists to fix.
   *
   * @param {Object} notice - the error being pushed.
   * @returns {boolean} true when the error was placed in the visible window.
   */
  function insertErrorIntoVisibleWindow(notice) {
    const cap = maxVisible.value;
    const idx = notices.value.findIndex(
      (n, i) => i < cap && n.level !== "error",
    );
    // The visible window is all errors: this one waits its turn behind them,
    // which §5 permits - the rule is that an error never queues behind a
    // *success*.
    if (idx === -1) return false;

    const [displaced] = notices.value.splice(idx, 1);
    // Newest-last within the visible window, matching push order elsewhere.
    notices.value.splice(cap - 1, 0, notice);
    // The bystander becomes the first thing shown when a slot next frees up.
    notices.value.splice(cap, 0, displaced);
    return true;
  }

  /**
   * Push a notice onto the queue.
   *
   * @param {Object} opts
   * @param {'info'|'success'|'warning'|'error'} [opts.level='info']
   * @param {string} opts.text - the message. Empty text is refused (§9.5).
   * @param {number} [opts.timeout] - auto-dismiss ms; 0 = sticky. Defaults per
   *   level, then raised by the reading-time floor. An `action` forces 0 unless
   *   this is passed explicitly, which overrides that default (§6 rule 1).
   * @param {{label: string, handler: Function}} [opts.action] - single optional
   *   action. Invoking it dismisses the notice unless the handler returns false.
   * @param {string} [opts.key] - coalescing key (§9.1). Pushing with a key that
   *   is already live updates that notice and increments its `count` instead of
   *   appending a second card. This is what stops a bulk operation over 50
   *   pictures pushing 50 sticky error cards.
   * @returns {number|null} the notice id, or null when the push was refused.
   */
  function push({
    level = "info",
    text = "",
    timeout,
    action = null,
    key,
  } = {}) {
    const safeLevel = LEVELS.has(level) ? level : "info";
    const message = String(text ?? "").trim();
    if (!message) {
      // §9.5 - a blank card tells the user nothing and hides the real bug at
      // the call site, so refuse it loudly rather than render it.
      console.warn("useNoticeStore.push() refused a notice with empty text.", {
        level: safeLevel,
        key,
      });
      return null;
    }

    const hasAction = Boolean(action && typeof action.handler === "function");
    const explicit = typeof timeout === "number";
    const baseTimeout = explicit ? timeout : DEFAULT_TIMEOUTS[safeLevel];
    const resolved = resolveTimeout({
      baseTimeout,
      text: message,
      hasAction,
      explicit,
    });

    // §9.1 - coalesce onto a live notice with the same key.
    if (key != null) {
      const existing = notices.value.find((n) => n.key === key);
      if (existing) {
        existing.count += 1;
        existing.text = message;
        existing.level = safeLevel;
        existing.timeout = resolved;
        existing.action = hasAction ? action : null;
        // Restart the countdown: the event just happened again, so the reading
        // window starts over rather than expiring mid-burst.
        //
        // The per-notice pause MUST survive this. `clearTimer` drops the whole
        // entry including `paused`, so a repeat arriving while the cursor is on
        // the card used to hand it a fresh countdown and dismiss it out from
        // under the user - WCAG 2.2.1. Carry the flag across. (The global
        // `document.hidden` pause was never affected: `startTimer` re-checks
        // `globallyPaused` independently.)
        const wasPaused = timers.get(existing.id)?.paused === true;
        clearTimer(existing.id);
        if (resolved > 0) {
          timers.set(existing.id, {
            handle: null,
            remaining: resolved,
            startedAt: 0,
            paused: wasPaused,
          });
        }
        reconcileTimers();
        return existing.id;
      }
    }

    const id = nextId++;
    const notice = {
      id,
      level: safeLevel,
      text: message,
      timeout: resolved,
      action: hasAction ? action : null,
      key: key ?? null,
      count: 1,
    };

    // An error takes a visible slot rather than queueing behind a success.
    const placed =
      safeLevel === "error" &&
      notices.value.length >= maxVisible.value &&
      insertErrorIntoVisibleWindow(notice);
    if (!placed) notices.value.push(notice);

    reconcileTimers();
    return id;
  }

  // Level convenience wrappers - the common call shape at adoption sites.
  const info = (text, opts = {}) => push({ ...opts, level: "info", text });
  const success = (text, opts = {}) =>
    push({ ...opts, level: "success", text });
  const warning = (text, opts = {}) =>
    push({ ...opts, level: "warning", text });
  const error = (text, opts = {}) => push({ ...opts, level: "error", text });

  /** Dismiss a single notice by id. */
  function dismiss(id) {
    clearTimer(id);
    const idx = notices.value.findIndex((n) => n.id === id);
    if (idx !== -1) notices.value.splice(idx, 1);
    // A slot may have opened: promote and start the newly-visible timers.
    reconcileTimers();
  }

  /**
   * Dismiss every live notice carrying `key` (§9.6 - scoped notices).
   *
   * Coalescing already guarantees at most one live notice per key, but a scope
   * usually owns a small FAMILY of keys (a card and its follow-up), and the
   * caller invalidating that scope must be able to retire them by name without
   * tracking ids it never saw. Unknown keys are a silent no-op: the notice may
   * already have timed out or been dismissed by hand, and that is the normal
   * case, not an error.
   *
   * @param {string} key - the coalescing key to retire.
   * @returns {number} how many notices were dismissed.
   */
  function dismissByKey(key) {
    if (key == null) return 0;
    const doomed = notices.value.filter((n) => n.key === key).map((n) => n.id);
    for (const id of doomed) dismiss(id);
    return doomed.length;
  }

  /**
   * Invoke a notice's action (§9.4). The notice is dismissed afterwards unless
   * the handler explicitly returns `false`, which lets a handler keep the card
   * up (e.g. to report that the retry also failed).
   * @param {number} id
   */
  function invokeAction(id) {
    const notice = notices.value.find((n) => n.id === id);
    const handler = notice?.action?.handler;
    if (typeof handler !== "function") return;
    let keepOpen = false;
    try {
      keepOpen = handler() === false;
    } catch (err) {
      // The action's own failure must not strand the card on screen.
      console.error("A notice action handler threw.", err);
    }
    if (!keepOpen) dismiss(id);
  }

  /** Clear every active notice. */
  function clear() {
    for (const n of notices.value) clearTimer(n.id);
    notices.value = [];
  }

  /** Host reports its cap (3, or 2 below 600px - spec §5). */
  function setMaxVisible(value) {
    const next = Number(value);
    if (!Number.isFinite(next) || next < 1) return;
    maxVisible.value = Math.floor(next);
    reconcileTimers();
  }

  return {
    // state
    notices,
    maxVisible,
    // computed
    visible,
    pending,
    // actions
    push,
    info,
    success,
    warning,
    error,
    dismiss,
    dismissByKey,
    invokeAction,
    clear,
    pause,
    resume,
    pauseAll,
    resumeAll,
    setMaxVisible,
  };
});
