<template>
  <div class="wfv" role="region" aria-label="Workflows">
    <p class="visually-hidden" role="status">{{ announcement }}</p>

    <div class="wfv-toolbar shelfbar toolbar">
      <span class="wfv-title">Workflows</span>
      <span class="wfv-sub num">{{ subtitle }}</span>

      <v-menu
        v-model="sortMenuOpen"
        location="bottom start"
        origin="top start"
        :offset="8"
        :close-on-content-click="false"
      >
        <template #activator="{ props: menuProps }">
          <AppBarButton
            v-bind="menuProps"
            prefix="Sort:"
            aria-haspopup="menu"
            :aria-expanded="sortMenuOpen"
            >{{ SORT_LABELS[store.sortKey].label }}</AppBarButton
          >
        </template>
        <div class="tbm">
          <span class="tbm-caret tbm-caret--start"></span>
          <div class="tbm-header">
            <v-icon size="18" class="tbm-header-icon">mdi-sort</v-icon>
            <span class="tbm-title">Sort</span>
          </div>
          <div class="tbm-section">
            <span class="tbm-label">Sort by</span>
            <OptionRows
              :options="sortOptions"
              :model-value="store.sortKey"
              aria-label="Sort by"
              @update:model-value="store.setSortKey"
            />
          </div>
        </div>
      </v-menu>

      <!-- No count on the strip's own chips and a count here, which is the
           reverse of the picture grid: that toolbar keeps its strip on screen
           beside the button, and this one is a row that only exists while a
           filter is on. Both read the same `filterChips`. -->
      <v-menu
        v-model="filterMenuOpen"
        location="bottom start"
        origin="top start"
        :offset="8"
        :close-on-content-click="false"
      >
        <template #activator="{ props: menuProps }">
          <AppBarButton
            v-bind="menuProps"
            icon="filter"
            :badge="store.filterChips.length || null"
            :active="store.filterChips.length > 0 && !filterMenuOpen"
            :open="filterMenuOpen"
            aria-haspopup="menu"
            :aria-expanded="filterMenuOpen"
            >Filters</AppBarButton
          >
        </template>
        <WorkflowFilterMenu />
      </v-menu>

      <!-- "Add" is today's workflow import, unchanged: the file lands through
           `POST /comfyui/workflows/import`, the route the retired shelf's own
           drop target posted to. The gesture went with the shelf; this screen
           has no drop handler, so `useWindowFileImport` no longer stands
           aside for it. -->
      <AppBarButton icon="plus" @click="fileInput?.click()">Add…</AppBarButton>
      <input
        ref="fileInput"
        class="wfv-file-input"
        type="file"
        accept=".json,application/json"
        multiple
        tabindex="-1"
        aria-hidden="true"
        @change="filesChosen"
      />

      <span class="wfv-spacer"></span>
      <!-- The app-wide tail, minus undo: this view replaces the grid and its
           toolbar, so without it neither Settings nor the right rail has a
           control on the screen — and the rail is where `WorkflowTab` is
           shown (`AppInspector` gates it on `sidebarStore.statsOpen`), so the
           rail would be unreachable (#1415). Its own `--space-3` cluster
           because this bar spaces its controls wider, and `rail-name` because
           the toggle's tooltip is also its accessible name and this rail is
           not the stats sidebar. -->
      <span class="wfv-bar-tail">
        <TbGlobalActions
          separator
          rail-name="inspector"
          @open-settings="emit('open-settings')"
        />
      </span>
    </div>

    <FilterStrip
      inline
      :chips="store.filterChips"
      :of-label="store.filterOfLabel"
      class="wfv-strip"
    />

    <p v-if="store.error" class="wfv-error" role="alert">{{ store.error }}</p>

    <!-- A Recipe section's Open that named a workflow this grid does not list.
         Said on screen and not only in the live region, because the reader
         made a deliberate gesture and the screen otherwise looks as though it
         ignored them. -->
    <p v-if="linkMissed" class="wfv-note">
      That workflow is not in this grid.
      <template v-if="withheld"
        >{{ withheld }} {{ withheld === 1 ? "is" : "are" }} being left out:
        {{ withheldHidden }} hidden and {{ withheldOneOffs }} counted as
        one-offs.</template
      >
    </p>

    <!-- The fifth kind of empty: the library HAS workflows and the filters
         leave none. Not the empty state below - that screen offers three ways
         to get a first workflow, and this reader already has some. The strip
         above carries the way back, so this only has to say which it is. -->
    <p v-if="filteredOut" class="wfv-note">
      No workflow matches these filters.
      <button
        class="wfv-note-clear"
        type="button"
        @click="store.clearFilters()"
      >
        Clear all
      </button>
    </p>

    <!-- The §9 empty state: the shipped art, a Tiny5 `--text-2xl` headline and
         one `--text-sm` line, then the routes out as plain buttons. Not the
         bordered option rows `LibraryEmptyState` uses — that screen explains
         three unfamiliar choices at first run; these three are one verb each. -->
    <div v-if="showEmptyState" class="wfv-empty">
      <div class="wfv-empty__card">
        <div class="wfv-empty__illustration" aria-hidden="true">
          <img src="/Empty.png" alt="" />
        </div>
        <h2 class="wfv-empty__title">Nothing found yet</h2>
        <p v-if="withheld" class="wfv-empty__lead">
          Every workflow this library has is being left out of the grid:
          {{ withheldHidden }} hidden and {{ withheldOneOffs }} counted as
          one-offs. Nothing has been lost.
        </p>
        <p v-else class="wfv-empty__lead">
          A workflow arrives with the pictures it made, or as a file of its own.
        </p>
        <div class="wfv-empty__actions">
          <AppButton
            size="sm"
            variant="primary"
            icon-left="tray-arrow-up"
            @click="fileInput?.click()"
          >
            Drop a workflow file
          </AppButton>
          <AppButton
            v-if="watchedFolder"
            size="sm"
            variant="secondary"
            icon-left="folder-eye-outline"
            @click="openWatchedFolder"
          >
            Open watched folder
          </AppButton>
          <!-- Gone once connected: there is nothing to offer somebody who has
               already done it. -->
          <AppButton
            v-if="!comfyuiConfigured"
            size="sm"
            variant="secondary"
            icon-left="graph-outline"
            @click="emit('open-settings', 'comfyui')"
          >
            Connect ComfyUI
          </AppButton>
        </div>
        <p v-if="watchedFolder" class="wfv-empty__path">{{ watchedPath }}</p>
      </div>
    </div>

    <!-- The scroller carries the padding so the grid below is the bare
         track: `measure()` reads the grid's `clientWidth`, which INCLUDES its
         own padding, and a padded grid would be measured 32px wider than the
         space the columns actually have. -->
    <div v-else class="wfv-scroll">
      <!-- One `treegrid` and one tab stop: the cursor roves with the arrow
           keys and the focused row is the only one at `tabindex="0"`. -->
      <div
        ref="gridEl"
        class="wfv-grid"
        role="treegrid"
        aria-label="Workflow cards"
        aria-multiselectable="true"
        :aria-busy="store.loading"
        :style="{
          '--wf-columns': columns,
          '--wf-column-min': `${COLUMN_MIN}px`,
          '--wf-gap': `${COLUMN_GAP}px`,
        }"
        @keydown="onKeyDown"
      >
        <template v-for="(entry, index) in flatRows" :key="entry.id">
          <!-- The panel is drawn once, in the slot its FIRST member holds; the
             rest of the member entries are index space only, and the rows they
             name live inside it. -->
          <StackPanel
            v-if="entry.kind === 'member' && entry.memberIndex === 0"
            ref="panelRef"
            :panel-id="panelId"
            :name="openStackName"
            :members="store.openMembers"
            :size="store.openStackSize"
            :loading="store.membersLoading"
            :columns="columns"
            :column-index="openColumnIndex"
            :selected-keys="store.selectedKeys"
            :cursor-key="cursorKey"
            :can-reorder="Boolean(store.openStackId)"
            @close="closePanel"
            @select="(key, event) => onRowClick(memberRowIndex(key), event)"
            @make-cover="(key) => followMove(key, store.makeCover(key))"
            @move="(key, delta) => moveMember(key, delta)"
            @unstack="store.unstackMember"
            @hide="store.hideMember"
          />
          <div
            v-else-if="entry.kind === 'card'"
            class="wfv-row"
            role="row"
            aria-level="1"
            :aria-selected="store.selectedKeys.includes(entry.key)"
            :aria-expanded="
              isStack(entry.card)
                ? String(store.openStackKey === entry.key)
                : undefined
            "
            :aria-controls="
              isStack(entry.card) && store.openStackKey === entry.key
                ? panelId
                : undefined
            "
            :aria-owns="
              store.openStackKey === entry.key ? memberRowIds : undefined
            "
            :aria-posinset="entry.cardIndex + 1"
            :aria-setsize="store.sortedCards.length"
            :tabindex="index === cursorIndex ? 0 : -1"
            :data-key="entry.key"
            @click="onRowClick(index, $event)"
            @dblclick="isStack(entry.card) && store.toggleStack(entry.key)"
          >
            <div class="wfv-cell" role="gridcell">
              <WorkflowCard
                :card="entry.card"
                :selected="store.selectedKeys.includes(entry.key)"
                :expanded="store.openStackKey === entry.key"
                :panel-id="store.openStackKey === entry.key ? panelId : ''"
                @toggle="store.toggleStack(entry.key)"
              />
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The Workflows grid (v1.12 Workflows & Recipes, F1a) — on `/workflows` since
 * F1b retired the shelf. See `docs/frontend_architecture.md` §5.
 *
 * ONE FLAT LIST. The cards, and — while a stack is open — its members, are one
 * index space, so the roving cursor crosses the panel boundary with the same
 * `index ± columns` arithmetic it uses inside the grid. The members are
 * inserted at the END of the open stack's row and padded to a whole number of
 * rows, which is where the panel sits in the DOM: that, and only that, is what
 * makes `index % columns` the column a row is drawn in on both sides of the
 * boundary. The padding entries are holes the cursor skips.
 */
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon, VMenu } from "vuetify/components";

