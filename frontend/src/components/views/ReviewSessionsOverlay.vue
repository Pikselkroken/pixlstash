<template>
  <!-- Tags to review is a view like Duplicates and Moves: App.vue draws it in
       the centre column in place of the grid, so the title bar and the
       sidebar never move. The sidebar's "Tags to review" row lights while it
       is up, and its open reviews nest under that row. -->
  <div
    class="rs-overlay"
    role="region"
    aria-label="Tags to review"
    data-testid="tags-to-review"
  >
    <!-- The grid's toolbar line, its contents swapped as the Lightbox swaps
         them: the way back (named for where it goes, with Esc), the title,
         then the board's own controls, which TagHealthBoard teleports into
         `.rs-toolbar-tools`. -->
    <header class="rs-toolbar" role="toolbar" aria-label="Tags to review">
      <ViewBackButton
        class="rs-toolbar-back"
        :label="backTarget"
        :tooltip="`Back to ${backTarget} (Esc)`"
        on-dark
        @click="goBack"
      />
      <span class="rs-toolbar-sep" aria-hidden="true"></span>
      <h2 class="rs-toolbar-title">{{ toolbarTitle }}</h2>
      <div :id="TOOLBAR_TOOLS_ID" class="rs-toolbar-tools"></div>
    </header>

    <div class="rs-shell">
      <ReviewSessionView
        v-if="store.activeSession"
        ref="sessionRef"
        :session="store.activeSession"
      />
      <ReviewArchivedReceipt
        v-else-if="archivedReview"
        :review="archivedReview"
      />
      <TagHealthBoard
        v-else
        ref="boardRef"
        :toolbar-target="`#${TOOLBAR_TOOLS_ID}`"
        @start-review="openNewReview($event)"
        @new-review="openNewReview()"
      />
    </div>

    <NewReviewDialog
      v-if="dialog"
      :preset="dialog.preset"
      :initial-scope="dialog.initialScope"
      @close="dialog = null"
    />

    <!-- Direct tagging of the current card's picture(s), independent of the
         queue decision. Opened via T or a card's bottom-left "Tag manually"
         button (`rs-manual-tag` in ReviewBinaryCard/ReviewPairCard, through
         the `rs-open-tag-apply` provide below) - that button is the one
         surviving entry point; this used to also render a second, redundant
         bottom-right "Apply tags" button, removed as a duplicate. Anchored
         bottom-right so the panel still opens in the same place. -->
    <div v-if="store.current" ref="tagApplyRef" class="rs-tag-apply">
      <!-- The grid rail's own tag field and chips: Esc in the field closes. -->
      <div v-if="tagApplyOpen" class="rs-tag-apply-panel">
        <RailTags
          :ids="cardImages.map((i) => i.id)"
          autofocus
          @tags-applied="onTagsApplied"
          @leave="tagApplyOpen = false"
        />
      </div>
    </div>

    <!-- Keyboard cheat-sheet (?) -->
    <div
      v-if="shortcutsOpen"
      class="rs-keys-backdrop"
      @click.self="shortcutsOpen = false"
    >
      <div class="rs-keys" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
        <h3 class="rs-keys-title">Keyboard shortcuts</h3>
        <dl class="rs-keys-list">
          <template v-for="[k, label] in SHORTCUTS" :key="k">
            <dt><kbd>{{ k }}</kbd></dt>
            <dd>{{ label }}</dd>
          </template>
        </dl>
        <button
          class="rs-keys-close"
          type="button"
          @click="shortcutsOpen = false"
        >
          Close
        </button>
      </div>
    </div>

    <!-- Full-screen zoom: scroll to magnify, drag to pan, click or Esc closes.
         Ported from the old overlay. -->
    <div
      v-if="zoom"
      class="rs-zoom"
      :class="{ 'rs-zoom--panning': panning }"
      @wheel.prevent="onZoomWheel"
      @mousedown="onZoomMouseDown"
      @mousemove="onZoomMouseMove"
      @mouseup="onZoomMouseUp"
      @mouseleave="onZoomMouseUp"
      @click="onZoomClick"
    >
      <img
        class="rs-zoom-img"
        :src="zoom.src"
        :style="zoomStyle"
        alt="zoomed image"
        draggable="false"
        @load="onZoomLoad"
      />
      <div
        class="rs-zoom-hint"
        :class="{ 'rs-zoom-hint--hidden': !zoomHintVisible }"
      >
        {{ Math.round(zoomScale * 100) }}% · scroll to zoom · drag to pan ·
        click or Esc to close
      </div>
      <button
        v-if="zoom.box"
        type="button"
        class="rs-zoom-fit"
        @click.stop="fitZoom"
      >
        <Tooltip text="Show the whole image" activator="parent" />
        <v-icon size="16">mdi-fit-to-page-outline</v-icon>
        Whole image
      </button>
    </div>
  </div>
