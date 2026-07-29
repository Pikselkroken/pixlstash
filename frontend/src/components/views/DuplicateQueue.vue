<template>
  <div
    ref="rootEl"
    class="dq"
    tabindex="-1"
    aria-label="Duplicate review queue"
    aria-describedby="dq-key-help"
    data-testid="duplicate-queue"
    @keydown="onKeydown"
  >
    <!-- The visible hint strip is a row of glyphs, so it is hidden from
         assistive tech and this sentence carries the model instead. It also
         carries the two keys the strip has no room for, and the one fact that
         makes the whole queue safe to work fast. -->
    <p id="dq-key-help" class="visually-hidden">
      Up and Down arrows choose a group. Page Up and Page Down move a screenful
      at a time, and Home and End jump to the first and last group. Enter stacks
      it. S keeps it separate. C compares every copy field by field. The number
      keys 1 to 9 choose the cover. X leaves the picture under the cursor out of
      the stack. Control Z undoes the last verdict. Escape returns here from a
      control. No picture is ever deleted, and a stack can be undone.
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
        <button
          type="button"
          class="qdecided"
          :class="{ 'qdecided--on': store.showingDecided }"
          :aria-pressed="store.showingDecided ? 'true' : 'false'"
          @click="store.toggleDecided()"
        >
          <v-icon size="15">{{
            store.showingDecided ? "mdi-arrow-left" : "mdi-history"
          }}</v-icon>
          {{ store.showingDecided ? "Back to review" : "Decided" }}
        </button>

        <span class="dq-tb-sep" aria-hidden="true"></span>

        <div ref="tierWrapEl" class="dq-tier-wrap">
          <button
            ref="tierButtonEl"
            type="button"
            class="dq-btn"
            :aria-expanded="tierMenuOpen"
            aria-haspopup="true"
            @click="toggleTierMenu"
          >
            <v-icon size="16">mdi-filter-outline</v-icon>
            <span>{{ tierLabel }}</span>
            <v-icon size="16">mdi-menu-down</v-icon>
          </button>
          <DedupTierMenu
            v-if="tierMenuOpen"
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
        <div v-if="store.hasGroups" class="dq-size">
          <v-icon size="16" aria-hidden="true">mdi-image-size-select-large</v-icon>
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
          />
          <span class="dq-size-value">{{ sizeLabel }}</span>
        </div>

        <button
          v-if="store.exactCount > 0 && !readOnly"
          type="button"
          class="dq-btn dq-btn--accent"
          @click="openAutoStack"
        >
          <v-icon size="16">mdi-flash-outline</v-icon>
          <span
            >Auto-stack {{ store.exactCount.toLocaleString() }} exact
            {{ store.exactCount === 1 ? "match" : "matches" }}</span
          >
        </button>
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
        Groups you stack or keep separate land here — from any session, not
        just this one — and every decision can be reviewed and cleared until
        you do.
      </p>
      <button type="button" class="qdecided" @click="store.toggleDecided()">
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
      <button type="button" class="qdecided" @click="store.toggleDecided()">
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
import { candidateId } from "../../utils/dedup";
import { createDedupKeyHandler } from "../../composables/useDedupQueueKeyboard";
import {
  MAX_THUMBNAIL_SIZE_LEVEL,
  sizeLabelForLevel,
} from "../../utils/thumbnailSizes";
import { pictureThumbnailUrl } from "../../api/pictures";
import DedupGroupRow from "../widgets/DedupGroupRow.vue";
import DedupTierMenu from "../widgets/DedupTierMenu.vue";
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

const route = useRoute();
const router = useRouter();
const store = useDedupStore();
const operationStore = useOperationStore();
const noticeStore = useNoticeStore();

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

const readOnly = computed(() => Boolean(isReadOnly.value));

const maxSizeLevel = MAX_THUMBNAIL_SIZE_LEVEL;
const sizeLabel = computed(() => sizeLabelForLevel(store.sizeLevel));

/** What the toolbar calls the queue: the count, and which side of it is shown. */
const headline = computed(() =>
  store.showingDecided
    ? `${store.total.toLocaleString()} decided ${store.total === 1 ? "group" : "groups"}`
    : `${store.openCount.toLocaleString()} ${store.openCount === 1 ? "group" : "groups"} to review`,
);

