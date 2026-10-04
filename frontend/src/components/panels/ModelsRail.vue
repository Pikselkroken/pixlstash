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
      <div class="mrail-head">
        <AppInput
          v-model="query"
          icon="magnify"
          placeholder="Find a model"
          aria-label="Find a model"
          data-testid="mrail-search"
        />
        <div class="mrail-chips" role="group" aria-label="Filters">
          <!-- Never applied for you: opening a set leaves it off. The set
               panel's Models · N fit is the one thing that turns it on. -->
          <button
            v-if="fitsChipSet"
            class="mrail-chip"
            :class="{ 'mrail-chip--on': fitsSet }"
            type="button"
            :aria-pressed="fitsSet ? 'true' : 'false'"
            data-testid="mrail-fits"
            @click="toggleFits"
          >
            Fits {{ fitsChipLabel }}
            <v-icon v-if="fitsSet" size="12" aria-hidden="true"
              >mdi-close</v-icon
            >
          </button>
          <button
            class="mrail-chip"
            :class="{ 'mrail-chip--on': looseOnly }"
            type="button"
            :aria-pressed="looseOnly ? 'true' : 'false'"
            data-testid="mrail-loose"
            @click="looseOnly = !looseOnly"
          >
            In no set yet
            <v-icon v-if="looseOnly" size="12" aria-hidden="true"
              >mdi-close</v-icon
            >
          </button>
        </div>
      </div>

      <p v-if="!listed.length" class="mrail-empty">
        No models on the shelf yet.
      </p>
      <p v-else-if="!shown.length && needle" class="mrail-empty">
        No model matches “{{ query.trim() }}”.
        <AppButton variant="ghost" size="sm" @click="query = ''"
          >Clear search</AppButton
        >
      </p>
      <p v-else-if="!shown.length && fitsSet" class="mrail-empty">
        No other {{ fitsBase ? `${fitsBase} model` : "model" }} on the shelf.
        <AppButton variant="ghost" size="sm" @click="showAll"
          >Show all</AppButton
        >
      </p>
      <p v-else-if="!shown.length" class="mrail-empty">
        Every model is in one of your sets.
        <AppButton variant="ghost" size="sm" @click="showAll"
          >Show all</AppButton
        >
      </p>

      <!-- One tab stop: the arrows walk it, typing jumps to a name, Enter adds
           the selection to the open set. Add on a row is the mouse's copy of
           Enter, so it is not a stop of its own. -->
      <div
        v-else
        ref="listEl"
        class="mrail-list"
        role="listbox"
        aria-label="Models"
        aria-multiselectable="true"
        tabindex="0"
        :aria-activedescendant="cursorRow ? optionId(cursorRow) : undefined"
        data-testid="mrail-list"
        @keydown="onListKeydown"
        @focus="ensureCursor"
      >
        <div
          v-for="group in groups"
          :key="group.id"
          role="group"
          :aria-labelledby="`mrail-g-${group.id}`"
        >
          <div
            :id="`mrail-g-${group.id}`"
            class="mrail-group section-label"
            role="presentation"
          >
            {{ group.label }}
            <span class="mrail-group-count num">{{ group.rows.length }}</span>
          </div>
          <div
            v-for="row in group.rows"
            :id="optionId(row)"
            :key="row.id"
            class="mrail-row"
            :class="{
              'mrail-row--sel': selected.has(row.id),
              'mrail-row--cur': cursorId === row.id,
              'mrail-row--off': !row.sha256,
            }"
            role="option"
            :aria-selected="selected.has(row.id) ? 'true' : 'false'"
            :aria-description="rowDescription(row)"
            :draggable="draggable(row) ? 'true' : 'false'"
            :data-model-id="row.id"
            @click="onRowClick(row, $event)"
            @contextmenu.prevent="openMenu($event.clientX, $event.clientY, row)"
            @dragstart="onDragStart(row, $event)"
            @dragend="store.railDrag = null"
          >
            <v-icon
              class="mrail-grip"
              :class="{ 'mrail-grip--off': !draggable(row) }"
              size="12"
              aria-hidden="true"
              >mdi-drag-vertical</v-icon
            >
            <ModelMark class="mrail-mark" :row="row" aria-hidden="true" />
            <span class="mrail-text">
              <span class="mrail-name">{{ nameOf(row) }}</span>
              <span class="mrail-why">{{ whyLine(row) }}</span>
            </span>
            <span
              v-if="rowState(row) === 'held'"
              class="mrail-in"
              data-testid="mrail-in-set"
            >
              <v-icon size="14" aria-hidden="true">mdi-check</v-icon>In set
            </span>
            <AppButton
              v-else-if="rowState(row)"
              class="mrail-add"
              variant="outline"
              size="sm"
              tabindex="-1"
              :disabled="rowState(row) === 'hashing' || pending.has(row.id)"
              data-testid="mrail-add"
              @click.stop="addFromRow(row)"
              >{{ addLabel(row) }}</AppButton
            >
          </div>
        </div>
      </div>
    </template>

    <template v-if="tab === 'models'" #footer>
      <div class="mrail-foot">
        <p v-if="footNote" class="mrail-foot-hint" role="status">
          {{ footNote }}
        </p>
        <p v-else-if="filtering" class="mrail-foot-hint">
          Showing {{ shownCount.toLocaleString() }} of
          {{ listed.length.toLocaleString() }} ·
          <button class="mrail-link" type="button" @click="showAll">
            Show all
          </button>
        </p>
        <p v-else-if="!store.handMadeSets.length" class="mrail-foot-hint">
          Drag onto New workflow set, or select and choose
          <em>New workflow set with these</em>.
        </p>
        <div class="mrail-foot-act">
          <span class="mrail-foot-count num">{{ selectionLabel }}</span>
          <span class="mrail-spacer"></span>
          <AppButton
            v-if="targetSet"
            size="sm"
            :disabled="!addableSelection.length"
            data-testid="mrail-add-selection"
            @click="addSelection"
            >Add to {{ targetName }}<kbd class="mrail-kbd" aria-hidden="true"
              >⏎</kbd
            ></AppButton
          >
          <AppBarButton
            ref="moreBtn"
            icon="dots-horizontal"
            tooltip="More for the selected models"
            :disabled="!actionRows.length"
            data-testid="mrail-more"
            @click="openMenuFrom($event.currentTarget)"
          />
        </div>
      </div>
    </template>
  </AppInspector>

  <!-- The rail's verbs, and only those: nothing here deletes, moves or forgets
       a file, because the rail's selection is not the shelf's. -->
  <v-menu
    v-model="menuOpen"
    :target="menuAt"
    location="top end"
    origin="bottom start"
    :offset="2"
    @update:model-value="(open) => open || returnFocus()"
  >
    <div
      ref="menuEl"
      class="ctx-menu mrail-menu"
      role="menu"
      tabindex="-1"
      @keydown="onMenuKeydown"
    >
      <template v-if="menuView === 'main'">
        <button
          v-if="targetSet"
          class="ctx-item"
          type="button"
          role="menuitem"
          :disabled="!addableSelection.length"
          @click="menuAdd(targetSet)"
        >
          <v-icon class="ctx-icon">mdi-plus</v-icon>
          <span class="ctx-label-text">Add to {{ targetName }}</span>
          <span class="ctx-shortcut">Enter</span>
        </button>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          :disabled="!store.handMadeSets.length"
          @click="showSetsView"
        >
          <v-icon class="ctx-icon">mdi-folder-plus-outline</v-icon>
          <span class="ctx-label-text">Add to set…</span>
        </button>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          :disabled="!hashedActionRows.length"
          data-testid="mrail-new-set"
          @click="menuNewSet"
        >
          <v-icon class="ctx-icon">mdi-plus-box-multiple-outline</v-icon>
          <span class="ctx-label-text">{{ newSetLabel }}</span>
        </button>
      </template>
      <template v-else>
        <div class="ctx-label">{{ addSetsHeading }}</div>
        <button
          v-for="choice in setChoices"
          :key="choice.set.id"
          class="ctx-item"
          :class="{ 'ctx-item--disabled': choice.refused }"
          type="button"
          role="menuitem"
          :disabled="Boolean(choice.refused)"
          @click="menuAdd(choice.set)"
        >
          <span class="ctx-label-text">{{ choice.name }}</span>
          <span class="ctx-meta">{{ choice.refused || choice.detail }}</span>
        </button>
        <div class="ctx-sep" role="separator"></div>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          :disabled="!hashedActionRows.length"
          @click="menuNewSet"
        >
          <v-icon class="ctx-icon">mdi-plus-box-multiple-outline</v-icon>
          <span class="ctx-label-text">{{ newSetLabel }}</span>
        </button>
      </template>
    </div>
  </v-menu>

  <!-- What a drag carries, drawn by the browser from this element: the
       grabbed model's name, or how many. Off screen, never on it. -->
  <div ref="dragChipEl" class="mrail-dragchip" aria-hidden="true">
    {{ dragChipText }}
  </div>
