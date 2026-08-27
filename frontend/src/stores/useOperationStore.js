// useOperationStore.js - the undo/redo stack, mirrored from the backend's
// append-only operation log (backend_architecture.md §21).
//
// The server owns the history. This store is a read model over it plus the
// transient "action receipt" state that narrates what just happened:
//
//   • `operations`  the newest 50 rows of GET /operations, newest FIRST.
//   • `canUndo` / `canRedo` / `nextUndo` / `nextRedo` from GET /operations/undo-state,
//     which is what enables and labels the toolbar control.
//   • `receipt`     at most one live receipt. Never two - the newest replaces
//     the current one in place (design rule), which is why this is a single ref
//     and not a queue like `useNoticeStore`.
//
// Origin discipline (integration_architecture.md §8.1, pitfall 14). Every
// operation row carries the `origin_client_id` of the tab that caused it. The
// receipt narrates THIS client's actions only: an operation that arrives from
// another tab or from a background job updates the stack silently, because a
// pill offering "Undo" for something the user did not just do is a trap. The id
// is used for echo-matching and nothing else - it is attacker-controllable and
// never an access decision.
//
// Undo is OWNER_ONLY on the server, so a share/read-only session never calls
// these endpoints and never renders the control.

import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import { isReadOnly, onSessionReset } from "../utils/apiClient";
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
const HISTORY_LIMIT = 50;

/** Receipt dwell, in ms. Destructive actions get longer to catch the mistake. */
export const RECEIPT_MS = 5000;
export const DESTRUCTIVE_RECEIPT_MS = 8000;

/** Trailing-edge window for WS-driven re-reads. */
export const WS_REFRESH_DEBOUNCE_MS = 400;

/**
 * Ghost tiles - the design's `none → pending → committed` machine.
 *
 * A move to the Scrapheap does not take its thumbnails away immediately. The
 * tiles stay exactly where they are, ghosted, for as long as the undo is still
 * one click away; only when that window closes does the grid close the gap.
 *
 * The window is the RECEIPT's, not a clock of its own. The receipt already
 * knows the destructive dwell, the hover/focus freeze (WCAG 2.2.1) and the
 * hidden-tab pause, so the ghosts are expressed as "this set lives as long as
 * receipt `key` is the live one". A second timer running alongside
 * `receiptRemaining` would drift out of that agreement within one hover.
 *
 * `pending` and `committed` are the only two the grid ever sees: `none` is the
 * absence of a set. `committed` is delivered as `collapsingPictureIds` - a
 * hand-off, because collapsing is an imperative grid op (`removeImagesById`)
 * and the store must not reach into the grid.
 */
const GHOST_NONE = "none";
export const GHOST_PENDING = "pending";
const GHOST_COMMITTED = "committed";

/**
 * How long a fresh ghost set waits for the receipt that will own its window.
 *
 * Ghosting starts optimistically, at the moment of the move, because the
 * receipt cannot arrive before the WS trailing edge plus the `/operations`
 * round trip. If none arrives - the socket dropped, the operation was not
 * recorded, another tab's action took the newest slot - the set would stay
 * ghosted forever with nothing left to un-ghost it. This is the liveness bound
 * on that gap, not a second dwell timer: it is cleared the moment a receipt
 * adopts the set, and a set that hits it collapses exactly as it would have.
 */
export const GHOST_ADOPT_TIMEOUT_MS = 2500;

/**
 * How many Ctrl+Z presses may queue behind an in-flight undo. A cap, because a
 * held key or a panicked burst should not walk the whole stack.
 */
const MAX_QUEUED_STEPS = 5;

/**
 * `op_type` → mdi glyph. Exact matches first; anything unknown falls through
 * `OP_ICON_RULES` and finally to `FALLBACK_ICON`.
 *
 * Deliberately generic: op types are added by whichever backend lane needs
 * them (the scrapheap-move lane lands its own alongside these), and a history
 * row for an unrecognised type must still render as a sensible step rather
 * than a blank or a crash.
 */
