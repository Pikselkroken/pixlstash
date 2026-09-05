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
            ref="searchRef"
            v-model="search"
            class="pp-search"
            icon="magnify"
            placeholder="Search this library"
            @enter="reload"
          />
          <!-- Paste is a real route in, and it IMPORTS: a screenshot that was
               never imported cannot be chosen, because everything downstream of
               this picker names a picture by its id. The affordance is all this
               component contributes - the window-level paste handler runs the
               import and `ImageImporter` announces it from inside, which is the
               only place that knows whether it happened. See the Paste note in
               the script block for why announcing it from here was removed. -->
          <span class="pp-paste">
            or press <kbd>Ctrl</kbd><kbd>V</kbd>
          </span>
        </div>

        <div class="pp-scroll">
          <!-- One live region for all three, so a reader who cannot see the
               grid fill is told that it did, that it did not, or that it
               failed. -->
          <p
            v-if="error || loading || !pictures.length"
            class="pp-note"
            :class="{ 'pp-note--error': error }"
            role="status"
          >
            {{
              error ||
              (loading
                ? "Loading pictures…"
                : "No pictures here. Try another grouping, search, or paste one in.")
            }}
          </p>
          <div v-if="pictures.length" class="pp-grid">
            <button
              v-for="(pic, i) in pictures"
              :key="pic.id"
              :ref="(el) => (cellRefs[i] = el)"
              type="button"
              class="pp-cell"
              :class="{
                'pp-cell--on': chosen?.id === pic.id,
                'pp-cell--gone': unavailable.has(pic.id),
              }"
              :aria-pressed="chosen?.id === pic.id"
              :aria-disabled="unavailable.has(pic.id) || undefined"
              :tabindex="i === tabStop ? 0 : -1"
              :title="tileTitle(pic)"
              @click="pick(pic)"
              @dblclick="useTile(pic)"
              @keydown="onCellKeydown($event, i)"
            >
              <img
                v-if="!unavailable.has(pic.id)"
                :src="thumbUrl(pic)"
                alt=""
                loading="lazy"
                decoding="async"
                @error="unavailable.add(pic.id)"
              />
              <!-- Not an empty box: a picture whose file the server cannot
                   reach - an unplugged drive, the state this very shelf models
                   - has to say so, or it reads as a thumbnail that has not
                   loaded yet and invites a click that cannot work. -->
              <span v-else class="pp-gone">
                <v-icon size="18">mdi-image-off-outline</v-icon>
              </span>
              <!-- The mark that says WHICH tile is chosen, so the edge is not
                   the only carrier of the state - the lesson the Character
                   editor's reference grid learned about a thin edge on a
                   photograph.

                   `aria-hidden`, and real markup rather than a `::before`,
                   because generated `content` counts towards the accessible
                   NAME (accname §4.3.2 step 2F, which Chromium implements). The
                   tile's subtree is one `alt=""` image, i.e. empty, so its name
                   falls through to `title`; a bare ✓ in there would make the
                   chosen tile announce as "✓" and lose the picture's name - a
                   regression on exactly the reader the badge is drawn for.
                   `aria-pressed` already says chosen. -->
              <span
                v-if="chosen?.id === pic.id"
                class="pp-tick"
                aria-hidden="true"
                >✓</span
              >
            </button>
          </div>
          <p v-if="capped" class="pp-note pp-note--after" role="status">
            Showing the first {{ SEARCH_CAP }} matches. Narrow it with a
            grouping, or search for something more particular.
          </p>
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
// SINGLE-SELECT ON PURPOSE. Every caller needs exactly one picture - a model's
// thumbnail, a workflow's fixed input, a run-time answer - and multi-select
// would be built on the argument that something might want it one day.
//
// Design: the `Picker` artboard of the 1.11 Workflow Library canvas. Facet rail
// left, search + grid right, receipt + verbs in the footer.

import { computed, nextTick, reactive, ref, watch } from "vue";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";
import AppInput from "./AppInput.vue";
import {
  getPictureCount,
  pictureThumbnailUrl,
  searchPictures,
  streamPictures,
} from "../../api/pictures";
import { errorDetail } from "../../utils/apiError";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
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
// The same ceiling for search, which needs one imposed HERE: `GET
// /pictures/search` ignores `top_n` and defaults its `limit` to `sys.maxsize`,
// so a loose query answers with every match above the similarity threshold and
// this grid is not virtualised. Cut, and SAID (see `capped` below) - a silent
// truncation reads as "that is all there is".
const SEARCH_CAP = 120;
// How many rows of a facet group are shown before `All N ›`. Three is the
// artboard's count and is enough to show what the group is.
const FACET_PREVIEW = 3;
// Columns in the tile grid. The arrow keys need the number the CSS is using,
// and `auto-fill` will not tell them, so the grid is held at a fixed count and
// the tiles flex instead - which is also what the artboard draws.
const COLUMNS = 5;

