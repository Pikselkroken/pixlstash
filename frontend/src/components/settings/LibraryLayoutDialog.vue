<script setup>
/**
 * Choose a layout - one pane, and the tree is the argument (v1.11 Phase 4c,
 * "The Live Folder Preview").
 *
 * A dialog, not a settings tab, opened from the active library's overflow menu
 * in LibrariesSection. The layout routes are `/server-config/layout`, which
 * address whichever library is *open*, which is why the entry point is on the
 * active row only.
 *
 * Two things share the pane and the pane's job is to keep them apart:
 *
 * 1. **Choosing a layout moves no files.** Every path already in the library is
 *    what its assignments were read from, so every path is already true. The
 *    layout decides where a *new* picture is written, and where one goes when
 *    the folder it sits in stops describing it.
 * 2. **Moving the existing library onto the layout is a separate gesture**, the
 *    primary button on the consequence bar, and the only control on the pane
 *    that changes colour.
 *
 * Three behaviours here are load-bearing rather than polish:
 *
 * - **The layout cannot be edited while a move is running.** Both the selects
 *   and the save path refuse. A layout edited mid-run makes later passes
 *   re-plan against a new layout, so half the library lands on one layout and
 *   half on another, under one undo batch that describes neither.
 * - **Re-counting never destroys the Undo.** `refreshPreview` does not touch
 *   `lastRun`; its `batchId` is the only route back to a move that has already
 *   happened, and an edit is not a reason to throw it away.
 * - **The move cannot fire on a stale count.** The consequence bar carries the
 *   number next to the verb, so a modal confirm on top would be a second yes
 *   for the same gesture. Instead every edit marks the count stale, the bar
 *   says "counting…" *instead of* the old number, and the primary button is
 *   inert until a fresh count lands. A number that lags the tree is worse than
 *   no number.
 *
 * A library switch reloads the page (`useLibrariesStore.begin` -> `reloadPage`),
 * so nothing here has to survive one.
 */
import { computed, onUnmounted, ref, watch } from "vue";
import { VCheckbox, VIcon, VSelect, VTextField } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { useOperationStore } from "../../stores/useOperationStore";
import {
  getLayoutSettings,
  setLayoutSettings,
  getLayoutMigrationPreview,
  runLayoutMigrationPass,
} from "../../api/serverConfig";
import {
  LAYOUT_FACETS,
  describeSegment,
  formatLayout,
  parseLayout,
} from "../../utils/libraryLayout";
import { errorDetail } from "../../utils/apiError";

/**
 * Four folder levels, one per facet, and the width the row is drawn to.
 *
 * The builder must not wrap up to four levels at the dialog's 620px. The
 * budget: 620 - 2x24 dialog padding = 572px of content; three separators at
 * ~7px and six 4px gaps take ~45px, leaving ~527px, so each level gets a 120px
 * flex basis (4 x 120 + 45 = 525 <= 572) and grows into the slack, landing at
 * ~131px per column. Of that, Vuetify's field spends ~8px inset either side
 * and ~28px on the chevron, leaving ~88px for the label.
 *
 * That is enough because **the widest label and the most levels cannot happen
 * together.** A facet may not appear twice, so four levels means one facet
 * each - "Project", "Person", "Set", "Tag", none over ~50px. A two-facet label
 * like "Person or Set" (~84px) needs a level to spare, and at three levels the
 * columns are ~180px. A label that still does not fit ellipsises and keeps its
 * full text in `title`.
 *
 * A fifth level cannot happen either, for the same reason, but the row is
 * `flex-wrap: wrap` so a narrower box or a larger text size degrades into a
 * tidy second line rather than a horizontal scrollbar.
 */
const MAX_LEVELS = LAYOUT_FACETS.length;

const props = defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["close"]);

const libraries = useLibrariesStore();
const operations = useOperationStore();

const loading = ref(false);
const loaded = ref(false);
const error = ref("");
/**
 * The initial read failed. Kept apart from `error` because it decides whether
 * the builder may be shown at all: an empty `segments` after a failed GET looks
 * exactly like "this library has no layout", and the next edit would PATCH a
 * layout over one nobody has read.
 */
const loadError = ref("");
const refused = ref(false);

