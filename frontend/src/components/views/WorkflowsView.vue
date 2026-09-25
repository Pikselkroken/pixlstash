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
        <WorkflowFilterMenu :open="filterMenuOpen" />
      </v-menu>

      <!-- "Add" is today's workflow import, unchanged: the file lands through
           `POST /comfyui/workflows/import`, the route the retired shelf's own
           drop target posted to. The gesture went with the shelf; this screen
           has no drop handler, so `useWindowFileImport` no longer stands
           aside for it. -->
      <AppBarButton icon="plus" @click="fileInput?.click()">Add…</AppBarButton>
      <!-- #1440: read every workflow ComfyUI has saved, over its own API.
           Only offered once ComfyUI is connected - the empty state's
           "Connect ComfyUI" is the way there - and it reads, never writes
           back. What it found lands in the band below the toolbar. -->
      <!-- `loading` rather than a hand-rolled busy state: it refuses the
           second press, spins the glyph and gives focus back when done. -->
      <AppBarButton
        v-if="comfyuiConfigured"
        ref="pullButton"
        icon="tray-arrow-down"
        :loading="pull.phase === 'pulling'"
        data-testid="wfv-pull"
        @click="pull.start()"
        >{{
          pull.phase === "pulling" ? "Pulling…" : "Pull from ComfyUI"
        }}</AppBarButton
      >
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

    <!-- `on-clear-all` because two of these filters are the server's: removing
         the chips one at a time would be two `setFilters`, and so two grid
         reads, where the panel's own Clear all is one. -->
    <FilterStrip
      inline
      :chips="store.filterChips"
      :of-label="store.filterOfLabel"
      :on-clear-all="store.clearFilters"
      class="wfv-strip"
    />

    <WorkflowPullSummary @dismissed="pullButton?.focus()" />

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

    <!-- A `?topology=` link whose card the payload holds and a filter hides.
         Not the note above: that one is about what the SERVER left out and
         offers no way in, and this one is one control away from fixed. -->
    <p v-if="linkFiltered" class="wfv-note" data-testid="wfv-link-filtered">
      That workflow is in this grid, but a filter is hiding it.
      <button
        class="wfv-note-clear"
        type="button"
        @click="store.clearFilters()"
      >
        Clear all
      </button>
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
          <!-- The connected half of the pair below: somebody with ComfyUI
               connected most likely has workflows saved there already. -->
          <AppButton
            v-if="comfyuiConfigured"
            size="sm"
            variant="secondary"
            icon-left="tray-arrow-down"
            :loading="pull.phase === 'pulling'"
            data-testid="wfv-empty-pull"
            @click="pull.start()"
          >
            {{ pull.phase === "pulling" ? "Pulling…" : "Pull from ComfyUI" }}
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
    <div v-else ref="scrollEl" class="wfv-scroll">
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
            :selected="openStackSelected"
            :cursor-key="cursorKey"
            :can-reorder="Boolean(store.openStackId)"
            :closing="store.panelClosing"
            @collapsed="store.finishCollapse"
            @close="closePanel"
            @select="(key, event) => onRowClick(memberRowIndex(key), event)"
            @make-cover="(key) => followMove(key, store.makeCover(key))"
            @move="(key, delta) => moveMember(key, delta)"
            @unstack="store.unstackMember"
            @hide="store.hideMember"
            @open-picture="openPicture"
          />
          <div
            v-else-if="entry.kind === 'card'"
            class="wfv-row"
            role="row"
            aria-level="1"
            :aria-selected="cardSelected(entry.key)"
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
            @contextmenu.prevent="openRowMenu(index, $event)"
          >
            <div class="wfv-cell" role="gridcell">
              <WorkflowCard
                :card="entry.card"
                :selected="cardSelected(entry.key)"
                :expanded="store.openStackKey === entry.key"
                :panel-id="store.openStackKey === entry.key ? panelId : ''"
                @toggle="store.toggleStack(entry.key)"
                @run="runCard(entry.card)"
              />
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- The pill floats over the END of the list, exactly as the shelf's and
         the photo grid's do: the list is what the selection was made in, and a
         docked strip between the toolbar and the cards would push the whole
         grid down every time a card was clicked. `.selbar-float` turns
         `pointer-events` off on the strip and back on for the pill, so the
         cards underneath it stay clickable. -->
    <div class="selbar-float">
      <WorkflowSelectionBar
        ref="selBarRef"
        @select-all="selectAllShown"
        @menu-closed="focusCursorRow"
        @open-cover="openCoverPicture"
        @run="runSelected"
        @stack="stackSelected"
        @unstack="unstackSelected"
        @rename="startRename"
        @make-cover="makeCoverOfSelected"
        @hide="hideSelected"
        @export="exportSelected"
        @duplicate="duplicateSelected"
        @clone-with-models="startCloneWithModels"
        @delete="confirmDelete"
      />
    </div>

    <!-- Rename needs somewhere to type, and the card has nowhere: its name row
         is one line of four in a fixed block, and an inline field there would
         reflow the card it is editing. One small dialog instead — the same
         shape `StackPanel` said its member menu was missing. -->
    <!-- `closeRename`, not `renameOpen = false`: a dialog closing has the same
         debt the context menu has — focus lands wherever the teleported
         surface left it, which is outside the grid. -->
    <AppDialog
      :open="renameOpen"
      title="Rename workflow"
      size="sm"
      @close="closeRename"
    >
      <AppInput
        v-model="renameDraft"
        label="Name"
        autofocus
        :placeholder="renameFallback"
        @enter="saveRename"
      />
      <p class="wfv-hint">
        It is called {{ renameFallback }} now. Leave this empty to keep the name
        PixlStash builds from the workflow itself.
      </p>
      <template #footer>
        <AppButton size="sm" variant="ghost" @click="closeRename"
          >Cancel</AppButton
        >
        <AppButton size="sm" variant="primary" @click="saveRename"
          >Rename</AppButton
        >
      </template>
    </AppDialog>

    <CloneWithModelsDialog
      :open="cloneOpen"
      :workflow-key="cloneKey"
      :card-name="cloneName"
      @close="closeClone"
      @cloned="clonedWithModels"
    />
  </div>
