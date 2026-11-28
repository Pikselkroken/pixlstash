<template>
  <AppDialog
    :open="open"
    title="Choose a picture"
    :subtitle="subtitle"
    size="xl"
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
            {{ group === "character" ? "Person" : "Picture set" }}
          </div>
          <template v-for="row in shown(group)" :key="`${group}-${row.id}`">
            <!-- A person unfolds into their face clusters rather than being a
                 scope of its own, so the row is a disclosure, not a toggle. -->
            <button
              v-if="group === 'character'"
              type="button"
              class="pp-facet"
              :aria-expanded="unfolded === row.id"
              @click="openCharacter(row)"
            >
              <v-icon size="16" class="pp-facet__chevron">{{
                unfolded === row.id ? "mdi-chevron-down" : "mdi-chevron-right"
              }}</v-icon>
              <span class="pp-facet__label">{{ row.name }}</span>
              <span class="pp-facet__count">{{ groupedNumber(row.count) }}</span>
            </button>
            <button
              v-else
              type="button"
              class="pp-facet"
              :class="{ 'pp-facet--on': isOn(group, row.id) }"
              :aria-pressed="isOn(group, row.id)"
              @click="choose(group, row.id)"
            >
              <span class="pp-facet__label">{{ row.name }}</span>
              <span class="pp-facet__count">{{ groupedNumber(row.count) }}</span>
            </button>
            <template v-if="group === 'character' && unfolded === row.id">
              <!-- Not a live region: the grid's note announces the grouping. -->
              <p v-if="grouping || groupingFailed" class="pp-rail-note">
                {{ grouping ? "Grouping faces…" : "Could not group the faces." }}
              </p>
              <button
                v-for="c in clusterRows"
                :key="`cluster-${c.key}`"
                type="button"
                class="pp-facet pp-facet--sub"
                :class="{ 'pp-facet--on': isCluster(c.key) }"
                :aria-pressed="isCluster(c.key)"
                @click="chooseCluster(c.key)"
              >
                <img
                  v-if="c.face != null"
                  class="pp-facet__face"
                  :src="pictureThumbnailUrl(c.face)"
                  alt=""
                />
                <span
                  class="pp-facet__label"
                  :class="{ 'pp-facet__label--quiet': c.key === UNMATCHED }"
                  >{{ c.label }}</span
                >
                <span class="pp-facet__count">{{ groupedNumber(c.count) }}</span>
              </button>
              <button
                type="button"
                class="pp-facet pp-facet--sub"
                :class="{ 'pp-facet--on': isOn('character', row.id) }"
                :aria-pressed="isOn('character', row.id)"
                @click="choose('character', row.id)"
              >
                <span class="pp-facet__label pp-facet__label--quiet"
                  >All of {{ row.name }}</span
                >
                <span class="pp-facet__count">{{ groupedNumber(row.count) }}</span>
              </button>
            </template>
          </template>
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
            :placeholder="
              scope ? `Search ${scope.name}'s ${scope.noun}` : 'Search this library'
            "
            @enter="reload"
          />
          <!-- Paste is a real route in, and it IMPORTS: a screenshot that was
               never imported cannot be chosen, because everything downstream of
               this picker names a picture by its id. The affordance is all this
               component contributes - the window-level paste handler runs the
               import and `ImageImporter` announces it from inside, which is the
               only place that knows whether it happened. See the Paste note in
               the script block for why announcing it from here was removed. -->
          <!-- Offered only where a video is usable: a caller that takes the
               picture's thumbnail gets a frame from a video for free. A
               workflow's LoadImage cannot read a video file at all, so the Run
               popup never offers this. -->
          <v-checkbox
            v-if="offerVideos"
            v-model="includeVideos"
            class="pp-videos"
            label="Include videos"
            density="compact"
            hide-details
          />
          <span class="pp-paste">
            or press <span><KeyCap k="Ctrl" /><kbd>V</kbd></span>
          </span>
        </div>

        <div class="pp-scroll">
          <!-- Which cluster the grid shows, so a face from Cluster 2 is never
               mistaken for a reference face. -->
          <p v-if="scope" class="pp-scope">
            <b>{{ scope.name }} · {{ scope.label }}</b>
            <span v-if="scope.isReference" class="pp-badge"
              ><v-icon size="12">mdi-check</v-icon>Reference cluster</span
            >
            <span>{{ scope.detail }}</span>
          </p>
          <!-- One live region for all three, so a reader who cannot see the
               grid fill is told that it did, that it did not, or that it
               failed. -->
          <p
            v-if="error || grouping || loading || !pictures.length"
            class="pp-note"
            :class="{ 'pp-note--error': error }"
            role="status"
          >
            {{
              error ||
              (grouping ? "Grouping faces…" : loading ? "Loading pictures…" : emptyNote)
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
              :aria-label="tileTitle(pic)"
              @click="pick(pic)"
              @dblclick="useTile(pic)"
              @keydown="onCellKeydown($event, i)"
            >
              <Tooltip
                :text="tileTitle(pic)"
                activator="parent"
                :describe="false"
              />
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
          <span class="pp-chosen"
            >{{ chosen ? "1 chosen" : "None chosen"
            }}<template v-if="chosen && scope">
              · from {{ scope.name }}'s {{ scope.from }}</template
            ></span
          >
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
import Tooltip from "./Tooltip.vue";
import { getFaceClusters } from "../../api/characters";
import {
  getPictureCount,
  pictureThumbnailUrl,
  searchPictures,
  streamPictures,
} from "../../api/pictures";
import { errorDetail } from "../../utils/apiError";
import { PIL_IMAGE_EXTENSIONS } from "../../utils/media";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useTasksStore } from "../../stores/useTasksStore";
import KeyCap from "./KeyCap.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  // What the picture is FOR, said in the caller's own words ("for Reference",
  // "for Flux Realism"). The title never changes; this does.
  subtitle: { type: String, default: "" },
  // Still images only unless this is set AND the reader ticks "Include
  // videos". Set it only where a video is usable as a frame - see the
  // checkbox in the template.
  offerVideos: { type: Boolean, default: false },
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
// The face clusters' cut: Normal, the Character editor's (services/face_clusters.py
// SAME_PERSON). A reader who wants another strictness has the Faces dialog.
const SAME_PERSON = 0.45;
// The cluster key of the faces that grouped with nobody.
const UNMATCHED = "unmatched";

const entityLists = useEntityListsStore();
const tasks = useTasksStore();

const searchRef = ref(null);
// `kind` "cluster" is a face cluster of the unfolded character `id`; `cluster`
// is its index in the answer, or `UNMATCHED`.
const facet = reactive({ kind: "", id: null, cluster: null });
const expanded = reactive({ character: false, set: false });
const search = ref("");
const includeVideos = ref(false);
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
// The same guard for the Everything count, which the checkbox re-reads.
let countSeq = 0;

// ── Face clusters ───────────────────────────────────────────────────────────
//
// Choosing a person unfolds their face clusters (`GET /face-clusters`, computed
// on each call) under their row, one person at a time, and lands on the
// reference cluster: who the owner said the person is. A person without one
// lands on all their pictures.
const unfolded = ref(null);
const faceGroups = ref(null);
const grouping = ref(false);
const groupingFailed = ref(false);
let clusterSeq = 0;
// The person still waiting to land on their reference cluster. Any choice the
// reader makes meanwhile clears it, so the answer never moves them.
let landOn = null;

const clusterRows = computed(() => {
  const body = faceGroups.value;
  if (!body) return [];
  const rows = (body.clusters || []).map((c, i) => ({
    key: i,
    label: c.is_reference ? "Reference" : `Cluster ${i + 1}`,
    isReference: c.is_reference,
    count: c.size,
    face: c.members[0]?.picture_id,
    members: c.members,
  }));
  if (body.unclustered?.length) {
    rows.push({
      key: UNMATCHED,
      label: "Matched no one",
      isReference: false,
      count: body.unclustered.length,
      face: null,
      members: body.unclustered,
    });
  }
  return rows;
});

// What the grid, the search field and the receipt say about a cluster.
const scope = computed(() => {
  if (facet.kind !== "cluster") return null;
  const row = clusterRows.value.find((r) => r.key === facet.cluster);
  if (!row) return null;
  const name = facets.value.character.find((c) => c.id === facet.id)?.name ?? "";
  if (row.key === UNMATCHED) {
    return {
      name,
      label: row.label,
      isReference: false,
      noun: "unmatched faces",
      from: "unmatched faces",
      detail: `${row.count} faces that grouped with no one`,
    };
  }
  return {
    name,
    label: row.label,
    isReference: row.isReference,
    noun: row.isReference ? "reference faces" : `${row.label} faces`,
    from: row.isReference ? "reference cluster" : row.label,
    detail: `${row.count} faces, most typical first`,
  };
});

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

// What an empty grid says, which has to name the way out: where videos are
// hidden but can be shown, that is the checkbox above.
const emptyNote = computed(() => {
  if (includeVideos.value) {
    return "No pictures here. Try another grouping, search, or paste one in.";
  }
  if (props.offerVideos) {
    return "No still images here. Tick Include videos to show videos too.";
  }
  return "No still images here. Videos are not offered for this. Try another grouping, search, or paste one in.";
});

function shown(group) {
  const rows = facets.value[group];
  return expanded[group] ? rows : rows.slice(0, FACET_PREVIEW);
}

function isOn(kind, id) {
  return facet.kind === kind && facet.id === id;
}

function isCluster(key) {
  return facet.kind === "cluster" && facet.cluster === key;
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
  landOn = null;
  if (kind !== "character") fold();
  facet.kind = kind;
  facet.id = id;
  facet.cluster = null;
  reload();
}

function fold() {
  clusterSeq += 1;
  unfolded.value = null;
  faceGroups.value = null;
  grouping.value = false;
  groupingFailed.value = false;
}

function chooseCluster(key) {
  landOn = null;
  facet.kind = "cluster";
  facet.id = unfolded.value;
  facet.cluster = key;
  reload();
}

/** The reference cluster when there is one, else all of the person. */
function landOnReference(id) {
  const at = (faceGroups.value?.clusters || []).findIndex((c) => c.is_reference);
  if (at >= 0) chooseCluster(at);
  else choose("character", id);
}

async function openCharacter(row) {
  if (unfolded.value === row.id && grouping.value) return;
  if (unfolded.value === row.id && faceGroups.value) {
    landOnReference(row.id);
    return;
  }
  fold();
  const seq = clusterSeq;
  unfolded.value = row.id;
  grouping.value = true;
  landOn = row.id;
  // Nothing in the grid until the person's clusters say where to land: a
  // flash of all their pictures first would be a list the reader never chose.
  loadSeq += 1;
  loading.value = false;
  error.value = "";
  pictures.value = [];
  chosen.value = null;
  done.value = true;
  capped.value = false;
  facet.kind = "character";
  facet.id = row.id;
  facet.cluster = null;
  try {
    const body = await getFaceClusters(row.id, SAME_PERSON);
    // Another person, a project or a set chosen meanwhile folded this one.
    if (seq !== clusterSeq) return;
    faceGroups.value = body;
  } catch (err) {
    if (seq !== clusterSeq) return;
    // The person's pictures are still all there under "All of"; only the
    // grouping is missing, and the rail says so.
    console.warn("[PicturePicker] could not group the faces of character", row.id, err);
    groupingFailed.value = true;
  }
  if (seq !== clusterSeq) return;
  grouping.value = false;
  if (landOn === row.id) landOnReference(row.id);
}

/** A cluster's members, as tiles, in the answer's most-typical-first order. */
function clusterTiles() {
  const row = clusterRows.value.find((r) => r.key === facet.cluster);
  return (row?.members || [])
    .filter(
      (m) =>
        includeVideos.value ||
        PIL_IMAGE_EXTENSIONS.includes(String(m.format || "").toLowerCase()),
    )
    .map((m) => ({ id: m.picture_id, format: m.format }));
}

/** Restrict a query to still images, unless videos were asked for. */
function mediaParams() {
  const params = new URLSearchParams();
  if (includeVideos.value) return params;
  // The grid's own "Images" filter, so the two agree on what an image is.
  for (const ext of PIL_IMAGE_EXTENSIONS) params.append("format", ext.toUpperCase());
  return params;
}

/** The facet as listing query params, shared by the stream and the search. */
function scopeParams() {
  const params = mediaParams();
  if (facet.kind === "project") params.set("project_id", String(facet.id));
  if (facet.kind === "character" || facet.kind === "cluster") {
    params.set("character_id", String(facet.id));
  }
  if (facet.kind === "set") params.set("set_id", String(facet.id));
  return params;
}

async function load({ append = false } = {}) {
  const seq = ++loadSeq;
  loading.value = true;
  error.value = "";
  try {
    const text = search.value.trim();
    // A cluster is already in hand: its tiles need no read, and a search inside
    // it is the person's search cut to its members.
    const tiles = facet.kind === "cluster" ? clusterTiles() : null;
    if (tiles && !text) {
      pictures.value = tiles;
      capped.value = false;
      done.value = true;
      nextOffset.value = 0;
      return;
    }
    if (text) {
      // Search answers in one shot, so there is nothing to page through - and
      // no ceiling on the way back either, so one is applied here.
      const rows = await searchPictures(text, { query: scopeParams().toString() });
      if (seq !== loadSeq) return;
      let all = Array.isArray(rows) ? rows : [];
      if (tiles) {
        const members = new Set(tiles.map((t) => t.id));
        all = all.filter((r) => members.has(r.id));
      }
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
// announces the import, from inside the import, where the truth is: it draws
// the arriving tiles on the same keystroke, counts the files, and reports
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

function readTotal() {
  const seq = ++countSeq;
  getPictureCount(mediaParams().toString())
    .then((body) => {
      if (seq === countSeq) totalCount.value = Number(body?.count);
    })
    .catch((err) => {
      if (seq !== countSeq) return;
      // A missing headline count is cosmetic: the rail still narrows and the
      // grid still fills. Logged rather than surfaced.
      console.warn("[PicturePicker] could not read the library count", err);
      totalCount.value = null;
    });
}

watch(includeVideos, () => {
  if (!props.open) return;
  readTotal();
  reload();
});

const importsRunning = computed(() => Object.keys(tasks.importRuns).length);

watch(importsRunning, (now, before) => {
  // Mid-grouping the landing reads the list itself; a read now would flash
  // all of the person first.
  if (!props.open || now !== 0 || !before || grouping.value) return;
  load();
});

watch(
  () => props.open,
  (isOpen) => {
    // Reset on CLOSE, so the checkbox's own watcher sees a closed picker and
    // does not read the list a second time on the next open.
    if (!isOpen) {
      includeVideos.value = false;
      return;
    }
    facet.kind = "";
    facet.id = null;
    facet.cluster = null;
    landOn = null;
    fold();
    search.value = "";
    chosen.value = null;
    capped.value = false;
    unavailable.value = new Set();
    expanded.character = false;
    expanded.set = false;
    entityLists.refresh("characters");
    entityLists.refresh("sets");
    if (entityLists.canSeeProjects) entityLists.refresh("projects");
    readTotal();
    load();
    // The search field is where a reader who did not come for a facet starts,
    // and the dialog would otherwise open with focus on its Close button.
    nextTick(() => searchRef.value?.focus());
  },
  { immediate: true },
);
</script>

<style scoped src="./PicturePicker.css"></style>