/** `[["project"], ["person", "set"]]`, the builder's own model. */
const segments = ref([]);
/** The folder a picture with nothing to file it by goes to. */
const unfiled = ref("");
/**
 * Whether "Move them now" also sweeps the pictures the layout cannot place
 * into `unfiled`. Off by default and not stored: it is an option on the
 * gesture, not on the layout, so it is sent with the preview and with every
 * pass rather than saved.
 */
const sweepUnfiled = ref(false);

const blocked = computed(
  () => libraries.hasLoadedSuccessfully && !libraries.canManage,
);
const unavailable = computed(() => blocked.value || refused.value);
const isOn = computed(() => segments.value.length > 0);
const layoutText = computed(() =>
  segments.value.map((segment) => describeSegment(segment)).join(" / "),
);

/**
 * The level slots the builder draws: every level in the layout, plus one empty
 * slot to add the next one, capped at four. The empty slot is a dropdown like
 * any other rather than a separate add button, which is what keeps the widest
 * case - four filled levels, no add control - the one the row is sized for.
 */
const levels = computed(() => {
  const rows = [...segments.value];
  if (rows.length < MAX_LEVELS) rows.push([]);
  return rows;
});

/**
 * A facet already spoken for at another level is not offered again. Left free
 * it renders a duplicated folder level - `project,person / set,tag / tag` is
 * expressible, draws `portrait/portrait/` on disk, and the backend takes it
 * verbatim.
 */
function itemsFor(index) {
  const taken = new Set(
    segments.value.flatMap((segment, i) => (i === index ? [] : segment)),
  );
  return LAYOUT_FACETS.map((facet) => ({
    ...facet,
    props: { disabled: taken.has(facet.value) },
  }));
}

function applySettings(body) {
  segments.value = parseLayout(body.layout);
  unfiled.value = body.layout_unfiled || "";
}

async function load() {
  loading.value = true;
  error.value = "";
  loadError.value = "";
  refused.value = false;
  try {
    applySettings(await getLayoutSettings());
    loaded.value = true;
    // A library with no layout opens on an empty slot, and the dialog writes
    // NOTHING until the owner picks a level.
    //
    // This used to seed `[["project"]]` through `scheduleSave`, which PATCHed
    // a layout onto the library as a side effect of opening the dialog. That
    // is not a safe default: `LibrarySettings.layout` being non-NULL is the
    // gate on the whole layout tracker (`database.py::_library_has_layout`),
    // so the seed armed `LayoutMoveTask` for every later reassignment - the
    // dialog decided, on the owner's behalf, that their files may be moved.
    // Opening a settings screen to read it must never be a decision.
    await refreshPreview();
  } catch (err) {
    refused.value = err?.response?.status === 403;
    if (!refused.value) {
      loadError.value =
        errorDetail(err) || err?.message || "Could not read the layout.";
    }
  } finally {
    loading.value = false;
  }
}

// Vuetify dialogs stay mounted after the first open, so onMounted fires only
// once - fetch on the open transition instead (the house pattern). `blocked` is
// watched alongside it because the registry read that answers it is still in
// flight when the dialog opens.
watch(
  [() => props.open, blocked],
  ([isOpen, cannot]) => {
    if (!isOpen || cannot || loaded.value) return;
    load();
  },
  { immediate: true },
);

/**
 * Coalesce a burst of edits into one PATCH.
 *
 * `v-select multiple` emits on **every item toggle**, and each save re-reads the
 * migration preview, which walks the whole library on disk. Ticking three
 * facets in one dropdown would otherwise be three writes and three full walks.
 * The model updates immediately so the control stays live; only the request
 * waits.
 */
const SAVE_DEBOUNCE_MS = 500;
let saveTimer = null;
/** Bumped per edit, so a response for a superseded edit is not applied. */
let editSeq = 0;

function scheduleSave(next) {
  // The one place the layout is frozen for the duration of a run, and it is
  // the right one: every edit routes through here, so a guard here covers the
  // model, the debounce and the write in one. The selects are `disabled` too,
  // but that is the affordance, not the rule.
  if (migrating.value) return;
  segments.value = next;
  scheduleWrite({ layout: formatLayout(next) });
}

/** The unfiled folder is a layout setting too, and is saved the same way. */
function setUnfiled(name) {
  if (migrating.value) return;
  unfiled.value = name;
  // Empty resets it: the server answers with its default, which the response
  // then puts back in the field.
  scheduleWrite({ layoutUnfiled: name.trim() || null });
}

