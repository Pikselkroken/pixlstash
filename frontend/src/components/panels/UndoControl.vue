<script setup>
/**
 * The toolbar history control - the persistent half of undo/redo.
 *
 * An undo/redo icon pair plus a chevron, in the main toolbar, which is in the
 * same place in the Electron shell and in the browser. The receipt is transient
 * and lives over the grid; this is what carries undo once the pill is gone, and
 * it is where the shortcut gets taught after that.
 *
 * Behaviour from the owner's "Undo / Redo System" design:
 *
 *   • Disabled-looking until there is history, but never `disabled`: the design
 *     requires the buttons to stay tabbable and to keep their tooltip
 *     ("Nothing to undo"), both of which the native attribute discards. So
 *     `aria-disabled` + a guarded handler, with the repo's own 35% disabled
 *     treatment applied by hand.
 *   • The tooltip names the exact step and its shortcut.
 *   • The chevron opens a History popover: newest first, hovering (or arrowing
 *     onto) a step previews how far back you would go by highlighting every
 *     step that would be undone, and activating it undoes to that step.
 *   • Steps that have been undone stay visible, struck through, until a new
 *     action supersedes them.
 *
 * Keyboard: the list is a real listbox-style menu of buttons in tab order, and
 * the preview follows focus as well as hover, so a keyboard user sees the same
 * range a mouse user does.
 */
import { computed, nextTick, ref, watch } from "vue";

import { isReadOnly } from "../../utils/apiClient";
import {
  formatOperationTime,
  iconForOpType,
  summarizeOperation,
  useOperationStore,
} from "../../stores/useOperationStore";
import {
  isApplePlatform,
  redoKeyHint,
  undoKeyHint,
} from "../../utils/shortcutHints";

const store = useOperationStore();

const menuOpen = ref(false);
/**
 * The oldest step the pointer/focus is previewing, held by operation ID rather
 * than by list position: a refresh landing while the popover is open would
 * otherwise leave the highlight describing a different range than the one the
 * user is looking at.
 */
const previewId = ref(null);
const listEl = ref(null);
// The chevron cannot carry its own template ref: Vuetify's activator slot props
// already bind one (`mergeProps({ ref: activatorRef }, …)` in VOverlay), and a
// literal `ref` on the same element would win and break the menu's anchoring.
// Reach it through the component root instead.
const rootEl = ref(null);

function focusChevron() {
  rootEl.value?.querySelector?.(".uc-btn--chevron")?.focus?.();
}

const past = computed(() => store.past);
const future = computed(() => store.future);

const undoKeys = undoKeyHint();
const redoKeys = redoKeyHint();

// A read-only (share-token) session keeps the control MOUNTED and inert rather
// than hiding it: the demo has to show that undo exists. There is nothing to
// soften here - `/operations*` is owner-only, so the store never reads a stack
// and every affordance below states that reason instead of implying an empty
// history. Same `aria-disabled` treatment as "nothing to undo", so the buttons
// stay tabbable and keep explaining themselves.
const UNAVAILABLE = "only available in your own library";
const canUndo = computed(
  () => store.canUndo && !store.busy && !isReadOnly.value,
);
const canRedo = computed(
  () => store.canRedo && !store.busy && !isReadOnly.value,
);

/** What one Ctrl+Z would take back right now, for the tooltip. */
const undoLabel = computed(() =>
  store.nextUndo ? summarizeOperation(store.nextUndo) : "Nothing to undo",
);
const redoLabel = computed(() =>
  store.nextRedo ? summarizeOperation(store.nextRedo) : "Nothing to redo",
);

// The undo target can quietly become another tab's action: an external
// operation updates the stack silently (the receipt only narrates this
// client's own work), so the affordance has to say so before it reverts
// something the user never did.
const undoTitle = computed(() => {
  if (isReadOnly.value) return `Undo is ${UNAVAILABLE}`;
  if (!store.nextUndo) return `Nothing to undo (${undoKeys.join("+")})`;
  const where = store.nextUndoIsExternal ? "Changed elsewhere: " : "";
  return `Undo: ${where}${undoLabel.value} (${undoKeys.join("+")})`;
});
const redoTitle = computed(() => {
  if (isReadOnly.value) return `Redo is ${UNAVAILABLE}`;
  return store.nextRedo
    ? `Redo: ${redoLabel.value} (${redoKeys.join("+")})`
    : `Nothing to redo (${redoKeys.join("+")})`;
});