const entityLists = useEntityListsStore();
const tasks = useTasksStore();

const searchRef = ref(null);
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
// True when a search returned more than `SEARCH_CAP` and the tail was dropped.
const capped = ref(false);
// Picture ids whose thumbnail would not load. A thumbnail is generated on
// demand, but only for a file the server can still reach and decode - an
// unplugged drive is a state this app models, so a tile has to be able to say
// "not available" rather than draw an empty box that can still be chosen.
const unavailable = ref(new Set());

// A request that was in flight when the facet changed must not overwrite the
// list the reader is now looking at. Asserted in the suite by resolving two
// reads out of order.
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

/** A facet count, grouped the way every other count in the app is. */
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

/** The tile's accessible name, which has to carry the unavailable state too. */
function tileTitle(pic) {
  return unavailable.value.has(pic.id)
    ? `${tileName(pic)} - not available`
    : tileName(pic);
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
      // Search answers in one shot, so there is nothing to page through - and
      // no ceiling on the way back either, so one is applied here.
      const rows = await searchPictures(text, { query: scopeParams().toString() });
      if (seq !== loadSeq) return;
      const all = Array.isArray(rows) ? rows : [];
      capped.value = all.length > SEARCH_CAP;
      pictures.value = all.slice(0, SEARCH_CAP);
      done.value = true;
      nextOffset.value = 0;
      return;
    }
    const params = scopeParams();
    // An explicit projection rather than `fields=grid`, which the route reads
    // as "this is the picture grid" and silently forces `stack_leaders_only`
    // (`_listing.py`) - a picker that cannot offer a stacked variant, with
    // nothing on screen saying so, and a list that would then disagree with
    // what the same query returns through search.
    params.set("fields", "id,file_path");
    params.set("sort", "DATE");
    params.set("descending", "true");
    const batch = await streamPictures(params.toString(), {
      offset: append ? nextOffset.value : 0,
      batchLimit: BATCH,
    });
    if (seq !== loadSeq) return;
    const rows = Array.isArray(batch?.pictures) ? batch.pictures : [];
    capped.value = false;
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

/**
 * Choose a tile, if it can be chosen at all.
 *
 * @returns {boolean} whether the choice landed - which the two "choose it AND
 *   take it" gestures below have to ask, or a refused tile falls through to
 *   `use()` and accepts whatever was chosen BEFORE it. That is the worst
 *   possible outcome for a refusal: silent, and a picture the reader did not
 *   point at.
 */
function pick(pic) {
  // An unreachable file cannot become a thumbnail, so it cannot be chosen
  // either - the tile says why rather than failing after the dialog has shut.
  if (!pic || unavailable.value.has(pic.id)) return false;
  chosen.value = pic;
  return true;
}

/** Double-click, and Enter: choose this one and take it, or do neither. */
function useTile(pic) {
  if (pick(pic)) use();
}

function use() {
  if (!chosen.value) return;
  emit("pick", chosen.value);
}

// ── The grid's keyboard ─────────────────────────────────────────────────────
//
// One tab stop for the whole grid and the arrows move inside it (the listbox
// pattern the app already uses in `DedupPictureStrip`). Without it a 120-tile
// grid is 120 tab stops between the search field and the footer verbs, which
// makes the keyboard route to `Use this picture` unusable - and it is the route
// the ↵ badge on that button promises.

const cellRefs = ref([]);

const tabStop = computed(() => {
  const at = pictures.value.findIndex((p) => p.id === chosen.value?.id);
  return at >= 0 ? at : 0;
});

function focusCell(index) {
  const clamped = Math.max(0, Math.min(pictures.value.length - 1, index));
  const pic = pictures.value[clamped];
  if (!pic) return;
  chosen.value = unavailable.value.has(pic.id) ? chosen.value : pic;
  nextTick(() => cellRefs.value[clamped]?.focus());
}

function onCellKeydown(event, index) {
  // Enter on a tile ACCEPTS. A tile is a `<button>`, and `AppDialog` exempts
  // buttons from its Enter contract precisely so native activation wins - so
  // without this the ↵ badge on `Use this picture` is a promise the one state
  // it matters in cannot keep.
  if (event.key === "Enter") {
    event.preventDefault();
    useTile(pictures.value[index]);
    return;
  }
  const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: COLUMNS, ArrowUp: -COLUMNS }[
    event.key
  ];
  if (step === undefined) return;
  event.preventDefault();
  focusCell(index + step);
}

