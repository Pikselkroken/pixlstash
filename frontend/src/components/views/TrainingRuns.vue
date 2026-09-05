<template>
  <div class="tr">
    <!-- No title, no subtitle, no brand mark. This is the shelf's second view
         and the shelf's toolbar already names it; a second heading here made
         two headers, two type scales for one rank, and put a vendor logo on one
         of the two views. What is left is the one fact the bar cannot carry -
         WHICH folder these came from - and the reload. -->
    <div v-if="source" class="tr-meta">
      <span class="tr-path" :title="source.path">{{ source.path }}</span>
      <!-- The view reloads itself whenever the tab is looked at again, which is
           the shape of the real workflow: leave PixlStash, train, come back.
           This button is for the other one - both windows visible at once, so
           focus never changes and nothing fires. No badge on it: the only way
           to know a run had appeared would be to poll the listing, and polling
           every run's checkpoints and samples to light a dot costs more than
           the button it would save. -->
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

    <p v-if="!source" class="tr-note" role="status">
      No ai-toolkit output folder is set yet.
      <AppButton size="sm" variant="secondary" @click="emit('set-folder')">
        <template #icon="{ size }"><AiToolkitIcon :size="size" /></template>
        Set ai-toolkit folder
      </AppButton>
    </p>
    <!-- The first read of a folder walks it and parses a `config.yaml` per run,
         which is not instant on a real output root - and until now that showed
         as an empty panel, which reads as "there is nothing here" rather than
         as "working". The shared spinner idiom (`mdi-loading` + `mdi-spin`, the
         one `AppButton` and the overlay panels already use), centred in the
         space the grid will fill, so the activity is where the result appears.
         A RELOAD does not take this branch: the grid stays up and the reload
         button carries its own spinner, because replacing a list you are
         reading with a spinner is worse than leaving it up a moment longer. -->
    <div v-else-if="loading && !runs.length" class="tr-loading" role="status">
      <v-icon size="28" class="mdi-spin">mdi-loading</v-icon>
      <span>Reading runs…</span>
    </div>
    <p v-else-if="error" class="tr-note" role="alert">{{ error }}</p>
    <p v-else-if="!runs.length" class="tr-note" role="status">
      Nothing in that folder looks like a training run yet.
    </p>

    <!-- `listbox` + `aria-multiselectable`, not a radiogroup. An import takes as
         many runs as are ticked: `POST /model-imports` is per-run and holds a
         non-blocking lock, so a batch is N sequential requests rather than
         something the API cannot express. -->
    <div
      v-else
      ref="gridEl"
      class="tr-grid"
      role="listbox"
      aria-multiselectable="true"
      aria-label="Training runs"
      :class="{ 'tr-grid--selecting': chosen.length > 0 }"
      @keydown="onGridKeydown"
    >
      <div
        v-for="(run, index) in runs"
        :key="run.name"
        class="tr-card"
        :class="{ 'tr-card--checked': isChosen(run) }"
        role="option"
        :aria-selected="isChosen(run)"
        :tabindex="index === cursor ? 0 : -1"
        :data-run-index="index"
        @click="toggle(run)"
        @focus="cursor = index"
        @keydown.enter.prevent="toggle(run)"
        @keydown.space.prevent="toggle(run)"
      >
        <div class="tr-card-shot">
          <!-- The first prompt at the run's highest step: what it has learned so
               far, on a prompt that stays the same across runs so two cards are
               comparable (see `coverOf`). `loading="lazy"`, because a run
               carries up to 130 samples and only visible cards need to fetch. -->
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

          <!-- The countable half of the checked state. The rail says "this one
               is in"; a disc per card is what makes "seven of them are in"
               readable at a glance. Hidden by opacity and never by `v-if`, so
               the target does not pop into existence under the pointer. -->
          <span class="tr-card-check" aria-hidden="true">
            <v-icon v-if="isChosen(run)" size="16">mdi-check</v-icon>
          </span>
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

    <!-- The same floating pill the shelf docks over its rows, in the same
         strip. Centred, which is also what keeps it clear of the fixed
         bottom-right shortcuts FAB that a full-width bar ran underneath. -->
    <div v-if="chosen.length" class="selbar-float">
      <div class="selbar" role="toolbar" aria-label="Selected training runs">
        <v-menu
          v-model="countMenuOpen"
          location="top"
          origin="bottom center"
          :offset="8"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="selbar-count"
              type="button"
              aria-haspopup="menu"
              :aria-expanded="countMenuOpen"
              :title="countTitle"
            >
              <AiToolkitIcon :size="16" />
              <span>{{ chosen.length.toLocaleString() }}</span>
              <span class="selbar-size">· {{ fileCountLabel }}</span>
              <v-icon size="15" class="selbar-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="shelf-menu" role="menu">
            <button
              class="shelf-mi"
              type="button"
              role="menuitem"
              @click="selectAll"
            >
              <v-icon size="16">mdi-select-all</v-icon>
              <span>Select all shown</span>
            </button>
            <button
              class="shelf-mi"
              type="button"
              role="menuitem"
              @click="clearSelection"
            >
              <v-icon size="16">mdi-close</v-icon>
              <span>Clear selection</span>
              <span class="shelf-mi-kbd">Esc</span>
            </button>
          </div>
        </v-menu>

        <span class="selbar-sep"></span>

        <!-- Which checkpoints, and only while exactly one run is chosen. At two
             or more the answer is every checkpoint in each: the steps of a run
             land as one stack, and a per-step list across five runs would be
             forty checkboxes for a decision nobody came here to make. The
             Import label says which rule is in force. -->
        <v-menu
          v-if="single"
          v-model="stepMenuOpen"
          location="top"
          :offset="8"
          :close-on-content-click="false"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="selbar-count"
              type="button"
              aria-haspopup="menu"
              :aria-expanded="stepMenuOpen"
              title="Choose which checkpoints of this run to take"
            >
              <span>{{ stepsLabel }}</span>
              <v-icon size="15" class="selbar-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="shelf-menu">
            <label
              v-for="cp in single.checkpoints"
              :key="cp.filename"
              class="shelf-mi tr-step"
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
          </div>
        </v-menu>

        <v-menu v-model="destMenuOpen" location="top" :offset="8">
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="selbar-count"
              type="button"
              aria-haspopup="menu"
              :aria-expanded="destMenuOpen"
              title="Which folder the checkpoints are copied into"
            >
              <v-icon size="16">mdi-folder-outline</v-icon>
              <span>{{ destinationName }}</span>
              <v-icon size="15" class="selbar-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="shelf-menu" role="menu">
            <button
              v-for="folder in destinations"
              :key="folder.id"
              class="shelf-mi"
              type="button"
              role="menuitemradio"
              :aria-checked="folder.id === destinationId"
              @click="destinationId = folder.id"
            >
              <v-icon size="16">{{
                folder.id === destinationId ? "mdi-check" : "mdi-blank"
              }}</v-icon>
              <span>{{ folder.path }}</span>
            </button>
          </div>
        </v-menu>

        <span class="selbar-sep"></span>

        <AppButton
          variant="primary"
          size="sm"
          :loading="working"
          :disabled="!canSubmit"
          :title="deletesSource ? deleteWarning : undefined"
          @click="submit"
        >
          {{ confirmLabel }}
        </AppButton>

        <button
          class="selbar-btn"
          type="button"
          title="Clear the selection (Esc)"
          aria-label="Clear the selection"
          @click="clearSelection"
        >
          <v-icon size="18">mdi-close</v-icon>
        </button>
      </div>
    </div>

    <!-- The one thing about an import that cannot be undone, said before it
         starts rather than in the receipt. It follows the SOURCE folder's own
         setting, so it is a property of where the runs live and not a choice
         being made here. Tinted rather than coloured text: `warning` as a
         foreground measures 3.09:1 on the light canvas. -->
    <p v-if="chosen.length && deletesSource" class="tr-warning" role="status">
      {{ deleteWarning }}
    </p>
  </div>