</template>

<script setup>
/**
 * The Workflows grid (v1.12 Workflows & Recipes, F1a) — on `/workflows` since
 * F1b retired the shelf. See `docs/frontend_architecture.md` §5.
 *
 * THE PANEL FOLLOWS THE SELECTION ONCE THE READER IS BROWSING STACKS
 * (`useWorkflowsStore.syncPanelToSelection`) — a click is never the way IN. ▸,
 * Enter and a double-click on the card are still the only ones, because a plain
 * click is a selection and not a request to look inside. Afterwards, selecting
 * another stack moves the band there and selecting anything outside the open
 * stack takes it off the screen — and the MODE outlives the band, so the next
 * stack picked opens with no second double-click. Both transitions animate,
 * which is why the store holds the open key until the panel reports its
 * collapse finished.
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
import { exportWorkflow, workflowCoverUrl } from "../../api/workflows";
import { useConfirm } from "../../composables/useConfirm";
import { useFilterStore } from "../../stores/useFilterStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useWorkflowPullStore } from "../../stores/useWorkflowPullStore";
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
import WorkflowPullSummary from "../panels/WorkflowPullSummary.vue";
import WorkflowSelectionBar from "../panels/WorkflowSelectionBar.vue";
import CloneWithModelsDialog from "../io/CloneWithModelsDialog.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
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
const notices = useNoticeStore();
const runDialog = useRunDialogStore();
const pull = useWorkflowPullStore();
const router = useRouter();
const route = useRoute();
const { confirm } = useConfirm();

const gridEl = ref(null);
// `v-for`'d, so Vue hands back an array even though only one panel is ever
// drawn — one stack is open at a time.
const panelRef = ref(null);
const fileInput = ref(null);
const pullButton = ref(null);
const selBarRef = ref(null);
const scrollEl = ref(null);
const sortMenuOpen = ref(false);
const filterMenuOpen = ref(false);
const renameOpen = ref(false);
const renameDraft = ref("");
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
// The same link, but the card IS in the payload and a filter is hiding it.
const linkFiltered = ref(false);
const watchedFolder = ref(null);

const panelId = "wfv-stack-panel";
const sortOptions = SORT_KEYS.map((key) => ({
  id: key,
  label: SORT_LABELS[key].label,
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

/**
 * Whether the open stack is selected WHOLE — every one of its cards.
 *
 * The panel wears the mark then, and its rows wear none: the stack is the thing
 * that was selected. `every`, not `some`: one member of three is not this
 * stack, and only the rows can say which one it is.
 *
 * Computed from the stack's own key set rather than from the rows on screen, so
 * a member request still in flight — or one that failed — cannot make a fully
 * selected stack read as a partly selected one. Both readers of this also
 * require a stack to be open, so the vacuous `true` an empty key set would give
 * reaches nothing and is not guarded against.
 */