import { importWorkflow } from "../../api/comfyui";
import { listImportFolders } from "../../api/folders";
import { useFilterStore } from "../../stores/useFilterStore";
import { useWorkflowPrefsStore } from "../../stores/useWorkflowPrefsStore";
import {
  SORT_KEYS,
  SORT_LABELS,
  useWorkflowsStore,
} from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import { isStack } from "../../utils/workflowCard";
import FilterStrip from "../panels/FilterStrip.vue";
import StackPanel from "../panels/StackPanel.vue";
import TbGlobalActions from "../panels/TbGlobalActions.vue";
import WorkflowFilterMenu from "../panels/WorkflowFilterMenu.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import OptionRows from "../widgets/OptionRows.vue";
import WorkflowCard from "../widgets/WorkflowCard.vue";

/**
 * The two numbers the column count is derived from, and the only copy of them.
 *
 * They are written onto the grid as `--wf-column-min` and `--wf-gap` rather
 * than read back out of the sheet, so the arithmetic here and the track the
 * browser paints are the same two numbers by construction. `COLUMN_GAP` is
 * `--space-3` (see below); `COLUMN_MIN` is the card's width, the partner of the
 * `--wf-meta-h` `WorkflowCard.vue` keeps locally. The card's HEIGHT is not a
 * number either side holds: the cover is a 6:5 box, so a card is as tall as
 * its column is wide plus the fixed meta block.
 */