// The standard attribute for "this control has a keyboard shortcut". It
// survives the visual keycaps being aria-hidden and is read on focus, which
// `title` never is.
const undoKeyShortcut = isApplePlatform() ? "Meta+Z" : "Control+Z";
const redoKeyShortcut = isApplePlatform() ? "Shift+Meta+Z" : "Control+Y";

/** Rows, newest first: the redo side struck through, then the undo stack. */
const rows = computed(() => {
  const undoneRows = future.value.map((op) => ({
    op,
    kind: "future",
    index: -1,
  }));
  const appliedRows = past.value.map((op, index) => ({
    op,
    kind: "past",
    index,
  }));
  return [...undoneRows, ...appliedRows];
});

/** Where the previewed step currently sits in the stack, or -1. */
const previewIndex = computed(() =>
  previewId.value == null
    ? -1
    : past.value.findIndex((op) => op?.id === previewId.value),
);

/** How many steps the current preview would undo. */
const previewSteps = computed(() =>
  previewIndex.value < 0 ? 0 : previewIndex.value + 1,
);

const footerText = computed(() => {
  // "Choose", not "Click": the list is fully keyboard-operable and this is the
  // only sentence that says how to use it.
  if (previewSteps.value === 0) return "Choose a step to undo back to it";
  return `Undo ${previewSteps.value} step${previewSteps.value === 1 ? "" : "s"}`;
});

/** A past row is in the preview range when it is at or above the previewed one. */
function willUndo(row) {
  if (row.kind !== "past") return false;
  if (previewIndex.value < 0) return false;
  return row.index <= previewIndex.value;
}

/** How many steps activating this row would undo (for its accessible name). */
function rowSteps(row) {
  return row.kind === "past" ? row.index + 1 : 0;
}

function preview(row) {
  previewId.value = row.kind === "past" ? row.op.id : null;
}

function rowIcon(op) {
  return iconForOpType(op?.op_type);
}

function rowLabel(op) {
  return summarizeOperation(op);
}

function rowTime(op) {
  return formatOperationTime(op?.created_at);
}

function onUndo() {
  if (!canUndo.value) return;
  store.undo();
}

function onRedo() {
  if (!canRedo.value) return;
  store.redo();
}

async function onPick(row) {
  if (row.kind !== "past" || store.busy) return;
  menuOpen.value = false;
  previewId.value = null;
  // Vuetify restores focus to the activator on Esc and on Tab-past-the-last
  // focusable, but not on a programmatic close: without this the row button is
  // unmounted under the keyboard and focus lands on <body> (WCAG 2.4.3).
  await nextTick();
  focusChevron();
  await store.undoTo(row.op.id);
}

/**
 * The popover is opened long after mount, and Vuetify keeps menu content in the
 * DOM once it has been opened, so the read is driven by the open flag rather
 * than by `onMounted` - otherwise the list would show whatever the stack looked
 * like the first time it was opened.
 */
watch(menuOpen, async (isOpen) => {
  previewId.value = null;
  if (!isOpen) return;
  store.refresh({ narrate: false });
  await nextTick();
  // Focus the newest undoable step so the popover is operable from the keyboard
  // the moment it opens.
  listEl.value?.querySelector?.("button.uc-row--past")?.focus?.();
});

defineExpose({
  /** Open the History popover (for a future menu/command-palette entry). */
  openHistory() {
    menuOpen.value = true;
  },
});
</script>