const openStackSelected = computed(() =>
  store
    .stackKeys(store.openStackKey)
    .every((key) => store.selectedKeys.includes(key)),
);

/**
 * Whether the GRID's card for `key` wears the mark.
 *
 * **A cover key names two rows**, the grid's stack card and the panel's first
 * member row, and only while the panel is open is the second of them on
 * screen. From that moment the grid's card stands for the whole stack and
 * nothing less: marking it on the key alone made selecting the top workflow —
 * the one gesture that reaches the cover as an individual — light the stack
 * card up as well, so the reader could not tell "this workflow" from "this
 * stack" and the stack's own row was the one card in the panel they could not
 * pick out.
 *
 * Closed, the card is the cover's only row, so the key is the whole answer;
 * that is also what keeps the `?topology=` deep link, which selects one cover
 * key, visibly landing somewhere.
 */
function cardSelected(key) {
  if (store.openStackKey !== key) return store.selectedKeys.includes(key);
  return openStackSelected.value;
}

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

/**
 * Where the reader was before they opened a picture, claimed once at SETUP.
 *
 * Taken here rather than read later because `unpark` clears it: claiming it
 * synchronously is what stops a second mount — a remount for any other reason
 * — from also thinking it is a return journey. Null on an ordinary arrival,
 * which is every visit that did not come back from a cover click.
 */
const parked = store.unpark();

/**
 * Put the reader back, once the cards it names actually exist.
 *
 * **Not in `onMounted`.** The grid is fetched there and the answer lands a
 * round trip later, so a restore that ran on mount would look for the cursor's
 * card in an empty list and give up; the store's cards can also already BE
 * there from a previous visit, which is why the watcher is `immediate` rather
 * than waiting for a change that may never come.
 *
 * By ID, never by index: `flatRows` is rebuilt by the refetch this mount
 * fires, and an index held across it names a different card or a hole. Once
 * applied it stops, so a later refetch — a file added, a LoRA slot flipped —
 * does not yank the cursor back to where the reader was ten minutes ago.
 */
let placeRestored = false;
watch(
  () => store.sortedCards,
  () => {
    if (!parked || placeRestored || !store.loaded) return;
    placeRestored = true;
    nextTick(() => {
      if (scrollEl.value && parked.scrollTop) {
        scrollEl.value.scrollTop = parked.scrollTop;
      }
      const at = flatRows.value.findIndex(
        (entry) => entry.id === parked.cursorId,
      );
      // Only if the card is still there. A workflow hidden or deleted while
      // the reader was in the lightbox leaves the cursor where it falls back
      // to, which is the first row, rather than being moved to nothing.
      if (at >= 0) moveCursor(at);
    });
  },
  { immediate: true },
);

