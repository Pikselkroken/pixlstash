<script setup>
/**
 * How your folders are laid out - the library's own picture root (v1.11
 * Phases 4b and 4c). The Storage artboard in `design/1.11-existing-library/`.
 *
 * Two things live here and they must not be confused, because one of them is
 * the release's headline promise:
 *
 * 1. **Choosing a layout moves no files.** Every path already in the library is
 *    what its assignments were read from, so every path is already true. What
 *    the layout decides is where a *new* picture is written, and where one goes
 *    when the folder it sits in stops describing it. The copy next to the
 *    builder is a table of what does and does not move - never a warning.
 * 2. **Moving the existing library onto the layout is a separate gesture**, at
 *    the bottom of the pane, previewed and consented to. It is offered when a
 *    layout is set or changed and it is never taken automatically.
 *
 * Gated behind `isReadOnly === false` at the tab level in UserSettingsDialog,
 * and behind the registry's `can_manage` here - the routes are on the §16.3
 * locality tier, so a remote owner gets the pane's sentence rather than a 403
 * on controls they cannot use.
 *
 * A library switch reloads the page (`useLibrariesStore.begin` -> `reloadPage`),
 * so nothing here has to survive one: the pane cannot be showing library A's
 * layout while `/server-config/layout` addresses library B.
 */
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { VIcon, VSelect } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsInfoCard from "./SettingsInfoCard.vue";
import SettingsRow from "./SettingsRow.vue";
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
  layoutExamples,
  parseLayout,
} from "../../utils/libraryLayout";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const libraries = useLibrariesStore();
const operations = useOperationStore();

const loading = ref(false);
const loaded = ref(false);
const saving = ref(false);
const error = ref("");
/**
 * The initial read failed. Kept apart from `error` because it decides whether
 * the builder may be shown at all: an empty `segments` after a failed GET looks
 * exactly like "this library has no layout", and the next click would PATCH a
 * layout over one nobody has read.
 */
const loadError = ref("");
const refused = ref(false);

/** `[["project"], ["person", "set"]]`, the builder's own model. */
const segments = ref([]);
const unfiled = ref("_Inbox");
const defaultLayout = ref("project/person,set");
const builder = ref(null);

const blocked = computed(
  () => libraries.hasLoadedSuccessfully && !libraries.canManage,
);
const unavailable = computed(() => blocked.value || refused.value);
const isOn = computed(() => segments.value.length > 0);
const layoutText = computed(() => formatLayout(segments.value));
const isDefault = computed(() => layoutText.value === defaultLayout.value);
const examples = computed(() => layoutExamples(segments.value, unfiled.value));
/** A level can only be added while some facet is still unspoken for. */
const spareFacet = computed(() => {
  const used = new Set(segments.value.flat());
  return LAYOUT_FACETS.find((facet) => !used.has(facet.value)) || null;
});

function applySettings(body) {
  segments.value = parseLayout(body.layout);
  unfiled.value = body.layout_unfiled || "_Inbox";
  if (body.default_layout) defaultLayout.value = body.default_layout;
}

