<template>
  <AppDialog
    :open="Boolean(verb)"
    :title="title"
    :subtitle="subtitle"
    :width="480"
    @close="emit('close')"
  >
    <!-- Rename and Set base model are one field each; Set kind is a choice plus
         one conditional field. Three dialogs would be three copies of the same
         footer, the same Enter handling and the same in-flight guard. -->
    <label v-if="verb === 'rename'" class="sed-field">
      <span class="sed-label">Name</span>
      <input
        ref="firstFieldEl"
        v-model="name"
        class="sed-input"
        type="text"
        placeholder="Leave empty to use the filename"
        @keydown.enter.prevent="submit"
      />
    </label>

    <label v-else-if="verb === 'base-model'" class="sed-field">
      <span class="sed-label">Base model</span>
      <!-- Completing, never constraining: the column is free text and anything
           typed here is stored verbatim whether the list knows it or not. -->
      <BaseModelInput
        ref="firstFieldEl"
        v-model="baseModel"
        class="sed-input"
        placeholder="e.g. FLUX.2, SDXL 1.0. Leave empty to clear it"
        @confirm="submit"
      />
    </label>

    <template v-else-if="verb === 'kind'">
      <fieldset class="sed-field sed-fieldset">
        <legend class="sed-label">What these files are</legend>
        <label
          v-for="option in FILE_KINDS"
          :key="option.value"
          class="sed-radio"
        >
          <input v-model="fileKind" type="radio" :value="option.value" />
          <span>{{ option.label }}</span>
        </label>
      </fieldset>

      <!-- An adapter must name its algorithm: the hub carries
           `CHECK (file_kind <> 'adapter' OR kind IS NOT NULL)`, so a file with
           none would be refused by the server. Asking here is the difference
           between a form field and a rejected request. -->
      <label v-if="fileKind === 'adapter'" class="sed-field">
        <span class="sed-label">Algorithm</span>
        <input
          v-model="kind"
          class="sed-input"
          type="text"
          placeholder="lora, lokr, loha, dora, oft…"
          @keydown.enter.prevent="submit"
        />
      </label>

      <!-- What it is FOR, which is a different question from what it IS: one
           repo can caption and detect. The Kind column has always shown these
           words on rows PixlStash classified, so leaving them out of the editor
           put a value on screen that the reader could not correct. Checkboxes
           rather than radios because the set is genuinely plural, and untouched
           they are not sent at all. -->
      <fieldset class="sed-field sed-fieldset">
        <legend class="sed-label">What these files are for</legend>
        <p class="sed-hint">Optional. Leave empty if they are not a tool.</p>
        <label
          v-for="option in CAPABILITIES"
          :key="option"
          class="sed-radio"
          :title="`File these under ${capabilityLabel(option)} on the Feature axis.`"
        >
          <input
            type="checkbox"
            :checked="capabilities.includes(option)"
            @change="toggleCapability(option, $event.target.checked)"
          />
          <span>{{ capabilityLabel(option) }}</span>
        </label>
        <p v-if="capabilitiesDiffer" class="sed-hint">
          The selected models are not filed the same way. Ticking a box sets all
          of them.
        </p>
      </fieldset>
    </template>

    <!-- The first of the shelf's two confirmations, shown inline rather than as
         a second dialog: it is a property of the selection the reader is
         already looking at, and stacking a prompt on a form is how people learn
         to click through prompts. -->
    <p v-if="overwriteWarning" class="sed-warning" role="status">
      {{ overwriteWarning }}
    </p>

    <template #footer>
      <AppButton variant="ghost" key-hint="esc" @click="emit('close')">
        Cancel
      </AppButton>
      <AppButton
        variant="primary"
        key-hint="enter"
        :loading="working"
        :disabled="!canSubmit"
        @click="submit"
      >
        {{ confirmLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
// Three of the shelf's five verbs, one dialog (shelf plan F3).
//
// Rename, Set base model and Set kind write one curated column each and differ
// only in which one, exactly as `PATCH /models` does on the other side. The
// dialog mirrors the route rather than inventing a shape of its own, so there
// is one place where "which fields does this verb send" is decided.
//
// It sends ONLY the field its verb owns. That is the whole reason the route
// distinguishes an absent field from a null one: Set base model across a
// selection must not blank the names in it.

import { computed, nextTick, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import BaseModelInput from "../widgets/BaseModelInput.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { adapterKindKey, capabilityLabel } from "../../utils/modelShelf";

const props = defineProps({
  /** `rename` | `base-model` | `kind`, or `""` when the dialog is closed. */
  verb: { type: String, default: "" },
});
const emit = defineEmits(["close"]);

const store = useModelShelfStore();

// The support kinds are correctable like the rest, and are the two most likely
// to need it: they are read off the folder the file sits in, so one kept outside
// the usual layout gets whatever that folder says.
const FILE_KINDS = [
  { value: "adapter", label: "Adapter (LoRA and friends)" },
  { value: "checkpoint", label: "Checkpoint (a base model)" },
  { value: "vae", label: "VAE" },
  { value: "text_encoder", label: "Text encoder" },
  { value: "unknown", label: "Unclassified" },
];

// The features a model may be filed under, in the shelf's own vocabulary. Two
// of the eight stored values are missing on purpose and the server refuses them
// too: `checkpoint` is what the file IS and the radios above already ask it,
// and `other` is the classifier's shrug, which an empty set says without
// printing a heading for it.
const CAPABILITIES = [
  "captioner",
  "tagger",
  "detector",
  "face",
  "search",
  "scorer",
];

const name = ref("");
const baseModel = ref("");
const kind = ref("");
const fileKind = ref("adapter");
const capabilities = ref([]);
// Untouched, the set is not sent at all. A dialog that always posted it would
// flatten a mixed selection onto whatever the first row happened to say, on a
// verb the reader opened to change the file kind.
const capabilitiesTouched = ref(false);
const capabilitiesDiffer = ref(false);
const working = ref(false);
const firstFieldEl = ref(null);

function toggleCapability(capability, on) {
  capabilitiesTouched.value = true;
  const next = capabilities.value.filter((item) => item !== capability);
  // Appended, never re-sorted: the order is what the server stores and what the
  // Kind column reads primary-first, so the first box ticked is the word the
  // row shows.
  capabilities.value = on ? [...next, capability] : next;
}

const count = computed(() => store.selectedRows.length);

const title = computed(
  () =>
    ({
      rename: "Rename model",
      "base-model": "Set base model",
      kind: "Set kind",
    })[props.verb] || "",
);

const subtitle = computed(() => {
  if (props.verb === "rename") return store.selectedRows[0]?.name?.text || "";
  return `${count.value.toLocaleString()} ${count.value === 1 ? "model" : "models"} selected`;
});

const confirmLabel = computed(() =>
  props.verb === "rename" ? "Rename" : `Apply to ${count.value}`,
);

/**
 * The bulk base-model overwrite prompt.
 *
 * Named as a count of what will be REPLACED rather than of what is selected:
 * "12 selected" is something the reader can already see, and the number that
 * decides whether this was a mistake is how many recorded values are about to
 * be gone. There is no undo, so this sentence is the whole safety net.
 */
const overwriteWarning = computed(() => {
  if (props.verb !== "base-model" || count.value < 2) return "";
  const replacing = store.selectedRows.filter(
    (row) => row.base_model && row.base_model !== baseModel.value,
  ).length;
  if (!replacing) return "";
  return `This replaces the base model recorded on ${replacing} of them. There is no undo.`;
});

const canSubmit = computed(() => {
  if (working.value || !count.value) return false;
  // An adapter without an algorithm is refused by the hub's own CHECK, so the
  // button is the honest place to say so rather than the error that follows.
  if (props.verb === "kind" && fileKind.value === "adapter") {
    return Boolean(kind.value.trim() || everySelectedRowAlreadyHasAKind.value);
  }
  return true;
});

// Folded, not `Boolean(row.kind)`: the hub CHECK makes an adapter's kind NOT
// NULL but not non-empty, so a whitespace-only one is truthy and would count as
// already set - leaving Save enabled on a verb with nothing to write.
const everySelectedRowAlreadyHasAKind = computed(() =>
  store.selectedRows.every((row) => Boolean(adapterKindKey(row.kind))),
);

// Seed from the selection on open, so the field shows what is there now rather
// than an empty box the reader has to interpret as "unset" or "unchanged".
watch(
  () => props.verb,
  async (verb) => {
    if (!verb) return;
    const rows = store.selectedRows;
    name.value = rows.length === 1 ? rows[0].display_name || "" : "";
    const shared = rows.every((row) => row.base_model === rows[0]?.base_model);
    baseModel.value = shared ? rows[0]?.base_model || "" : "";
    fileKind.value = rows[0]?.file_kind || "adapter";
    // Compared FOLDED. The shelf now draws `lora` and `LoRA` under one group
    // header, in one checkbox, with both Kind cells reading `LoRA`, so a raw
    // comparison here opened this field blank on a selection the rest of the
    // screen presents as one algorithm - and blank means "they disagree".
    // Seeded with the first row's own spelling, so saving converges the two.
    const sharedKind = rows.every(
      (row) => adapterKindKey(row.kind) === adapterKindKey(rows[0]?.kind),
    );
    kind.value = sharedKind ? rows[0]?.kind || "" : "";
    // Seeded from the selection when it agrees, and left EMPTY when it does not
    // - with a line saying so, because an empty box that means "they differ"
    // and an empty box that means "none" are the same box otherwise.
    const sets = rows.map((row) =>
      (Array.isArray(row.capabilities) ? row.capabilities : []).join(","),
    );
    capabilitiesDiffer.value = sets.some((set) => set !== sets[0]);
    capabilities.value = capabilitiesDiffer.value
      ? []
      : (rows[0]?.capabilities || []).filter((c) => CAPABILITIES.includes(c));
    capabilitiesTouched.value = false;
    working.value = false;
    await nextTick();
    firstFieldEl.value?.focus();
  },
  { immediate: true },
);

/** The body for this verb, and nothing else. */
function changes() {
  if (props.verb === "rename")
    return { display_name: name.value.trim() || null };
  if (props.verb === "base-model") {
    return { base_model: baseModel.value.trim() || null };
  }
  const patch = { file_kind: fileKind.value };
  if (fileKind.value === "adapter" && kind.value.trim()) {
    patch.kind = kind.value.trim();
  }
  if (capabilitiesTouched.value) patch.capabilities = capabilities.value;
  return patch;
}

async function submit() {
  if (!canSubmit.value) return;
  working.value = true;
  const ok = await store.editSelected(changes());
  working.value = false;
  if (ok) emit("close");
}
</script>

<style scoped>
.sed-field {
  display: block;
  margin-bottom: var(--space-5);
}

.sed-fieldset {
  border: 0;
  padding: 0;
  margin-inline: 0;
}

.sed-label {
  display: block;
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-panel));
}

.sed-input {
  width: 100%;
  min-height: 36px;
  padding: 0 var(--space-3);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-panel));
  background: rgba(var(--v-theme-on-panel), 0.06);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.sed-radio {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-panel));
}

/* Not an alert: it appears as the reader types rather than in response to a
   failure, and a live alert would interrupt them mid-word. */
/* Sits under a legend and above its boxes, so it is quieter than the label and
   never mistaken for one of the options. */
.sed-hint {
  margin: 0 0 var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), 0.7);
}

.sed-warning {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-panel));
  background: rgba(var(--v-theme-warning), 0.14);
  border: 1px solid rgba(var(--v-theme-warning), 0.4);
}
</style>