onMounted(async () => {
  store.fetchCards();
  // A pull started before a reload, or from another tab, is still running on
  // the server: the button should say so rather than offer to start one.
  if (comfyuiConfigured.value) pull.resume();
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
// `GET /workflows` leaves out the hidden ones and the one-offs (under
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
    // **The verdict is read off the payload, not off what is drawn.** With a
    // client-side filter on, `sortedCards` can be missing a card the answer
    // plainly contains, and saying "not in this grid" about it sends the
    // reader looking for a workflow that is one × away. The two are different
    // states with different ways out, so they are said differently.
    if (!card) {
      const filtered = store.cards.some(
        (entry) => entry.topology_hash === wanted,
      );
      if (filtered) {
        announcement.value =
          "That workflow is in this grid, but a filter is hiding it.";
        linkFiltered.value = true;
        linkMissed.value = false;
        return;
      }
      const plural = withheld.value === 1 ? "is" : "are";
      announcement.value = withheld.value
        ? `That workflow is not in the grid. ${withheld.value} ${plural} being left out: ${withheldHidden.value} hidden and ${withheldOneOffs.value} counted as one-offs.`
        : "That workflow is not in the grid.";
      linkMissed.value = true;
      linkFiltered.value = false;
      return;
    }
    honouredTopology = wanted;
    linkMissed.value = false;
    linkFiltered.value = false;
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
  else
    store.select(entry.key, {
      additive: event?.ctrlKey || event?.metaKey,
      whole: entry.kind === "card",
    });
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
  // A card row in the range brings its whole stack; a MEMBER row brings only
  // itself, or a range ending two rows into an open panel would drag the rest
  // of that stack back in — including members the reader had just Ctrl-clicked
  // out. Deduplicated because a cover is in the range twice, as the grid's
  // stack card and as the panel's first member row, and because expanding the
  // card row already named every member the range then meets.
  store.selectRange([
    ...new Set(
      flatRows.value.slice(start, end + 1).flatMap((entry) => {
        if (entry.kind === "card") return store.stackKeys(entry.key);
        return entry.kind === "member" ? [entry.key] : [];
      }),
    ),
  ]);
  // A Shift range can take in a screenful without the cursor passing over
  // most of it, so the count is said the way Ctrl+A's is.
  announceSelection();
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
  store.collapseStack();
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
    // **The panel can close from under the cursor.** Ctrl-clicking a member
    // while a card outside the stack is already selected reaches outside the
    // open stack, which closes it — and the member row the cursor was standing
    // on goes with it, taking the grid's only tab stop and the focus off the
    // screen. `closePanel` has already put the cursor back when Esc or Close
    // did it; this only catches a cursor left naming a row that is gone.
    if (flatRows.value.some((entry) => entry.id === cursorId.value)) return;
    const at = flatRows.value.findIndex(
      (entry) => entry.kind === "card" && entry.key === previous,
    );
    if (at >= 0) moveCursor(at);
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
  // Select all shown, the chord the pill's count menu prints a keycap for.
  // Checked before the switch because it is the only binding here that takes a
  // modifier, and a keycap naming a key nothing listens for is a lie the
  // reader can only discover by pressing it.
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
    event.preventDefault();
    selectAllShown();
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
        store.select(entry.key, {
          additive: true,
          whole: entry.kind === "card",
        });
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
    case "F2":
      // The rename key, everywhere a list has one. Single-selection only,
      // which is the gate the bar's button carries too.
      event.preventDefault();
      // `entry &&`, not `entry?.kind !== "hole"`: an absent entry passes that
      // test (undefined is not "hole") and the next line reads `.key` off it.
      if (entry && entry.kind !== "hole") {
        if (!store.selectedKeys.includes(entry.key)) {
          store.select(entry.key, { whole: entry.kind === "card" });
        }
        startRename();
      }
      return;
    case "F10":
      if (!event.shiftKey) return;
      event.preventDefault();
      // The context-menu keys reach the MEMBER MENU on a member row — the
      // same menu right-click opens, and the only route to *Make it the
      // cover*, *Move* and *Unstack* for somebody not using a pointer. On a
      // CARD they now reach the card menu, which is what those keys mean
      // everywhere else in this app and in every file manager; they used to
      // open ⓘ, which Enter already does and still does (#1455).
      if (entry?.kind === "member") openMemberMenu(entry.key);
      else openMenuAtCursor();
      return;
    case "ContextMenu":
      event.preventDefault();
      if (entry?.kind === "member") openMemberMenu(entry.key);
      else openMenuAtCursor();
      return;
    case "Escape":
      // Innermost first. The popover and the card menu are `VMenu`s: they
      // consume their own Escape and it never reaches here, so what is left is
      // the panel, then the selection.
      if (store.openStackKey && !store.panelClosing) {
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

// ── The selection's verbs (#1455) ─────────────────────────────────────────
//
// The bar owns the gates and emits; this owns the confirmation, the dialog and
// the store call — `ModelShelf.vue` splits the shelf's the same way, and for
// the same reason: two confirmations half here and half there is how they
// drift apart.

/** The selected card, when exactly one is. Null otherwise. */
const onlyCard = computed(() =>
  store.selectedCards.length === 1 ? store.selectedCards[0] : null,
);

/**
 * Open a picture one of a card's covers draws, in the lightbox.
 *
 * The lightbox is mounted inside `ImageGrid`, which this screen REPLACES, so
 * the only way to it is the picture-grid route — and `?from=` is what makes
 * closing it come back here instead of leaving the reader on All Pictures
 * (#1446). This is the caller that mechanism has been waiting for.
 *
 * `route.path` and not the full route: `?topology=` is a one-shot instruction
 * from a Recipe link that this view honours on mount, so carrying it back
 * would re-select whichever workflow that link named over whichever one the
 * reader has since chosen.
 */
function openPicture(pictureId) {
  // Where the reader is, so the return lands here rather than at the top.
  // The store holds it because this component is remounted on the way back
  // (`App.vue` has no `<KeepAlive>` for this view), so nothing local survives.
  store.park({
    cursorId: cursorId.value,
    scrollTop: scrollEl.value?.scrollTop ?? 0,
  });
  router.push({
    name: "all-pictures",
    query: { overlay: String(pictureId), from: route.path },
  });
}

/**
 * Run one card: the Run popup, on the card rather than on a picture (#1407).
 *
 * The popup is `App.vue`'s and the store is how anything reaches it. No
 * picture behind it, so it shows the card's cover, an empty prompt and a set
 * picker for where the output is filed — the one thing a card-sourced run has
 * to be told and a picture-sourced one already knows.
 */
function runCard(card) {
  if (!card) return;
  runDialog.openRun({
    kind: "card",
    workflowKey: card.key,
    name: card.name,
    // Through the helper: a raw `covers` entry is API-relative and an
    // `<img src>` would resolve it against the page origin instead.
    coverUrl: card.covers?.[0] ? workflowCoverUrl(card.covers[0]) : "",
    emptyPrompt: true,
  });
}

function runSelected() {
  runCard(store.runnableCard);
}

async function stackSelected() {
  const count = store.selectedKeys.length;
  if (await store.stackSelected()) {
    announcement.value = `${count} workflows stacked together`;
  }
}

/**
 * Break up every stack the selection touches.
 *
 * **Confirmed above one**, unlike its neighbour Stack. It is bulk, it is not
 * undoable on a screen with no undo, and what it discards is not the cards —
 * those stay — but the member ORDER and the cover choice somebody arranged by
 * hand. Its glyph is a near-twin of Stack's one button away, so the press is
 * easy to make by accident; one stack is a gesture a reader can see the
 * result of, and six is not.
 */
async function unstackSelected() {
  const stacks = store.selectedStackIds.length;
  if (stacks > 1) {
    const ok = await confirm({
      title: `Break up ${stacks} stacks?`,
      message:
        `Every workflow in ${stacks} stacks goes back to standing on its own. ` +
        "The workflows and their pictures stay; what is lost is the order " +
        "they were in and which one stood for each stack. There is no undo " +
        "on this screen.",
      confirmLabel: "Break them up",
    });
    if (!ok) return;
  }
  const { refused } = await store.unstackSelected();
  if (refused) {
    notices.push({
      level: "error",
      text:
        refused === stacks
          ? stacks === 1
            ? "That stack could not be broken up."
            : "None of those stacks could be broken up."
          : `${stacks - refused} of ${stacks} stacks were broken up; the rest could not be.`,
    });
    return;
  }
  announcement.value =
    stacks === 1 ? "Stack broken up" : `${stacks} stacks broken up`;
}

/**
 * Hide, or unhide, the selection.
 *
 * `unhide` is the bar's reading of the selection, handed down rather than
 * recomputed here: the button the reader pressed said which of the two it was
 * about to do, and a second derivation could disagree with the label they
 * just read.
 */
async function hideSelected(unhide) {
  const count = store.selectedKeys.length;
  const { refused } = await store.hideSelected(!unhide);
  const verb = unhide ? "unhidden" : "hidden";
  if (refused) {
    notices.push({
      level: "error",
      text:
        refused === count
          ? `None of those workflows could be ${verb}.`
          : `${count - refused} of ${count} workflows were ${verb}; the rest could not be.`,
    });
    return;
  }
  announcement.value =
    count === 1 ? `Workflow ${verb}` : `${count} workflows ${verb}`;
}

/** *Make it the cover*, for a member picked inside the open stack's panel. */
function makeCoverOfSelected() {
  const key = store.selectedKeys[0];
  if (key) followMove(key, store.makeCover(key));
}

// ── Rename ────────────────────────────────────────────────────────────────

/**
 * What the card is called now, shown as the field's PLACEHOLDER.
 *
 * **The field opens empty, and that is deliberate.** `name` is never null —
 * the server falls back to the workflow file, then to a description built from
 * what the card loads and does — and the payload does not say WHICH of the
 * three it gave, so a field seeded from it would turn a generated name into a
 * typed one the moment somebody opened Rename and pressed the button without
 * changing anything. The card would stop following the workflow from then on,
 * invisibly.
 *
 * So empty means "no name of my own", which is what an empty field looks like
 * everywhere, and saving it clears the stored name back to the generated one.
 * The cost is that editing a name you already typed means typing it again;
 * the placeholder and the line under the field are what make that legible.
 */
const renameFallback = computed(() => onlyCard.value?.name ?? "");

/**
 * Put focus back on the roving cursor's row.
 *
 * Every surface this view opens is TELEPORTED — the context menu to a pair of
 * coordinates, the rename dialog to the end of `<body>` — so none of them has
 * an activator the browser can restore focus to on close, and it lands on
 * `document.body`: outside the grid, with the cursor's row still marked and
 * the next arrow key going nowhere (WCAG 2.4.3). `followMove` proved the
 * pattern for the one path that already had it.
 */
function focusCursorRow() {
  const entry = flatRows.value[cursorIndex.value];
  if (!entry || entry.kind === "hole") return;
  nextTick(() => rowElement(entry)?.focus());
}

/**
 * Open whichever picture the menu's open row named.
 *
 * The bar decides which that is — the tile the right-click landed on, else
 * the card's cover — so this takes the answer rather than recomputing it,
 * which is what keeps the row's label and what it opens the same thing.
 */
// The Recipes tab's thumbnails, which live outside this view (see the store).
watch(
  () => store.pictureToOpen,
  (id) => {
    if (id == null) return;
    store.pictureToOpen = null;
    openPicture(id);
  },
);

function openCoverPicture() {
  const id = selBarRef.value?.openTarget?.id;
  if (id != null) openPicture(id);
}

function closeRename() {
  renameOpen.value = false;
  focusCursorRow();
}

function startRename() {
  const card = onlyCard.value;
  if (!card) return;
  // The card's `name` is never null — the server falls back to the file, then
  // to a built description — so the field cannot be seeded from it without
  // turning a generated name into a typed one on the first Rename that is
  // cancelled halfway. It opens EMPTY, with that name as the placeholder.
  renameDraft.value = "";
  renameOpen.value = true;
}

async function saveRename() {
  const card = onlyCard.value;
  closeRename();
  if (!card) return;
  const name = renameDraft.value.trim();
  if (await store.renameCard(card.key, name)) {
    // The cleared case says what the card is called NOW rather than "cleared":
    // the server re-derives a name immediately, so "cleared" would describe a
    // state the grid never shows. It is read back off the re-fetched card.
    const renamed = store.cards.find((entry) => entry.key === card.key);
    announcement.value = name
      ? `Renamed to ${name}`
      : `Name cleared, now ${renamed?.name ?? "unnamed"}`;
  }
}

// ── Export, duplicate, delete ─────────────────────────────────────────────

/**
 * Save this card as a ComfyUI file somebody else can open.
 *
 * The scrub is the SERVER's (`GET /workflows/{key}/export`): prompts and seeds
 * blanked, recipe LoRA slots emptied, model names this machine does not hold
 * left out. What comes back names what it took out, and the notice says so —
 * a file that quietly differs from the workflow it was exported from is worse
 * than one that says which parts did not travel.
 */
async function exportSelected() {
  const card = onlyCard.value;
  if (!card) return;
  let body;
  try {
    body = await exportWorkflow(card.key);
  } catch (err) {
    console.warn(`[workflows] could not export ${card.key}`, err);
    notices.push({
      level: "error",
      text: errorMessage(err, "That workflow could not be exported."),
    });
    return;
  }
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(body.workflow, null, 2)], {
      type: "application/json",
    }),
  );
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = body.filename || "workflow.json";
    link.click();
  } finally {
    // Revoked on the next frame, not immediately: the click is queued and a
    // URL revoked in the same task can be gone before the download starts.
    requestAnimationFrame(() => URL.revokeObjectURL(url));
  }
  notices.push({
    level: "success",
    text: body.removed?.length
      ? `Exported ${body.filename}, without: ${body.removed.join(", ")}.`
      : `Exported ${body.filename}.`,
  });
}

