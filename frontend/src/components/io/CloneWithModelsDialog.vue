<template>
  <AppDialog
    :open="open"
    title="Clone with new models"
    size="md"
    @close="emit('close')"
    @accept="clone"
  >
    <AppInput
      v-model="name"
      label="Name"
      placeholder="Name the clone"
      autofocus
      @update:model-value="nameTouched = true"
      @enter="clone"
    />

    <p v-if="loading" class="cwm-note cwm-quiet">
      Reading this workflow's models…
    </p>
    <p v-else-if="loadError" class="cwm-note cwm-note--bad" role="alert">
      {{ loadError }}
    </p>

    <template v-else-if="options">
      <p v-if="!baseSlot" class="cwm-note cwm-quiet">
        PixlStash cannot find the checkpoint this workflow loads, so there is
        nothing to change it from.
      </p>
      <div v-else class="cwm-row">
        <AppSelect
          v-model="checkpointId"
          label="Checkpoint"
          :options="checkpointOptions"
          :disabled="cloning"
        />
      </div>

      <div v-for="row in rows" :key="row.original" class="cwm-row">
        <AppSelect
          v-model="row.value"
          :label="row.label"
          :options="optionsFor(row)"
          :disabled="cloning || proposing"
          @update:model-value="row.touched = true"
        />
        <!-- Every row a proposal filled says which step answered it, so a
             guess from the architecture family reads weaker than a file that
             ran with this very checkpoint. -->
        <p
          v-if="provenance(row)"
          class="cwm-note"
          :class="{ 'cwm-warn': !row.via }"
        >
          {{ provenance(row) }}
        </p>
      </div>

      <p v-if="otherBaseSlots.length" class="cwm-note cwm-warn">
        Only the first base model changes.
        {{ otherBaseSlots.join(", ") }}
        {{ otherBaseSlots.length === 1 ? "stays" : "stay" }} as the workflow has
        {{ otherBaseSlots.length === 1 ? "it" : "them" }}.
      </p>
      <p v-if="loraSlots.length" class="cwm-note cwm-quiet">
        {{ loraSentence }}
      </p>
      <p v-for="flag in flags" :key="flag.filename" class="cwm-note cwm-warn">
        <v-icon size="14">mdi-alert-circle-outline</v-icon>
        <span
          >“{{ flag.filename }}” was trained on {{ flag.base_model }}; this
          checkpoint is {{ chosenCheckpoint?.base_model }}.</span
        >
      </p>
    </template>

    <p v-if="cloneError" class="cwm-note cwm-note--bad" role="alert">
      {{ cloneError }}
    </p>

    <template #footer>
      <AppButton :disabled="cloning" @click="emit('close')">Cancel</AppButton>
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
 * Clone with new models: the same workflow, loading a different checkpoint.
 *
 * Choose the checkpoint and the VAE and text-encoder rows fill themselves in
 * from what recipes on this machine have run beside it (`GET
 * /workflows/{key}/model-swap?checkpoint_id=`), widened from that checkpoint to
 * its base model to its family, each row saying which step answered. **A row
 * nothing answers keeps the workflow's own file and says so**: there is no
 * compatibility table behind this, only evidence, and a checkpoint nothing has
 * run with proposes nothing.
 *
 * LoRAs are never dropped. One trained on another family than the new
 * checkpoint is named, and stays; removing it would change what the workflow
 * makes without anybody having asked.
 *
 * Swapped by filename (`swaps` is graph filename -> new filename), so every
 * loader naming the file changes together, wherever it sits in the graph.
 */
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { readModelSwap } from "../../api/workflows";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  workflowKey: { type: String, default: "" },
  /** The card's own name, which the clone's is built from. */
  cardName: { type: String, default: "" },
});

const emit = defineEmits(["close", "cloned"]);

const store = useWorkflowsStore();

const BASE_KINDS = ["checkpoint", "unet"];
const ROW_KINDS = { vae: "VAE", clip: "Text encoder" };
const PROPOSAL_KIND = { vae: "vae", clip: "text_encoder" };

const options = ref(null);
const loading = ref(false);
const loadError = ref("");
const checkpointId = ref("");
const rows = ref([]);
const flags = ref([]);
const name = ref("");
const nameTouched = ref(false);
const cloning = ref(false);
/** A proposal read is in flight: Clone waits, or it would post half a swap. */
const proposing = ref(false);
/**
 * The proposal read for the chosen checkpoint failed. Clone waits for another
 * choice rather than writing that checkpoint with companions nobody vouched for.
 */
