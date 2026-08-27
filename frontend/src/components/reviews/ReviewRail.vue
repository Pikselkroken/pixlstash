<template>
  <nav ref="railRootRef" class="rs-rail">
    <!-- Close + title live in the rail (the rail owns the overlay chrome). -->
    <div class="rs-rail-head">
      <button
        class="rs-rail-close"
        type="button"
        title="Close (Esc)"
        @click="emit('close')"
      >
        <v-icon size="18">mdi-close</v-icon>
      </button>
      <h1 class="rs-rail-title">Review tags</h1>
    </div>

    <!-- Scrollable middle - navigation keeps priority over the sticker shelf. -->
    <div class="rs-rail-scroll">
      <button
        class="rs-rail-item rs-rail-board"
        :class="{ 'rs-rail-item--active': store.view.type === 'board' }"
        type="button"
        @click="store.showBoard()"
      >
        <v-icon size="17" class="rs-rail-board-icon">mdi-heart-pulse</v-icon>
        <span class="rs-rail-board-label">Tag health</span>
      </button>

      <div class="rs-rail-label">Open reviews</div>
      <!-- Each row is a wrapper DIV so the discard control can be a real
           sibling <button> (never nested inside the session button), revealed
           on hover / focus-within. -->
      <div
        v-for="s in store.sessions"
        :key="s.id"
        class="rs-rail-session-wrap"
        :class="{ 'rs-rail-session-wrap--active': isActive(s.id) }"
      >
        <button
          class="rs-rail-item rs-rail-session"
          type="button"
          @click="store.openSession(s.id)"
        >
          <span class="rs-rail-session-row">
            <span class="rs-rail-session-tag" :title="s.tag">{{ s.tag }}</span>
            <v-icon
              v-if="s.stale"
              size="14"
              class="rs-rail-stale"
              title="vault changed since this scan"
              >mdi-clock-alert-outline</v-icon
            >
            <span class="rs-rail-session-count">{{ progressText(s) }}</span>
          </span>
          <span class="rs-rail-progress">
            <span
              class="rs-rail-progress-fill"
              :style="{ width: `${progressPct(s)}%` }"
            ></span>
          </span>
        </button>
        <!-- Scope + abort share the card's bottom row. Abort is a real sibling
             button (never nested inside the session button above) so it stays
             a valid, independently focusable control; revealed on hover /
             focus-within of the wrap. The row itself also opens the session
             (matching the rest of the card) - only the abort button, which
             stops its click from bubbling here, behaves differently. -->
        <div class="rs-rail-session-meta" @click="store.openSession(s.id)">
          <span class="rs-rail-session-scope" :title="scopeLabel(s)">{{
            scopeLabel(s)
          }}</span>
          <v-icon
            v-if="scopeSetLocked(s)"
            size="12"
            class="rs-rail-scope-lock"
            :title="scopeSetLockTitle(s)"
            >mdi-lock-outline</v-icon
          >
          <button
            class="rs-rail-abort"
            type="button"
            title="Abort this review"
            @click.stop="openAbortDialog(s.id)"
          >
            <v-icon size="13">mdi-close-circle-outline</v-icon>
            Abort
          </button>
        </div>
      </div>
      <div v-if="!store.sessions.length" class="rs-rail-none">None open</div>

      <button
        ref="railNewRef"
        class="rs-rail-new"
        type="button"
        @click="emit('new-review')"
      >
        <v-icon size="16">mdi-plus</v-icon> New review
      </button>

      <template v-if="store.archived.length">
        <div class="rs-rail-label rs-rail-label--archived rs-archived-head">
          <span>Archived</span>
          <span class="rs-archived-spacer"></span>
          <!-- Clearing every receipt is permanent, so it takes two clicks
               (same arm→"Sure?"→wipe pattern as the sticker shelf clear): the
               first arms a state that auto-reverts after a few seconds, the
               second wipes the list. Only the receipts go - the decisions were
               written through during each review and are untouched. -->
          <button
            class="rs-archived-clear"
            :class="{ 'rs-archived-clear--armed': archivedClearArmed }"
            type="button"
            :title="
              archivedClearArmed
                ? 'Click again to clear every archived review - this cannot be undone'
                : 'Clear all archived reviews'
            "
            :aria-label="
              archivedClearArmed
                ? 'Confirm: clear every archived review'
                : 'Clear all archived reviews'
            "
            @click="onClearArchivedClick"
          >
            <v-icon size="14">mdi-trash-can-outline</v-icon>
            <span v-if="archivedClearArmed">Sure?</span>
          </button>
        </div>
        <!-- Each row is a wrapper DIV so the per-item delete can be a real
             sibling <button> (never nested inside the receipt button), revealed
             on hover / focus-within - mirrors the open-session rows above. -->
        <div
          v-for="a in store.archived"
          :key="a.id"
          class="rs-rail-archived-wrap"
          :class="{ 'rs-rail-archived-wrap--active': isArchivedActive(a.id) }"
        >
          <button
            class="rs-rail-item rs-rail-archived"
            type="button"
            :title="`Show the receipt for “${a.tag}”`"
            @click="store.openArchived(a.id)"
          >
            <v-icon size="14" class="rs-rail-archived-check">mdi-check</v-icon>
            <span class="rs-rail-archived-tag" :title="a.tag">{{ a.tag }}</span>
            <span class="rs-rail-archived-sum">{{ archivedSummary(a) }}</span>
          </button>
          <!-- One-click delete: a receipt is just an audit summary (decisions
               are preserved server-side), so it needs no confirm. Sibling
               button, box reserved via visibility so revealing it never
               reflows the row. -->
          <button
            class="rs-rail-archived-del"
            type="button"
            :title="`Delete the archived review for “${a.tag}”`"
            :aria-label="`Delete the archived review for ${a.tag}`"
            @click.stop="onDeleteArchived(a.id)"
          >
            <v-icon size="13">mdi-close</v-icon>
          </button>
        </div>
      </template>
    </div>

    <!-- Abort dialog: aborting always discards the remaining queue; the
         reviewer chooses what happens to the changes already written through.
         Skipped items are not changes and are never bulk-undone. -->
    <div
      v-if="abortDialog"
      class="rs-abort-backdrop"
      @click.self="abortDialog = null"
    >
      <div
        class="rs-abort"
        role="dialog"
        aria-modal="true"
        aria-label="Abort review"
      >
        <h3 class="rs-abort-title">Abort “{{ abortDialog.tag }}”?</h3>
        <p class="rs-abort-msg">
          You made {{ abortDialog.changes }} change{{
            abortDialog.changes === 1 ? "" : "s"
          }}
          in this review.
        </p>
        <div class="rs-abort-actions">
          <button
            class="rs-abort-btn rs-abort-btn--keep"
            type="button"
            title="Abort the review; the changes stand"
            @click="abortKeep"
          >
            Keep {{ abortDialog.changes }} change{{
              abortDialog.changes === 1 ? "" : "s"
            }}
          </button>
          <button
            class="rs-abort-btn rs-abort-btn--undo"
            type="button"
            title="Reverse every change this review made, then abort"
            @click="abortUndo"
          >
            Undo {{ abortDialog.changes }} change{{
              abortDialog.changes === 1 ? "" : "s"
            }}
          </button>
          <button
            class="rs-abort-btn"
            type="button"
            @click="abortDialog = null"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>

    <!-- Sticker shelf: earned rewards live here. Capped (~1/3 rail height) and
         scrollable so it always yields space to the navigation above; stickers
         shrink when the collection grows. Only shown while "Pretend this is
         fun" is on - the collection is kept, not cleared, so toggling the
         setting back restores it. -->
    <div v-if="store.gamify && store.stickers.length" class="rs-shelf">
      <div class="rs-rail-label rs-shelf-label">
        <v-icon size="13">mdi-sticker-circle-outline</v-icon>
        Stickers
        <span class="rs-shelf-count">· {{ store.stickers.length }}</span>
        <span class="rs-shelf-spacer"></span>
        <!-- Clearing is permanent (the collection is not recoverable), so it
             takes two clicks: the first arms a "Sure?" state that auto-reverts
             after a few seconds, the second wipes the shelf. -->
        <button
          class="rs-shelf-clear"
          :class="{ 'rs-shelf-clear--armed': clearArmed }"
          type="button"
          :title="
            clearArmed
              ? 'Click again to clear every sticker - this cannot be undone'
              : 'Clear all stickers'
          "
          @click="onClearClick"
        >
          <v-icon size="14">mdi-trash-can-outline</v-icon>
          <span v-if="clearArmed">Sure?</span>
        </button>
        <button
          class="rs-shelf-toggle"
          type="button"
          :title="shelfOpen ? 'Collapse the shelf' : 'Show the shelf'"
          :aria-expanded="shelfOpen"
          @click="shelfOpen = !shelfOpen"
        >
          <v-icon size="15">{{
            shelfOpen ? "mdi-chevron-down" : "mdi-chevron-up"
          }}</v-icon>
        </button>
      </div>
      <div v-if="shelfOpen" class="rs-shelf-grid">
        <ReviewSticker
          v-for="(s, i) in store.stickers"
          :key="s.id"
          :icon="s.icon"
          :color="s.color"
          :size="store.stickers.length > 12 ? 27 : 34"
          :tilt="((i % 5) - 2) * 4"
          :fresh="i === store.stickers.length - 1"
          :label="s.tag ? `${s.label} - earned reviewing “${s.tag}”` : s.label"
        />
      </div>
    </div>
  </nav>