</template>

<script setup>
// The ai-toolkit training runs - the model shelf's second view (shelf plan F6).
//
// A VIEW and not a dialog, which is the whole reason it can stay current. A
// dialog is opened, read once and dismissed, so a run that finished while it
// was open was invisible until it was closed and reopened. The folder this
// reads is still set in a dialog - that is a setting, and a dialog is the right
// place to set what a folder IS. What is inside the folder is not a setting,
// and it changes without PixlStash doing anything, so it gets a view that
// reloads: on entry, and whenever the tab is looked at again.
//
// It lives INSIDE `ModelShelf.vue` as a tabpanel rather than at its own route,
// because these are models too - still in ai-toolkit's output folder rather
// than on the shelf, and importing one is the act of moving it from here to
// there. The shelf owns the toolbar, the tabs and the count.
//
// Built on the promise the listing route makes: describing a run costs nothing
// and changes nothing, so the whole grid - names, steps, sizes, previews, what
// the config says it trained against - is drawn before the user commits to any
// of it. Nothing here hashes, copies or writes until Import is pressed. That is
// also what makes reloading free enough to do on every focus.

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { VIcon, VMenu } from "vuetify/components";

import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import AppButton from "../widgets/AppButton.vue";
import { importRun, listRuns, runSampleUrl } from "../../api/modelImports";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { formatModelSize } from "../../utils/modelShelf";

