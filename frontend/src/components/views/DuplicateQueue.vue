<template>
  <div
    ref="rootEl"
    class="dq"
    tabindex="-1"
    aria-label="Duplicate review queue"
    aria-describedby="dq-key-help"
    data-testid="duplicate-queue"
  >
    <!-- The visible hint strip is a row of glyphs, so it is hidden from
         assistive tech and this sentence carries the model instead. It also
         carries the two keys the strip has no room for, and the one fact that
         makes the whole queue safe to work fast. -->
    <p id="dq-key-help" class="visually-hidden">
      Up and Down arrows choose a group. Page Up and Page Down move a screenful
      at a time, and Home and End jump to the first and last group. Enter or S
      stacks it. K keeps it separate. Down moves on without deciding. C compares
      every copy field by field. The number keys 1 to 9 choose the cover. X
      leaves the picture under the cursor out of the stack. Control Z undoes the
      last verdict. Escape returns here from a control. No picture is ever
      deleted, and a stack can be undone.
    </p>

    <!-- One toolbar, not two. The queue's count, the way to the Decided page,
         the tier gate and the size control are all state or controls; the
         keyboard model that used to sit on a second bar is stated on the rows
         themselves (the Enter/S/C chips), in Compare's footer and in the
         description above, which is where a hint belongs (owner call,
         2026-07-29). -->
    <div class="dq-toolbar">
      <div class="dq-tb-left">
        <span v-if="store.hasGroups" class="qtitle">{{ headline }}</span>
        <!-- SESSION tally, and says so: the durable record is the Decided
             page, which spans every session. -->
        <span v-if="store.doneCount && !store.showingDecided" class="qsub"
          >{{ store.doneCount.toLocaleString() }} done this session</span
        >
        <!-- The flip side of the queue: review what was already decided and
             clear a decision. -->
        <!-- Compresses, never folds (amendment #2): at ≤720 the label span
             hides and the icon-only form remains — the same pattern as
             Auto-stack — so the button carries its own accessible name at
             every width. -->
        <button
          type="button"
          class="qdecided"
          :class="{ 'qdecided--on': store.showingDecided }"
          :title="decidedToggleLabel"
          :aria-label="decidedToggleLabel"
          :aria-pressed="store.showingDecided ? 'true' : 'false'"
          @click="onToggleDecided"
        >
          <v-icon size="15">{{
            store.showingDecided ? "mdi-arrow-left" : "mdi-history"
          }}</v-icon>
          <span class="qdecided-label">{{ decidedToggleLabel }}</span>
        </button>

        <!-- Separator D-S1: renders at ALL widths (amendment #2). With the
             Decided toggle compressing instead of folding, its left flank is
             always populated — including on an empty queue, where the
             headline is v-if'd away but the toggle remains. The tail's D-S2
             stays at every width too. -->
        <span class="dq-tb-sep" aria-hidden="true"></span>

        <!-- Escape inside the popover (including on its threshold slider,
             where the queue's key model stands down for a typing target)
             dismisses it back to the trigger, the standard popover exit. -->
        <div
          ref="tierWrapEl"
          class="dq-tier-wrap"
          @keydown.esc.stop.prevent="closeTierMenu()"
        >
          <!-- The label ellipsizes under pressure and hides at ≤720 (the
               compressed form is [filter icon][chevron], the grid Filter
               trigger's grammar), so the button carries its own accessible
               name at every width — without it the hidden span would leave
               the name empty (WCAG 4.1.2). -->
          <button
            ref="tierButtonEl"
            type="button"
            class="dq-btn"
            :title="tierLabel"
            :aria-label="tierLabel"
            :aria-expanded="tierMenuOpen"
            aria-haspopup="true"
            @click="toggleTierMenu"
          >
            <v-icon size="16">mdi-filter-outline</v-icon>
            <span class="dq-tier-label">{{ tierLabel }}</span>
            <v-icon size="16">mdi-menu-down</v-icon>
          </button>
          <!-- Two menus behind one button. The tier gate says nothing about a
               decision already made — the server ignores it on the decided
               page entirely — so what a user reviewing decisions wants to
               narrow by is the DECISION (owner call, 2026-07-30). -->
          <DedupVerdictMenu
            v-if="tierMenuOpen && store.showingDecided"
            class="dq-tier-menu"
            :verdicts="store.verdictRows"
            :group-count="store.total"
            @toggle="onVerdictToggle"
          />
          <DedupTierMenu
            v-else-if="tierMenuOpen"
            class="dq-tier-menu"
            :tiers="store.tierRows"
            :group-count="store.openCount"
            :threshold="store.threshold"
            :min-threshold="store.bounds?.min_threshold ?? null"
            :max-threshold="store.bounds?.max_threshold ?? null"
            @threshold="onThresholdChange"
            @toggle="onTierToggle"
          />
        </div>

        <DedupScopePill
          v-if="store.isScoped"
          :label="store.scopeLabel || 'This collection'"
          :icon="store.scopeIcon || 'mdi-folder-multiple-image'"
          @dismiss="onDismissScope"
        />
      </div>

      <div class="dq-tb-right">
        <!-- The same Tiny-to-Huge ladder the grid uses, driving the strip's
             picture height and therefore the row's. Live on drag: unlike the
             grid's, this control changes a list that is already on screen, so
             the user is looking straight at the answer. -->
        <div v-if="store.hasGroups" class="dq-size dq-fold-720">
          <v-icon size="16" aria-hidden="true"
            >mdi-image-size-select-large</v-icon
          >
          <v-slider
            class="dq-size-slider"
            :model-value="store.sizeLevel"
            :min="0"
            :max="maxSizeLevel"
            :step="1"
            density="compact"
            hide-details
            color="primary"
            thumb-color="primary"
            :aria-label="`Thumbnail size: ${sizeLabel}`"
            @update:model-value="store.setSizeLevel($event)"
            @end="onSizeCommitted"
          />
          <span class="dq-size-value">{{ sizeLabel }}</span>
        </div>

        <!-- Compresses with the bar rather than folding: a bulk action with
             an accent fill must stay a visible target. Full label → short
             "Auto-stack N" (≤720) → icon + count (≤600), the sentence
             surviving as tooltip and accessible name throughout. -->
        <button
          v-if="store.exactCount > 0 && !readOnly"
          type="button"
          class="dq-btn dq-btn--accent"
          :title="autoStackLabel"
          :aria-label="autoStackLabel"
          @click="openAutoStack"
        >
          <v-icon size="16">mdi-flash-outline</v-icon>
          <span class="dq-auto-full">{{ autoStackLabel }}</span>
          <span class="dq-auto-short" aria-hidden="true"
            >Auto-stack {{ store.exactCount.toLocaleString() }}</span
          >
          <span class="dq-auto-count" aria-hidden="true">{{
            store.exactCount.toLocaleString()
          }}</span>
        </button>

        <!-- The app-wide chrome, the same components the grid's toolbar
             mounts: Duplicates replaces the grid (and with it that toolbar),
             but undo/redo, Settings and the stats rail are not the grid's —
             they must not vanish with it. One separator divides the queue's
             own controls from the app-wide cluster. -->
        <span class="dq-tb-sep" aria-hidden="true"></span>
        <!-- No burger in this bar (amendment #2): a burger may only collapse
             controls from its own visual group, and every foldable here
             found a better answer — Decided and Auto-stack compress to
             icon forms, the size slider simply hides at ≤720 (the value
             persists in the store), and the app-wide tail never folds. -->
        <UndoControl v-if="!readOnly" />
        <TbGlobalActions @open-settings="emit('open-settings')" />
      </div>
    </div>

    <DedupScanBanner :scan="store.scan" />

    <!-- One live region for the whole destination, and deliberately OUTSIDE the
         branches below: a region that unmounts with the last row takes the
         verdict that emptied the queue down with it, so the one announcement a
         user most needs is the one they would never hear. -->
    <span
      class="visually-hidden"
      role="status"
      aria-live="polite"
      data-testid="dedup-announcement"
      >{{ announcement }}</span
    >

    <div v-if="store.loading" class="dq-state" role="status">
      Looking for duplicates.
    </div>

    <div v-else-if="store.hasGroups" class="queue">
      <!-- The bulk-scope statement: while ≥2 groups are selected, a verdict on
           any of them takes all of them. The only thing left on a second bar,
           and it appears WITH the selection and goes with it — live state, not
           a standing explanation. -->
      <div v-if="store.selectionCount > 1" class="qselbar">
        <span class="qselchip" role="status">
          <v-icon size="14">mdi-checkbox-multiple-marked-outline</v-icon>
          {{ store.selectionCount }} groups selected —
          {{
            store.showingDecided
              ? "Clear decision applies to all"
              : "Stack and Keep separate apply to all"
          }}
          <button
            type="button"
            class="qselclear"
            title="Clear the selection (Esc)"
            @click="store.clearSelection()"
          >
            Clear
          </button>
        </span>
      </div>

      <div ref="listEl" class="qlist" @scroll.passive="onListScroll">
        <div
          v-if="topSpacer"
          class="qspacer"
          :style="{ height: `${topSpacer}px` }"
          aria-hidden="true"
        ></div>
        <DedupGroupRow
          v-for="entry in windowedGroups"
          :key="entry.group.signature"
          :group="entry.group"
          :index="entry.index"
          :focused="entry.index === store.focusIndex"
          :selected="store.isSelected(entry.group.signature)"
          :selection-count="store.selectionCount"
          :bulk-keys="bulkKeysActive"
          :verdict="store.showingDecided ? entry.group.verdict || '' : ''"
          :decided-at="entry.group.decided_at || ''"
          :cover-id="store.coverIdFor(entry.group)"
          :excluded-ids="store.excludedFor(entry.group.signature)"
          :load-thumbnails="entry.loadThumbnails"
          :thumb-height="store.thumbHeight"
          :busy="store.busy"
          :read-only="readOnly"
          :flash-ids="
            flashSignature === entry.group.signature ? flashIds : EMPTY_IDS
          "
          @focus="onRowFocus(entry.index, $event)"
          @stack="onStack(entry.group)"
          @keep-separate="onKeepSeparate(entry.group)"
          @compare="onCompare(entry.index)"
          @set-cover="store.setCover(entry.group.signature, $event)"
          @toggle-excluded="onToggleExcluded(entry.group, $event)"
          @clear-decision="onClearDecision(entry.group)"
        />
        <div
          v-if="bottomSpacer"
          class="qspacer"
          :style="{ height: `${bottomSpacer}px` }"
          aria-hidden="true"
        ></div>
        <!-- The track is sized for the whole queue, so a fast drag can land in
             rows that have not arrived yet. Sticky, because at that point the
             end of the list is thousands of pixels below the viewport and a
             message down there would never be seen. -->
        <div v-if="store.loadingMore" class="qmore" role="status">
          <v-icon size="14">mdi-progress-download</v-icon>
          Loading more groups
        </div>
      </div>
    </div>

    <!-- "Queue clear" has to be true when it is shown. A page can be emptied
         faster than the read-ahead refills it, and claiming the work is done
         while the next page is in flight is how the count stops being
         trusted. -->
    <div v-else-if="store.loadingMore" class="dq-state" role="status">
      Loading the next groups.
    </div>

    <!-- The empty DECIDED page keeps its own copy and, crucially, its own way
         back: the header toggle lives on the list, which is not rendered here. -->
    <div v-else-if="store.showingDecided" class="qdone">
      <v-icon size="48">mdi-history</v-icon>
      <h3>No decided groups</h3>
      <p>
        Groups you stack or keep separate land here — from any session, not just
        this one — and every decision can be reviewed and cleared until you do.
      </p>
      <button type="button" class="qdecided" @click="onToggleDecided">
        <v-icon size="15">mdi-arrow-left</v-icon>
        Back to review
      </button>
    </div>

    <div v-else class="qdone">
      <v-icon size="48">mdi-check-circle-outline</v-icon>
      <h3>Queue clear</h3>
      <p>
        {{ store.stackedCount.toLocaleString() }}
        {{ store.stackedCount === 1 ? "group" : "groups" }} stacked,
        {{ store.separatedCount.toLocaleString() }} kept separate. Every picture
        is still in your library. Scanning continues in the background, and new
        groups appear here as they are found.
      </p>
      <!-- Always offered: decisions are SERVER state, remembered across
           sessions, so the way to them must not depend on this session's
           tally. An empty Decided page explains itself. -->
      <button type="button" class="qdecided" @click="onToggleDecided">
        <v-icon size="15">mdi-history</v-icon>
        Review decided groups
      </button>
    </div>

    <DedupCompareDialog
      ref="compareRef"
      :open="compareOpen"
      :group="store.focusedGroup"
      :cover-id="
        store.focusedGroup ? store.coverIdFor(store.focusedGroup) : null
      "
      :excluded-ids="
        store.focusedGroup
          ? store.excludedFor(store.focusedGroup.signature)
          : []
      "
      :busy="store.busy"
      :read-only="readOnly"
      @close="closeCompare"
      @set-cover="
        store.focusedGroup &&
        store.setCover(store.focusedGroup.signature, $event)
      "
      @toggle-excluded="
        store.focusedGroup && onToggleExcluded(store.focusedGroup, $event)
      "
      @stack="onCompareStack"
      @keep-separate="onCompareKeepSeparate"
    />

    <DedupAutoStackDialog
      :open="autoStackOpen"
      :preview="autoStackPreview"
      :loading="autoStackLoading"
      :preview-failed="autoStackPreviewFailed"
      :busy="store.busy"
      :queue-remaining="store.queueOnlyCount"
      @close="autoStackOpen = false"
      @confirm="confirmAutoStack"
    />

    <ActionReceipt v-if="!readOnly" />
  </div>
</template>

<script setup>
// The duplicate triage queue: the whole "Duplicates" destination.
//
// It replaces the grid rather than floating over it, because duplicates are a
// task with a to-do count rather than a lens on the library. Three design rules
// are load-bearing here and are worth stating where they are implemented:
//
//   * **Never block on a full pass.** The view renders whatever the first page
//     returned and lets `DedupScanBanner` narrate the rest. There is no state in
//     which the user waits on a complete scan before seeing a group.
//   * **Keep one group's worth of pictures in the DOM.** The row list is
//     windowed around the focus, and only the focused row and the one after it
//     decode real thumbnails. Ten groups and ten thousand groups therefore cost
//     the same to render, which is the difference between this and a review
//     page that renders everything and dies.
//   * **Auto-advance.** Every verdict removes its row and the focus lands on
//     the next open group, so a run of `Enter` presses works the queue without
//     a single extra keystroke.
//
// Undo is not reimplemented here. A verdict is recorded server-side like any
// other change, the operation store notices the resulting event and raises the
// standard receipt, and `Ctrl+Z` walks the same shared stack. The queue's only
// undo-specific job is to claim the chord so the app shell does not also
// handle it and undo twice.

import {
  ref,
  computed,
  watch,
  onMounted,
  onBeforeUnmount,
  nextTick,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { useDedupStore } from "../../stores/useDedupStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { API_BASE_URL, isReadOnly } from "../../utils/apiClient";
import {
  candidateId,
  serverDetail,
  lockedPictureIds,
  partialStackSentence,
} from "../../utils/dedup";
import { createDedupKeyHandler } from "../../composables/useDedupQueueKeyboard";
import {
  MAX_THUMBNAIL_SIZE_LEVEL,
  sizeLabelForLevel,
} from "../../utils/thumbnailSizes";
import { pictureThumbnailUrl } from "../../api/pictures";
import TbGlobalActions from "../panels/TbGlobalActions.vue";
import UndoControl from "../panels/UndoControl.vue";
import DedupGroupRow from "../widgets/DedupGroupRow.vue";
import DedupTierMenu from "../widgets/DedupTierMenu.vue";
import DedupVerdictMenu from "../widgets/DedupVerdictMenu.vue";
import DedupScanBanner from "../widgets/DedupScanBanner.vue";
import DedupScopePill from "../widgets/DedupScopePill.vue";
import DedupCompareDialog from "../widgets/DedupCompareDialog.vue";
import DedupAutoStackDialog from "../widgets/DedupAutoStackDialog.vue";
import ActionReceipt from "../widgets/ActionReceipt.vue";

/**
 * How many rows beyond the anchors stay mounted.
 *
 * The window is anchored to BOTH the keyboard focus and the scroll position:
 * anchoring to the focus alone renders a fixed dozen rows and leaves a mouse
 * user scrolling into blank spacer — a 327-group queue that appears to hold 9.
 * Enough margin that neither a page of arrow presses nor a flick of the wheel
 * lands on an empty viewport; small enough that the mounted row count stays a
 * constant rather than a function of the queue's length.
 */
const WINDOW_BEFORE = 4;
const WINDOW_AFTER = 8;

/**
 * What a row costs beyond its pictures: 8px of padding top and bottom, a 1px
 * border on each edge, and the 8px gap to the next row. Measured, and the
 * reason the estimate is a function of the size level rather than a constant —
 * the whole scroll track is sized from it, so it has to move when the size
 * control does.
 */
const ROW_CHROME_PX = 28;

/**
 * The floor a row cannot go under whatever the pictures do: the info column
 * (title, confidence, one why-pill) and the three verdict buttons both sit
 * beside the strip. Below this the size control stops buying rows per screen
 * and only buys back horizontal space.
 */
const MIN_ROW_CONTENT_PX = 89;

/**
 * What the queue says when `X` is refused at the stack floor.
 *
 * One string for both routes into the refusal (the row's right-click and the
 * key handler), because a rule stated two ways is a rule that drifts.
 */
const STACK_FLOOR_NOTICE =
  "A stack needs at least two pictures, so this one has to stay in. Keep the group separate instead.";

/**
 * How long the lock chip stays flashed after a refused Stack.
 *
 * Comfortably longer than the animation itself (`--dur-2`, 200ms) so the class
 * is not pulled off mid-run, and short enough that a stale amber chip is never
 * still on screen by the time the user acts again.
 */
const LOCK_FLASH_MS = 1000;

/**
 * A stable empty array for rows that are not flashing.
 *
 * A fresh `[]` in the template would be a new prop identity on every render of
 * every row, which on a queue of twenty rows is twenty needless updates per
 * keystroke.
 */
const EMPTY_IDS = Object.freeze([]);

// The settings dialog is App.vue's; the queue only asks for it, the same way
// the grid's toolbar does.
const emit = defineEmits(["open-settings"]);

const route = useRoute();
const router = useRouter();
const store = useDedupStore();
const operationStore = useOperationStore();
const noticeStore = useNoticeStore();

// ── Undo/redo must put the queue back, not just fix the badge ─────────────
// Reverting a stack verdict reopens the group server-side (the op log's
// post-restore hook), but no WebSocket event says "a dedup group returned":
// the undo's pictures_changed echo carries this client's own origin and is
// suppressed like any other echo, and only the COUNTS refresh through the
// sidebar path. So the queue subscribes to the shared operation store's own
// actions and reloads itself after an undo/redo that touched a dedup
// operation — the same reload reopen() performs, through the same store, not
// a new mechanism. Scoped to dedup op types so undoing an unrelated tag edit
// does not yank a triage in progress back to the top. The subscription is
// made in setup, so Pinia removes it when the view unmounts.

const UNDO_REDO_ACTIONS = new Set(["undo", "redo", "undoTo", "undoBatchById"]);

/**
 * The operations an undo/redo action is ABOUT to touch, read before the
 * action runs (afterwards the stack has already moved past them).
 * @param {string} name
 * @param {Array} args
 * @returns {Object[]}
 */
function opsUndoActionTouches(name, args) {
  if (name === "undo") {
    return operationStore.nextUndo ? [operationStore.nextUndo] : [];
  }
  if (name === "redo") {
    return operationStore.nextRedo ? [operationStore.nextRedo] : [];
  }
  if (name === "undoTo") {
    const past = operationStore.past ?? [];
    const index = past.findIndex((op) => op?.id === args?.[0]);
    return index < 0 ? past : past.slice(0, index + 1);
  }
  if (name === "undoBatchById") {
    return (operationStore.operations ?? []).filter(
      (op) => op?.batch_id === args?.[0],
    );
  }
  return [];
}

operationStore.$onAction(({ name, args, after }) => {
  if (readOnly.value || !UNDO_REDO_ACTIONS.has(name)) return;
  const touched = opsUndoActionTouches(name, args);
  if (!touched.some((op) => String(op?.op_type || "").startsWith("dedup."))) {
    return;
  }
  after(async () => {
    // Same sequence as reopen(): the group is back in the server's unresolved
    // set, so the list, the per-scope caches and the badge all re-read.
    store.invalidateScopeCounts();
    await store.loadFirstPage();
    store.refreshCounts();
  });
});

const rootEl = ref(null);
const listEl = ref(null);
const tierWrapEl = ref(null);
const tierButtonEl = ref(null);
const tierMenuOpen = ref(false);
const compareOpen = ref(false);
const autoStackOpen = ref(false);
const autoStackLoading = ref(false);
const autoStackPreview = ref(null);
const autoStackPreviewFailed = ref(false);
const announcement = ref("");

// Which thumbnails are currently flashing their lock chip, and on which row.
// Scoped by signature so a refusal on one group cannot light up a same-id
// candidate that also appears in another.
const flashIds = ref([]);
const flashSignature = ref("");
let flashTimer = null;

const readOnly = computed(() => Boolean(isReadOnly.value));

const maxSizeLevel = MAX_THUMBNAIL_SIZE_LEVEL;
const sizeLabel = computed(() => sizeLabelForLevel(store.sizeLevel));

/** What the Decided toggle says: the visible label on a wide bar, the
 * tooltip and accessible name at every width (the span hides at ≤720). */
const decidedToggleLabel = computed(() =>
  store.showingDecided ? "Back to review" : "Decided",
);

/** The auto-stack button's full sentence: the visible label on a wide bar,
 * the tooltip and accessible name always. */
const autoStackLabel = computed(
  () =>
    `Auto-stack ${store.exactCount.toLocaleString()} exact ${
      store.exactCount === 1 ? "match" : "matches"
    }`,
);

/** What the toolbar calls the queue: the count, and which side of it is shown. */
const headline = computed(() =>
  store.showingDecided
    ? `${store.total.toLocaleString()} decided ${store.total === 1 ? "group" : "groups"}`
    : `${store.openCount.toLocaleString()} ${store.openCount === 1 ? "group" : "groups"} to review`,
);

/** The row pitch a given picture height implies, before anything is measured. */
function estimatedPitch() {
  return Math.max(store.thumbHeight, MIN_ROW_CONTENT_PX) + ROW_CHROME_PX;
}

/** The real row pitch, measured once two rows exist; the estimate until then. */
const rowPitchPx = ref(estimatedPitch());
/** First row index the scroll position implies, and how many rows fit. */
const scrollIndex = ref(0);
const viewportRows = ref(WINDOW_AFTER);

function measureRowPitch() {
  const list = listEl.value;
  if (!list) return;
  const rows = list.querySelectorAll(".grow");
  if (rows.length >= 2) {
    const pitch = rows[1].offsetTop - rows[0].offsetTop;
    if (pitch > 0) rowPitchPx.value = pitch;
  }
  if (list.clientHeight > 0) {
    viewportRows.value = Math.ceil(list.clientHeight / rowPitchPx.value) + 1;
  }
}

/**
 * Fetch the next page when the scroll position is reaching past the rows the
 * client holds.
 *
 * Called on scroll AND whenever the list grows, because the scrollbar is sized
 * for the whole queue: a drag into the reserved-but-unloaded tail produces one
 * scroll event and then nothing, so without the second trigger the chase would
 * stall one page short of where the user is looking.
 */
function maybeLoadMore() {
  // While an End jump/chase is in flight the store drives its own loading;
  // scroll-position math over a window that is about to be replaced would
  // fire pages the rebase then has to discard.
  if (store.endChaseActive) return;
  const windowEnd = store.windowStart + store.groups.length;
  if (
    store.hasMore &&
    scrollIndex.value + viewportRows.value + WINDOW_AFTER >= windowEnd
  ) {
    store.loadMore();
  }
  // The mirror image after an End jump: the scroll reaching up past the
  // window's start backfills the rows above it, page by page, to the top.
  if (
    store.windowStart > 0 &&
    scrollIndex.value - WINDOW_BEFORE < store.windowStart
  ) {
    store.loadPrevious();
  }
}

function onListScroll() {
  const list = listEl.value;
  if (!list) return;
  scrollIndex.value = Math.max(
    0,
    Math.floor(list.scrollTop / rowPitchPx.value),
  );
  // A scroll away from the tail while an End jump is still paging is the user
  // taking their place back: the chase dies here rather than yanking them to
  // the bottom when its last page lands. Slack of one row keeps the pin's own
  // rounding from reading as a user move.
  if (
    store.endChaseActive &&
    scrollIndex.value + viewportRows.value < totalRows.value - 1
  ) {
    store.cancelEndChase();
  }
  // Mouse-wheel users reach the tail without ever moving the keyboard focus,
  // so the scroll position must drive loadMore exactly as the focus does.
  maybeLoadMore();
}

const focusAnchor = computed(() =>
  store.focusIndex < 0 ? 0 : store.focusIndex,
);

/**
 * A rebase (windowStart moving FORWARD: the End jump) relocates the held span
 * wholesale, but `scrollIndex` still reflects the last scroll event — load
 * decisions made from that stale value would immediately backfill the very
 * pages the jump skipped. Snap it to the focus the jump just set; the real
 * scrollbar follows through scrollFocusIntoView in the same breath. Created
 * BEFORE the groups-length watcher below, so it runs first in the flush.
 * Backward moves (upward backfill, Home's reset) keep the user's scroll.
 */
watch(
  () => store.windowStart,
  (now, before) => {
    if (now > before) scrollIndex.value = Math.max(0, focusAnchor.value);
  },
);

/**
 * Whether Enter/S would genuinely take the whole selection: two or more
 * groups selected AND the keyboard cursor inside it. Only then may every
 * selected row wear the Enter/S chips — a chip on a row the key will not hit
 * is a lie.
 */
const bulkKeysActive = computed(() => {
  const focusedGroup = store.focusedGroup;
  return Boolean(
    focusedGroup &&
    store.selectionCount > 1 &&
    store.isSelected(focusedGroup.signature),
  );
});

// Anchored to the SCROLL alone, so the mounted count stays a constant: a
// union with the focus window would mount the whole span between the keyboard
// cursor and a far-away scrollbar. Keyboard moves stay covered because
// scrollFocusIntoView drags the scroll (and thus this window) to the cursor.
//
// Everything here is in ABSOLUTE queue indices, clamped to the span the store
// actually HOLDS ([windowStart, windowStart + groups.length]) — after an End
// jump that span hangs off the queue's tail, and a scroll position outside it
// renders spacer alone while the backfill catches up.
function clampToHeld(index) {
  return Math.max(
    store.windowStart,
    Math.min(store.windowStart + store.groups.length, index),
  );
}
const renderStart = computed(() =>
  clampToHeld(scrollIndex.value - WINDOW_BEFORE),
);
const renderEnd = computed(() =>
  clampToHeld(scrollIndex.value + viewportRows.value + WINDOW_AFTER),
);
/**
 * How many rows the scroll height stands for: every group the queue HAS, not
 * just the pages fetched so far.
 *
 * Sizing the spacers to the loaded rows alone made the scrollbar grow under the
 * user's hand — the thumb shrank and jumped on every page, and the track never
 * meant anything, because "the bottom" moved each time it was reached. The
 * server's total is the only number that does not move as paging proceeds.
 * Once `hasMore` goes false AND the window is anchored at the top, the loaded
 * length is the truth (a total counted under a running scan can lag the rows
 * it already handed out); a window rebased onto the tail always stands for
 * the rows above it too.
 */
const totalRows = computed(() => {
  const held = store.windowStart + store.groups.length;
  if (!store.hasMore && store.windowStart === 0) return store.groups.length;
  return Math.max(held, store.total);
});

// Absolute spacers: the top one covers every row above the render window,
// including rows above windowStart that are not held at all, so the track
// keeps its full height (and the thumb its position) across a tail jump and
// the upward backfill — a prepended page fills spacer, it never moves the
// scroll.
const topSpacer = computed(() => renderStart.value * rowPitchPx.value);
const bottomSpacer = computed(() =>
  Math.max(0, (totalRows.value - renderEnd.value) * rowPitchPx.value),
);

/**
 * The rows that are actually mounted, each carrying its true (absolute)
 * index. Every mounted row may decode thumbnails: the window itself is the
 * budget, and the imgs are `loading="lazy"`, so off-viewport rows inside it
 * cost a request only as they approach.
 */
const windowedGroups = computed(() => {
  const localStart = renderStart.value - store.windowStart;
  const localEnd = renderEnd.value - store.windowStart;
  if (localEnd <= localStart) return [];
  return store.groups.slice(localStart, localEnd).map((group, i) => ({
    group,
    index: renderStart.value + i,
    loadThumbnails: true,
  }));
});

watch(
  () => store.groups.length,
  async () => {
    await nextTick();
    measureRowPitch();
    maybeLoadMore();
  },
);

/**
 * A size change makes every measurement taken at the old size wrong, and the
 * spacers are what the scrollbar is built from. Drop straight to the estimate
 * for the new size so the track is never sized from a stale pitch, re-measure
 * once the rows have laid out, and keep the keyboard cursor on screen: resizing
 * must not cost the user their place in the queue.
 */
watch(
  () => store.thumbHeight,
  async () => {
    rowPitchPx.value = estimatedPitch();
    await nextTick();
    measureRowPitch();
    scrollFocusIntoView();
    maybeLoadMore();
  },
);

/**
 * One End press pins the scroll to the queue's true end at once: the spacers
 * already stand for every unloaded row (the track is sized from the server
 * total), so the track's bottom exists before its rows do. The store keeps
 * paging behind the pin and lands the focus on the real last group when the
 * chase completes — or the chase dies the moment the user moves the focus
 * (store-side) or scrolls away from the tail (onListScroll above).
 */
watch(
  () => store.endChaseActive,
  async (active) => {
    if (!active) return;
    announcement.value =
      "Jumping to the end of the queue. Loading the remaining groups.";
    await nextTick();
    // The chase can be over or cancelled by the time the DOM settles; pinning
    // then would be exactly the late yank cancellation exists to prevent.
    if (!store.endChaseActive) return;
    const list = listEl.value;
    if (!list) return;
    list.scrollTop = Math.max(
      0,
      totalRows.value * rowPitchPx.value - list.clientHeight,
    );
    onListScroll();
  },
);

/**
 * What the filter button says the list is currently showing.
 *
 * On the QUEUE it names the loosest tier that is on, because that is the one
 * that decides how speculative the list is. On the DECIDED page the tier gate
 * is not in force at all (the server ignores it there), so the button names the
 * verdict filter instead — a button reading "Exact only" over a page that is
 * showing every decision would be a plain lie. Both are built from the server's
 * own rows, so a tier or verdict added later names itself here rather than
 * falling through to a wrong label.
 */
const tierLabel = computed(() => {
  if (store.showingDecided) {
    const on = store.verdictRows.filter((verdict) => verdict.enabled);
    if (!on.length || on.length === store.verdictRows.length) {
      return "All decisions";
    }
    return on.map((verdict) => verdict.label).join(" and ");
  }
  const on = store.tierRows.filter((tier) => tier.enabled);
  const loosest = on[on.length - 1];
  if (!loosest || loosest.locked) return "Exact only";
  return `Exact and ${loosest.label.toLowerCase()}`;
});

/**
 * Warm the browser cache for the group after the focused one.
 *
 * Only one group ahead: prefetching further turns the "one group in the DOM"
 * rule back into "load the whole queue", slowly.
 */
function prefetchNextGroup() {
  // focusIndex is absolute; the array holds the window.
  const next = store.groups[store.focusIndex - store.windowStart + 1];
  if (!next || typeof Image === "undefined") return;
  for (const candidate of next.candidates ?? []) {
    const img = new Image();
    // candidateId, not .id — the server calls the field picture_id — and the
    // backend origin, or the warmed URL is not the one the row will render.
    img.src = pictureThumbnailUrl(candidateId(candidate), {
      version: candidate.thumbnail_version,
      baseUrl: API_BASE_URL,
    });
  }
}

/** Keep the focused row inside the scroll viewport. */
async function scrollFocusIntoView() {
  await nextTick();
  const list = listEl.value;
  if (!list) return;
  const row = list.querySelector(".grow--focus");
  if (!row || typeof row.offsetTop !== "number") {
    // The cursor left the mounted window (a verdict advanced it while the
    // user was scrolled elsewhere). Jump the scroll to its estimated pitch;
    // the scroll event remounts the row and the next pass fine-tunes.
    list.scrollTop = Math.max(0, focusAnchor.value * rowPitchPx.value);
    onListScroll();
    return;
  }
  const top = row.offsetTop;
  const bottom = top + row.offsetHeight;
  if (top < list.scrollTop) list.scrollTop = top;
  else if (bottom > list.scrollTop + list.clientHeight) {
    list.scrollTop = bottom - list.clientHeight;
  }
}

/**
 * Take the DOM focus back off a row control when the keyboard cursor moves.
 *
 * A user who tabs onto a row's Compare button and then presses ArrowDown ends
 * up with the focus ring on one row and the "Keyboard acts here" label on
 * another, which is exactly the ambiguity the focused-row treatment exists to
 * prevent. The cursor wins; the ring comes back to the queue.
 */
function reclaimFocusFromRow() {
  if (typeof document === "undefined") return;
  const list = listEl.value;
  const active = document.activeElement;
  if (!list || !active || active === rootEl.value) return;
  if (list.contains(active)) rootEl.value?.focus?.();
}

watch(
  () => store.focusIndex,
  () => {
    reclaimFocusFromRow();
    scrollFocusIntoView();
    prefetchNextGroup();
  },
);

watch(
  () => store.focusedGroup,
  (group) => {
    if (!group) {
      // The queue ran out from under Compare — the last verdict, whichever
      // path gave it (footer buttons, Enter/S), or a reload that emptied the
      // list. Nothing is left to compare, so the dialog closes back to the
      // queue's done state. This is the ONLY way a verdict closes Compare:
      // with groups still open, a verdict advances in place instead.
      if (compareOpen.value) closeCompare();
      return;
    }
    const n = group.candidates?.length ?? 0;
    // Against the whole queue's length, not the held window's: after an End
    // jump "Group 200 of 200" is the truth and "of 20" would be nonsense.
    announcement.value = `Group ${store.focusIndex + 1} of ${totalRows.value}, ${n} pictures.`;
  },
);

/** Open Compare on a row, focusing it first so the two can never disagree. */
function onCompare(index) {
  store.setFocus(index);
  compareOpen.value = true;
}

function closeCompare() {
  compareOpen.value = false;
  rootEl.value?.focus?.();
}

/**
 * Give a verdict and narrate it for assistive tech.
 *
 * The visible receipt is the operation store's; this line exists because the
 * receipt is a floating pill a screen reader would otherwise have to find.
 */
async function onStack(group) {
  const targets = store.verdictTargets(group);
  if (targets.length > 1) {
    const pictures = targets.reduce((n, g) => n + store.stackSizeFor(g), 0);
    const result = await store.stack(group);
    if (result) {
      announcement.value = `Stacked ${targets.length} groups (${pictures} pictures). One undo reverses them all.`;
      reportPartialStack(result, pictures);
      return;
    }
    reportVerdictFailure("stack those groups", store.error, group);
    return;
  }
  const size = store.stackSizeFor(group);
  const result = await store.stack(group);
  if (result) {
    announcement.value = `Stacked ${size} pictures. The cover is kept and nothing is deleted.`;
    reportPartialStack(result, result.picture_ids?.length ?? size);
    return;
  }
  reportVerdictFailure("stack that group", store.error, group);
}

/**
 * Say so when a locked set held some members back.
 *
 * The everyday path never reaches this: the queue already marks a frozen
 * candidate and leaves it out of the request, so `skipped` only fills when the
 * set was locked after the page was loaded. It is a warning rather than an
 * error because the verdict DID land, and the group is gone from the queue, so
 * there is no row left to anchor the explanation to.
 *
 * @param {Object} result - the verdict response.
 * @param {number} stacked - how many pictures went in.
 */
function reportPartialStack(result, stacked) {
  const sentence = partialStackSentence(result?.gesture_skipped, stacked);
  if (!sentence) return;
  announcement.value = sentence;
  noticeStore.warning(sentence);
}

/**
 * Say when `X` was refused rather than letting it read as a dead key.
 *
 * The store holds a stack at two members because the server refuses a
 * one-member stack outright, and a group of two therefore accepts no exclusion
 * at all. Without this the row simply does not change and the user presses the
 * key again; with it the queue names the rule and the way past it.
 *
 * @param {Object} group
 * @param {number} pictureId
 */
function onToggleExcluded(group, pictureId) {
  const outcome = store.toggleExcluded(group, pictureId);
  if (outcome === true) return;
  if (outcome === "locked") {
    // A different refusal from the floor, so a different sentence: this one the
    // user cannot get past by including something else, only by unlocking the
    // set. The chip on the thumbnail carries the how.
    announcement.value =
      "That picture is in a locked set, so it cannot be put into the stack. Unlock the set to include it.";
    flashLockedPictures([pictureId], group);
    return;
  }
  announcement.value = STACK_FLOOR_NOTICE;
}

/**
 * Keep a group separate, and offer the only way back.
 *
 * This verdict changes no picture row, so the backend deliberately records no
 * operation for it: there is nothing for undo to restore, and an empty
 * operation row would still consume a Ctrl+Z. No receipt will ever appear for
 * it, so the narration and the escape hatch have to be raised here instead.
 */
async function onKeepSeparate(group) {
  const targets = store.verdictTargets(group);
  const size = targets.reduce((n, g) => n + (g.candidates?.length ?? 0), 0);
  const result = await store.keepSeparate(group);
  if (!result) {
    reportVerdictFailure("record that decision", store.error);
    return;
  }
  const sentence =
    targets.length > 1
      ? `Kept ${targets.length} groups (${size} pictures) separate. Change your mind under Decided.`
      : `Kept ${size} pictures separate. Change your mind under Decided.`;
  // No sticky notice any more (owner call, 2026-07-29): the Decided page is
  // the standing way back, so the narration can be transient.
  announcement.value = sentence;
  // A backend that records the verdict (batch_id in the response) raises the
  // standard undo receipt through the store; a second toast on top of it
  // would say the same thing twice. Older backends record nothing, so the
  // info notice remains their only visible confirmation.
  if (!result?.batch_id) noticeStore.info(sentence);
}

/**
 * Clear one decided group's verdict from the Decided page.
 *
 * Never touches pictures: a reopened "stacked" group stays stacked until it
 * is unstacked from the Stacks view; the group simply returns to the queue.
 *
 * @param {Object} group
 */
async function onClearDecision(group) {
  const targets = store.verdictTargets(group);
  const signatures = targets.map((g) => g.signature);
  const { cleared, returned } = await store.reopenMany(signatures);
  if (!cleared) {
    noticeStore.error("Could not clear that decision. Nothing changed.");
    return;
  }
  if (cleared < signatures.length) {
    noticeStore.error(
      `Cleared ${cleared} of ${signatures.length} decisions; the rest kept theirs.`,
    );
  }
  if (signatures.length > 1) {
    announcement.value = returned
      ? `Cleared ${cleared} decisions; ${returned} ${returned === 1 ? "group is" : "groups are"} back in the review queue.`
      : `Cleared ${cleared} decisions. The groups return to the queue after the next scan.`;
    return;
  }
  announcement.value = returned
    ? "Decision cleared. The group is back in the review queue."
    : "Decision cleared. The group returns to the queue after the next scan.";
}

/**
 * Say so when a verdict did not land.
 *
 * A failed verdict leaves the row exactly where it was, which on a queue whose
 * whole promise is auto-advance reads as a dead keypress. It has to be a
 * notice, not just a live-region line: the user is looking at the row, not
 * listening. When the server said why, that sentence is carried through
 * verbatim rather than being flattened into this function's generic one.
 *
 * @param {string} what - the attempt, phrased to follow "could not".
 * @param {*} [err] - the rejection, for its `detail`.
 */
function reportVerdictFailure(what, err, group = null) {
  const detail = serverDetail(err);
  const because = detail ? ` ${detail}` : "";
  announcement.value = `Could not ${what}.${because} The group is still in the queue, so nothing was lost.`;
  noticeStore.error(
    `Could not ${what}.${because} The group is still in the queue, so you can try again.`,
  );
  // The row stays on screen, so the refusal has an anchor: flash the lock chip
  // on the exact pictures the server named. The global sentence says what
  // happened; the flash says WHICH, which no bottom-centre notice can.
  flashLockedPictures(lockedPictureIds(err), group);
}

/**
 * Draw the eye to the thumbnails a refusal named.
 *
 * One shot: the class is dropped again once the animation has run, so a second
 * refusal on the same row flashes again rather than being a no-op. The chip
 * itself is permanent; only the amber is transient.
 *
 * @param {Array<number>} pictureIds
 * @param {Object|null} group
 */
function flashLockedPictures(pictureIds, group) {
  if (!pictureIds.length) return;
  if (group?.signature) flashSignature.value = group.signature;
  flashIds.value = pictureIds;
  if (flashTimer) clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    flashIds.value = [];
    flashSignature.value = "";
    flashTimer = null;
  }, LOCK_FLASH_MS);
}

/**
 * A verdict given from inside Compare STAYS in Compare (owner requirement,
 * 2026-07-30): the store's auto-advance lands the focus on the next open
 * group and the dialog, which renders `store.focusedGroup`, flips to it in
 * place — the next decision starts without reopening anything, which is the
 * whole point of comparing a run of groups. The dialog closes only when the
 * verdict emptied the queue (the focusedGroup watcher above), and a FAILED
 * verdict changes nothing: the same group stays on screen with the failure
 * notice over it. The zoom needs no handling here — the dialog resets it on
 * every group signature change.
 */
function onCompareStack() {
  const group = store.focusedGroup;
  if (group) onStack(group);
}

function onCompareKeepSeparate() {
  const group = store.focusedGroup;
  if (group) onKeepSeparate(group);
}

/**
 * Drop the scope and say so.
 *
 * The list underneath is replaced wholesale, so silence here would leave a
 * screen-reader user with a cursor on a group they were never told about.
 */
async function onDismissScope() {
  await store.clearScope();
  announcement.value =
    "Showing duplicates from the whole library, starting at the first group.";
  rootEl.value?.focus?.();
}

function toggleTierMenu() {
  tierMenuOpen.value ? closeTierMenu() : (tierMenuOpen.value = true);
}

/**
 * True from a pointer press inside the tier popover until the next threshold
 * commit: it is what tells a POINTER-committed threshold change apart from a
 * keyboard one, because only the pointer path may hand focus back to the
 * queue (see onThresholdChange).
 */
let thresholdPointerTuning = false;

/**
 * Dismiss the tier popover.
 *
 * By default the focus goes back to the control that opened it (Escape, and
 * any dismissal that is *about the popover*), so the keyboard never has to
 * hunt for where it went. A dismissal caused by a COMMITTED change passes
 * `focusTrigger: false` and hands the focus to the queue instead — the
 * popover session is over and the next keys are for the rows.
 */
function closeTierMenu({ focusTrigger = true } = {}) {
  if (!tierMenuOpen.value) return;
  tierMenuOpen.value = false;
  thresholdPointerTuning = false;
  if (focusTrigger) tierButtonEl.value?.focus?.();
}

/**
 * A pointer press anywhere outside the popover dismisses it; one inside it
 * marks the start of a pointer gesture (a slider drag) for onThresholdChange.
 */
function onDocumentPointerDown(event) {
  if (!tierMenuOpen.value) return;
  if (tierWrapEl.value?.contains?.(event.target)) {
    thresholdPointerTuning = true;
    return;
  }
  tierMenuOpen.value = false;
}

/**
 * Whether a key event belongs to the open tier popover rather than the queue.
 *
 * The popover blocks only the keys pressed INSIDE itself: once a committed
 * change has handed focus back to the queue, the keys must work the rows even
 * while the popover stays open showing its live counts. (The auto-stack
 * dialog, a true modal, still blocks everything — see the isBlocked dep.)
 *
 * @param {KeyboardEvent} [event]
 * @returns {boolean}
 */
function tierMenuOwnsEvent(event) {
  return Boolean(tierWrapEl.value?.contains?.(event?.target));
}

/**
 * What `Escape` means in the queue.
 *
 * A popover first, because that is the thing on top. Otherwise it hands the
 * DOM focus back to the queue itself, which is the way out of a row's buttons
 * without tabbing through the rest of them.
 */
function onEscape() {
  if (tierMenuOpen.value) {
    closeTierMenu();
    return;
  }
  // The selection is the next thing "on top": clearing it must not also cost
  // the user their place, so the focus stays where it is.
  if (store.selectionCount > 0) {
    store.clearSelection();
    return;
  }
  // The Decided flip is an Escape layer of its own: one press returns to the
  // review queue, exactly as the Back-to-review toggle does (same reload
  // semantics — the queue reopens at its top, which is what toggleDecided has
  // always meant), with the keyboard handed straight back to the list.
  if (store.showingDecided) {
    onToggleDecided();
    return;
  }
  rootEl.value?.focus?.();
}

/**
 * A row click chooses; a modified row click SELECTS.
 *
 * Ctrl (or Cmd) toggles the group in and out of the multi-selection,
 * Shift extends the range from the anchor, and a plain click focuses the row
 * and drops any selection — exactly the grid's own conventions, so nothing
 * new has to be learned here.
 *
 * @param {number} index
 * @param {MouseEvent} [event] - absent when a row control re-emits focus.
 */
function onRowFocus(index, event) {
  // A row control re-emitting focus (a cover click, an exclusion toggle)
  // carries no event: it must move the cursor without costing the selection.
  if (!event) {
    store.setFocus(index);
    return;
  }
  if (event.shiftKey) {
    store.selectRange(index);
    return;
  }
  if (event.ctrlKey || event.metaKey) {
    store.toggleSelected(index);
    return;
  }
  store.setFocus(index);
  store.clearSelection();
}

/**
 * Move the tier gate. The store reloads the queue, so the menu closes: leaving
 * it open over a list that just changed underneath reads as a glitch. The
 * COMMIT ends the popover session, so the focus goes to the queue, not back
 * to the trigger — the user changed the lens and expects Enter/S/arrows to
 * work the rows now, without a click first (Escape, by contrast, still
 * returns to the trigger).
 */
async function onTierToggle(id, on) {
  closeTierMenu({ focusTrigger: false });
  rootEl.value?.focus?.();
  await store.setTierEnabled(id, on);
}

/**
 * Narrow the Decided page to one kind of decision.
 *
 * The menu STAYS open, unlike a tier toggle: with only two verdicts, hiding one
 * is usually followed by hiding or restoring the other, and a popover that shut
 * after every press would make a two-press adjustment a four-press one. The
 * keyboard still goes back to the list, so the rows are workable underneath —
 * the same split the threshold slider already uses.
 */
async function onVerdictToggle(id, on) {
  const changed = await store.setVerdictEnabled(id, on);
  if (!changed) return;
  const row = store.verdictRows.find((verdict) => verdict.id === id);
  const label = row?.label ?? id;
  announcement.value = on
    ? `Showing ${label.toLowerCase()} groups again. ${store.total.toLocaleString()} decided ${store.total === 1 ? "group" : "groups"}.`
    : `Hiding ${label.toLowerCase()} groups. ${store.total.toLocaleString()} decided ${store.total === 1 ? "group" : "groups"} left.`;
}

/**
 * Move the similarity threshold.
 *
 * The popover stays open: a threshold is a value the user tunes and re-reads
 * against the count next to it, unlike a tier switch, which is a decision they
 * make once and then want to see the result of.
 *
 * A POINTER-committed change (drag released — `change` fires once) hands the
 * keyboard back to the queue while the popover stays up with its live count.
 * A KEYBOARD-committed one keeps focus on the slider: every arrow press fires
 * its own `change`, and yanking focus after the first would turn the rest of
 * the tuning into row moves.
 */
async function onThresholdChange(value) {
  const byPointer = thresholdPointerTuning;
  thresholdPointerTuning = false;
  await store.setThreshold(value);
  if (byPointer) rootEl.value?.focus?.();
}

/**
 * A pointer-committed size change hands the keyboard back to the queue.
 * Vuetify's `end` fires on drag/track release only, so keyboard sizing keeps
 * focus on the thumb — whose arrow keys the queue's model already leaves
 * alone (`role="slider"` is a typing target).
 */
function onSizeCommitted() {
  rootEl.value?.focus?.();
}

/**
 * Flip to or from the Decided page and hand the keyboard straight to the list
 * that just appeared: focus left on the toggle makes the next Enter flip the
 * page straight back.
 */
function onToggleDecided() {
  store.toggleDecided();
  rootEl.value?.focus?.();
}

/** Open the bulk dialog on its dry run, so the preview is never stale. */
async function openAutoStack() {
  autoStackOpen.value = true;
  autoStackLoading.value = true;
  autoStackPreviewFailed.value = false;
  const preview = await store.previewAutoStack();
  autoStackPreview.value = preview;
  // A failed dry run must not render as a confident row of zeroes: that reads
  // as "there is nothing to stack" when the truth is "nobody asked".
  autoStackPreviewFailed.value = !preview;
  autoStackLoading.value = false;
}

async function confirmAutoStack() {
  const result = await store.runAutoStack();
  autoStackOpen.value = false;
  if (result?.batch_id) {
    const made = Number(result.groups ?? 0).toLocaleString();
    announcement.value = `Created ${made} stacks. Undo reverses the whole run in one step.`;
    // One unstackable group never aborts the run, so a partial result has to be
    // reported rather than hidden behind a success message.
    const skipped = result.failures?.length ?? 0;
    if (skipped) {
      noticeStore.warning(
        `Created ${made} stacks. ${skipped} ${skipped === 1 ? "group was" : "groups were"} skipped and stayed in the queue.`,
      );
    }
  } else {
    reportVerdictFailure("create those stacks", store.error);
  }
  rootEl.value?.focus?.();
}

const compareRef = ref(null);

/**
 * Select every group in the queue and say what that came to.
 *
 * Ctrl+A pages the rest of the queue in, so it is not instant and it can stop
 * short of the whole thing. Both facts have to be said out loud: a gesture
 * called "select all" that quietly took 500 of 5,000 would put a bulk verdict
 * on a set the user never saw the size of.
 */
async function onSelectAll() {
  const { selected, total, truncated } = await store.selectAll();
  if (!selected) return;
  const count = selected.toLocaleString();
  announcement.value = truncated
    ? `Selected ${count} of ${total.toLocaleString()} groups, the most confident ones. That is as many as one selection can hold.`
    : `Selected all ${count} ${selected === 1 ? "group" : "groups"}.`;
  if (truncated) {
    noticeStore.info(
      `Selected the ${count} most confident groups. That is as many as one selection can hold, so the rest of the queue is untouched.`,
    );
  }
}

/**
 * A dialog the queue did NOT open is on screen — Settings, Share, anything the
 * sidebar raised over us.
 *
 * The handler is bound at the document (see below), so without this the queue
 * would answer keys meant for whatever is on top of it. Compare and the
 * auto-stack dialog are the queue's own and are exempt: the model has branches
 * for both.
 *
 * @returns {boolean}
 */
function foreignDialogOpen() {
  if (typeof document === "undefined") return false;
  if (compareOpen.value || autoStackOpen.value) return false;
  // Two signals, because the app has two kinds of modal: Vuetify's scrim (every
  // AppDialog) and the review overlay, which paints its own. Both are stated on
  // the DOM rather than on listener order, for the reason App.vue records at
  // `handleGlobalKeydown` — a remount silently reorders listeners.
  return (
    document.querySelector(".v-overlay--active .v-overlay__scrim") != null ||
    document.querySelector(".rs-overlay") != null
  );
}

const handleKeydown = createDedupKeyHandler({
  store,
  isCompareOpen: () => compareOpen.value,
  openCompare: () => {
    if (store.focusedGroup) compareOpen.value = true;
  },
  closeCompare,
  undo: () => operationStore.undo(),
  isReadOnly: () => readOnly.value,
  // The auto-stack dialog is a modal and blocks everything; the tier popover
  // owns only the keys pressed inside itself, so a committed change that
  // handed focus back to the queue leaves the rows workable underneath it.
  isBlocked: (event) =>
    autoStackOpen.value || (tierMenuOpen.value && tierMenuOwnsEvent(event)),
  // One row less than the viewport holds, so a page move keeps the row the
  // user was reading on screen as the anchor for the next one.
  pageRows: () => Math.max(1, viewportRows.value - 1),
  onEscape,
  onExclusionRefused: () => {
    announcement.value = STACK_FLOOR_NOTICE;
  },
  selectAll: onSelectAll,
  // The blink compare's state lives in the dialog; its KEYS live in the one
  // keyboard model, driven through the dialog's exposed surface.
  zoom: {
    isOpen: () => Boolean(compareRef.value?.isZoomOpen?.()),
    open: () => compareRef.value?.openZoom?.(),
    close: () => compareRef.value?.closeZoom?.(),
    flip: (delta) => compareRef.value?.flipZoom?.(delta),
    to: (index) => compareRef.value?.zoomTo?.(index),
    togglePixels: () => compareRef.value?.toggleZoomPixels?.(),
  },
});

/**
 * The queue's keys, bound at the DOCUMENT for as long as the view is mounted.
 *
 * They used to be bound on the queue root, which meant they only worked while
 * the DOM focus was inside it: one click on a sidebar row and every shortcut
 * went dead, with nothing on screen to say why or how to get them back. The
 * queue is a whole destination, not a widget — while it is the view, the keys
 * are the view's.
 *
 * Two things keep that from being greedy. `isTypingTarget` still declines to a
 * text field wherever it lives, and `foreignDialogOpen` hands the keyboard to
 * any dialog raised over the queue. And it stays honest with the app shell for
 * free: `claim()` calls `stopPropagation`, and this listener sits on the
 * document while `App.vue`'s sits on the window, so a key the queue takes never
 * reaches the shell's Ctrl+Z or its Home/End scrolling.
 */
function onKeydown(event) {
  if (foreignDialogOpen()) return;
  handleKeydown(event);
}

/**
 * Read the scope out of the URL.
 *
 * The scope lives in the query rather than in a store so a scoped queue is a
 * link that survives a reload. `useViewStore.parseRouteView` deliberately
 * returns `null` for this route (it drives no grid), so opening the queue is
 * this component's own job rather than something the route sync does for it.
 *
 * @returns {Object} the `openQueue` scope argument.
 */
function scopeFromRoute() {
  const query = route.query ?? {};
  return {
    type: query.scope ? String(query.scope) : "global",
    id: query.scope_id ?? null,
    label: query.scope_label ? String(query.scope_label) : "",
    icon: query.scope_icon ? String(query.scope_icon) : "",
  };
}

/**
 * The filter selection the URL carries, or null when it carries none.
 *
 * Each key is applied only when present, so a bare /duplicates URL keeps the
 * server's defaults rather than forcing everything off.
 *
 * @returns {Object|null}
 */
function filtersFromRoute() {
  const query = route.query ?? {};
  const filters = {};
  if (query.near !== undefined) {
    filters.near = query.near === "1" || query.near === "true";
  }
  if (query.embedding !== undefined) {
    filters.embedding = query.embedding === "1" || query.embedding === "true";
  }
  const parsed = Number(query.threshold);
  if (Number.isFinite(parsed)) filters.threshold = parsed;
  if (query.view !== undefined) filters.decided = query.view === "decided";
  // The Decided page's own filter, comma-joined so it stays ONE scalar the
  // mirror below can compare against the live URL without array identity
  // games. The store drops any id the server does not publish.
  if (query.verdict !== undefined) {
    filters.verdicts = String(query.verdict).split(",").filter(Boolean);
  }
  return Object.keys(filters).length ? filters : null;
}

// The filter selection is part of the ADDRESS: a full refresh (or a shared
// link) must restore it. Mirrored with replace(), never push() — tuning the
// tier gate is not a history step the Back button should have to unwind.
// Non-default tier/threshold values are written explicitly (near=0 included):
// "absent" always means "the server's default", never "whatever it was".
const FILTER_QUERY_KEYS = ["near", "embedding", "threshold", "view", "verdict"];

watch(
  () => [
    store.nearEnabled,
    store.embeddingEnabled,
    store.threshold,
    store.showingDecided,
    store.enabledVerdicts.join(","),
    store.policyLoaded,
    store.filtersRestored,
  ],
  () => {
    // filtersRestored is load-bearing, not belt-and-braces. The regression it
    // closes: on a full reload the policy landing flipped policyLoaded one
    // microtask BEFORE openQueue applied the URL's filters, this mirror ran on
    // that flip, saw a pristine default gate, and replaced the URL WITHOUT its
    // filter params. By the time the store adopted the params and the mirror
    // re-ran, `route.query` still showed the old query (the stripping
    // navigation was async and in flight), the `same` check passed, no
    // corrective write happened — and the params were gone for good. The gate
    // keeps the mirror silent until the store has actually adopted the URL.
    if (
      route.name !== "duplicates" ||
      !store.policyLoaded ||
      !store.filtersRestored
    ) {
      return;
    }
    const defaults = store.policyDefaults ?? {};
    const next = { ...route.query };
    for (const key of FILTER_QUERY_KEYS) delete next[key];
    const isDefault =
      store.nearEnabled === Boolean(defaults.near_enabled) &&
      store.embeddingEnabled === Boolean(defaults.embedding_enabled) &&
      (!Number.isFinite(store.threshold) ||
        store.threshold === Number(defaults.threshold));
    if (!isDefault) {
      next.near = store.nearEnabled ? "1" : "0";
      next.embedding = store.embeddingEnabled ? "1" : "0";
      if (Number.isFinite(store.threshold)) {
        next.threshold = String(store.threshold);
      }
    }
    if (store.showingDecided) {
      next.view = "decided";
      // Only a NARROWED selection is written: absent means every decision, the
      // same "absent is the default" rule the tier params follow. The gate is
      // meaningless off the Decided page, so it is never written there.
      const shown = store.enabledVerdicts;
      if (shown.length && shown.length < store.verdictRows.length) {
        next.verdict = shown.join(",");
      }
    }
    const current = route.query ?? {};
    const same = FILTER_QUERY_KEYS.every(
      (key) => (next[key] ?? null) === (current[key] ?? null),
    );
    if (!same) router.replace({ query: next });
  },
);

/**
 * Open the queue for the URL's scope, unless it is already showing it.
 *
 * The already-showing path still refreshes the counts. The badge is the one
 * piece of state that outlives this view, a keep-separate raises no WebSocket
 * event to correct it, and arriving at the destination is exactly the moment
 * its number has to be true.
 */
function syncQueueToRoute() {
  const scope = scopeFromRoute();
  const alreadyShowing =
    String(store.scopeType) === scope.type &&
    String(store.scopeId ?? "") === String(scope.id ?? "");
  if (alreadyShowing && store.groups.length) {
    store.refreshCounts();
    return;
  }
  store.openQueue({ ...scope, filters: filtersFromRoute() });
}

// A scope change is a navigation, not a remount: the component stays mounted
// when the user picks "Find duplicates in..." on a second collection.
//
// An ARRAY of getters, never a getter returning an array: the latter builds a
// fresh array each run, Vue compares it by identity, and the watcher fires on
// EVERY route.query write — including the filter mirror's own replace() above.
// On an empty queue that refire fell through syncQueueToRoute's fast path
// (which requires held rows) into a full openQueue, which force-reset the
// Decided flip the mirror was in the middle of recording: the decided rows
// flashed and were replaced by "Queue clear".
watch([() => route.query.scope, () => route.query.scope_id], syncQueueToRoute);

onMounted(() => {
  syncQueueToRoute();
  // The queue is a keyboard surface. Taking focus on mount is what makes the
  // first Enter work without the user hunting for a click target first.
  rootEl.value?.focus?.();
  nextTick(measureRowPitch);
  prefetchNextGroup();
  if (typeof document !== "undefined") {
    document.addEventListener("mousedown", onDocumentPointerDown);
    document.addEventListener("keydown", onKeydown);
  }
});

onBeforeUnmount(() => {
  // Leaving the destination mid-jump must stop the paging, not let it keep
  // fetching a queue nobody is looking at.
  store.cancelEndChase();
  if (flashTimer) {
    clearTimeout(flashTimer);
    flashTimer = null;
  }
  if (typeof document === "undefined") return;
  document.removeEventListener("mousedown", onDocumentPointerDown);
  document.removeEventListener("keydown", onKeydown);
});

defineExpose({ windowedGroups, tierLabel });
</script>

<style scoped>
.dq {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  position: relative;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  outline: none;
}

.dq-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  /* The shell's top band. The GRID toolbar (`.selection-bar-overlay` in
     Toolbar.vue) is the point of truth for the band's box recipe, and this
     bar copies it exactly: `height: 36px` + `box-sizing: border-box` (the
     1px bottom border sits INSIDE the 36) + zero vertical padding, with
     `align-items: center` doing the vertical work. The previous recipe here
     (`min-height: 36px` + `var(--space-2)` vertical padding, content-box)
     rendered 41px once the 32px app-wide tail buttons landed — the bars
     visibly stepped. Guardrail: Toolbar.test.js asserts both bars carry the
     same recipe. This is NOT `--bar-height` (48px): that token is the design
     manual's target for the band, and unifying the shipped 34/36/40/48/56
     onto it (or tokenising the shipped 36) is the open, UI/UX-gated
     reconciliation item in visual-language.md §5 — a bar that jumped there
     alone would just be drift in the other direction. */
  height: 36px;
  box-sizing: border-box;
  /* Split inset, each side anchored to what it must align WITH. RIGHT is
     --space-3, the grid bar's inset: the app-wide tail ([sep][Undo][Global])
     is a stable anchor only if its icons land at the identical distance from
     the edge in every view — a uniform --space-5 here put them 8px further
     left than the grid's and the tail jumped on view switches (guardrail in
     Toolbar.test.js pins the right insets equal). LEFT stays --space-5, the
     queue's own content gutter: the count headline sits flush over the
     list's rows (.qlist, .qselbar, .dq-state all inset by --space-5). */
  padding: 0 var(--space-3) 0 var(--space-5);
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  container-type: inline-size;
  /* `dqbar` for this bar's own ladder; the shared `toolbar` name is what the
     shared chrome (UndoControl, TbGlobalActions, the overflow) writes its
     scoped @container rules against, so it degrades identically here and in
     the grid bar (`selbar toolbar`). */
  container-name: dqbar toolbar;
}

.dq-tb-left,
.dq-tb-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.dq-tb-right {
  margin-left: auto;
}

/* Divides the queue's identity (what it holds, which side is showing) from the
   controls that change what it holds. */
.dq-tb-sep {
  width: 1px;
  height: 18px;
  background: rgb(var(--v-theme-divider));
}

/* The size control. A fixed track width, because a slider that grows with the
   toolbar makes the same drag mean a different size on every window. */
/* space-3, not space-2: the slider's thumb overhangs both ends of its track, so
   a tighter gap has it colliding with the icon at Tiny and the label at Huge. */
.dq-size {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.dq-size-slider {
  width: 96px;
  flex: 0 0 96px;
}

/* Vuetify's compact slider still reserves a form-control's worth of height,
   which is taller than the whole band it sits in and pushed the toolbar off
   the shell's 36px strip. The track and thumb need none of it. */
.dq-size-slider :deep(.v-input__control) {
  min-height: 0;
  height: 20px;
}

/* Fixed width: the label changes on every notch, and one that resizes with its
   text drags the whole toolbar sideways as the user drags the slider. Wide
   enough for the longest rung ("Very Large"). */
.dq-size-value {
  width: 11ch;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), 0.7);
  white-space: nowrap;
}

.dq-tier-wrap {
  position: relative;
  /* Part of the tier button's shrink chain: without this the flex default
     (min-width: auto) refuses to shrink and the label wraps instead. */
  min-width: 0;
}

/* The icons flank the ellipsis, never feed it. */
.dq-btn .v-icon {
  flex-shrink: 0;
}

.dq-tier-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dq-tier-menu {
  position: absolute;
  top: calc(100% + var(--space-2));
  left: 0;
  z-index: var(--z-dropdown);
}

.dq-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  /* Structural no-wrap: under width pressure the LABEL ellipsizes on one
     line; the 27px button and the 36px band never grow. */
  white-space: nowrap;
  min-width: 0;
  height: 27px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  background: transparent;
  color: inherit;
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.dq-btn:hover {
  background: var(--hover-wash);
}

.dq-btn:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.dq-btn--accent {
  background: rgb(var(--v-theme-accent));
  border-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}

.queue {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

/* The count leads the toolbar: it is the queue's to-do number, and the one
   thing the user checks on arrival and after every verdict. */
.qtitle {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  /* The to-do count is the queue's one number; it never truncates. */
  flex-shrink: 0;
}

.qsub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), 0.6);
  white-space: nowrap;
}

