<template>
  <div class="rs-session">
    <ReviewCelebration
      :on="store.gamify"
      :tick="store.decisionTick"
      :award="store.activeAward"
    />

    <!-- Session header: title + scan receipt + staleness + gamify pill + tally,
         all in normal flow (nothing absolutely positioned over anything). -->
    <div class="rs-session-head">
      <span class="rs-session-title">Review: “{{ session.tag }}”</span>
      <span class="rs-session-receipt">{{ receiptLine }}</span>
      <span class="rs-session-spacer"></span>
      <span v-if="session.stale" class="rs-session-stale">
        <v-icon size="15">mdi-clock-alert-outline</v-icon>
        Vault changed since scan
        <button
          class="rs-session-refresh"
          type="button"
          title="Append newly-found suspects - decided cards are never resurrected"
          @click="store.refreshSession(session.id)"
        >
          <v-icon size="14">mdi-refresh</v-icon> Refresh
        </button>
      </span>
      <span v-if="store.gamify" class="rs-xp-pill">
        <v-icon size="16" class="rs-xp-trophy">mdi-trophy</v-icon>
        <span class="rs-xp-level">LEVEL {{ level }}</span>
        <span class="rs-xp-points">{{ xp }} XP</span>
        <span class="rs-xp-streak">
          <v-icon size="14">mdi-fire</v-icon>{{ store.decisionsCount }}×
        </span>
      </span>
      <!-- `progress.locked`: suspects frozen mid-session and held out of the
           queue. Surfaced so a review that visibly shrinks explains itself
           instead of silently dropping its count. -->
      <span
        v-if="lockedProgress"
        class="rs-session-locked"
        :title="lockedProgressNote(lockedProgress)"
      >
        <v-icon size="14">mdi-lock-outline</v-icon>
        {{ lockedProgress }} frozen
      </span>
      <span class="rs-session-tally">
        <span class="rs-tally-removed">✗ {{ tally.removed }}</span>
        <span class="rs-tally-added">+ {{ tally.added }}</span>
        <span class="rs-tally-kept">✓ {{ tally.kept }}</span>
        <span v-if="tally.skipped" class="rs-tally-skipped"
          >{{ tally.skipped }} skipped</span
        >
      </span>
    </div>

    <section class="rs-session-body">
      <!-- Store-level error (a failed decision write, refresh, undo…). The
           optimistic mutation in resolveCurrent() rolls back on failure, so
           without this the user sees an identical screen and a tally that
           flickers and returns - a 423 with no renderer. -->
      <div v-if="store.error" class="rs-error-bar" role="alert">
        <v-icon size="16">mdi-alert-circle-outline</v-icon>
        <span class="rs-error-msg">{{ store.error }}</span>
        <button
          class="rs-error-dismiss"
          type="button"
          @click="store.error = null"
        >
          Dismiss
        </button>
      </div>

      <div v-if="queueError" class="rs-state rs-state--error">
        {{ queueError }}
        <button
          class="rs-state-btn"
          type="button"
          @click="store.fetchQueue(session.id)"
        >
          Retry
        </button>
      </div>

      <div v-else-if="loadingEmpty" class="rs-state">Loading…</div>

      <!-- Explicit empty-scan state: the scan found nothing (never an
           ambiguous "all caught up"). -->
      <div v-else-if="emptyScan" class="rs-state rs-state--done">
        <v-icon size="44">mdi-radar</v-icon>
        <p class="rs-state-big">
          The scan found nothing to review for “{{ session.tag }}”.
        </p>
        <p class="rs-state-sub">
          Scanned {{ scanned.toLocaleString() }} pictures ·
          {{ session.stats?.prev_reviewed ?? 0 }} handled in earlier reviews.
        </p>
        <p class="rs-state-sub rs-state-sub--muted">
          The board's Priority number is a fast estimate - the review scan is
          more selective, so finding fewer (or none) here doesn't mean that
          number was wrong.
        </p>
        <div class="rs-state-actions">
          <button
            ref="archiveBtnRef"
            class="rs-state-btn rs-state-btn--archive"
            type="button"
            @click="store.archiveSession(session.id)"
          >
            <v-icon size="16">mdi-archive-check-outline</v-icon> Archive review
          </button>
          <button
            class="rs-state-btn"
            type="button"
            @click="store.refreshSession(session.id)"
          >
            <v-icon size="16">mdi-refresh</v-icon> Refresh “{{ session.tag }}”
          </button>
        </div>
      </div>

      <!-- Completion: the queue is empty - a real state with a receipt. -->
      <div v-else-if="!current" class="rs-state rs-state--done">
        <v-icon size="48" class="rs-state-check">mdi-check-decagram</v-icon>
        <p class="rs-state-big">
          Review complete - {{ found }} suspect{{ found === 1 ? "" : "s" }}
          reviewed.
        </p>
        <p class="rs-state-sub">
          <span class="rs-tally-removed">✗ {{ receipt.removed }} removed</span>
          <span class="rs-tally-added">+ {{ receipt.added }} added</span>
          <span class="rs-tally-kept">✓ {{ receipt.kept }} kept</span>
          <span v-if="receipt.skipped" class="rs-tally-skipped"
            >{{ receipt.skipped }} skipped</span
          >
        </p>
        <div class="rs-state-actions">
          <button
            ref="archiveBtnRef"
            class="rs-state-btn rs-state-btn--archive"
            type="button"
            @click="store.archiveSession(session.id)"
          >
            <v-icon size="16">mdi-archive-check-outline</v-icon> Archive review
          </button>
          <button
            v-if="reopenableSkips > 0"
            class="rs-state-btn rs-state-btn--accent"
            type="button"
            title="Put the cards you skipped back in the queue"
            @click="store.reopenSkipped(session.id)"
          >
            <v-icon size="16">mdi-restart</v-icon> Reopen
            {{ reopenableSkips }} skipped
          </button>
          <button
            class="rs-state-btn"
            type="button"
            @click="store.refreshSession(session.id)"
          >
            <v-icon size="16">mdi-refresh</v-icon> Refresh “{{ session.tag }}”
          </button>
        </div>
      </div>

      <!-- The card. Focus lives on this container (role=group, named by the
           question); it re-keys per card so entry transitions play and focus
           can follow. -->
      <!-- Key is namespaced (`card-…`) so it can never equal the compiler's
           numeric keys for the sibling v-if/v-else-if branches above (0,1,2,3).
           A bare `:key="current.id"` collides when current.id is 1 (the Loading
           branch's auto-key): in a production build (no DEV_ROOT_FRAGMENT
           wrapping) Vue then block-patches the empty Loading <div> into this
           card <div>, desyncing dynamicChildren and crashing patchBlockChildren
           with "reading 'el'" (BUG-RS-1). -->
      <div
        v-else
        ref="cardRef"
        :key="`card-${current.id}`"
        class="rs-card"
        :class="{ 'rs-card--entering': holdActive }"
        role="group"
        tabindex="-1"
        :aria-label="questionLabel"
      >
        <ReviewPairCard v-if="current.kind === 'pair'" :item="current" />
        <ReviewBinaryCard v-else :item="current" />
      </div>

      <!-- Live consistency guard: shown only when the staged decision
           contradicts a confident prior call this session. -->
      <div v-if="pendingDecision" class="rs-confirm" role="alertdialog">
        <span class="rs-confirm-msg">⚠ {{ pendingMessage }}</span>
        <div class="rs-confirm-actions">
          <button
            class="rs-confirm-btn rs-confirm-btn--apply"
            type="button"
            title="Apply this decision despite the earlier call (Enter)."
            @click="confirmPending"
          >
            <kbd>↵</kbd> Apply
          </button>
          <button
            class="rs-confirm-btn"
            type="button"
            title="Leave the card unchanged (Esc)."
            @click="cancelPending"
          >
            <kbd>Esc</kbd> Cancel
          </button>
        </div>
      </div>

      <!-- Assertive because it answers a deliberate user action and must
           interrupt; cleared on card change so it never re-reads a stale reason.
           Clipped, never `display: none` - a hidden node is not announced. -->
      <p class="visually-hidden" role="status" aria-live="assertive">
        {{ announcement }}
      </p>

      <ReviewDecisionBar
        v-if="current"
        :kind="current.kind === 'pair' ? 'pair' : 'binary'"
        :direction="current.direction"
        :can-undo="store.canUndo"
        :gamify="store.gamify"
        :hold="holdActive"
        :blocked="blockedReasons"
        :lock-note="lockNote"
        :lock-detail="lockDetail"
        :flash-tick="flashTick"
        @answer="attemptBinary"
        @corner="attemptPair"
        @skip="doSkip"
        @undo="attemptUndo"
        @gamify-toggle="store.setGamify($event)"
      />
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import {
  useReviewSessionsStore,
  binaryAction,
  pairAction,
} from "../../stores/useReviewSessionsStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import {
  LOCKED_UNDO_CHIP_LABEL,
  blockedDecisionMessage,
  blockedUndoMessage,
  lockedDecisionChipLabel,
  lockedProgressNote,
  lockedSetNamesOf,
} from "./lockedSetCopy";
import ReviewBinaryCard from "./ReviewBinaryCard.vue";
import ReviewPairCard from "./ReviewPairCard.vue";
import ReviewDecisionBar from "./ReviewDecisionBar.vue";
import ReviewCelebration from "./ReviewCelebration.vue";

