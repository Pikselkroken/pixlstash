<template>
  <AppDialog
    :open="open"
    title="Save as recipe"
    size="md"
    @close="emit('close')"
    @accept="onAccept"
  >
    <!-- ── The question: a LoRA the shelf cannot name, taken out of what? ──
         A recipe cannot delete a loader (`POST /workflows/run` overrides a
         slot, it cannot remove one), so "take it out" means two different
         acts here, and which one is meant differs from one picture to the
         next. Asked every time; no preference is stored. -->
    <template v-if="asking">
      <p class="svr-question" data-testid="svr-question">
        Take {{ asking.stem }} out of what?
      </p>
      <p class="svr-note svr-quiet">
        The workflow has a loader for it, and a recipe cannot delete one. These
        are two different acts.
      </p>
      <div role="radiogroup" :aria-label="`Take ${asking.stem} out of what?`">
        <label
          v-for="choice in TAKE_OUT_CHOICES"
          :key="choice.id"
          class="svr-choice"
          :class="{ 'svr-choice--on': takeOut === choice.id }"
        >
          <input
            v-model="takeOut"
            class="svr-box"
            type="radio"
            :name="`${nameId}-take-out`"
            :value="choice.id"
          />
          <span class="svr-choice-text">
            <b class="svr-strong">{{ choice.label }}</b>
            <span class="svr-quiet">{{ choice.hint }}</span>
          </span>
        </label>
      </div>
      <p class="svr-note svr-quiet">
        Either way the saved recipe holds exactly the
        {{ keptAfterAsking === 1 ? "LoRA" : `${keptAfterAsking} LoRAs` }} the
        list showed.
      </p>
    </template>

    <template v-else>
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
        <p v-if="!rows.length && !loraRows.length" class="svr-note svr-quiet">
          Nothing was changed from the workflow, so this recipe would keep only
          its name.
        </p>
      </div>

      <!-- One row per LoRA, not one checkbox line (#1478): what the recipe
           keeps is exactly the rows standing here, and each can be taken off on
           its own. A row taken off stays on screen, struck through, with
           Restore — "the list is one shorter" does not say what went.

           A LoRA the shelf cannot identify is flagged rather than dropped in
           silence: the recipe's key has to be the picture's key, and a run
           ignores it, which is the promise the flag makes out loud. -->
      <div v-if="loraRows.length">
        <span class="section-label">LoRAs this recipe keeps</span>
        <ul class="svr-loras">
          <li
            v-for="row in loraRows"
            :key="row.id"
            class="svr-lora"
            :class="{ 'svr-lora--off': row.removed }"
            :data-lora="row.id"
          >
            <div class="svr-lora-line">
              <v-icon
                v-if="!row.sha256"
                size="16"
                class="svr-flag-glyph"
                aria-hidden="true"
                >mdi-alert-outline</v-icon
              >
              <span class="svr-lora-name">{{ row.label }}</span>
              <span v-if="row.removed" class="visually-hidden">, taken off</span>
              <span class="svr-lora-strength">{{ row.strengthText }}</span>
              <AppButton
                v-if="row.removed"
                size="sm"
                data-focus="restore"
                :aria-label="`Restore ${row.stem}`"
                @click="restoreLora(row)"
              >
                Restore
              </AppButton>
              <AppBarButton
                v-else
                icon="delete-outline"
                data-focus="delete"
                :tooltip="`Take ${row.stem} off this recipe`"
                @click="removeLora(row)"
              />
            </div>
            <p v-if="!row.sha256 && !row.removed" class="svr-note svr-flag">
              Not on your model shelf. A run ignores this one, so a recipe that
              lists it promises a LoRA it will not apply.
            </p>
          </li>
        </ul>
      </div>

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
    </template>

    <p class="visually-hidden" role="status" aria-live="polite">
      {{ liveMessage }}
    </p>

    <template #footer>
      <template v-if="asking">
        <AppButton @click="cancelAsking">Back</AppButton>
        <AppButton
          variant="primary"
          :disabled="!takeOut"
          @click="answerTakeOut"
        >
          Take it out
        </AppButton>
      </template>
      <template v-else>
        <AppButton :disabled="saving" @click="emit('close')">Cancel</AppButton>
        <AppButton
          variant="primary"
          :icon-left="collision ? 'content-save-edit-outline' : 'bookmark-plus-outline'"
          :loading="saving"
          :disabled="!name.trim() || !workflowKey || existingPending"
          @click="save"
        >
          {{ collision ? `Replace “${collision.name}”` : "Save recipe" }}
        </AppButton>
      </template>
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
 * choice. The LoRAs are one row EACH with a trash of their own (#1478), and
 * the saved recipe holds exactly the rows left standing.
 *
 * **A LoRA the shelf cannot name is flagged, and its trash asks "Take X out of
 * what?"** — this recipe, or the workflow's loader for it. A recipe cannot
 * delete a loader, so the second answer hands the owner to Edit LoRAs… on the
 * card with that entry already deleted (`utils/loraChain.editLorasRoute`).
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
import { computed, nextTick, reactive, ref, useId, watch } from "vue";
import { useRouter } from "vue-router";
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
import { editLorasRoute, loraStem } from "../../utils/loraChain";
import AppBarButton from "../widgets/AppBarButton.vue";
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

/**
 * `handoff` fires when the owner answered "The workflow": this dialog has
 * closed and sent them to Edit LoRAs… on the card, so a host that is itself a
 * popup (the Run popup) closes too rather than standing over the Workflows
 * screen it was sent to.
 */
const emit = defineEmits(["close", "saved", "handoff"]);

/** The two answers to "Take X out of what?", in the design's words. */
const TAKE_OUT_CHOICES = [
  {
    id: "recipe",
    label: "This recipe",
    hint: "The recipe stops listing it. Runs from this workflow still carry the loader, and ComfyUI still loads it by name.",
  },
  {
    id: "workflow",
    label: "The workflow",
    hint: "Opens Edit LoRAs… with that entry deleted, ready to save as a new workflow.",
  },
];

const notices = useNoticeStore();
const workflows = useWorkflowsStore();
const { confirm } = useConfirm();
const router = useRouter();
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
/** A suggested or typed name cannot save until its stack collision check settles. */
const existingPending = ref(false);

/** Row id -> taken off this recipe. Reset each time the dialog opens. */
const removedLoras = reactive({});
/** The flagged row the question is about, or null. */
const asking = ref(null);
/** The answer picked to the question: "recipe", "workflow" or "". */
const takeOut = ref("");
const liveMessage = ref("");

/**
 * One row per LoRA the caller handed over, in the caller's order.
 *
 * A LoRA the shelf can name is shown by its stem; one it cannot is shown as
 * the FILE, because that file name is the only thing anybody can go and look
 * for. Strength to two decimals, as the Run popup prints it.
 */
const loraRows = computed(() =>
  props.loras.map((lora, index) => {
    const base = String(lora.filename || "").split(/[\\/]/).pop();
    const strength = Number(lora.strength ?? 1);
    const id = `l${index}`;
    return {
      id,
      filename: lora.filename,
      sha256: lora.sha256 || "",
      strength: Number.isFinite(strength) ? strength : 1,
      strengthText: (Number.isFinite(strength) ? strength : 1).toFixed(2),
      stem: loraStem(lora.filename),
      label: lora.sha256 ? loraStem(lora.filename) : base,
      removed: Boolean(removedLoras[id]),
    };
  }),
);

/** How many LoRAs the saved recipe would hold once the asked-about one goes. */
const keptAfterAsking = computed(
  () =>
    loraRows.value.filter(
      (row) => !row.removed && row.id !== asking.value?.id,
    ).length,
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
    for (const key of Object.keys(removedLoras)) delete removedLoras[key];
    asking.value = null;
    takeOut.value = "";
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
    existingPending.value = Boolean(props.open && props.workflowKey);
    if (!props.open || !props.workflowKey) return;
    const wanted = props.workflowKey;
    void getWorkflowCard(wanted)
      .then((body) => {
        if (wanted !== props.workflowKey) return;
        stackName.value = body?.card?.name || "";
        // A caller with no name to suggest opened this over an empty box and a
        // disabled primary, one press after a button that said "Save as recipe".
        // Never over anything the owner has typed.
        if (!name.value) name.value = stackName.value;
        unproposeATakenName();
      })
      .catch((err) => {
        // The line it feeds is not load-bearing: without a name the dialog drops
        // the sentence rather than printing a blank one, and the save is
        // unaffected.
        console.warn("Could not read the card a recipe would be saved to:", err);
      });
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
    } finally {
      if (wanted === props.workflowKey) existingPending.value = false;
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

// ── Taking a LoRA off ─────────────────────────────────────────────────────

function say(message) {
  liveMessage.value = message;
}

async function focusLora(rowId, which) {
  await nextTick();
  const el = document.querySelector(
    `[data-lora="${rowId}"] [data-focus="${which}"]`,
  );
  el?.focus?.();
}

/**
 * The trash on one row.
 *
 * A LoRA the shelf names comes straight off the recipe: the recipe carries it
 * as an override, and not listing it is all "take it off" can mean. A flagged
 * one asks first, because the workflow carries a loader for it and the owner
 * may have meant that.
 */
async function removeLora(row) {
  if (!row.sha256) {
    asking.value = row;
    takeOut.value = "";
    return;
  }
  removedLoras[row.id] = true;
  say(`${row.stem} taken off this recipe. Restore is on the same row.`);
  await focusLora(row.id, "restore");
}

async function restoreLora(row) {
  delete removedLoras[row.id];
  say(`${row.stem} is back on this recipe.`);
  await focusLora(row.id, "delete");
}

async function cancelAsking() {
  const row = asking.value;
  asking.value = null;
  takeOut.value = "";
  if (row) await focusLora(row.id, "delete");
}

/**
 * Carry out the answer to "Take X out of what?".
 *
 * **This recipe** strikes the row, as any other trash does. **The workflow**
 * closes this dialog and opens the card on the Workflows screen with Edit
 * LoRAs… open and that loader already deleted: the edit is saved there, as a
 * new workflow, and nothing is saved here — a recipe written now would be
 * filed against the card that still carries the loader.
 */
async function answerTakeOut() {
  const row = asking.value;
  if (!row || !takeOut.value) return;
  if (takeOut.value === "recipe") {
    asking.value = null;
    takeOut.value = "";
    removedLoras[row.id] = true;
    say(`${row.stem} taken off this recipe. Restore is on the same row.`);
    await focusLora(row.id, "restore");
    return;
  }
  asking.value = null;
  takeOut.value = "";
  emit("close");
  emit("handoff", { workflowKey: props.workflowKey, filename: row.filename });
  await router?.push?.(
    editLorasRoute(props.workflowKey, { dropLora: row.filename }),
  );
}

function onAccept() {
  if (asking.value) void answerTakeOut();
  else void save();
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

/** A row's strength, or 1 when it has none: a 0 is a strength, not a gap. */
function strengthOr1(value) {
  if (value === null || value === undefined || value === "") return 1;
  const number = Number(value);
  return Number.isFinite(number) ? number : 1;
}

/** What the recipe keeps, in the shape both the POST and the PATCH take. */
function body() {
  return {
    name: name.value.trim(),
    prompt: kept.prompt ? props.prompt : "",
    negative: kept.negative ? props.negative : null,
    // Exactly the rows standing in the list: a row taken off is not sent,
    // and a flagged row still standing is, because the list said so.
    loras: loraRows.value
      .filter((row) => !row.removed)
      .map((row) => ({
        filename: row.filename,
        sha256: row.sha256 || null,
        strength: strengthOr1(row.strength),
      })),
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
  if (!label || !props.workflowKey || saving.value || existingPending.value) {
    return;
  }
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

.svr-question {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.svr-choice {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  align-items: start;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  cursor: pointer;
}

.svr-choice + .svr-choice {
  margin-top: var(--space-2);
}

/* Olive selects: the chosen answer takes the selection's bar and wash. */
.svr-choice--on {
  border-color: var(--active-bar);
  background: var(--active-wash);
}

.svr-choice-text {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  line-height: var(--leading-body);
}

.svr-loras {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.svr-lora {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-1) 0;
}

.svr-lora-line {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h);
  font-size: var(--text-sm);
}

.svr-lora-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.svr-lora-strength {
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A row taken off stays legible as a row that was taken off. */
.svr-lora--off .svr-lora-name,
.svr-lora--off .svr-lora-strength {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-decoration: line-through;
}

.svr-flag-glyph {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

.svr-flag {
  padding-left: calc(var(--gutter-glyph) + var(--space-3));
}

.svr-warn {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  color: rgb(var(--v-theme-surface-warning));
}
</style>
