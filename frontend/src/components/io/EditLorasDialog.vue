<template>
  <AppDialog
    :open="open"
    title="Edit LoRAs"
    :subtitle="cardName"
    size="md"
    @close="close"
    @accept="onAccept"
  >
    <p v-if="loading" class="eld-note eld-quiet">Reading the chain…</p>
    <p v-else-if="loadError" class="eld-note eld-bad" role="alert">
      {{ loadError }}
    </p>

    <!-- ── Step one: the chain ───────────────────────────────────────── -->
    <template v-else-if="step === 'edit'">
      <!-- The planner's own sentence, shown rather than hit: an edit that
           cannot be saved is not arranged first and refused after. -->
      <div v-if="!editable" class="eld-refusal" role="status">
        <v-icon size="16" class="eld-refusal-glyph">mdi-lock-outline</v-icon>
        <p class="eld-note">
          {{ refusal || "This chain cannot be edited just now." }}
          Delete, drag and Add are off; the list is shown as read.
        </p>
      </div>

      <p v-if="dropMissed" class="eld-note eld-quiet" role="status">
        This workflow has no loader for {{ dropMissed }}, so nothing was
        deleted for it.
      </p>

      <!-- The rails say what "order" is order of, and are not rows: nothing
           drags onto or past them. -->
      <div class="eld-chain">
        <div class="eld-rail" data-testid="eld-source">
          <template v-if="chain?.source">
            <span class="eld-rail-name">{{ chain.source.class_type }}</span>
            <span class="eld-quiet"
              >#{{ chain.source.node_id }} · hands out
              {{ (chain.source.outputs || []).join(", ") }}</span
            >
          </template>
          <span v-else class="eld-quiet">No model source found</span>
        </div>

        <ol ref="listEl" class="eld-list" aria-label="LoRAs, in the order the chain applies them">
          <li
            v-for="(row, index) in rows"
            :key="row.id"
            class="eld-row"
            :class="{
              'eld-row--deleted': row.deleted,
              'eld-row--dragging': draggingId === row.id,
              'eld-row--over': overId === row.id && draggingId !== row.id,
            }"
            :data-row="row.id"
            @dragover.prevent="onDragOver(row)"
            @dragleave="onDragLeave(row)"
            @drop.prevent="onDrop(index)"
          >
            <div class="eld-line">
              <!-- The handle is the drag source AND the keyboard path, and the
                   keyboard path is in its name: a tooltip needs a hover a
                   keyboard user does not have. The Recipes tab's idiom. -->
              <button
                class="eld-handle"
                type="button"
                data-focus="handle"
                :draggable="canReorder(row) ? 'true' : 'false'"
                :disabled="!canReorder(row)"
                :aria-label="`Reorder ${row.name || 'this LoRA'}: hold Alt and press the up or down arrow, or drag`"
                @dragstart="onDragStart(row, $event)"
                @dragend="onDragEnd"
                @keydown.up.alt.prevent="move(index, -1)"
                @keydown.down.alt.prevent="move(index, 1)"
              >
                <Tooltip
                  text="Drag to reorder, or Alt with the arrow keys"
                  activator="parent"
                  :describe="false"
                />
                <v-icon size="16">mdi-drag-vertical</v-icon>
              </button>

              <span class="eld-pos" aria-hidden="true">{{
                row.deleted ? "—" : positions[row.id]
              }}</span>

              <span v-if="row.isNew && !row.sha256" class="eld-name">
                <AppSelect
                  :model-value="row.sha256"
                  :label="`LoRA to add, row ${positions[row.id]}`"
                  hide-label
                  compact
                  :options="pickerOptions"
                  :disabled="saving"
                  @update:model-value="(value) => pick(row, value)"
                />
              </span>
              <span v-else class="eld-name">
                <v-icon
                  v-if="!row.onShelf && !row.isNew"
                  size="16"
                  class="eld-flag-glyph"
                  aria-hidden="true"
                  >mdi-alert-outline</v-icon
                >
                <span class="eld-name-text">{{
                  row.onShelf || row.isNew ? row.name : row.fileBase
                }}</span>
                <span v-if="row.isNew" class="eld-tag">new</span>
                <span v-if="row.deleted" class="visually-hidden"
                  >, deleted</span
                >
              </span>

              <span v-if="row.deleted || !row.hasStrength" class="eld-strength eld-quiet">
                {{ row.deleted ? "—" : "wired" }}
              </span>
              <AppInput
                v-else
                class="eld-strength"
                :model-value="row.strengthText"
                :aria-label="`Strength of ${row.name || 'this LoRA'}`"
                type="number"
                min="-10"
                max="10"
                :disabled="!editable || saving"
                :error="strengthInvalid(row)"
                @update:model-value="(value) => (row.strengthText = value)"
                @keydown.stop
              />

              <!-- Delete, not ×: the entry leaves the workflow, and an × in
                   this app closes things. A deleted row stays on screen with
                   Restore until the dialog is saved or cancelled. -->
              <AppButton
                v-if="row.deleted"
                size="sm"
                data-focus="restore"
                :aria-label="`Restore ${row.name || 'this LoRA'}`"
                :disabled="saving"
                @click="restore(row)"
              >
                Restore
              </AppButton>
              <AppBarButton
                v-else
                icon="delete-outline"
                data-focus="delete"
                :tooltip="`Delete ${row.name || 'this LoRA'}`"
                :disabled="!editable || saving"
                @click="remove(row)"
              />
            </div>
            <p
              v-if="!row.onShelf && !row.isNew && !row.deleted"
              class="eld-note eld-flag"
            >
              {{ row.fileBase }} is missing from your model shelf.
            </p>
          </li>
        </ol>

        <div class="eld-add">
          <AppButton
            size="sm"
            icon-left="plus"
            :disabled="!canAdd"
            @click="add"
          >
            Add a LoRA
          </AppButton>
          <span v-if="editable && !shelf.length && shelfRead" class="eld-note eld-quiet">
            Your model shelf has no LoRA to add.
          </span>
        </div>

        <div class="eld-rail" data-testid="eld-sink">
          <span v-if="chain?.sink" class="eld-rail-name">{{
            chain.sink.summary
          }}</span>
          <span v-else class="eld-quiet">Nothing found reading the chain</span>
        </div>
      </div>

      <!-- The empty chain says which loader class goes in, rather than
           surprising the owner with a node they did not choose. -->
      <p v-if="firstLoaderNote" class="eld-note eld-quiet">
        {{ firstLoaderNote }}
      </p>

      <p v-if="editable" class="eld-note eld-quiet">
        Saving writes a new workflow. <b class="eld-strong">{{ cardName }}</b>
        and its {{ picturesLabel }} stay as they are.
      </p>
    </template>

    <!-- ── Step two: a named thing, and what it changes ──────────────── -->
    <template v-else>
      <AppInput
        v-model="newName"
        label="Name of the new workflow"
        :disabled="saving"
        autofocus
        @enter="save"
      />
      <div>
        <span class="section-label">What changes</span>
        <p v-if="previewing" class="eld-note eld-quiet">
          Working out what changes…
        </p>
        <p v-else-if="previewError" class="eld-note eld-bad" role="alert">
          {{ previewError }}
        </p>
        <ul v-else class="eld-changes" data-testid="eld-changes">
          <li v-for="(change, index) in changes" :key="`${index}:${change.kind}`">
            {{ change.text }}
          </li>
        </ul>
      </div>
      <p class="eld-note eld-quiet">
        The original keeps its {{ picturesLabel }}. Nothing made before this
        edit changes meaning.
      </p>
      <p v-if="saveError" class="eld-note eld-bad" role="alert">
        {{ saveError }}
      </p>
    </template>

    <!-- One live region for the gestures a reader who is not watching the
         list cannot see: a move, a delete, a restore. -->
    <p class="visually-hidden" role="status" aria-live="polite">
      {{ liveMessage }}
    </p>

    <template #footer>
      <template v-if="step === 'edit'">
        <span v-if="editable" class="eld-count" data-testid="eld-count">
          {{ changeLabel }}
        </span>
        <AppButton @click="close">{{ editable ? "Cancel" : "Close" }}</AppButton>
        <AppButton
          v-if="editable"
          variant="primary"
          :disabled="!canContinue"
          @click="toConfirm"
        >
          Save…
        </AppButton>
      </template>
      <template v-else>
        <AppButton :disabled="saving" @click="step = 'edit'">Back</AppButton>
        <AppButton
          variant="primary"
          :loading="saving"
          :disabled="!canSave"
          @click="save"
        >
          Save as a new workflow
        </AppButton>
      </template>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * Edit LoRAs (#1478, direction B): a workflow's LoRA chain as one list
 * between two fixed ends, saved as a NEW workflow.
 *
 * The model source above, the sink below, and between them the loaders in
 * the order the chain applies them — so dragging row 3 above row 2 genuinely
 * makes a different picture. Three gestures and no others: delete a row, drag
 * a row (Alt+↑/↓ on the focused handle does the same), add one to the end.
 *
 * **Nothing is written until the second step.** Save… asks the route for a dry
 * run and lists the changes it answers with — the rewires included, which are
 * the part no owner can see — beside the new workflow's name; only "Save as a
 * new workflow" writes. The original card and its pictures never change: the
 * route stores a content-addressed copy.
 *
 * **Read-only when the planner refuses** (`editable: false`): ComfyUI down,
 * two model sources, a stacker node… The refusal is the server's sentence and
 * is shown instead of letting an edit be arranged that cannot be saved.
 */
import { computed, nextTick, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { listAdapters } from "../../api/modelShelf";
import { getLoraChain, saveLoraChain } from "../../api/workflows";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import {
  chainEntries,
  countChanges,
  loraBase,
  loraStem,
} from "../../utils/loraChain";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The card whose chain is edited. The save never modifies it. */
  workflowKey: { type: String, default: "" },
  /** What the owner calls the card: the subtitle, and the new name's stem. */
  cardName: { type: String, default: "" },
  /** How many pictures the card made, for "its N pictures stay as they are". */
  pictureCount: { type: Number, default: 0 },
  /**
   * A LoRA filename to open with its loader already struck through.
   *
   * Save-as-recipe's "The workflow" answer: the owner already said which
   * entry they meant, so the dialog opens with it deleted and Restore beside
   * it rather than asking them to find it again.
   */
  dropLora: { type: String, default: "" },
});