const OP_ICONS = {
  "pictures.tags.add": "mdi-tag-plus-outline",
  "pictures.tags.remove": "mdi-tag-minus-outline",
  "pictures.tags.remove_all": "mdi-tag-off-outline",
  "pictures.tags.clear": "mdi-tag-off-outline",
  "pictures.tags.replace": "mdi-tag-multiple-outline",
  "pictures.score": "mdi-star-outline",
  "pictures.fields": "mdi-pencil-outline",
  // One glyph for both directions: the op type does not name which way the
  // picture turned, and a receipt that guessed would be wrong half the time.
  "pictures.rotate": "mdi-rotate-right",
  "pictures.project": "mdi-folder-outline",
  "characters.assign": "mdi-account-check-outline",
  "characters.unassign": "mdi-account-off-outline",
  "picture_sets.members.add": "mdi-playlist-plus",
  "picture_sets.members.remove": "mdi-playlist-minus",
  "picture_sets.members.replace": "mdi-playlist-edit-outline",
  "stacks.create": "mdi-layers-outline",
  "stacks.dissolve": "mdi-layers-off-outline",
  // The inverse of the mdi-layers-plus the user pressed to build the stack, and
  // the same glyph the menu item and the confirm button carry, so the operation
  // is named identically at all three moments.
  "stack.keep_cover_only": "mdi-layers-minus",
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
  // `stack.keep_cover_only` moves every copy but the cover to the scrapheap, so
  // it earns the long window on consequence. It is named here rather than
  // caught by /scrapheap/ because the op type describes what the user asked
  // for, not where the pictures went.
  /keep_cover_only/,
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
 * "how much would this undo?" - the design's `Add tag "portrait" · 12` shape.
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
  // Grouped, like every other count in the product: "2,700", not "2700".
  if (Number.isFinite(count) && count > 1) {
    return `${base} · ${count.toLocaleString()}`;
  }
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
  /** True while an undo/redo round-trip is in flight - the control disables. */
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

  /**
   * A second sentence the CALLER wants on the next receipt of one op type.
   *
   * The server's `summary` says what an operation did; some actions also have
   * something to say about what they deliberately did NOT do: Keep cover only
   * skips a whole stack when a locked set or a character link would lose data.
   * That belongs on the same pill as the move: two surfaces for one action means
   * the user reads the reassuring half and dismisses the half that needed a
   * decision.
   *
   * Held as `{ opType, note }` and consumed by the FIRST receipt built, matching
   * op type or not, so a note can never drift onto a later unrelated action. A
   * mismatched receipt drops it instead of carrying it forward.
   */
  let pendingReceiptNote = null;

  // Highest operation id seen by a completed refresh. Used to tell a genuinely
  // new operation from a re-read of the same history - an id we have already
  // seen must never raise a second receipt.
  let highWaterMark = null;

  // Coalesce overlapping refreshes: WS bursts collapse into one in-flight
  // request plus at most one trailing refetch.
  let inFlight = false;
  let refetchQueued = false;

  // Every request belongs to exactly one authentication epoch. Reset aborts
  // the transport where possible and the epoch check remains the backstop for
  // mocks/transports that settle after abort.
  let sessionEpoch = 0;
  const activeRequests = new Set();

  function beginRequest() {
    const request = { epoch: sessionEpoch, controller: new AbortController() };
    activeRequests.add(request);
    return request;
  }

  function finishRequest(request) {
    activeRequests.delete(request);
  }

  function requestIsCurrent(request) {
    return request.epoch === sessionEpoch && !request.controller.signal.aborted;
  }

  // Trailing-edge debounce for WS-driven re-reads, and whether anything in the
  // current window came from this client (and may therefore narrate itself).
  let wsDebounceTimer = null;
  let wsNarrate = false;

  // Ctrl+Z presses waiting behind an in-flight undo.
  let queuedUndos = 0;

  // ── Ghost state ──────────────────────────────────────────────────────────
  /** Picture ids whose tiles are ghosted in place, awaiting their undo window. */
  const ghostPictureIds = ref([]);
  /** `none` | `pending` | `committed` - see the constants above. */
  const ghostState = ref(GHOST_NONE);
  /**
   * Ids whose window has closed and which the grid must now drop. A hand-off
   * queue, not a state: collapsing is an imperative grid op and the store does
   * not reach into the grid. The grid drains it with `takeCollapsingGhosts()`.
   */
  const collapsingPictureIds = ref([]);
  /** The `receipt.key` that owns the live set, or null while unadopted. */
  let ghostReceiptKey = null;
  let ghostAdoptTimer = null;

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
   * Steps that have been undone and can still be redone - the struck-through
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

  /**
   * Was the step Ctrl+Z would take back done somewhere else?
   *
   * An external operation updates the stack silently (a pill offering to undo
   * something the user did not just do is a trap), but the CONSEQUENCE is that
   * the undo target quietly becomes another tab's action while the control
   * looks unchanged. The affordance has to say so before it reverts something
   * the user never did.
   */
  const nextUndoIsExternal = computed(() => {
    if (!nextUndo.value) return false;
    const origin = nextUndo.value.origin_client_id;
    // No origin at all means a background job - also not this tab.
    if (!origin) return true;
    return origin !== myClientId();
  });

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
    // Consumed by the first receipt built after it was set, whether or not the
    // op type matches: carrying it forward would eventually land it on an
    // unrelated action, and a wrong second sentence is worse than none.
    const note =
      pendingReceiptNote && pendingReceiptNote.opType === opType
        ? pendingReceiptNote.note
        : "";
    pendingReceiptNote = null;
    receiptKey += 1;
    return {
      key: receiptKey,
      mode,
      operationId: operation?.id ?? null,
      batchId,
      opType,
      icon: iconForOpType(opType),
      summary: summarizeOperation(operation),
      // What the action deliberately left alone, as a second sentence on the
      // same pill. Empty for everything that has nothing to add.
      note,
      targetCount: Number(operation?.target_count) || 0,
      mergedCount: merged,
      steps,
      destructive,
      // Whether the step behind this pill can actually be taken back. The
      // ghosted tiles read it: holding them open for an undo that does not
      // exist would promise something the Undo button cannot deliver.
      undoable: operation?.undoable !== false,
      durationMs: destructive ? DESTRUCTIVE_RECEIPT_MS : RECEIPT_MS,
    };
  }

  /**
   * Arm a second sentence for the next receipt of `opType`.
   *
   * Call it immediately BEFORE the `refresh()` that will narrate the action, so
   * the note and the operation it describes arrive together. A blank note
   * clears any armed one rather than queueing an empty sentence.
   *
   * @param {string} opType - the dotted op type the note belongs to.
   * @param {string} note - the sentence, already worded for the user.
   * @returns {void}
   */
  function noteNextReceipt(opType, note) {
    const text = String(note ?? "").trim();
    pendingReceiptNote = text ? { opType: String(opType ?? ""), note: text } : null;
  }

  /**
   * Raise a receipt, replacing any live one in place (never stacked).
   * @param {Object} entry - a payload from {@link buildReceipt}.
   */
  function showReceipt(entry) {
    if (!entry) return;
    // Before the swap, so a set whose pill is being replaced sees the raise.
    reconcileGhostsWithReceipt(entry);
    receipt.value = entry;
    armReceiptTimer(entry.durationMs);
  }

  /**
   * Retire the live receipt and its countdown.
   *
   * This is the single place the ghost window ends on time: the dwell timer
   * fires here, `resumeReceipt` funnels a drained countdown here, and an
   * explicit dismissal lands here too. Binding the ghosts to it - rather than
   * to a clock of their own - is what makes the hover freeze, the focus freeze
   * and the hidden-tab pause apply to the tiles for free.
   */
  function dismissReceipt() {
    clearReceiptTimer();
    if (ghostReceiptKey != null && receipt.value?.key === ghostReceiptKey) {
      commitGhosts();
    }
    receipt.value = null;
  }

  /**
   * Freeze the countdown (hover / focus-within). WCAG 2.2.1 - the user must be
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

  // ── Ghost lifecycle ──────────────────────────────────────────────────────
  function clearGhostAdoptTimer() {
    if (ghostAdoptTimer != null) clearTimeout(ghostAdoptTimer);
    ghostAdoptTimer = null;
  }

  function resetGhostSet() {
    clearGhostAdoptTimer();
    ghostReceiptKey = null;
    ghostPictureIds.value = [];
    ghostState.value = GHOST_NONE;
  }

  /**
   * End the live set's window: hand its ids to the grid to remove, then forget
   * it. `pending → committed → none`, in one call, because `committed` only
   * exists as the hand-off.
   */
  function commitGhosts() {
    if (!ghostPictureIds.value.length) {
      resetGhostSet();
      return;
    }
    ghostState.value = GHOST_COMMITTED;
    collapsingPictureIds.value = [
      ...collapsingPictureIds.value,
      ...ghostPictureIds.value,
    ];
    resetGhostSet();
  }

  /**
   * Ghost a set of pictures that just went to the Scrapheap: their tiles stay
   * mounted, greyed, until the undo window closes.
   *
   * Any set already live is committed first - the design never stacks two
   * receipts, so the older set's one-click undo is gone the moment the newer
   * action raises its own pill, and a ghost with no live undo behind it is a
   * lie about what a click can still do.
   *
   * @param {Array<number|string>} ids - the pictures the server accepted.
   * @returns {boolean} false when ghosting does not apply (read-only session,
   *   or nothing to ghost) and the caller should drop the tiles outright.
   */
  function markGhosted(ids) {
    const wanted = (Array.isArray(ids) ? ids : []).filter(
      (id) => id !== null && id !== undefined,
    );
    if (!wanted.length) return false;
    // A read-only session never calls the undo endpoints and never renders the
    // control, so there is no window to hold the tiles open for.
    if (isReadOnly.value) return false;
    commitGhosts();
    ghostPictureIds.value = wanted;
    ghostState.value = GHOST_PENDING;
    ghostReceiptKey = null;
    clearGhostAdoptTimer();
    ghostAdoptTimer = setTimeout(() => {
      ghostAdoptTimer = null;
      console.warn(
        "useOperationStore: no receipt adopted the ghosted pictures within " +
          `${GHOST_ADOPT_TIMEOUT_MS}ms; collapsing them. The operation log did ` +
          "not report this action back to this tab (dropped socket, unrecorded " +
          "operation, or another tab's action took the newest slot).",
        wanted,
      );
      commitGhosts();
    }, GHOST_ADOPT_TIMEOUT_MS);
    return true;
  }

  /**
   * Take pictures back out of the ghost set - an undo landed and the tiles stay
   * where they are, at full strength, with no refetch flash.
   * @param {Array<number|string>} ids
   */
  function unghostPictures(ids) {
    if (!ghostPictureIds.value.length) return;
    const back = new Set((Array.isArray(ids) ? ids : []).map(String));
    if (!back.size) return;
    const remaining = ghostPictureIds.value.filter(
      (id) => !back.has(String(id)),
    );
    if (remaining.length === ghostPictureIds.value.length) return;
    if (!remaining.length) {
      resetGhostSet();
      return;
    }
    ghostPictureIds.value = remaining;
  }

  /**
   * Forget the ghost set WITHOUT collapsing it - the grid was rebuilt from a
   * fresh fetch, so the scrapheaped pictures are already absent and there is
   * nothing left to grey out. The receipt is deliberately untouched: undo stays
   * offered, it just has no tiles to un-ghost any more.
   */
  function dropGhosts() {
    if (ghostState.value === GHOST_NONE) return;
    resetGhostSet();
  }

  /** Drain the collapse queue. Returns the ids the grid must now remove. */
  function takeCollapsingGhosts() {
    const ids = collapsingPictureIds.value;
    collapsingPictureIds.value = [];
    return ids;
  }

  /**
   * Bind a fresh ghost set to the receipt that will own its window, or end the
   * set when a receipt that is not its own takes the pill's slot.
   */
  function reconcileGhostsWithReceipt(entry) {
    if (ghostState.value !== GHOST_PENDING) return;
    const unadopted = ghostReceiptKey == null;
    if (unadopted && entry.mode === "did" && entry.destructive) {
      ghostReceiptKey = entry.key;
      clearGhostAdoptTimer();
      // Recorded for audit but not reversible: there is no undo to wait for, so
      // holding the tiles open would promise one that does not exist.
      if (!entry.undoable) commitGhosts();
      return;
    }
    // Any other raise replaced our pill in place (the design never stacks two),
    // so the one-click undo for this set is gone and the grid closes the gap.
    if (entry.key !== ghostReceiptKey) commitGhosts();
  }

  /** Un-ghost whatever an undo/redo response says it put back. */
  function applyLifecycleToGhosts(result) {
    const restored = result?.restored_picture_ids;
    if (Array.isArray(restored) && restored.length) unghostPictures(restored);
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
    const request = beginRequest();
    try {
      const [rows, state] = await Promise.all([
        listOperations({ limit: HISTORY_LIMIT, signal: request.controller.signal }),
        getUndoState({ signal: request.controller.signal }),
      ]);
      if (!requestIsCurrent(request)) return;
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
      if (!requestIsCurrent(request)) return;
      // The stack is an affordance over a server that stays correct either
      // way, so a failed read must never break the toolbar - log and keep the
      // last known state rather than clearing it into a dead control.
      console.warn(
        "useOperationStore: failed to refresh the operation log; keeping last state",
        e,
      );
    } finally {
      finishRequest(request);
      if (requestIsCurrent(request)) {
        inFlight = false;
        if (refetchQueued) {
          refetchQueued = false;
          refresh({ narrate });
        }
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
   * Debounced on the trailing edge: a bulk action over thousands of pictures
   * emits a continuous stream of these, and the stack does not need to be
   * sub-second fresh. Without it the two reads would poll back to back at
   * round-trip speed for the whole run.
   *
   * @param {Object} payload - the parsed WS envelope.
   * @returns {Promise<void>} resolves once the re-read has landed.
   */
  function onPictureEvent(payload) {
    if (isReadOnly.value || !payload) return Promise.resolve();
    const mine =
      payload.origin_client_id && payload.origin_client_id === myClientId();
    // Any own-origin event in the window makes the whole window narratable:
    // dropping the flag because a background job's event arrived last would
    // silently swallow the user's own receipt.
    wsNarrate = wsNarrate || Boolean(mine);
    if (wsDebounceTimer != null) clearTimeout(wsDebounceTimer);
    return new Promise((resolve) => {
      wsDebounceTimer = setTimeout(() => {
        wsDebounceTimer = null;
        const narrate = wsNarrate;
        wsNarrate = false;
        refresh({ narrate }).then(resolve, resolve);
      }, WS_REFRESH_DEBOUNCE_MS);
    });
  }

  // ── Mutations ────────────────────────────────────────────────────────────
  /**
   * Surface a failed undo/redo. Three shapes, because they mean three things:
   *
   *   409 - the stack moved under you (another tab got there first, or a new
   *         action superseded the redo stack). Ordinary, not a defect.
   *   423 - a locked picture set froze one of the targets. A state, not a
   *         failure: the user has to unlock the set, and the server's detail
   *         names it.
   *   else - a real error, which stays until dismissed.
   */
  function reportFailure(action, error) {
    const detail = error?.response?.data?.detail;
    const status = error?.response?.status;
    let level = "error";
    let text;
    if (status === 409) {
      level = "warning";
      text = detail ?? `Nothing left to ${action}.`;
    } else if (status === 423) {
      level = "warning";
      text =
        detail ??
        `Could not ${action}: a locked picture set has frozen one of the pictures. Unlock the set to change it.`;
    } else {
      text = detail ?? `Could not ${action}. ${error?.message ?? ""}`.trim();
    }
    console.warn(`useOperationStore: ${action} failed`, error);
    // A receipt still offering the action that just failed is a lie; retire it
    // before the notice explains what happened.
    dismissReceipt();
    try {
      useNoticeStore().push({ level, text, key: `operation-${action}` });
    } catch (e) {
      console.warn("useOperationStore: could not surface the failure", e);
    }
  }

  /**
   * Tell the user that a shortcut they pressed had nothing to act on. A global
   * key binding that silently does nothing is the one unacceptable answer:
   * the user cannot tell it from a broken feature.
   */
  function reportNothingToDo(action) {
    try {
      useNoticeStore().push({
        level: "info",
        text: `Nothing to ${action}.`,
        key: `operation-nothing-to-${action}`,
      });
    } catch (e) {
      console.warn("useOperationStore: could not surface the empty stack", e);
    }
  }

  /**
   * Undo the newest reversible operation (and its whole batch).
   *
   * Pressing Ctrl+Z repeatedly is the canonical undo idiom, and each press
   * takes a round trip. A press that lands while one is in flight is QUEUED
   * (up to `MAX_QUEUED_STEPS`) rather than dropped, or four of five presses
   * would silently do nothing.
   *
   * @returns {Promise<Object|null>} the API result, or null when it failed.
   */
  async function undo() {
    if (isReadOnly.value) return null;
    if (busy.value) {
      if (queuedUndos < MAX_QUEUED_STEPS) queuedUndos += 1;
      return null;
    }
    if (!canUndo.value) {
      reportNothingToDo("undo");
      return null;
    }
    const target = nextUndo.value;
    busy.value = true;
    const request = beginRequest();
    try {
      const result = await undoLastOperation({ signal: request.controller.signal });
      if (!requestIsCurrent(request)) return null;
      // Before the receipt: the raise below would otherwise read as "a pill
      // that is not this set's" and collapse the very tiles the undo just
      // brought back.
      applyLifecycleToGhosts(result);
      await refresh({ narrate: false });
      if (!requestIsCurrent(request)) return null;
      const reverted = target ?? result?.operations?.[0] ?? null;
      if (reverted) showReceipt(buildReceipt(reverted, "undone"));
      return result;
    } catch (e) {
      if (!requestIsCurrent(request)) return null;
      queuedUndos = 0;
      reportFailure("undo", e);
      await refresh({ narrate: false });
      return null;
    } finally {
      finishRequest(request);
      if (requestIsCurrent(request)) {
        busy.value = false;
        if (queuedUndos > 0 && canUndo.value) {
          queuedUndos -= 1;
          undo();
        } else {
          queuedUndos = 0;
        }
      }
    }
  }

  /**
   * Re-apply the most recently undone operation (and its whole batch).
   * @returns {Promise<Object|null>} the API result, or null when it failed.
   */
  async function redo() {
    if (isReadOnly.value || busy.value) return null;
    if (!canRedo.value) {
      reportNothingToDo("redo");
      return null;
    }
    const target = nextRedo.value;
    busy.value = true;
    const request = beginRequest();
    try {
      const result = await redoOperation({ signal: request.controller.signal });
      if (!requestIsCurrent(request)) return null;
      await refresh({ narrate: false });
      if (!requestIsCurrent(request)) return null;
      // Redoing a move puts the pictures back in the Scrapheap. Their tiles are
      // on screen again (the undo reinstated them), so they ghost again for the
      // new receipt's window rather than vanishing - the same offer, both ways.
      const rescrapheaped = result?.scrapheaped_picture_ids;
      if (Array.isArray(rescrapheaped) && rescrapheaped.length) {
        markGhosted(rescrapheaped);
      }
      const replayed = target ?? result?.operations?.[0] ?? null;
      if (replayed) showReceipt(buildReceipt(replayed, "did"));
      return result;
    } catch (e) {
      if (!requestIsCurrent(request)) return null;
      reportFailure("redo", e);
      await refresh({ narrate: false });
      return null;
    } finally {
      finishRequest(request);
      if (requestIsCurrent(request)) busy.value = false;
    }
  }

  /**
   * Undo every step from the newest down to, and including, `operationId` -
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
    const request = beginRequest();
    let reverted = 0;
    // One POST per step: a 20-step walk on a slow link would otherwise look
    // like the app froze, with the popover shut and both buttons greyed until
    // a receipt appeared from nowhere. Say what is happening while it happens.
    const progressKey = steps > 1 ? "operation-undo-progress" : null;
    if (progressKey) {
      try {
        useNoticeStore().push({
          level: "info",
          text: `Undoing ${steps} steps…`,
          timeout: 0,
          key: progressKey,
        });
      } catch (e) {
        console.warn("useOperationStore: could not report undo progress", e);
      }
    }
    try {
      while (pending.length) {
        const id = pending.shift();
        const result = await undoOperation(id, {
          signal: request.controller.signal,
        });
        if (!requestIsCurrent(request)) return reverted;
        applyLifecycleToGhosts(result);
        const done = new Set((result?.operations ?? []).map((op) => op?.id));
        done.add(id);
        reverted += done.size;
        for (let i = pending.length - 1; i >= 0; i -= 1) {
          if (done.has(pending[i])) pending.splice(i, 1);
        }
      }
      await refresh({ narrate: false });
      if (!requestIsCurrent(request)) return reverted;
      if (oldest) showReceipt(buildReceipt(oldest, "undone", steps));
      return reverted;
    } catch (e) {
      if (!requestIsCurrent(request)) return reverted;
      reportFailure("undo", e);
      await refresh({ narrate: false });
      return reverted;
    } finally {
      finishRequest(request);
      if (requestIsCurrent(request)) busy.value = false;
      if (progressKey && requestIsCurrent(request)) {
        try {
          useNoticeStore().dismissByKey(progressKey);
        } catch (e) {
          console.warn(
            "useOperationStore: could not retire the progress notice",
            e,
          );
        }
      }
    }
  }

  /**
   * Undo one whole bulk action by its batch id - the single-call revert behind
   * a bulk report ("Collapsed 2,700 groups - Undo").
   * @param {string} batchId
   * @returns {Promise<Object|null>} the API result, or null when it failed.
   */
  async function undoBatchById(batchId) {
    if (isReadOnly.value || busy.value || !batchId) return null;
    const target =
      operations.value.find((op) => op?.batch_id === batchId) ?? null;
    busy.value = true;
    const request = beginRequest();
    try {
      const result = await undoBatch(batchId, {
        signal: request.controller.signal,
      });
      if (!requestIsCurrent(request)) return null;
      applyLifecycleToGhosts(result);
      await refresh({ narrate: false });
      if (!requestIsCurrent(request)) return null;
      const reverted = target ?? result?.operations?.[0] ?? null;
      if (reverted) showReceipt(buildReceipt(reverted, "undone"));
      return result;
    } catch (e) {
      if (!requestIsCurrent(request)) return null;
      reportFailure("undo", e);
      await refresh({ narrate: false });
      return null;
    } finally {
      finishRequest(request);
      if (requestIsCurrent(request)) busy.value = false;
    }
  }

  /** Drop every trace of the previous session (logout / vault switch). */
  function reset() {
    sessionEpoch += 1;
    for (const request of activeRequests) request.controller.abort();
    activeRequests.clear();
    inFlight = false;
    refetchQueued = false;
    busy.value = false;
    dismissReceipt();
    resetGhostSet();
    collapsingPictureIds.value = [];
    if (wsDebounceTimer != null) clearTimeout(wsDebounceTimer);
    wsDebounceTimer = null;
    wsNarrate = false;
    queuedUndos = 0;
    pendingReceiptNote = null;
    try {
      useNoticeStore().dismissByKey("operation-undo-progress");
    } catch (e) {
      console.warn("useOperationStore: could not retire stale undo progress", e);
    }
    operations.value = [];
    canUndo.value = false;
    canRedo.value = false;
    nextUndo.value = null;
    nextRedo.value = null;
    loaded.value = false;
    highWaterMark = null;
  }

  // The undo stack is the previous credential's history - it goes with the rest
  // of the session state, through the one chokepoint in apiClient.
  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

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
    ghostPictureIds,
    ghostState,
    collapsingPictureIds,
    // computed
    past,
    future,
    historyCount,
    hasHistory,
    nextUndoIsExternal,
    // actions
    refresh,
    onPictureEvent,
    undo,
    redo,
    undoTo,
    undoBatchById,
    showReceipt,
    buildReceipt,
    noteNextReceipt,
    dismissReceipt,
    pauseReceipt,
    resumeReceipt,
    markGhosted,
    unghostPictures,
    dropGhosts,
    takeCollapsingGhosts,
    reset,
  };
});