</template>

<script setup>
// The Tags to review view: a toolbar line (back, title, the board's controls)
// over the main area (tag health board / one session / an archived receipt),
// plus the view-level layers - new-review dialog, direct-tagging panel (T),
// keyboard cheat-sheet (?), and the full-screen zoom. It replaces the grid in
// App.vue's centre column; the open reviews are listed in the app sidebar.
//
// Keyboard: one capture-phase window handler (same discipline as the old
// overlay). It bails for genuine text entry (input/textarea/contenteditable -
// NOT <select>, which is a fixed-choice control whose type-ahead must not
// swallow decision keys; selects are blurred after every change). Session keys
// are delegated to the session view, which owns the consistency guard.
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  provide,
  ref,
  watch,
} from "vue";
import TagHealthBoard from "../reviews/TagHealthBoard.vue";
import ReviewSessionView from "../reviews/ReviewSessionView.vue";
import ReviewArchivedReceipt from "../reviews/ReviewArchivedReceipt.vue";
import NewReviewDialog from "../reviews/NewReviewDialog.vue";
import RailTags from "../panels/RailTags.vue";
import Tooltip from "../widgets/Tooltip.vue";
import ViewBackButton from "../widgets/ViewBackButton.vue";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useProjectStore } from "../../stores/useProjectStore";
import { formatKeyHint, undoKeyHint } from "../../utils/shortcutHints";
import { API_BASE_URL } from "../../utils/apiClient";
// Sidebar selection sentinels (mirrors useSelectionStore.js / App.vue).
const ALL_PICTURES_ID = "ALL";
const UNASSIGNED_PICTURES_ID = "UNASSIGNED";
const SCRAPHEAP_PICTURES_ID = "SCRAPHEAP";

const TOOLBAR_TOOLS_ID = "rs-toolbar-tools";

const props = defineProps({
  backendUrl: { type: String, default: () => API_BASE_URL },
  // The view underneath, named as it names itself: where back and Esc go
  // from the board.
  backLabel: { type: String, default: "All Pictures" },
});
const emit = defineEmits(["close", "tags-applied"]);

const store = useReviewSessionsStore();
const selectionStore = useSelectionStore();
const projectStore = useProjectStore();
const noticeStore = useNoticeStore();

const sessionRef = ref(null);
const boardRef = ref(null);
const dialog = ref(null); // { preset, initialScope } | null
const shortcutsOpen = ref(false);
const tagApplyOpen = ref(false);
const tagApplyRef = ref(null);

// The tag panel opens from code (T, or a card's Tag manually), so nothing
// hands focus back when it closes: it goes back to whatever had it, the
// button that opened it, rather than dropping to <body>. Read before the
// panel renders and its field takes focus.
let tagApplyReturnFocus = null;
watch(tagApplyOpen, (open) => {
  if (open) {
    tagApplyReturnFocus = document.activeElement;
    return;
  }
  const el = tagApplyReturnFocus;
  tagApplyReturnFocus = null;
  if (el instanceof HTMLElement && el !== document.body && el.isConnected) {
    nextTick(() => el.focus({ preventScroll: true }));
  }
});

provide("rs-backend-url", props.backendUrl);

// Inside a review or a receipt the way back is the board; on the board it is
// the view the review was opened from.
const onBoard = computed(
  () => !store.activeSession && store.view.type !== "archived",
);
const backTarget = computed(() =>
  onBoard.value ? props.backLabel : "Tags to review",
);
const toolbarTitle = computed(() => {
  if (store.activeSession) return `Review: “${store.activeSession.tag}”`;
  if (archivedReview.value) return `Archived: “${archivedReview.value.tag}”`;
  return "Tags to review";
});

