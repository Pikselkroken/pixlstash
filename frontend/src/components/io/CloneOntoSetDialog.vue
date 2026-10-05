<template>
  <AppDialog
    :open="open"
    title="Clone onto a workflow set"
    :subtitle="subtitle"
    size="xl"
    @close="close"
    @accept="clone"
  >
    <p v-if="loading" class="cos-note">Reading your workflow sets…</p>
    <p v-else-if="loadError" class="cos-note cos-bad" role="alert">
      {{ loadError }}
    </p>

    <div v-else class="cos-panes">
      <!-- ── Left: the shelf's workflow sets, by how well they fit ───────── -->
      <section class="cos-sets" aria-labelledby="cos-sets-label">
        <div class="cos-sets-head">
          <span id="cos-sets-label" class="section-label">Workflow sets</span>
          <div class="cos-tabs" role="tablist" aria-label="Which sets">
            <button
              v-for="tab in TABS"
              :key="tab.id"
              class="cos-tab"
              :class="{ 'cos-tab--on': view === tab.id }"
              type="button"
              role="tab"
              :aria-selected="String(view === tab.id)"
              @click="view = tab.id"
            >
              {{ tab.label }}
            </button>
          </div>
        </div>

        <p v-if="!shownGroups.length && view === 'fits'" class="cos-note">
          No set on your shelf has this workflow's base model or family, or
          fills its loaders one for one. All lists every set.
        </p>

        <div class="cos-list" role="radiogroup" aria-labelledby="cos-sets-label">
          <template v-for="group in shownGroups" :key="group.id">
            <p class="cos-group section-label">{{ group.label }}</p>
            <button
              v-for="set in group.sets"
              :key="set.key"
              class="cos-set"
              :class="{
                'cos-set--on': set.key === chosenKey,
                'cos-set--off': set.plan.fit === 'wont_load',
              }"
              type="button"
              role="radio"
              :aria-checked="String(set.key === chosenKey)"
              :disabled="set.plan.fit === 'wont_load' || cloning"
              :data-testid="`cos-set-${set.key}`"
              @click="choose(set.key)"
            >
              <span class="cos-cover" aria-hidden="true">
                <img v-if="set.cover" :src="coverSrc(set.cover)" alt="" loading="lazy" />
                <v-icon v-else class="cos-cover-none" size="20">mdi-image-off-outline</v-icon>
                <span v-if="set.pictures" class="cos-badge">{{
                  picturesText(set.pictures)
                }}</span>
              </span>
              <span class="cos-set-body">
                <span class="cos-set-name">{{ set.name }}</span>
                <span class="cos-set-files">{{
                  set.plan.reason || set.files
                }}</span>
                <span
                  v-if="loraCount && set.plan.fit !== 'wont_load'"
                  class="cos-set-loras"
                  :class="{ 'cos-warn': !set.plan.keeps_loras }"
                  >{{ loraVerdict(set.plan) }}</span
                >
              </span>
              <!-- Who grouped it is a tooltip: as a badge it was cut short on
                   a cover this size and hid the picture count. -->
              <Tooltip
                v-if="set.handMade"
                text="Grouped by you"
                activator="parent"
              />
            </button>
          </template>

          <!-- Per-file picking, which this dialog replaced in the menu. -->
          <button
            v-if="view === 'all'"
            class="cos-set cos-set--pick"
            type="button"
            data-testid="cos-pick-files"
            :disabled="cloning"
            @click="emit('pick-files')"
          >
            <span class="cos-cover cos-cover--pick" aria-hidden="true">
              <v-icon size="20">mdi-file-swap-outline</v-icon>
            </span>
            <span class="cos-set-body">
              <span class="cos-set-name">Pick files myself</span>
              <span class="cos-set-files">One model at a time, from the shelf</span>
            </span>
          </button>
        </div>
      </section>

      <!-- ── Right: the clone as a change list ─────────────────────────── -->
      <section class="cos-diff" aria-labelledby="cos-diff-label">
        <p v-if="!chosen" class="cos-note">
          Pick a set to see what the clone changes.
        </p>
        <template v-else>
          <span id="cos-diff-label" class="section-label">What the clone changes</span>
          <ul class="cos-rows" data-testid="cos-loaders">
            <li
              v-for="row in chosen.plan.loaders"
              :key="row.node_id"
              class="cos-row"
              :class="{ 'cos-row--same': !changed(row) }"
            >
              <span class="cos-row-kind">{{ kindLabel(row.kind) }}</span>
              <span v-if="!changed(row)" class="cos-row-body">
                <span class="cos-class">{{ row.was_class }}</span>
                · {{ fileNames(row.was) }}
                <span class="cos-same">Unchanged</span>
              </span>
              <span v-else class="cos-row-body">
                <span class="cos-was">
                  <span class="cos-class">{{ row.was_class }}</span>
                  {{ fileNames(row.was) }}
                  <template v-if="row.was_type !== row.now_type">
                    · {{ row.was_type }}</template
                  >
                </span>
                <span class="cos-now">
                  <v-icon size="14" aria-hidden="true">mdi-arrow-right</v-icon>
                  <span class="cos-class">{{ row.now_class }}</span>
                  {{ fileNames(row.now) }}
                  <template v-if="row.was_type !== row.now_type">
                    · {{ row.now_type }}</template
                  >
                  <span v-if="row.pack" class="cos-pack">{{ row.pack }}</span>
                </span>
              </span>
            </li>
          </ul>
          <p v-if="baseKept" class="cos-note cos-warn" data-testid="cos-base-kept">
            <v-icon size="14" aria-hidden="true">mdi-alert-outline</v-icon>
            <span
              >This set has no checkpoint for {{ baseKept }}, which stays as
              the workflow has it.</span
            >
          </p>
          <p
            v-for="pack in packs"
            :key="pack.name"
            class="cos-note"
            :class="{ 'cos-warn': pack.installed !== true }"
            data-testid="cos-pack-note"
          >
            <v-icon size="14" aria-hidden="true">{{
              pack.installed === true ? "mdi-check" : "mdi-alert-outline"
            }}</v-icon>
            <span>{{ packSentence(pack) }}</span>
          </p>

          <div class="cos-loras-head">
            <span class="section-label">LoRAs</span>
            <AppButton
              size="sm"
              variant="ghost"
              data-testid="cos-edit-loras"
              :disabled="!chainEditable || cloning"
              @click="editOpen = true"
            >
              Edit LoRAs…
            </AppButton>
          </div>
          <p v-if="dropNotice" class="cos-note" role="status">{{ dropNotice }}</p>
          <p v-if="!chain" class="cos-note cos-bad" role="alert">
            Could not read this workflow's LoRAs, so PixlStash cannot tell
            which to keep.
          </p>
          <p v-else-if="!loraCount && !addedRows.length" class="cos-note">
            This workflow has no LoRAs.
          </p>
          <template v-else-if="chosen.plan.keeps_loras">
            <p class="cos-note">
              Same base model as {{ checkpointName }}, so its LoRAs are kept.
            </p>
          </template>
          <template v-else-if="loraCount">
            <p class="cos-removed" data-testid="cos-removed">
              <span class="cos-removed-label">Removed {{ checkpointName }} LoRAs</span>
              <s v-for="loader in readLoaders" :key="loader.node_id" class="cos-chip">{{
                loader.name
              }}</s>
            </p>
            <p class="cos-note">{{ removedReason }}</p>
            <p v-if="!chainEditable" class="cos-note cos-bad" role="alert">
              PixlStash cannot take them out just now.
              {{ chain?.refusal || "" }}
            </p>
          </template>
          <ul v-if="shownRows.length" class="cos-chain" data-testid="cos-chain">
            <li v-for="row in shownRows" :key="row.id" class="cos-chain-row">
              <span class="cos-chain-name">{{ row.name }}</span>
              <span v-if="row.isNew" class="cos-new">new</span>
              <span class="cos-strength">{{ row.strengthText }}</span>
            </li>
          </ul>

          <AppInput
            v-model="name"
            class="cos-name"
            label="Name"
            placeholder="Name the clone"
            :disabled="cloning"
            @update:model-value="nameTouched = true"
            @enter="clone"
          />
          <p v-if="sameFiles" class="cos-note">
            This workflow already loads this set's files, so a clone would be a
            copy.
          </p>
        </template>
        <p v-if="cloneError" class="cos-note cos-bad" role="alert">
          {{ cloneError }}
        </p>
      </section>
    </div>

    <EditLorasDialog
      :open="editOpen"
      :workflow-id="workflowId"
      :card-name="name"
      :pending="pendingEdit"
      @close="editOpen = false"
      @done="takeEdit"
    />

    <template #footer>
      <span v-if="chosen" class="cos-count" data-testid="cos-summary">{{
        summary
      }}</span>
      <AppButton :disabled="cloning" @click="close">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="content-duplicate"
        :loading="cloning"
        :disabled="!canClone"
        @click="clone"
      >
        Clone
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * Clone onto a workflow set: this workflow, but with that set's models.
 *
 * Two panes. On the left the shelf's workflow sets (the owner's own and the
 * evidence ones), grouped by how well they fit: same base model, same family,
 * the rest, and the ones that will not load, disabled with the reason. On the
 * right the clone as a node diff, read from `POST …/set-clone-plans`: each
 * loader's class and files before and after, the node pack a new class comes
 * from and whether ComfyUI has it. **A missing pack warns and never refuses.**
 *
 * **LoRAs go only when the base model changes** (`keeps_loras`, decided by the
 * server from the shelf's base models; an unknown one counts as different).
 * Then the section says "Removed <checkpoint> LoRAs" and Clone sends an empty
 * chain. *Edit LoRAs…* opens the Workflow tab's own `EditLorasDialog` in its
 * pending mode, on the clone's chain; *Done* hands the chain back here, and
 * the clone is still one write.
 *
 * Replaces *Clone with new models* in the menu; *Pick files myself* (the All
 * tab's last card) opens that dialog instead.
 */
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { fetchWorkflowSets } from "../../api/modelShelf";
import { pictureThumbnailUrl } from "../../api/pictures";
import { getLoraChain, planSetClones } from "../../api/workflows";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import { loraStem } from "../../utils/loraChain";
import { deriveModelName } from "../../utils/modelShelf";
import {
  cloneOntoSetName,
  handMadeName,
  setCheckpoint,
  setGroups,
} from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import Tooltip from "../widgets/Tooltip.vue";
import EditLorasDialog from "./EditLorasDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The card being cloned. It is never changed. */
  workflowId: { type: String, default: "" },
  /** What the owner calls it: the subtitle, and the clone's name's stem. */
  cardName: { type: String, default: "" },
  /** The card's `type_label`, which tells a generated name from the owner's. */
  cardTypeLabel: { type: String, default: "" },
});

