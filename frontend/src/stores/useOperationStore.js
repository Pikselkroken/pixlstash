// useOperationStore.js — the undo/redo stack, mirrored from the backend's
// append-only operation log (backend_architecture.md §21).
//
// The server owns the history. This store is a read model over it plus the
// transient "action receipt" state that narrates what just happened:
//
//   • `operations`  the newest 50 rows of GET /operations, newest FIRST.
//   • `canUndo` / `canRedo` / `nextUndo` / `nextRedo` from GET /operations/undo-state,
//     which is what enables and labels the toolbar control.
//   • `receipt`     at most one live receipt. Never two — the newest replaces
//     the current one in place (design rule), which is why this is a single ref
//     and not a queue like `useNoticeStore`.
//
// Origin discipline (integration_architecture.md §8.1, pitfall 14). Every
// operation row carries the `origin_client_id` of the tab that caused it. The
// receipt narrates THIS client's actions only: an operation that arrives from
// another tab or from a background job updates the stack silently, because a
// pill offering "Undo" for something the user did not just do is a trap. The id
// is used for echo-matching and nothing else — it is attacker-controllable and
// never an access decision.
//
// Undo is OWNER_ONLY on the server, so a share/read-only session never calls
// these endpoints and never renders the control.

import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { isReadOnly } from "../utils/apiClient";
import {
  getUndoState,
  listOperations,
  redoOperation,
  undoBatch,
  undoLastOperation,
  undoOperation,
} from "../api/operations";
import { useNoticeStore } from "./useNoticeStore";
import { useWsStore } from "./useWsStore";

/** How many history steps the popover shows (design rule: capped at 50). */
export const HISTORY_LIMIT = 50;

/** Receipt dwell, in ms. Destructive actions get longer to catch the mistake. */
export const RECEIPT_MS = 5000;
export const DESTRUCTIVE_RECEIPT_MS = 8000;

/**
 * `op_type` → mdi glyph. Exact matches first; anything unknown falls through
 * `OP_ICON_RULES` and finally to `FALLBACK_ICON`.
 *
 * Deliberately generic: op types are added by whichever backend lane needs
 * them (the scrapheap-move lane lands its own alongside these), and a history
 * row for an unrecognised type must still render as a sensible step rather
 * than a blank or a crash.
 */
export const OP_ICONS = {
  "pictures.tags.add": "mdi-tag-plus-outline",
  "pictures.tags.remove": "mdi-tag-minus-outline",
  "pictures.tags.remove_all": "mdi-tag-off-outline",
  "pictures.tags.clear": "mdi-tag-off-outline",
  "pictures.tags.replace": "mdi-tag-multiple-outline",
  "pictures.score": "mdi-star-outline",
  "pictures.fields": "mdi-pencil-outline",
  "pictures.project": "mdi-folder-outline",
  "characters.assign": "mdi-account-check-outline",
  "characters.unassign": "mdi-account-off-outline",
  "picture_sets.members.add": "mdi-playlist-plus",
  "picture_sets.members.remove": "mdi-playlist-minus",
  "picture_sets.members.replace": "mdi-playlist-edit-outline",
  "stacks.create": "mdi-layers-outline",
  "stacks.dissolve": "mdi-layers-off-outline",
};

/** Substring rules applied when `OP_ICONS` has no exact entry. Order matters. */
const OP_ICON_RULES = [
  [/scrapheap|trash|delete/, "mdi-trash-can-outline"],
  [/restore|recover/, "mdi-backup-restore"],
  [/tag/, "mdi-tag-outline"],
  [/score|rating/, "mdi-star-outline"],
  [/character|face/, "mdi-account-outline"],
  [/set|collection/, "mdi-playlist-edit-outline"],
  [/stack/, "mdi-layers-outline"],
  [/project/, "mdi-folder-outline"],
  [/description|caption/, "mdi-text-box-outline"],
];

const FALLBACK_ICON = "mdi-history";

/**
 * Op types whose receipt holds for 8s instead of 5s. Substring-matched for the
 * same reason as the icons: a scrapheap op type this build has never seen must
 * still get the longer window, because that is the one you most want to catch.
 */