const proposalFailed = ref(false);
const cloneError = ref("");
// Which read is current: a slow answer for a checkpoint the owner has since
// changed must not fill the rows in for the wrong one. The open read keeps a
// sequence of its own, so a checkpoint reset while it is in flight cannot
// orphan it and leave the dialog stuck on "Reading…".
let readSeq = 0;
let openSeq = 0;

/** A filename as two spellings of one file compare: basename, case folded. */
function same(a, b) {
  const fold = (value) =>
    String(value || "")
      .toLowerCase()
      .split(/[\\/]/)
      .pop();
  return fold(a) === fold(b);
}

function modelLabel(model) {
  return model.display_name || model.filename;
}

const baseSlot = computed(
  () =>
    options.value?.slots.find((slot) => BASE_KINDS.includes(slot.kind)) || null,
);

const loraSlots = computed(
  () => options.value?.slots.filter((slot) => slot.kind === "lora") || [],
);

// Compared as strings: a native `<select>` hands its value back as text.
const chosenCheckpoint = computed(
  () =>
    options.value?.checkpoints.find(
      (model) => String(model.id) === String(checkpointId.value),
    ) || null,
);

/**
 * The checkpoints, with the workflow's own file among them when the shelf
 * does not hold it: without it the select would render empty and read as a
 * slot nobody filled (`RunDialog`'s `optionsFor`, for the same reason).
 */
const checkpointOptions = computed(() => {
  const list = (options.value?.checkpoints || []).map((model) => ({
    value: model.id,
    label: modelLabel(model),
  }));
  // No option for the graph's own file (not on the shelf, or on it as
  // something other than a checkpoint) would render the select empty.
  if (baseSlot.value && baseSlot.value.model?.file_kind !== "checkpoint") {
    list.unshift({
      value: "",
      label: `${baseSlot.value.filename} (not on your shelf)`,
    });
  }
  return list;
});

function optionsFor(row) {
  const shelf =
    options.value?.[row.kind === "vae" ? "vaes" : "text_encoders"] || [];
  const list = shelf.map((model) => ({
    value: model.filename,
    label: modelLabel(model),
  }));
  if (!list.some((option) => option.value === row.own)) {
    list.unshift({
      value: row.original,
      label: `${row.original} (the workflow's)`,
    });
  }
  return list;
}

/** The workflow's own checkpoint is chosen: nothing is being proposed. */
function isOriginal(model) {
  return model.id === baseSlot.value?.model?.id;
}

function provenance(row) {
  const chosen = chosenCheckpoint.value;
  if (!chosen || isOriginal(chosen) || row.touched) return "";
  if (row.via === "checkpoint") return "Used with this checkpoint";
  if (row.via === "base_model") {
    return `Used with other ${chosenCheckpoint.value.base_model} checkpoints`;
  }
  if (row.via === "family") return "Used with other checkpoints of its family";
  if (row.exhausted) {
    return "The suggestions went to the rows above: keeps the workflow's file";
  }
  return "Nothing on your shelf has run with this checkpoint or its family: keeps the workflow's file";
}

/** Base loaders after the first (a refiner, Wan 2.2's second expert). */
const otherBaseSlots = computed(() =>
  (options.value?.slots || [])
    .filter((slot) => BASE_KINDS.includes(slot.kind))
    .slice(1)
    .map((slot) => slot.filename),
);

const loraSentence = computed(() => {
  const n = loraSlots.value.length;
  return n === 1 ? "1 LoRA stays as it is." : `${n} LoRAs stay as they are.`;
});

/** Graph filename -> the one to load instead, for every row that changed. */
const swaps = computed(() => {
  const out = {};
  const base = baseSlot.value;
  const chosen = chosenCheckpoint.value;
  // By shelf row, not by name: the graph may load the file under another
  // copy's name, and an unchanged checkpoint must never read as a swap.
  if (base && chosen && !isOriginal(chosen)) {
    out[base.filename] = chosen.filename;
  }
  for (const row of rows.value) {
    if (row.value && !same(row.value, row.original))
      out[row.original] = row.value;
  }
  return out;
});

/**
 * Only when something actually changes. A clone that swaps nothing keeps the
 * structural hash, lands on the ORIGINAL card and reads as a gesture that did
 * nothing, so the button stays off until it would do something.
 */
const canClone = computed(
  () =>
    Boolean(name.value.trim()) &&
    Object.keys(swaps.value).length > 0 &&
    !cloning.value &&
    !loading.value &&
    !proposing.value &&
    !proposalFailed.value,
);