</template>

<script setup>
import { nextTick, onUnmounted, ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import ReviewSticker from "./ReviewSticker.vue";

const emit = defineEmits(["close", "new-review"]);
const store = useReviewSessionsStore();

const abortDialog = ref(null); // { id, tag, changes } | null
const shelfOpen = ref(true);
const railRootRef = ref(null);
const railNewRef = ref(null);

// Two-step sticker clear: first click arms, second (within the window) wipes.
const clearArmed = ref(false);
let clearArmTimer = null;
const CLEAR_ARM_MS = 3000;

function onClearClick() {
  clearTimeout(clearArmTimer);
  if (!clearArmed.value) {
    clearArmed.value = true;
    clearArmTimer = setTimeout(() => {
      clearArmed.value = false;
    }, CLEAR_ARM_MS);
    return;
  }
  clearArmed.value = false;
  store.clearStickers();
}

// Two-step clear-all for the Archived list - same arm→confirm→wipe pattern as
// the sticker shelf clear above, with its own ref + timer (never shared).
const archivedClearArmed = ref(false);
let archivedClearTimer = null;

// The whole Archived section unmounts when the list empties, so the button the
// user just pressed disappears and focus would fall to <body>. Capture the
// fallback target BEFORE the store call (it's still mounted), then move focus
// once the removal has rendered.
function railNewEl() {
  return railNewRef.value?.$el ?? railNewRef.value ?? null;
}

async function onClearArchivedClick() {
  clearTimeout(archivedClearTimer);
  if (!archivedClearArmed.value) {
    archivedClearArmed.value = true;
    archivedClearTimer = setTimeout(() => {
      archivedClearArmed.value = false;
    }, CLEAR_ARM_MS);
    return;
  }
  archivedClearArmed.value = false;
  const fallback = railNewEl();
  await store.clearArchived();
  await nextTick();
  fallback?.focus();
}

// Per-item delete: focus the next archived row's delete button, or the rail's
// "New review" button when that was the last archived item.
async function onDeleteArchived(id) {
  const index = store.archived.findIndex((a) => a.id === id);
  const wasLast = store.archived.length <= 1;
  const fallback = railNewEl();
  await store.deleteArchived(id);
  await nextTick();
  if (wasLast) {
    fallback?.focus();
    return;
  }
  const dels =
    railRootRef.value?.querySelectorAll(".rs-rail-archived-del") ?? [];
  const next = dels[Math.min(Math.max(index, 0), dels.length - 1)];
  (next ?? fallback)?.focus();
}

onUnmounted(() => {
  clearTimeout(clearArmTimer);
  clearTimeout(archivedClearTimer);
});

function isActive(id) {
  return store.view.type === "session" && store.view.id === id;
}

function isArchivedActive(id) {
  return store.view.type === "archived" && store.view.id === id;
}

// N = decided changes (skips are not changes). With zero changes there is
// nothing to keep or undo, so abort straight away.
function openAbortDialog(id) {
  const s = store.sessions.find((x) => x.id === id);
  const changes = store.decidedCountFor(id);
  if (!changes) {
    store.abortSession(id);
    return;
  }
  abortDialog.value = { id, tag: s?.tag ?? "", changes };
}

function abortKeep() {
  const d = abortDialog.value;
  abortDialog.value = null;
  if (d) store.abortSession(d.id);
}

function abortUndo() {
  const d = abortDialog.value;
  abortDialog.value = null;
  if (d) store.undoChangesAndAbort(d.id);
}

function foundOf(s) {
  return s.stats?.found ?? 0;
}

function doneOf(s) {
  return s.progress?.done ?? 0;
}

function progressPct(s) {
  const found = foundOf(s);
  return found ? Math.round((doneOf(s) / found) * 100) : 0;
}

// "21/23 · 2 skipped" - the skipped tail is visible from the rail.
function progressText(s) {
  const skipped = store.skippedCountFor(s.id);
  const base = `${doneOf(s)}/${foundOf(s)}`;
  return skipped > 0 ? `${base} · ${skipped} skipped` : base;
}

// Resolve the frozen scope JSON to a short label using the option lists the
// store loaded for the creation dialog. Unknown ids degrade to the raw id.
function scopeLabel(s) {
  const scope = s.scope || {};
  const parts = [];
  if (scope.project_id != null) {
    const p = store.projects.find((x) => x.id === scope.project_id);
    parts.push(`Project: ${p?.name ?? scope.project_id}`);
  }
  if (scope.set_id != null) {
    const set = store.sets.find((x) => x.id === scope.set_id);
    parts.push(`Set: ${set?.name ?? scope.set_id}`);
  }
  if (scope.character_id != null && scope.character_id !== "") {
    if (String(scope.character_id) === "UNASSIGNED") {
      parts.push("Character: Unassigned");
    } else {
      const c = store.characters.find(
        (x) => String(x.id) === String(scope.character_id),
      );
      parts.push(`Character: ${c?.name ?? scope.character_id}`);
    }
  }
  return parts.length ? parts.join(" · ") : "Whole vault";
}

// A session whose frozen scope is a set that is now locked: flag it in the rail
// so the user sees why its cards are reference-frozen (the scan already excludes
// locked suspects; this is the at-a-glance marker on the session row).
function scopeSet(s) {
  const setId = s.scope?.set_id;
  if (setId == null) return null;
  return store.sets.find((x) => x.id === setId) || null;
}

function scopeSetLocked(s) {
  return !!scopeSet(s)?.locked;
}

function scopeSetLockTitle(s) {
  const set = scopeSet(s);
  const name = set?.name ?? s.scope?.set_id;
  return `'${name}' is locked - its pictures are read-only.`;
}

function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function archivedSummary(a) {
  const reviewed = a.stats?.found ?? 0;
  const when = shortDate(a.refreshed_at || a.created_at);
  return when ? `${reviewed} reviewed · ${when}` : `${reviewed} reviewed`;
}
</script>

<style scoped>
.rs-rail {
  width: 244px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: var(--space-3);
  background: rgba(var(--v-theme-on-dark-surface), 0.04);
  border-right: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
}

.rs-rail-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-2);
  border-radius: var(--radius-sm);
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-primary)) 12%,
    rgb(var(--v-theme-dark-surface))
  );
  border: 1px solid
    color-mix(in srgb, rgb(var(--v-theme-primary)) 25%, transparent);
}
.rs-rail-close {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-primary));
  background: rgb(var(--v-theme-primary));
  transition: filter 0.12s;
}
.rs-rail-close:hover {
  filter: brightness(0.85);
}
.rs-rail-title {
  font-size: 0.95rem;
  font-weight: var(--weight-bold);
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.rs-rail-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.rs-rail-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 100%;
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-rail-item:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}
.rs-rail-item--active {
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
}