async function duplicateSelected() {
  const card = onlyCard.value;
  if (!card) return;
  const body = await store.duplicateCard(card.key);
  if (!body) return;
  notices.push({ level: "success", text: `Copied to ${body.name}.` });
}

// Clone with new models: one dialog, one new card. Held by key rather than by
// the selection, so the dialog keeps its card if the selection moves under it.
const cloneOpen = ref(false);
const cloneKey = ref("");
const cloneName = ref("");

function startCloneWithModels() {
  const card = onlyCard.value;
  if (!card) return;
  cloneKey.value = card.key;
  cloneName.value = card.name || "";
  cloneOpen.value = true;
}

function closeClone() {
  cloneOpen.value = false;
  focusCursorRow();
}

/** Name the file written, say when ComfyUI could not check it, select the card. */
function clonedWithModels(body) {
  notices.push(
    body.verified
      ? { level: "success", text: `Cloned to ${body.name}.` }
      : {
          level: "warning",
          text: `Cloned to ${body.name}. ComfyUI did not confirm every new model name, so run it once to check.`,
        },
  );
  if (body.workflow_key) store.select(body.workflow_key);
}

/**
 * Delete the selected cards' workflow FILES, after saying so in as many words.
 *
 * The one verb here that touches bytes, so the one that asks first. It is not
 * a "permanent" delete — the file goes to the system trash — and the card and
 * its pictures stay, which the question says, because "delete this workflow"
 * reads like losing the pictures it made.
 */