function scheduleWrite(patch) {
  // The count on the bar now describes a layout nobody is looking at.
  countStale.value = true;
  editSeq += 1;
  const seq = editSeq;
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => save(patch, seq), SAVE_DEBOUNCE_MS);
}

onUnmounted(() => {
  window.clearTimeout(saveTimer);
  // Stops the pass loop below: the run is resumable, so abandoning it mid-way
  // is safe and finishing it is one more click.
  cancelled = true;
});

async function save(next, seq = ++editSeq) {
  error.value = "";
  try {
    const body = await setLayoutSettings(next);
    // A newer edit is already queued: applying this response would snap the
    // builder back to the layout the owner has moved on from.
    if (seq !== editSeq) return;
    applySettings(body);
    await refreshPreview();
  } catch (err) {
    error.value =
      errorDetail(err) || err?.message || "Could not save the layout.";
    // The server stored nothing, so what the builder is showing is not what is
    // recorded. Re-read rather than leave it showing a layout that was refused.
    try {
      applySettings(await getLayoutSettings());
    } catch {
      // Already covered by the error above.
    }
  }
}

function setLevel(index, facets) {
  const next = [...segments.value];
  next[index] = facets;
  // A level emptied by clearing its last facet is a level the owner has
  // removed, not an empty folder to draw: dropping it is what the grammar does
  // anyway, and keeping it would send a layout with a hole in it.
  const kept = next.filter((segment) => segment.length > 0);
  // Clearing the last level is how the owner turns the layout OFF, and it must
  // stay reachable: `formatLayout([])` is `null`, the PATCH the backend takes
  // to mean "no layout", which is what un-gates `_library_has_layout` and stops
  // LayoutMoveTask touching files again. This used to be refused, so a library
  // that had a layout could never be put back to not having one from the UI.
  // `refreshPreview` clears the count and the move bar on its own once `isOn`
  // goes false.
  scheduleSave(kept);
}

// ---------------------------------------------------------------------------
// Moving the existing library onto this layout
// ---------------------------------------------------------------------------

const preview = ref(null);
const previewing = ref(false);
/** The count on the bar no longer describes the layout on screen. */
const countStale = ref(true);
const migrating = ref(false);
/** Undo is one request with no per-pass progress, so its bar is indeterminate. */
const undoing = ref(false);
const migrationError = ref("");
/** `{movedCount, batchId}` once a run finishes, so Undo has something to undo. */
const lastRun = ref(null);
const movedSoFar = ref(0);
/**
 * What the run refused, by reason, accumulated over every pass.
 *
 * A file locked on Windows, or a name that appeared at the destination since
 * the plan, is reported as `move_failed` rather than moved. Dropping it would
 * let a run that could not touch 500 files report a clean "Moved 3,609".
 */
const runSkipped = ref({});
let cancelled = false;

/** Non-zero only when something would actually move. */
const wouldMove = computed(() => preview.value?.picture_count || 0);
const folderCount = computed(() => preview.value?.folder_count || 0);
const crossVolume = computed(() => preview.value?.cross_volume_count || 0);
const collisions = computed(() => preview.value?.collision_count || 0);
/**
 * The tree, with each row told how much of its own path it has to say.
 *
 * A folder is a row only when one of have/arriving/leaving is non-zero, so
 * under a multi-level layout **almost every row is a leaf whose ancestors are
 * absent** - a project folder that holds no pictures of its own is not in the
 * response, and its people are. Indenting on `depth` alone then communicates
 * nothing: there is no parent row above to be indented relative to, and nine
 * rows read "arm hair", "beard", "arm hair" with no idea which person each
 * sits under.
 *
 * So each row shows the ancestors that have no row of their own as a muted
 * breadcrumb, and indents only under the nearest ancestor that IS present.
 * `path` already carries exactly this; nothing is synthesised, and no parent
 * row the backend deliberately withheld is invented.
 */
