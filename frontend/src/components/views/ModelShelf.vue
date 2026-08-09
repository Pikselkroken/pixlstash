<template>
  <!-- role="region" because a bare div is role `generic`, which prohibits an
       accessible name: without it the aria-label is dropped and #shelf-help is
       never announced, so the whole paragraph below is dead weight. -->
  <div
    ref="rootEl"
    class="shelf"
    role="region"
    tabindex="-1"
    aria-label="Model shelf"
    aria-describedby="shelf-help"
  >
    <p id="shelf-help" class="visually-hidden">
      Every adapter and checkpoint PixlStash has found on this machine. Show
      chooses which kinds are listed and which base models. A name in a
      monospaced face was taken from the filename, because nobody has named
      that file yet.
    </p>

    <div class="shelf-toolbar">
      <span class="shelf-title">Models</span>
      <span class="shelf-sub">{{ countLabel }}</span>
      <span class="shelf-spacer"></span>
      <v-menu
        v-model="showMenuOpen"
        :close-on-content-click="false"
        location="bottom end"
        origin="top end"
        :offset="8"
        transition="scale-transition"
      >
        <!-- The boxed bar button, its badge and the panel shell are the
             toolbar's shipped filter pattern; v-menu is also what returns
             focus to this button on Escape and on an outside click, so none
             of that is hand-rolled. -->
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            class="bar-btn bar-btn--boxed"
            :class="{
              'bar-btn--active': store.activeCount > 0 && !showMenuOpen,
              'bar-btn--open': showMenuOpen,
            }"
            type="button"
            title="Show"
          >
            <span class="bar-icon-badge-wrap">
              <v-icon size="19">mdi-eye-outline</v-icon>
              <span v-if="store.activeCount > 0" class="bar-filter-badge">{{
                store.activeCount
              }}</span>
            </span>
            <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
          </button>
        </template>
        <ShelfShowPanel />
      </v-menu>
    </div>

    <div class="shelf-body">
      <p v-if="store.loading" class="shelf-state">Reading the shelf…</p>
      <p v-else-if="store.error" class="shelf-state" role="alert">
        {{ store.error }}
      </p>
      <!-- Three empty states, deliberately distinct. Conflating "you filtered
           everything out" with "there is nothing here" is the failure: the
           first is one click from fixed and the second is not, so only the
           first two offer Reset. -->
      <div v-else-if="store.nothingSelected" class="shelf-state">
        <p>Nothing is selected in Show.</p>
        <button class="tbm-action" type="button" @click="store.resetFilters()">
          Reset filters
        </button>
      </div>
      <div v-else-if="!store.rows.length" class="shelf-state">
        <p>No models found.</p>
        <p>
          PixlStash lists what it finds in the model folders registered on this
          machine. Register one to fill the shelf.
        </p>
      </div>
      <div v-else-if="!store.visibleRows.length" class="shelf-state">
        <p>No models match these filters.</p>
        <button class="tbm-action" type="button" @click="store.resetFilters()">
          Reset filters
        </button>
      </div>

      <!-- Rows are not focus stops: they carry no verb and no selection, so
           1,800 empty tab stops would be a trap. Roving focus arrives with the
           first thing a focused row can do. -->
      <ul v-else class="shelf-list" role="list">
        <li
          v-for="row in store.visibleRows"
          :key="row.id"
          class="ps-row shelf-row"
          :title="rowTitle(row)"
        >
          <span class="ps-row-glyph ps-row-glyph--empty"></span>
          <span class="shelf-row-kind">
            <v-icon size="16">{{ KIND_ICON[row.file_kind] }}</v-icon>
          </span>
          <span class="shelf-row-label">
            <span
              class="shelf-row-name"
              :class="{ 'shelf-row-name--derived': row.name.derived }"
              >{{ row.name.text
              }}<span v-if="row.name.derived" class="visually-hidden">
                (name taken from the filename)</span
              ></span
            >
            <span class="shelf-row-meta">
              <span>{{ kindLabel(row) }}</span>
              <span>{{ row.base_model || "Base model not set" }}</span>
              <span v-if="row.file_size" class="shelf-row-size">{{
                formatModelSize(row.file_size)
              }}</span>
            </span>
          </span>
          <span
            class="shelf-row-loc"
            :class="`shelf-row-loc--${row.locState}`"
            :title="LOC_TITLE[row.locState]"
          >
            <v-icon size="16">{{ LOC_ICON[row.locState] }}</v-icon>
          </span>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import ShelfShowPanel from "../panels/ShelfShowPanel.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { formatModelSize } from "../../utils/modelShelf";

const store = useModelShelfStore();
const rootEl = ref(null);
const showMenuOpen = ref(false);

// A closed vocabulary gets a glyph, an open one a word. `unknown` gets a plain
// file rather than a question mark (an unclassified file is a fact about our
// parser, not the user's mistake) and never the checkpoint cube.
const KIND_ICON = {
  adapter: "mdi-layers-outline",
  checkpoint: "mdi-cube-outline",
  unknown: "mdi-file-outline",
};