async function confirmDelete() {
  const cards = [...store.selectedCards];
  if (!cards.length) return;
  const what =
    cards.length === 1
      ? `“${cards[0].name}”`
      : `${cards.length} workflow files`;
  const ok = await confirm({
    title:
      cards.length === 1
        ? "Delete this workflow file?"
        : "Delete these workflow files?",
    message:
      `The file for ${what} goes to your system trash. The card stays in ` +
      "this grid and so do the pictures it made — what is lost is the ability " +
      "to run it from a file on this machine.",
    confirmLabel: "Delete",
    danger: true,
  });
  if (!ok) return;
  const { refused } = await store.deleteSelected();
  if (refused) {
    notices.push({
      level: "error",
      text:
        refused === cards.length
          ? "None of those workflow files could be deleted."
          : `${cards.length - refused} of ${cards.length} files were deleted; the rest could not be.`,
    });
    return;
  }
  announcement.value =
    cards.length === 1
      ? "Workflow file deleted"
      : `${cards.length} workflow files deleted`;
}

// ── The card menu ─────────────────────────────────────────────────────────

/**
 * Right-click a card: the full verb inventory, at the pointer.
 *
 * The file-manager rule, which is the shelf's and the picture grid's: right-
 * clicking a card that is NOT selected selects it and acts on it alone;
 * right-clicking one that IS leaves the selection alone, so a menu opened on
 * any of forty selected cards acts on all forty. Without that, the commonest
 * gesture in a bulk edit — select, then right-click one of them — would
 * silently drop the other thirty-nine.
 *
 * A MEMBER row inside an open stack panel is not reached here: it carries its
 * own `@contextmenu` and `StackPanel`'s member menu (#1405), which holds the
 * verbs that are only about a row inside a run.
 */