<template>
  <div ref="rootEl" class="uc" role="group" aria-label="Undo and redo">
    <div class="uc-group" :class="{ 'uc-group--open': menuOpen }">
      <button
        type="button"
        class="uc-btn uc-btn--undo"
        :aria-disabled="!canUndo"
        :title="undoTitle"
        :aria-label="undoTitle"
        :aria-keyshortcuts="undoKeyShortcut"
        @click="onUndo"
      >
        <v-icon size="19">mdi-undo-variant</v-icon>
      </button>
      <button
        type="button"
        class="uc-btn uc-btn--redo"
        :aria-disabled="!canRedo"
        :title="redoTitle"
        :aria-label="redoTitle"
        :aria-keyshortcuts="redoKeyShortcut"
        @click="onRedo"
      >
        <v-icon size="19">mdi-redo-variant</v-icon>
      </button>
      <v-menu
        v-model="menuOpen"
        :close-on-content-click="false"
        location="bottom end"
        origin="top end"
        :offset="8"
        transition="scale-transition"
        :activator-props="{ 'aria-haspopup': 'dialog' }"
      >
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            type="button"
            class="uc-btn uc-btn--chevron"
            title="History"
            aria-label="History"
            :aria-expanded="menuOpen"
          >
            <v-icon size="18" class="uc-chevron">mdi-menu-down</v-icon>
          </button>
        </template>
        <div
          class="tbm uc-panel"
          role="dialog"
          aria-label="History"
          @mouseleave="previewId = null"
        >
          <span class="tbm-caret tbm-caret--icon-center-end"></span>
          <div class="tbm-header">
            <v-icon size="18" class="tbm-header-icon">mdi-history</v-icon>
            <span class="tbm-title">History</span>
            <span class="tbm-spacer"></span>
            <!-- No tally in a read-only session: the stack was never read, so
                 "0 steps" would be a claim about the library rather than a
                 count of what is on screen. -->
            <span v-if="!isReadOnly" class="uc-count"
              >{{ store.historyCount }} step{{
                store.historyCount === 1 ? "" : "s"
              }}</span
            >
          </div>
          <div ref="listEl" class="uc-list">
            <p v-if="isReadOnly" class="uc-empty">
              History is only available in your own library. There, every change
              is listed here newest first, and you can step back to any of them.
            </p>
            <p v-else-if="!rows.length" class="uc-empty">
              Nothing recorded yet. Your next change lands here.
            </p>
            <button
              v-for="row in rows"
              :key="`${row.kind}-${row.op.id}`"
              type="button"
              class="uc-row"
              :class="{
                'uc-row--past': row.kind === 'past',
                'uc-row--future': row.kind === 'future',
                'uc-row--willundo': willUndo(row),
              }"
              :disabled="row.kind !== 'past'"
              :aria-label="
                row.kind === 'past'
                  ? `Undo back to: ${rowLabel(row.op)} (${rowSteps(row)} step${
                      rowSteps(row) === 1 ? '' : 's'
                    })`
                  : `${rowLabel(row.op)} (undone)`
              "
              @mouseenter="preview(row)"
              @focus="preview(row)"
              @click="onPick(row)"
              @keydown.enter.stop.prevent="onPick(row)"
            >
              <v-icon size="18" class="uc-row-icon">{{
                rowIcon(row.op)
              }}</v-icon>
              <span class="uc-row-label">{{ rowLabel(row.op) }}</span>
              <span class="uc-row-time">{{ rowTime(row.op) }}</span>
            </button>
          </div>
          <!-- The footer teaches the one gesture the list answers to. A
               read-only session has no list, so it would be teaching nothing. -->
          <div v-if="!isReadOnly" class="tbm-footer">
            <v-icon size="16">mdi-gesture-tap</v-icon>
            <span>{{ footerText }}</span>
          </div>
        </div>
      </v-menu>
    </div>
  </div>
</template>

<style scoped>
.uc {
  position: relative;
  display: flex;
  align-items: center;
}

/* Mirrors `.bar-split-button` / `.bar-btn` from the toolbar. Deliberately its
   own class names rather than reusing those: they live in Toolbar.vue's SCOPED
   style block, so a child component gets none of them, and promoting them into
   App.css is a toolbar-wide refactor that belongs to the lane that owns this
   area - not to this change. Recorded as a follow-up. */
.uc-group {
  display: flex;
  align-items: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
}

/* Open: the whole group adopts the panel fill + border so it reads as one
   object with its menu, exactly as the sort split button does. */
.uc-group--open {
  border-color: rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-panel));
}