const treeRows = computed(() => {
  const rows = preview.value?.tree || [];
  const present = new Set(rows.map((row) => row.path));
  return rows.map((row) => {
    const parts = (row.path || row.name || "").split("/");
    // How many leading components are covered by an ancestor that is a row.
    let anchored = parts.length - 1;
    while (anchored > 0 && !present.has(parts.slice(0, anchored).join("/"))) {
      anchored -= 1;
    }
    return {
      ...row,
      // Muted, and only what no row above already says. Empty when the parent
      // is present, which is when the indent alone is enough.
      crumbs: parts.slice(anchored, -1).join(" / "),
      indent: anchored,
    };
  });
});
/** Refusals worth naming, whichever half of the flow produced them. */
const refusals = computed(() => {
  const counts = { ...(preview.value?.skipped_counts || {}) };
  for (const [reason, count] of Object.entries(runSkipped.value)) {
    counts[reason] = (counts[reason] || 0) + count;
  }
  // Cross-volume has its own flag, so it is not repeated in the list.
  delete counts.destination_other_volume;
  return counts;
});

const REFUSAL_LABELS = {
  move_failed: "could not be moved just now",
  destination_taken: "would land on a name that is taken",
  source_file_missing: "are not on disk where the library records them",
  source_is_symlink: "are links rather than files",
  path_outside_root: "are outside the library folder",
  destination_outside_root: "would land outside the library folder",
};

const flags = computed(() => {
  const rows = [];
  if (collisions.value) {
    rows.push(
      `${collisions.value.toLocaleString()} renamed to -2, -3 and so on. Nothing is overwritten.`,
    );
  }
  if (crossVolume.value) {
    rows.push(
      `${crossVolume.value.toLocaleString()} on another drive, staying put.`,
    );
  }
  for (const [reason, count] of Object.entries(refusals.value)) {
    rows.push(
      `${count.toLocaleString()} ${REFUSAL_LABELS[reason] || `were refused (${reason})`}.`,
    );
  }
  return rows;
});

/**
 * The count is fresh, something would move, and nothing else is in flight.
 * This is the whole guard on the move: the bar shows the number beside the
 * verb, so the button being inert while the number is unknown is what stands
 * in for a confirm.
 */
const canMove = computed(
  () =>
    !migrating.value &&
    !previewing.value &&
    !countStale.value &&
    wouldMove.value > 0,
);

/**
 * Re-read the plan. **Never touches `lastRun`.** Its `batchId` is the only
 * route back to a move that has already happened, and a re-count is not a
 * reason to throw one away.
 */
async function refreshPreview() {
  if (!isOn.value) {
    preview.value = null;
    countStale.value = false;
    return;
  }
  previewing.value = true;
  try {
    preview.value = await getLayoutMigrationPreview({
      sweepUnfiled: sweepUnfiled.value,
    });
    countStale.value = false;
  } catch (err) {
    preview.value = null;
    // Only when the run has not already said something more specific: "the
    // move stopped part way, press Move again" is what the owner has to act
    // on, and a failed re-count on top of it is the lesser fact.
    if (!migrationError.value) {
      migrationError.value =
        errorDetail(err) || err?.message || "Could not count what would move.";
    }
  } finally {
    previewing.value = false;
  }
}

// The sweep changes the count, so toggling it is an edit as far as the bar is
// concerned: stale until the new count lands.
watch(sweepUnfiled, () => {
  if (migrating.value) return;
  countStale.value = true;
  refreshPreview();
});

/**
 * Run the migration to completion, one pass at a time.
 *
 * The loop *is* the progress bar, and echoing `batch_id` on every pass after
 * the first is what makes the whole run a single undo - each pass records its
 * own operation under that one id, and a batch is one undo unit. Dropping the
 * id would leave the owner undoing 200 pictures at a time.
 *
 * A pass that throws stops the loop and keeps what has already moved, which is
 * the resumable half of the contract: the tree is half-moved and wholly
 * consistent, and pressing the button again finishes it rather than starting
 * over.
 */