// ── Paste ───────────────────────────────────────────────────────────────────
//
// **This component does not handle the paste, and deliberately says nothing
// about it.** `useWindowFileImport` already claims a pasted image anywhere in
// the window, and `ImageImporter` - the thing it hands off to - is what
// announces the import, from inside the import, where the truth is: it opens
// its own progress dialog on the same keystroke, counts the files, and reports
// the buckets at the end. A second announcement from here could only ever be a
// guess, and it guessed wrong in three ways worth writing down, because they
// are the reason this is subtraction rather than a fix:
//
//   * `startImport` refuses outright while another import is running, and
//     refuses again under a read-only token - so a picker that announced
//     "importing your pasted picture" said so about nothing at all;
//   * neither refusal ever registers a run, so a flag armed on paste and
//     disarmed on the run finishing stayed armed for the life of the dialog;
//   * the window importer takes video as well as images, so a filter of this
//     component's own reported one paste and stayed silent on another.
//
// What is left is the half that IS this component's business: what was pasted
// has to become selectable without reopening the dialog. So the list is
// re-read when an import finishes while the picker is open - the CURRENT list,
// in place. It deliberately does not jump back to `Everything`: an import
// finishing is not a reason to throw away the facet, the search and the choice
// the reader has made in the meantime, and any import may be one this reader
// never started.

const importsRunning = computed(() => Object.keys(tasks.importRuns).length);

watch(importsRunning, (now, before) => {
  if (!props.open || now !== 0 || !before) return;
  load();
});

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    facet.kind = "";
    facet.id = null;
    search.value = "";
    chosen.value = null;
    capped.value = false;
    unavailable.value = new Set();
    expanded.character = false;
    expanded.set = false;
    entityLists.refresh("characters");
    entityLists.refresh("sets");
    if (entityLists.canSeeProjects) entityLists.refresh("projects");
    getPictureCount()
      .then((body) => {
        totalCount.value = Number(body?.count);
      })
      .catch((err) => {
        // A missing headline count is cosmetic: the rail still narrows and the
        // grid still fills. Logged rather than surfaced.
        console.warn("[PicturePicker] could not read the library count", err);
        totalCount.value = null;
      });
    load();
    // The search field is where a reader who did not come for a facet starts,
    // and the dialog would otherwise open with focus on its Close button.
    nextTick(() => searchRef.value?.focus());
  },
  { immediate: true },
);
</script>

<style scoped>
.pp {
  display: flex;
  min-height: 0;
  /* 70vh so the grid is the tall thing on a tall screen, capped so it does not
     become a wall of tiles on a very tall one. Both on the 4px grid. */
  height: min(660px, 70vh);
}

.pp-rail {
  width: 208px;
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
  min-height: 32px;
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
  padding: var(--space-1) var(--space-2);
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
  /* A fixed five, matching the artboard AND the arrow keys' `COLUMNS`: with
     `auto-fill` the two would silently disagree the moment the dialog narrowed,
     and Down would jump the wrong distance. The tiles flex instead. */
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--space-3);
}
.pp-cell {
  position: relative;
  /* A stacking context of its own, so the tick's z-index below is scoped to
     this tile rather than escaping into whatever context an ancestor happens
     to establish. */
  isolation: isolate;
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
/* The chosen tile wears the whole §11 selected vocabulary - `--active-wash`
   fill AND `--active-bar` edge - not the edge alone. The edge on its own is a
   2px stroke drawn INSIDE a photograph that can be any colour, so whether it
   registers depends on the frame behind it: `--active-bar` is `primary` on
   dark and the `accent` amber on light, and either can land on a picture that
   already contains it. That is what "the picker doesn't get any selection
   marker" was - checked in a browser against real thumbnails, in both themes.

   Outline rather than a box-shadow, because the focus ring below is a
   box-shadow and the two would fight over one property - which is how the
   chosen state was once hidden from the reader who has no other way to see it
   (a focused, chosen tile showed only the ring). */
.pp-cell--on {
  outline: var(--space-1) solid var(--active-bar);
  outline-offset: calc(-1 * var(--space-1));
}
/* The wash, over the image rather than under it - a tile IS its picture, so
   there is no surface left to tint. A pseudo-element because it carries no
   text: nothing for the accessibility tree to pick up (unlike the tick, see
   the template). */
.pp-cell--on::after {
  content: "";
  position: absolute;
  inset: 0;
  background: var(--active-wash);
  pointer-events: none;
}
/* The tick. `z-index` to clear the wash, which is an ::after and therefore
   paints after every child; the elevation is what lifts it off an arbitrary
   photograph, the same job it does on the image grid's own check badge. */
.pp-tick {
  position: absolute;
  z-index: 1;
  top: var(--space-2);
  right: var(--space-2);
  width: var(--space-6);
  height: var(--space-6);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-pill);
  background: var(--active-bar);
  color: var(--active-text);
  box-shadow: var(--elevation-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-bold);
  line-height: 1;
  pointer-events: none;
}
.pp-cell:focus-visible {
  box-shadow: var(--focus-ring);
}
.pp-cell--gone {
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: not-allowed;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.pp-gone {
  display: inline-flex;
}
.pp-note {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.pp-note--error {
  color: rgb(var(--v-theme-error));
}
.pp-note--after {
  padding-top: var(--space-5);
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