const emit = defineEmits(["close", "saved"]);

const notices = useNoticeStore();
const workflows = useWorkflowsStore();

/** `GET …/lora-chain`, as read. */
const chain = ref(null);
const loading = ref(false);
const loadError = ref("");
/** The rows on screen, in list order. See `rowOf`. */
const rows = ref([]);
/** The shelf's digested LoRAs, for Add. */
const shelf = ref([]);
const shelfRead = ref(false);

/** "edit" (the chain) or "confirm" (the name and what changes). */
const step = ref("edit");
const newName = ref("");
const changes = ref([]);
const previewing = ref(false);
const previewError = ref("");
const saving = ref(false);
const saveError = ref("");
/** A `dropLora` the chain has no loader for, said rather than ignored. */
const dropMissed = ref("");

const draggingId = ref(null);
const overId = ref(null);
const liveMessage = ref("");
const listEl = ref(null);

let newRows = 0;
/** Bumped per open, so a slow read for a closed dialog writes nothing. */
let loadToken = 0;

const editable = computed(() => Boolean(chain.value?.editable));
const refusal = computed(() => chain.value?.refusal || "");

const picturesLabel = computed(() => {
  const count = Number(props.pictureCount) || 0;
  return `${count} ${count === 1 ? "picture" : "pictures"}`;
});

