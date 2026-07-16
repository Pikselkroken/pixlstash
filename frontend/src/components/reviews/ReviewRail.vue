<template>
  <nav class="rs-rail">
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

    <!-- Scrollable middle — navigation keeps priority over the sticker shelf. -->
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
            <span class="rs-rail-session-tag">{{ s.tag }}</span>
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
          <span class="rs-rail-session-scope">{{ scopeLabel(s) }}</span>
        </button>
        <!-- Abort: opens a dialog offering to keep or undo the review's
             changes before discarding the session. -->
        <button
          class="rs-rail-abort"
          type="button"
          title="Abort this review"
          @click="openAbortDialog(s.id)"
        >
          <v-icon size="13">mdi-close-circle-outline</v-icon>
          Abort
        </button>
      </div>
      <div v-if="!store.sessions.length" class="rs-rail-none">None open</div>

      <button class="rs-rail-new" type="button" @click="emit('new-review')">
        <v-icon size="16">mdi-plus</v-icon> New review
      </button>

      <template v-if="store.archived.length">
        <div class="rs-rail-label rs-rail-label--archived">Archived</div>
        <button
          v-for="a in store.archived"
          :key="a.id"
          class="rs-rail-item rs-rail-archived"
          :class="{ 'rs-rail-item--active': isArchivedActive(a.id) }"
          type="button"
          :title="`Show the receipt for “${a.tag}”`"
          @click="store.openArchived(a.id)"
        >
          <v-icon size="14" class="rs-rail-archived-check">mdi-check</v-icon>
          <span class="rs-rail-archived-tag">{{ a.tag }}</span>
          <span class="rs-rail-archived-sum">{{ archivedSummary(a) }}</span>
        </button>
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
         shrink when the collection grows. -->
    <div v-if="store.stickers.length" class="rs-shelf">
      <div class="rs-rail-label rs-shelf-label">
        <v-icon size="13">mdi-sticker-circle-outline</v-icon>
        Stickers
        <span class="rs-shelf-count">· {{ store.stickers.length }}</span>
        <span class="rs-shelf-spacer"></span>
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
          :label="s.tag ? `${s.label} — earned reviewing “${s.tag}”` : s.label"
        />
      </div>
    </div>
  </nav>
</template>

<script setup>
import { ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import ReviewSticker from "./ReviewSticker.vue";

const emit = defineEmits(["close", "new-review"]);
const store = useReviewSessionsStore();

const abortDialog = ref(null); // { id, tag, changes } | null
const shelfOpen = ref(true);

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

// "21/23 · 2 skipped" — the skipped tail is visible from the rail.
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

.rs-rail :is(button):focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
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
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
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
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  background: transparent;
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

.rs-rail-session-wrap {
  border-radius: var(--radius-sm);
}
.rs-rail-session-wrap--active .rs-rail-session {
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
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
.rs-rail-session-scope {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Abort control: a real sibling button, hidden until the row is hovered or
   anything in it has focus (focus-within keeps it keyboard-reachable). */
.rs-rail-abort {
  display: none;
  align-items: center;
  gap: 4px;
  margin: 2px var(--space-3) 4px;
  padding: 2px 6px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-error));
  cursor: pointer;
}
.rs-rail-session-wrap:hover .rs-rail-abort,
.rs-rail-session-wrap:focus-within .rs-rail-abort {
  display: inline-flex;
}
.rs-rail-abort:hover {
  background: color-mix(in srgb, rgb(var(--v-theme-error)) 12%, transparent);
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
  cursor: pointer;
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
}
.rs-abort-btn--keep {
  border-color: color-mix(in srgb, rgb(var(--v-theme-success)) 60%, transparent);
  color: rgb(var(--v-theme-success));
}
.rs-abort-btn--undo {
  border-color: color-mix(in srgb, rgb(var(--v-theme-error)) 60%, transparent);
  color: rgb(var(--v-theme-error));
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
  cursor: pointer;
  background: transparent;
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
.rs-rail-archived {
  flex-direction: row;
  align-items: center;
  gap: var(--space-2);
  padding: 7px var(--space-3);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-rail-archived-check {
  color: rgb(var(--v-theme-success));
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
  border: none;
  background: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
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
