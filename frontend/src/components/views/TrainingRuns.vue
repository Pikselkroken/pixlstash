<template>
  <div
    ref="rootEl"
    class="tr"
    role="region"
    tabindex="-1"
    aria-label="ai-toolkit training runs"
  >
    <div class="tr-toolbar">
      <div class="tr-tb-left">
        <AiToolkitIcon :size="20" class="tr-mark" />
        <span class="tr-title">Training runs</span>
        <span v-if="subtitle" class="tr-sub">{{ subtitle }}</span>
      </div>

      <div class="tr-tb-right">
        <!-- The path is the only thing on screen that says WHICH folder these
             runs came from, and it is read-only here: changing it is a folder
             setting, and the folders dialog owns it. -->
        <span v-if="source" class="tr-path" :title="source.path">{{
          source.path
        }}</span>
        <!-- The view reloads itself whenever the tab is looked at again, which
             is the shape of the real workflow: leave PixlStash, train, come
             back. This button is for the other one — both windows visible at
             once, so focus never changes and nothing fires. No badge on it: the
             only way to know a run had appeared would be to poll the listing,
             and polling every run's checkpoints and samples to light a dot
             costs more than the button it would save. -->
        <AppButton
          size="sm"
          variant="ghost"
          icon-left="refresh"
          :loading="loading"
          title="Look for runs that have appeared since this list was read"
          aria-label="Reload the training runs"
          @click="reload"
        />
      </div>
    </div>

    <p v-if="!source" class="tr-note" role="status">
      No ai-toolkit output folder is set yet. Open
      <strong>Model folders</strong> and choose
      <strong>Set ai-toolkit folder</strong>, and the runs under it will be
      listed here.
    </p>
    <p v-else-if="loading && !runs.length" class="tr-note" role="status">
      Reading runs…
    </p>
    <p v-else-if="error" class="tr-note" role="alert">{{ error }}</p>
    <p v-else-if="!runs.length" class="tr-note" role="status">
      Nothing in that folder looks like a training run yet.
    </p>

    <!-- One card per run. `role="radiogroup"`, because an import takes ONE run:
         the destination, the step selection and the receipt are all per-run,
         and letting two be ticked would promise a batch nothing implements. -->
    <div
      v-else
      ref="gridEl"
      class="tr-grid"
      role="radiogroup"
      aria-label="Training runs"
      @keydown="onGridKeydown"
    >
      <div
        v-for="(run, index) in runs"
        :key="run.name"
        class="tr-card"
        :class="{ 'tr-card--picked': run.name === pickedName }"
        role="radio"
        :aria-checked="run.name === pickedName"
        :tabindex="run.name === (pickedName || runs[0].name) ? 0 : -1"
        :data-run-index="index"
        @click="pick(run)"
        @keydown.enter.prevent="pick(run)"
        @keydown.space.prevent="pick(run)"
      >
        <!-- The first prompt at the run's highest step: what it has learned so
             far, on a prompt that stays the same across runs so two cards are
             comparable (see `coverOf`). `loading="lazy"`, because a run carries
             up to 130 samples and only the visible cards need to fetch. -->
        <img
          v-if="coverOf(run)"
          class="tr-card-preview"
          :src="coverOf(run)"
          alt=""
          loading="lazy"
        />
        <div v-else class="tr-card-preview tr-card-preview--none">
          <v-icon size="20">mdi-image-off-outline</v-icon>
          <span>No previews</span>
        </div>

        <div class="tr-card-body">
          <span class="tr-card-name">{{ run.name }}</span>
          <span class="tr-card-meta">
            <span>{{ stepCount(run) }}</span>
            <span>{{ run.base_model || "Base model not recorded" }}</span>
            <span v-if="run.rank">rank {{ run.rank }}</span>
          </span>
          <!-- An unconfirmed cover is a fact about the run, not a warning about
               the user: ai-toolkit writes a bare final file at the end, so a run
               without one either is still training or was interrupted. The
               highest step is then the best available answer, not a certain
               one, and saying so is the difference between a surprise and a
               choice. -->
          <span v-if="!hasBareFinal(run)" class="tr-card-note">
            No final file yet, so the newest step is the cover.
          </span>
          <span v-if="run.config_error" class="tr-card-note">
            Could not read its config, so the base model and triggers are
            unknown. The steps still import.
          </span>
        </div>
      </div>
    </div>

    <!-- The import controls appear only once a run is picked: before that they
         would describe nothing, and after it they are the only decisions left.
         A bar pinned to the foot of the view rather than a dialog footer,
         because the grid above it stays live and readable while they are used. -->
    <div v-if="picked" class="tr-bar">
      <fieldset class="tr-field tr-steps">
        <legend class="tr-label">Take which checkpoints</legend>
        <label
          v-for="cp in picked.checkpoints"
          :key="cp.filename"
          class="tr-step"
        >
          <input
            v-model="chosenSteps"
            type="checkbox"
            :value="cp.step ?? null"
            :disabled="working"
          />
          <span>{{ cp.step === null ? "Final" : `Step ${cp.step}` }}</span>
          <span class="tr-step-size">{{ formatModelSize(cp.size) }}</span>
        </label>
      </fieldset>

      <label class="tr-field">
        <span class="tr-label">Put them in</span>
        <select v-model="destinationId" class="tr-select" :disabled="working">
          <option
            v-for="folder in destinations"
            :key="folder.id"
            :value="folder.id"
          >
            {{ folder.path }}
          </option>
        </select>
      </label>

      <!-- The one thing about an import that cannot be undone, said before it
           starts rather than in the receipt. It follows the SOURCE folder's own
           setting, so it is a property of where the run lives and not a choice
           being made here. -->
      <p v-if="deletesSource" class="tr-warning" role="status">
        This folder is set to remove a run after importing it, so
        {{ picked.name }} will be gone from disk once its files have landed.
      </p>

      <div class="tr-bar-actions">
        <AppButton variant="ghost" @click="clearPick">Cancel</AppButton>
        <AppButton
          variant="primary"
          :loading="working"
          :disabled="!canSubmit"
          @click="submit"
        >
          {{ confirmLabel }}
        </AppButton>
      </div>
    </div>
  </div>