/* The Decided toggle: same chrome as the toolbar buttons, pressed state
   while the flip side is showing. */
.qdecided {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: transparent;
  font-size: var(--text-xs);
  font-family: var(--font-ui);
  color: inherit;
  white-space: nowrap;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.qdecided:hover {
  background: var(--hover-wash);
}

.qdecided:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.qdecided--on {
  background: var(--active-wash);
  border-color: rgba(var(--v-theme-accent), 0.5);
}

/* Appears with the selection and goes with it, so the queue has one bar in
   its resting state and a second only while a bulk gesture is live. */
.qselbar {
  display: flex;
  align-items: center;
  padding: var(--space-2) var(--space-5) 0;
}

/* The bulk-scope chip: accent-washed so it reads as state, not decoration —
   while it shows, a verdict on any selected row takes the whole selection. */
.qselchip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border: 1px solid rgba(var(--v-theme-accent), 0.5);
  border-radius: var(--radius-pill);
  background: var(--active-wash);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
}

.qselclear {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-accent));
  cursor: pointer;
}

.qselclear:focus-visible {
  outline: none;
  border-radius: var(--radius-sm);
  box-shadow: var(--focus-ring);
}

.qlist {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: 0 var(--space-5) var(--space-4);
  overflow-y: auto;
  min-height: 0;
  flex: 1;
  scrollbar-gutter: stable;
}