const emit = defineEmits(["set-folder", "count"]);

const folders = useModelFoldersStore();
const shelf = useModelShelfStore();

const gridEl = ref(null);
const destinationId = ref(null);
/** Run NAMES, not indices: a reload reorders and renumbers, names survive it. */
const chosen = ref([]);
const chosenSteps = ref([]);
const cursor = ref(0);
const runs = ref([]);
const loading = ref(false);
const working = ref(false);
const error = ref("");
const countMenuOpen = ref(false);
const stepMenuOpen = ref(false);
const destMenuOpen = ref(false);

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

const destinationName = computed(() => {
  const folder = destinations.value.find((f) => f.id === destinationId.value);
  if (!folder) return "Nowhere to put them";
  const parts = String(folder.path)
    .replace(/[\\/]+$/, "")
    .split(/[\\/]/);
  return parts[parts.length - 1] || folder.path;
});

const chosenRuns = computed(() =>
  runs.value.filter((run) => chosen.value.includes(run.name)),
);

/** The one chosen run, or null - what gates the per-step picker. */
const single = computed(() =>
  chosenRuns.value.length === 1 ? chosenRuns.value[0] : null,
);

const deletesSource = computed(() =>
  Boolean(source.value?.delete_after_import),
);

const deleteWarning = computed(
  () =>
    `This folder is set to remove a run after importing it, so ${
      chosenRuns.value.length === 1
        ? chosenRuns.value[0]?.name
        : `all ${chosenRuns.value.length} of these runs`
    } will be gone from disk once the files have landed.`,
);

/** How many files the press will actually copy, across every chosen run. */
const fileTotal = computed(() =>
  single.value
    ? chosenSteps.value.length
    : chosenRuns.value.reduce(
        (n, run) => n + (run.checkpoints || []).length,
        0,
      ),
);

const fileCountLabel = computed(
  () =>
    `${fileTotal.value.toLocaleString()} ${fileTotal.value === 1 ? "file" : "files"}`,
);

const stepsLabel = computed(() => {
  const n = chosenSteps.value.length;
  const all = (single.value?.checkpoints || []).length;
  return n === all ? "Every checkpoint" : `${n} of ${all} checkpoints`;
});

const countTitle = computed(
  () => `${chosen.value.length} of ${runs.value.length} runs selected`,
);

const confirmLabel = computed(() => {
  if (!fileTotal.value) return "Import";
  const n = chosenRuns.value.length;
  return n === 1
    ? `Import ${fileCountLabel.value}`
    : `Import ${n} runs · ${fileCountLabel.value}`;
});

// An import needs BOTH ends named. The destination has always been stated
// here; the source root is stated beside it so `submit` cannot reach its loop
// without one and send a request whose `sourceFolderId` is missing - a
// round-trip that can only come back refused, reported per run as if the run
// were at fault.
const canSubmit = computed(
  () =>
    !working.value &&
    source.value?.id != null &&
    chosenRuns.value.length > 0 &&
    fileTotal.value > 0 &&
    destinationId.value != null,
);

/**
 * The run's cover: the FIRST prompt at its highest step.
 *
 * Highest step because that is what the run has learned so far. First prompt,
 * and deliberately not the last one rendered - `index` distinguishes *prompts*
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

function isChosen(run) {
  return chosen.value.includes(run.name);
}

/**
 * Add or remove a run.
 *
 * Ticking the only chosen run fills the step list with all of its checkpoints,
 * because importing part of a run is the exception: the steps become one stack
 * and the point of a stack is that the run is kept together.
 */
function toggle(run) {
  chosen.value = isChosen(run)
    ? chosen.value.filter((name) => name !== run.name)
    : [...chosen.value, run.name];
}

function selectAll() {
  chosen.value = runs.value.map((run) => run.name);
  countMenuOpen.value = false;
}

function clearSelection() {
  chosen.value = [];
  countMenuOpen.value = false;
}