async function migrate() {
  if (!canMove.value) return;
  migrating.value = true;
  migrationError.value = "";
  runSkipped.value = {};
  movedSoFar.value = 0;
  cancelled = false;
  let cursor = 0;
  let batchId = null;
  try {
    for (;;) {
      const pass = await runLayoutMigrationPass({
        afterId: cursor,
        batchId,
        sweepUnfiled: sweepUnfiled.value,
      });
      batchId = pass.batch_id;
      movedSoFar.value += pass.moved_count || 0;
      for (const entry of pass.skipped || []) {
        const reason = entry?.reason || "move_failed";
        runSkipped.value[reason] = (runSkipped.value[reason] || 0) + 1;
      }
      if (pass.done || cancelled) break;
      // The cursor must strictly advance or this is an infinite loop at full
      // request rate. It does on today's server - the planner filters
      // `Picture.id > after_id` - but that is the server's property, not this
      // loop's, and a client spinning forever is not a failure mode worth
      // trusting somebody else's code to prevent.
      if (!(pass.next_after_id > cursor)) break;
      cursor = pass.next_after_id;
    }
    lastRun.value = { movedCount: movedSoFar.value, batchId };
  } catch (err) {
    // The resume sentence is appended rather than used as a fallback: the
    // server's own detail is the more useful half, and the guidance is the half
    // the owner has to act on. Half-moved is a valid state here and the copy
    // has to say so, or it reads as damage.
    const detail = errorDetail(err) || err?.message || "";
    migrationError.value = `${
      detail
        ? `The move stopped part way: ${detail}.`
        : "The move stopped part way."
    } Nothing is half-written. Press Move again to finish it.`;
    // Only when this run actually minted one: overwriting a previous run's
    // batch id with null would take its Undo away with it.
    if (batchId) lastRun.value = { movedCount: movedSoFar.value, batchId };
  } finally {
    migrating.value = false;
    countStale.value = true;
    await refreshPreview();
  }
}

function stopRun() {
  cancelled = true;
}

async function undoMigration() {
  const batchId = lastRun.value?.batchId;
  if (!batchId) return;
  migrating.value = true;
  undoing.value = true;
  try {
    // `undoBatchById` answers `null` rather than throwing when it refuses - a
    // read-only session, another operation already in flight, or a failure it
    // has reported itself. Clearing the banner regardless would throw away the
    // batch id, which is the only route back to this undo.
    const result = await operations.undoBatchById(batchId);
    if (result) {
      lastRun.value = null;
      migrationError.value = "";
      runSkipped.value = {};
    } else {
      migrationError.value =
        "Could not undo the move just now. The Undo is still here, try again in a moment.";
    }
  } finally {
    migrating.value = false;
    undoing.value = false;
    countStale.value = true;
    await refreshPreview();
  }
}

function treeIndent(steps) {
  return {
    paddingInlineStart: `calc(var(--space-4) + ${steps || 0} * var(--indent-step))`,
  };
}

/** `arriving` minus `leaving`, which is what the row has to say in one column. */
function netDelta(row) {
  return (row.arriving || 0) - (row.leaving || 0);
}
</script>