const emit = defineEmits(["close", "cloned", "pick-files"]);

const store = useWorkflowsStore();

const TABS = [
  { id: "fits", label: "Fits this graph" },
  { id: "all", label: "All" },
];

const FIT_GROUPS = [
  { id: "same_base_model", label: "Same base model" },
  { id: "same_family", label: "Same family" },
  { id: "other", label: "Other base models" },
  { id: "wont_load", label: "Won't load in this graph" },
];

const KIND_LABELS = {
  checkpoint: "Checkpoint",
  unet: "Model",
  vae: "VAE",
  clip: "Text enc.",
};

const loading = ref(false);
const loadError = ref("");
/** Every set, each `{key, name, files, cover, pictures, handMade, loraIds, checkpointIds, plan}`. */
const sets = ref([]);
/** `set-clone-plans`' `base_filename` and `base_model`. */
const base = ref({ filename: null, model: null });
/** The original's `GET …/lora-chain`. */
const chain = ref(null);
/** Every combination, for the picker's "Ran with" group. */
const combinations = ref([]);
const view = ref("fits");
const chosenKey = ref("");
const name = ref("");
const nameTouched = ref(false);
const cloning = ref(false);
const cloneError = ref("");
const editOpen = ref(false);
/** Edit LoRAs' Done for the chosen set: `{key, rows, body}`, or null. */
const edited = ref(null);
const dropNotice = ref("");
let token = 0;

