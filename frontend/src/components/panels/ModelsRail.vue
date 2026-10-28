<template>
  <AppInspector
    v-model="tab"
    class="mrail"
    label="Models and tasks"
    :open="sidebarStore.modelsRailOpen"
    :tabs="tabs"
  >
    <TasksPanel v-if="tab === 'tasks'" />

    <template v-else>
      <header class="mrail-head">
        <h2 class="mrail-title" data-testid="mrail-title">{{ title }}</h2>
        <AppButton
          v-if="hasSelection"
          class="mrail-clear"
          variant="ghost"
          size="sm"
          data-testid="mrail-clear"
          @click="clearSelection"
          >Clear</AppButton
        >
      </header>

      <div class="mrail-scroll">
        <!-- The training runs place (#2169): the picked run, or the folder. -->
        <template v-if="onRuns">
          <ModelsRailRun
            v-if="pickedRuns.length === 1"
            :key="pickedRuns[0].key"
            :card="pickedRuns[0]"
          />
          <div
            v-else-if="pickedRuns.length > 1"
            class="mrail-pane"
            data-testid="mrail-runs-many"
          >
            <dl class="mrs-facts">
              <div class="mrs-fact">
                <dt>Checkpoints</dt>
                <dd>{{ runCheckpoints }}</dd>
              </div>
              <div class="mrs-fact">
                <dt>Base models</dt>
                <dd>{{ runBases || "–" }}</dd>
              </div>
            </dl>
            <p class="mrs-hint">
              At two or more runs, Import takes every checkpoint of each; each
              run lands as one stack.
            </p>
          </div>
          <div v-else class="mrail-pane" data-testid="mrail-runs-hint">
            <dl class="mrs-facts">
              <div class="mrs-fact">
                <dt>Folder</dt>
                <dd class="mrail-path">{{ runsStore.source?.path || "–" }}</dd>
              </div>
              <div class="mrs-fact">
                <dt>Runs</dt>
                <dd>{{ runsLine }}</dd>
              </div>
            </dl>
            <p class="mrs-hint">
              Pick a run to see its samples and loss. Import puts its LoRA on
              the shelf.
            </p>
          </div>
        </template>

        <div v-else-if="kind === 'none'" class="mrail-pane">
          <p v-if="store.setsError" class="mrail-warn" role="alert">
            Could not read your sets, so where each model is cannot be shown.
            <AppButton
              variant="ghost"
              size="sm"
              @click="store.loadWorkflowSets({ force: true })"
              >Retry</AppButton
            >
          </p>

          <div class="mrail-stats">
            <div class="mrail-stat">
              <span class="mrail-stat-n num" data-testid="mrail-stat-count">{{
                stat1
              }}</span>
              <span class="mrail-stat-l">{{ stat1Label }}</span>
            </div>
            <div class="mrail-stat">
              <span class="mrail-stat-n num" data-testid="mrail-stat-disk">{{
                diskTotal
              }}</span>
              <span class="mrail-stat-l">on disk</span>
            </div>
            <div class="mrail-stat">
              <span
                class="mrail-stat-n num mrail-warn-ink"
                data-testid="mrail-stat-loose"
                >{{ looseCount }}</span
              >
              <span class="mrail-stat-l">in no set</span>
            </div>
          </div>

          <section class="mrail-block" aria-labelledby="mrail-disk-label">
            <span id="mrail-disk-label" class="section-label">Disk by type</span>
            <div
              class="mrail-bar"
              role="img"
              :aria-label="diskAria"
              data-testid="mrail-disk-bar"
            >
              <span
                v-for="bucket in disk"
                :key="bucket.key"
                class="mrail-bar-seg"
                :class="`mrail-tone--${bucket.tone}`"
                :style="{ width: `${bucket.pct}%` }"
              ></span>
            </div>
            <ul class="mrail-legend">
              <li v-for="bucket in disk" :key="bucket.key">
                <span
                  class="mrail-swatch"
                  :class="`mrail-tone--${bucket.tone}`"
                  aria-hidden="true"
                ></span>
                {{ bucket.label }} {{ bytesText(bucket.bytes) }}
              </li>
            </ul>
          </section>

          <section class="mrail-block" aria-labelledby="mrail-largest-label">
            <span id="mrail-largest-label" class="section-label"
              >Largest files</span
            >
            <ul class="mrail-files">
              <li
                v-for="row in largest"
                :key="row.id"
                class="mrail-file"
                data-testid="mrail-largest"
              >
                <TypeMark v-bind="shelfTypeMark(row)" />
                <span class="mrs-name">{{ rowName(row) }}</span>
                <span class="mrs-num num">{{ bytesText(row.file_size) }}</span>
              </li>
            </ul>
          </section>

          <div
            v-if="missingCount"
            class="mrail-note"
            role="status"
            data-testid="mrail-missing-note"
          >
            <v-icon size="16" aria-hidden="true">mdi-alert-outline</v-icon>
            <span class="mrail-note-text">{{ missingText }}</span>
            <AppButton
              variant="ghost"
              size="sm"
              data-testid="mrail-select-missing"
              @click="selectMissing"
              >{{ missingCount === 1 ? "Select it" : "Select them" }}</AppButton
            >
          </div>
        </div>

        <ModelsRailSet
          v-else-if="kind === 'set'"
          :key="pickedGroups[0].key"
          :group="pickedGroups[0]"
        />

        <ModelsRailModel
          v-else-if="kind === 'model'"
          :key="pickedRows[0].id"
          :row="pickedRows[0]"
        />

        <div v-else class="mrail-pane" data-testid="mrail-many">
          <dl class="mrs-facts">
            <div class="mrs-fact">
              <dt>On disk</dt>
              <dd>{{ bytesText(manyBytes) }}</dd>
            </div>
            <div class="mrs-fact">
              <dt>Base models</dt>
              <dd>{{ manyBases || "–" }}</dd>
            </div>
          </dl>
          <p class="mrs-hint">
            Assign, move, hide or delete them from the selection pill.
          </p>
        </div>
      </div>
    </template>
  </AppInspector>
</template>

<script setup>
/**
 * The Models screen's right rail (#2168): an inspector of the shelf's
 * selection. With nothing picked it shows the view's figures; with one set or
 * one model it shows that item's sections; with several it says how much they
 * hold. The Tasks tab stays beside it, and the title bar's tasks pill opens it.
 *
 * The rail holds no selection of its own: it reads the store's, so the shelf,
 * the set grid and this rail cannot disagree about what is picked.
 */
import { computed, onBeforeUnmount, provide, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { formatModelSize, shelfTypeMark } from "../../utils/modelShelf";
import {
  baseOf,
  describeSet,
  diskByType,
  largestFiles,
  missingNote,
  RAIL_SECTIONS,
  rowName,
  selectionCount,
  setName,
  totalBytes,
  viewName,
} from "../../utils/modelsInspector";
import AppButton from "../widgets/AppButton.vue";
import AppInspector from "../widgets/AppInspector.vue";
import TypeMark from "../widgets/TypeMark.vue";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
import { baseOf as runBaseOf } from "../../utils/trainingRuns.js";
import ModelsRailModel from "./ModelsRailModel.vue";
import ModelsRailRun from "./ModelsRailRun.vue";
import ModelsRailSet from "./ModelsRailSet.vue";
import TasksPanel, { tasksTabFor } from "./TasksPanel.vue";

const store = useModelShelfStore();
const foldersStore = useModelFoldersStore();
const sidebarStore = useSidebarStore();
const tasksStore = useTasksStore();
const runsStore = useTrainingRunsStore();
const route = useRoute();

// The sets say where each model already is, and the rail's figures and the
// sets it describes need every model, so it reads them while it is mounted.
store.loadWorkflowSets();
store.setRailMounted(true);
onBeforeUnmount(() => store.setRailMounted(false));

const tab = ref("models");
watch(
  () => sidebarStore.tasksTabRequest,
  () => {
    tab.value = "tasks";
  },
);
// A double-click on a set card that opened this rail (`showCardInInspector`).
watch(
  () => sidebarStore.cardTabRequest,
  () => {
    tab.value = "models";
  },
);

const tabs = computed(() => [
  {
    value: "models",
    label: "Models",
    icon: "mdi-cube-outline",
    count: store.visibleRows.length,
  },
  tasksTabFor(tasksStore),
]);

/** Open or shut, per section key, for the life of the rail. */
const shut = reactive({});
provide(RAIL_SECTIONS, {
  isShut: (key) => Boolean(shut[key]),
  toggle: (key) => {
    shut[key] = !shut[key];
  },
});

// ── What is picked ────────────────────────────────────────────────────────

const onRuns = computed(() => route.name === "models-runs");

/** The set cards picked: evidence cards, missing cards, and hand-made sets. */
const pickedGroups = computed(() => {
  const cards = new Set([...store.pickedCardKeys, ...store.selectedMissingKeys]);
  const groups = store.setGroups.filter((group) => cards.has(group.key));
  for (const set of store.selectedSets) {
    const group = store.handMadeGroups.find((g) => g.key === `hand:${set.id}`);
    if (group) groups.push(group);
  }
  return groups;
});

const pickedRows = computed(() => store.selectedRows);

const kind = computed(() => {
  const sets = pickedGroups.value.length;
  const models = pickedRows.value.length;
  if (!sets && !models) return "none";
  if (sets === 1 && !models) return "set";
  if (models === 1 && !sets) return "model";
  return "many";
});

const hasSelection = computed(() =>
  onRuns.value ? pickedRuns.value.length > 0 : kind.value !== "none",
);

// ── Training runs ─────────────────────────────────────────────────────────

const pickedRuns = computed(() => runsStore.chosenCards);

const runCheckpoints = computed(() => {
  const n = pickedRuns.value.reduce(
    (sum, card) => sum + (card.run?.checkpoints || []).length,
    0,
  );
  return `${n.toLocaleString()} ${n === 1 ? "checkpoint" : "checkpoints"}`;
});

const runBases = computed(() =>
  [...new Set(pickedRuns.value.map((card) => runBaseOf(card)))].join(", "),
);

const runsLine = computed(() => {
  const n = runsStore.count;
  if (n == null) return "–";
  const live = runsStore.liveCount;
  return live ? `${n.toLocaleString()} · ${live} training now` : n.toLocaleString();
});

const title = computed(() => {
  if (onRuns.value) {
    const picked = pickedRuns.value;
    if (picked.length === 1) return picked[0].name;
    if (picked.length > 1) return `${picked.length} runs`;
    return "Training runs";
  }
  if (kind.value === "set") return setName(pickedGroups.value[0]);
  if (kind.value === "model") return rowName(pickedRows.value[0]);
  if (kind.value === "many") {
    return selectionCount(pickedGroups.value.length, pickedRows.value.length);
  }
  return viewName({
    runs: false,
    place: store.place,
    sidebarFilter: store.sidebarFilter,
    folderName: openFolderName.value,
  });
});

/** The open folder's own name: its last segment, else the registered folder's. */
const openFolderName = computed(() => {
  const scope = store.folderScope;
  if (!scope) return "";
  if (scope.path) return scope.path.split("/").pop();
  return foldersStore.labelByFolderId.get(scope.folderId) ?? "";
});

function clearSelection() {
  if (onRuns.value) {
    runsStore.clearSelection();
    return;
  }
  store.clearSelection();
  store.clearSetSelection();
  store.clearMissingSelection();
}

// ── Nothing picked: the view's figures ────────────────────────────────────

/**
 * The models the figures are about: the list's. On the Sets place that is the
 * whole shelf under `Show`, since the sidebar's narrowing is off there; the
 * grid's own rows are only what an open tray shows, which is none at rest.
 */
const viewRows = computed(() => store.visibleRows);

const stat1 = computed(() =>
  store.place === "sets"
    ? store.placeCounts.sets.toLocaleString()
    : viewRows.value.length.toLocaleString(),
);
const stat1Label = computed(() => (store.place === "sets" ? "sets" : "models"));
const diskTotalBytes = computed(() => totalBytes(viewRows.value));
const diskTotal = computed(() => bytesText(diskTotalBytes.value));
const looseCount = computed(
  () => store.placeCounts.loose?.toLocaleString() ?? "–",
);
const disk = computed(() => diskByType(viewRows.value));
const diskAria = computed(() =>
  disk.value.map((b) => `${b.label} ${bytesText(b.bytes)}`).join(", "),
);
const largest = computed(() => largestFiles(viewRows.value));

const missingCount = computed(() => store.missingSetKeys.length);
const missingText = computed(() => missingNote(missingCount.value));

/** Select the cards missing their checkpoint, on the Sets place they are drawn on. */
function selectMissing() {
  if (store.place !== "sets") store.setPlace("sets");
  store.selectAllMissing();
}

// ── Several picked ───────────────────────────────────────────────────────

const rowsById = computed(() => new Map(store.rows.map((row) => [row.id, row])));

/** Each picked set's description, read the way its own section reads it. */
const pickedSetInfo = computed(() =>
  pickedGroups.value.map((group) =>
    describeSet(group, {
      rowsById: rowsById.value,
      combinations: store.workflowSets.combinations ?? [],
    }),
  ),
);

/**
 * On disk, each file once: two picked sets can share a VAE, and a set can be
 * picked with one of its own models, so the sum is over distinct model ids.
 */
const manyBytes = computed(() => {
  const ids = new Set(pickedRows.value.flatMap((row) => row.memberIds ?? [row.id]));
  for (const info of pickedSetInfo.value) {
    for (const member of info.members) {
      if (member.onShelf && member.id != null) ids.add(member.id);
    }
  }
  return totalBytes([...ids].map((id) => rowsById.value.get(id)));
});

const manyBases = computed(() =>
  [
    ...new Set(
      [
        ...pickedRows.value.map((row) => baseOf(row)),
        ...pickedSetInfo.value.map((info) => info.base),
      ].filter((base) => base && base !== "–"),
    ),
  ].join(", "),
);

/** A byte count as the rows draw it, or the dash when there is none. */
function bytesText(bytes) {
  return bytes ? formatModelSize(bytes) : "–";
}
</script>

<style scoped>
.mrail-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 40px;
  padding: 0 var(--space-5);
  flex-shrink: 0;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}

.mrail-title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mrail-clear {
  margin-left: auto;
  flex-shrink: 0;
}

.mrail-scroll {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 0 var(--space-5) var(--space-5);
  overflow-y: auto;
}

.mrail-pane {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-3);
}

