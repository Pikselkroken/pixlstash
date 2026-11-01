<template>
  <!-- A run PixlStash trains opens its own page inside the tab; the toolbar's
       back arrow returns here. The Tasks tab's AI Toolkit row and the tab's
       amber dot lead to it through `?run=<uuid>`. A run trained elsewhere
       opens the same page drawn from its folder, through `?folder-run=<name>`. -->
  <TrainingRunLive v-if="openJob" :job-id="openJob.id" @close="closeRun" />
  <TrainingRunLive
    v-else-if="openFolderCard"
    :run="openFolderCard.run"
    :folder-id="source.id"
    @close="closeRun"
  />
  <p v-else-if="openFolderRun" class="tr-note tr-note--pad" role="status">
    <template v-if="error || jobsFailed || folders.error">
      Could not read the training runs.
      <AppButton size="sm" variant="secondary" @click="closeRun">All runs</AppButton>
    </template>
    <template v-else-if="folderRunsRead">
      That run is not in the training folder.
      <AppButton size="sm" variant="secondary" @click="closeRun">All runs</AppButton>
    </template>
    <template v-else>Reading the run…</template>
  </p>
  <p v-else-if="openUuid" class="tr-note tr-note--pad" role="status">
    <template v-if="jobsFailed">
      Could not read the training runs.
      <AppButton size="sm" variant="secondary" @click="closeRun">All runs</AppButton>
    </template>
    <template v-else-if="jobsLoaded">
      That training run is not in this library.
      <AppButton size="sm" variant="secondary" @click="closeRun">All runs</AppButton>
    </template>
    <template v-else>Reading the run…</template>
  </p>
  <div v-else class="tr">
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
        tooltip="Look for runs that have appeared since this list was read"
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
    <div
      v-else-if="loading && !runs.length && !cards.length"
      class="tr-loading"
      role="status"
    >
      <v-icon size="28" class="mdi-spin">mdi-loading</v-icon>
      <span>Reading runs…</span>
    </div>
    <p v-else-if="error" class="tr-note" role="alert">{{ error }}</p>
    <p v-else-if="!cards.length" class="tr-note" role="status">
      Nothing in that folder looks like a training run yet.
    </p>
    <p v-if="jobsFailed" class="tr-note" role="alert">
      Could not read which runs are training, so nothing can be imported until
      it can.
      <AppButton size="sm" variant="secondary" @click="reload">Retry</AppButton>
    </p>

    <div
      v-if="!(source && loading && !cards.length) && !error"
      ref="gridEl"
      class="tr-grid"
      @keydown="onGridKeydown"
    >
      <!-- The door into training from here (#2169). This screen has no set or
           person in view, so the dialog opens on its Train on picker. -->
      <button
        type="button"
        class="tr-new"
        data-testid="tr-new-run"
        aria-haspopup="dialog"
        @click="training.openToPick()"
      >
        <span class="tr-new-mark" aria-hidden="true"
          ><v-icon size="16">mdi-plus</v-icon></span
        >
        <span class="tr-new-label">New training run</span>
        <span class="tr-new-hint"
          >Pick a person or a set, and PixlStash writes the captions and the
          plan.</span
        >
      </button>

      <!-- `listbox` + `aria-multiselectable`, not a radiogroup. An import takes
           as many runs as are ticked: `POST /model-imports` is per-run and holds
           a non-blocking lock, so a batch is N sequential requests rather than
           something the API cannot express. `display: contents`, so the cards
           sit in the grid beside the New tile while staying one listbox. -->
      <div
        class="tr-list"
        role="listbox"
        aria-multiselectable="true"
        aria-label="Training runs"
      >
        <div
          v-for="(card, index) in cards"
          :key="card.key"
          class="tr-card"
          :class="{ 'tr-card--checked': isChosen(card) }"
          role="option"
          :aria-selected="isChosen(card)"
          :tabindex="index === cursor ? 0 : -1"
          :data-run-index="index"
          :data-run-key="card.key"
          @click="$event.detail > 1 || onCardClick(card, $event)"
          @dblclick="onCardDblClick(card)"
          @contextmenu.prevent="openMenu(card, $event.clientX, $event.clientY)"
          @focus="cursor = index"
          @keydown.enter.prevent="toggle(card)"
          @keydown.space.prevent="toggle(card)"
        >
          <div class="tr-card-shot">
            <!-- The highest step's samples, one per prompt, first prompt first:
                 what it has learned so far, on prompts that stay the same
                 across runs so two cards are comparable (see `stripOf`).
                 `loading="lazy"`, because a run carries up to 130 samples and
                 only visible cards need to fetch. -->
            <div v-if="stripOf(card).length" class="tr-card-strip">
              <img
                v-for="url in stripOf(card)"
                :key="url"
                class="tr-card-preview"
                :src="url"
                alt=""
                loading="lazy"
              />
            </div>
            <div v-else class="tr-card-preview tr-card-preview--none">
              <v-icon v-if="card.live" size="18" class="mdi-spin"
                >mdi-loading</v-icon
              >
              <v-icon v-else-if="failed(card)" size="18" class="tr-card-err"
                >mdi-alert</v-icon
              >
              <v-icon v-else size="18">mdi-image-off-outline</v-icon>
              <!-- A job with no folder has nothing to preview: its state is the
                   whole card (#2293). -->
              <span>{{
                card.live || failed(card) || !card.run
                  ? jobStatus(card.job)
                  : "No previews"
              }}</span>
            </div>
          </div>

          <div class="tr-card-body">
            <span class="tr-card-title">
              <span class="tr-card-name">{{ card.name }}</span>
              <span v-if="card.live" class="tr-card-chip">{{
                card.job?.status === "completed" ? "Finishing" : "Training"
              }}</span>
              <span v-else-if="failed(card)" class="tr-card-chip tr-card-chip--err"
                >Failed</span
              >
            </span>
            <span
              v-if="progressOf(card) != null"
              class="tr-card-bar"
              :class="{ 'tr-card-bar--live': card.live }"
              aria-hidden="true"
              ><span :style="{ width: `${progressOf(card)}%` }"></span
            ></span>
            <span class="tr-card-meta">
              <span class="tr-fact">{{ baseOf(card) }}</span>
              <span class="tr-fact">{{ stepsOf(card) }}</span>
              <span v-if="dateOf(card)" class="tr-fact">{{ dateOf(card) }}</span>
            </span>
            <!-- An unconfirmed cover is a fact about the run, not a warning
                 about the user: ai-toolkit writes a bare final file at the end,
                 so a run without one either is still training or was
                 interrupted. The highest step is then the best available
                 answer, not a certain one, and saying so is the difference
                 between a surprise and a choice. -->
            <!-- Why it ended, in the trainer's words: a failed run with a
                 folder otherwise reads as one that is merely unfinished. -->
            <span
              v-if="!card.live && card.job?.error"
              class="tr-card-note tr-card-reason"
              data-testid="tr-card-reason"
              :title="card.job.error"
              >{{ card.job.error }}</span
            >
            <span
              v-else-if="card.run && !card.live && !failed(card) && !hasBareFinal(card.run)"
              class="tr-card-note"
            >
              No final file yet, so the newest step is the cover.
            </span>
            <span v-if="card.run?.config_error" class="tr-card-note">
              Could not read its config, so the base model and triggers are
              unknown. The steps still import.
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- The card context menu, anchored to the pointer. A card right-clicked
         outside the selection becomes the selection first, as on the shelf, so
         Import here and on the pill always mean the same runs. -->
    <v-menu
      v-model="cardMenuOpen"
      :target="cardMenuAt"
      location="bottom end"
      origin="top start"
      :offset="2"
    >
      <div
        class="ctx-menu shelf-menu"
        role="menu"
        tabindex="-1"
        data-testid="tr-card-menu"
        @keydown="onMenuKeydown"
      >
        <button
          v-if="menuCard?.job || menuCard?.run"
          class="ctx-item"
          type="button"
          role="menuitem"
          data-testid="tr-menu-open"
          @click="openCard(menuCard)"
        >
          <v-icon class="ctx-icon">mdi-open-in-new</v-icon>
          <span class="ctx-label-text">Open run</span>
        </button>
        <!-- The LoRA the run put on the shelf, opened on that row. -->
        <button
          v-if="resultModelId(menuCard) != null"
          class="ctx-item"
          type="button"
          role="menuitem"
          data-testid="tr-menu-model"
          @click="goToModel(menuCard)"
        >
          <v-icon class="ctx-icon">mdi-bookshelf</v-icon>
          <span class="ctx-label-text">Go to model</span>
        </button>
        <button
          v-if="importable.length"
          class="ctx-item"
          type="button"
          role="menuitem"
          data-testid="tr-menu-import"
          :disabled="!canSubmit"
          @click="submit"
        >
          <v-icon class="ctx-icon">mdi-tray-arrow-down</v-icon>
          <span class="ctx-label-text">{{ confirmLabel }}</span>
        </button>
        <button
          v-if="canStop(menuCard)"
          class="ctx-item"
          type="button"
          role="menuitem"
          data-testid="tr-menu-stop"
          @click="runsStore.stopJob(menuCard.job, menuCard.name)"
        >
          <v-icon class="ctx-icon">mdi-stop</v-icon>
          <span class="ctx-label-text">Stop training</span>
        </button>
        <div
          v-if="menuCard?.job || importable.length"
          class="ctx-sep"
          role="separator"
        ></div>
        <button class="ctx-item" type="button" role="menuitem" @click="selectAll">
          <v-icon class="ctx-icon">mdi-select-all</v-icon>
          <span class="ctx-label-text">Select all shown</span>
        </button>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          @click="clearSelection"
        >
          <v-icon class="ctx-icon">mdi-close</v-icon>
          <span class="ctx-label-text">Clear selection</span>
          <span class="ctx-shortcut">Esc</span>
        </button>
      </div>
    </v-menu>

    <!-- The closed Models rail's handle, the one the shelf and Workflows show
         (visual-language "Closed inspector"): it names the picked runs and
         bounces on a new pick. -->
    <InspectorEdgeTab
      v-if="!sidebarStore.modelsRailOpen"
      :label="railLabel"
      :name="`Show runs: ${railLabel}`"
      :nudge="sidebarStore.inspectorNudge"
      data-testid="runs-rail-handle"
      @open="openRailFromTab"
    />

    <!-- The same floating pill the shelf docks over its rows, in the same
         strip. Centred, which is also what keeps it clear of the fixed
         bottom-right shortcuts FAB that a full-width bar ran underneath. -->
    <div v-if="importable.length" class="selbar-float">
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
            >
              <Tooltip :text="countTitle" activator="parent" />
              <span>{{ runCountLabel }}</span>
              <span class="selbar-size">· {{ fileCountLabel }}</span>
              <v-icon size="16" class="selbar-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="ctx-menu shelf-menu" role="menu"
            tabindex="-1"
            @keydown="onMenuKeydown"
          >
            <button
              class="ctx-item"
              type="button"
              role="menuitem"
              @click="selectAll"
            >
              <v-icon class="ctx-icon">mdi-select-all</v-icon>
              <span class="ctx-label-text">Select all shown</span>
            </button>
            <button
              class="ctx-item"
              type="button"
              role="menuitem"
              @click="clearSelection"
            >
              <v-icon class="ctx-icon">mdi-close</v-icon>
              <span class="ctx-label-text">Clear selection</span>
              <span class="ctx-shortcut">Esc</span>
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
            >
              <Tooltip
                text="Choose which checkpoints of this run to take"
                activator="parent"
              />
              <span>{{ stepsLabel }}</span>
              <v-icon size="16" class="selbar-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="ctx-menu shelf-menu">
            <label
              v-for="cp in single.checkpoints"
              :key="cp.filename"
              class="ctx-item tr-step"
            >
              <input
                v-model="chosenSteps"
                type="checkbox"
                :value="cp.step ?? null"
                :disabled="working"
              />
              <span class="ctx-label-text">{{
                cp.step === null ? "Final" : `Step ${cp.step}`
              }}</span>
              <span class="ctx-meta">{{ formatModelSize(cp.size) }}</span>
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
            >
              <Tooltip
                text="Which folder the checkpoints are copied into"
                activator="parent"
              />
              <v-icon size="16">mdi-folder-outline</v-icon>
              <span>{{ destinationName }}</span>
              <v-icon size="16" class="selbar-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <div class="ctx-menu shelf-menu" role="menu"
            tabindex="-1"
            @keydown="onMenuKeydown"
          >
            <button
              v-for="folder in destinations"
              :key="folder.id"
              class="ctx-item"
              type="button"
              role="menuitemradio"
              :aria-checked="folder.id === destinationId"
              @click="destinationId = folder.id"
            >
              <span class="ctx-label-text">{{ folder.path }}</span>
              <v-icon v-if="folder.id === destinationId" class="ctx-check"
                >mdi-check</v-icon
              >
            </button>
          </div>
        </v-menu>

        <span class="selbar-sep"></span>

        <AppButton
          variant="primary"
          size="sm"
          :loading="working"
          :disabled="!canSubmit"
          :tooltip="deletesSource ? deleteWarning : ''"
          @click="submit"
        >
          {{ confirmLabel }}
        </AppButton>

        <AppBarButton
          shape="round"
          icon="close"
          tooltip="Clear the selection (Esc)"
          aria-label="Clear the selection"
          @click="clearSelection"
        />
      </div>
    </div>

    <!-- The one thing about an import that cannot be undone, said before it
         starts rather than in the receipt. It follows the SOURCE folder's own
         setting, so it is a property of where the runs live and not a choice
         being made here. Tinted rather than coloured text: `warning` as a
         foreground measures 3.09:1 on the light canvas. -->
    <p v-if="importable.length && deletesSource" class="tr-warning" role="status">
      {{ deleteWarning }}
    </p>
  </div>
</template>

<script setup>
// The ai-toolkit training runs - the Models workspace's Training runs place
// (shelf plan F6, #2169).
//
// A VIEW and not a dialog, which is the whole reason it can stay current. A
// dialog is opened, read once and dismissed, so a run that finished while it
// was open was invisible until it was closed and reopened. The folder this
// reads is still set in a dialog - that is a setting, and a dialog is the right
// place to set what a folder IS. What is inside the folder is not a setting,
// and it changes without PixlStash doing anything, so it gets a view that
// reloads: on entry, and whenever the tab is looked at again.
//
// The runs and which are picked live in `useTrainingRunsStore`, because the
// rail describes the picked run and the sidebar counts them. This view draws
// the grid, owns the import pill and keeps the list current.
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
import { storeToRefs } from "pinia";
import { VIcon, VMenu } from "vuetify/components";

import { useRoute, useRouter } from "vue-router";

import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import TrainingRunLive from "./TrainingRunLive.vue";
import AppButton from "../widgets/AppButton.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import InspectorEdgeTab from "../widgets/InspectorEdgeTab.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { importRun, runSampleUrl } from "../../api/modelImports";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
import { useTrainingStore } from "../../stores/useTrainingStore";
import { errorDetail } from "../../utils/apiError";
import { formatModelSize } from "../../utils/modelShelf";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import {
  baseOf,
  dateOf,
  defaultDestination,
  failed,
  hasBareFinal,
  importDestinations,
  jobStatus,
  progressOf,
  resultModelId,
  stepsOf,
} from "../../utils/trainingRuns.js";

const emit = defineEmits(["set-folder"]);

/** A live run's figures are re-read this often while this view is up. */
const LIVE_POLL_MS = 5000;

const folders = useModelFoldersStore();
const shelf = useModelShelfStore();
const runsStore = useTrainingRunsStore();
const training = useTrainingStore();
const {
  runs,
  cards,
  chosen,
  chosenSteps,
  importable,
  single,
  loading,
  error,
  jobs,
  jobsLoaded,
  jobsFailed,
  source,
} = storeToRefs(runsStore);
const {
  isChosen,
  toggle,
  pick,
  selectAll: pickAll,
  clearSelection: unpick,
} = runsStore;

const gridEl = ref(null);
const destinationId = ref(null);
const cursor = ref(0);
const working = ref(false);
const countMenuOpen = ref(false);
const cardMenuOpen = ref(false);
const cardMenuAt = ref([0, 0]);
const menuKey = ref("");
/** Read live, so a reload while the menu is open acts on the current job. */
const menuCard = computed(
  () => cards.value.find((card) => card.key === menuKey.value) ?? null,
);
const stepMenuOpen = ref(false);
const destMenuOpen = ref(false);

// ── The closed Models rail (visual-language "Closed inspector") ───────────

const sidebarStore = useSidebarStore();

/** What the closed handle names: one run, `N runs`, or "" for none. */
const railLabel = computed(() => {
  const n = chosen.value.length;
  if (n > 1) return `${n.toLocaleString()} runs`;
  return n ? (cards.value.find((card) => isChosen(card))?.name ?? "") : "";
});

// A NEW pick nudges the rail: the handle bounces and the toolbar's rail glyph
// flashes, both only while the rail is closed. "New" means it holds a run the
// last pick did not, as on the Media grid: re-clicking what is picked, a clear,
// and a reload dropping a run that vanished are not news. Nor is a queued job's
// `job:<uuid>` tick moving to its folder card's key when the folder appears.
// Not on a run's own page, which shows no handle.
watch(chosen, (keys, previous) => {
  if (openUuid.value) return;
  const before = new Set(previous);
  for (const card of cards.value) {
    if (card.job && before.has(`job:${card.job.uuid}`)) before.add(card.key);
  }
  if (keys.some((key) => !before.has(key))) sidebarStore.inspectorNudge += 1;
});

// The same act as the toolbar toggle, so it persists. The handle unmounts as
// the rail opens, so focus is handed into the rail rather than to `body`.
function openRailFromTab() {
  sidebarStore.setModelsRailOpen(true);
  nextTick(() =>
    (
      document.querySelector(".mrail .mrail-clear") ??
      document.querySelector(".mrail .mrail-title")
    )?.focus(),
  );
}

// ── A run's own page ───────────────────────────────────────────────────────

const route = useRoute();
const router = useRouter();

const openUuid = computed(() =>
  typeof route.query.run === "string" ? route.query.run : "",
);
const openFolderRun = computed(() =>
  typeof route.query["folder-run"] === "string" ? route.query["folder-run"] : "",
);
// Not until the job list is read: a run PixlStash trained has a job, and its
// own page says more than its folder does. Unread, every run would look
// trained elsewhere, so the page would say so of runs PixlStash trained.
const openFolderCard = computed(() => {
  if (
    !openFolderRun.value ||
    !jobsLoaded.value ||
    jobsFailed.value ||
    source.value?.id == null
  ) {
    return null;
  }
  return (
    cards.value.find((card) => card.run?.name === openFolderRun.value) ?? null
  );
});
const openJob = computed(
  () =>
    jobs.value.find((job) => job.uuid === openUuid.value) ??
    openFolderCard.value?.job ??
    null,
);
/** The folder has been listed, so a run missing from it is really missing. */
const folderRunsRead = computed(
  () => folders.loaded && !loading.value && jobsLoaded.value,
);

function closeRun() {
  router.push({ name: "models-runs" });
}

/** Click, Ctrl+click, Shift+click: the same three gestures as every grid. */
function onCardClick(card, event) {
  pick(card, {
    ctrl: event.ctrlKey || event.metaKey,
    shift: event.shiftKey,
  });
}

/**
 * The pair's first click selected (the second is ignored); a Ctrl+double-click
 * unticked a card that was already selected, so it is picked again before its
 * page opens.
 */
function onCardDblClick(card) {
  if (!isChosen(card)) toggle(card);
  openCard(card);
}

/** A run PixlStash trained opens its job; one trained elsewhere its folder. */
function openCard(card) {
  if (card?.job) router.push({ name: "models-runs", query: { run: card.job.uuid } });
  else if (card?.run) {
    router.push({ name: "models-runs", query: { "folder-run": card.run.name } });
  }
}

/** The shelf, opened on the model the run made (`?model=` picks that row). */
function goToModel(card) {
  router.push({ name: "models", query: { model: String(resultModelId(card)) } });
}

/**
 * Whether the menu offers Stop: a run that is training. A trained job still
 * drawing previews or importing has a phase the job list does not carry, so
 * skipping its previews stays on the rail, which polls it.
 */
function canStop(card) {
  return Boolean(
    card?.live && card.job && !["completed", "stopping"].includes(card.job.status),
  );
}

/** Right-click: the card becomes the selection unless it is already in it. */
async function openMenu(card, x, y) {
  cursor.value = cards.value.indexOf(card);
  if (!isChosen(card)) pick(card);
  cardMenuOpen.value = false;
  await nextTick();
  menuKey.value = card.key;
  cardMenuAt.value = [x, y];
  cardMenuOpen.value = true;
}

// A menu anchored to coordinates has no element to hand focus back to, so it
// would land on <body>; give it back to the card the menu was opened on.
watch(cardMenuOpen, (open, wasOpen) => {
  if (open || !wasOpen) return;
  nextTick(() => {
    gridEl.value?.querySelector(`[data-run-index="${cursor.value}"]`)?.focus();
  });
});

/** Where an import may land (`importDestinations` says which and why). */
const destinations = computed(() => importDestinations(folders.folders));

const destinationName = computed(() => {
  const folder = destinations.value.find((f) => f.id === destinationId.value);
  if (!folder) return "Nowhere to put them";
  const parts = String(folder.path)
    .replace(/[\\/]+$/, "")
    .split(/[\\/]/);
  return parts[parts.length - 1] || folder.path;
});

/** The runs an Import takes: the picked cards that are finished. */
const chosenRuns = computed(() => importable.value.map((card) => card.run));

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

/** How many checkpoints the press will actually copy, across every run. */
const fileTotal = computed(() =>
  single.value
    ? chosenSteps.value.length
    : chosenRuns.value.reduce(
        (n, run) => n + (run.checkpoints || []).length,
        0,
      ),
);

const plural = (n, one, many) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

const fileCountLabel = computed(() =>
  plural(fileTotal.value, "checkpoint", "checkpoints"),
);

const runCountLabel = computed(() =>
  plural(chosenRuns.value.length, "run", "runs"),
);

const stepsLabel = computed(() => {
  const n = chosenSteps.value.length;
  const all = (single.value?.checkpoints || []).length;
  return n === all ? "Every checkpoint" : `${n} of ${all} checkpoints`;
});

const countTitle = computed(
  () =>
    `${chosenRuns.value.length} of ${
      cards.value.filter((card) => !card.live && card.run).length
    } runs selected`,
);

// The rule in force, said on the button: one run takes the checkpoints its
// picker names; two or more take every checkpoint of each, as one stack each.
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
 * The card's strip: the samples at the run's highest step, one per prompt,
 * first prompt first.
 *
 * Highest step because that is what the run has learned so far. Prompt order,
 * and deliberately not render order - `index` distinguishes *prompts* within a
 * step, not time, so every sample at the top step is equally "newest". Keeping
 * prompt 0 first keeps the strip's first frame on the same prompt for every run
 * and at every step, which is what makes two cards in this grid comparable.
 */
function stripOf(card) {
  const samples = card.run?.samples || [];
  if (!samples.length || source.value?.id == null) return [];
  const top = Math.max(...samples.map((s) => s.step));
  return samples
    .filter((s) => s.step === top)
    .sort((a, b) => a.index - b.index)
    .slice(0, 4)
    .map((s) => runSampleUrl(source.value.id, card.run.name, s.filename));
}

function selectAll() {
  pickAll();
  countMenuOpen.value = false;
}

function clearSelection() {
  unpick();
  countMenuOpen.value = false;
}

/** Arrow keys move the cursor; Space and Enter tick. Ctrl+A selects all, Escape clears. */
function onGridKeydown(event) {
  const menuKey =
    event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey);
  const cardEl = event.target?.closest?.("[data-run-index]");
  if (menuKey && cardEl) {
    event.preventDefault();
    const rect = cardEl.getBoundingClientRect();
    openMenu(cards.value[Number(cardEl.dataset.runIndex)], rect.left, rect.bottom);
    return;
  }
  if (
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    event.key?.toLowerCase() === "a"
  ) {
    event.preventDefault();
    selectAll();
    return;
  }
  if (event.key === "Escape" && chosen.value.length) {
    event.preventDefault();
    clearSelection();
    return;
  }
  const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft"];
  if (!keys.includes(event.key) || !cards.value.length) return;
  event.preventDefault();
  const grid = event.currentTarget;
  const step = event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 1;
  // From the New tile the cursor belongs to the last card focused: enter the
  // cards at the near end instead of stepping from it.
  if (event.target?.closest?.(".tr-new")) {
    cursor.value = step === 1 ? 0 : cards.value.length - 1;
  } else {
    cursor.value =
      (cursor.value + step + cards.value.length) % cards.value.length;
  }
  nextTick(() => {
    grid?.querySelector(`[data-run-index="${cursor.value}"]`)?.focus();
  });
}

/**
 * Re-read the runs, keeping the reader's place.
 *
 * A reload fires on its own whenever the tab regains focus, so it must not
 * move the page under someone who is mid-decision: the scroll offset is
 * restored here, and the store keeps every still-present tick.
 */
async function loadRuns() {
  const scrollTop = gridEl.value?.scrollTop ?? 0;
  await runsStore.loadRuns();
  cursor.value = Math.min(cursor.value, Math.max(cards.value.length - 1, 0));
  await nextTick();
  if (gridEl.value) gridEl.value.scrollTop = scrollTop;
}

/** The button, and the auto-reload, are the same act. */
function reload() {
  loadRuns();
  runsStore.loadJobs();
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
  if (document.visibilityState === "visible") reload();
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
    destinationId.value = defaultDestination(list)?.id ?? null;
  },
  { immediate: true },
);