const props = defineProps({
  session: { type: Object, required: true },
});

const store = useReviewSessionsStore();
const lockedSetsStore = useLockedSetsStore();
// "Nothing to undo" is reported on the notice surface rather than the card's
// live region: the region is assertive and cleared on every card change, and
// this message has to be visible to a sighted user who pressed a shortcut.
const noticeStore = useNoticeStore();

const current = computed(() => store.current);

// --- Lock gating ---------------------------------------------------------------
//
// A card has TWO sides and either can be frozen, which is what the original gate
// got wrong: it tested only `picture_id` (the suspect), while on a pair card the
// locked picture is almost always the TWIN - and `pairAction()` writes the twin
// on the fix-twin/swap corners. So every button stayed live, the request went
// out, and the backend answered 423.
//
// The payload is authoritative and ships the locking set NAMES inline
// (`locked_sets` / `twin_locked_sets`), so no extra call is needed for the copy.
// `useLockedSetsStore` is the defensive fallback for the mid-session case where
// this client's cached card predates the lock.
function resolveLock(pictureId, payloadLocked, payloadSets) {
  const payloadNames = lockedSetNamesOf(payloadSets);
  const locked =
    !!payloadLocked || payloadNames.length > 0 || lockedSetsStore.isLocked(pictureId);
  if (!locked) return { locked: false, names: [] };
  return {
    locked: true,
    names: payloadNames.length
      ? payloadNames
      : lockedSetsStore.lockedSetNames(pictureId),
  };
}