</template>

<script setup>
/**
 * The Models screen's right rail (design B, "Models tab in the right rail"):
 * `Models | Tasks` in place of the library's stats.
 *
 * **It lists every model a set slot takes, not only loose ones**, each saying
 * where it already is, because one VAE or one detail LoRA often belongs in
 * several sets. Two optional filters narrow it - *Fits <set>* and *In no set
 * yet* - and neither is ever applied on the reader's behalf.
 *
 * **Its selection is its own.** A click here selects in the rail, not on the
 * shelf, so the shelf's pill never appears and nothing in the rail can reach
 * Delete, Move or Forget. Its verbs are Add to the open set, Add to set…, and a
 * new set with these.
 *
 * **A run of adds is one receipt.** Adds into the same set go out quietly and,
 * once the run pauses, one receipt counts the whole run, with an Undo that
 * takes all of it back. A run ends when an add goes to another set, the set
 * panel closes, or the tab changes. An added row stays where it is with a check,
 * so the row under the pointer never changes and a double click cannot file the
 * next model.
 *
 * Rows are dragged onto the set grid (`ModelSetGrid.vue`): the dragged rows
 * travel in `store.railDrag`, which the grid reads to accept or refuse.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { VIcon, VMenu } from "vuetify/components";

import { GRID_GROUP_BY, useModelShelfStore } from "../../stores/useModelShelfStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { formatModelSize, modelName } from "../../utils/modelShelf";
import {
  defaultSlot,
  handMadeBase,
  handMadeName,
  pictureCount,
  railListed,
  rowBaseModel,
  rowMatches,
  SET_SLOTS,
  setCheckpoint,
  setFits,
  witnessCount,
} from "../../utils/workflowSets";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppInspector from "../widgets/AppInspector.vue";
import ModelMark from "../widgets/ModelMark.vue";
import TasksPanel, { tasksTabFor } from "./TasksPanel.vue";

/** How long the adds have to pause before the run's receipt goes up. */
const RUN_PAUSE_MS = 1500;