const subtitle = computed(() => {
  const checkpoint = base.value.filename ? checkpointName.value : "";
  return [props.cardName, checkpoint].filter(Boolean).join(" · ");
});

const checkpointName = computed(
  () => deriveModelName(base.value.filename || "") || "this checkpoint",
);

const chosen = computed(
  () => sets.value.find((set) => set.key === chosenKey.value) ?? null,
);

/**
 * Fits this graph: the same base model or family, or a set whose files fill
 * the graph's checkpoint, VAE and text-encoder loaders one for one
 * (`maps_cleanly`), whatever its base model.
 */
function fits(set) {
  return (
    ["same_base_model", "same_family"].includes(set.plan.fit) ||
    Boolean(set.plan.maps_cleanly)
  );
}

const shownGroups = computed(() =>
  FIT_GROUPS.map((group) => ({
    ...group,
    sets: sets.value.filter(
      (set) => set.plan.fit === group.id && (view.value === "all" || fits(set)),
    ),
  })).filter((group) => group.sets.length),
);

/** Every loader of the original's chain, trunk and lanes. */
const readLoaders = computed(() => [
  ...(chain.value?.loaders ?? []),
  ...(chain.value?.lanes ?? []).flatMap((lane) => lane.loaders ?? []),
]);
const loraCount = computed(() => readLoaders.value.length);
const chainEditable = computed(() => Boolean(chain.value?.editable));