// Back from a run page: what it trained may have landed meanwhile.
watch([openUuid, openFolderRun], ([uuid, name]) => {
  if (!uuid && !name) reload();
});

// While a run trains its card follows it: the job's step for the bar, the
// folder for its samples and checkpoints (the listing costs nothing). A run
// leaving the live set has written its last checkpoint, so the folder is read
// again.
let liveTimer = null;
watch(
  () => runsStore.liveCount,
  (n, before) => {
    clearInterval(liveTimer);
    liveTimer = null;
    if (n > 0) {
      liveTimer = setInterval(() => {
        if (document.visibilityState === "visible") reload();
      }, LIVE_POLL_MS);
    }
    if (before != null && n < before) loadRuns();
  },
  { immediate: true },
);

onMounted(() => {
  if (!folders.loaded) folders.refresh();
  loadRuns();
  runsStore.loadJobs();
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("focus", onVisible);
});

onBeforeUnmount(() => {
  clearInterval(liveTimer);
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
/* The panel sits inside `.shelf`, under its toolbar, and is itself the
   positioning context for the pill and the closed rail's handle, so the handle
   centres below the toolbar as it does on the Models tab. The bottom inset is
   what keeps the last row of cards out from under that pill. */
/* The inset matches `.shelf-body`, which carries NO horizontal padding at all -
   its rows run to the edges and take their own `--space-3` inset from the
   shared row rule. A 16px box around this grid put the two tabs on different
   left edges and wasted a column of grid width at every viewport. `--space-3`
   horizontally lines the first card up with where a row's content starts.

   The 56px foot is the shelf's own figure and is there for the same reason:
   room under the last card for the pill to float over nothing, so the bottom
   cards are readable and clickable at the one moment they matter. */
.tr {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: var(--space-3) var(--space-3) 56px;
  gap: var(--space-3);
}

.tr-note--pad {
  padding: var(--space-5);
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
/* The padding is the selection ring's room (two `--space-1`), which the scroll
   container would otherwise clip on the outer cards; the negative margin keeps
   the cards on the edge the comment above lines them up with. */
.tr-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-4);
  padding: var(--space-2);
  margin: calc(var(--space-2) * -1);
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

/* The global ink outline, drawn inside the card so it stays clear of the
   selection ring outside it, and both show on a focused selected card. */
.tr-card:focus-visible {
  outline-offset: calc(var(--focus-width) * -1);
}

/* The Workflow set card's selection mark (`ModelSetCard`'s `.msc--on`): a ring
   off the card in the active ink, `--space-1` wide and `--space-1` out. Drawn as
   two spread shadows rather than an outline, because the focus ring above is
   the outline. A left rail ran down the previews and stopped at the body's
   wash, which read as a stray edge. */
.tr-card--checked {
  box-shadow:
    0 0 0 var(--space-1) rgb(var(--v-theme-background)),
    0 0 0 calc(var(--space-1) * 2) var(--active-bar);
}

/* The New tile leads the grid, dashed like the set grid's New workflow set:
   a place to start rather than a thing to pick. */
.tr-new {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  min-height: 180px;
  padding: var(--space-4);
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  font: inherit;
  text-align: center;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.tr-new:hover {
  background: var(--hover-wash);
}

.tr-new-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-bar);
  height: var(--control-h-bar);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-background), 0.08);
}