const DESTRUCTIVE_RULES = [
  /scrapheap/,
  /delete/,
  /remove_all/,
  /\.clear$/,
  /dissolve/,
];

/**
 * The mdi glyph for an operation type.
 * @param {string} opType - dotted verb, e.g. `"pictures.tags.add"`.
 * @returns {string} an mdi class name, never empty.
 */
export function iconForOpType(opType) {
  const key = String(opType ?? "");
  if (OP_ICONS[key]) return OP_ICONS[key];
  for (const [pattern, icon] of OP_ICON_RULES) {
    if (pattern.test(key)) return icon;
  }
  return FALLBACK_ICON;
}

/**
 * Is this operation destructive enough to earn the longer receipt window?
 * @param {string} opType
 * @returns {boolean}
 */
export function isDestructiveOpType(opType) {
  const key = String(opType ?? "");
  return DESTRUCTIVE_RULES.some((pattern) => pattern.test(key));
}

/**
 * Human label for an operation. The server's `summary` is the single source of
 * truth for the wording; the target count is appended so one glance answers
 * "how much would this undo?" — the design's `Add tag "portrait" · 12` shape.
 *
 * Falls back to the dotted `op_type`, de-dotted, when a lane records a row
 * without a summary: an unlabelled step is still better than a blank row.
 *
 * @param {Object} operation - an operation row from the API.
 * @returns {string}
 */
export function summarizeOperation(operation) {
  if (!operation) return "";
  const raw = String(operation.summary ?? "").trim();
  const base = raw || humanizeOpType(operation.op_type);
  const count = Number(operation.target_count);
  if (Number.isFinite(count) && count > 1) return `${base} · ${count}`;
  return base;
}