.qspacer {
  flex: 0 0 auto;
}

/* Pinned to the foot of the scrollport, not to the end of the content. */
.qmore {
  position: sticky;
  bottom: 0;
  align-self: center;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-divider));
  box-shadow: var(--elevation-1);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.dq-state {
  padding: var(--space-6) var(--space-5);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.6);
}

.qdone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  flex: 1;
  padding: var(--space-9) var(--space-6);
  text-align: center;
  color: rgba(var(--v-theme-on-background), 0.75);
}

.qdone h3 {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-background));
}

.qdone p {
  margin: 0;
  max-width: 52ch;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* ── The collapse ladder (docs/design/toolbar-responsive-decisions.md,
   amendment #2 — this bar has NO burger: every foldable compresses or
   hides within its own group instead). Floor: count, Decided (icon), Tier
   gate (icon+chevron), scope pill (compressed, if scoped), Auto-stack
   (compressed, if present), separator, Undo, Settings, Stats. ──────────── */
.dq-auto-short,
.dq-auto-count {
  display: none;
}

/* Auto-stack's full label shares the tier label's latent wrap; same cure. */
.dq-auto-full {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

@container dqbar (max-width: 860px) {
  .qsub {
    display: none;
  }
  .dq-size-value {
    display: none;
  }
}

@container dqbar (max-width: 720px) {
  /* The size control hides outright — no replacement row: the value
     persists in the store and comes back with the width. */
  .dq-fold-720 {
    display: none;
  }
  .dq-auto-full {
    display: none;
  }
  .dq-auto-short {
    display: inline;
  }
  /* The tier trigger compresses to [filter icon][chevron] — the grid Filter
     trigger's grammar; the button's own title/aria-label carry the name. */
  .dq-tier-label {
    display: none;
  }
  /* The Decided toggle compresses to its icon, the Auto-stack pattern; its
     title/aria-label carry the name. */
  .qdecided-label {
    display: none;
  }
}

@container toolbar (max-width: 600px) {
  .dq-auto-short {
    display: none;
  }
  .dq-auto-count {
    display: inline;
  }
}
</style>