</template>

<script setup>
// The ai-toolkit training runs, and the import that takes one onto the shelf
// (shelf plan F6).
//
// A VIEW and not a dialog, which is the whole reason it can stay current. A
// dialog is opened, read once and dismissed, so a run that finished while it
// was open was invisible until it was closed and reopened. The folder this
// reads is still set in a dialog — that is a setting, and a dialog is the right
// place to set what a folder IS. What is inside the folder is not a setting,
// and it changes without PixlStash doing anything, so it gets a view that
// reloads: on entry, and whenever the tab is looked at again.
//
// Built on the promise the listing route makes: describing a run costs nothing
// and changes nothing, so the whole grid — names, steps, sizes, previews, what
// the config says it trained against — is drawn before the user commits to any
// of it. Nothing here hashes, copies or writes until Import is pressed. That is
// also what makes reloading free enough to do on every focus.
//
// One run at a time, deliberately. The destination, the step selection and the
// receipt are all per-run, so a multi-select would promise a batch that
// `POST /model-imports` does not implement.

import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { VIcon } from "vuetify/components";

import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import AppButton from "../widgets/AppButton.vue";
import { importRun, listRuns, runSampleUrl } from "../../api/modelImports";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { formatModelSize, importReceipt } from "../../utils/modelShelf";

const folders = useModelFoldersStore();
const shelf = useModelShelfStore();

const rootEl = ref(null);
const gridEl = ref(null);
const destinationId = ref(null);
const pickedName = ref("");
const chosenSteps = ref([]);
const runs = ref([]);
const loading = ref(false);
const working = ref(false);
const error = ref("");

/** The registered ai-toolkit output root. One, by the store's own rule. */
const source = computed(() => folders.sourceFolder);

/**
 * Where an import may land.
 *
 * The same two exclusions a move applies, and for the same reasons: a `source`
 * folder is taken from rather than written into (the server refuses it), and an
 * `external` folder is shared with other software.
 */