/** Each standing row's 1-based place in the chain; a deleted row has none. */
const positions = computed(() => {
  const out = {};
  let at = 0;
  for (const row of rows.value) {
    if (!row.deleted) {
      at += 1;
      out[row.id] = at;
    }
  }
  return out;
});

/** The rows with their strength as a number, the shape the helpers read. */
const effectiveRows = computed(() =>
  rows.value.map((row) => ({
    ...row,
    strength: !row.hasStrength
      ? null
      : row.strengthText === fmt(row.readStrength)
        ? row.readStrength
        : Number(row.strengthText),
  })),
);

const changeCount = computed(() =>
  countChanges(chain.value?.loaders || [], effectiveRows.value),
);

const changeLabel = computed(() => {
  const count = changeCount.value;
  if (!count) return "No changes";
  return `${count} ${count === 1 ? "change" : "changes"}`;
});

const pickerOptions = computed(() => [
  { value: "", label: "Pick a LoRA from your shelf…" },
  ...shelf.value.map((adapter) => ({
    value: String(adapter.sha256),
    label: adapter.display_name || loraStem(adapter.filename) || adapter.sha256,
  })),
]);

/** A new row with nothing picked yet: Add waits for it, and so does Save. */
const unpicked = computed(() =>
  rows.value.some((row) => row.isNew && !row.sha256),
);

const canAdd = computed(
  () =>
    editable.value &&
    !saving.value &&
    !unpicked.value &&
    shelf.value.length > 0,
);

const canContinue = computed(
  () =>
    editable.value &&
    changeCount.value > 0 &&
    !unpicked.value &&
    !rows.value.some(strengthInvalid),
);

