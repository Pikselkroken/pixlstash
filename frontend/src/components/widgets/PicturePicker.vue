<template>
  <AppDialog
    :open="open"
    title="Choose a picture"
    :subtitle="subtitle"
    :width="820"
    :pad-body="false"
    @close="emit('close')"
    @accept="use"
  >
    <div class="pp">
      <!-- The facets are the groupings the vault ALREADY stores. Nothing new is
           invented to describe a picture: the brand mark lives in a project,
           the face lives on a character, the style plates live in a set. Free
           search below is the escape hatch, not the primary route. -->
      <nav class="pp-rail" aria-label="Narrow the pictures">
        <div class="pp-sec">Project</div>
        <button
          type="button"
          class="pp-facet"
          :class="{ 'pp-facet--on': !facet.kind }"
          :aria-pressed="!facet.kind"
          @click="choose('', null)"
        >
          <span class="pp-facet__label">Everything</span>
          <span v-if="totalCount != null" class="pp-facet__count">{{
            groupedNumber(totalCount)
          }}</span>
        </button>
        <button
          v-for="p in projectFacets"
          :key="`project-${p.id}`"
          type="button"
          class="pp-facet"
          :class="{ 'pp-facet--on': isOn('project', p.id) }"
          :aria-pressed="isOn('project', p.id)"
          @click="choose('project', p.id)"
        >
          <span class="pp-facet__label">{{ p.name }}</span>
          <span class="pp-facet__count">{{ groupedNumber(p.count) }}</span>
        </button>

        <template v-for="group in ['character', 'set']" :key="group">
          <div class="pp-sec">
            {{ group === "character" ? "Character" : "Picture set" }}
          </div>
          <button
            v-for="row in shown(group)"
            :key="`${group}-${row.id}`"
            type="button"
            class="pp-facet"
            :class="{ 'pp-facet--on': isOn(group, row.id) }"
            :aria-pressed="isOn(group, row.id)"
            @click="choose(group, row.id)"
          >
            <span class="pp-facet__label">{{ row.name }}</span>
            <span class="pp-facet__count">{{ groupedNumber(row.count) }}</span>
          </button>
          <button
            v-if="facets[group].length > shown(group).length"
            type="button"
            class="pp-more"
            @click="expanded[group] = true"
          >
            All {{ facets[group].length }} &rsaquo;
          </button>
        </template>
      </nav>

      <div class="pp-main">
        <div class="pp-head">
          <AppInput
            v-model="search"
            class="pp-search"
            icon="magnify"
            placeholder="Search this library"
            @enter="reload"
          />
          <!-- Paste is a real route in, and it IMPORTS: a screenshot that was
               never imported cannot be chosen, because everything downstream of
               this picker names a picture by its id. The app's window-level
               paste handler does the import; this says so at the moment of
               pasting rather than surprising someone later with a picture they
               did not know they filed, and reloads the list when it lands. -->
          <span class="pp-paste">
            or press <kbd>Ctrl</kbd><kbd>V</kbd>
          </span>
        </div>

        <div class="pp-scroll">
          <p v-if="error" class="pp-note pp-note--error">{{ error }}</p>
          <p v-else-if="loading && !pictures.length" class="pp-note">
            Loading pictures…
          </p>
          <p v-else-if="!pictures.length" class="pp-note">
            No pictures here. Try another grouping, search, or paste one in.
          </p>
          <div v-else class="pp-grid">
            <button
              v-for="pic in pictures"
              :key="pic.id"
              type="button"
              class="pp-cell"
              :class="{ 'pp-cell--on': chosen?.id === pic.id }"
              :aria-pressed="chosen?.id === pic.id"
              :title="tileName(pic)"
              @click="chosen = pic"
              @dblclick="use"
            >
              <img
                :src="thumbUrl(pic)"
                alt=""
                loading="lazy"
                decoding="async"
              />
            </button>
          </div>
          <div v-if="pictures.length && !done" class="pp-more-row">
            <AppButton
              variant="secondary"
              size="sm"
              :loading="loading"
              @click="loadMore"
              >Show more</AppButton
            >
          </div>
        </div>

        <div class="pp-foot">
          <span class="pp-chosen">{{ chosen ? "1 chosen" : "None chosen" }}</span>
          <span class="pp-spacer"></span>
          <!-- Where a caller puts the route this picker deliberately does not
               replace. The model shelf keeps "Choose a file…" here: removing a
               shipped way of doing the job is a regression, and the point of
               this step is to prove the picker without taking anything away. -->
          <slot name="footer-start" />
          <AppButton variant="secondary" key-hint="esc" @click="emit('close')"
            >Cancel</AppButton
          >
          <AppButton
            variant="primary"
            key-hint="enter"
            :disabled="!chosen"
            @click="use"
            >Use this picture</AppButton
          >
        </div>
      </div>
    </div>
  </AppDialog>