function openRowMenu(index, event) {
  const entry = flatRows.value[index];
  if (!entry || entry.kind === "hole") return;
  cursorId.value = entry.id;
  if (!store.selectedKeys.includes(entry.key)) {
    store.select(entry.key, { whole: entry.kind === "card" });
  }
  selBarRef.value?.openContextMenu(
    event.clientX,
    event.clientY,
    pictureUnder(event),
  );
}

/**
 * The cover tile a pointer event landed on, or null.
 *
 * Read off the EVENT's own target rather than tracked by the card: a
 * right-click on a card's third thumbnail and one on its name row are the same
 * `contextmenu` on the same row, and the only thing that tells them apart is
 * where the pointer was. The tile carries its own id and position
 * (`WorkflowCard.vue`), so this is a lookup rather than a second piece of
 * state that can go stale.
 *
 * `closest`, not the target itself: the press lands on the `<img>` inside the
 * cell rather than on the cell.
 */
function pictureUnder(event) {
  const tile = event.target?.closest?.(".wf-card__pic");
  const id = Number(tile?.dataset.pictureId);
  if (!tile || !Number.isFinite(id)) return null;
  return {
    id,
    index: Number(tile.dataset.pictureIndex) || 1,
    total: Number(tile.dataset.pictureTotal) || 1,
  };
}