.mrail-path {
  min-width: 0;
  font-family: var(--font-mono);
  overflow-wrap: anywhere;
}

.mrail-warn {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  color: rgb(var(--v-theme-warning));
  font-size: var(--text-xs);
}

.mrail-warn-ink {
  color: rgb(var(--v-theme-warning));
}

.mrail-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}

.mrail-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.mrail-stat-n {
  font-size: var(--text-md);
  white-space: nowrap;
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.mrail-stat-l {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
}

.mrail-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.mrail-bar {
  display: flex;
  height: 8px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.mrail-bar-seg {
  display: block;
  height: 100%;
}

.mrail-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-4);
  margin: 0;
  padding: 0;
  list-style: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
}

.mrail-legend li {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.mrail-swatch {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-sm);
}

/* The four buckets of "Disk by type": the bar and its legend share these. */
.mrail-tone--tertiary {
  background: rgb(var(--v-theme-tertiary));
}

.mrail-tone--secondary {
  background: rgb(var(--v-theme-secondary));
}

.mrail-tone--neutral {
  background: rgba(var(--v-theme-on-surface), 0.45);
}

.mrail-tone--neutral-light {
  background: rgba(var(--v-theme-on-surface), 0.22);
}

.mrail-files {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.mrail-file {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 24px;
  font-size: var(--text-xs);
}

.mrail-note {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface-warning));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-xs);
}

.mrail-note-text {
  flex: 1;
  min-width: 0;
}
</style>