const COLUMN_MIN = 240;
// `--space-3`. It was `--space-4` (12), which is the "comfortable gap inside a
// group" and is what the reading surfaces use; this is a grid of cards, and
// its siblings are dense - the picture grid sets its thumbnails `--space-2`
// apart and the model shelf's rows touch. 8 is the small gap between
// bordered things, which is what these are.
const COLUMN_GAP = 8;

// Settings is App.vue's dialog; TbGlobalActions flips the sidebar store itself.
const emit = defineEmits(["open-settings"]);

const store = useWorkflowsStore();
const filterStore = useFilterStore();
const prefs = useWorkflowPrefsStore();
const router = useRouter();
const route = useRoute();

const gridEl = ref(null);
// `v-for`'d, so Vue hands back an array even though only one panel is ever
// drawn — one stack is open at a time.
const panelRef = ref(null);
const fileInput = ref(null);
const sortMenuOpen = ref(false);
const filterMenuOpen = ref(false);
const columns = ref(1);
// **The cursor is an entry id, not an index.** `flatRows` is rebuilt by a
// resort, by a stack opening and by one closing, and an index held across any
// of those names a different card — or, when the list shrinks, no card at all,
// which takes the grid's only tab stop with it and strands a keyboard user
// outside a screen they cannot get back into.
const cursorId = ref("");
const announcement = ref("");
// A `?topology=` link that named a workflow the grid does not list.
const linkMissed = ref(false);
const watchedFolder = ref(null);

const panelId = "wfv-stack-panel";
const sortOptions = SORT_KEYS.map((key) => ({
  id: key,
  label: SORT_LABELS[key].label,
  icon: SORT_LABELS[key].icon,
}));

const comfyuiConfigured = computed(() => filterStore.comfyuiConfigured);
const showEmptyState = computed(
  () => store.loaded && !store.loading && store.cards.length === 0,
);
const filteredOut = computed(
  () =>
    store.loaded &&
    !store.loading &&
    store.cards.length > 0 &&
    store.sortedCards.length === 0,
);

/**
 * How many workflows the grid HAS but is not drawing.
 *
 * "Nothing found yet" over a subtitle reading "40 hidden · 12 one-offs" is a
 * screen arguing with itself, so the empty state says which of the two it is
 * in. Show — the control that would let somebody act on it — needs the write
 * routes that are not in `develop` yet, so for now this is a sentence rather
 * than a way back in.
 */
const withheldHidden = computed(() =>
  store.filters.showHidden ? 0 : store.hidden,
);
const withheldOneOffs = computed(() =>
  store.filters.hideOneOffs ? store.oneOffs : 0,
);
const withheld = computed(() => withheldHidden.value + withheldOneOffs.value);

const subtitle = computed(() => {
  const count = store.sortedCards.length;
  const parts = [count === 1 ? "1 workflow" : `${count} workflows`];
  // Only what is still being LEFT OUT. A filter that let the hidden ones in
  // makes "40 hidden" a caption for cards the reader is looking at.
  if (withheldHidden.value) parts.push(`${withheldHidden.value} hidden`);
  if (withheldOneOffs.value) parts.push(`${withheldOneOffs.value} one-offs`);
  return parts.join(" · ");
});

/**
 * The cards, with the open stack's members spliced in at its row's end.
 *
 * `{kind: "card"|"member"|"hole", id, key, card, memberIndex?}`. `id` is unique
 * where `key` is not: a stack's COVER is drawn twice — once as the grid's stack
 * card and once, flagged, as the panel's first member — so the two rows share
 * a card key and must not share a `v-for` key or a lookup. `hole` pads the
 * member block out to whole rows, so every card after the panel keeps naming
 * the column it is drawn in.
 */