const store = useModelShelfStore();
const sidebarStore = useSidebarStore();
const tasksStore = useTasksStore();
const route = useRoute();

// The sets say where each model already is. The store's guard makes a repeat
// of the grid's own read free.
store.loadWorkflowSets();

const tab = ref("models");
watch(
  () => sidebarStore.tasksTabRequest,
  () => {
    tab.value = "tasks";
  },
);

/** Every model the rail can offer: what the tab counts. */
const listed = computed(() => store.rows.filter(railListed));

const tabs = computed(() => [
  {
    value: "models",
    label: "Models",
    icon: "mdi-cube-outline",
    count: listed.value.length,
  },
  tasksTabFor(tasksStore),
]);

// ── Where things are ──────────────────────────────────────────────────────

/** `sha256` → the names of the hand-made sets holding it. */
const setsBySha = computed(() => {
  const map = new Map();
  for (const set of store.handMadeSets) {
    const name = handMadeName(set);
    for (const member of set.members ?? []) {
      if (!member.sha256) continue;
      if (!map.has(member.sha256)) map.set(member.sha256, []);
      map.get(member.sha256).push(name);
    }
  }
  return map;
});

/** Add and drop work on the Workflow set axis only: that is where sets are. */
const onSetAxis = computed(
  () =>
    route.name !== "models-runs" && store.view.groupBy === GRID_GROUP_BY,
);

/** The open hand-made set, which Enter and Add file into, or null. */
const targetSet = computed(() => {
  if (!onSetAxis.value) return null;
  return (
    store.handMadeGroups.find((group) => group.key === store.openSetKey)
      ?.set ?? null
  );
});

const targetName = computed(() =>
  targetSet.value ? handMadeName(targetSet.value) : "",
);

const targetHeld = computed(
  () =>
    new Set((targetSet.value?.members ?? []).map((member) => member.sha256)),
);

// ── Filters ───────────────────────────────────────────────────────────────

const query = ref("");
const needle = computed(() => query.value.trim().toLowerCase());
const looseOnly = ref(false);

/** The set the Fits filter is on, while it still exists. */
const fitsSet = computed(
  () =>
    store.handMadeSets.find((set) => set.id === store.railFitsSetId) ?? null,
);

/** The set the Fits chip would name: the one it is on, else the open one. */
const fitsChipSet = computed(() => fitsSet.value ?? targetSet.value);

const fitsBase = computed(() => handMadeBase(fitsChipSet.value));

const fitsChipLabel = computed(() =>
  [handMadeName(fitsChipSet.value), fitsBase.value].filter(Boolean).join(" · "),
);

function toggleFits() {
  store.railFitsSetId = fitsSet.value ? null : (fitsChipSet.value?.id ?? null);
}

function showAll() {
  store.railFitsSetId = null;
  looseOnly.value = false;
  query.value = "";
}