.rs-rail-board {
  flex-direction: row;
  align-items: center;
  gap: var(--space-2);
}
.rs-rail-board-icon {
  color: rgb(var(--v-theme-accent));
}
.rs-rail-board-label {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.rs-rail-label {
  padding: var(--space-3) var(--space-3) var(--space-1);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

/* The wrap itself carries the card surface (hover / active) so it extends
   under the abort row below - otherwise the shaded background stops at the
   session button and abort reads as floating outside the card. */
.rs-rail-session-wrap {
  border-radius: var(--radius-sm);
}
.rs-rail-session-wrap:hover,
.rs-rail-session-wrap:focus-within,
.rs-rail-session-wrap--active {
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
}
.rs-rail-session-wrap:hover .rs-rail-session,
.rs-rail-session-wrap:focus-within .rs-rail-session,
.rs-rail-session-wrap--active .rs-rail-session {
  background: transparent;
}

.rs-rail-session-row {
  display: flex;
  align-items: center;
  gap: 7px;
}
.rs-rail-session-tag {
  flex: 1;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rs-rail-stale {
  color: rgb(var(--v-theme-warning));
}
.rs-rail-session-count {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.rs-rail-progress {
  height: 3px;
  border-radius: 2px;
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
  overflow: hidden;
}
.rs-rail-progress-fill {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-accent));
  transition: width 0.2s;
}
/* Bottom row of the card: scope label + abort share one line. Also opens the
   session on click (like the rest of the card) - cursor says so too. */
.rs-rail-session-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 0 var(--space-3) var(--space-2);
  cursor: pointer;
}
.rs-rail-session-scope {
  flex: 1;
  min-width: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rs-rail-scope-lock {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

/* Abort control: a real sibling button, invisible until the row is hovered or
   anything in it has focus (focus-within keeps it keyboard-reachable).
   `visibility` (not `display`) so its box stays reserved in the meta row at
   all times - otherwise the row (and the whole card) grows taller the moment
   the button appears, since it's taller than the scope label beside it. */
.rs-rail-abort {
  visibility: hidden;
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-rail-session-wrap:hover .rs-rail-abort,
.rs-rail-session-wrap:focus-within .rs-rail-abort {
  visibility: visible;
}
.rs-rail-abort:hover {
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
}

.rs-abort-backdrop {
  position: fixed;
  inset: 0;
  z-index: 4350;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}
.rs-abort {
  width: 360px;
  max-width: calc(100vw - 32px);
  padding: 18px;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rs-abort-title {
  font-size: 15px;
  font-weight: var(--weight-bold);
}
.rs-abort-msg {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-dark-surface), 0.8);
}
.rs-abort-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.rs-abort-btn {
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
}
.rs-abort-btn--keep {
  border-color: color-mix(in srgb, rgb(var(--v-theme-dark-surface-success)) 60%, transparent);
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-abort-btn--undo {
  border-color: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 60%, transparent);
  color: rgb(var(--v-theme-dark-surface-error));
}

.rs-rail-none {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.rs-rail-new {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  margin: var(--space-2) 2px 2px;
  height: 34px;
  border: 1px dashed rgba(var(--v-theme-on-dark-surface), 0.3);
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}
.rs-rail-new:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}

.rs-rail-label--archived {
  padding-top: var(--space-4);
}
/* Archived header carries the "Clear all" control on its trailing edge. */
.rs-archived-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.rs-archived-spacer {
  flex: 1;
}
/* Clear-all: mirrors the sticker-shelf clear (.rs-shelf-clear) so both
   destructive clears read identically. */
.rs-archived-clear {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-archived-clear:hover {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-archived-clear--armed {
  color: rgb(var(--v-theme-dark-surface-error));
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
}

/* The wrap carries the card surface (hover / active) so it extends under the
   delete button beside the receipt button - same structure as the session
   rows above. */
.rs-rail-archived-wrap {
  display: flex;
  align-items: center;
  border-radius: var(--radius-sm);
}
.rs-rail-archived-wrap:hover,
.rs-rail-archived-wrap:focus-within,
.rs-rail-archived-wrap--active {
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
}
.rs-rail-archived-wrap:hover .rs-rail-archived,
.rs-rail-archived-wrap:focus-within .rs-rail-archived,
.rs-rail-archived-wrap--active .rs-rail-archived {
  background: transparent;
}
.rs-rail-archived {
  flex: 1;
  min-width: 0;
  flex-direction: row;
  align-items: center;
  gap: var(--space-2);
  padding: 7px var(--space-3);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-rail-archived-check {
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-rail-archived-tag {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rs-rail-archived-sum {
  font-size: 11px;
  white-space: nowrap;
}

/* Per-item delete: a real sibling button, invisible until the row is hovered or
   anything in it has focus (focus-within keeps it keyboard-reachable).
   `visibility` (not `display`) so its box stays reserved and revealing it never
   reflows the row - same technique as the session abort control. */
.rs-rail-archived-del {
  visibility: hidden;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-right: var(--space-1);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-rail-archived-wrap:hover .rs-rail-archived-del,
.rs-rail-archived-wrap:focus-within .rs-rail-archived-del {
  visibility: visible;
}
.rs-rail-archived-del:hover {
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
}

/* Sticker shelf: hard-capped height + own scroll so navigation always wins
   the space fight. */
.rs-shelf {
  flex-shrink: 0;
  max-height: 34%;
  display: flex;
  flex-direction: column;
  padding-top: var(--space-2);
  margin-top: var(--space-2);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rs-shelf-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 var(--space-3) 5px;
  flex-shrink: 0;
}
.rs-shelf-count {
  font-weight: var(--weight-medium);
}
.rs-shelf-spacer {
  flex: 1;
}
.rs-shelf-toggle {
  display: inline-flex;
  padding: 1px;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-shelf-clear {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 3px;
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-shelf-clear:hover {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-shelf-clear--armed {
  color: rgb(var(--v-theme-dark-surface-error));
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
}
.rs-shelf-grid {
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 2px 9px 4px;
  align-content: flex-start;
}
</style>