</template>

<script setup>
// One picker, faceted by project, character and picture set, single-select.
//
// SINGLE-SELECT ON PURPOSE. Every caller needs exactly one picture — a model's
// thumbnail, a workflow's fixed input, a run-time answer — and multi-select
// would be built on the argument that something might want it one day.
//
// Design: the `Picker` artboard of the 1.11 Workflow Library canvas. Facet rail
// left, search + grid right, receipt + verbs in the footer.

import { computed, onUnmounted, reactive, ref, watch } from "vue";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";
import AppInput from "./AppInput.vue";
import {
  getPictureCount,
  pictureThumbnailUrl,
  searchPictures,
  streamPictures,
} from "../../api/pictures";
import { isSupportedImportFile } from "../../utils/media.js";
import { errorDetail } from "../../utils/apiError";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTasksStore } from "../../stores/useTasksStore";

const props = defineProps({
  open: { type: Boolean, default: false },
  // What the picture is FOR, said in the caller's own words ("for Reference",
  // "for Flux Realism"). The title never changes; this does.
  subtitle: { type: String, default: "" },
});

const emit = defineEmits(["close", "pick"]);

// One batch is a screenful several times over. The rail and the search are how
// a 28k-picture library is narrowed; paging is the honest fallback, never the
// route, which is why `Show more` is a button rather than an infinite scroll.
const BATCH = 120;
// How many rows of a facet group are shown before `All N ›`. Three is the
// artboard's count and is enough to show what the group is.
const FACET_PREVIEW = 3;

const entityLists = useEntityListsStore();
const notices = useNoticeStore();
const tasks = useTasksStore();

const facet = reactive({ kind: "", id: null });
const expanded = reactive({ character: false, set: false });
const search = ref("");
const pictures = ref([]);
const chosen = ref(null);
const loading = ref(false);
const error = ref("");
const done = ref(true);
const nextOffset = ref(0);
const totalCount = ref(null);

// A request that was in flight when the facet changed must not overwrite the
// list the reader is now looking at.
let loadSeq = 0;

const facets = computed(() => ({
  character: entityLists.characters
    .map((c) => ({ id: c.id, name: c.name, count: c.image_count ?? 0 }))
    .sort((a, b) => b.count - a.count),
  set: entityLists.pictureSets
    .filter((s) => !s.reference_character)
    .map((s) => ({ id: s.id, name: s.name, count: s.picture_count ?? 0 }))
    .sort((a, b) => b.count - a.count),
}));

const projectFacets = computed(() =>
  entityLists.projects
    .map((p) => ({ id: p.id, name: p.name, count: p.image_count ?? 0 }))
    .sort((a, b) => b.count - a.count),
);

function shown(group) {
  const rows = facets.value[group];
  return expanded[group] ? rows : rows.slice(0, FACET_PREVIEW);
}

function isOn(kind, id) {
  return facet.kind === kind && facet.id === id;
}

/** Thin space between thousands, as the counts are drawn on the artboard. */
function groupedNumber(n) {
  return Number(n || 0).toLocaleString("en-GB").replace(/,/g, " ");
}

function thumbUrl(pic) {
  return pictureThumbnailUrl(pic.id);
}

/** What to call a tile in its tooltip: the file's own name, never its path. */
function tileName(pic) {
  const path = pic.file_path || "";
  const name = path.split(/[\\/]/).pop();
  return name || `Picture ${pic.id}`;
}

function choose(kind, id) {
  facet.kind = kind;
  facet.id = id;
  reload();
}

/** The facet as listing query params, shared by the stream and the search. */
function scopeParams() {
  const params = new URLSearchParams();
  if (facet.kind === "project") params.set("project_id", String(facet.id));
  if (facet.kind === "character") params.set("character_id", String(facet.id));
  if (facet.kind === "set") params.set("set_id", String(facet.id));
  return params;
}