function humanizeOpType(opType) {
  const key = String(opType ?? "").trim();
  if (!key) return "Change";
  const words = key.replace(/[._]/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Local time-of-day label for a history row, matching the design's `14:02`.
 * @param {string} createdAt - ISO timestamp from the API (UTC, naive).
 * @returns {string} `HH:MM`, or `""` when unparseable.
 */
export function formatOperationTime(createdAt) {
  if (!createdAt) return "";
  // The API serialises naive UTC datetimes; without the marker the browser
  // reads them as local and every row is off by the UTC offset.
  const text = String(createdAt);
  const iso = /(Z|[+-]\d{2}:?\d{2})$/.test(text) ? text : `${text}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export const useOperationStore = defineStore("operation", () => {
  // ── Server state ─────────────────────────────────────────────────────────
  /** The newest rows of the log, newest FIRST (the order the API returns). */
  const operations = ref([]);
  const canUndo = ref(false);
  const canRedo = ref(false);
  const nextUndo = ref(null);
  const nextRedo = ref(null);
  /** True while an undo/redo round-trip is in flight — the control disables. */
  const busy = ref(false);
  /** True once the first successful refresh has landed. */
  const loaded = ref(false);

  // ── Receipt state ────────────────────────────────────────────────────────
  // At most one. `key` increments on every raise so the component can re-run
  // its enter transition and restart the drain even when the pill is replaced
  // in place rather than unmounted.
  const receipt = ref(null);
  let receiptKey = 0;
  let receiptTimer = null;
  let receiptRemaining = 0;
  let receiptStartedAt = 0;
  let receiptPaused = false;

  // Highest operation id seen by a completed refresh. Used to tell a genuinely
  // new operation from a re-read of the same history — an id we have already
  // seen must never raise a second receipt.
  let highWaterMark = null;

  // Coalesce overlapping refreshes: WS bursts collapse into one in-flight
  // request plus at most one trailing refetch.
  let inFlight = false;
  let refetchQueued = false;

  // ── Derived history ──────────────────────────────────────────────────────
  /**
   * The undo stack, newest first, capped at `HISTORY_LIMIT`. Only `applied`
   * rows: an `undone` row belongs to the redo side and a `superseded` one was
   * cleared by a later action and can never come back.
   */
  const past = computed(() =>
    operations.value
      .filter((op) => op?.status === "applied")
      .slice(0, HISTORY_LIMIT),
  );

  /**
   * Steps that have been undone and can still be redone — the struck-through
   * rows at the top of the History popover. `superseded` rows are excluded:
   * a new action cleared them, which is exactly when the design says they go.
   */
  const future = computed(() =>
    operations.value.filter((op) => op?.status === "undone"),
  );

  /** How many steps the footer reports. */
  const historyCount = computed(() => past.value.length);

  /** Nothing recorded yet → the toolbar control is disabled, not hidden. */
  const hasHistory = computed(
    () => past.value.length > 0 || future.value.length > 0,
  );

  function myClientId() {
    try {
      return useWsStore().clientId;
    } catch (e) {
      // Pinia not active (a bare unit test of a helper). Not fatal: without an
      // id every operation reads as external, which is the safe direction.
      console.warn("useOperationStore: no ws store for the client id", e);
      return null;
    }
  }

  // ── Receipt lifecycle ────────────────────────────────────────────────────
  function clearReceiptTimer() {
    if (receiptTimer != null) clearTimeout(receiptTimer);
    receiptTimer = null;
  }

  function armReceiptTimer(ms) {
    clearReceiptTimer();
    receiptRemaining = ms;
    receiptStartedAt = Date.now();
    receiptPaused = false;
    receiptTimer = setTimeout(dismissReceipt, ms);
  }

  /**
   * Build the receipt payload for one operation.
   *
   * @param {Object} operation - the operation row being narrated.
   * @param {'did'|'undone'|'blocked'} mode - `did` after the action, `undone`
   *   after reverting it (the pill flips in place and offers Redo), `blocked`
   *   when the operation was recorded for audit but cannot be reversed.
   * @param {number} [steps=1] - how many history steps this receipt covers, so
   *   a multi-step undo says so instead of naming only the newest one.
   * @returns {Object} the receipt.
   */
  function buildReceipt(operation, mode, steps = 1) {
    const opType = operation?.op_type ?? "";
    const destructive = isDestructiveOpType(opType);
    // "+N": how many sibling rows of the same bulk action this step carries.
    // Grouped by batch id, which is the server's own definition of "one user
    // action", rather than by a client-side time window.
    const batchId = operation?.batch_id ?? null;
    const merged = batchId
      ? Math.max(
          0,
          operations.value.filter((op) => op?.batch_id === batchId).length - 1,
        )
      : 0;
    receiptKey += 1;
    return {
      key: receiptKey,
      mode,
      operationId: operation?.id ?? null,
      batchId,
      opType,
      icon: iconForOpType(opType),
      summary: summarizeOperation(operation),
      targetCount: Number(operation?.target_count) || 0,
      mergedCount: merged,
      steps,
      destructive,
      durationMs: destructive ? DESTRUCTIVE_RECEIPT_MS : RECEIPT_MS,
    };
  }

  /**
   * Raise a receipt, replacing any live one in place (never stacked).
   * @param {Object} entry - a payload from {@link buildReceipt}.
   */
  function showReceipt(entry) {
    if (!entry) return;
    receipt.value = entry;
    armReceiptTimer(entry.durationMs);
  }

  /** Retire the live receipt and its countdown. */
  function dismissReceipt() {
    clearReceiptTimer();
    receipt.value = null;
  }

  /**
   * Freeze the countdown (hover / focus-within). WCAG 2.2.1 — the user must be
   * able to read and reach an Undo button without it disappearing.
   */
  function pauseReceipt() {
    if (!receipt.value || receiptPaused || receiptTimer == null) return;
    clearTimeout(receiptTimer);
    receiptTimer = null;
    receiptRemaining = Math.max(
      0,
      receiptRemaining - (Date.now() - receiptStartedAt),
    );
    receiptPaused = true;
  }

  /** Resume a frozen countdown from where it stopped. */
  function resumeReceipt() {
    if (!receipt.value || !receiptPaused) return;
    receiptPaused = false;
    if (receiptRemaining <= 0) {
      dismissReceipt();
      return;
    }
    receiptStartedAt = Date.now();
    receiptTimer = setTimeout(dismissReceipt, receiptRemaining);
  }

  // ── Server reads ─────────────────────────────────────────────────────────
  /**
   * Re-read the log and the undo state.
   *
   * @param {Object} [options]
   * @param {boolean} [options.narrate=true] - raise a receipt when the refresh
   *   reveals a new operation from THIS client. Own undo/redo actions pass
   *   `false` and raise their own receipt, so the two never race.
   * @returns {Promise<void>}
   */
  async function refresh({ narrate = true } = {}) {
    if (isReadOnly.value) return;
    if (inFlight) {
      refetchQueued = true;
      return;
    }
    inFlight = true;
    try {
      const [rows, state] = await Promise.all([
        listOperations({ limit: HISTORY_LIMIT }),
        getUndoState(),
      ]);
      operations.value = Array.isArray(rows) ? rows : [];
      canUndo.value = Boolean(state?.can_undo);
      canRedo.value = Boolean(state?.can_redo);
      nextUndo.value = state?.next_undo ?? null;
      nextRedo.value = state?.next_redo ?? null;
      const previous = highWaterMark;
      const newest = operations.value[0];
      if (newest?.id != null) {
        highWaterMark =
          previous == null ? newest.id : Math.max(previous, newest.id);
      }
      loaded.value = true;
      if (narrate) narrateNewest(previous, newest);
    } catch (e) {
      // The stack is an affordance over a server that stays correct either
      // way, so a failed read must never break the toolbar — log and keep the
      // last known state rather than clearing it into a dead control.
      console.warn(
        "useOperationStore: failed to refresh the operation log; keeping last state",
        e,
      );
    } finally {
      inFlight = false;
      if (refetchQueued) {
        refetchQueued = false;
        refresh({ narrate });
      }
    }
  }

  /**
   * Raise a receipt for a newly-arrived operation, but only for this client's
   * own actions. An operation from another tab or a background job updates the
   * stack silently.
   */
  function narrateNewest(previousHighWaterMark, newest) {
    if (!newest || newest.id == null) return;
    // First load: the whole history is "new". Narrating it would pop a receipt
    // for something that happened before the tab existed.
    if (previousHighWaterMark == null) return;
    if (newest.id <= previousHighWaterMark) return;
    if (newest.status !== "applied") return;
    const mine =
      newest.origin_client_id && newest.origin_client_id === myClientId();
    if (!mine) return;
    showReceipt(buildReceipt(newest, newest.undoable ? "did" : "blocked"));
  }

  /**
   * A WebSocket picture-change event landed. The log has no event of its own,
   * so any picture mutation is the signal that the stack may have moved.
   * Origin is read from the event `data` (never a contextvar, never a guess),
   * and only to decide whether the change may narrate itself.
   *
   * @param {Object} payload - the parsed WS envelope.
   * @returns {Promise<void>} resolves once the re-read has landed.
   */
  function onPictureEvent(payload) {
    if (isReadOnly.value || !payload) return Promise.resolve();
    const mine =
      payload.origin_client_id && payload.origin_client_id === myClientId();
    return refresh({ narrate: Boolean(mine) });
  }

  // ── Mutations ────────────────────────────────────────────────────────────
  function reportFailure(action, error) {
    const detail = error?.response?.data?.detail;
    const status = error?.response?.status;
    // 409 is the ordinary "the stack moved under you" answer, not a defect:
    // another tab undid it first, or a new action superseded the redo stack.
    const text =
      status === 409
        ? (detail ?? `Nothing left to ${action}.`)
        : (detail ?? `Could not ${action}. ${error?.message ?? ""}`.trim());
    console.warn(`useOperationStore: ${action} failed`, error);
    try {
      useNoticeStore().push({
        level: status === 409 ? "warning" : "error",
        text,
        key: `operation-${action}`,
      });
    } catch (e) {
      console.warn("useOperationStore: could not surface the failure", e);
    }
  }

  /**
   * Undo the newest reversible operation (and its whole batch).
   * @returns {Promise<Object|null>} the API result, or null when it failed.
   */
  async function undo() {
    if (isReadOnly.value || busy.value || !canUndo.value) return null;
    const target = nextUndo.value;
    busy.value = true;
    try {
      const result = await undoLastOperation();
      await refresh({ narrate: false });
      const reverted = target ?? result?.operations?.[0] ?? null;
      if (reverted) showReceipt(buildReceipt(reverted, "undone"));
      return result;
    } catch (e) {
      reportFailure("undo", e);
      await refresh({ narrate: false });
      return null;
    } finally {
      busy.value = false;
    }
  }

  /**
   * Re-apply the most recently undone operation (and its whole batch).
   * @returns {Promise<Object|null>} the API result, or null when it failed.
   */
  async function redo() {
    if (isReadOnly.value || busy.value || !canRedo.value) return null;
    const target = nextRedo.value;
    busy.value = true;
    try {
      const result = await redoOperation();
      await refresh({ narrate: false });
      const replayed = target ?? result?.operations?.[0] ?? null;
      if (replayed) showReceipt(buildReceipt(replayed, "did"));
      return result;
    } catch (e) {
      reportFailure("redo", e);
      await refresh({ narrate: false });
      return null;
    } finally {
      busy.value = false;
    }
  }

  /**
   * Undo every step from the newest down to, and including, `operationId` —
   * the History popover's "click a step to undo back to it".
   *
   * The server has no multi-step call: `POST /operations/{id}/undo` reverts
   * that operation and its batch, nothing newer. So the walk happens here,
   * newest first, and each response tells us which ids it actually reverted
   * (a batch takes its siblings with it), so a member already handled by an
   * earlier iteration is dropped rather than re-requested into a 409. Nothing
   * is swallowed: a real failure stops the walk and surfaces.
   *
   * @param {number} operationId - the step to stop at (it is undone too).
   * @returns {Promise<number>} how many operations were reverted.
   */
  async function undoTo(operationId) {
    if (isReadOnly.value || busy.value || operationId == null) return 0;
    const stack = past.value;
    const stopAt = stack.findIndex((op) => op?.id === operationId);
    if (stopAt === -1) return 0;
    const targets = stack.slice(0, stopAt + 1);
    const oldest = targets[targets.length - 1];
    const steps = targets.length;
    const pending = targets
      .map((op) => op?.id)
      .filter((id) => id != null)
      .sort((a, b) => b - a);

    busy.value = true;
    let reverted = 0;
    try {
      while (pending.length) {
        const id = pending.shift();
        const result = await undoOperation(id);
        const done = new Set((result?.operations ?? []).map((op) => op?.id));
        done.add(id);
        reverted += done.size;
        for (let i = pending.length - 1; i >= 0; i -= 1) {
          if (done.has(pending[i])) pending.splice(i, 1);
        }
      }
      await refresh({ narrate: false });
      if (oldest) showReceipt(buildReceipt(oldest, "undone", steps));
      return reverted;
    } catch (e) {
      reportFailure("undo", e);
      await refresh({ narrate: false });
      return reverted;
    } finally {
      busy.value = false;
    }
  }

  /**
   * Undo one whole bulk action by its batch id — the single-call revert behind
   * a bulk report ("Collapsed 2,700 groups — Undo").
   * @param {string} batchId
   * @returns {Promise<Object|null>} the API result, or null when it failed.
   */
  async function undoBatchById(batchId) {
    if (isReadOnly.value || busy.value || !batchId) return null;
    const target =
      operations.value.find((op) => op?.batch_id === batchId) ?? null;
    busy.value = true;
    try {
      const result = await undoBatch(batchId);
      await refresh({ narrate: false });
      const reverted = target ?? result?.operations?.[0] ?? null;
      if (reverted) showReceipt(buildReceipt(reverted, "undone"));
      return result;
    } catch (e) {
      reportFailure("undo", e);
      await refresh({ narrate: false });
      return null;
    } finally {
      busy.value = false;
    }
  }

  /** Drop every trace of the previous session (logout / vault switch). */
  function reset() {
    dismissReceipt();
    operations.value = [];
    canUndo.value = false;
    canRedo.value = false;
    nextUndo.value = null;
    nextRedo.value = null;
    loaded.value = false;
    highWaterMark = null;
  }

  return {
    // state
    operations,
    canUndo,
    canRedo,
    nextUndo,
    nextRedo,
    busy,
    loaded,
    receipt,
    // computed
    past,
    future,
    historyCount,
    hasHistory,
    // actions
    refresh,
    onPictureEvent,
    undo,
    redo,
    undoTo,
    undoBatchById,
    showReceipt,
    buildReceipt,
    dismissReceipt,
    pauseReceipt,
    resumeReceipt,
    reset,
  };
});