const canSave = computed(
  () =>
    !saving.value &&
    !previewing.value &&
    !previewError.value &&
    canContinue.value,
);

/**
 * Where the first loader goes, said before it is saved.
 *
 * Only for a chain with no loader of its own: there the class that goes in
 * is decided in code (LoraLoader with a CLIP source, LoraLoaderModelOnly
 * without), and the owner is told which rather than finding a node they did
 * not choose.
 */
const firstLoaderNote = computed(() => {
  const read = chain.value;
  if (!read || !editable.value || (read.loaders || []).length) return "";
  if (!rows.value.some((row) => row.isNew)) return "";
  const cls = read.added_loader_class;
  const source = read.source;
  const readers = (read.sink?.consumers || []).filter(
    (consumer) => String(consumer.type || "").toUpperCase() === "MODEL",
  ).length;
  const parts = [];
  if (source) {
    parts.push(
      `The loader goes in after #${source.node_id} ${source.class_type}, and the ${readers} ${readers === 1 ? "input" : "inputs"} reading its MODEL ${readers === 1 ? "is" : "are"} rewired to it.`,
    );
  }
  if (cls === "LoraLoaderModelOnly") {
    parts.push("This graph has no CLIP source, so LoraLoaderModelOnly is used.");
  } else if (cls) {
    parts.push(`This graph has a CLIP source, so ${cls} is used.`);
  }
  return parts.join(" ");
});

function strengthInvalid(row) {
  if (row.deleted || !row.hasStrength) return false;
  const text = String(row.strengthText ?? "").trim();
  const value = Number(text);
  return !text || !Number.isFinite(value) || value < -10 || value > 10;
}

function fmt(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "";
}

/** One row of the list, from one loader of the chain as read. */
function rowOf(loader) {
  const hasStrength = loader.strength !== null && loader.strength !== undefined;
  return {
    id: `n:${loader.node_id}`,
    nodeId: String(loader.node_id),
    name: loader.name || loraStem(loader.filename),
    fileBase: String(loader.filename || "").split(/[\\/]/).pop() || loader.name,
    filename: loader.filename || "",
    sha256: loader.sha256 || "",
    onShelf: Boolean(loader.on_shelf),
    hasStrength,
    strengthText: hasStrength ? fmt(loader.strength) : "",
    // The value as read, so a field left alone saves 0.855 and not the 0.86
    // it displays: re-weighting a loader nobody touched is a change the
    // owner did not make.
    readStrength: hasStrength ? Number(loader.strength) : null,
    deleted: false,
    isNew: false,
  };
}

function say(message) {
  liveMessage.value = message;
}

/** Focus one control of one row once the list has re-rendered. */
async function focusRow(rowId, which) {
  await nextTick();
  const el = listEl.value?.querySelector(
    `[data-row="${rowId}"] [data-focus="${which}"]`,
  );
  el?.focus?.();
}

async function load() {
  const token = (loadToken += 1);
  const mine = () => token === loadToken;
  step.value = "edit";
  chain.value = null;
  rows.value = [];
  loadError.value = "";
  saveError.value = "";
  dropMissed.value = "";
  newName.value = "";
  changes.value = [];
  say("");
  if (!props.workflowKey) return;
  loading.value = true;
  try {
    const read = await getLoraChain(props.workflowKey);
    if (!mine()) return;
    chain.value = read;
    rows.value = (read?.loaders || []).map(rowOf);
    applyDrop();
  } catch (err) {
    if (!mine()) return;
    console.warn(
      `[workflows] could not read the LoRA chain of ${props.workflowKey}`,
      err,
    );
    loadError.value = errorMessage(err, "Could not read this workflow's LoRAs.");
  } finally {
    if (mine()) loading.value = false;
  }
  void loadShelf();
}

/**
 * Strike through the loader Save-as-recipe named, if the chain has it.
 *
 * Matched on the case-folded base name, which is how the shelf matches a
 * file, so a recipe naming `Hairstyle-V3.safetensors` finds the loader that
 * reads `loras/hairstyle-v3.safetensors`. A read-only chain strikes nothing:
 * a delete there is exactly what cannot be saved.
 */
function applyDrop() {
  const wanted = loraBase(props.dropLora);
  if (!wanted || !editable.value) return;
  const row = rows.value.find(
    (entry) =>
      !entry.deleted &&
      (loraBase(entry.filename) === wanted ||
        loraBase(entry.fileBase) === wanted),
  );
  if (!row) {
    dropMissed.value = props.dropLora;
    return;
  }
  row.deleted = true;
  say(`${row.name} is deleted. Restore is on its row.`);
}