// `missing` is a fact (the folder was readable, the file was not in it);
// `unreachable` is the absence of one (we could not look). Only the fact wears
// a status colour — claiming a hue for "we do not know" would assert knowledge
// we do not have. `present` reserves its slot and shows nothing.
const LOC_ICON = {
  present: "mdi-check",
  missing: "mdi-file-remove-outline",
  unreachable: "mdi-help-circle-outline",
  forgotten: "mdi-folder-off-outline",
};

const LOC_TITLE = {
  present: "",
  missing: "The file is not where it was",
  unreachable: "Could not check this location",
  forgotten: "Every registered copy has been forgotten",
};

// Trainers spell these however they like; the shelf spells them one way.
const ALGO_LABEL = {
  lora: "LoRA",
  lokr: "LoKr",
  loha: "LoHa",
  dora: "DoRA",
  oft: "OFT",
};

const countLabel = computed(() => {
  const n = store.visibleRows.length;
  return `${n.toLocaleString()} ${n === 1 ? "model" : "models"}`;
});

/** The always-present anchor of the metadata line, whatever else is null. */
function kindLabel(row) {
  if (row.file_kind === "checkpoint") return "Checkpoint";
  if (row.file_kind === "unknown") return "Unclassified";
  const kind = String(row.kind || "").toLowerCase();
  return ALGO_LABEL[kind] || kind || "Adapter";
}

/** Filename and folder live in the tooltip; the row shows the name. */
function rowTitle(row) {
  const where = (row.locations || [])
    .map((loc) => `${loc.folder_path}/${loc.relpath}`)
    .join("\n");
  return [row.filename, where].filter(Boolean).join("\n");
}

onMounted(() => {
  // Tab out of the sidebar lands in the shelf, the same contract the duplicate
  // queue has. Synchronously, like DuplicateQueue: taking focus one round trip
  // after mount would discard wherever the user had moved in the meantime.
  rootEl.value?.focus();
  store.fetchRows();
});

// A credential change (logout, login, share token, restore) empties the store,
// and an empty shelf reads as "this machine has no models". Refetching rather
// than gating the empty state on `loaded`: the view is still on screen and its
// job is to show the shelf, so a blank body would be a second wrong answer.
// The store cannot do this itself: session-reset handlers run BEFORE the new
// credential is installed, whereas this pre-flush watcher runs after.
watch(
  () => store.loaded,
  (isLoaded) => {
    if (!isLoaded) store.fetchRows();
  },
);
</script>

<style scoped>
.shelf {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  outline: none;
}

.shelf-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: var(--bar-height);
  padding: 0 var(--space-5);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  flex-shrink: 0;
}

.shelf-title {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.shelf-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
  font-variant-numeric: tabular-nums;
}

.shelf-spacer {
  flex: 1 1 auto;
}

.shelf-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.shelf-state {
  padding: var(--space-7) var(--space-5);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.7);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-4);
  max-width: 60ch;
}

.shelf-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

/* The box, the rail and the indent come from the shared row system
   (SideBar.global.css, visual-language.md §5.1) via `.ps-row`; only the
   columns and the vertical rhythm are the shelf's own. Column 1 stays
   reserved and empty: grouping fills it, and a column that appears later
   would move every label sideways. */
.shelf-row {
  display: grid;
  grid-template-columns:
    var(--gutter-glyph)
    var(--entity-thumb)
    minmax(0, 1fr)
    auto;
  align-items: center;
  gap: var(--space-2);
  padding-top: var(--space-2);
  padding-bottom: var(--space-2);
  transition: background var(--dur-1) var(--ease-standard);
  /* Native windowing: the browser skips layout and paint for rows outside the
     viewport, which is what 1,800 rows need and is two lines rather than a
     virtual scroller. The size hint is only the first guess — `auto` makes the
     browser remember each row's real height after it has painted once. */
  content-visibility: auto;
  contain-intrinsic-size: auto calc(var(--entity-thumb) + var(--space-5));
}

.shelf-row:hover {
  background: var(--hover-wash);
}

.shelf-row-kind {
  display: inline-flex;
  justify-content: center;
  color: rgba(var(--v-theme-on-background), 0.7);
}

.shelf-row-label {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.shelf-row-name {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The unnamed third. Mono at regular weight, at FULL strength: §3 gives the
   mono face to file paths, and a filename-derived name is one — so this says
   what the string is rather than demoting it. Rank is never opacity (§5.1),
   and 37% of rows faded would be a column of ghosts. */
.shelf-row-name--derived {
  font-family: var(--font-mono);
  font-weight: var(--weight-regular);
}

.shelf-row-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
  /* 0.7, not 0.6: at 12px the lower alpha measures 4.07:1 on the light canvas
     and misses the 4.5:1 floor. */
  color: rgba(var(--v-theme-on-background), 0.7);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shelf-row-size {
  font-variant-numeric: tabular-nums;
}

.shelf-row-loc {
  display: inline-flex;
  width: var(--gutter-glyph);
  margin-left: var(--space-3);
}

.shelf-row-loc--present {
  visibility: hidden;
}

.shelf-row-loc--missing,
.shelf-row-loc--forgotten {
  color: rgb(var(--v-theme-error));
}

.shelf-row-loc--unreachable {
  color: rgba(var(--v-theme-on-background), 0.7);
}
</style>