<template>
  <AppDialog
    :open="open"
    :width="620"
    :title="migrating ? `Moving onto ${layoutText}` : 'Choose a layout'"
    :persistent="migrating"
    @close="emit('close')"
  >
    <p v-if="unavailable" class="layout-dlg__sub">
      Choosing a layout is only available on the machine running PixlStash, or
      over your local network or Tailscale, because it decides where files are
      written on that machine.
    </p>

    <template v-else-if="loadError">
      <p class="layout-dlg__error">
        <v-icon size="15">mdi-alert-outline</v-icon> {{ loadError }}
      </p>
      <AppButton variant="secondary" size="sm" :loading="loading" @click="load">
        Try again
      </AppButton>
    </template>

    <template v-else>
      <p class="layout-dlg__sub">
        <template v-if="migrating">
          Stopping is safe at any point. Every file is at its old path or its
          new one, never in between.
        </template>
        <template v-else>
          Where new pictures get written, and if you want, where the ones you
          already have get moved to.
        </template>
      </p>

      <!-- The builder. Disabled outright while a run is going: a layout edited
           mid-run makes later passes re-plan against a new layout, so half the
           library lands on one and half on the other, under one undo batch
           that describes neither. -->
      <div
        class="layout-levels"
        role="group"
        aria-label="Folder levels"
        :aria-disabled="migrating ? 'true' : undefined"
        :class="{ 'layout-levels--frozen': migrating }"
      >
        <template v-for="(segment, index) in levels" :key="index">
          <span v-if="index > 0" class="layout-levels__sep" aria-hidden="true"
            >/</span
          >
          <div class="layout-level" :class="`layout-level--${index + 1}`">
            <v-select
              :model-value="segment"
              :items="itemsFor(index)"
              item-title="label"
              item-value="value"
              multiple
              density="compact"
              variant="outlined"
              hide-details
              :disabled="migrating"
              :label="`Level ${index + 1}`"
              placeholder="None"
              persistent-placeholder
              class="layout-level__select"
              @update:model-value="(value) => setLevel(index, value)"
            >
              <template #selection="{ index: i }">
                <span
                  v-if="i === 0"
                  class="layout-level__text"
                  :title="describeSegment(segment)"
                >
                  {{ describeSegment(segment) }}
                </span>
              </template>
            </v-select>
          </div>
        </template>
      </div>

      <!-- The unfiled folder is where a picture with nothing to file it by is
           written, sweep or no sweep; the sweep is only whether the ones
           already here are moved there too. -->
      <div class="layout-unfiled">
        <v-checkbox
          v-model="sweepUnfiled"
          label="Move unassigned pictures into"
          density="compact"
          hide-details
          :disabled="migrating"
        />
        <v-text-field
          :model-value="unfiled"
          density="compact"
          variant="outlined"
          hide-details
          class="layout-unfiled__name"
          aria-label="Folder for unassigned pictures"
          title="Where a picture with no project, person, set or tag is written"
          :disabled="migrating"
          @update:model-value="setUnfiled"
        />
      </div>

      <template v-if="isOn">
        <div class="layout-tree__head">
          <span>{{
            migrating ? "Filling in" : "Your folders, as this layout draws them"
          }}</span>
          <span>have · change</span>
        </div>
        <div
          class="layout-tree"
          role="table"
          aria-label="Folder preview: name, pictures now, change"
        >
          <div
            v-for="row in treeRows"
            :key="row.path"
            class="layout-tree__row"
            :class="{ 'layout-tree__row--new': row.is_new }"
            role="row"
          >
            <span
              class="layout-tree__name"
              role="cell"
              :style="treeIndent(row.indent)"
              :title="row.path"
            >
              <v-icon size="14">mdi-folder-outline</v-icon>
              <span class="layout-tree__path">
                <span v-if="row.crumbs" class="layout-tree__crumbs"
                  >{{ row.crumbs }} /&nbsp;</span
                >
                <span class="layout-tree__label">{{ row.name }}</span>
              </span>
              <span v-if="row.is_new" class="layout-tree__badge">new</span>
            </span>
            <span class="layout-tree__have" role="cell">{{
              row.is_new ? "—" : (row.have || 0).toLocaleString()
            }}</span>
            <span
              class="layout-tree__delta"
              role="cell"
              :class="{
                'layout-tree__delta--in': netDelta(row) > 0,
                'layout-tree__delta--out': netDelta(row) < 0,
                'layout-tree__delta--none': netDelta(row) === 0,
              }"
            >
              <template v-if="netDelta(row) > 0"
                >+{{ netDelta(row).toLocaleString() }}</template
              >
              <template v-else-if="netDelta(row) < 0"
                >−{{ Math.abs(netDelta(row)).toLocaleString() }}</template
              >
              <template v-else>unchanged</template>
            </span>
          </div>
          <div v-if="!treeRows.length" class="layout-tree__row" role="row">
            <span class="layout-tree__more" role="cell">
              {{ previewing ? "Reading your folders…" : "No folders to draw." }}
            </span>
          </div>
        </div>

        <ul v-if="flags.length && !migrating" class="layout-flags">
          <li v-for="flag in flags" :key="flag">
            <v-icon size="13">mdi-alert-outline</v-icon> {{ flag }}
          </li>
        </ul>

        <details v-if="!migrating" class="layout-never">
          <summary>What this layout will never do</summary>
          <ul>
            <li>
              Move a picture because you added a second project or person. The
              folder it is in is still true.
            </li>
            <li>
              Move anything you dragged into a folder of your own. There is
              nothing to contradict, so it stays. Only "Move them now" moves it.
            </li>
            <li>Delete a folder it empties.</li>
          </ul>
        </details>
      </template>

      <p v-if="error" class="layout-dlg__error">{{ error }}</p>
      <p v-if="migrationError" class="layout-dlg__error">
        {{ migrationError }}
      </p>
    </template>

    <template v-if="!unavailable && !loadError && isOn" #footer>
      <div class="layout-consequence">
        <template v-if="migrating">
          <div
            class="layout-consequence__count"
            role="status"
            aria-live="polite"
          >
            <template v-if="undoing">
              <div class="layout-consequence__num">
                Undoing… {{ lastRun.movedCount.toLocaleString() }}
                {{ lastRun.movedCount === 1 ? "picture" : "pictures" }}
              </div>
              <progress class="layout-consequence__bar" />
            </template>
            <template v-else>
              <div class="layout-consequence__num">
                Moving… {{ movedSoFar.toLocaleString() }} of
                {{ wouldMove.toLocaleString() }}
              </div>
              <progress
                class="layout-consequence__bar"
                :max="wouldMove || 1"
                :value="movedSoFar"
              />
            </template>
          </div>
          <div v-if="!undoing" class="layout-consequence__buttons">
            <AppButton
              variant="danger"
              size="sm"
              :disabled="cancelled"
              @click="stopRun"
            >
              Stop after this pass
            </AppButton>
          </div>
        </template>

        <template v-else>
          <div
            class="layout-consequence__count"
            role="status"
            aria-live="polite"
          >
            <template v-if="previewing || countStale">
              <div class="layout-consequence__num">counting…</div>
            </template>
            <template v-else-if="lastRun">
              <div class="layout-consequence__num">
                Moved {{ lastRun.movedCount.toLocaleString() }}
                {{ lastRun.movedCount === 1 ? "picture" : "pictures" }}
              </div>
              <div class="layout-consequence__sub">
                {{
                  wouldMove
                    ? `${wouldMove.toLocaleString()} still do not match`
                    : "one undo puts every file back"
                }}
              </div>
            </template>
            <template v-else-if="wouldMove">
              <div class="layout-consequence__num">
                {{ wouldMove.toLocaleString() }}
                {{ wouldMove === 1 ? "picture" : "pictures" }} would move
              </div>
              <div class="layout-consequence__sub">
                into {{ folderCount.toLocaleString() }}
                {{ folderCount === 1 ? "folder" : "folders" }} · nothing has
                moved yet
              </div>
            </template>
            <template v-else>
              <div class="layout-consequence__num">Nothing to move</div>
              <div class="layout-consequence__sub">
                every picture is already where this layout would put it
              </div>
            </template>
          </div>
          <div class="layout-consequence__buttons">
            <AppButton
              v-if="lastRun"
              variant="secondary"
              size="sm"
              icon-left="undo"
              @click="undoMigration"
            >
              Undo
            </AppButton>
            <AppButton variant="secondary" size="sm" @click="emit('close')">
              Keep layout, move nothing
            </AppButton>
            <AppButton
              variant="primary_green"
              size="sm"
              icon-left="folder-move-outline"
              :disabled="!canMove"
              @click="migrate"
            >
              {{ lastRun ? "Move the rest" : "Move them now" }}
            </AppButton>
          </div>
        </template>
      </div>
    </template>
  </AppDialog>