.uc-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  /* 32×32, not the design's 18px-wide chevron: an 18px target is under the
     WCAG 2.5.8 24×24 floor. */
  width: 32px;
  height: 32px;
  padding: 0;
  flex-shrink: 0;
  color: rgb(var(--v-theme-toolbar-text));
  /* A transparent 1px border is reserved so the open state does not resize the
     button and make the group jump. */
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  box-sizing: border-box;
  font-family: inherit;
}

.uc-btn:hover {
  background: rgba(var(--v-theme-toolbar-text), 0.1);
}

/* `aria-disabled`, not the `disabled` attribute: the design requires the
   buttons to stay tabbable and to keep naming the step ("Nothing to undo"),
   and a `disabled` button is neither focusable nor tooltip-bearing. The visual
   is the repo's own disabled treatment minus the pointer-events kill. */
.uc-btn[aria-disabled="true"] {
  opacity: 0.35;
  filter: grayscale(30%);
  cursor: default;
}
.uc-btn[aria-disabled="true"]:hover {
  background: transparent;
}

.uc-group--open .uc-chevron {
  transform: rotate(180deg);
  transition: transform var(--dur-1) var(--ease-standard);
}

/* ── History popover ─────────────────────────────────────────────────────── */
/* Width invents nothing: 280px is the notice card's min-width and the max is
   the notice surface's own `--notice-max-w`. Content sizes between. */
.uc-panel {
  min-width: 280px;
  max-width: var(--notice-max-w);
}

.uc-count {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), 0.6);
  white-space: nowrap;
}

/* 50 steps needs a scroll region. */
.uc-list {
  max-height: 40vh;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-panel), 0.4) transparent;
  scrollbar-gutter: stable;
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.uc-empty {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-panel), 0.6);
}

/* The row recipe, mirroring the toolbar's `.gb-recent-row`: leading icon,
   flexible ellipsised label, trailing meta. */
.uc-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-panel));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  text-align: left;
  transition: background var(--dur-1) var(--ease-standard);
}

.uc-row:hover:not(:disabled) {
  background: var(--hover-wash);
}

.uc-row-icon {
  color: rgba(var(--v-theme-on-panel), 0.5);
  flex-shrink: 0;
}

.uc-row-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.uc-row-time {
  color: rgba(var(--v-theme-on-panel), 0.35);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.uc-row:hover:not(:disabled) .uc-row-time {
  color: rgba(var(--v-theme-on-panel), 0.7);
}

/* The hover preview: every step that would be undone. Built from the shipped
   selected vocabulary (--active-wash + --active-bar) rather than a new
   highlight; the rail is what makes the EXTENT of the range read as one
   contiguous block, which a fill alone does not. */
.uc-row--willundo,
.uc-row--willundo:hover:not(:disabled) {
  background: var(--active-wash);
  box-shadow: inset var(--space-1) 0 0 0 var(--active-bar);
}
/* Focus must still win over the range highlight, or a keyboard user loses the
   cursor inside the previewed block. */
.uc-row--willundo:focus-visible {
  box-shadow:
    inset var(--space-1) 0 0 0 var(--active-bar),
    var(--focus-ring);
}

/* Undone steps: visible, struck through, inert. Mirrors the shortcuts dialog's
   own disabled-row treatment rather than inventing a second one. */
.uc-row--future {
  opacity: 0.35;
  text-decoration: line-through;
  text-decoration-color: rgba(var(--v-theme-on-panel), 0.4);
  cursor: default;
}
.uc-row--future:hover {
}

@media (prefers-reduced-motion: reduce) {
  .uc-group--open .uc-chevron,
  .uc-row {
    transition: none;
  }
}

/* ── Shared toolbar collapse (docs/design/toolbar-responsive-decisions.md).
   Both host bars name their container `toolbar`, so these steps degrade
   identically everywhere the control mounts. Undo itself NEVER hides: the
   recovery control stays a single visible target at every width, which is
   also what keeps the "Changed elsewhere" warning surfaced. The hidden
   History popover stays reachable through the hosts' ⋯ "History…" row, which
   calls the exposed openHistory(). ─────────────────────────────────────── */
@container toolbar (max-width: 480px) {
  .uc-btn--chevron {
    display: none;
  }
}

@container toolbar (max-width: 420px) {
  .uc-btn--redo {
    display: none;
  }
}
</style>