async function load() {
  loading.value = true;
  error.value = "";
  loadError.value = "";
  refused.value = false;
  try {
    applySettings(await getLayoutSettings());
    loaded.value = true;
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
// flight when the pane opens.
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
 * facets in one dropdown would otherwise be three writes and three full scans.
 * The model updates immediately so the control stays live; only the request
 * waits.
 */
const SAVE_DEBOUNCE_MS = 500;
let saveTimer = null;
/** Bumped per edit, so a response for a superseded edit is not applied. */
let editSeq = 0;

function scheduleSave(next) {
  segments.value = next;
  editSeq += 1;
  const seq = editSeq;
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(
    () => save({ layout: formatLayout(next) }, seq),
    SAVE_DEBOUNCE_MS,
  );
}

onUnmounted(() => {
  window.clearTimeout(saveTimer);
  // Stops the pass loop below: the run is resumable, so abandoning it mid-way
  // is safe and finishing it is one more click.
  cancelled = true;
});

async function save(next, seq = ++editSeq) {
  saving.value = true;
  error.value = "";
  try {
    const body = await setLayoutSettings(next);
    // A newer edit is already queued: applying this response would snap the
    // builder back to the layout the owner has moved on from.
    if (seq !== editSeq) return;
    applySettings(body);
    // A new layout is a new question, so whatever the previous run reported is
    // no longer about anything on screen.
    resetRun();
    // "Offered whenever a layout is set or changed" - the offer is this
    // preview, so it is re-read on every successful save rather than only on
    // open. A layout the owner has just narrowed can move a different set of
    // files than the one before it.
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
  } finally {
    saving.value = false;
  }
}

function setSegment(index, facets) {
  const next = segments.value.map((segment, i) =>
    i === index ? facets : segment,
  );
  // A segment emptied by clearing its last facet is a level the owner has
  // removed, not an empty folder to render: dropping it here is what the
  // grammar does anyway, and leaving it would send a layout with a hole in it.
  scheduleSave(next.filter((segment) => segment.length > 0));
}

function addSegment() {
  // Guarded rather than defaulted. A fallback here appends a facet that is
  // already in the layout - `project,person / set,tag / tag` is expressible,
  // renders `portrait/portrait/` on disk, and the backend takes it verbatim.
  if (!spareFacet.value) return;
  scheduleSave([...segments.value, [spareFacet.value.value]]);
}

async function removeSegment(index) {
  scheduleSave(segments.value.filter((_, i) => i !== index));
  // The button that had focus has just been unmounted; without this, focus
  // falls to <body> and a keyboard user loses their place in the builder.
  await nextTick();
  const buttons = builder.value?.querySelectorAll("button") || [];
  buttons[Math.min(index, buttons.length - 1)]?.focus();
}

// ---------------------------------------------------------------------------
// Phase 4c - moving the existing library onto this layout
// ---------------------------------------------------------------------------

const preview = ref(null);
const previewing = ref(false);
const migrating = ref(false);
const migrationError = ref("");
/** `{movedCount, batchId}` once a run finishes, so Undo has something to undo. */
const lastRun = ref(null);
const movedSoFar = ref(0);
/**
 * What the run refused, by reason, accumulated over every pass.
 *
 * The API reports these per pass and the backend has a comment saying the
 * caller has to be told: a file locked on Windows, or a name that appeared at
 * the destination since the plan, is reported as `move_failed` rather than
 * moved. Dropping it would let a run that could not touch 500 files report a
 * clean "Moved 3,609 pictures".
 */
const runSkipped = ref({});
let cancelled = false;

/** Non-zero only when something would actually move. */
const wouldMove = computed(() => preview.value?.picture_count || 0);
const crossVolume = computed(() => preview.value?.cross_volume_count || 0);
/** Refusals worth naming, whichever half of the flow produced them. */
const refusals = computed(() => {
  const counts = { ...(preview.value?.skipped_counts || {}) };
  for (const [reason, count] of Object.entries(runSkipped.value)) {
    counts[reason] = (counts[reason] || 0) + count;
  }
  // Cross-volume has its own sentence, so it is not repeated in the list.
  delete counts.destination_other_volume;
  return counts;
});
const refusalTotal = computed(() =>
  Object.values(refusals.value).reduce((sum, n) => sum + n, 0),
);

const REFUSAL_LABELS = {
  move_failed: "could not be moved just now",
  destination_taken: "would land on a name that is taken",
  source_file_missing: "are not on disk where the library records them",
  source_is_symlink: "are links rather than files",
  path_outside_root: "are outside the library folder",
  destination_outside_root: "would land outside the library folder",
};

function resetRun() {
  lastRun.value = null;
  migrationError.value = "";
  runSkipped.value = {};
}

async function refreshPreview() {
  if (!isOn.value) {
    preview.value = null;
    return;
  }
  previewing.value = true;
  try {
    preview.value = await getLayoutMigrationPreview();
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

function recount() {
  resetRun();
  refreshPreview();
}

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
  migrating.value = true;
  resetRun();
  movedSoFar.value = 0;
  cancelled = false;
  let cursor = 0;
  let batchId = null;
  try {
    for (;;) {
      const pass = await runLayoutMigrationPass({ afterId: cursor, batchId });
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
    // the owner has to act on, so a run that fails with a specific message must
    // not lose it. Half-moved is a valid state here and the copy has to say so,
    // or it reads as damage.
    const detail = errorDetail(err) || err?.message || "";
    migrationError.value = `${
      detail
        ? `The move stopped part way: ${detail}.`
        : "The move stopped part way."
    } Nothing is half-written - press Move again to finish it.`;
    if (batchId) lastRun.value = { movedCount: movedSoFar.value, batchId };
  } finally {
    migrating.value = false;
    await refreshPreview();
  }
}

async function undoMigration() {
  const batchId = lastRun.value?.batchId;
  if (!batchId) return;
  migrating.value = true;
  try {
    // `undoBatchById` answers `null` rather than throwing when it refuses - a
    // read-only session, another operation already in flight, or a failure it
    // has reported itself. Clearing the banner regardless would throw away the
    // batch id, which is the only route back to this undo.
    const result = await operations.undoBatchById(batchId);
    if (result) {
      resetRun();
    } else {
      migrationError.value =
        "Could not undo the move just now. The Undo is still here - try again in a moment.";
    }
  } finally {
    migrating.value = false;
    await refreshPreview();
  }
}
</script>

<template>
  <div class="layout-pane">
    <SettingsSection
      title="How your folders are laid out"
      desc="Where a new picture is written, and the one rule that decides when an existing one is ever moved again."
      first
    >
      <template v-if="isOn && !unavailable" #action>
        <AppButton
          variant="ghost"
          size="sm"
          :disabled="saving || migrating"
          @click="save({ layout: null })"
        >
          Turn off
        </AppButton>
      </template>

      <!-- The same locality rule as the library controls, and deliberately the
           same wording, because it is the same answer. -->
      <SettingsInfoCard v-if="unavailable">
        Choosing a layout is only available on the machine running PixlStash, or
        over your local network or Tailscale, because it decides where files are
        written on that machine.
      </SettingsInfoCard>

      <template v-else>
        <SettingsInfoCard v-if="loadError">
          <v-icon size="15">mdi-alert-outline</v-icon>
          {{ loadError }}
          <AppButton variant="ghost" size="sm" :loading="loading" @click="load">
            Try again
          </AppButton>
        </SettingsInfoCard>

        <!-- The builder is behind the read, not beside it: an empty `segments`
             after a failed GET is indistinguishable from "no layout", and the
             next click would write one over a layout nobody has read. -->
        <div
          v-else
          ref="builder"
          class="layout-builder"
          role="group"
          aria-label="How your folders are laid out"
        >
          <template v-for="(segment, index) in segments" :key="index">
            <span
              v-if="index > 0"
              class="layout-builder__sep"
              aria-hidden="true"
              >/</span
            >
            <div class="layout-seg">
              <v-select
                :model-value="segment"
                :items="LAYOUT_FACETS"
                item-title="label"
                item-value="value"
                multiple
                density="compact"
                variant="outlined"
                hide-details
                :label="`Level ${index + 1}`"
                class="layout-seg__select"
                @update:model-value="(value) => setSegment(index, value)"
              >
                <template #selection="{ index: i }">
                  <span v-if="i === 0" class="layout-seg__text">
                    {{ describeSegment(segment) }}
                  </span>
                </template>
              </v-select>
              <button
                type="button"
                class="layout-seg__remove"
                :aria-label="`Remove the ${describeSegment(segment)} level`"
                @click="removeSegment(index)"
              >
                <v-icon size="14">mdi-close</v-icon>
              </button>
            </div>
          </template>
          <AppButton
            v-if="spareFacet"
            variant="ghost"
            size="sm"
            icon-left="mdi-plus"
            @click="addSegment"
          >
            {{ isOn ? "add one" : "Choose a layout…" }}
          </AppButton>
        </div>

        <dl v-if="isOn && !loadError" class="layout-examples">
          <template v-for="example in examples" :key="example.caption">
            <dt>{{ example.caption }}</dt>
            <dd>{{ example.folder }}</dd>
          </template>
        </dl>

        <p v-if="!loadError" class="layout-hint">
          A segment can hold more than one thing, and the first that applies
          wins. A segment with nothing to fill it is skipped rather than left as
          an empty folder, which is what keeps the tree two deep instead of
          five.
          <template v-if="isOn && !isDefault">
            A new, empty library starts on
            {{ defaultLayout.replace(/[/]/g, " / ").replace(/,/g, " or ") }}.
            <AppButton
              variant="ghost"
              size="sm"
              @click="scheduleSave(parseLayout(defaultLayout))"
            >
              Use that
            </AppButton>
          </template>
          <template v-else-if="isOn">
            A new, empty library starts on exactly this layout.
          </template>
        </p>

        <div v-if="error" class="settings-error">{{ error }}</div>
      </template>
    </SettingsSection>

    <SettingsSection
      v-if="isOn && !unavailable"
      title="A picture only moves when its folder stops being true"
      desc="Not whenever something about it changes. Only when the folder it is sitting in would otherwise be saying something that is no longer the case."
    >
      <table class="layout-table">
        <thead>
          <tr>
            <th scope="col">You do this</th>
            <th scope="col">Is the folder still true?</th>
            <th scope="col">Files moved</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Import your library</td>
            <td>
              true the moment it is written — the folder is where the assignment
              came from
            </td>
            <td>none, ever</td>
          </tr>
          <tr>
            <td>Add a second project, or a second person</td>
            <td>yes. It is still in the first one</td>
            <td>none</td>
          </tr>
          <tr>
            <td>Rename a project</td>
            <td>yes, under a new name</td>
            <td>none — the folder is renamed where it can be</td>
          </tr>
          <tr>
            <td>Remove the project its folder is named after</td>
            <td>no</td>
            <td>moves</td>
          </tr>
          <tr>
            <td>Swap one project for another</td>
            <td>no</td>
            <td>moves</td>
          </tr>
        </tbody>
      </table>
      <ul class="layout-checks">
        <li>One undo puts every file back</li>
        <li>Two changes in a row are one move, not two</li>
        <li>A folder left empty is kept, never deleted</li>
      </ul>
      <SettingsInfoCard>
        <strong>A folder that does not match the layout is never wrong.</strong>
        Drag something into a folder of your own and there is nothing for
        PixlStash to contradict, so it stays there. Permanently. That is the
        override, and it needs no setting.
      </SettingsInfoCard>
    </SettingsSection>

    <SettingsSection
      v-if="isOn && !unavailable"
      title="New pictures with nothing to file them by"
      desc="No project, no person, nothing. They land here, and leave on their own the moment you give them one."
    >
      <SettingsRow label="Unfiled folder" :sub="unfiled" />
    </SettingsSection>

    <!-- Phase 4c. Its own section, below the rule above and visibly not part of
         it: everything above moves nothing, and this moves everything. -->
    <SettingsSection
      v-if="isOn && !unavailable"
      title="Move your library onto this layout"
      desc="The rule above never touches a file that is already here — a folder it cannot read can never stop being true. This is the other thing: rearrange what is already in the library, now, in one go."
    >
      <!-- One live region for the whole flow. A whole-library file move that
           reports "Counting…", "Moving… 3,200 of 4,109" and "Moved N" in
           silence is a multi-minute operation a screen-reader user has no
           account of. LibrariesSection does the same beside this pane. -->
      <div class="layout-migrate__live" role="status" aria-live="polite">
        <div v-if="previewing" class="layout-migrate__status">Counting…</div>

        <template v-else-if="migrating">
          <div class="layout-migrate__status">
            Moving… {{ movedSoFar.toLocaleString() }} of
            {{ wouldMove.toLocaleString() }}
          </div>
          <progress
            class="layout-migrate__bar"
            :max="wouldMove || 1"
            :value="movedSoFar"
          />
        </template>

        <template v-else-if="lastRun">
          <div class="layout-migrate__status">
            Moved {{ lastRun.movedCount.toLocaleString() }}
            {{ lastRun.movedCount === 1 ? "picture" : "pictures" }}.
            <template v-if="wouldMove">
              {{ wouldMove.toLocaleString() }} still
              {{ wouldMove === 1 ? "does" : "do" }} not match — run it again to
              finish.
            </template>
          </div>
        </template>

        <div v-else-if="wouldMove === 0" class="layout-migrate__status">
          <template v-if="crossVolume">
            Nothing here can be moved onto this layout.
          </template>
          <template v-else>
            Every picture is already where this layout would put it, or is
            somewhere the layout does not decide. Nothing to move.
          </template>
        </div>

        <div v-else class="layout-migrate__count">
          <strong>{{ wouldMove.toLocaleString() }}</strong>
          {{ wouldMove === 1 ? "picture" : "pictures" }} would move into
          <strong>{{ preview.folder_count.toLocaleString() }}</strong>
          {{ preview.folder_count === 1 ? "folder" : "folders" }}. Nothing has
          moved yet.
        </div>
      </div>

      <dl v-if="!migrating && wouldMove" class="layout-samples">
        <template v-for="sample in preview.samples" :key="sample.picture_id">
          <dt>{{ sample.from }}</dt>
          <dd><v-icon size="15">mdi-arrow-right</v-icon> {{ sample.to }}</dd>
        </template>
      </dl>

      <SettingsInfoCard v-if="!migrating && preview?.collision_count">
        <v-icon size="15">mdi-alert-outline</v-icon>
        {{ preview.collision_count.toLocaleString() }} would land on a name
        something already has, so they get <code>-2</code>, <code>-3</code> and
        so on — for example <code>{{ preview.collisions[0]?.to }}</code
        >. The file already sitting there is never renamed and never
        overwritten.
      </SettingsInfoCard>

      <!-- Outside the "something would move" branch on purpose. A library whose
           every candidate is across a mount point counts zero movable pictures,
           and saying "nothing to move" there without this would be false about
           the one case the check exists for. -->
      <SettingsInfoCard v-if="!migrating && crossVolume">
        <v-icon size="15">mdi-alert-outline</v-icon>
        {{ crossVolume.toLocaleString() }} sit on a different drive from where
        this layout would put them — a mount point inside your library — and
        <strong>cannot be moved</strong>. They stay exactly where they are, and
        the count above does not include them.
      </SettingsInfoCard>

      <!-- Every refusal the preview or the run reported. The API reports these
           per pass precisely so the owner is not told a run finished cleanly
           over files it never touched. -->
      <SettingsInfoCard v-if="!migrating && refusalTotal">
        <v-icon size="15">mdi-alert-outline</v-icon>
        Left where they are:
        <template v-for="(count, reason) in refusals" :key="reason">
          {{ count.toLocaleString() }}
          {{ REFUSAL_LABELS[reason] || `were refused (${reason})` }};
        </template>
      </SettingsInfoCard>

      <div v-if="!migrating" class="layout-migrate__actions">
        <AppButton
          v-if="lastRun"
          variant="secondary"
          size="sm"
          icon-left="mdi-undo"
          @click="undoMigration"
        >
          Undo
        </AppButton>
        <AppButton
          v-if="wouldMove"
          variant="primary_green"
          size="sm"
          icon-left="mdi-folder-move-outline"
          @click="migrate"
        >
          {{ lastRun ? "Move the rest" : "Move them now" }}
        </AppButton>
        <AppButton variant="ghost" size="sm" @click="recount">
          Re-count
        </AppButton>
        <span v-if="lastRun" class="layout-migrate__note">
          One undo, for the whole move — every file goes back to the path it
          had.
        </span>
      </div>

      <div v-if="migrationError" class="settings-error">
        {{ migrationError }}
      </div>
    </SettingsSection>
  </div>
</template>

<style scoped>
.layout-builder {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.layout-builder__sep {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.layout-seg {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.layout-seg__select {
  min-width: 168px;
}

.layout-seg__text {
  font-size: var(--text-sm);
  white-space: nowrap;
}

.layout-seg__remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), 0.55);
  background: transparent;
  cursor: pointer;
}

.layout-seg__remove:hover:not(:disabled) {
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-surface));
}

.layout-seg__remove:disabled {
  opacity: 0.4;
  cursor: default;
}

.layout-examples,
.layout-samples {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-1) var(--space-4);
  margin: 0 0 var(--space-3);
  font-size: var(--text-xs);
}

.layout-examples dt,
.layout-samples dt {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.layout-examples dd,
.layout-samples dd {
  margin: 0;
  font-family: var(--font-mono);
}

.layout-samples dt {
  font-family: var(--font-mono);
}

.layout-hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin: 0 0 var(--space-2);
}

.layout-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
  margin-bottom: var(--space-3);
}

.layout-table th,
.layout-table td {
  text-align: left;
  padding: var(--space-2) var(--space-3) var(--space-2) 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  vertical-align: top;
}

.layout-table th {
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.layout-table td:last-child {
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
}

.layout-checks {
  list-style: none;
  padding: 0;
  margin: 0 0 var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.layout-checks li::before {
  content: "✓";
  color: rgb(var(--v-theme-success));
  margin-right: var(--space-2);
}

.layout-migrate__count,
.layout-migrate__status {
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  margin-bottom: var(--space-3);
}

.layout-migrate__bar {
  width: 100%;
  height: 4px;
  margin-bottom: var(--space-3);
  accent-color: rgb(var(--v-theme-accent));
}

.layout-migrate__actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  margin-top: var(--space-3);
}

.layout-migrate__note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.settings-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  margin-top: var(--space-2);
}
</style>