const flatRows = computed(() => {
  const cards = store.sortedCards.map((card, cardIndex) => ({
    kind: "card",
    id: `card:${card.key}`,
    key: card.key,
    card,
    cardIndex,
  }));
  const openKey = store.openStackKey;
  if (!openKey) return cards;
  const at = cards.findIndex((entry) => entry.key === openKey);
  if (at < 0) return cards;
  const cols = Math.max(1, columns.value);
  const rowEnd = (Math.floor(at / cols) + 1) * cols;
  // **The stack's own row is padded to whole first**, not just the member
  // block. A stack in the last, INCOMPLETE row would otherwise splice the
  // block in at `cards.length`, which is not a multiple of `cols`, and from
  // there `index % columns` stops naming the column a row is drawn in: six
  // cards over four columns put the block at index 6, so Down from the stack
  // card landed on a hole with nothing below it and the panel could not be
  // reached by keyboard at all.
  const head = cards.slice(0, rowEnd);
  while (head.length < rowEnd) {
    head.push({ kind: "hole", id: `hole:head:${head.length}` });
  }
  const members = store.openMembers;
  const block = members.map((card, memberIndex) => ({
    kind: "member",
    id: `member:${card.key}`,
    key: card.key,
    card,
    memberIndex,
  }));
  const padded = Math.ceil(Math.max(block.length, 1) / cols) * cols;
  while (block.length < padded) {
    block.push({ kind: "hole", id: `hole:block:${block.length}` });
  }
  return [...head, ...block, ...cards.slice(rowEnd)];
});

/** The open stack card's column, for the panel's notch. */
const openColumnIndex = computed(() => {
  const at = flatRows.value.findIndex(
    (entry) => entry.kind === "card" && entry.key === store.openStackKey,
  );
  return at < 0 ? 0 : at % Math.max(1, columns.value);
});

const openStackName = computed(
  () =>
    store.cards.find((card) => card.key === store.openStackKey)?.name ||
    "Stack",
);

/** Where the cursor is now. Always a real row: it falls back to the first. */
const cursorIndex = computed(() => {
  const at = flatRows.value.findIndex((entry) => entry.id === cursorId.value);
  return at >= 0 ? at : (firstStop(0, 1) ?? 0);
});

/** The cursor's key when it is inside the panel, else "". */
const cursorKey = computed(() => {
  const entry = flatRows.value[cursorIndex.value];
  return entry?.kind === "member" ? entry.key : "";
});

/**
 * The member rows' DOM ids, for the stack row's `aria-owns`.
 *
 * **This is what makes the treegrid true.** The panel is a full-width band
 * after the LAST card of the stack's row, so in DOM order the member rows
 * follow whichever card ends that row, not the expanded one — and a reader on
 * the stack card pressing Down would be handed its right-hand neighbour
 * instead of its children. `aria-owns` re-parents them in the accessibility
 * tree without moving a pixel, which is the one thing it is for.
 */
const memberRowIds = computed(() =>
  store.openMembers.map((card) => `${panelId}-row-${card.key}`).join(" "),
);

/** Where one of the panel's member rows sits in the flat list. */
function memberRowIndex(key) {
  return flatRows.value.findIndex(
    (entry) => entry.kind === "member" && entry.key === key,
  );
}

// ── Columns from the real container width ─────────────────────────────────
// The count the grid is TOLD to draw, so `auto-fill` cannot disagree with the
// arithmetic the cursor and the notch are built on.
function measure() {
  // The grid carries no padding (`.wfv-scroll` does), so `clientWidth` is the
  // track, not the track plus a border box's worth of inset.
  const width = gridEl.value?.clientWidth ?? 0;
  columns.value = Math.max(
    1,
    Math.floor((width + COLUMN_GAP) / (COLUMN_MIN + COLUMN_GAP)),
  );
}

let observer = null;
watch(gridEl, (element) => {
  observer?.disconnect();
  observer = null;
  if (!element || typeof ResizeObserver === "undefined") return;
  observer = new ResizeObserver(measure);
  observer.observe(element);
  measure();
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = null;
});

onMounted(async () => {
  store.fetchCards();
  try {
    const body = await listImportFolders();
    watchedFolder.value = (body?.folders ?? [])[0] ?? null;
  } catch (err) {
    // Not an error worth a banner: it costs the empty state one of its three
    // routes out, and the other two still work.
    console.warn("[workflows] could not read the watched folders", err);
  }
});

/** What the owner calls the folder: the host path when the server has one. */
const watchedPath = computed(
  () => watchedFolder.value?.host_path || watchedFolder.value?.folder || "",
);

// ── Arriving from a picture's Recipe section (#1313) ──────────────────────
//
// `?topology=<hash>` selects the card that topology made and puts the cursor
// on it, so the link from the lightbox lands on the card rather than at the
// top of the grid. A topology can hold several cards — a different checkpoint
// is a different card — so the FIRST in the sorted order is taken: it is the
// one the reader would have found first anyway.
//
// **It can only reach the cards the GRID lists, and that is not every card.**
// `GET /workflows/cards` leaves out the hidden ones and the one-offs (under
// three pictures, unrated, not imported, no saved recipe) — which is the
// ordinary state of a workflow used once, and exactly when "what made this?"
// is worth asking. A stack is one card here too, grouped by `core_hash`, which
// strips post-processing, so a member can carry a topology its cover does not.
// The shelf had no such gap, listing every topology as a row of its own, and
// closing it needs a topology→card read the API does not have.
//
// So a miss SAYS SO rather than doing nothing. Dropping the reader at the top
// of a grid that does not contain what they clicked, with no word about it, is
// the silent failure this screen must not have; the subtitle already counts
// what is being withheld, so the sentence has something true to point at. F7
// replaces this link with the card's own *Show all N pictures* chip.
//
// Honoured once per value rather than on every `cards` change, because the
// grid is refetched (a file is added, a LoRA slot is flipped) while the query
// string stays put, and a second application would take focus back off
// whatever the reader had moved to. `immediate` because the store's cards
// outlive a route change: a second visit on the same link has nothing left to
// change for the watcher to see.
//
// **Only a HIT is marked honoured.** The store outlives this component, so the
// `immediate` pass runs during setup against whatever the last visit left in
// `cards` — before `onMounted` refetches. Marking a MISS honoured there makes
// a verdict reached on a stale list permanent: a workflow that has since
// crossed the one-off threshold, or arrived from the watched folder, is in the
// fresh answer, but the refetch's re-fire returns at the guard above and the
// screen goes on saying the opposite of what the grid holds. A hit still
// applies once, which is all the focus protection was ever for; only the "not
// here" verdict is left open to fresher data.
let honouredTopology = null;