/** The rows a Done handed back for this set, else the chain the rule leaves. */
const shownRows = computed(() => {
  if (edited.value?.key === chosenKey.value) {
    return edited.value.rows.filter((row) => !row.deleted && (!row.isNew || row.sha256));
  }
  if (!chosen.value?.plan.keeps_loras) return [];
  return readLoaders.value.map((loader) => ({
    id: `n:${loader.node_id}`,
    name: loader.name || loraStem(loader.filename),
    strengthText: strengthText(loader.strength),
    isNew: false,
  }));
});
const addedRows = computed(() => shownRows.value.filter((row) => row.isNew));

const removedReason = computed(() => {
  const set = chosen.value;
  if (!base.value.model) {
    return `PixlStash does not know which base model ${checkpointName.value} is, so its LoRAs do not come along.`;
  }
  if (!set?.plan.base_model) {
    return `PixlStash does not know which base model ${set?.name} is, so LoRAs trained for ${checkpointName.value} do not come along.`;
  }
  return `${set.name} is another base model, so LoRAs trained for ${checkpointName.value} do not come along.`;
});

/** Each node pack a rewritten loader comes from, once. */
const packs = computed(() => {
  const found = new Map();
  for (const row of chosen.value?.plan.loaders ?? []) {
    if (row.pack && !found.has(row.pack)) {
      found.set(row.pack, { name: row.pack, installed: row.installed });
    }
  }
  return [...found.values()];
});

/**
 * The base loaders the server paired with none of the set's checkpoints (a
 * refiner, Wan 2.2's other expert, when the set holds fewer than the graph
 * loads): said rather than letting a mixed graph pass as a clone onto the set.
 * Read off the pairing, not off unchanged rows: a set checkpoint that is the
 * very file the graph loads leaves its row unchanged too.
 */
const baseKept = computed(() =>
  fileNames(chosen.value?.plan.unpaired_bases ?? []),
);

const sameFiles = computed(
  () => Boolean(chosen.value) && !Object.keys(chosen.value.plan.swaps).length,
);

/** What Clone sends as the chain: the edit, an empty chain, or nothing. */
const lorasBody = computed(() => {
  if (edited.value?.key === chosenKey.value) return edited.value.body;
  if (chosen.value?.plan.keeps_loras || !loraCount.value) return null;
  const lanes = chain.value?.lanes ?? [];
  return { entries: [], lanes: lanes.length ? lanes.map(() => []) : null };
});

const summary = computed(() => {
  const plan = chosen.value?.plan;
  if (!plan) return "";
  const rewritten = plan.loaders.filter(changed).length;
  const parts = [`${rewritten} ${rewritten === 1 ? "loader" : "loaders"} rewritten`];
  // Counted off the rows Clone sends (an Edit LoRAs… Done included), so the
  // line matches the write: an edit can delete a kept LoRA or add one.
  const lora = (n) => (n === 1 ? "LoRA" : "LoRAs");
  const added = addedRows.value.length;
  const kept = shownRows.value.length - added;
  const removed = loraCount.value - kept;
  if (!added && !removed && kept) parts.push("LoRAs kept");
  else {
    const bits = [];
    if (kept) bits.push(`${kept} ${lora(kept)} kept`);
    // Named by the checkpoint only when the base model change removed them.
    if (removed) {
      bits.push(
        plan.keeps_loras
          ? `${removed} ${lora(removed)} removed`
          : `${removed} ${checkpointName.value} ${lora(removed)} removed`,
      );
    }
    if (added) bits.push(`${added} ${lora(added)} added`);
    if (bits.length) parts.push(bits.join(", "));
  }
  if (packs.value.length) {
    parts.push(`${packs.value.length} node ${packs.value.length === 1 ? "pack" : "packs"}`);
  }
  return parts.join(" · ");
});