/** The row pitch a given picture height implies, before anything is measured. */
function estimatedPitch() {
  return (
    Math.max(store.thumbHeight, MIN_ROW_CONTENT_PX) + ROW_CHROME_PX
  );
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
  if (!store.hasMore) return;
  if (
    scrollIndex.value + viewportRows.value + WINDOW_AFTER >=
    store.groups.length
  ) {
    store.loadMore();
  }
}

function onListScroll() {
  const list = listEl.value;
  if (!list) return;
  scrollIndex.value = Math.max(0, Math.floor(list.scrollTop / rowPitchPx.value));
  // Mouse-wheel users reach the tail without ever moving the keyboard focus,
  // so the scroll position must drive loadMore exactly as the focus does.
  maybeLoadMore();
}

const focusAnchor = computed(() => (store.focusIndex < 0 ? 0 : store.focusIndex));

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
const renderStart = computed(() =>
  Math.max(0, scrollIndex.value - WINDOW_BEFORE),
);
const renderEnd = computed(() =>
  Math.min(
    store.groups.length,
    scrollIndex.value + viewportRows.value + WINDOW_AFTER,
  ),
);
/**
 * How many rows the scroll height stands for: every group the queue HAS, not
 * just the pages fetched so far.
 *
 * Sizing the spacers to the loaded rows alone made the scrollbar grow under the
 * user's hand — the thumb shrank and jumped on every page, and the track never
 * meant anything, because "the bottom" moved each time it was reached. The
 * server's total is the only number that does not move as paging proceeds.
 * Once `hasMore` goes false the loaded length is the truth (a total counted
 * under a running scan can lag the rows it already handed out).
 */
const totalRows = computed(() =>
  store.hasMore
    ? Math.max(store.groups.length, store.total)
    : store.groups.length,
);

const topSpacer = computed(() => renderStart.value * rowPitchPx.value);
const bottomSpacer = computed(() =>
  Math.max(0, (totalRows.value - renderEnd.value) * rowPitchPx.value),
);

/**
 * The rows that are actually mounted, each carrying its true index. Every
 * mounted row may decode thumbnails: the window itself is the budget, and the
 * imgs are `loading="lazy"`, so off-viewport rows inside it cost a request
 * only as they approach.
 */
const windowedGroups = computed(() =>
  store.groups.slice(renderStart.value, renderEnd.value).map((group, i) => {
    const index = renderStart.value + i;
    return {
      group,
      index,
      loadThumbnails: true,
    };
  }),
);

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
 * What the tier button says the queue is currently showing.
 *
 * Named after the loosest tier that is on, because that is the one that decides
 * how speculative the list is. Built from the server's tier rows, so a tier the
 * server adds later names itself here rather than falling through to a wrong
 * label.
 */