async function loadShelf() {
  if (shelfRead.value) return;
  try {
    const adapters = await listAdapters();
    shelf.value = (adapters || []).filter((adapter) => adapter?.sha256);
  } catch (err) {
    // Add is the only thing this costs, and it says so by staying disabled.
    console.warn("[workflows] could not read the model shelf's LoRAs", err);
    shelf.value = [];
  } finally {
    shelfRead.value = true;
  }
}

// ── The three gestures ─────────────────────────────────────────────────────

function canReorder(row) {
  return editable.value && !row.deleted && !saving.value;
}

async function remove(row) {
  if (!editable.value) return;
  if (row.isNew) {
    // A loader that was never in the graph has nothing to restore: it goes.
    rows.value = rows.value.filter((entry) => entry.id !== row.id);
    say(`The new ${row.sha256 ? row.name : "LoRA"} row is taken out.`);
    return;
  }
  row.deleted = true;
  say(`${row.name} deleted. Restore is on the same row.`);
  await focusRow(row.id, "restore");
}

async function restore(row) {
  row.deleted = false;
  say(`${row.name} restored, at ${positions.value[row.id]}.`);
  await focusRow(row.id, "delete");
}

/** Append one row with a shelf picker in it, just above the sampler. */
async function add() {
  if (!canAdd.value) return;
  newRows += 1;
  const row = {
    id: `new:${newRows}`,
    nodeId: null,
    name: "",
    fileBase: "",
    filename: "",
    sha256: "",
    onShelf: true,
    hasStrength: true,
    strengthText: fmt(1),
    deleted: false,
    isNew: true,
  };
  rows.value = [...rows.value, row];
  say(`A new row is at ${positions.value[row.id]}. Pick a LoRA for it.`);
  await nextTick();
  listEl.value
    ?.querySelector(`[data-row="${row.id}"] select`)
    ?.focus?.();
}

function pick(row, sha256) {
  const found = shelf.value.find((adapter) => String(adapter.sha256) === sha256);
  row.sha256 = sha256 || "";
  row.name = found
    ? found.display_name || loraStem(found.filename)
    : "";
  row.filename = found?.filename || "";
  row.fileBase = String(found?.filename || "").split(/[\\/]/).pop();
}

function onDragStart(row, event) {
  if (!canReorder(row)) return;
  draggingId.value = row.id;
  // Firefox starts no drag without a payload; `move` makes the cursor say so.
  event.dataTransfer?.setData("text/plain", row.id);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
}

function onDragOver(row) {
  if (draggingId.value) overId.value = row.id;
}

function onDragLeave(row) {
  if (overId.value === row.id) overId.value = null;
}

function onDragEnd() {
  draggingId.value = null;
  overId.value = null;
}

function onDrop(toIndex) {
  const from = rows.value.findIndex((row) => row.id === draggingId.value);
  onDragEnd();
  if (from < 0 || from === toIndex) return;
  place(from, toIndex, { focus: false });
}

function move(index, delta) {
  const row = rows.value[index];
  if (!row || !canReorder(row)) return;
  // Past any deleted row: it stays in the list struck through, and swapping
  // with it moves nothing the save would write while announcing a move.
  let to = index + delta;
  while (to >= 0 && to < rows.value.length && rows.value[to].deleted) to += delta;
  if (to < 0 || to >= rows.value.length) return;
  place(index, to, { focus: true });
}

/**
 * Move one row and say where it landed.
 *
 * Nothing is written: a move is one more gesture for the save. Focus follows
 * the handle that moved, so Alt+↓ held down walks one row down the list.
 */
function place(from, to, { focus }) {
  const next = rows.value.slice();
  const [row] = next.splice(from, 1);
  next.splice(to, 0, row);
  rows.value = next;
  const live = next.filter((entry) => !entry.deleted).length;
  say(
    `${row.name || "The new LoRA"} moved to ${positions.value[row.id]} of ${live}.`,
  );
  if (focus) void focusRow(row.id, "handle");
}

// ── Saving: a dry run to list the changes, then the write ────────────────

function requestBody(dryRun) {
  return {
    entries: chainEntries(effectiveRows.value),
    name: newName.value.trim() || null,
    dry_run: dryRun,
  };
}