function uniqueNames(names) {
  return [...new Set(names)];
}

const suspectLock = computed(() => {
  const item = current.value;
  if (!item) return { locked: false, names: [] };
  return resolveLock(item.picture_id, item.locked, item.locked_sets);
});

const twinLock = computed(() => {
  const item = current.value;
  if (!item || item.twin_picture_id == null) return { locked: false, names: [] };
  return resolveLock(item.twin_picture_id, item.twin_locked, item.twin_locked_sets);
});

// Which side each per-item action writes. Normal locked-twin pair cards arrive
// already degraded to `kind: "binary"` by the backend, so this table is the
// defensive path for a stale cached card - not elaborate per-corner gating.
const ACTION_WRITES = {
  accept: { suspect: true, twin: false },
  dismiss: { suspect: true, twin: false },
  "fix-twin": { suspect: false, twin: true },
  swap: { suspect: true, twin: true },
};

function blockReasonFor(kind, decision) {
  const item = current.value;
  if (!item) return "";
  const action =
    kind === "binary" ? binaryAction(item, decision) : pairAction(item, decision);
  // Unknown action → assume it writes the suspect (fail closed, never open).
  const writes = ACTION_WRITES[action] ?? { suspect: true, twin: false };
  const hitsSuspect = writes.suspect && suspectLock.value.locked;
  const hitsTwin = writes.twin && twinLock.value.locked;
  if (!hitsSuspect && !hitsTwin) return "";
  if (hitsSuspect && hitsTwin) {
    return blockedDecisionMessage(
      uniqueNames([...suspectLock.value.names, ...twinLock.value.names]),
      "both",
    );
  }
  if (hitsSuspect) return blockedDecisionMessage(suspectLock.value.names, "suspect");
  return blockedDecisionMessage(twinLock.value.names, "twin");
}

