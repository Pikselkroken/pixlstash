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

    <!-- A warning, not an input error: the name is perfectly valid, it is the
         save that has changed meaning. Red-outlining the field would say the
         owner typed something wrong. -->
    <p v-if="collision" class="svr-note svr-warn">
      <v-icon size="14">mdi-alert-circle-outline</v-icon>
      <span
        >“{{ collision.name }}” is already saved here. Saving replaces
        it.</span
      >
    </p>

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
        :icon-left="collision ? 'content-save-edit-outline' : 'bookmark-plus-outline'"
        :loading="saving"
        :disabled="!name.trim() || !workflowKey"
        @click="save"
      >
        {{ collision ? `Replace “${collision.name}”` : "Save recipe" }}
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
 *
 * **A name already on this card's stack turns Save into Replace** (#1480).
 * Nothing makes a recipe name unique, so the alternative was a second row
 * reading exactly the same thing as the first, with nothing to tell them
 * apart and no undo.
 */
import { computed, reactive, ref, useId, watch } from "vue";
import { VIcon } from "vuetify/components";

import {
  createSavedRecipe,
  editSavedRecipe,
  listSavedRecipes,
} from "../../api/recipes";
import { getWorkflowCard } from "../../api/workflows";
import { useConfirm } from "../../composables/useConfirm";
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
const { confirm } = useConfirm();
const nameId = useId();

const name = ref("");
const saving = ref(false);
const saveError = ref("");
/** Row id -> whether it goes into the recipe. */
const kept = reactive({});
/** The card's own name, for "Saved to X". */
const stackName = ref("");
/**
 * The recipes already on this card's STACK, for the name collision.
 *
 * Read here rather than handed over by the caller, although two of the three
 * already hold a list: **`GET /recipes?workflow_key=` resolves the stack
 * server-side and nothing on the client can.** The Recipes tab's own list is
 * the union of every selected card's stack, and a `PATCH` matched against a
 * row of another stack would overwrite a recipe on a workflow nobody was
 * saving to; narrowing that union by `workflow_key` instead drops the target's
 * own stack siblings, which is the collision this exists to catch. One read on
 * a dialog somebody deliberately opened is the cheaper half of that trade.
 */
const existing = ref([]);

/** The LoRAs a run will not apply, because the shelf cannot identify them. */
const unknownLoras = computed(() =>
  props.loras.filter((lora) => !lora.sha256).map((lora) => lora.filename),
);

/**
 * The saved recipe this name would collide with, or null.
 *
 * **Nothing makes a recipe name unique** - not the column, not `POST
 * /recipes`, not this dialog - so without this two visits to the same card
 * make two rows reading the same thing and no way to tell them apart. The
 * answer is not to refuse the name but to change what the button does: the
 * owner naming a recipe that already exists means the one that exists.
 *
 * **Matched case-insensitively, and the row's OWN spelling is what the button
 * and the warning print.** "Portrait" and "portrait" are one name to anybody
 * reading the list, so treating them as two would leave exactly the pair this
 * closes; saying which spelling is about to be replaced is what keeps the
 * folding honest.
 */
const collision = computed(() => {
  const wanted = name.value.trim().toLowerCase();
  if (!wanted) return null;
  return (
    existing.value.find(
      (row) => (row.name || "").trim().toLowerCase() === wanted,
    ) || null
  );
});

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
    existing.value = [];
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
      unproposeATakenName();
    } catch (err) {
      // The line it feeds is not load-bearing: without a name the dialog drops
      // the sentence rather than printing a blank one, and the save is
      // unaffected.
      console.warn("Could not read the card a recipe would be saved to:", err);
    }
    try {
      const rows = await listSavedRecipes(wanted);
      if (wanted !== props.workflowKey) return;
      existing.value = rows;
      unproposeATakenName();
    } catch (err) {
      // Without the list there is no collision to spot, so the dialog goes on
      // offering Save - which is what it did before #1480 and is the safe way
      // round: a failed read must not turn a save into an overwrite.
      console.warn("Could not read this card's saved recipes:", err);
    }
  },
  { immediate: true },
);

/**
 * Never OPEN on a name that means Replace.
 *
 * The box is prefilled with the card's name, so a card whose first recipe took
 * that name would hand every later save a destructive primary by default, one
 * Enter away, over a dialog still titled "Save as recipe". A suggestion the
 * dialog made is withdrawn instead; a name the OWNER types is theirs, and
 * Replace is what they asked for.
 */
function unproposeATakenName() {
  if (collision.value && name.value === (props.suggestedName || stackName.value)) {
    name.value = "";
  }
}

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

/** What the recipe keeps, in the shape both the POST and the PATCH take. */
function body() {
  return {
    name: name.value.trim(),
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
  };
}

/**
 * The same body, minus what this surface cannot speak for.
 *
 * `PATCH /recipes/{id}` writes every field the request carries - its
 * `exclude_unset` sees the key, not the value - so a `null` here is an
 * instruction to forget. The Recipes tab and the lightbox open this dialog
 * with no picture of their own on some paths, and nulling the row's
 * `source_picture_id` would take away the picture the recipe is drawn from
 * without anything on screen having mentioned it. Every other field IS on
 * screen, in the list headed "What the recipe keeps", which is what the
 * confirm says is overwritten.
 */
function replacement() {
  const changes = body();
  if (changes.source_picture_id === null) delete changes.source_picture_id;
  return changes;
}

async function save() {
  const label = name.value.trim();
  if (!label || !props.workflowKey || saving.value) return;
  const replacing = collision.value;
  saveError.value = "";
  // **Set before the await, not after.** The confirm below is the first thing
  // in this function to yield, and a guard read before it would let a second
  // press re-enter and queue a second confirm and a second write.
  saving.value = true;
  try {
    // **The same gate the Recipes tab's Delete has, for the same reason.** A
    // replace overwrites a saved row's prompt, LoRAs and settings and there is
    // no undo; naming the row on the button is an affordance, not a second
    // press. `PATCH /recipes/{id}` keeps the row's id, so its place in the tab
    // and the pictures it is credited with survive.
    //
    // The rename is named when there is one: the button prints the ROW's
    // spelling and the write sends the TYPED one, so "Portrait" replaced by
    // "portrait" is a rename the owner has not been told about anywhere else.
    if (
      replacing &&
      !(await confirm({
        title: `Replace “${replacing.name}”?`,
        message:
          (replacing.name || "").trim() === label
            ? "Its prompt, LoRAs and settings are overwritten. This cannot be undone."
            : `It is renamed to “${label}” and its prompt, LoRAs and settings are overwritten. This cannot be undone.`,
        confirmLabel: "Replace",
        danger: true,
      }))
    ) {
      return;
    }
    const saved = replacing
      ? await editSavedRecipe(replacing.id, replacement())
      : await createSavedRecipe({
          workflow_key: props.workflowKey,
          ...body(),
        });
    const credited = await creditOf(saved?.id);
    const verb = replacing ? "Replaced" : "Saved";
    notices.push({
      level: "success",
      text: credited
        ? `${verb} “${label}”. ${credited} picture${credited === 1 ? "" : "s"} already match it.`
        : `${verb} “${label}”.`,
    });
    // Every surface showing this card's recipes re-reads, wherever the save
    // was made from: the Recipes tab is often the screen behind this dialog.
    workflows.notedRecipesChanged();
    emit("saved", saved);
    emit("close");
  } catch (err) {
    saveError.value = errorMessage(
      err,
      replacing
        ? "Could not replace that recipe."
        : "Could not save that recipe.",
    );
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