watch(
  [() => route.query?.topology, () => store.sortedCards],
  ([wanted]) => {
    if (!wanted) {
      honouredTopology = null;
      // The query can go without a remount - the sidebar's Workflows entry
      // pushes this same route with no query - and the note belongs to the
      // link, not to the screen.
      linkMissed.value = false;
      return;
    }
    // A repeated `?topology=` makes this an array, which matches no card. It
    // takes the same path as any other miss rather than a branch of its own.
    if (honouredTopology === wanted) return;
    // Nothing to say until the grid has been read: "not here" is false while
    // the answer is still on the wire.
    if (!store.loaded) return;
    const card = store.sortedCards.find(
      (entry) => entry.topology_hash === wanted,
    );
    if (!card) {
      const plural = withheld.value === 1 ? "is" : "are";
      announcement.value = withheld.value
        ? `That workflow is not in the grid. ${withheld.value} ${plural} being left out: ${store.hidden} hidden and ${store.oneOffs} counted as one-offs.`
        : "That workflow is not in the grid.";
      linkMissed.value = true;
      return;
    }
    honouredTopology = wanted;
    linkMissed.value = false;
    store.select(card.key);
    // Not while the reader is inside a popover or the file dialog: the cards
    // arrive asynchronously, so this can fire a second after the screen went
    // interactive, and yanking focus out from under a gesture in progress is
    // worse than landing at the top of the grid. `onKeyDown` guards on the
    // same flag.
    //
    // The row is looked up BY ID inside the callback, never as an index
    // computed out here: `flatRows` is rebuilt by a resort, by a stack opening
    // and by `columns` landing — which it does between setup and this tick,
    // since `measure()` runs as a pre-flush job — and an index held across any
    // of those names a different card, or a `hole`. That is the whole reason
    // the cursor is an id (see `cursorId`).
    if (!sortMenuOpen.value) {
      nextTick(() =>
        moveCursor(
          flatRows.value.findIndex((entry) => entry.id === `card:${card.key}`),
        ),
      );
    }
  },
  { immediate: true },
);

function openWatchedFolder() {
  const id = watchedFolder.value?.id;
  if (id != null) {
    router.push({ name: "import-folder", params: { id: String(id) } });
  }
}

// ── Selection and the panel ───────────────────────────────────────────────

function onRowClick(index, event) {
  const entry = flatRows.value[index];
  if (!entry || entry.kind === "hole") return;
  cursorId.value = entry.id;
  if (event?.shiftKey) selectToCursor(index);
  else store.select(entry.key, { additive: event?.ctrlKey || event?.metaKey });
}

/**
 * Every selectable key between the first already-selected row and `index`.
 *
 * The anchor is read off the selection rather than remembered, so a Shift-click
 * after a Ctrl-click extends from the topmost of them. Holes are dropped and a
 * cover, which is in the range twice, counts once.
 */
function selectToCursor(index) {
  const anchor = flatRows.value.findIndex((entry) =>
    store.selectedKeys.includes(entry.key),
  );
  const from = anchor < 0 ? index : anchor;
  const [start, end] = from <= index ? [from, index] : [index, from];
  // Deduplicated: a cover inside the range is both a card row and a member
  // row, and selecting it twice would make the count lie.
  store.selectRange([
    ...new Set(
      flatRows.value
        .slice(start, end + 1)
        .filter((entry) => entry.kind === "card" || entry.kind === "member")
        .map((entry) => entry.key),
    ),
  ]);
}

/**
 * Close the panel and put the cursor back on the card that opened it.
 *
 * Esc and the panel's own Close come through here. ▸ and the layered count do
 * NOT: those are a click on a card, and the cursor belongs where the reader
 * left it rather than wherever the thing they clicked happened to be.
 */
function closePanel() {
  const key = store.openStackKey;
  store.closeStack();
  if (!key) return;
  const at = flatRows.value.findIndex(
    (entry) => entry.kind === "card" && entry.key === key,
  );
  if (at >= 0) moveCursor(at);
}

/**
 * A stack opening or closing announces itself: every row below it moves, and a
 * reader who is not on that card hears nothing otherwise.
 *
 * Keyed on `openStackKey` ALONE and counted from `stack_size`, which the cover
 * card already carries. Watching how many members had arrived announced twice
 * — "0 workflows" the moment the panel opened, then the real figure once the
 * detail requests landed — and the first of those was simply wrong.
 *
 * The closing announcement matters as much as the opening one and was missing:
 * the rows move just as far on the way back. The count is part of the string
 * so that reopening the same stack is not a no-op for a reader whose screen
 * reader suppresses an unchanged live region.
 */
