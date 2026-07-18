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
          title="Append newly-found suspects — decided cards are never resurrected"
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
          The board's Priority number is a fast estimate — the review scan is
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

      <!-- Completion: the queue is empty — a real state with a receipt. -->
      <div v-else-if="!current" class="rs-state rs-state--done">
        <v-icon size="48" class="rs-state-check">mdi-check-decagram</v-icon>
        <p class="rs-state-big">
          Review complete — {{ found }} suspect{{ found === 1 ? "" : "s" }}
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

      <ReviewDecisionBar
        v-if="current"
        :kind="current.kind === 'pair' ? 'pair' : 'binary'"
        :direction="current.direction"
        :can-undo="store.canUndo"
        :gamify="store.gamify"
        :hold="holdActive"
        :locked="suspectLocked"
        :lock-reason="suspectLockReason"
        @answer="attemptBinary"
        @corner="attemptPair"
        @skip="doSkip"
        @undo="store.undo()"
        @gamify-toggle="store.setGamify($event)"
      />
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import ReviewBinaryCard from "./ReviewBinaryCard.vue";
import ReviewPairCard from "./ReviewPairCard.vue";
import ReviewDecisionBar from "./ReviewDecisionBar.vue";
import ReviewCelebration from "./ReviewCelebration.vue";

const props = defineProps({
  session: { type: Object, required: true },
});

const store = useReviewSessionsStore();
const lockedSetsStore = useLockedSetsStore();

const current = computed(() => store.current);

// The suspect (the editable picture) can become locked mid-session if its set
// was locked after this review opened — the scan excludes locked suspects on
// refresh, but an already-materialised card can still surface one. Deciding it
// would write the frozen label ledger and 423, so decisions are gated here (in
// addition to the decision bar's disabled buttons) and the keyboard path below.
const suspectLocked = computed(
  () => !!current.value && lockedSetsStore.isLocked(current.value.picture_id),
);
const suspectLockReason = computed(() =>
  current.value ? lockedSetsStore.lockReason(current.value.picture_id) : "",
);
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

// XP/level/streak: monotonic counters of decisions made — Undo never
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

function attemptBinary(answer) {
  if (holdActive.value || !current.value || suspectLocked.value) return;
  const conflict = store.decisionConflict(current.value, "binary", answer);
  if (conflict) {
    pendingDecision.value = { kind: "binary", decision: answer, conflict };
    return;
  }
  store.answerBinary(answer);
}

function attemptPair(corner) {
  if (holdActive.value || !current.value || suspectLocked.value) return;
  const conflict = store.decisionConflict(current.value, "pair", corner);
  if (conflict) {
    pendingDecision.value = { kind: "pair", decision: corner, conflict };
    return;
  }
  store.answerPair(corner);
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
// evidence region.
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
    return ["y", "n", "b", "l", "r", "s", "u"].includes(key);
  }
  const item = current.value;
  if (!item) return false;
  if (key === "s") {
    doSkip();
    return true;
  }
  if (key === "u") {
    store.undo();
    return true;
  }
  if (key === "h") {
    store.setHeatmapEnabled(!store.heatmapEnabled);
    return true;
  }
  if (item.kind === "pair") {
    if (key === "b") return attemptPair("both"), true;
    if (key === "n") return attemptPair("neither"), true;
    if (key === "l") return attemptPair("left"), true;
    if (key === "r") return attemptPair("right"), true;
    return false;
  }
  if (key === "y") return attemptBinary("yes"), true;
  if (key === "n") return attemptBinary("no"), true;
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
.rs-session :is(button):focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
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
  cursor: pointer;
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

.rs-session-tally {
  display: inline-flex;
  gap: 9px;
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
}
.rs-tally-removed {
  color: rgb(var(--v-theme-error));
}
.rs-tally-added {
  color: rgb(var(--v-theme-primary));
}
.rs-tally-kept {
  color: rgb(var(--v-theme-success));
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
.rs-card:focus {
  outline: none;
}
.rs-card:focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: -2px;
  border-radius: var(--radius-md);
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
  color: rgb(var(--v-theme-error));
}
.rs-state-check {
  color: rgb(var(--v-theme-success));
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
   not the primary receipt line above it — quieter size and tone. */
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
  cursor: pointer;
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
  border-color: color-mix(in srgb, rgb(var(--v-theme-success)) 60%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-success)) 16%, transparent);
  color: rgb(var(--v-theme-success));
}
.rs-state-btn--accent {
  border-color: color-mix(in srgb, rgb(var(--v-theme-accent)) 60%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 16%, transparent);
  color: rgb(var(--v-theme-accent));
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
  cursor: pointer;
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