// Whenever the selection narrows to exactly one run, the step list becomes
// meaningful and starts filled. Anywhere else it is not shown and not read.
//
// Keyed on the run's NAME and not on the computed's identity: `single`
// recomputes whenever `runs` is replaced, which a reload does on every window
// focus, and refilling there would silently re-tick a checkpoint the reader had
// just excluded - the exact "moves the ground under you" failure the reload is
// written to avoid.
let stepsFilledFor = "";
watch(single, (run) => {
  const name = run?.name || "";
  if (name === stepsFilledFor) return;
  stepsFilledFor = name;
  chosenSteps.value = run
    ? (run.checkpoints || []).map((cp) => cp.step ?? null)
    : [];
});

/** Arrow keys move the cursor; Space and Enter tick. Escape clears. */
function onGridKeydown(event) {
  if (event.key === "Escape" && chosen.value.length) {
    event.preventDefault();
    clearSelection();
    return;
  }
  const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft"];
  if (!keys.includes(event.key) || !runs.value.length) return;
  event.preventDefault();
  const grid = event.currentTarget;
  const step = event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 1;
  cursor.value = (cursor.value + step + runs.value.length) % runs.value.length;
  nextTick(() => {
    grid?.querySelector(`[data-run-index="${cursor.value}"]`)?.focus();
  });
}

/**
 * Re-read the runs under the output root.
 *
 * Keeps the reader's place. A reload fires on its own whenever the tab regains
 * focus, so it must not move the page under someone who is mid-decision: the
 * scroll offset is restored, and every still-present run keeps its tick. Runs
 * that have VANISHED (imported from another window, or deleted) drop out of the
 * selection rather than leaving it pointing at nothing.
 *
 * Reads overlap - mount, a folder change, a visibility change and a window
 * focus all start one, and none of them cancels the last - so every completion
 * is checked against the generation before it writes anything. An older read
 * answering last would otherwise replace the newer one's rows, and the
 * selection reconciled below would be filtering the newer listing's names
 * against the older one's. The folder id is captured too and the request is
 * made against THAT rather than re-read from the store, so a read is always
 * asking about the folder it was started for.
 */
let generation = 0;
async function loadRuns() {
  const sourceId = source.value?.id ?? null;
  const gen = ++generation;
  const current = () => gen === generation;
  if (sourceId == null) {
    runs.value = [];
    emit("count", null);
    // This read is over, and it owns the flag as much as any other: an
    // in-flight one is now stale and will decline to clear it.
    loading.value = false;
    return;
  }
  const scrollTop = gridEl.value?.scrollTop ?? 0;
  const keep = [...chosen.value];
  const keepSteps = [...chosenSteps.value];
  loading.value = true;
  error.value = "";
  try {
    const found = await listRuns(sourceId);
    if (!current()) return;
    runs.value = found;
    emit("count", runs.value.length);
    const names = new Set(runs.value.map((run) => run.name));
    chosen.value = keep.filter((name) => names.has(name));
    if (chosen.value.length === 1 && keep.length === 1) {
      chosenSteps.value = keepSteps;
    }
    cursor.value = Math.min(cursor.value, Math.max(runs.value.length - 1, 0));
    await nextTick();
    if (gridEl.value) gridEl.value.scrollTop = scrollTop;
  } catch (err) {
    if (!current()) return;
    error.value = errorDetail(err) || "Could not read that folder.";
    runs.value = [];
    emit("count", null);
    clearSelection();
  } finally {
    // Only the current read owns the spinner: an older one clearing it would
    // report the newer, still-running read as finished.
    if (current()) loading.value = false;
  }
}

/** The button, and the auto-reload, are the same act. */
function reload() {
  loadRuns();
}

/**
 * Read the runs whenever the output root changes - including from nothing.
 *
 * The load used to hang off `onMounted` alone, which was wrong in the one case
 * that matters most: setting the folder from THIS view's own empty state. The
 * shelf answers that by showing the runs tab, but the runs tab is already
 * showing, so nothing remounts and the panel sat empty next to a folder it now
 * had. Depending on the folder itself is the fix rather than re-emitting a
 * navigation that is already where it wants to be.
 *
 * `loadRuns` clears and reports an empty count when the id goes away, so
 * forgetting the folder is the same edge handled by the same call.
 *
 * The old root's rows and its selection are dropped FIRST, before the new read
 * is even started. Waiting for the new listing to replace them leaves the old
 * root's cards on screen under the new root's path for as long as the walk
 * takes, with Import live: a run name means nothing outside the root it was
 * read under, so a tick surviving that window sends the new root's id with the
 * old root's run name - which is issue #1019 whatever the response ordering
 * does. Same reason the count is cleared rather than left describing a folder
 * the shelf is no longer pointing at.
 */