watch(
  () => store.openStackKey,
  (key, previous) => {
    if (key) {
      const size = store.openStackSize;
      announcement.value = `${openStackName.value} opened, ${size} workflows`;
      return;
    }
    const closed = store.cards.find((entry) => entry.key === previous);
    announcement.value = `${closed?.name || "Stack"} closed`;
  },
);

// ── The roving cursor ─────────────────────────────────────────────────────

/** First index at or after `index` that is a real row, travelling in `step`. */
function firstStop(index, step) {
  for (let i = index; i >= 0 && i < flatRows.value.length; i += step) {
    const kind = flatRows.value[i]?.kind;
    if (kind === "card" || kind === "member") return i;
  }
  return null;
}

/**
 * The row above or below `index`, keeping its column.
 *
 * Steps by WHOLE ROWS over the padding holes, never one index at a time: a
 * stack whose last member row is ragged — five members across four columns —
 * leaves holes in the middle of the index space, and a linear scan walks out
 * of the column the reader is travelling down. Only once the row-wise walk has
 * left the list entirely does it settle for the nearest row in the direction
 * of travel, so the last row stays reachable from every column.
 */
function verticalStop(index, cols, direction) {
  const total = flatRows.value.length;
  for (let i = index; i >= 0 && i < total; i += direction * cols) {
    const kind = flatRows.value[i]?.kind;
    if (kind === "card" || kind === "member") return i;
  }
  return firstStop(Math.min(Math.max(index, 0), total - 1), direction);
}

/**
 * The DOM row for one flat entry.
 *
 * Scoped by kind, not by key: a stack's cover holds the same key twice, once
 * in the grid and once in the panel. A key is arbitrary text, so it is
 * compared rather than spliced into a selector.
 */
function rowElement(entry) {
  if (!entry) return undefined;
  const selector =
    entry.kind === "member" ? ".stack-panel__member" : ".wfv-row";
  return Array.from(gridEl.value?.querySelectorAll(selector) ?? []).find(
    (element) => element.dataset.key === entry.key,
  );
}

/** Put the cursor on whichever real row `index` names, if there is one. */
function moveCursor(index) {
  const entry = flatRows.value[index];
  if (!entry || entry.kind === "hole") return;
  cursorId.value = entry.id;
  nextTick(() => rowElement(entry)?.focus());
}

/**
 * True while the open panel draws its members one per line.
 *
 * The flat index space does not change with the view — the member block is
 * still padded to whole grid rows, so every card AFTER the panel keeps naming
 * the column it is drawn in. What changes is the STEP: in List a vertical move
 * inside the block is one member, and stepping by `columns` would walk past
 * two of every three.
 */
const listMembers = computed(
  () => prefs.stackView === "list" && Boolean(store.openStackKey),
);

/** Where the first and last member rows sit in the flat list. */
function memberBounds() {
  const first = flatRows.value.findIndex((entry) => entry.kind === "member");
  if (first < 0) return null;
  let last = first;
  for (let i = first; i < flatRows.value.length; i += 1) {
    if (flatRows.value[i].kind === "member") last = i;
  }
  return { first, last };
}

/**
 * The row a vertical arrow should land on.
 *
 * Grid keeps the column, by the whole-row walk `verticalStop` does. List steps
 * one member at a time inside the panel, and **entering** the panel from the
 * grid lands on the member nearest the direction of travel: every member is in
 * the same column there, so "keep your column" has only one answer.
 */
function verticalTarget(direction) {
  const cols = Math.max(1, columns.value);
  const index = cursorIndex.value;
  const bounds = listMembers.value ? memberBounds() : null;
  if (!bounds) return verticalStop(index + direction * cols, cols, direction);
  if (flatRows.value[index]?.kind === "member") {
    return firstStop(index + direction, direction);
  }
  const target = verticalStop(index + direction * cols, cols, direction);
  // **Crossed, not landed on.** The block is padded to whole grid rows, so a
  // two-member stack over four columns leaves holes in two of them — and the
  // whole-row walk steps straight over the panel and out the other side.
  // Testing where it came to rest reached the panel from the columns the
  // members happened to fill and jumped it from the rest, which is a panel
  // that is keyboard-reachable or not depending on the window's width.
  if (direction === 1 && index < bounds.first && target >= bounds.first) {
    return bounds.first;
  }
  if (direction === -1 && index > bounds.last && target <= bounds.last) {
    return bounds.last;
  }
  return target;
}