function goBack() {
  if (onBoard.value) emit("close");
  else store.showBoard();
}

// The sidebar's "New review…" row asks through the store, since the dialog
// lives here. A dialog already up is left alone: reopening it would wipe the
// tag and scope picked so far.
watch(
  () => store.newReviewTick,
  () => {
    if (!dialog.value) openNewReview();
  },
);

const archivedReview = computed(() =>
  store.view.type === "archived"
    ? (store.archived.find((a) => a.id === store.view.id) ??
      store.details[store.view.id] ??
      null)
    : null,
);

// The undo row names both keys because they are the same action: Ctrl+Z (⌘Z on
// a Mac) is the app-wide undo vocabulary and must mean something everywhere,
// and `U` is the reviewer muscle memory that keeps working. "in this review" is
// the whole point of the row: it is the only place the scope boundary between
// the review's undo and the app-wide one is taught before the user hits it.
const SHORTCUTS = computed(() => [
  ["Y / N", "Answer a binary card (yes / no)"],
  ["B / N / L / R", "Answer a pair card (both / neither / left / right)"],
  ["S", "Skip - leaves the queue undecided, no change made"],
  [
    `U / ${formatKeyHint(undoKeyHint())}`,
    "Undo the last decision in this review",
  ],
  ["H", "Show / hide the evidence region"],
  ["T", "Apply tags to the pictured image(s)"],
  ["/", "Filter the tag health board"],
  ["?", "This cheat-sheet"],
  ["Esc", "Close the topmost layer"],
]);

// Translate the app's current selection into the dialog's scope prefill, so a
// review created from a filtered view lands pre-scoped to it (old overlay
// behaviour).
function initialScopeFromSelection() {
  const scope = { projectId: null, setId: null, characterId: null };
  if (projectStore.selectedProjectId != null) {
    scope.projectId = projectStore.selectedProjectId;
  }
  if (selectionStore.selectedSet != null) {
    scope.setId = selectionStore.selectedSet;
  }
  const character = selectionStore.selectedCharacter;
  if (character === UNASSIGNED_PICTURES_ID) {
    scope.characterId = UNASSIGNED_PICTURES_ID;
  } else if (
    character != null &&
    character !== ALL_PICTURES_ID &&
    character !== SCRAPHEAP_PICTURES_ID
  ) {
    scope.characterId = character;
  }
  return scope;
}

function openNewReview(preset = "") {
  store.createError = null;
  // A scope chosen on the health board is the user's explicit working scope -
  // it wins over the app-selection prefill.
  const initialScope = store.healthScoped
    ? { ...store.healthScope }
    : initialScopeFromSelection();
  dialog.value = { preset, initialScope };
}

// The current card's picture(s), for the tag panel.
const cardImages = computed(() => {
  const item = store.current;
  if (!item) return [];
  const out = [{ id: item.picture_id, format: item.picture_ext }];
  if (item.kind === "pair" && item.twin_picture_id != null) {
    out.push({ id: item.twin_picture_id, format: item.twin_ext });
  }
  return out.filter((i) => i.id != null);
});

function onTagsApplied(payload) {
  emit("tags-applied", payload);
}

// Cards render a visible bottom-left "Tag manually" button that opens the
// same tag panel as the T shortcut.
provide("rs-open-tag-apply", () => {
  tagApplyOpen.value = true;
});

// --- Zoom (ported, trimmed, from the old overlay) ------------------------------
const zoom = ref(null); // { src, box } | null
const zoomScale = ref(1);
const zoomNaturalW = ref(0);
const zoomNaturalH = ref(0);

// The hint pill sits over the bottom of the image - it MUST auto-hide after a
// few seconds or it covers exactly the detail being inspected (it fades back
// in on the next zoom open).
const zoomHintVisible = ref(true);
let zoomHintTimer = null;
const ZOOM_HINT_MS = 2500;

function showZoomHint() {
  zoomHintVisible.value = true;
  clearTimeout(zoomHintTimer);
  zoomHintTimer = setTimeout(() => {
    zoomHintVisible.value = false;
  }, ZOOM_HINT_MS);
}

const zoomStyle = computed(() =>
  zoomNaturalW.value
    ? { width: `${Math.round(zoomNaturalW.value * zoomScale.value)}px` }
    : {},
);