const canClone = computed(
  () =>
    Boolean(chosen.value) &&
    chosen.value.plan.fit !== "wont_load" &&
    !sameFiles.value &&
    Boolean(name.value.trim()) &&
    !cloning.value &&
    // Removing LoRAs rewires the graph, which needs the chain's planner; an
    // unread chain may hold LoRAs of the old base model.
    (chosen.value.plan.keeps_loras || Boolean(chain.value)) &&
    (lorasBody.value === null || chainEditable.value),
);

/** What Edit LoRAs… opens on: the clone's chain, not the card's. */
const pendingEdit = computed(() => {
  const set = chosen.value;
  if (!set) return null;
  const sourceClass = set.plan.loaders.find(
    (row) => row.kind === "checkpoint" || row.kind === "unet",
  )?.now_class;
  return {
    name: name.value.trim() || props.cardName,
    clear: !set.plan.keeps_loras,
    sourceClass: sourceClass || null,
    rows: edited.value?.key === set.key ? edited.value.rows : null,
    baseModel: set.plan.keeps_loras ? base.value.model : set.plan.base_model,
    setLoraIds: set.loraIds,
    ranWith: ranWith(set.checkpointIds[0]),
    checkpointName: set.checkpointName,
  };
});

function changed(row) {
  return (
    row.was_class !== row.now_class ||
    (row.was_type ?? null) !== (row.now_type ?? null) ||
    row.was.length !== row.now.length ||
    row.was.some((file, index) => file !== row.now[index])
  );
}

function kindLabel(kind) {
  return KIND_LABELS[kind] || kind;
}

function fileNames(files) {
  return files.map((file) => deriveModelName(file) || file).join(", ");
}

function strengthText(value) {
  const number = Number(value);
  return value === null || value === undefined || !Number.isFinite(number)
    ? ""
    : number.toFixed(2);
}

function picturesText(count) {
  return `${count} ${count === 1 ? "picture" : "pictures"}`;
}

function coverSrc(cover) {
  return pictureThumbnailUrl(cover.picture_id, { version: cover.version });
}

function loraVerdict(plan) {
  const count = loraCount.value;
  const noun = count === 1 ? "LoRA" : "LoRAs";
  return plan.keeps_loras ? `Keeps its ${count} ${noun}` : `Clears ${count} ${noun}`;
}

function packSentence(pack) {
  if (pack.installed === true) return `${pack.name} is installed in your ComfyUI.`;
  if (pack.installed === false) {
    return `${pack.name} is not installed in your ComfyUI. The clone is written anyway; install it before you run it.`;
  }
  return `PixlStash could not ask ComfyUI whether ${pack.name} is installed.`;
}

/** Adapter ids that ran beside *checkpointId*, with how many recipes. */
function ranWith(checkpointId) {
  const out = {};
  if (checkpointId == null) return out;
  for (const combination of combinations.value) {
    const models = combination.models ?? [];
    if (models[0]?.id !== checkpointId) continue;
    for (const model of models) {
      if (model.kind !== "adapter") continue;
      out[model.id] = (out[model.id] ?? 0) + (combination.recipes ?? 0);
    }
  }
  return out;
}

/** A file as the shelf and the plan both spell it: no folder, any case. */
function fileKey(file) {
  return String(file).split(/[/\\]/).pop().toLowerCase();
}

/** Each member's shelf name, by `fileKey` of its file. */
function namesByFile(members) {
  return Object.fromEntries(
    members.filter((m) => m.filename).map((m) => [fileKey(m.filename), m.name]),
  );
}

/**
 * The base models the clone loads, by their shelf names: the plan's base
 * loaders' new files in loader order, each name once (a two-model graph names
 * both, as the server's generated names do). Off the plan, not the set's
 * first checkpoint, which may be off the shelf or paired with no loader.
 */
function loadedModelName(set) {
  if (!set) return "";
  const names = set.plan.loaders
    .filter((row) => row.kind === "checkpoint" || row.kind === "unet")
    .flatMap((row) => row.now)
    .map((file) => set.modelNames[fileKey(file)] || deriveModelName(file) || file);
  return [...new Set(names)].join(" + ");
}

/** The names a set's files line shows: its checkpoint, then the rest. */
function filesLine(members) {
  return members
    .filter((member) => member.kind !== "adapter" && member.slot !== "lora")
    .slice(0, 3)
    .map((member) => member.name)
    .join(" · ");
}