function onKeyDown(event) {
  // The sort popover owns its own keys, Escape included.
  if (sortMenuOpen.value) return;
  const entry = flatRows.value[cursorIndex.value];
  // Alt+Up / Alt+Down MOVE the row rather than travelling to another, and
  // only inside the panel: the grid's own order is the sort, which is not
  // something a card can be dragged around in. Checked before the plain
  // arrows so the modifier is not swallowed by the cursor.
  if (event.altKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
    event.preventDefault();
    // Swallowed whether or not it can act. A write re-reads the grid and
    // rebuilds every member row, so a HELD Alt+Down arrives while the cursor
    // is briefly not on a member — and falling through would then walk the
    // cursor down the grid instead, which is a held key that reorders twice
    // and then leaves the panel.
    if (entry?.kind === "member") {
      moveMember(entry.key, event.key === "ArrowDown" ? 1 : -1);
    }
    return;
  }
  switch (event.key) {
    case "ArrowRight":
      event.preventDefault();
      moveCursor(firstStop(cursorIndex.value + 1, 1));
      return;
    case "ArrowLeft":
      event.preventDefault();
      moveCursor(firstStop(cursorIndex.value - 1, -1));
      return;
    case "ArrowDown":
      event.preventDefault();
      moveCursor(verticalTarget(1));
      return;
    case "ArrowUp":
      event.preventDefault();
      moveCursor(verticalTarget(-1));
      return;
    case " ":
      event.preventDefault();
      if (entry && entry.kind !== "hole") {
        store.select(entry.key, { additive: true });
      }
      return;
    case "Enter":
      event.preventDefault();
      // A stack card's Enter opens the panel; anything else opens ⓘ, which is
      // the card's own button and the only escape hatch every card has —
      // except a List member row, which draws no card and so has no ⓘ. Its
      // menu is what the row offers instead.
      if (entry?.kind === "card" && isStack(entry.card)) {
        store.toggleStack(entry.key);
      } else if (
        !activeCardButton(".wf-card__info") &&
        entry?.kind === "member"
      ) {
        openMemberMenu(entry.key);
      } else {
        activeCardButton(".wf-card__info")?.click();
      }
      return;
    case "F10":
      if (!event.shiftKey) return;
      event.preventDefault();
      // The context-menu keys reach the MEMBER MENU on a member row — the
      // same menu right-click opens, and the only route to *Unstack* and
      // *Hide* for somebody not using a pointer: both ⋯ buttons sit at
      // `tabindex="-1"` because the grid owns Tab. Elsewhere they still open
      // ⓘ, which is all a top-level card has.
      if (entry?.kind === "member") openMemberMenu(entry.key);
      else activeCardButton(".wf-card__info")?.click();
      return;
    case "ContextMenu":
      event.preventDefault();
      if (entry?.kind === "member") openMemberMenu(entry.key);
      else activeCardButton(".wf-card__info")?.click();
      return;
    case "Escape":
      // Innermost first. The popover and the card menu are `VMenu`s: they
      // consume their own Escape and it never reaches here, so what is left is
      // the panel, then the selection.
      if (store.openStackKey) {
        event.preventDefault();
        closePanel();
      } else if (store.selectedKeys.length) {
        event.preventDefault();
        store.clearSelection();
      }
      return;
    default:
  }
}

/**
 * Follow a member that has just been reordered, and say where it landed.
 *
 * **Every path that moves a member goes through here** — Alt+↑/↓, and the
 * menu's *Move earlier* / *Move later* / *Make it the cover*, which reach the
 * same three writes by pointer or by Shift+F10. The write re-reads the grid,
 * so every member row is torn down and rebuilt and the browser drops the
 * focus it was holding; the menu's own activator is a pair of coordinates, so
 * there is no element for it to fall back to. Without this a keyboard reader
 * choosing *Move later* is left outside the grid, told only that the panel
 * closed and reopened, and never that the row moved at all.
 */
async function followMove(key, write) {
  const moved = await write;
  const at = memberRowIndex(key);
  if (at < 0) return;
  moveCursor(at);
  // **Only a move that happened is announced.** A stack with no `stack_id` —
  // one the grid drew part of — and a refused write both come back here, and
  // "position 2 of 6" over either tells a reader the row moved when the list
  // in front of them is unchanged. `store.error` already carries the refusal.
  if (!moved) return;
  const position = store.openMembers.findIndex((card) => card.key === key);
  const member = store.openMembers[position];
  announcement.value = `${member?.name || "Workflow"}, position ${
    position + 1
  } of ${store.openMembers.length}`;
}

/** Alt+↑/↓, and the menu's two Move items. */
function moveMember(key, delta) {
  return followMove(key, store.moveMember(key, delta));
}

/** Open the open panel's member menu on `key`, anchored on its row. */
function openMemberMenu(key) {
  const panel = Array.isArray(panelRef.value)
    ? panelRef.value[0]
    : panelRef.value;
  panel?.openMenuAt(key);
}

/** One of the cursor card's own `tabindex="-1"` buttons. */
function activeCardButton(selector) {
  return rowElement(flatRows.value[cursorIndex.value])?.querySelector(selector);
}

// ── Add a workflow file ───────────────────────────────────────────────────

/** Larger than any real workflow; a bigger file is not read into memory. */
const MAX_WORKFLOW_BYTES = 50 * 1024 * 1024;

async function filesChosen(event) {
  const files = Array.from(event.target?.files ?? []);
  event.target.value = "";
  let added = false;
  for (const file of files) {
    if (file.size > MAX_WORKFLOW_BYTES) {
      store.error = `${file.name} is too large to be a workflow.`;
      continue;
    }
    try {
      await importWorkflow({
        name: file.name.replace(/\.json$/i, ""),
        workflow: JSON.parse(await file.text()),
        keepBoth: true,
      });
      added = true;
    } catch (err) {
      store.error = errorMessage(
        err,
        `Could not add ${file.name}: it may not be a workflow.`,
      );
      console.warn(`[workflows] could not add ${file.name}`, err);
    }
  }
  if (added) await store.fetchCards();
}
</script>