/**
 * Open the card menu over the cursor row's own box, for the keyboard's sake.
 *
 * **Nothing to point at is nothing to open.** On a padding hole, or with the
 * cursor out of range of a list that has just shrunk, there is no card the
 * menu would act on — it opened all-disabled at viewport (0, 0), which is a
 * menu about nothing in the corner of the screen.
 */
function openMenuAtCursor() {
  const entry = flatRows.value[cursorIndex.value];
  if (!entry || entry.kind === "hole") return;
  if (!store.selectedKeys.includes(entry.key)) {
    store.select(entry.key, { whole: entry.kind === "card" });
  }
  const box = rowElement(entry)?.getBoundingClientRect();
  selBarRef.value?.openContextMenu(
    box ? box.left + 24 : 0,
    box ? box.bottom : 0,
  );
}

/**
 * *Select all shown*, from the pill's count menu and from Ctrl/Cmd+A.
 *
 * Whole stacks: a stack card on screen stands for its members, so "all shown"
 * means the cards the grid is drawing, expanded the way clicking each of them
 * would expand them.
 */
function selectAllShown() {
  store.selectRange([
    ...new Set(store.sortedCards.flatMap((card) => store.stackKeys(card.key))),
  ]);
  // **Said aloud, because nothing else says it.** Ctrl/Cmd+A can select the
  // whole grid without moving the cursor or changing a single visible row a
  // screen reader is on, and the pill is a `role="toolbar"` rather than a live
  // region — so the selection it counts changed in silence.
  announceSelection();
}

/** How many cards are selected, for the live region. */
function announceSelection() {
  const n = store.selectedKeys.length;
  announcement.value = n === 1 ? "1 workflow selected" : `${n} workflows selected`;
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
  /* The positioned ancestor `.selbar-float` is absolute against: only the
     container knows where its list ends, which is why the shared float rule
     is absolute rather than fixed (`App.css`). */
  position: relative;
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
/* The rename dialog's helper line. Deliberately NOT `.wfv-note`: that class is
   a band the GRID shows when it has something to say, and this is body copy
   inside a dialog. */
.wfv-hint {
  margin: 0;
  padding-top: var(--space-3);
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: var(--text-sm);
}

.wfv-note-clear {
  /* The inline-button reset again — without `font: inherit` this one also
     loses `.wfv-note`'s `--text-sm` to the UA's button size. */
  padding: 0;
  font: inherit;
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