</template>

<style scoped>
.layout-dlg__sub {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  margin: 0 0 var(--space-5);
}

.layout-dlg__error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  margin: var(--space-3) 0 0;
}

/* ---- the level builder ---------------------------------------------------
   Four levels on one line at 620px. See MAX_LEVELS in the script for the
   width budget; the basis is what holds the four together and what makes a
   narrower box wrap tidily instead of overflowing. */
.layout-levels {
  display: flex;
  flex-wrap: wrap;
  /* `stretch`, not `center`: a filled select is taller than an empty one (its
     label floats above the value rather than sitting in it), and centring two
     different heights puts the four boxes on four different baselines on one
     line. Measured in a browser at 620px: `center` gave 2 distinct tops at
     one, two and three filled levels, 1 at four. */
  align-items: stretch;
  gap: var(--space-2);
  margin-bottom: var(--space-5);
}

.layout-levels--frozen {
  opacity: var(--opacity-disabled);
}

.layout-levels__sep {
  flex: none;
  align-self: center;
  color: rgba(var(--v-theme-on-surface), 0.35);
}

.layout-level {
  flex: 1 1 120px;
  min-width: 0;
}

/* One hue per level, from the theme's `level-1..4` (main.js). Colour is a
   second cue only: the level number is on the select's own label and stays
   there whatever the hue does. The wash is a tint of the same hue rather than
   a second value, so a palette change moves both together; its alpha is
   `--level-wash` (style.css), which is deeper in dark because 0.10 over
   `input-background` there reads as a smudge. */
.layout-level--1 {
  --level-hue: rgb(var(--v-theme-level-1));
  --level-tint: rgba(var(--v-theme-level-1), var(--level-wash));
}

.layout-level--2 {
  --level-hue: rgb(var(--v-theme-level-2));
  --level-tint: rgba(var(--v-theme-level-2), var(--level-wash));
}