<style scoped>
.wfv {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

/* The retired workflow shelf's box recipe, carried over so the screen that
   replaced it sits at the same height beside the model shelf: a fixed 36
   with the hairline INSIDE it and no vertical padding. `--bar-height` is 48
   and would not. `container-name` carries the names the shared chrome's own
   `@container` rules are written against — the class attribute alone does
   nothing, they are container names and not classes. */
.wfv-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  container-type: inline-size;
  container-name: shelfbar toolbar;
  box-sizing: border-box;
  flex: none;
  height: 36px;
  padding: 0 var(--space-3) 0 var(--space-5);
  background: rgb(var(--v-theme-toolbar));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

/* The identity pair every view bar wears, at the one size a 36px band takes.
   The title gives first and from the right: every control here is `nowrap`,
   so without a shrinking member the surplus leaves through the right edge and
   what gets clipped is the tail. */
.wfv-title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  min-width: 0;
  flex-shrink: 6;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wfv-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), 0.6);
  white-space: nowrap;
  min-width: 0;
  flex-shrink: 4;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wfv-spacer {
  flex: 1;
}

/* The app-wide tail's own gap: this bar spaces its controls at --space-4, and
   the tail must land at the --space-3 every other host lays it out at. Never
   the member that gives — it is the app-wide chrome, and the control that
   opens the rail must not be the one the edge eats. */
.wfv-bar-tail {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 0 0 auto;
}

.wfv-error {
  margin: 0;
  padding: var(--space-3) var(--space-5);
  color: rgb(var(--v-theme-error));
  font-size: var(--text-sm);
}

/* Not an error - the grid is behaving as designed and the reader simply asked
   for something it does not draw - so the quiet ink rather than the error one. */
.wfv-note {
  margin: 0;
  padding: var(--space-3) var(--space-5);
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: var(--text-sm);
}
.wfv-note-clear {
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
}
.wfv-note-clear:hover {
  color: rgb(var(--v-theme-primary));
}

/* The chip strip sits in the flow here (`inline`), so it needs the screen's
   own side padding rather than the picture grid's full-bleed bar. */
.wfv-strip {
  padding-inline: var(--space-5);
}

/* The scroller, and the only thing here carrying padding: the grid inside it
   has to be a bare track, because its `clientWidth` is what the column count
   is computed from and that measurement includes an element's own padding. */
.wfv-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* `--space-3`, not the `--space-5` this shipped with. 16 down every side is
     what Moves and Insights use, and those say in as many words that they are
     reading surfaces; the picture grid, which this is actually a sibling of,
     carries no side padding at all and lets the scrollbar's own gutter be the
     gap. Cards have a border, so bleeding them to the window edge would look
     like a mistake - 8 is the smallest inset that still reads as deliberate.

     `scrollbar-gutter: stable` is reserved as the model shelf reserves it,
     though for a smaller reason than the shelf's: nothing here breaks without
     it - the grid's `clientWidth` shrinks, the `ResizeObserver` fires and the
     columns recompute correctly - but the whole track jumps sideways the
     moment the grid crosses one screen. Reserved, the only movement is the
     vertical one the reader asked for. */
  scrollbar-gutter: stable;
  padding: var(--space-3);
}

/* `--wf-columns` is set from the measured width, so the painted grid and the
   cursor's arithmetic cannot disagree; `--wf-gap` and `--wf-column-min` are
   the two numbers that count was derived from, written by the same JS that
   derived it. The gap is `--space-4` and the floor is the card's own width,
   but they are set from script rather than read from the sheet because a
   token edit that moved one without the other would leave the painted grid
   and the cursor's arithmetic disagreeing silently — which is the exact
   failure this whole "computed, not auto-fill" design exists to prevent. */
.wfv-grid {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  align-content: start;
  gap: var(--wf-gap);
}

.wfv-cell {
  border-radius: var(--radius-md);
}

/* The selection mark is the CARD's own (`WorkflowCard.vue`,
   `.wf-card--selected`). It was here, on the cell, and never showed: the card
   paints an opaque surface across the whole cell and covered it. */

.wfv-file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

/* The §9 empty state: the shipped art, a Tiny5 `--text-2xl` headline, a
   `--text-sm` line, and the routes out. */
.wfv-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
}

.wfv-empty__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  width: min(480px, calc(100% - var(--space-7)));
  box-sizing: border-box;
  margin: auto;
  padding: var(--space-6) var(--space-7);
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-background));
  text-align: center;
  box-shadow: var(--elevation-3);
}

/* The cap `LibraryEmptyState` uses, so the two cards show the art at one size. */
.wfv-empty__illustration {
  width: 90%;
  max-width: 260px;
  color: rgba(var(--v-theme-on-panel), 0.45);
}

.wfv-empty__illustration img {
  display: block;
  width: 100%;
  height: auto;
}

.wfv-empty__title {
  margin: 0;
  font-family: var(--font-pixel);
  font-size: var(--text-2xl);
  font-weight: var(--weight-regular);
  line-height: var(--leading-tight);
}

.wfv-empty__lead {
  margin: 0;
  max-width: 46ch;
  color: rgba(var(--v-theme-on-background), 0.72);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.wfv-empty__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-3);
}

/* The path is a value, not prose: monospace, and it wraps rather than
   ellipsizes, because half a path names the wrong folder. */
.wfv-empty__path {
  margin: 0;
  max-width: 100%;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.72);
  overflow-wrap: anywhere;
}
</style>