const destinations = computed(() =>
  folders.folders.filter(
    (folder) => folder.kind !== "source" && folder.movable !== "external",
  ),
);

const picked = computed(() =>
  runs.value.find((run) => run.name === pickedName.value),
);

const deletesSource = computed(() =>
  Boolean(source.value?.delete_after_import),
);

const subtitle = computed(() =>
  runs.value.length
    ? `${runs.value.length.toLocaleString()} ${runs.value.length === 1 ? "run" : "runs"}`
    : "",
);

const confirmLabel = computed(() => {
  const n = chosenSteps.value.length;
  if (!picked.value || !n) return "Import";
  return `Import ${n.toLocaleString()} ${n === 1 ? "file" : "files"}`;
});

const canSubmit = computed(
  () =>
    !working.value &&
    Boolean(picked.value) &&
    chosenSteps.value.length > 0 &&
    destinationId.value != null,
);

/**
 * The run's cover: the FIRST prompt at its highest step.
 *
 * Highest step because that is what the run has learned so far. First prompt,
 * and deliberately not the last one rendered — `index` distinguishes *prompts*
 * within a step, not time, so every sample at the top step is equally "newest"
 * and a tie-break on recency has nothing to break. Choosing index 0 keeps the
 * cover on the same prompt for every run and at every step, which is what makes
 * two cards in this grid comparable and stops a card changing subject when a
 * later step renders more prompts.
 */
function coverOf(run) {
  const samples = run.samples || [];
  if (!samples.length || source.value?.id == null) return "";
  const cover = samples.reduce((best, s) =>
    s.step > best.step || (s.step === best.step && s.index < best.index)
      ? s
      : best,
  );
  return runSampleUrl(source.value.id, run.name, cover.filename);
}

/** True when the run wrote the bare final file that confirms it finished. */
function hasBareFinal(run) {
  return (run.checkpoints || []).some((cp) => cp.step === null);
}

function stepCount(run) {
  const n = (run.checkpoints || []).length;
  return `${n.toLocaleString()} ${n === 1 ? "checkpoint" : "checkpoints"}`;
}

/**
 * Pick a run, and tick every checkpoint in it.
 *
 * All of them, because importing part of a run is the exception: the steps
 * become one stack and the point of a stack is that the run is kept together.
 */
function pick(run) {
  pickedName.value = run.name;
  chosenSteps.value = (run.checkpoints || []).map((cp) => cp.step ?? null);
}

function clearPick() {
  pickedName.value = "";
  chosenSteps.value = [];
}

/** Arrow keys move between cards, the radiogroup pattern the role promises. */
function onGridKeydown(event) {
  const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft"];
  if (!keys.includes(event.key) || !runs.value.length) return;
  event.preventDefault();
  // Captured before the await: `currentTarget` is null by the time a queued
  // callback runs, so reading it inside `nextTick` would silently drop focus.
  const grid = event.currentTarget;
  const current = runs.value.findIndex((run) => run.name === pickedName.value);
  const step = event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 1;
  const from = current < 0 ? 0 : current;
  const next = (from + step + runs.value.length) % runs.value.length;
  pick(runs.value[next]);
  nextTick(() => {
    grid?.querySelector(`[data-run-index="${next}"]`)?.focus();
  });
}

/**
 * Re-read the runs under the output root.
 *
 * Keeps the reader's place. A reload fires on its own whenever the tab regains
 * focus, so it must not move the page under someone who is mid-decision: the
 * scroll offset is restored, and the picked run keeps its ticked checkpoints as
 * long as the run is still there. A run that has VANISHED (imported from
 * another window, or deleted) drops the selection rather than leaving a picked
 * name pointing at nothing.
 */
async function loadRuns() {
  if (source.value?.id == null) {
    runs.value = [];
    return;
  }
  const scrollTop = gridEl.value?.scrollTop ?? 0;
  const keepName = pickedName.value;
  const keepSteps = [...chosenSteps.value];
  loading.value = true;
  error.value = "";
  try {
    runs.value = await listRuns(source.value.id);
    const survived = runs.value.some((run) => run.name === keepName);
    pickedName.value = survived ? keepName : "";
    chosenSteps.value = survived ? keepSteps : [];
    await nextTick();
    if (gridEl.value) gridEl.value.scrollTop = scrollTop;
  } catch (err) {
    error.value = errorDetail(err) || "Could not read that folder.";
    runs.value = [];
    clearPick();
  } finally {
    loading.value = false;
  }
}