.tr-new-label {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.tr-new-hint {
  max-width: 24ch;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}

/* One listbox, laid out as the grid's own cells beside the New tile. */
.tr-list {
  display: contents;
}

.tr-card-shot {
  position: relative;
}

/* The highest step's samples side by side, at a fixed height so a run with
   one prompt is not four times taller than a run with four. */
.tr-card-strip {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(0, 1fr);
  gap: var(--space-1);
  aspect-ratio: 3 / 1;
  background: rgba(var(--v-theme-on-background), 0.06);
}

.tr-card-preview {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  background: rgba(var(--v-theme-on-background), 0.06);
}

.tr-card-preview--none {
  aspect-ratio: 3 / 1;
  height: auto;
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
  background: var(--active-wash);
}

.tr-card-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-background));
  word-break: break-word;
}

.tr-card-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

/* Amber is the attention colour: this run is training now. Outlined, the
   recipe of the set card's badges, so the ink stays the label's. */
.tr-card-chip {
  flex-shrink: 0;
  height: var(--tag-h-xs);
  line-height: calc(var(--tag-h-xs) - 2px);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-accent));
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-background));
  font-size: var(--text-2xs);
}

/* As the run page's notices: the hue on the edge and the glyph, the word in ink. */
.tr-card-chip--err {
  border-color: rgb(var(--v-theme-surface-error));
}

.tr-card-err {
  color: rgb(var(--v-theme-surface-error));
}

.tr-card-bar {
  display: block;
  height: 3px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--track-trough);
}

.tr-card-bar > span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-tertiary));
}

.tr-card-bar--live > span {
  background: rgb(var(--v-theme-accent));
}

/* The set card's fact chips (`msc__fact`). */
.tr-fact {
  display: inline-block;
  max-width: 100%;
  box-sizing: border-box;
  height: var(--tag-h-xs);
  line-height: calc(var(--tag-h-xs) - 2px);
  padding: 0 var(--space-2);
  overflow: hidden;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* A run failed before its reason was summarised still carries the trainer's
   whole paragraph; clamp it, and `title` keeps the full text reachable. */
.tr-card-reason {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
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
  border: 1px solid rgba(var(--v-theme-surface-warning), 0.35);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-background));
}
</style>