async function toConfirm() {
  if (!canContinue.value) return;
  step.value = "confirm";
  saveError.value = "";
  previewError.value = "";
  if (!newName.value.trim()) {
    newName.value = props.cardName ? `${props.cardName} (edited)` : "";
  }
  previewing.value = true;
  changes.value = [];
  const token = loadToken;
  try {
    const answer = await saveLoraChain(props.workflowKey, requestBody(true));
    if (token !== loadToken) return;
    changes.value = Array.isArray(answer?.changes) ? answer.changes : [];
  } catch (err) {
    if (token !== loadToken) return;
    console.warn("[workflows] the LoRA chain dry run was refused", err);
    previewError.value = errorMessage(
      err,
      "PixlStash could not work out what this edit changes.",
    );
  } finally {
    if (token === loadToken) previewing.value = false;
  }
}

async function save() {
  if (!canSave.value) return;
  saving.value = true;
  saveError.value = "";
  try {
    const answer = await saveLoraChain(props.workflowKey, requestBody(false));
    const saved = String(answer?.name || newName.value.trim() || "");
    notices.push({
      level: "success",
      text: saved
        ? `Saved “${saved.replace(/\.json$/i, "")}” as a new workflow.`
        : "Saved as a new workflow.",
    });
    // The new card is a card of its own, so the grid is re-read and the rail
    // moves onto it: the owner's next look is at what they just made.
    workflows.forgetMembers();
    await workflows.fetchCards();
    if (answer?.workflow_key) workflows.select(answer.workflow_key);
    emit("saved", answer);
    emit("close");
  } catch (err) {
    console.warn("[workflows] the LoRA chain save was refused", err);
    saveError.value = errorMessage(err, "Could not save the edited LoRAs.");
  } finally {
    saving.value = false;
  }
}

function onAccept() {
  if (step.value === "edit") void toConfirm();
  else void save();
}

function close() {
  if (saving.value) return;
  emit("close");
}

watch(
  () => [props.open, props.workflowKey],
  ([isOpen]) => {
    if (isOpen) void load();
    else loadToken += 1;
  },
  { immediate: true },
);

defineExpose({ rows, step, changeCount });
</script>

<style scoped>
.eld-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

.eld-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.eld-bad {
  color: rgb(var(--v-theme-surface-error));
}

.eld-strong {
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-semibold);
}

/* The notice surface's shape: a rail down the left edge in the status hue,
   the glyph in the same hue, and the sentence left as ordinary text
   (docs/design/notice-surface.md). Warning, not error: nothing failed, the
   chain is only not editable here. */
.eld-refusal {
  display: grid;
  grid-template-columns: var(--gutter-glyph) minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-left: var(--rail-w) solid rgb(var(--v-theme-surface-warning));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.eld-refusal-glyph {
  color: rgb(var(--v-theme-surface-warning));
}

.eld-chain {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* The two ends: filled, not bordered like a row, and with no handle, so
   they read as the frame the list sits in rather than two more entries. */
.eld-rail {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-3);
  min-height: var(--control-h);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  font-size: var(--text-xs);
}

.eld-rail-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.eld-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.eld-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
}

.eld-row--dragging {
  opacity: var(--opacity-disabled);
}

/* Where a drop would land: the selection's bar, because the target is the
   thing chosen, not an action. */
.eld-row--over {
  box-shadow: inset 0 var(--rail-w) 0 var(--active-bar);
}

.eld-line {
  display: grid;
  grid-template-columns: var(--gutter-glyph) var(--space-5) minmax(0, 1fr) var(--space-9) auto;
  align-items: center;
  gap: var(--space-3);
}

.eld-handle {
  display: inline-flex;
  align-items: center;
  border: 0;
  background: none;
  padding: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: grab;
}

.eld-handle:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}

.eld-pos {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-align: right;
}

.eld-name {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  font-size: var(--text-sm);
}

.eld-name > .app-select {
  flex: 1;
  min-width: 0;
}

.eld-name-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* A deleted row stays legible as a row that was taken out. */
.eld-row--deleted .eld-name-text {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-decoration: line-through;
}

.eld-tag {
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.eld-flag-glyph {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

.eld-flag {
  padding-left: calc(var(--gutter-glyph) + var(--space-5) + var(--space-3) * 2);
}

.eld-strength {
  font-variant-numeric: tabular-nums;
  font-size: var(--text-sm);
}

.eld-add {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.eld-changes {
  margin: var(--space-2) 0 0;
  padding-left: var(--space-5);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.eld-count {
  margin-right: auto;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