// Undo is ONE-WAY on a locked card: `reopen_suggestion` guards both sides
// unconditionally, so a decision made there is final until the set is unlocked.
// Explain that up front rather than letting Undo fail silently - which would
// reproduce the exact bug this change fixes.
const undoBlockedReason = computed(() => {
  const s = store.activeSession;
  if (!s) return "";
  const stack = store.undoStacks[s.id] || [];
  const item = stack[stack.length - 1]?.item;
  if (!item) return "";
  const a = resolveLock(item.picture_id, item.locked, item.locked_sets);
  const b =
    item.twin_picture_id != null
      ? resolveLock(item.twin_picture_id, item.twin_locked, item.twin_locked_sets)
      : { locked: false, names: [] };
  if (!a.locked && !b.locked) return "";
  return blockedUndoMessage(uniqueNames([...a.names, ...b.names]));
});

// decision key -> reason. A key present here is pre-emptively marked
// `aria-disabled` on the bar AND refused by the attempt*() guards below, so the
// mouse and keyboard paths share one gate and one announcement.
const blockedReasons = computed(() => {
  const item = current.value;
  const out = {};
  if (item) {
    const keys =
      item.kind === "pair"
        ? ["both", "neither", "left", "right"]
        : ["yes", "no"];
    const kind = item.kind === "pair" ? "pair" : "binary";
    for (const key of keys) {
      const reason = blockReasonFor(kind, key);
      if (reason) out[key] = reason;
    }
  }
  if (undoBlockedReason.value) out.undo = undoBlockedReason.value;
  return out;
});

const cardLockNames = computed(() =>
  uniqueNames([
    ...(suspectLock.value.locked ? suspectLock.value.names : []),
    ...(twinLock.value.locked ? twinLock.value.names : []),
  ]),
);

const lockNote = computed(() => {
  if (suspectLock.value.locked || twinLock.value.locked) {
    return lockedDecisionChipLabel(cardLockNames.value);
  }
  return undoBlockedReason.value ? LOCKED_UNDO_CHIP_LABEL : "";
});

const lockDetail = computed(() => {
  const r = blockedReasons.value;
  return (
    r.yes || r.no || r.both || r.neither || r.left || r.right || r.undo || ""
  );
});

// Response to a blocked press: announced assertively AND flashed on the chip,
// because the keyboard user this protects is usually sighted.
const announcement = ref("");
const flashTick = ref(0);

function announceBlocked(reason) {
  if (!reason) return;
  announcement.value = reason;
  flashTick.value += 1;
}

const lockedProgress = computed(() => props.session.progress?.locked ?? 0);

const tally = computed(() => store.activeTally);
const found = computed(() => props.session.stats?.found ?? 0);
const scanned = computed(() => props.session.stats?.scanned ?? 0);
const emptyScan = computed(() => found.value === 0);
const reopenableSkips = computed(() =>
  store.reopenableSkipsFor(props.session.id),
);
const receipt = computed(() => store.receiptFor(props.session.id));
const queueError = computed(
  () => store.queues[props.session.id]?.error ?? null,
);
const loadingEmpty = computed(
  () => store.activeQueueLoading && !store.activeQueue.length,
);

// XP/level/streak: monotonic counters of decisions made - Undo never
// decrements them.
const level = computed(() => Math.floor(store.decisionsCount / 3) + 1);
const xp = computed(() => store.decisionsCount * 100);

const receiptLine = computed(() => {
  const s = props.session;
  const created = formatWhen(s.created_at);
  return `Scanned ${scanned.value.toLocaleString()} pictures · ${found.value} suspects · ${
    s.stats?.prev_reviewed ?? 0
  } handled earlier${created ? ` · ${created}` : ""}`;
});

function formatWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) {
    return `today ${d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    })}`;
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const questionLabel = computed(() => {
  const item = current.value;
  if (!item) return "";
  return item.kind === "pair"
    ? `Which really has “${item.tag}”?`
    : `Should this have the tag “${item.tag}”?`;
});

// --- Key-slip guard -----------------------------------------------------------
//
// When the card TYPE changes (binary ↔ pair), hold decision input for ~300ms so
// a rapid-keyed N can't fire "Neither" on a card the user hasn't seen.
const holdActive = ref(false);
let holdTimer = null;
let prevKind = null;

const cardRef = ref(null);
const archiveBtnRef = ref(null);

watch(
  () => current.value && `${current.value.id}`,
  () => {
    const kind = current.value?.kind ?? null;
    if (kind && prevKind && kind !== prevKind) {
      holdActive.value = true;
      if (holdTimer) clearTimeout(holdTimer);
      holdTimer = setTimeout(() => {
        holdActive.value = false;
      }, 300);
    }
    prevKind = kind;
    // A new card invalidates the previous card's blocked-decision explanation -
    // clear the live region so it can never be re-read out of context.
    announcement.value = "";
    // Focus follows the card (the container is re-keyed per card).
    nextTick(() => cardRef.value?.focus?.({ preventScroll: true }));
  },
  { immediate: true },
);

// On completion, focus moves to Archive.
watch(
  () => !current.value && !loadingEmpty.value,
  (done) => {
    if (done) nextTick(() => archiveBtnRef.value?.focus?.());
  },
);

onUnmounted(() => {
  if (holdTimer) clearTimeout(holdTimer);
});

// --- Decisions + consistency guard ----------------------------------------------
//
// Every decision routes through attempt*(…): if it contradicts a confident
// prior call on a pictured id this session, it is staged in pendingDecision
// (an inline confirm bar) instead of dispatching.
const pendingDecision = ref(null); // { kind, decision, conflict } or null

// The ONE gate for both inputs. The decision bar emits on a blocked click just
// like an unblocked one (its buttons are aria-disabled, not disabled, so they
// stay focusable) and lands here - so a blocked mouse press and a blocked key
// press produce the identical announcement, and neither issues a request.
function attemptBinary(answer) {
  if (holdActive.value || !current.value) return;
  const blocked = blockedReasons.value[answer];
  if (blocked) {
    announceBlocked(blocked);
    return;
  }
  announcement.value = "";
  const conflict = store.decisionConflict(current.value, "binary", answer);
  if (conflict) {
    pendingDecision.value = { kind: "binary", decision: answer, conflict };
    return;
  }
  store.answerBinary(answer);
}

function attemptPair(corner) {
  if (holdActive.value || !current.value) return;
  const blocked = blockedReasons.value[corner];
  if (blocked) {
    announceBlocked(blocked);
    return;
  }
  announcement.value = "";
  const conflict = store.decisionConflict(current.value, "pair", corner);
  if (conflict) {
    pendingDecision.value = { kind: "pair", decision: corner, conflict };
    return;
  }
  store.answerPair(corner);
}

/**
 * The one gate every undo goes through: the bar's button, `U`, and Ctrl+Z.
 *
 * Three outcomes, all of them answered. A locked picture set makes the decision
 * final (announced and flashed on the chip). An empty stack says so, and says
 * where the other stack is: the app-wide Ctrl+Z history is deliberately NOT
 * this one, and a user who made forty edits before opening the review would
 * otherwise conclude it had been thrown away. Otherwise the decision is
 * reopened and the card comes back.
 */
function attemptUndo() {
  if (undoBlockedReason.value) {
    announceBlocked(undoBlockedReason.value);
    return;
  }
  if (!store.canUndo) {
    announcement.value = "";
    noticeStore.push({
      level: "info",
      text: "Nothing to undo in this review. Earlier changes are still undoable from the toolbar after you close it.",
      key: "review-nothing-to-undo",
    });
    return;
  }
  announcement.value = "";
  undoAndAnnounce();
}

/**
 * Undo, then say so - in that order, and after the card has landed.
 *
 * The `current` watcher clears `announcement` on every card change, and a
 * successful undo IS a card change (the reopened card returns to the head of
 * the queue). Announcing before that lands means the watcher wipes it and the
 * success is silent, which looks exactly like a working feature.
 */
async function undoAndAnnounce() {
  await store.undo();
  await nextTick();
  announcement.value = "Undone. The last decision is back in the queue.";
}

function confirmPending() {
  const pending = pendingDecision.value;
  pendingDecision.value = null;
  if (!pending) return;
  if (pending.kind === "binary") store.answerBinary(pending.decision);
  else store.answerPair(pending.decision);
}

function cancelPending() {
  pendingDecision.value = null;
}

function doSkip() {
  pendingDecision.value = null;
  store.skip();
}

const DECISION_LABELS = {
  yes: "Yes",
  no: "No",
  both: "Both",
  neither: "Neither",
  left: "Left only",
  right: "Right only",
};

const pendingMessage = computed(() => {
  const pending = pendingDecision.value;
  if (!pending) return "";
  const { conflict } = pending;
  const tag = current.value?.tag ?? "this";
  const priorClean = conflict.asserting === "has";
  const count = priorClean ? conflict.priorNot : conflict.priorHas;
  const priorPhrase = priorClean ? "clean" : `having “${tag}”`;
  const label = DECISION_LABELS[pending.decision] || pending.decision;
  return `You've already marked #${conflict.pid} as ${priorPhrase} ${count}× this session. Apply “${label}” anyway?`;
});

// --- Keyboard (called by the overlay's capture-phase handler) --------------------
//
// Returns true when the key was consumed. Y/N/S/U on binary; B/N/L/R/S/U on
// pair; Enter/Escape resolve a pending consistency confirm; H toggles the
// evidence region. `"undo"` is the same request as `U`, sent by the overlay
// when the user presses Ctrl+Z: one undo vocabulary, two ways to say it.
function handleKey(key) {
  if (pendingDecision.value) {
    if (key === "enter") {
      confirmPending();
      return true;
    }
    if (key === "escape") {
      cancelPending();
      return true;
    }
    // Swallow decision keys while a confirm is staged.
    return ["y", "n", "b", "l", "r", "s", "u", "undo"].includes(key);
  }
  // Undo sits ABOVE the `current` guard on purpose: it does not act on the card
  // in front of you, it puts the LAST one back - which is exactly what you want
  // when the queue has just run dry and there is no current card at all.
  // It always consumes the key: `attemptUndo` answers all three outcomes
  // (blocked, nothing to undo, done), so the press was answered rather than
  // swallowed.
  if (key === "u" || key === "undo") {
    attemptUndo();
    return true;
  }
  const item = current.value;
  if (!item) return false;
  if (key === "s") {
    doSkip();
    return true;
  }
  if (key === "h") {
    store.setHeatmapEnabled(!store.heatmapEnabled);
    return true;
  }
  // The `return attempt…(x), true` comma-operator form used here reported the
  // key as consumed unconditionally - including when attempt*() bailed at its
  // lock guard - so the overlay called preventDefault() and the user got nothing
  // at all: strictly worse than the mouse path. Every branch is now an explicit
  // statement + `return true`. Consuming the key is still correct: attempt*()
  // has announced the reason, so the press was answered, not swallowed.
  if (item.kind === "pair") {
    if (key === "b") {
      attemptPair("both");
      return true;
    }
    if (key === "n") {
      attemptPair("neither");
      return true;
    }
    if (key === "l") {
      attemptPair("left");
      return true;
    }
    if (key === "r") {
      attemptPair("right");
      return true;
    }
    return false;
  }
  if (key === "y") {
    attemptBinary("yes");
    return true;
  }
  if (key === "n") {
    attemptBinary("no");
    return true;
  }
  return false;
}

defineExpose({ handleKey });
</script>

<style scoped>
.rs-session {
  flex: 1;
  min-width: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}

.rs-session-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 20px 24px 12px;
}
.rs-session-title {
  font-size: 18px;
  font-weight: var(--weight-bold);
}
.rs-session-receipt {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-session-spacer {
  flex: 1;
  min-width: 8px;
}
.rs-session-stale {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-warning));
  font-weight: var(--weight-semibold);
}
.rs-session-refresh {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border: 1px solid
    color-mix(in srgb, rgb(var(--v-theme-warning)) 55%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, rgb(var(--v-theme-warning)) 12%, transparent);
  color: rgb(var(--v-theme-warning));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.rs-xp-pill {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 5px 12px;
  border-radius: 999px;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, rgb(var(--v-theme-accent)) 22%, rgb(var(--v-theme-dark-surface))),
    color-mix(in srgb, rgb(var(--v-theme-primary)) 22%, rgb(var(--v-theme-dark-surface)))
  );
  border: 1px solid
    color-mix(in srgb, rgb(var(--v-theme-accent)) 45%, transparent);
}
.rs-xp-trophy {
  color: #ffd166;
}
.rs-xp-level {
  font-size: var(--text-2xs);
  font-weight: 800;
  letter-spacing: 0.03em;
}
.rs-xp-points {
  font-size: var(--text-2xs);
  font-weight: var(--weight-bold);
  color: rgb(var(--v-theme-accent));
  font-variant-numeric: tabular-nums;
}
.rs-xp-streak {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--text-2xs);
  font-weight: var(--weight-bold);
  color: rgb(var(--v-theme-tertiary));
}