watch(
  () => source.value?.id,
  () => {
    runs.value = [];
    emit("count", null);
    clearSelection();
    loadRuns();
  },
);

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

/**
 * Keep a valid destination selected, rather than picking one once at mount.
 *
 * `folders.refresh()` is not awaited - this view must draw before the registry
 * lands - so on a cold start or a direct navigation to `/models/runs` the list
 * is EMPTY at mount. Deriving the default there left `destinationId` null with
 * nothing to re-derive it, and `canSubmit` requires a destination: the reader
 * could tick runs and find Import disabled with nothing on screen saying why.
 *
 * `immediate` covers the mount case, so this is the only place that chooses.
 * It also re-chooses when the selected folder stops being a legal destination
 * (forgotten, or relocated into `external`), instead of holding an id the
 * server would refuse.
 */
watch(
  destinations,
  (list) => {
    const stillThere = list.some((f) => f.id === destinationId.value);
    if (destinationId.value != null && stillThere) return;
    const managed = list.find((f) => f.kind === "managed");
    destinationId.value = managed?.id ?? list[0]?.id ?? null;
  },
  { immediate: true },
);

onMounted(() => {
  if (!folders.loaded) folders.refresh();
  loadRuns();
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("focus", onVisible);
});

onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", onVisible);
  window.removeEventListener("focus", onVisible);
});

/**
 * Import every chosen run, one after another.
 *
 * SEQUENTIALLY, and that is a requirement rather than a simplification:
 * `POST /model-imports` takes `SHELF_IO_LOCK` with `blocking=False` and answers
 * 409 if anything else holds it, so firing the batch concurrently would fail
 * every request after the first. Each run is caught on its own, so run 5 still
 * gets its turn when run 3 fails, and the receipt names what actually landed -
 * stopping at the first failure would leave the user unable to tell which of
 * the five are now on the shelf.
 */
async function submit() {
  if (!canSubmit.value) return;
  const notices = useNoticeStore();
  // Captured once, because the batch is sequential and the registry can change
  // between two of its requests. The rows and the tick are dropped the moment
  // the root changes (see the watcher), so what is captured here is the root
  // the chosen runs were read under - and every request in the batch names it,
  // rather than whichever folder is registered when its turn arrives. Non-null
  // by `canSubmit`, which is checked above.
  const sourceId = source.value.id;
  const batch = [...chosenRuns.value];
  const imported = [];
  const failed = [];
  working.value = true;
  try {
    for (const run of batch) {
      const steps = single.value
        ? chosenSteps.value
        : (run.checkpoints || []).map((cp) => cp.step ?? null);
      try {
        const report = await importRun({
          sourceFolderId: sourceId,
          runName: run.name,
          destinationFolderId: destinationId.value,
          steps,
        });
        const bad = (report?.files || []).filter((f) => f.status === "failed");
        if (bad.length) failed.push(`${run.name} (${bad.length} of its files)`);
        else imported.push(run.name);
      } catch (err) {
        failed.push(`${run.name} (${errorDetail(err) || "refused"})`);
      }
    }
    notices.push({
      level: failed.length ? "warning" : "success",
      text: batchReceipt(imported, failed),
    });
    // Both stores: the shelf gained rows, and the destination folder's file
    // count and `shelf_bytes` moved with them, so the drive bands are stale too.
    await Promise.all([shelf.fetchRows(), folders.refresh({ quiet: true })]);
    clearSelection();
    await loadRuns();
  } finally {
    working.value = false;
  }
}

/**
 * What the batch did, naming the failures rather than averaging them away.
 *
 * Per-run and not per-file: a run is the unit the user chose, so "3 of 5 runs"
 * is the sentence they can act on. A file that failed inside an otherwise-good
 * run makes that run a failure here, because its stack is incomplete.
 */
function batchReceipt(imported, failed) {
  const ok = imported.length
    ? `Imported ${imported.length} ${imported.length === 1 ? "run" : "runs"}`
    : "Imported nothing";
  if (!failed.length) return `${ok}.`;
  return `${ok}. Could not import ${failed.join(", ")}.`;
}
</script>

<style scoped>
/* The panel sits inside `.shelf`, which owns the toolbar and the positioning
   context the pill docks against. The bottom inset is what keeps the last row
   of cards out from under that pill. */