/** The button, and the auto-reload, are the same act. */
function reload() {
  loadRuns();
}

/**
 * Reload when the tab is looked at again.
 *
 * `visibilitychange` covers switching tabs and un-minimising; `focus` covers
 * moving between windows on one desktop, which fires no visibility change. Both
 * are cheap here because the listing walks a directory and reads nothing else,
 * and neither runs while the tab is hidden, which is what a polling timer could
 * not promise.
 */
function onVisible() {
  if (document.visibilityState === "visible") loadRuns();
}

onMounted(() => {
  if (!folders.loaded) folders.refresh();
  loadRuns();
  const managed = destinations.value.find((f) => f.kind === "managed");
  destinationId.value = managed?.id ?? destinations.value[0]?.id ?? null;
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("focus", onVisible);
});

onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", onVisible);
  window.removeEventListener("focus", onVisible);
});

async function submit() {
  if (!canSubmit.value) return;
  const notices = useNoticeStore();
  working.value = true;
  try {
    const report = await importRun({
      sourceFolderId: source.value.id,
      runName: picked.value.name,
      destinationFolderId: destinationId.value,
      steps: chosenSteps.value,
    });
    const failed = (report?.files || []).some((f) => f.status === "failed");
    notices.push({
      level: failed ? "warning" : "success",
      text: importReceipt(report),
    });
    // Both stores: the shelf gained rows, and the destination folder's file
    // count and `shelf_bytes` moved with them, so the drive bands are stale too.
    await Promise.all([shelf.fetchRows(), folders.refresh({ quiet: true })]);
    // Stay on the view rather than navigating away: the run may be gone now
    // (`delete_after_import`) and the rest are still here to work through.
    clearPick();
    await loadRuns();
  } catch (err) {
    notices.push({
      level: "error",
      text: errorDetail(err) || "Could not import that run.",
    });
  } finally {
    working.value = false;
  }
}
</script>

<style scoped>
.tr {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: var(--space-4);
  gap: var(--space-3);
}

.tr-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.tr-tb-left,
.tr-tb-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.tr-mark {
  color: rgb(var(--v-theme-on-surface-variant));
  flex-shrink: 0;
}

.tr-title {
  font-size: var(--text-lg);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.tr-sub {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}

/* Truncates from the LEFT: the run folder's own name is the identifying end of
   an output path, the same reason the folders dialog truncates that way. */
.tr-path {
  direction: rtl;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 32ch;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

/* auto-fill rather than auto-fit: a single run keeps a card's width instead of
   stretching one preview across the whole view. */
.tr-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-3);
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  align-content: start;
}

.tr-card {
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  overflow: hidden;
  cursor: pointer;
  background: rgb(var(--v-theme-surface));
}

.tr-card:hover {
  background: var(--hover-wash);
}

/* The pick reads as a ring, not a fill: the card's own preview is the content,
   and washing it would change the image the choice is being made on. */
.tr-card--picked {
  border-color: rgba(var(--v-theme-primary), 0.9);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-primary), 0.9);
}

.tr-card-preview {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  display: block;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.tr-card-preview--none {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

.tr-card-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
}

.tr-card-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
  word-break: break-word;
}

.tr-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

.tr-card-note {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

/* The decisions, pinned below the grid rather than floating over it: the cards
   stay readable while the checkpoints are chosen. */
.tr-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.15);
}

.tr-field {
  display: block;
  border: none;
  padding: 0;
  min-width: 0;
}

.tr-label {
  display: block;
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface-variant));
}

.tr-select {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.25);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
  max-width: 40ch;
}

.tr-steps {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.tr-step {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.tr-step-size {
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: var(--text-xs);
}

.tr-bar-actions {
  display: flex;
  gap: var(--space-2);
  margin-left: auto;
}

.tr-note {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}

.tr-warning {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-warning));
}
</style>
