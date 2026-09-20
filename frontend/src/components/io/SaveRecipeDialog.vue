<template>
  <AppDialog
    :open="open"
    title="Save as recipe"
    size="md"
    @close="emit('close')"
    @accept="save"
  >
    <AppInput
      v-model="name"
      label="Name"
      placeholder="Name this recipe"
      autofocus
      @enter="save"
    />

    <div>
      <span class="section-label">What the recipe keeps</span>
      <div
        v-for="row in rows"
        :key="row.id"
        class="svr-check"
        :class="{ 'svr-check--off': !kept[row.id] }"
      >
        <input
          :id="`${nameId}-${row.id}`"
          v-model="kept[row.id]"
          class="svr-box"
          type="checkbox"
        />
        <label class="svr-text" :for="`${nameId}-${row.id}`">
          {{ row.label }}
          <span v-if="row.aside" class="svr-quiet">{{ row.aside }}</span>
        </label>
        <span v-if="row.hint" class="svr-quiet svr-hint">{{ row.hint }}</span>
      </div>
      <p v-if="!rows.length" class="svr-note svr-quiet">
        Nothing was changed from the workflow, so this recipe would keep only
        its name.
      </p>
    </div>

    <!-- A LoRA this machine cannot identify is kept in the recipe, because the
         recipe's key has to be the picture's key, and said out loud here,
         because a run drops it: `POST /workflows/run` applies only the saved
         LoRAs that carry a digest. Removing it is #1478's gesture, not this
         dialog's. -->
    <p v-if="unknownLoras.length" class="svr-note svr-warn">
      <v-icon size="14">mdi-alert-circle-outline</v-icon>
      {{ unknownLoras.length === 1 ? "This LoRA is" : "These LoRAs are" }} not
      on your model shelf, so
      {{ unknownLoras.length === 1 ? "it is" : "they are" }} kept in the recipe
      but ignored when it runs: {{ unknownLoras.join(", ") }}.
    </p>

    <!-- A dialog headed "What the recipe keeps" must not drop a thing the
         reader can see on the screen behind it. The lightbox knows a
         picture's settings but not which graph input each one addresses, so
         it cannot offer them; saying so is the honest half. -->
    <p v-if="!overrides.length && settingsAside" class="svr-note svr-quiet">
      {{ settingsAside }}
    </p>

    <!-- What the recipe starts life crediting. The exact number of pictures
         that already match is only known once the row exists - credit is
         computed on a read of the saved recipes and there is no route that
         previews it - so the sentence says what is certain here and the
         notice after the save carries the count. -->
    <div v-if="sourcePictureId" class="svr-banner">
      <v-icon size="16">mdi-bookmark-outline</v-icon>
      <span
        >Starts with <b>this picture</b> and any others that already
        match.</span
      >
    </div>

    <p v-if="stackName" class="svr-note svr-quiet">
      Saved to <b class="svr-strong">{{ stackName }}</b
      >. It runs on any workflow in that stack.
    </p>

    <p v-if="saveError" class="svr-note svr-note--bad" role="alert">
      {{ saveError }}
    </p>

    <template #footer>
      <AppButton :disabled="saving" @click="emit('close')">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="bookmark-plus-outline"
        :loading="saving"
        :disabled="!name.trim() || !workflowKey"
        @click="save"
      >
        Save recipe
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * Save as recipe (v1.12 F6).
 *
 * The look a person is looking at, kept: the prompt, the LoRAs with their
 * strengths, the parameters they changed, and - off unless they ask for it -
 * the seed. Every line is a checkbox because the dialog's promise is "what the
 * recipe keeps", and a list you cannot argue with is a claim rather than a
 * choice.
 *
 * **The seed is off by default** and says why on the row: a recipe with a
 * fixed seed makes the same picture every time, which is almost never what
 * keeping a look means.
 */
import { computed, reactive, ref, useId, watch } from "vue";
import { VIcon } from "vuetify/components";

import { createSavedRecipe, listSavedRecipes } from "../../api/recipes";
import { getWorkflowCard } from "../../api/workflows";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The card the recipe is filed under. Its stack is what it can run on. */
  workflowKey: { type: String, default: "" },
  /** The name to start the box at - the card's, usually. */
  suggestedName: { type: String, default: "" },
  prompt: { type: String, default: "" },
  negative: { type: String, default: "" },
  /** `[{filename, sha256, strength}]`, already resolved to shelf names. */
  loras: { type: Array, default: () => [] },
  /** `[{address, label, value, base}]` - one per parameter changed. */
  overrides: { type: Array, default: () => [] },
  /** The seed's digits as text; empty when there is no fixed seed to keep. */
  seed: { type: String, default: "" },
  /**
   * One line naming what this caller cannot hand over, or "".
   *
   * Rendered only when no overrides came with it, so a caller that simply has
   * nothing changed says nothing.
   */
  settingsAside: { type: String, default: "" },
  sourcePictureId: { type: Number, default: null },
});

const emit = defineEmits(["close", "saved"]);

const notices = useNoticeStore();
const workflows = useWorkflowsStore();
const nameId = useId();

const name = ref("");
const saving = ref(false);
const saveError = ref("");
/** Row id -> whether it goes into the recipe. */
const kept = reactive({});
/** The card's own name, for "Saved to X". */
const stackName = ref("");

/** The LoRAs a run will not apply, because the shelf cannot identify them. */
const unknownLoras = computed(() =>
  props.loras.filter((lora) => !lora.sha256).map((lora) => lora.filename),
);