/* `progress.locked` - the suspects this review is holding back. Warning-toned
   because it explains a missing count, not an error. */
.rs-session-locked {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-warning));
  background: color-mix(in srgb, rgb(var(--v-theme-warning)) 12%, transparent);
  border: 1px solid
    color-mix(in srgb, rgb(var(--v-theme-warning)) 45%, transparent);
}

.rs-session-tally {
  display: inline-flex;
  gap: 9px;
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
}
.rs-tally-removed {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-tally-added {
  color: rgb(var(--v-theme-dark-surface-primary));
}
.rs-tally-kept {
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-tally-skipped {
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
}

.rs-session-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 24px 20px;
}

.rs-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
/* `.rs-card` is a tabindex="-1" focus-management target: it's programmatically
   focused on every card advance so the group is announced, but it can never
   receive a keyboard-Tab focus. Chromium's sticky keyboard modality makes that
   scripted focus match :focus-visible right after a Y/N/S/U keypress, painting a
   full-card purple ring after every decision (GH #578, most visible in Electron
   on Windows). The ring is always spurious here - the real focus indicators live
   on the buttons/thumbnails - so suppress it on the container. Both properties:
   the app-wide `:focus-visible` rule in style.css paints the ring with
   `box-shadow`, so staying silent about it would put the #578 ring straight
   back, in amber instead of purple. */
.rs-card:focus,
.rs-card:focus-visible {
  outline: none;
  box-shadow: none;
}
/* Distinct entry transition while the key-slip hold is active, so a card-type
   change is visually announced. */
.rs-card--entering {
  animation: rs-card-in 0.3s ease-out;
}
@keyframes rs-card-in {
  from {
    opacity: 0.35;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
@media (prefers-reduced-motion: reduce) {
  .rs-card--entering {
    animation: none;
  }
}

.rs-state {
  margin: auto;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
}
.rs-state--error {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-state-check {
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-state-big {
  font-size: 17px;
  font-weight: var(--weight-semibold);
}
.rs-state-sub {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
  display: flex;
  gap: 10px;
}
/* The emptyScan reassurance line (Spec C item 4) is a secondary clarification,
   not the primary receipt line above it - quieter size and tone. */
.rs-state-sub--muted {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
  max-width: 420px;
}
.rs-state-actions {
  display: flex;
  gap: 10px;
  margin-top: 6px;
}
.rs-state-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 14px;
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-state-btn:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rs-state-btn--archive {
  border-color: color-mix(in srgb, rgb(var(--v-theme-dark-surface-success)) 60%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-success)) 16%, transparent);
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-state-btn--accent {
  border-color: color-mix(in srgb, rgb(var(--v-theme-accent)) 60%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 16%, transparent);
  color: rgb(var(--v-theme-accent));
}

/* Store-level error surface (defence in depth behind the pre-emptive lock
   gating: a stale lock store means a decision can still be refused server-side,
   and that refusal must be readable rather than an invisible rollback). */
.rs-error-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid
    color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 55%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-error-msg {
  flex: 1;
  font-size: var(--text-sm);
}
.rs-error-dismiss {
  flex-shrink: 0;
  height: 26px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid
    color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 55%, transparent);
  color: rgb(var(--v-theme-dark-surface-error));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.rs-confirm {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid color-mix(in srgb, rgb(var(--v-theme-warning)) 55%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-warning)) 12%, transparent);
}
.rs-confirm-msg {
  flex: 1;
  font-size: var(--text-sm);
}
.rs-confirm-actions {
  display: flex;
  gap: var(--space-2);
}
.rs-confirm-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 11px;
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-confirm-btn--apply {
  border-color: rgb(var(--v-theme-warning));
  color: rgb(var(--v-theme-warning));
}
.rs-confirm-btn kbd {
  font-family: var(--font-mono, monospace);
  font-size: 10px;
  padding: 0 4px;
  border-radius: 3px;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.3);
}
</style>