/** The shelf's sets as this dialog draws them, before their plans. */
function candidates(payload) {
  const handMade = (payload.hand_made ?? []).map((set) => {
    const members = (set.members ?? []).filter((m) => m.on_shelf && m.id != null);
    const checkpoint = setCheckpoint(set);
    const ordered = [
      ...members.filter((m) => m.slot === "checkpoint"),
      ...members.filter((m) => m.slot !== "checkpoint"),
    ];
    return {
      key: `hand:${set.id}`,
      handMade: true,
      name: handMadeName(set),
      files: filesLine(ordered),
      cover: (set.covers ?? [])[0] ?? null,
      pictures: Number(set.picture_count) || 0,
      // The plan uses a set's VAEs and encoders; its LoRAs only feed the picker.
      modelIds: ordered
        .filter((m) => m.slot !== "checkpoint" && m.slot !== "lora")
        .map((m) => m.id),
      loraIds: members.filter((m) => m.slot === "lora").map((m) => m.id),
      // Every on-shelf one (`members` is on-shelf only, so an off-shelf
      // checkpoint never trips the server's `gone` check): a two-model graph
      // takes one per base loader (#1690).
      checkpointIds: members
        .filter((m) => m.slot === "checkpoint")
        .map((m) => m.id),
      checkpointName: checkpoint?.name || "",
      modelNames: namesByFile(members),
    };
  });
  // A set whose base model is off the shelf has no checkpoint to clone onto.
  const groups = setGroups(payload.combinations ?? []).filter(
    (group) => !group.head?.missing,
  );
  const evidence = groups.map((group) => {
    const models = group.models ?? [];
    return {
      key: group.key,
      handMade: false,
      name: group.head?.name || "Unnamed set",
      files: filesLine(models),
      cover: (group.covers ?? [])[0] ?? null,
      pictures: group.pictures || 0,
      modelIds: models
        .filter((m) => m.id !== group.head.id && m.kind !== "adapter")
        .map((m) => m.id),
      loraIds: models.filter((m) => m.kind === "adapter").map((m) => m.id),
      // The head, then any other checkpoint the pictures ran beside it.
      checkpointIds: [
        group.head.id,
        ...models
          .filter((m) => m.id !== group.head.id && m.kind === "checkpoint")
          .map((m) => m.id),
      ],
      checkpointName: group.head?.name || "",
      modelNames: namesByFile(models),
    };
  });
  return [...handMade, ...evidence];
}

async function load() {
  const mine = (token += 1);
  loading.value = true;
  loadError.value = "";
  cloneError.value = "";
  sets.value = [];
  chain.value = null;
  chosenKey.value = "";
  edited.value = null;
  dropNotice.value = "";
  nameTouched.value = false;
  name.value = "";
  view.value = "fits";
  try {
    const [payload, read] = await Promise.all([
      fetchWorkflowSets(),
      getLoraChain(props.workflowId).catch((err) => {
        // Only the LoRA half needs it; the dialog says so there, and will not
        // clone onto another base model without knowing what to remove.
        console.warn(
          `[workflows] could not read the LoRA chain of ${props.workflowId}`,
          err,
        );
        return null;
      }),
    ]);
    if (mine !== token) return;
    const found = candidates(payload);
    const answer = await planSetClones(
      props.workflowId,
      found.map((set) => ({
        key: set.key,
        checkpoint_ids: set.checkpointIds,
        model_ids: set.modelIds,
      })),
    );
    if (mine !== token) return;
    const plans = new Map((answer?.plans ?? []).map((plan) => [plan.key, plan]));
    combinations.value = payload.combinations ?? [];
    chain.value = read;
    base.value = { filename: answer?.base_filename, model: answer?.base_model };
    sets.value = found
      .filter((set) => plans.has(set.key))
      .map((set) => ({ ...set, plan: plans.get(set.key) }));
    if (!shownGroups.value.length) view.value = "all";
  } catch (err) {
    if (mine !== token) return;
    console.warn(`[workflows] could not plan clones of ${props.workflowId}`, err);
    loadError.value = errorMessage(err, "Could not read your workflow sets.");
  } finally {
    if (mine === token) loading.value = false;
  }
}