.layout-level--3 {
  --level-hue: rgb(var(--v-theme-level-3));
  --level-tint: rgba(var(--v-theme-level-3), var(--level-wash));
}

.layout-level--4 {
  --level-hue: rgb(var(--v-theme-level-4));
  --level-tint: rgba(var(--v-theme-level-4), var(--level-wash));
}

/* The value row is narrow by design; a long label ellipsises and keeps the
   full text in its `title`. Vuetify's default 16px field inset is what would
   otherwise cost the fourth level its label. */
.layout-level__select :deep(.v-field__input) {
  padding-inline: var(--space-3);
  min-height: 34px;
}

.layout-level__text {
  font-size: var(--text-sm);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.layout-level__select :deep(.v-field__outline) {
  --v-field-border-opacity: 1;
  color: var(--level-hue);
}

.layout-level__select :deep(.v-field) {
  background: var(--level-tint);
  border-radius: var(--radius-sm);
}

.layout-level__select :deep(.v-label) {
  color: var(--level-hue);
  opacity: 1;
}

/* ---- the unfiled folder -------------------------------------------------- */
.layout-unfiled {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: var(--space-3) 0 var(--space-5);
}

.layout-unfiled__name {
  flex: 0 1 180px;
}

.layout-unfiled__name :deep(.v-field__input) {
  padding-inline: var(--space-3);
  min-height: 34px;
  font-size: var(--text-sm);
}

/* ---- the tree ------------------------------------------------------------ */
.layout-tree__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-4);
  margin: 0 0 var(--space-2);
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.layout-tree {
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  max-height: 320px;
  overflow-y: auto;
  background: rgb(var(--v-theme-background));
  font-variant-numeric: tabular-nums;
}

.layout-tree__row {
  display: grid;
  grid-template-columns: 1fr 6ch 9ch;
  gap: var(--space-4);
  align-items: center;
  padding: var(--space-3) var(--space-4) var(--space-3) 0;
  font-size: var(--text-sm);
}

.layout-tree__row + .layout-tree__row {
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.layout-tree__name {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

/* The row is the folder plus whatever of its parentage no row above it says.
   The breadcrumb is the part that may be cut, so it is the only part that
   shrinks: the leaf is what identifies the row and always shows in full. */
.layout-tree__path {
  display: flex;
  min-width: 0;
  white-space: nowrap;
}

.layout-tree__crumbs {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.layout-tree__label {
  flex: none;
}

.layout-tree__row--new .layout-tree__label {
  font-weight: var(--weight-medium);
}

.layout-tree__badge {
  flex: none;
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-primary));
  border: 1px solid rgb(var(--v-theme-primary));
  border-radius: var(--radius-pill);
  padding: 0 var(--space-2);
}

.layout-tree__have {
  text-align: right;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.layout-tree__delta {
  text-align: right;
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
}

.layout-tree__delta--in {
  color: rgb(var(--v-theme-primary));
}

.layout-tree__delta--out {
  color: rgb(var(--v-theme-accent));
}

.layout-tree__delta--none {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-weight: var(--weight-regular);
}

.layout-tree__more {
  grid-column: 1 / -1;
  padding-inline-start: var(--space-4);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* ---- flags and the disclosure -------------------------------------------- */
.layout-flags {
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.layout-flags li {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-warning), 0.14);
}

.layout-flags :deep(.v-icon) {
  color: rgb(var(--v-theme-warning));
}

.layout-never {
  margin-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding-top: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.layout-never summary {
  cursor: pointer;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.layout-never ul {
  margin: var(--space-3) 0 0;
  padding-inline-start: var(--space-5);
  line-height: var(--leading-body);
}

/* ---- the consequence bar -------------------------------------------------
   One row: the count on the left, both buttons together on the right. The
   count wraps to its own line before the buttons ever separate, because
   "Keep layout, move nothing" is a real choice and has to sit beside the one
   it is a choice against. */
.layout-consequence {
  flex: 1;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
}

.layout-consequence__count {
  flex: 1 1 200px;
  min-width: 0;
}

.layout-consequence__num {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  line-height: var(--leading-tight);
}

.layout-consequence__sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  margin-top: var(--space-1);
}

.layout-consequence__bar {
  width: 100%;
  height: var(--countdown-h);
  margin-top: var(--space-2);
  accent-color: rgb(var(--v-theme-primary));
}

.layout-consequence__buttons {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
</style>
