<template>
  <AppDialog
    :open="open"
    title="Edit LoRAs"
    :subtitle="cardName"
    :size="branched ? 'xl' : 'md'"
    :fullscreen="lanes.length > 2"
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
          Deleting, dragging and adding are off; the list is shown as read.
        </p>
      </div>

      <p v-if="dropMissed" class="eld-note eld-quiet" role="status">
        This workflow has no loader for {{ dropMissed }}, so nothing was
        deleted for it.
      </p>

      <!-- The chain drawn as a graph: the model source, each loader and what
           reads the result, one node each, with an arrow from every node to
           the next. The two ends are not rows: nothing drags onto or past
           them. -->
      <div ref="listEl">
      <div v-if="!branched" class="eld-chain">
        <div class="eld-node" data-testid="eld-source">
          <template v-if="chain?.source">
            <span class="eld-node-title">{{ chain.source.class_type }}</span>
            <span class="eld-node-body eld-quiet"
              >#{{ chain.source.node_id }} · hands out
              {{ (chain.source.outputs || []).join(", ") }}</span
            >
          </template>
          <span v-else class="eld-node-body eld-quiet">No model source found</span>
        </div>

        <ol class="eld-list" aria-label="LoRAs, in the order the chain applies them">
          <EditLorasRow
            v-for="row in segmentRows(null)"
            :key="row.id"
            v-bind="rowProps(row)"
            v-on="rowHandlers(row)"
          />
        </ol>

        <!-- Add appends the last loader, so it sits on the last wire, the
             one into the node that reads the chain. -->
        <span class="eld-wire eld-wire--plain" aria-hidden="true"></span>
        <div class="eld-add">
          <AppButton
            size="sm"
            icon-left="plus"
            :disabled="!canAdd"
            :aria-label="spliceLabel"
            @click="add(null)"
          >
            {{ spliceIn ? "Insert LoRA between these nodes" : "Add a LoRA" }}
          </AppButton>
          <span v-if="editable && !shelf.length && shelfRead" class="eld-note eld-quiet">
            Your model shelf has no LoRA to add.
          </span>
        </div>

        <span class="eld-wire" aria-hidden="true"></span>

        <div class="eld-node" data-testid="eld-sink">
          <span v-if="chain?.sink?.summary" class="eld-node-title">{{
            chain.sink.summary
          }}</span>
          <span v-else class="eld-node-body eld-quiet"
            >Nothing found reading the chain</span
          >
        </div>
      </div>

      <!-- The model forks: the loaders every pass reads once, at the top, then
           a drawn fork into one lane per sampler, side by side. Every loader
           appears exactly once, where the model actually passes through it. -->
      <div v-else class="eld-graph" data-testid="eld-graph">
        <div v-if="chain.source" class="eld-trunk">
          <div class="eld-node" data-testid="eld-source">
            <span class="eld-node-title"
              >{{ chain.source.class_type }} #{{ chain.source.node_id }}</span
            >
            <span class="eld-node-body eld-quiet"
              >hands out {{ (chain.source.outputs || []).join(", ") }}</span
            >
          </div>
          <span class="eld-wire" aria-hidden="true"></span>
          <section class="eld-group" :aria-label="everyLabel" data-testid="eld-trunk">
            <div class="eld-group-head">
              <span class="section-label">{{ everyLabel }}</span>
              <span class="eld-note eld-quiet">before the fork</span>
            </div>
            <ol class="eld-list" :aria-label="`LoRAs ${everyWord} get, in apply order`">
              <EditLorasRow
                v-for="row in segmentRows(null)"
                :key="row.id"
                v-bind="rowProps(row)"
                v-on="rowHandlers(row)"
              />
            </ol>
            <div
              v-if="dropTarget(null)"
              class="eld-drop"
              @dragover.prevent
              @drop.prevent="dropInto(null)"
            >
              {{ dropTarget(null) }}
            </div>
            <span class="eld-wire eld-wire--plain eld-wire--short" aria-hidden="true"></span>
            <div class="eld-add">
              <AppButton size="sm" icon-left="plus" :disabled="!canAdd" @click="add(null)">
                Add for {{ everyWord }}
              </AppButton>
            </div>
          </section>
        </div>

        <!-- The fork: a stem off the trunk, a bar, and a leg into each lane. -->
        <div v-if="chain.source" class="eld-fork" aria-hidden="true">
          <span class="eld-fork-stem"></span>
          <span
            class="eld-fork-bar"
            :style="{ left: legAt(0), right: `calc(100% - ${legAt(lanes.length - 1)})` }"
          ></span>
          <span
            v-for="(lane, index) in lanes"
            :key="index"
            class="eld-fork-leg"
            :style="{ left: legAt(index) }"
          ></span>
        </div>

        <div class="eld-lanes" :style="{ '--eld-lanes': lanes.length }">
          <section
            v-for="(lane, index) in lanes"
            :key="index"
            class="eld-lane"
            :class="`eld-lane--${index % 2 ? 'b' : 'a'}`"
            :aria-label="laneName(lane)"
            data-testid="eld-lane"
          >
            <header class="eld-lane-head">
              <b>{{ laneName(lane) }}</b>{{ " " }}<small>{{ laneMeta(lane, index) }}</small>
            </header>
            <template v-if="lane.source">
              <div class="eld-node">
                <span class="eld-node-title"
                  >{{ lane.source.class_type }} #{{ lane.source.node_id }}</span
                >
                <span class="eld-node-body eld-quiet">hands out MODEL</span>
              </div>
            </template>
            <ol class="eld-list" :aria-label="`LoRAs only ${laneName(lane)} gets, in apply order`">
              <EditLorasRow
                v-for="row in segmentRows(index)"
                :key="row.id"
                v-bind="rowProps(row)"
                v-on="rowHandlers(row)"
              />
            </ol>
            <div
              v-if="dropTarget(index)"
              class="eld-drop"
              @dragover.prevent
              @drop.prevent="dropInto(index)"
            >
              {{ dropTarget(index) }}
            </div>
            <span class="eld-wire eld-wire--plain eld-wire--short" aria-hidden="true"></span>
            <div class="eld-add">
              <AppButton
                size="sm"
                icon-left="plus"
                :disabled="!canAdd"
                :aria-label="`Add a LoRA for ${laneName(lane)} only`"
                @click="add(index)"
              >
                Add
              </AppButton>
            </div>
            <!-- The shorter lane's sampler sits level with the other's, so
                 both chains end on the same line. -->
            <span class="eld-spacer"></span>
            <span class="eld-wire" aria-hidden="true"></span>
            <div class="eld-node">
              <span v-if="lane.sink?.summary" class="eld-node-title">{{
                lane.sink.summary
              }}</span>
              <span v-else class="eld-node-body eld-quiet"
                >Nothing found reading this pass</span
              >
            </div>
          </section>
        </div>
        <span v-if="editable && !shelf.length && shelfRead" class="eld-note eld-quiet">
          Your model shelf has no LoRA to add.
        </span>
      </div>
      </div>

      <!-- Why the list is shorter than the workflow: a node that is not a
           loader, or a further branch, and the loaders past it are left as
           they are. -->
      <p v-if="chain?.branch_note" class="eld-note eld-quiet" data-testid="eld-branch">
        {{ chain.branch_note }}
      </p>

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
 * **Where the model forks** (`lanes` in the read), the chain is drawn as the
 * graph it is: the loaders every pass reads once at the top, then one lane per
 * sampler side by side. Every loader appears once; a drag (or the row's menu)
 * moves one within a lane, into the trunk, or across into another pass. A
 * workflow loading a model per pass has no trunk, only lanes.
 *
 * **Read-only when the planner refuses** (`editable: false`): ComfyUI down,
 * loaders that start from different places… The refusal is the server's
 * sentence and is shown instead of letting an edit be arranged that cannot be
 * saved.
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
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import EditLorasRow from "./EditLorasRow.vue";

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

/** One lane per pass when the model forks; empty for a straight chain. */
const lanes = computed(() => chain.value?.lanes || []);
const branched = computed(() => lanes.value.length > 0);

/** "both passes" at a two-way fork, "every pass" past that. */
const everyWord = computed(() =>
  lanes.value.length === 2 ? "both passes" : "every pass",
);
const everyLabel = computed(
  () => everyWord.value.charAt(0).toUpperCase() + everyWord.value.slice(1),
);

/** The rows of one segment, in list order: `null` is the trunk, a number a lane. */
function segmentRows(lane) {
  return rows.value.filter((row) => row.lane === lane);
}

/**
 * Each standing row's 1-based place on its sampler's path; a deleted row has
 * none. A lane counts on from the trunk, since the trunk runs first.
 */
const positions = computed(() => {
  const out = {};
  const trunk = segmentRows(null).filter((row) => !row.deleted);
  trunk.forEach((row, index) => {
    out[row.id] = index + 1;
  });
  lanes.value.forEach((_lane, lane) => {
    segmentRows(lane)
      .filter((row) => !row.deleted)
      .forEach((row, index) => {
        out[row.id] = trunk.length + index + 1;
      });
  });
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

/** Every loader as read, each marked with the segment it was read in. */
const readLoaders = computed(() => [
  ...(chain.value?.loaders || []).map((loader) => ({ ...loader, lane: null })),
  ...lanes.value.flatMap((lane, index) =>
    (lane.loaders || []).map((loader) => ({ ...loader, lane: index })),
  ),
]);

const changeCount = computed(() =>
  countChanges(readLoaders.value, effectiveRows.value),
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

/**
 * No row at all, both ends read, and the chain editable: the button sits on
 * the wire between the two nodes and says so. A deleted row still stands on
 * screen with Restore, so it keeps the list between the nodes and the button
 * an Add; a read-only chain offers no insert to name.
 */
const spliceIn = computed(
  () =>
    editable.value &&
    !branched.value &&
    !rows.value.length &&
    Boolean(chain.value?.source && chain.value?.sink?.summary),
);

/**
 * The splice button's name, with the two nodes in it.
 *
 * The sink comes after the button in reading order, so "these nodes" alone
 * points a screen reader at something it has not reached. The visible words
 * lead, so a speech-input user can say what they see (WCAG 2.5.3).
 */
const spliceLabel = computed(() => {
  if (!spliceIn.value) return undefined;
  const { source, sink } = chain.value;
  const reader = (sink.consumers || []).find(
    (consumer) => String(consumer.type || "").toUpperCase() === "MODEL",
  );
  const readerName = reader
    ? `${reader.class_type} #${reader.node_id}`
    : sink.summary;
  return `Insert LoRA between these nodes: ${source.class_type} #${source.node_id} and ${readerName}`;
});

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
  if (!read || !editable.value || branched.value || (read.loaders || []).length) {
    return "";
  }
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

/**
 * A loader node's title bar, spelled like the source's: its class and id. A
 * new row has no node yet, so it names the class a save puts in.
 */
function loaderTitle(row) {
  if (row.isNew) {
    const owner = row.lane === null ? chain.value : lanes.value[row.lane];
    return owner?.added_loader_class || "LoRA loader";
  }
  return `${row.classType || "LoRA loader"} #${row.nodeId}`;
}

/** A pass as the owner reads it: its sampler's title, else `KSampler #15`. */
function laneName(lane) {
  const sampler = lane?.sampler || {};
  return sampler.title || `${sampler.class_type || "Node"} #${sampler.node_id}`;
}

/** Where a segment's rows go, in words: a lane's name, or "both passes". */
function segmentLabel(lane) {
  return lane === null ? everyWord.value : laneName(lanes.value[lane]);
}

/**
 * The lane header's small line: the sampler's class and id when a title
 * stands above it, and how many LoRAs that sampler gets in all, trunk
 * included, so nobody has to add it up.
 */
function laneMeta(lane, index) {
  const count = [...segmentRows(null), ...segmentRows(index)].filter(
    (row) => !row.deleted && (!row.isNew || row.sha256),
  ).length;
  const all = `${count} ${count === 1 ? "LoRA" : "LoRAs"} in all`;
  const sampler = lane?.sampler || {};
  return sampler.title ? `${sampler.class_type} #${sampler.node_id} · ${all}` : all;
}

/** Where the fork's leg into lane *index* lands, across the lanes' width. */
function legAt(index) {
  return `${((index + 0.5) / lanes.value.length) * 100}%`;
}

/** The segments a row's menu can send it to: every one but its own. */
function moveTargets(row) {
  if (!branched.value || row.deleted) return [];
  const targets = [];
  if (chain.value?.source && row.lane !== null) {
    targets.push({ lane: null, label: everyWord.value });
  }
  lanes.value.forEach((_lane, index) => {
    if (row.lane !== index) targets.push({ lane: index, label: segmentLabel(index) });
  });
  return targets;
}

function rowProps(row) {
  return {
    row,
    position: positions.value[row.id] ?? null,
    title: loaderTitle(row),
    editable: editable.value,
    saving: saving.value,
    canReorder: canReorder(row),
    invalid: strengthInvalid(row),
    dragging: draggingId.value === row.id,
    over: overId.value === row.id,
    compact: branched.value,
    pickerOptions: pickerOptions.value,
    moveTargets: moveTargets(row),
  };
}

function rowHandlers(row) {
  return {
    dragstart: (event) => onDragStart(row, event),
    dragend: onDragEnd,
    dragover: () => onDragOver(row),
    dragleave: () => onDragLeave(row),
    drop: () => onDrop(row),
    move: (delta) => move(row, delta),
    "move-to": (lane) => moveTo(row, lane),
    pick: (value) => pick(row, value),
    strength: (value) => {
      row.strengthText = value;
    },
    remove: () => remove(row),
    restore: () => restore(row),
  };
}

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

/** One row of the list, from one loader of the chain as read in *lane*. */
function rowOf(loader, lane = null) {
  const hasStrength = loader.strength !== null && loader.strength !== undefined;
  return {
    id: `n:${loader.node_id}`,
    nodeId: String(loader.node_id),
    classType: loader.class_type || "",
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
    lane,
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
    rows.value = [
      ...(read?.loaders || []).map((loader) => rowOf(loader, null)),
      ...(read?.lanes || []).flatMap((lane, index) =>
        (lane.loaders || []).map((loader) => rowOf(loader, index)),
      ),
    ];
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

/** Append one row with a shelf picker in it to *lane*, above its sampler. */
async function add(lane = null) {
  if (!canAdd.value) return;
  newRows += 1;
  const row = {
    id: `new:${newRows}`,
    nodeId: null,
    classType: "",
    name: "",
    fileBase: "",
    filename: "",
    sha256: "",
    onShelf: true,
    hasStrength: true,
    strengthText: fmt(1),
    deleted: false,
    isNew: true,
    lane,
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

/**
 * A drop on a row lands in that row's place, and in its segment: dropping a
 * hires loader on a trunk row puts it before the fork.
 */
function onDrop(target) {
  const from = rows.value.findIndex((row) => row.id === draggingId.value);
  const to = rows.value.findIndex((row) => row.id === target.id);
  onDragEnd();
  if (from < 0 || to < 0 || from === to) return;
  const row = rows.value[from];
  const crossed = row.lane !== target.lane;
  row.lane = target.lane;
  place(from, to, { focus: false });
  // Across the fork the pass is the news, not the place in it.
  if (crossed) {
    say(
      `${row.name || "The new LoRA"} moved to ${segmentLabel(row.lane)}, at ${positions.value[row.id]}.`,
    );
  }
}

/** The drop slot's words while a row from another segment is lifted. */
function dropTarget(lane) {
  const row = rows.value.find((entry) => entry.id === draggingId.value);
  if (!row || row.lane === lane) return "";
  return `Drop to move ${row.name || "the new LoRA"} to ${segmentLabel(lane)}`;
}

function dropInto(lane) {
  const row = rows.value.find((entry) => entry.id === draggingId.value);
  onDragEnd();
  if (row) moveTo(row, lane);
}

/** Send *row* to the end of another segment: the drag's keyboard twin. */
async function moveTo(row, lane) {
  if (!canReorder(row) || row.lane === lane) return;
  row.lane = lane;
  rows.value = [...rows.value.filter((entry) => entry.id !== row.id), row];
  say(
    `${row.name || "The new LoRA"} moved to ${segmentLabel(lane)}, at ${positions.value[row.id]}.`,
  );
  await focusRow(row.id, "handle");
}

function move(row, delta) {
  const index = rows.value.indexOf(row);
  if (index < 0 || !canReorder(row)) return;
  // Past any deleted row, and within the row's own segment: it stays in the
  // list struck through, and swapping with it moves nothing the save would
  // write while announcing a move. Crossing the fork is the menu's job.
  let to = index + delta;
  while (
    to >= 0 &&
    to < rows.value.length &&
    (rows.value[to].deleted || rows.value[to].lane !== row.lane)
  ) {
    to += delta;
  }
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
  const standing = next.filter(
    (entry) => !entry.deleted && entry.lane === row.lane,
  );
  say(
    `${row.name || "The new LoRA"} moved to ${standing.indexOf(row) + 1} of ${standing.length}.`,
  );
  if (focus) void focusRow(row.id, "handle");
}

// ── Saving: a dry run to list the changes, then the write ────────────────

function requestBody(dryRun) {
  const effective = effectiveRows.value;
  const body = {
    entries: chainEntries(effective.filter((row) => row.lane === null)),
    name: newName.value.trim() || null,
    dry_run: dryRun,
  };
  if (branched.value) {
    body.lanes = lanes.value.map((_lane, index) =>
      chainEntries(effective.filter((row) => row.lane === index)),
    );
  }
  return body;
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

<!-- Not scoped: the rows are EditLorasRow's markup, drawn by these rules, and
     every class here carries the dialog's own `eld-` prefix. -->
<style>
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
}

/* Every step of the chain is one of these: the model source, each loader and
   what reads the result. A title bar over a body, the same width and look for
   all three, so the list reads as a graph and not as a list between two
   labels. */
.eld-node {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  font-size: var(--text-xs);
}

.eld-node-title {
  padding: var(--space-2) var(--space-3);
  background: rgba(var(--v-theme-on-surface), 0.06);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  overflow-wrap: anywhere;
}

.eld-node-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  overflow-wrap: anywhere;
}

/* The wire from one node to the next: a line ending in an arrowhead on the
   node below, drawn rather than an icon because a glyph's own shaft reads as
   the line and leaves a head too small to see. The plain one is the stretch
   above the Add button, which sits on the last wire. */
.eld-wire {
  align-self: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: var(--space-6);
  --eld-wire-color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.eld-wire::before {
  content: "";
  flex: 1;
  width: var(--rail-w);
  background: var(--eld-wire-color);
}

.eld-wire::after {
  content: "";
  border-top: var(--space-3) solid var(--eld-wire-color);
  border-right: var(--space-3) solid transparent;
  border-left: var(--space-3) solid transparent;
}

.eld-wire--plain {
  height: var(--space-4);
}

.eld-wire--short {
  height: var(--space-4);
}

.eld-wire--plain::after {
  content: none;
}

.eld-list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.eld-row {
  display: flex;
  flex-direction: column;
}

.eld-row--dragging {
  opacity: var(--opacity-disabled);
}

/* Where a drop would land: the selection's bar, because the target is the
   thing chosen, not an action. */
.eld-row--over > .eld-node {
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

.eld-actions {
  display: inline-flex;
  align-items: center;
}

.eld-add {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
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

/* ── The fork drawn side by side ─────────────────────────────────────────── */

.eld-graph {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* The trunk: one narrow column in the middle, every pass's loaders once. */
.eld-trunk {
  display: flex;
  flex-direction: column;
  align-self: center;
  width: min(100%, var(--dialog-w-sm));
}

.eld-group,
.eld-lane {
  display: flex;
  flex-direction: column;
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.03);
}

.eld-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

/* A stem off the trunk, a bar across, and a leg with an arrowhead into each
   lane, drawn in the wire's colour so the fork reads as more wire. */
.eld-fork {
  position: relative;
  height: var(--space-6);
  --eld-wire-color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.eld-fork-stem,
.eld-fork-bar,
.eld-fork-leg {
  position: absolute;
  background: var(--eld-wire-color);
}

.eld-fork-stem {
  left: 50%;
  top: 0;
  width: var(--rail-w);
  height: var(--space-3);
  transform: translateX(-50%);
}

.eld-fork-bar {
  top: var(--space-3);
  height: var(--rail-w);
}

.eld-fork-leg {
  top: var(--space-3);
  bottom: var(--space-3);
  width: var(--rail-w);
  transform: translateX(-50%);
}

.eld-fork-leg::after {
  content: "";
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border-top: var(--space-3) solid var(--eld-wire-color);
  border-right: var(--space-3) solid transparent;
  border-left: var(--space-3) solid transparent;
}

/* One lane per sampler. Two fit the xl dialog; from three the dialog goes
   fullscreen, and past what fits the lanes scroll sideways at a floor of
   half the default dialog's width. */
.eld-lanes {
  display: grid;
  grid-template-columns: repeat(
    var(--eld-lanes),
    minmax(calc(var(--dialog-w-md) / 2), 1fr)
  );
  gap: var(--space-5);
  overflow-x: auto;
}

.eld-lane-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  padding: 0 var(--space-1) var(--space-3);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-sm);
}

.eld-lane-head small {
  margin-left: auto;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  white-space: nowrap;
}

/* A rail down each lane's left edge in an identity hue, so a lane stays
   identifiable when its header has scrolled away. Never the only cue: the
   header names the sampler. */
.eld-lane--a {
  box-shadow: inset var(--rail-w) 0 0 rgb(var(--v-theme-tertiary));
}

.eld-lane--b {
  box-shadow: inset var(--rail-w) 0 0 rgb(var(--v-theme-quaternary));
}

.eld-spacer {
  flex: 1;
}

/* Where a lifted row from another segment would land: the selection's olive
   bar, the same one a drop onto a row shows. */
.eld-drop {
  display: grid;
  place-items: center;
  min-height: var(--space-7);
  margin-top: var(--space-3);
  padding: 0 var(--space-3);
  border: 1px dashed var(--active-bar);
  border-radius: var(--radius-md);
  color: var(--active-bar);
  font-size: var(--text-xs);
  text-align: center;
}
</style>