function openZoom(src, box = null) {
  if (!src) return;
  zoom.value = { src, box };
  zoomScale.value = 1;
  zoomNaturalW.value = 0;
  showZoomHint();
}
provide("rs-open-zoom", openZoom);

function closeZoom() {
  zoom.value = null;
  clearTimeout(zoomHintTimer);
}

function onZoomLoad(event) {
  const img = event.target;
  const nw = img.naturalWidth || 0;
  const nh = img.naturalHeight || 0;
  zoomNaturalW.value = nw;
  zoomNaturalH.value = nh;
  const box = zoom.value?.box || null;
  const container = img.parentElement;
  if (box && nw && nh && container) {
    // Frame the region: scale so the box (nearly) fills the viewport, then
    // scroll its centre to the viewport centre.
    const availW = container.clientWidth;
    const availH = container.clientHeight;
    const boxW = nw * box[2];
    const boxH = nh * box[3];
    const pad = 0.9;
    const scale = Math.max(
      0.25,
      Math.min((availW / boxW) * pad, (availH / boxH) * pad, 12),
    );
    zoomScale.value = scale;
    nextTick(() => {
      const cx = nw * (box[0] + box[2] / 2) * scale;
      const cy = nh * (box[1] + box[3] / 2) * scale;
      container.scrollLeft = cx - availW / 2;
      container.scrollTop = cy - availH / 2;
    });
    return;
  }
  const fitByHeight = nh > 0 ? (window.innerHeight * 0.92) / nh : 1;
  zoomScale.value = Math.max(0.25, Math.min(fitByHeight, 4));
}

function fitZoom() {
  const nh = zoomNaturalH.value;
  const fit = nh > 0 ? (window.innerHeight * 0.92) / nh : 1;
  zoomScale.value = Math.max(0.25, Math.min(fit, 4));
  if (zoom.value) zoom.value = { ...zoom.value, box: null };
  nextTick(() => {
    const el = document.querySelector(".rs-zoom");
    if (el) {
      el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2;
      el.scrollTop = 0;
    }
  });
}

function onZoomWheel(event) {
  const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
  zoomScale.value = Math.max(0.1, Math.min(zoomScale.value * factor, 12));
}

const panning = ref(false);
let panLastX = 0;
let panLastY = 0;
let panDist = 0;

function onZoomMouseDown(event) {
  if (event.button !== 0) return;
  panning.value = true;
  panDist = 0;
  panLastX = event.clientX;
  panLastY = event.clientY;
  event.preventDefault();
}
function onZoomMouseMove(event) {
  if (!panning.value) return;
  const dx = event.clientX - panLastX;
  const dy = event.clientY - panLastY;
  panLastX = event.clientX;
  panLastY = event.clientY;
  panDist += Math.abs(dx) + Math.abs(dy);
  const el = event.currentTarget;
  el.scrollLeft -= dx;
  el.scrollTop -= dy;
}
function onZoomMouseUp() {
  panning.value = false;
}
function onZoomClick() {
  if (panDist > 6) {
    panDist = 0;
    return;
  }
  closeZoom();
}

// --- Keyboard --------------------------------------------------------------------

// Genuine text entry only - NOT <select> (see the old overlay's select-blur
// fix: a focused select must not swallow decision keys into its type-ahead).
function isEditable(el) {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA";
}

/**
 * Ctrl+Z / ⌘Z, and only that.
 *
 * Ctrl+Shift+Z and Ctrl+Y are REDO, which a review has no meaning for: undo
 * puts the card back at the head of the queue, so "redo" would be deciding it
 * again, and that is a decision rather than a replay. They are left alone.
 *
 * @param {KeyboardEvent} event
 * @returns {boolean}
 */
function isUndoChord(event) {
  return (
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.shiftKey &&
    !event.repeat &&
    event.key?.toLowerCase() === "z"
  );
}

/** Ctrl+Y or Ctrl+Shift+Z: redo, everywhere else in the app. */
function isRedoChord(event) {
  const key = event.key?.toLowerCase();
  return (
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.repeat &&
    (key === "y" || (key === "z" && event.shiftKey))
  );
}