async function load({ append = false } = {}) {
  const seq = ++loadSeq;
  loading.value = true;
  error.value = "";
  try {
    const text = search.value.trim();
    if (text) {
      // Search answers in one shot, so there is nothing to page through.
      const rows = await searchPictures(text, { query: scopeParams().toString() });
      if (seq !== loadSeq) return;
      pictures.value = Array.isArray(rows) ? rows : [];
      done.value = true;
      nextOffset.value = 0;
      return;
    }
    const params = scopeParams();
    // `grid` rather than `grid_lite`: the lite projection drops `file_path`,
    // and the file's own name is the only thing on a tile that tells two
    // near-identical thumbnails apart.
    params.set("fields", "grid");
    params.set("sort", "DATE");
    params.set("descending", "true");
    const batch = await streamPictures(params.toString(), {
      offset: append ? nextOffset.value : 0,
      batchLimit: BATCH,
    });
    if (seq !== loadSeq) return;
    const rows = Array.isArray(batch?.pictures) ? batch.pictures : [];
    pictures.value = append ? [...pictures.value, ...rows] : rows;
    done.value = Boolean(batch?.done);
    nextOffset.value = Number(batch?.next_offset) || 0;
  } catch (err) {
    if (seq !== loadSeq) return;
    error.value = errorDetail(err) || "Could not read the library.";
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

function reload() {
  chosen.value = null;
  load();
}

function loadMore() {
  load({ append: true });
}

function use() {
  if (!chosen.value) return;
  emit("pick", chosen.value);
}

// ── Paste ───────────────────────────────────────────────────────────────────
//
// The import itself is the app's, not this component's: `useWindowFileImport`
// already claims a pasted image anywhere in the window and runs it through the
// staging session. Re-implementing that here would be a second import path to
// keep correct. What is missing is the part this picker owes the reader — being
// told the paste FILED something, and finding it selectable straight after —
// and that is all this adds.

const awaitingPaste = ref(false);

function onPaste(event) {
  // A paste into the search field is a search, not an import — and the window
  // importer skips editable targets for the same reason.
  const target = event.target;
  if (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target?.isContentEditable
  ) {
    return;
  }
  const files = Array.from(event.clipboardData?.items || [])
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file) => file && isSupportedImportFile(file));
  if (!files.length) return;
  awaitingPaste.value = true;
  notices.push({
    level: "info",
    text:
      files.length === 1
        ? "Importing the pasted picture into your library — it will appear here when it lands."
        : `Importing ${files.length} pasted pictures into your library — they will appear here when they land.`,
  });
}

const importsRunning = computed(() => Object.keys(tasks.importRuns).length);

watch(importsRunning, (now, before) => {
  if (!awaitingPaste.value || now !== 0 || !before) return;
  awaitingPaste.value = false;
  // Newest first, so what was just pasted is the first tile.
  facet.kind = "";
  facet.id = null;
  search.value = "";
  reload();
});

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      facet.kind = "";
      facet.id = null;
      search.value = "";
      chosen.value = null;
      expanded.character = false;
      expanded.set = false;
      awaitingPaste.value = false;
      entityLists.refresh("characters");
      entityLists.refresh("sets");
      if (entityLists.canSeeProjects) entityLists.refresh("projects");
      getPictureCount()
        .then((body) => {
          totalCount.value = Number(body?.count);
        })
        .catch((err) => {
          // A missing headline count is cosmetic: the rail still narrows and
          // the grid still fills. Logged rather than surfaced.
          console.warn("[PicturePicker] could not read the library count", err);
          totalCount.value = null;
        });
      load();
      window.addEventListener("paste", onPaste);
    } else {
      window.removeEventListener("paste", onPaste);
    }
  },
  { immediate: true },
);

// The listener is added on open and removed on close, so a component unmounted
// while still open would leave one behind on `window` for the life of the tab —
// and it holds this instance's notice store and refs.
onUnmounted(() => window.removeEventListener("paste", onPaste));
</script>

<style scoped>
.pp {
  display: flex;
  min-height: 0;
  height: min(660px, 70vh);
}

.pp-rail {
  width: 210px;
  flex-shrink: 0;
  overflow-y: auto;
  padding: var(--space-3);
  border-right: 1px solid rgb(var(--v-theme-divider));
}

.pp-sec {
  padding: var(--space-4) var(--space-3) var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.pp-sec:first-child {
  padding-top: var(--space-2);
}

.pp-facet,
.pp-more {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  min-height: 30px;
  padding: var(--space-2) var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
}
.pp-facet:hover,
.pp-more:hover {
  background: var(--hover-wash);
}
.pp-facet--on {
  background: var(--active-wash);
  color: var(--active-text);
  font-weight: var(--weight-medium);
}
.pp-facet__label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pp-facet__count {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.pp-more {
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-accent));
}

.pp-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.pp-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}
.pp-search {
  flex: 1;
}
.pp-paste {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.pp-paste kbd {
  padding: 3px 5px;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}

.pp-scroll {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5);
}
.pp-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--space-3);
}
.pp-cell {
  aspect-ratio: 1 / 1;
  padding: 0;
  border: 0;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  overflow: hidden;
  cursor: pointer;
}
.pp-cell img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.pp-cell--on {
  box-shadow: 0 0 0 2px var(--active-bar);
}
.pp-cell:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
.pp-note {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.pp-note--error {
  color: rgb(var(--v-theme-error));
}
.pp-more-row {
  display: flex;
  justify-content: center;
  padding-top: var(--space-5);
}

.pp-foot {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid rgb(var(--v-theme-divider));
}
.pp-chosen {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.pp-spacer {
  flex: 1;
}
</style>