const rows = computed(() => {
  const out = [];
  if (props.prompt.trim()) {
    const words = props.prompt.trim().split(/\s+/).length;
    out.push({
      id: "prompt",
      label: "Prompt",
      hint: `${words} ${words === 1 ? "word" : "words"}`,
    });
  }
  if (props.negative.trim()) {
    out.push({ id: "negative", label: "Negative prompt" });
  }
  if (props.loras.length) {
    out.push({
      id: "loras",
      label: `LoRAs: ${props.loras
        .map((lora) => `${lora.filename} ${Number(lora.strength ?? 1)}`)
        .join(", ")}`,
    });
  }
  for (const row of props.overrides) {
    out.push({
      id: `o:${row.address}`,
      label: `${row.label} ${row.value}`,
      aside:
        row.base === undefined || row.base === null
          ? ""
          : `instead of the workflow's ${row.base}`,
    });
  }
  if (props.seed) {
    out.push({
      id: "seed",
      label: `Seed ${props.seed}`,
      hint: "off: every run varies",
    });
  }
  return out;
});

/**
 * Start every row checked except the seed, each time the dialog opens.
 *
 * Keyed on `open` rather than on `rows`, so a row arriving late (the card read
 * that names an override) cannot re-tick something the owner just cleared.
 */
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    saveError.value = "";
    // The card's name lands here when the caller had none to suggest; it
    // arrives with the read below, which is why this is not the only writer.
    name.value = props.suggestedName || stackName.value || "";
    for (const key of Object.keys(kept)) delete kept[key];
    for (const row of rows.value) kept[row.id] = row.id !== "seed";
  },
  { immediate: true },
);

// A row that appears after the open (a late card read) still needs a default,
// and the seed's is off.
watch(rows, (list) => {
  for (const row of list) {
    if (!(row.id in kept)) kept[row.id] = row.id !== "seed";
  }
});

watch(
  () => [props.open, props.workflowKey],
  async () => {
    stackName.value = "";
    if (!props.open || !props.workflowKey) return;
    const wanted = props.workflowKey;
    try {
      const body = await getWorkflowCard(wanted);
      if (wanted !== props.workflowKey) return;
      stackName.value = body?.card?.name || "";
      // A caller with no name to suggest opened this over an empty box and a
      // disabled primary, one press after a button that said "Save as recipe".
      // Never over anything the owner has typed.
      if (!name.value) name.value = stackName.value;
    } catch (err) {
      // The line it feeds is not load-bearing: without a name the dialog drops
      // the sentence rather than printing a blank one, and the save is
      // unaffected.
      console.warn("Could not read the card a recipe would be saved to:", err);
    }
  },
  { immediate: true },
);

/**
 * How many kept pictures the saved recipe now accounts for.
 *
 * Read back rather than taken from the POST, which answers 0 by design: credit
 * is computed on a read, and this is the first moment the number exists.
 */
async function creditOf(recipeId) {
  try {
    const list = await listSavedRecipes(props.workflowKey);
    return list.find((row) => row.id === recipeId)?.pictures ?? 0;
  } catch (err) {
    console.warn("Could not read the new recipe's credit:", err);
    return 0;
  }
}

async function save() {
  const label = name.value.trim();
  if (!label || !props.workflowKey || saving.value) return;
  saving.value = true;
  saveError.value = "";
  try {
    const saved = await createSavedRecipe({
      workflow_key: props.workflowKey,
      name: label,
      prompt: kept.prompt ? props.prompt : "",
      negative: kept.negative ? props.negative : null,
      loras: kept.loras
        ? props.loras.map((lora) => ({
            filename: lora.filename,
            sha256: lora.sha256 ?? null,
            strength: Number(lora.strength) || 1,
          }))
        : [],
      overrides: Object.fromEntries(
        props.overrides
          .filter((row) => kept[`o:${row.address}`])
          .map((row) => [row.address, row.value]),
      ),
      seed: kept.seed && props.seed ? props.seed : null,
      // **`keep_seed` is what makes the seed live.** `POST /workflows/run`
      // reads a saved seed only when this is true, so a row with the digits
      // and the flag off keeps a seed nothing will ever use — the checkbox
      // would be decoration and every run would still draw a new one.
      keep_seed: Boolean(kept.seed && props.seed),
      source_picture_id: props.sourcePictureId ?? null,
    });
    const credited = await creditOf(saved?.id);
    notices.push({
      level: "success",
      text: credited
        ? `Saved “${label}”. ${credited} picture${credited === 1 ? "" : "s"} already match it.`
        : `Saved “${label}”.`,
    });
    // Every surface showing this card's recipes re-reads, wherever the save
    // was made from: the Recipes tab is often the screen behind this dialog.
    workflows.notedRecipesChanged();
    emit("saved", saved);
    emit("close");
  } catch (err) {
    saveError.value = errorMessage(err, "Could not save that recipe.");
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.svr-check {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
}

/* An unkept row stays readable rather than disappearing: the list is the
   dialog's claim about the file, so a line taken out has to still be legible
   as a line that was taken out. */
.svr-check--off .svr-text {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-decoration: line-through;
}

.svr-box {
  width: 16px;
  height: 16px;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

.svr-text {
  min-width: 0;
  cursor: pointer;
}

.svr-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.svr-hint {
  font-size: var(--text-xs);
  white-space: nowrap;
}

.svr-banner {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
  font-size: var(--text-sm);
}

.svr-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

.svr-strong {
  color: rgb(var(--v-theme-on-surface));
}

.svr-note--bad {
  color: rgb(var(--v-theme-surface-error));
}

.svr-warn {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  color: rgb(var(--v-theme-surface-warning));
}
</style>