/**
 * One undo request, however it was typed: `U`, Ctrl+Z, or the bar's button all
 * land here.
 *
 * The session view owns every guard and every message once a review is open - a
 * locked picture set makes a decision final, an empty stack has to say so - and
 * it consumes the key in all of those cases. This function only covers the case
 * it can see and the session view cannot: no review open at all.
 *
 * The review's undo is deliberately a SEPARATE stack from the app-wide one
 * behind the overlay. A review decision also flips its suggestion row's status
 * and writes the human-label ledger, and the operation log captures neither, so
 * one shared stack would undo half of each decision. Ctrl+Z therefore always
 * means "my last action HERE" and never quietly reaches past the overlay; the
 * message says where the other stack lives instead.
 */
function handleUndoRequest() {
  if (sessionRef.value) {
    sessionRef.value.handleKey("undo");
    closeZoom();
    return;
  }
  noticeStore.push({
    level: "info",
    text: "Nothing to undo here. Close the review sessions to undo earlier changes from the toolbar.",
    key: "review-nothing-to-undo",
  });
}

/**
 * Redo has no meaning in a review: undo puts the card back at the head of the
 * queue, so re-applying the decision means answering the card again. Say that
 * rather than leaving the chord dead, and never let it fall through to the
 * app-wide redo behind the overlay.
 */
function reportNoRedo() {
  noticeStore.push({
    level: "info",
    text: "Nothing to redo in this review. Answer the card again to reapply a decision you undid.",
    key: "review-nothing-to-redo",
  });
}

function handleKeyDown(event) {
  if (isEditable(event.target) || isEditable(document.activeElement)) return;

  // Undo/redo are checked BEFORE the modifier bail below - that bail is what
  // made Ctrl+Z dead in a review session, while App's global handler skips the
  // whole overlay. The owner ruled the chord must be consistent, so it lands
  // here on the review's own undo, exactly like `U`. The bail itself stays for
  // everything else: Ctrl+F, Ctrl+A, Ctrl+C and Ctrl+R must keep working.
  //
  // Skipped while the cheat-sheet or the new-review dialog owns the screen, on
  // the same rule the letter keys follow below: a modal layer is up, so nothing
  // behind it acts.
  const modalLayerUp = shortcutsOpen.value || Boolean(dialog.value);
  if (!modalLayerUp && (isUndoChord(event) || isRedoChord(event))) {
    if (isUndoChord(event)) handleUndoRequest();
    else reportNoRedo();
    event.preventDefault();
    event.stopImmediatePropagation();
    return;
  }

  if (event.metaKey || event.ctrlKey || event.altKey) return;

  const key = event.key.toLowerCase();
  let handled = true;

  if (event.key === "?") {
    shortcutsOpen.value = !shortcutsOpen.value;
  } else if (key === "escape") {
    // Unwind the topmost layer: cheat-sheet → dialog → zoom → tag panel →
    // pending confirm → review (back to tag health) → overlay.
    if (shortcutsOpen.value) shortcutsOpen.value = false;
    else if (dialog.value) dialog.value = null;
    else if (zoom.value) closeZoom();
    else if (tagApplyOpen.value) tagApplyOpen.value = false;
    else if (sessionRef.value?.handleKey("escape")) {
      // The session consumed it (a pending consistency confirm).
    } else if (boardRef.value?.handleEscape?.()) {
      // The board's Groups view cleared its tag selection.
    } else {
      // Same exit as the toolbar's back button: a review (or a receipt) steps
      // back to the board, the board back to the view underneath.
      goBack();
    }
  } else if (shortcutsOpen.value || dialog.value) {
    // A modal layer is up: swallow nothing else, let it be.
    handled = false;
  } else if (event.key === "/" && store.view.type === "board") {
    boardRef.value?.focusFilter?.();
  } else if (key === "t" && store.current) {
    tagApplyOpen.value = !tagApplyOpen.value;
  } else if (key === "u") {
    // Same request as Ctrl+Z, so the two cannot drift: `U` on the board (no
    // review open) now gets the same answer instead of doing nothing.
    handleUndoRequest();
  } else if (key === "enter" && sessionRef.value) {
    handled = !!sessionRef.value.handleKey("enter");
  } else if (sessionRef.value) {
    handled = !!sessionRef.value.handleKey(key);
    if (handled) closeZoom();
  } else {
    handled = false;
  }

  if (handled) {
    // Capture-phase + stopImmediatePropagation keeps these keys from reaching
    // the grid's nav handler and App's global shortcuts behind the overlay.
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}

onMounted(() => {
  window.addEventListener("keydown", handleKeyDown, true);
  store.load();
});

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeyDown, true);
  clearTimeout(zoomHintTimer);
  store.reset(); // stops the health poll + award timers; queues refetch on reopen
});
</script>