/** Said in the footer when the filtered set is deleted under the filter. */
const footNote = ref("");
let fitsName = "";
watch(fitsSet, (set) => {
  if (set) {
    fitsName = handMadeName(set);
    footNote.value = "";
    return;
  }
  // The id still points at something the sets no longer hold: it was deleted.
  if (store.railFitsSetId != null && store.setsLoaded) {
    store.railFitsSetId = null;
    footNote.value = `${fitsName || "That set"} was deleted, so the filter was cleared.`;
  }
});

// Any later change of filter is a new answer, and the note was about the old.
watch([looseOnly, needle], () => {
  footNote.value = "";
});

/**
 * What the reader added while Fits was on. Those rows would leave the list the
 * moment the set holds them; they keep their place, with a check, until the
 * filter changes.
 */
const addedHere = ref(new Set());

watch(
  () => store.railFitsSetId,
  (id) => {
    addedHere.value = new Set();
    // Asked for from the set panel's Models · N fit: show the list it opened.
    if (id != null) tab.value = "models";
  },
);

const fits = computed(() =>
  fitsSet.value
    ? setFits(fitsSet.value, listed.value, store.workflowSets.combinations)
    : null,
);

const shown = computed(() => {
  let rows = listed.value;
  if (fits.value) {
    const keep = new Set(fits.value.rows.map((row) => row.id));
    rows = rows.filter(
      (row) => keep.has(row.id) || addedHere.value.has(row.id),
    );
  }
  if (looseOnly.value) {
    rows = rows.filter(
      (row) => !row.sha256 || !setsBySha.value.has(row.sha256),
    );
  }
  if (needle.value) rows = rows.filter((row) => rowMatches(row, needle.value));
  return rows;
});

const shownCount = computed(() => shown.value.length);
const filtering = computed(
  () => Boolean(fitsSet.value || looseOnly.value || needle.value),
);

function nameOf(row) {
  return modelName(row).text || row.filename || "";
}

const byName = (a, b) => nameOf(a).localeCompare(nameOf(b));

/**
 * The list under the set panel's own slot headings. With Fits on, the models
 * pictures ran with the set's checkpoint come first, strongest evidence first,
 * then the rest under the ＋ chooser's "<base> <kind>" headings.
 */
const groups = computed(() => {
  const bySlot = (rows) =>
    SET_SLOTS.map((slot) => ({
      slot,
      rows: rows.filter((row) => defaultSlot(row.file_kind) === slot.id),
    })).filter((entry) => entry.rows.length);
  if (!fits.value) {
    return bySlot([...shown.value].sort(byName)).map(({ slot, rows }) => ({
      id: slot.id,
      label: slot.label,
      rows,
    }));
  }
  const evidence = fits.value.evidence;
  const checkpoint = setCheckpoint(fitsSet.value)?.name ?? "its checkpoint";
  const ranked = shown.value
    .filter((row) => evidence.has(row.id))
    .sort(
      (a, b) =>
        (evidence.get(b.id).pictures || 0) -
          (evidence.get(a.id).pictures || 0) || byName(a, b),
    );
  const rest = shown.value.filter((row) => !evidence.has(row.id)).sort(byName);
  return [
    ...bySlot(ranked).map(({ slot, rows }) => ({
      id: `ran-${slot.id}`,
      label: `${slot.label} · ran with ${checkpoint}`,
      rows,
    })),
    ...bySlot(rest).map(({ slot, rows }) => ({
      id: `fit-${slot.id}`,
      label: fitsBase.value ? `${fitsBase.value} ${slot.noun}` : slot.label,
      rows,
    })),
  ];
});

/** The rows in the order they are drawn: what the arrows walk. */
const flat = computed(() => groups.value.flatMap((group) => group.rows));

// ── A row's state and words ───────────────────────────────────────────────

/**
 * What a row's right-hand column is: "" (no set open), "hashing", "held",
 * "second" (a checkpoint into a set that has one) or "add".
 */
function rowState(row) {
  if (!targetSet.value) return "";
  if (!row.sha256) return "hashing";
  if (targetHeld.value.has(row.sha256)) return "held";
  if (defaultSlot(row.file_kind) === "checkpoint" && setCheckpoint(targetSet.value)) {
    return "second";
  }
  return "add";
}

function addLabel(row) {
  const state = rowState(row);
  if (state === "hashing") return "Hashing…";
  return state === "second" ? "Add as 2nd" : "Add";
}