watch(
  () => [props.open, props.workflowKey],
  async ([isOpen]) => {
    options.value = null;
    rows.value = [];
    flags.value = [];
    checkpointId.value = "";
    cloneError.value = "";
    loadError.value = "";
    nameTouched.value = false;
    name.value = props.cardName ? `${props.cardName} (new models)` : "";
    if (!isOpen || !props.workflowKey) return;
    const seq = ++openSeq;
    loading.value = true;
    try {
      const body = await readModelSwap(props.workflowKey);
      if (seq !== openSeq) return;
      options.value = body;
      checkpointId.value =
        baseSlot.value?.model?.file_kind === "checkpoint"
          ? baseSlot.value.model.id
          : "";
      rows.value = body.slots
        .filter((slot) => slot.kind in ROW_KINDS)
        .map((slot) => {
          const shelf =
            body[slot.kind === "vae" ? "vaes" : "text_encoders"] || [];
          // The select shows the shelf's spelling of the graph's own file when
          // it has one: a native select matches values exactly, and the graph
          // may name it under a folder the shelf row does not carry.
          const own =
            shelf.find((model) => same(model.filename, slot.filename))
              ?.filename ?? slot.filename;
          return {
            kind: slot.kind,
            label: ROW_KINDS[slot.kind],
            original: slot.filename,
            own,
            family: slot.model?.family ?? null,
            value: own,
            via: null,
            touched: false,
            exhausted: false,
          };
        });
    } catch (err) {
      if (seq !== openSeq) return;
      loadError.value = errorMessage(
        err,
        "Could not read this workflow's models.",
      );
    } finally {
      if (seq === openSeq) loading.value = false;
    }
  },
  { immediate: true },
);

/**
 * Fill the companion rows for the checkpoint just chosen.
 *
 * A row whose own file is among the proposals keeps it, with the step that
 * vouched for it; the others take the next proposal nobody has taken; a row
 * with none left keeps the workflow's file and is marked, never filled with a
 * guess. Called once per checkpoint choice, which is what the server's
 * full-shelf read can afford.
 */
watch(checkpointId, async () => {
  flags.value = [];
  cloneError.value = "";
  const chosen = chosenCheckpoint.value;
  // Bumped on every choice, the way back to the original included, so a read
  // still in flight for the previous choice can never fill the rows.
  const seq = ++readSeq;
  proposalFailed.value = false;
  if (!chosen || isOriginal(chosen)) {
    proposing.value = false;
    resetRows();
    return;
  }
  if (!nameTouched.value) {
    name.value = `${props.cardName || "Workflow"} — ${modelLabel(chosen)}`;
  }
  let body;
  proposing.value = true;
  try {
    body = await readModelSwap(props.workflowKey, { checkpointId: chosen.id });
  } catch (err) {
    if (seq === readSeq) {
      proposing.value = false;
      proposalFailed.value = true;
      resetRows();
      cloneError.value = errorMessage(
        err,
        "Could not read what goes with that checkpoint.",
      );
    }
    return;
  }
  if (seq !== readSeq) return;
  proposing.value = false;
  flags.value = body.flags || [];
  const taken = new Set();
  const byKind = {};
  // From the workflow's own files every time: a row filled for the previous
  // checkpoint is not evidence about this one. The row selects are disabled
  // while the read was in flight, so nothing the owner picked is lost here.
  resetRows();
  for (const row of rows.value) {
    const proposals = body.proposals?.[PROPOSAL_KIND[row.kind]] || [];
    byKind[row.kind] = proposals;
    const own = proposals.find((p) => same(p.filename, row.original));
    if (own) {
      taken.add(own.id);
      row.via = own.via;
    }
  }
  for (const row of rows.value) {
    if (row.via) continue;
    // A slot whose file has a known layout (clip_l, t5_xxl) takes only a
    // proposal of that layout, or of none recorded: recipe counts alone would
    // put a CLIP-L into a T5 slot.
    const next = byKind[row.kind].find(
      (p) =>
        !taken.has(p.id) &&
        (!row.family || !p.family || p.family === row.family),
    );
    if (next) {
      taken.add(next.id);
      Object.assign(row, { value: next.filename, via: next.via });
    } else {
      row.exhausted = byKind[row.kind].length > 0;
    }
  }
});

function resetRows() {
  for (const row of rows.value) {
    Object.assign(row, {
      value: row.own,
      via: null,
      touched: false,
      exhausted: false,
    });
  }
}

async function clone() {
  if (!canClone.value) return;
  cloneError.value = "";
  cloning.value = true;
  try {
    const answer = await store.cloneCardWithModels(props.workflowKey, {
      name: name.value.trim(),
      swaps: swaps.value,
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
</script>

<style scoped>
.cwm-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.cwm-note {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cwm-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cwm-warn {
  color: rgb(var(--v-theme-surface-warning));
}

.cwm-note--bad {
  color: rgb(var(--v-theme-surface-error));
}
</style>