/* The inset matches `.shelf-body`, which carries NO horizontal padding at all -
   its rows run to the edges and take their own `--space-3` inset from the
   shared row rule. A 16px box around this grid put the two tabs on different
   left edges and wasted a column of grid width at every viewport. `--space-3`
   horizontally lines the first card up with where a row's content starts.

   The 56px foot is the shelf's own figure and is there for the same reason:
   room under the last card for the pill to float over nothing, so the bottom
   cards are readable and clickable at the one moment they matter. */
.tr {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: var(--space-3) var(--space-3) 56px;
  gap: var(--space-3);
}

.tr-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-2);
  min-width: 0;
}

/* Truncates from the LEFT: the run folder's own name is the identifying end of
   an output path, the same reason the folders dialog truncates that way. Mono,
   because §3 gives a path the mono face. */
.tr-path {
  direction: rtl;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 48ch;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* auto-fill rather than auto-fit: a single run keeps a card's width instead of
   stretching one preview across the whole view. */
.tr-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-4);
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  align-content: start;
}

/* On the canvas with a hairline, exactly like a shelf row - NOT filled with
   `surface`. On dark, `surface` is 1.12:1 from `background`, so the fill bought
   nothing visually and cost the selected rail its contrast: `primary` measures
   2.72:1 on `surface` (under WCAG 1.4.11's 3:1) and 3.04:1 on `background`,
   which is the number the shelf's own rows already ship. */
.tr-card {
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-divider));
  overflow: hidden;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.tr-card:hover {
  background: var(--hover-wash);
  border-color: rgb(var(--v-theme-border));
}

.tr-card:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* The shelf row's own recipe, verbatim: a `--rail-w` rail down the left edge.
   The same object, which is the strongest cue that these are two views of one
   shelf - and it survives desaturation, which a wash alone does not. */
.tr-card--checked {
  border-color: rgb(var(--v-theme-primary));
  box-shadow: inset var(--rail-w) 0 0 rgb(var(--v-theme-primary));
}

.tr-card-shot {
  position: relative;
}

.tr-card-preview {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  display: block;
  background: rgba(var(--v-theme-on-background), 0.06);
}

.tr-card-preview--none {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* Hidden by opacity, never by `v-if` or `visibility`: a target that pops into
   existence under the pointer is a mis-click. It appears on hover, on keyboard
   focus, and on every card once anything is ticked - so the grid says plainly
   that it is in a selecting mode. */
.tr-card-check {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: var(--space-6);
  height: var(--space-6);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-pill);
  z-index: var(--z-raised);
  opacity: 0;
  transition: opacity var(--dur-1) var(--ease-standard);
  /* The 2px ring in the page colour is what lets an opaque disc read against an
     arbitrary photo without a scrim: it needs a boundary, not a darkening. */
  border: 2px solid rgb(var(--v-theme-background));
  box-shadow: var(--elevation-2);
}

.tr-card:hover .tr-card-check,
.tr-card:focus-visible .tr-card-check,
.tr-grid--selecting .tr-card-check {
  opacity: 1;
}

.tr-card--checked .tr-card-check {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  opacity: 1;
}

.tr-card-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
}

/* The wash goes on the metadata strip and NEVER on the preview: the image is
   the evidence the choice is being made on, and tinting it changes the thing
   being judged. (The photo grid does wash its whole tile - legal there, because
   the user already knows the photo.) */
.tr-card--checked .tr-card-body {
  background: rgba(var(--v-theme-primary), 0.12);
}

.tr-card-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-background));
  word-break: break-word;
}

.tr-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-background), 0.7);
}

.tr-card-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
}

.tr-step {
  cursor: pointer;
}

.tr-step-size {
  margin-left: auto;
  padding-left: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.tr-loading {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* The global reduced-motion reset would freeze this into a static broken ring,
   which reads as a bug rather than as restraint. Slowed, not stopped - the same
   exemption `AppButton` takes for the same glyph. */
@media (prefers-reduced-motion: reduce) {
  .tr-loading .mdi-spin::before {
    animation-duration: 2s !important;
    animation-iteration-count: infinite !important;
  }
}

.tr-note {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* The colour carries the variant; the TEXT stays full strength. The warning hue
   as a foreground measures 3.09:1 on the light background - under the 4.5:1
   floor, on the one sentence in this view that says runs are about to be
   deleted from disk. Tint and border instead, the recipe `library-chip--warn`
   and `DedupAutoStackDialog` already use. */
.tr-warning {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-warning), 0.12);
  border: 1px solid rgba(var(--v-theme-warning), 0.35);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-background));
}
</style>