/** Where a model already is, leaving out the set it is being added to. */
function whereLine(row, { after = false } = {}) {
  const names = (setsBySha.value.get(row.sha256) ?? []).filter(
    (name) => !targetSet.value || name !== targetName.value,
  );
  const held = targetHeld.value.has(row.sha256);
  let text;
  if (!names.length) text = held ? "" : "In no set yet";
  else if (names.length === 1) text = `${held || after ? "Also in" : "In"} ${names[0]}`;
  else text = `${held || after ? "Also in" : "In"} ${names.length} of your sets`;
  if (!text) return "";
  return after ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

function whyLine(row) {
  if (!row.sha256) return "Still hashing, can't be added yet";
  const state = rowState(row);
  if (state === "held" && addedHere.value.has(row.id)) {
    const slot = SET_SLOTS.find((s) => s.id === defaultSlot(row.file_kind));
    return `Added to ${slot?.label ?? "the set"} · Undo on the receipt`;
  }
  if (state === "second") {
    return `This set already has ${setCheckpoint(targetSet.value).name}`;
  }
  const evidence = fits.value?.evidence.get(row.id);
  if (evidence) {
    const checkpoint = setCheckpoint(fitsSet.value)?.name ?? "its checkpoint";
    const witness = evidence.pictures
      ? pictureCount(evidence.pictures)
      : witnessCount(evidence);
    return [`In ${witness} with ${checkpoint}`, whereLine(row, { after: true })]
      .filter(Boolean)
      .join(" · ");
  }
  return [whereLine(row), formatModelSize(row.file_size)]
    .filter(Boolean)
    .join(" · ");
}

/** What Enter does to this row, for a screen reader: the option's description. */
function rowDescription(row) {
  const state = rowState(row);
  const where =
    state === "add" || state === "second"
      ? `Enter adds it to ${targetName.value}.`
      : state === "held"
        ? `In ${targetName.value}.`
        : "";
  return [whyLine(row), where].filter(Boolean).join(". ");
}

function optionId(row) {
  return `mrail-opt-${row.id}`;
}

// ── Selection ─────────────────────────────────────────────────────────────

const selected = ref(new Set());
const cursorId = ref(null);
const anchorId = ref(null);
const listEl = ref(null);

const cursorRow = computed(
  () => flat.value.find((row) => row.id === cursorId.value) ?? null,
);

// A row the filters took away leaves the selection: a verb must not reach
// something the reader can no longer see.
watch(flat, (rows) => {
  const ids = new Set(rows.map((row) => row.id));
  const kept = [...selected.value].filter((id) => ids.has(id));
  if (kept.length !== selected.value.size) selected.value = new Set(kept);
});

function ensureCursor() {
  if (!cursorRow.value && flat.value.length) {
    cursorId.value = flat.value[0].id;
  }
}

function moveTo(row, { extend = false, keep = false } = {}) {
  if (!row) return;
  cursorId.value = row.id;
  if (extend) {
    selectRange(anchorId.value ?? row.id, row.id);
  } else if (!keep) {
    selected.value = new Set([row.id]);
    anchorId.value = row.id;
  }
  nextTick(() =>
    document.getElementById(optionId(row))?.scrollIntoView?.({ block: "nearest" }),
  );
}

function selectRange(fromId, toId) {
  const ids = flat.value.map((row) => row.id);
  const a = ids.indexOf(fromId);
  const b = ids.indexOf(toId);
  if (a < 0 || b < 0) return;
  const [lo, hi] = a < b ? [a, b] : [b, a];
  selected.value = new Set(ids.slice(lo, hi + 1));
}

function onRowClick(row, event) {
  cursorId.value = row.id;
  if (event.shiftKey) {
    selectRange(anchorId.value ?? row.id, row.id);
  } else if (event.ctrlKey || event.metaKey) {
    const next = new Set(selected.value);
    if (next.has(row.id)) next.delete(row.id);
    else next.add(row.id);
    selected.value = next;
    anchorId.value = row.id;
  } else {
    selected.value = new Set([row.id]);
    anchorId.value = row.id;
  }
  listEl.value?.focus({ preventScroll: true });
}

/** What the verbs act on: the selection, else the row under the cursor. */
const actionRows = computed(() => {
  const rows = flat.value.filter((row) => selected.value.has(row.id));
  if (rows.length) return rows;
  return cursorRow.value ? [cursorRow.value] : [];
});

const hashedActionRows = computed(() =>
  actionRows.value.filter((row) => row.sha256),
);

/** The action rows the open set could still take. */
const addableSelection = computed(() =>
  hashedActionRows.value.filter(
    (row) => !targetHeld.value.has(row.sha256) && !pending.value.has(row.id),
  ),
);

const selectionLabel = computed(() => {
  const n = selected.value.size;
  return n ? `${n} selected` : "";
});

const newSetLabel = computed(() => {
  const n = hashedActionRows.value.length;
  return n > 1 ? `New workflow set with these ${n}` : "New workflow set with this";
});

// ── Keyboard ──────────────────────────────────────────────────────────────

let typed = "";
let typedAt = 0;

function onListKeydown(event) {
  const rows = flat.value;
  if (!rows.length) return;
  const at = rows.findIndex((row) => row.id === cursorId.value);
  const mod = event.ctrlKey || event.metaKey;
  const step = (delta) =>
    rows[Math.min(rows.length - 1, Math.max(0, (at < 0 ? -1 : at) + delta))];
  let handled = true;
  switch (event.key) {
    case "ArrowDown":
      moveTo(at < 0 ? rows[0] : step(1), { extend: event.shiftKey, keep: mod });
      break;
    case "ArrowUp":
      moveTo(at < 0 ? rows[0] : step(-1), { extend: event.shiftKey, keep: mod });
      break;
    case "Home":
      moveTo(rows[0], { extend: event.shiftKey, keep: mod });
      break;
    case "End":
      moveTo(rows.at(-1), { extend: event.shiftKey, keep: mod });
      break;
    case " ":
      if (cursorRow.value) {
        const next = new Set(selected.value);
        if (next.has(cursorRow.value.id)) next.delete(cursorRow.value.id);
        else next.add(cursorRow.value.id);
        selected.value = next;
        anchorId.value = cursorRow.value.id;
      }
      break;
    case "Enter":
      addSelection();
      break;
    case "Escape":
      if (!selected.value.size) handled = false;
      selected.value = new Set();
      break;
    case "ContextMenu":
      openMenuAtCursor();
      break;
    case "F10":
      if (event.shiftKey) openMenuAtCursor();
      else handled = false;
      break;
    default:
      if (mod && event.key.toLowerCase() === "a") {
        selected.value = new Set(rows.map((row) => row.id));
      } else if (!mod && !event.altKey && event.key.length === 1) {
        typeAhead(event.key);
      } else {
        handled = false;
      }
  }
  if (handled) {
    event.preventDefault();
    // The shelf and the app have their own Escape, Ctrl+A and letter keys;
    // these belong to the rail's selection only.
    event.stopPropagation();
  }
}

function typeAhead(key) {
  const now = Date.now();
  typed = now - typedAt > 700 ? key : typed + key;
  typedAt = now;
  const lower = typed.toLowerCase();
  const hit = flat.value.find((row) => nameOf(row).toLowerCase().startsWith(lower));
  if (hit) moveTo(hit);
}

// ── Adding, in runs ───────────────────────────────────────────────────────

/** Rows whose add is still on the wire: a second press waits for it. */
const pending = ref(new Set());

/**
 * `{setId, added: [{model_id, slot, sha256, name}], raised, timer}` or null.
 * `raised` is how many of `added` the receipt on screen already counts, so a
 * run that ends after its pause raises nothing twice.
 */
let run = null;

function endRun() {
  if (!run) return;
  clearTimeout(run.timer);
  raiseRun();
  run = null;
}

/** The run's receipt, for what the set still holds of it (an Undo took the rest). */
function raiseRun() {
  if (!run) return;
  const set = store.handMadeSets.find((candidate) => candidate.id === run.setId);
  if (!set) return;
  const held = new Set((set.members ?? []).map((member) => member.sha256));
  run.added = run.added.filter((member) => held.has(member.sha256));
  if (!run.added.length || run.raised === run.added.length) return;
  run.raised = run.added.length;
  const slots = new Set(run.added.map((member) => member.slot));
  const noun =
    slots.size === 1
      ? (SET_SLOTS.find((slot) => slots.has(slot.id))?.noun ?? "models")
      : "models";
  store.announceAdded(set, run.added, noun);
}

watch(
  [() => store.openSetKey, tab, () => sidebarStore.modelsRailOpen],
  () => endRun(),
);
watch(
  () => store.railFitsSetId,
  () => endRun(),
);
onBeforeUnmount(() => endRun());

// A drag whose source row left the DOM mid-flight never sees its `dragend`,
// so the window's own end of every drag clears it too, as does leaving.
function clearDrag() {
  store.railDrag = null;
}
window.addEventListener("dragend", clearDrag, true);
window.addEventListener("drop", clearDrag);
onBeforeUnmount(() => {
  window.removeEventListener("dragend", clearDrag, true);
  window.removeEventListener("drop", clearDrag);
  clearDrag();
});

/**
 * Add shelf rows to a set, quietly, as part of the current run.
 *
 * @param {Object} set
 * @param {Array<Object>} rows
 * @param {{ask?: boolean}} [options] - `ask` confirms a second checkpoint; a
 *   row's own *Add as 2nd* has already said it.
 */
async function addRows(set, rows, { ask = true } = {}) {
  const held = new Set((set.members ?? []).map((member) => member.sha256));
  const fresh = rows.filter(
    (row) => row.sha256 && !held.has(row.sha256) && !pending.value.has(row.id),
  );
  if (!fresh.length) return;
  const members = fresh.map((row) => ({
    model_id: row.id,
    slot: defaultSlot(row.file_kind),
  }));
  if (ask && !(await store.confirmSecondCheckpoint(set, members))) return;
  if (run && run.setId !== set.id) endRun();
  pending.value = new Set([...pending.value, ...fresh.map((row) => row.id)]);
  let result;
  try {
    result = await store.addToHandMadeSet(set, members, { quiet: true });
  } finally {
    const next = new Set(pending.value);
    fresh.forEach((row) => next.delete(row.id));
    pending.value = next;
  }
  const stored = new Set(result?.added ?? []);
  const added = fresh
    .filter((row) => stored.has(row.sha256))
    .map((row) => ({
      model_id: row.id,
      slot: defaultSlot(row.file_kind),
      sha256: row.sha256,
      name: nameOf(row),
    }));
  if (!added.length) return;
  if (fitsSet.value?.id === set.id) {
    addedHere.value = new Set([
      ...addedHere.value,
      ...added.map((member) => member.model_id),
    ]);
  }
  if (!run) run = { setId: set.id, added: [], raised: 0, timer: 0 };
  run.added.push(...added);
  clearTimeout(run.timer);
  run.timer = setTimeout(raiseRun, RUN_PAUSE_MS);
}

/** A row's own Add: that row alone, then the cursor moves on. */
async function addFromRow(row) {
  if (!targetSet.value) return;
  const at = flat.value.findIndex((candidate) => candidate.id === row.id);
  const next = flat.value[at + 1];
  if (next) moveTo(next);
  await addRows(targetSet.value, [row], { ask: false });
}

/** Enter and the footer's Add: the selection, or the cursor's row. */
async function addSelection() {
  if (!targetSet.value) return;
  const rows = addableSelection.value;
  if (!rows.length) return;
  // Only the row's own *Add as 2nd* has said it; Enter and the footer ask.
  if (rows.length === 1 && rows[0].id === cursorId.value) {
    const next = flat.value[flat.value.indexOf(rows[0]) + 1];
    if (next) moveTo(next);
  }
  await addRows(targetSet.value, rows);
}

// ── The menu ──────────────────────────────────────────────────────────────

const menuOpen = ref(false);
const menuAt = ref([0, 0]);
const menuView = ref("main");
const menuEl = ref(null);
const moreBtn = ref(null);
let menuReturn = null;

function openMenu(x, y, row) {
  if (row && !selected.value.has(row.id)) {
    selected.value = new Set([row.id]);
    anchorId.value = row.id;
  }
  if (row) cursorId.value = row.id;
  menuReturn = listEl.value;
  menuAt.value = [x, y];
  menuView.value = "main";
  menuOpen.value = true;
  focusMenu();
}

function openMenuFrom(el) {
  const box = el?.getBoundingClientRect?.();
  menuReturn = el;
  menuAt.value = box ? [box.left, box.top] : [0, 0];
  menuView.value = "main";
  menuOpen.value = true;
  focusMenu();
}

function openMenuAtCursor() {
  const el = cursorRow.value
    ? document.getElementById(optionId(cursorRow.value))
    : null;
  const box = el?.getBoundingClientRect?.();
  openMenu(box ? box.left + 24 : 0, box ? box.bottom : 0, cursorRow.value);
}

function focusMenu() {
  nextTick(() =>
    nextTick(() =>
      menuEl.value?.querySelector(".ctx-item:not(:disabled)")?.focus(),
    ),
  );
}

function showSetsView() {
  menuView.value = "sets";
  focusMenu();
}

/** Programmatic menus drop focus to <body> on close; put it back. */
function returnFocus() {
  const el = menuReturn?.isConnected ? menuReturn : listEl.value;
  menuReturn = null;
  el?.focus?.({ preventScroll: true });
}

async function menuAdd(set) {
  menuOpen.value = false;
  await addRows(set, hashedActionRows.value);
}

async function menuNewSet() {
  menuOpen.value = false;
  endRun();
  await store.createSetFromRows(hashedActionRows.value);
}

const addSetsHeading = computed(() => {
  const rows = hashedActionRows.value;
  const kinds = new Set(rows.map((row) => defaultSlot(row.file_kind)));
  const slot = kinds.size === 1 ? SET_SLOTS.find((s) => kinds.has(s.id)) : null;
  const what =
    rows.length === 1
      ? nameOf(rows[0])
      : `${rows.length} ${slot?.noun ?? "models"}`;
  return `Add ${what} to`;
});

/**
 * Your sets, each saying where the models would go or what it already holds.
 * A set on another base model is shown, faded, with the reason.
 */
const setChoices = computed(() => {
  const rows = hashedActionRows.value;
  const bases = new Set(rows.map(rowBaseModel).filter(Boolean));
  const rowsBase = bases.size === 1 ? [...bases][0] : null;
  const kinds = new Set(rows.map((row) => defaultSlot(row.file_kind)));
  const slot = kinds.size === 1 ? SET_SLOTS.find((s) => kinds.has(s.id)) : null;
  return store.handMadeSets.map((set) => {
    const held = new Set((set.members ?? []).map((member) => member.sha256));
    const already = rows.filter((row) => held.has(row.sha256)).length;
    const base = handMadeBase(set);
    const refused =
      already === rows.length
        ? "Already in it"
        : base && rowsBase && base !== rowsBase
          ? `${base}, not ${rowsBase}`
          : "";
    const detail = already
      ? `${already} already in it`
      : `→ ${slot?.label ?? "their slots"}`;
    return { set, name: handMadeName(set), refused, detail };
  });
});

// ── Dragging ──────────────────────────────────────────────────────────────

const dragChipEl = ref(null);
const dragChipText = ref("");

function draggable(row) {
  return Boolean(row.sha256) && onSetAxis.value;
}

/**
 * Drag the selection when the grabbed row is in it, else that row alone (and
 * it becomes the selection): the file-manager rule the shelf follows.
 */
function onDragStart(row, event) {
  if (!draggable(row)) {
    event.preventDefault();
    return;
  }
  if (!selected.value.has(row.id)) {
    selected.value = new Set([row.id]);
    anchorId.value = row.id;
  }
  cursorId.value = row.id;
  endRun();
  const rows = flat.value.filter(
    (candidate) => selected.value.has(candidate.id) && candidate.sha256,
  );
  store.railDrag = rows;
  dragChipText.value = rows.length === 1 ? nameOf(rows[0]) : `${rows.length} models`;
  event.dataTransfer.effectAllowed = "copy";
  event.dataTransfer.setData(
    "application/x-pixlstash-models",
    JSON.stringify(rows.map((candidate) => candidate.id)),
  );
  if (dragChipEl.value) {
    // The chip has to hold its text before the browser draws it.
    dragChipEl.value.textContent = dragChipText.value;
    event.dataTransfer.setDragImage(dragChipEl.value, 12, 12);
  }
}
</script>

<style scoped>
.mrail-head {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.mrail-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.mrail-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: var(--tag-h-xs);
  padding: 0 var(--space-3);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.24);
  border-radius: var(--radius-pill);
  background: none;
  font: inherit;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
}