const tierLabel = computed(() => {
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
  const next = store.groups[store.focusIndex + 1];
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
    if (!group) return;
    const n = group.candidates?.length ?? 0;
    announcement.value = `Group ${store.focusIndex + 1} of ${store.groups.length}, ${n} pictures.`;
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
      return;
    }
    reportVerdictFailure("stack those groups", store.error);
    return;
  }
  const size = store.stackSizeFor(group);
  const result = await store.stack(group);
  if (result) {
    announcement.value = `Stacked ${size} pictures. The cover is kept and nothing is deleted.`;
    return;
  }
  reportVerdictFailure("stack that group", store.error);
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
  if (store.toggleExcluded(group, pictureId) !== false) return;
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
  noticeStore.info(sentence);
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
 * The server's own explanation for a refusal, when it gave one.
 *
 * A dedup verdict is refused for reasons the user can act on ("a stack needs at
 * least two pictures", a locked set), and a generic "could not stack that
 * group" hides every one of them behind the same sentence. FastAPI puts the
 * reason in `detail`; anything else is not a message worth quoting.
 *
 * @param {*} err - the rejection the store recorded.
 * @returns {string} the server's sentence, or the empty string.
 */
function serverDetail(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail !== "string") return "";
  const text = detail.trim();
  if (!text) return "";
  return /[.!?]$/.test(text) ? text : `${text}.`;
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
function reportVerdictFailure(what, err) {
  const detail = serverDetail(err);
  const because = detail ? ` ${detail}` : "";
  announcement.value = `Could not ${what}.${because} The group is still in the queue, so nothing was lost.`;
  noticeStore.error(
    `Could not ${what}.${because} The group is still in the queue, so you can try again.`,
  );
}

function onCompareStack() {
  const group = store.focusedGroup;
  compareOpen.value = false;
  if (group) onStack(group);
}

function onCompareKeepSeparate() {
  const group = store.focusedGroup;
  compareOpen.value = false;
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
 * Dismiss the tier popover and put the focus back on the control that opened
 * it, so the keyboard never has to hunt for where it went.
 */
function closeTierMenu() {
  if (!tierMenuOpen.value) return;
  tierMenuOpen.value = false;
  tierButtonEl.value?.focus?.();
}

/** A pointer press anywhere outside the popover dismisses it. */
function onDocumentPointerDown(event) {
  if (!tierMenuOpen.value) return;
  if (tierWrapEl.value?.contains?.(event.target)) return;
  tierMenuOpen.value = false;
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
 * it open over a list that just changed underneath reads as a glitch.
 */
async function onTierToggle(id, on) {
  closeTierMenu();
  await store.setTierEnabled(id, on);
}

/**
 * Move the similarity threshold.
 *
 * The popover stays open: a threshold is a value the user tunes and re-reads
 * against the count next to it, unlike a tier switch, which is a decision they
 * make once and then want to see the result of.
 */
async function onThresholdChange(value) {
  await store.setThreshold(value);
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

const onKeydown = createDedupKeyHandler({
  store,
  isCompareOpen: () => compareOpen.value,
  openCompare: () => {
    if (store.focusedGroup) compareOpen.value = true;
  },
  closeCompare,
  undo: () => operationStore.undo(),
  isReadOnly: () => readOnly.value,
  isBlocked: () => autoStackOpen.value || tierMenuOpen.value,
  // One row less than the viewport holds, so a page move keeps the row the
  // user was reading on screen as the anchor for the next one.
  pageRows: () => Math.max(1, viewportRows.value - 1),
  onEscape,
  onExclusionRefused: () => {
    announcement.value = STACK_FLOOR_NOTICE;
  },
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

// Compare renders through the shared dialog, which teleports out of this
// subtree and moves the focus into itself. Its keys therefore never bubble back
// to the queue root, so the model's Compare branch is bound at the document for
// exactly as long as the dialog is up. Keys the root handler claims stop
// propagating there, so nothing is handled twice.
watch(compareOpen, (open) => {
  if (typeof document === "undefined") return;
  if (open) document.addEventListener("keydown", onKeydown);
  else document.removeEventListener("keydown", onKeydown);
});

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
  return Object.keys(filters).length ? filters : null;
}

// The filter selection is part of the ADDRESS: a full refresh (or a shared
// link) must restore it. Mirrored with replace(), never push() — tuning the
// tier gate is not a history step the Back button should have to unwind.
// Non-default tier/threshold values are written explicitly (near=0 included):
// "absent" always means "the server's default", never "whatever it was".
const FILTER_QUERY_KEYS = ["near", "embedding", "threshold", "view"];

watch(
  () => [
    store.nearEnabled,
    store.embeddingEnabled,
    store.threshold,
    store.showingDecided,
    store.policyLoaded,
  ],
  () => {
    if (route.name !== "duplicates" || !store.policyLoaded) return;
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
    if (store.showingDecided) next.view = "decided";
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
watch(() => [route.query.scope, route.query.scope_id], syncQueueToRoute);

onMounted(() => {
  syncQueueToRoute();
  // The queue is a keyboard surface. Taking focus on mount is what makes the
  // first Enter work without the user hunting for a click target first.
  rootEl.value?.focus?.();
  nextTick(measureRowPitch);
  prefetchNextGroup();
  if (typeof document !== "undefined") {
    document.addEventListener("mousedown", onDocumentPointerDown);
  }
});

onBeforeUnmount(() => {
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
  min-height: var(--bar-height);
  padding: var(--space-2) var(--space-5);
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
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
</style>