function choose(key) {
  if (key === chosenKey.value) return;
  // The rule is re-applied from the original, never from the last pick: LoRAs
  // added for another set do not follow the owner here, and nor does any
  // other edit (a deleted or re-weighted LoRA), so the notice covers them all.
  const added = edited.value?.rows?.filter((row) => row.isNew && row.sha256) ?? [];
  dropNotice.value = edited.value?.changes
    ? `Dropped the LoRA edits made for ${chosen.value?.name}` +
      (added.length ? `, including ${added.map((row) => row.name).join(", ")}.` : ".")
    : "";
  edited.value = null;
  chosenKey.value = key;
  if (!nameTouched.value) {
    name.value = cloneOntoSetName(
      props.cardName,
      props.cardTypeLabel,
      chosen.value?.name,
      loadedModelName(chosen.value),
    );
  }
}

function takeEdit({ rows, body, changes }) {
  edited.value = { key: chosenKey.value, rows, body, changes };
  dropNotice.value = "";
}

async function clone() {
  if (!canClone.value) return;
  cloning.value = true;
  cloneError.value = "";
  try {
    // Never rejects: the store catches every failure, logs it and answers
    // null with the reason in `store.error`, which the branch below shows.
    const answer = await store.cloneCardWithModels(props.workflowId, {
      name: name.value.trim(),
      swaps: chosen.value.plan.swaps,
      loras: lorasBody.value,
    });
    if (!answer) {
      cloneError.value = store.error || "Could not clone that workflow.";
      return;
    }
    emit("cloned", answer);
    emit("close");
  } finally {
    cloning.value = false;
  }
}

function close() {
  if (cloning.value) return;
  emit("close");
}

watch(
  () => [props.open, props.workflowId],
  ([isOpen]) => {
    if (isOpen && props.workflowId) void load();
    else token += 1;
  },
  { immediate: true },
);
</script>

<style scoped>
.cos-panes {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  gap: var(--space-6);
  min-height: 0;
}

.cos-sets,
.cos-diff {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.cos-sets-head,
.cos-loras-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.cos-tabs {
  display: flex;
  gap: var(--space-1);
}

.cos-tab {
  height: var(--control-h-sm);
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cos-tab:hover {
  background: var(--hover-wash);
}

.cos-tab--on {
  background: var(--active-wash);
  color: rgb(var(--v-theme-selected-ink));
  font-weight: var(--weight-semibold);
}

.cos-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-height: 60vh;
  overflow-y: auto;
}

.cos-group {
  margin: var(--space-3) 0 0;
}

.cos-set {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: var(--space-4);
  align-items: center;
  padding: var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  text-align: left;
  color: rgb(var(--v-theme-on-surface));
}

.cos-set:hover:not(:disabled) {
  background: var(--hover-wash);
}

.cos-set--on {
  border-color: rgb(var(--v-theme-accent));
  background: var(--active-wash);
}

.cos-set--off {
  cursor: not-allowed;
  opacity: var(--opacity-disabled);
}

.cos-set--pick {
  border-style: dashed;
}

.cos-cover {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  aspect-ratio: 6 / 5;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.cos-cover-none {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cos-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* The shelf card's scrim badge (ModelSetCard's `.msc__badge`), smaller. */
.cos-badge {
  position: absolute;
  left: var(--space-1);
  bottom: var(--space-1);
  display: inline-flex;
  align-items: center;
  min-height: var(--badge-size);
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  white-space: nowrap;
  pointer-events: none;
}

.cos-set-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.cos-set-name {
  overflow: hidden;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cos-set-files,
.cos-set-loras {
  overflow: hidden;
  font-size: var(--text-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cos-rows,
.cos-chain {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cos-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
}

.cos-row--same {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cos-row-kind {
  font-weight: var(--weight-semibold);
}

.cos-row-body,
.cos-was,
.cos-now {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.cos-row-body {
  flex-direction: column;
  align-items: flex-start;
}

.cos-row--same .cos-row-body {
  flex-direction: row;
}

.cos-was {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-decoration: line-through;
}

.cos-class {
  font-family: var(--font-mono);
}

.cos-same {
  margin-left: auto;
}

.cos-pack,
.cos-new,
.cos-chip {
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
}

.cos-removed {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-xs);
}

.cos-removed-label {
  font-weight: var(--weight-semibold);
}

.cos-chip {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cos-chain-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
}

.cos-strength {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.cos-name {
  margin-top: var(--space-3);
}

.cos-note {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cos-warn {
  color: rgb(var(--v-theme-surface-warning));
}

.cos-bad {
  color: rgb(var(--v-theme-surface-error));
}

.cos-count {
  margin-right: auto;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

@media (max-width: 720px) {
  .cos-panes {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