.mrail-chip:hover {
  background: var(--hover-wash);
}

.mrail-chip--on {
  border-color: var(--active-bar);
  background: var(--active-wash);
  color: var(--active-text);
}

.mrail-empty {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mrail-list {
  display: flex;
  flex-direction: column;
  /* Full-bleed rows inside the body's inline padding. */
  margin: 0 calc(-1 * var(--space-3));
  outline: none;
}

.mrail-list:focus-visible .mrail-row--cur {
  box-shadow: var(--focus-ring-inset);
}

.mrail-group {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-3) var(--space-1);
}

.mrail-group-count {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mrail-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 44px;
  padding: var(--space-1) var(--space-3) var(--space-1) var(--space-1);
  cursor: default;
  /* Thousands of rows: off-screen ones skip layout and paint. */
  content-visibility: auto;
  contain-intrinsic-size: auto 44px;
}

.mrail-row:hover {
  background: var(--hover-wash);
}

.mrail-row--sel {
  background: var(--active-wash);
}

.mrail-row--off .mrail-name {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mrail-grip {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: grab;
}

.mrail-grip--off {
  visibility: hidden;
}

.mrail-mark {
  flex-shrink: 0;
}

.mrail-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.mrail-name,
.mrail-why {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mrail-name {
  font-size: var(--text-sm);
}

.mrail-why {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mrail-add {
  flex-shrink: 0;
}

.mrail-in {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--selected-ink);
}

.mrail-foot {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.mrail-foot-hint {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mrail-link {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
  cursor: pointer;
}

.mrail-foot-act {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.mrail-foot-count {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
}

.mrail-spacer {
  flex: 1;
}

.mrail-kbd {
  margin-left: var(--space-2);
  font-family: inherit;
  opacity: 0.7;
}

.mrail-dragchip {
  position: fixed;
  top: -1000px;
  left: -1000px;
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
  white-space: nowrap;
}
</style>