<style scoped>
/* Fills the grid's place in the centre column. The review stays a dark
   surface in both themes, as the Lightbox's stage does, and its toolbar band
   with it: the board's controls live on that band. */
.rs-overlay {
  /* Amber on a dark panel keeps the bright amber in both themes (#1413). */
  --v-theme-accent: var(--v-theme-dark-surface-accent);
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
}

/* The grid toolbar's box (height, padding, rule), on the review's surface. */
.rs-toolbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--toolbar-height);
  padding: 0 var(--space-3);
  box-sizing: border-box;
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
  white-space: nowrap;
}
.rs-toolbar::-webkit-scrollbar {
  display: none;
}
.rs-toolbar :deep(:focus-visible) {
  outline: none;
  box-shadow: var(--focus-ring-inset);
}
/* The back button's Esc keycap takes the review's dark ink, as the bar
   button does through `on-dark`. */
.rs-toolbar :deep(.bar-btn-kbd) {
  border-color: rgba(var(--v-theme-on-dark-surface), 0.28);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
.rs-toolbar-sep {
  flex-shrink: 0;
  width: 1px;
  height: 20px;
  margin: 0 var(--space-2);
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rs-toolbar-title {
  margin: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.rs-toolbar-tools {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.rs-shell {
  flex: 1;
  min-height: 0;
  display: flex;
}

/* Positioning anchor only now - the visible bottom-right "Apply tags" button
   that used to live here was removed as a duplicate of each card's
   bottom-left `.rs-manual-tag` button (the surviving entry point); the panel
   itself still opens fixed bottom-right via this wrapper. */
.rs-tag-apply {
  position: fixed;
  right: 18px;
  bottom: 76px;
  z-index: var(--z-dropdown);
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--space-3);
}

.rs-tag-apply-panel {
  width: var(--stats-panel-w);
  max-width: calc(100vw - 2 * var(--space-5));
  max-height: 70vh;
  overflow-y: auto;
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-4);
}

.rs-keys-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}
.rs-keys {
  /* Amber on a dark panel keeps the bright amber in both themes (#1413). */
  --v-theme-accent: var(--v-theme-dark-surface-accent);
  width: 380px;
  max-width: calc(100vw - 32px);
  padding: 20px;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
}
.rs-keys-title {
  font-size: 15px;
  font-weight: var(--weight-bold);
  margin-bottom: var(--space-4);
}
.rs-keys-list {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-3) 14px;
  font-size: var(--text-sm);
  margin: 0 0 14px;
}
.rs-keys-list kbd {
  font-family: var(--font-mono, monospace);
  font-size: var(--text-2xs);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.3);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  white-space: nowrap;
}
.rs-keys-list dd {
  margin: 0;
  color: rgba(var(--v-theme-on-dark-surface), 0.8);
}
.rs-keys-close {
  height: 30px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}

.rs-zoom {
  position: fixed;
  inset: var(--titlebar-h) 0 0 0;
  z-index: var(--z-drawer);
  overflow: auto;
  background: rgba(0, 0, 0, 0.92);
  cursor: zoom-out;
}
.rs-zoom--panning {
  cursor: grabbing;
}
.rs-zoom-img {
  display: block;
  margin: 0 auto;
  user-select: none;
}
.rs-zoom-hint {
  position: fixed;
  bottom: 18px;
  left: 50%;
  transform: translateX(-50%);
  padding: 5px var(--space-4);
  border-radius: var(--radius-pill);
  background: rgba(0, 0, 0, 0.65);
  color: rgba(255, 255, 255, 0.85);
  font-size: var(--text-xs);
  pointer-events: none;
  white-space: nowrap;
  transition: opacity var(--dur-3) var(--ease-standard);
}
.rs-zoom-hint--hidden {
  opacity: 0;
}
.rs-zoom-fit {
  position: fixed;
  top: calc(var(--titlebar-h) + 14px);
  right: 16px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 11px;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(255, 255, 255, 0.3);
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
}
</style>
