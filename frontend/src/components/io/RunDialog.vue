<template>
  <AppDialog
    :open="open"
    size="lg"
    :title="title"
    :subtitle="subtitle"
    :persistent="submitting || dirty"
    @close="onRequestClose"
    @keydown.esc="onEscape"
  >
    <p v-if="loadFailed" class="rund-note rund-note--bad" role="alert">
      {{ loadFailed }}
    </p>
    <p v-else-if="loading" class="rund-note" role="status">
      Reading what this runs…
    </p>

    <div v-else class="rund">
      <!-- ── Left: what the run is made from ──────────────────────────────
           168px wide and the picture 168×252, which is the design's own
           measurement and nothing else in the app uses: a component-local
           value rather than a token nobody else would read. -->
      <div class="rund-src">
        <img
          v-if="coverUrl"
          class="rund-pic"
          :src="coverUrl"
          :alt="`The picture this run is made from: ${sourceName}`"
        />
        <span v-else class="rund-pic rund-pic--empty">
          <v-icon size="28">mdi-sitemap-outline</v-icon>
        </span>
        <div class="rund-meta">
          <span class="rund-name">{{ sourceName }}</span>
          <span class="rund-sub">{{ sourceKindLine }}</span>
        </div>
        <dl class="rund-kv">
          <dt>Results go to</dt>
          <dd v-if="!picksDestination">{{ destinationLine }}</dd>
          <dd v-else>
            <AppSelect
              v-model="destinationSetId"
              label="Results go to"
              hide-label
              compact
              :options="setOptions"
            />
          </dd>
          <template v-if="seedText">
            <dt>Seed of the original</dt>
            <dd class="rund-mono">{{ seedText }}</dd>
          </template>
        </dl>
      </div>

      <!-- ── Right: the form, four columns ──────────────────────────────── -->
      <div class="rund-form">
        <div class="rund-f rund-f--4">
          <span class="rund-l">Workflow</span>
          <AppSelect
            v-model="workflowKey"
            label="Workflow"
            hide-label
            compact
            :options="workflowOptions"
            :disabled="submitting"
          />
        </div>

        <p v-if="fellBack.length" class="rund-f rund-f--4 rund-note" role="status">
          {{ fellBackLine }}
        </p>

        <!-- ── Pictures in (#1457): one row per picture input of the card,
             straight off the pre-flight, which also decides how each is
             filled. The popup never re-derives that: `fill` is the server's
             answer, so a card whose one open input the selection fills shows
             the selection there without anybody having asked for it. -->
        <div
          v-if="pictureInputs.length"
          class="rund-f rund-f--4 rund-pics"
          role="group"
          :aria-labelledby="picsLabelId"
        >
          <span :id="picsLabelId" class="rund-l">Pictures</span>
          <div
            v-for="input in pictureInputs"
            :key="address(input)"
            class="rund-in"
          >
            <!-- The selection is not a picker: it is the pictures the run
                 repeats over, so it is shown as what it is, and changed by
                 moving it rather than by choosing one picture. -->
            <span
              v-if="input.fill === 'selection'"
              class="rund-in-tile rund-in-tile--sel"
            >
              <img
                v-for="id in pictureIds.slice(0, 4)"
                :key="id"
                class="rund-in-cell"
                :src="thumbUrl(id)"
                alt=""
              />
            </span>
            <!-- `aria-disabled`, never `disabled`, on every control in these
                 rows: a natively-disabled button drops out of the tab order
                 while its own write is in flight, and a keyboard user's focus
                 falls to the page. The handlers do the refusing. -->
            <button
              v-else
              type="button"
              class="rund-in-tile"
              :class="{
                'rund-in-tile--empty': !input.picture_id,
                'rund-in-tile--off': !setupReady,
              }"
              :data-input="address(input)"
              :aria-label="
                input.picture_id
                  ? `Change the picture for ${inputTitle(input)}`
                  : `Choose a picture for ${inputTitle(input)}`
              "
              :aria-disabled="setupReady ? undefined : 'true'"
              @click="openPicker(input)"
            >
              <img
                v-if="input.picture_id"
                class="rund-in-img"
                :src="thumbUrl(input.picture_id)"
                alt=""
              />
              <v-icon v-else size="24">mdi-image-plus-outline</v-icon>
            </button>
            <span class="rund-in-text">
              <span class="rund-in-title">{{ inputTitle(input) }}</span>
              <span
                class="rund-in-sub"
                :class="{ 'rund-in-sub--bad': input.fill === null }"
              >
                {{ inputLine(input) }}
              </span>
              <AppButton
                v-if="pictureIds.length && input.fill !== 'selection'"
                size="sm"
                class="rund-in-move"
                :aria-disabled="setupReady ? undefined : 'true'"
                @click="useSelectionHere(input)"
              >
                Use my selection here
              </AppButton>
            </span>
            <!-- Keep this picture: a pin is the card's own setup, so it holds
                 for every later run of this workflow in this library. -->
            <!-- One fixed name; `aria-pressed` alone says whether it is on,
                 so a reader never hears "Stop keeping... pressed". -->
            <AppBarButton
              v-if="input.picture_id && input.fill !== 'selection'"
              :icon="input.fill === 'fixed' ? 'pin' : 'pin-outline'"
              :active="input.fill === 'fixed'"
              :aria-pressed="input.fill === 'fixed' ? 'true' : 'false'"
              :aria-disabled="setupReady ? undefined : 'true'"
              :tooltip="`Keep this picture for ${inputTitle(input)} on every run`"
              @click="togglePin(input)"
            />
            <span v-else class="rund-x-gap" />
            <AppBarButton
              v-if="input.fill === 'request'"
              icon="close"
              :tooltip="`Clear the picture for ${inputTitle(input)}`"
              :aria-disabled="setupReady ? undefined : 'true'"
              @click="clearPick(input)"
            />
            <span v-else class="rund-x-gap" />
          </div>
          <p v-if="inputsError" class="rund-note rund-note--bad" role="alert">
            {{ inputsError }}
          </p>
        </div>

        <div class="rund-f rund-f--4">
          <span class="rund-l">
            Prompt
            <RunResetChip
              v-if="prompt !== basePrompt"
              :value="basePrompt || 'no prompt'"
              label="Prompt"
              @reset="prompt = basePrompt"
            />
          </span>
          <AppTextarea
            v-model="prompt"
            label="Prompt"
            :rows="3"
            :disabled="submitting"
            @keydown.stop
          />
        </div>

        <div class="rund-f rund-f--4">
          <span class="rund-l">
            LoRAs<span class="rund-sp" /><span class="rund-l2">Strength</span>
            <span class="rund-x-gap" />
          </span>
          <p v-if="!loraSlots.length" class="rund-note">
            {{
              hasRecipe
                ? "This workflow has no LoRA loader."
                : "Open one of this workflow's pictures to change its LoRAs."
            }}
          </p>
          <div v-for="(row, index) in loras" :key="row.key" class="rund-lora">
            <AppSelect
              v-model="row.sha256"
              :label="`LoRA ${index + 1}`"
              hide-label
              compact
              :options="optionsFor(row)"
              :disabled="submitting"
            />
            <AppInput
              v-model.number="row.strength"
              :aria-label="`Strength of LoRA ${index + 1}`"
              type="number"
              min="-10"
              max="10"
              :disabled="submitting"
              @keydown.stop
            />
            <!-- Only a row the owner ADDED can be taken away again. A slot the
                 graph carries is in the graph: `POST /workflows/run` overrides
                 a slot, it cannot delete one, so an × here would promise a
                 removal the run does not perform. -->
            <AppBarButton
              v-if="row.added"
              icon="close"
              :tooltip="`Remove LoRA ${index + 1}`"
              :disabled="submitting"
              @click="removeLora(index)"
            />
            <span v-else class="rund-x-gap" />
          </div>
          <p v-if="unresolvedLoras.length" class="rund-note">
            Not on your model shelf, so {{ unresolvedLoras.length === 1 ? "it stays" : "they stay" }}
            as the workflow has {{ unresolvedLoras.length === 1 ? "it" : "them" }}:
            {{ unresolvedLoras.join(", ") }}.
          </p>
          <AppButton
            v-if="loraSlots.length"
            size="sm"
            icon-left="plus"
            :disabled="submitting || loras.length >= loraSlots.length"
            @click="addLora"
          >
            Add LoRA
          </AppButton>
        </div>

        <div v-if="sizeFields.length" class="rund-f rund-f--2">
          <span class="rund-l">
            Size
            <RunResetChip
              v-if="sizeEdited"
              :value="`${baseOf(sizeFields[0])} × ${baseOf(sizeFields[1])}`"
              label="Size"
              @reset="resetSize"
            />
          </span>
          <div class="rund-size">
            <AppInput
              v-for="field in sizeFields"
              :key="address(field)"
              :model-value="currentValue(field)"
              :aria-label="field.label"
              type="number"
              :disabled="submitting"
              @update:model-value="(v) => setValue(field, coerce(field, v))"
              @keydown.stop
            />
          </div>
        </div>

        <div v-for="field in scalarFields" :key="address(field)" class="rund-f">
          <span class="rund-l">
            {{ field.label }}
            <RunResetChip
              v-if="isEdited(field)"
              :value="baseOf(field)"
              :label="field.label"
              @reset="resetValue(field)"
            />
          </span>
          <AppInput
            :model-value="currentValue(field)"
            :aria-label="field.label"
            type="number"
            :disabled="submitting"
            @update:model-value="(v) => setValue(field, coerce(field, v))"
            @keydown.stop
          />
        </div>

        <div class="rund-f">
          <span class="rund-l">Count</span>
          <AppInput
            v-model.number="count"
            aria-label="How many to make"
            type="number"
            min="1"
            :max="String(MAX_COUNT)"
            :disabled="submitting"
            @keydown.stop
          />
        </div>

        <div class="rund-f rund-f--3">
          <span class="rund-l">Seed</span>
          <AppSelect
            v-model="seedMode"
            label="Seed"
            hide-label
            compact
            :options="seedOptions"
            :disabled="submitting"
          />
          <!-- Deliberately a TEXT field: `type="number"` hands back a
               `Number`, which is exactly the rounding this avoids. -->
          <AppInput
            v-if="seedMode === 'fixed'"
            v-model="seed"
            aria-label="Seed"
            mono
            :error="seedError"
            :disabled="submitting"
            @keydown.stop
          />
        </div>

        <!-- Per run and never remembered (#1457, decision 4): the default
             follows whether a selected picture is actually fed into the graph,
             which is when the new picture is another take of that one. -->
        <!-- Shown only where its label is true: a picture fed into the graph
             is each output's own source, and one picture is the source of
             all of them. A selection nothing reads would stack every output
             behind the FIRST picture, which is not "the ones they came
             from". -->
        <div v-if="stackOffered" class="rund-f rund-f--4 rund-check">
          <input
            :id="stackId"
            v-model="stack"
            class="rund-box"
            type="checkbox"
            :disabled="submitting"
          />
          <label :for="stackId">Stack new pictures with the ones they came from</label>
        </div>

        <div v-if="checkpointField" class="rund-f rund-f--4">
          <span class="rund-l">
            Checkpoint
            <RunResetChip
              v-if="isEdited(checkpointField)"
              :value="baseOf(checkpointField)"
              label="Checkpoint"
              @reset="resetValue(checkpointField)"
            />
          </span>
          <AppInput
            :model-value="String(currentValue(checkpointField))"
            aria-label="Checkpoint"
            mono
            :disabled="submitting"
            @update:model-value="(v) => setValue(checkpointField, coerce(checkpointField, v))"
            @keydown.stop
          />
        </div>

        <div class="rund-f rund-f--4 rund-more">
          <!-- The negative prompt only opens when the original had one: an
               empty box under every run would read as a field somebody forgot
               to fill in. -->
          <details v-if="baseNegative" class="rund-disc">
            <summary>Negative prompt</summary>
            <AppTextarea
              v-model="negative"
              label="Negative prompt"
              :rows="2"
              :disabled="submitting"
              @keydown.stop
            />
          </details>
          <details v-if="restFields.length" class="rund-disc">
            <summary>
              All {{ defaults.length }} parameters
              <span class="rund-quiet">{{ restFields.length }} more</span>
            </summary>
            <div v-for="field in restFields" :key="address(field)" class="rund-rest">
              <span class="rund-l">
                {{ field.label }}
                <RunResetChip
                  v-if="isEdited(field)"
                  :value="baseOf(field)"
                  :label="field.label"
                  @reset="resetValue(field)"
                />
              </span>
              <AppInput
                :model-value="String(currentValue(field))"
                :aria-label="field.label"
                :disabled="submitting"
                @update:model-value="(v) => setValue(field, coerce(field, v))"
                @keydown.stop
              />
            </div>
          </details>
        </div>

        <!-- Keyed on the position as well as the code: `reasons` is flat-mapped
             across every group, so two groups refusing for the same reason
             would collide on the code alone. -->
        <!-- `alert` is assertive and interrupts; a notice about a run that IS
             going ahead is a `status`. Only a real refusal earns that. -->
        <div
          v-if="runNotes.length"
          class="rund-f rund-f--4 rund-reasons"
          :role="shownRefusals.length ? 'alert' : 'status'"
        >
        <RunReasonNotice
          v-for="(reason, index) in runNotes"
          :key="`${index}:${reason.code}`"
          :reason="reason"
          :busy="preflighting"
          @settings="emit('open-settings', 'compute')"
          @retry="runPreflight()"
          @drop-lora="dropLoras"
        />
        </div>
        <p v-if="submitError" class="rund-f rund-f--4 rund-note rund-note--bad" role="alert">
          {{ submitError }}
        </p>
      </div>
    </div>

    <template #footer>
      <!-- Status, never an alert: it says a gesture is already done, and the
           `--bad` spelling beside a real run refusal would read as a second
           thing wrong. -->
      <p v-if="keptAs" :id="savedReasonId" class="rund-note rund-kept">
        Already kept as “{{ keptAs.name || "Untitled" }}”.
      </p>
      <p v-if="runBlocker" :id="blockerId" class="rund-note rund-note--bad">
        {{ runBlocker }}
      </p>
      <!-- `aria-disabled`, not `disabled`, for this dialog's own stated
           reason: a natively-disabled button is out of the tab order, so a
           keyboard reader could never reach the sentence saying why it is
           inert. `onSave` does the refusing. -->
      <AppButton
        :icon-left="keptAs ? 'check' : 'bookmark-plus-outline'"
        :disabled="!activeKey || submitting"
        :aria-disabled="keptAs ? 'true' : undefined"
        :aria-describedby="keptAs ? savedReasonId : undefined"
        @click="onSave"
      >
        {{ keptAs ? "Saved" : "Save as recipe" }}
      </AppButton>
      <span class="rund-sp" />
      <AppButton :disabled="submitting" @click="onRequestClose">Cancel</AppButton>
      <!-- `aria-disabled`, not `disabled`: a natively-disabled button is out of
           the tab order, so a keyboard reader could never reach the reason
           `aria-describedby` points at. `submit()` does the actual refusing.
           Same shape as the Recipe tab's own Run button. -->
      <AppButton
        variant="primary"
        icon-left="play"
        :loading="submitting"
        :class="{ 'run-refused': !canRun }"
        :aria-disabled="canRun ? undefined : 'true'"
        :aria-describedby="runBlocker ? blockerId : undefined"
        @click="submit"
      >
        {{ runLabel }}
      </AppButton>
    </template>
  </AppDialog>

  <!-- Its own dialog rather than a mode in this footer: "what the recipe
       keeps" is a list the owner reads and argues with, and a run form is
       already the densest surface in the app. -->
  <SaveRecipeDialog
    v-if="saveOpen"
    :open="saveOpen"
    :workflow-key="activeKey"
    :suggested-name="card?.name || ''"
    :prompt="prompt"
    :negative="negative"
    :loras="recipeLoras"
    :overrides="recipeOverrides"
    :seed="seedMode === 'fixed' ? String(seed).trim() : ''"
    :settings-aside="seedMode === 'keep' ? KEEP_SEED_ASIDE : ''"
    :source-picture-id="pictureIds[0] ?? null"
    @close="saveOpen = false"
    @saved="onSaved"
  />

  <!-- Mounted while a slot is being filled and not before: the picker reads
       the library's facets when it opens, which a run that never touches a
       picture should not pay for. -->
  <PicturePicker
    v-if="pickerFor !== null"
    :open="pickerFor !== null"
    :subtitle="pickerFor ? `for ${inputTitle(pickerFor)}` : ''"
    @close="pickerFor = null"
    @pick="onPicked"
  />
</template>

<script setup>
/**
 * The Run popup (v1.12 F5) - one dialog for every source.
 *
 * A picture's recipe, a whole selection, or a workflow card off the Workflows
 * view all open this: the left column says what the run is made from and where
 * its output goes, the right is the card's own parameters, prefilled and
 * editable. Nothing here is written back into a workflow - every edit is an
 * override `POST /workflows/run` applies to the graph at submission time, which
 * is what keeps the card's content-addressed identity intact (B7).
 *
 * **An edited field is marked in its label row**, never beside the control: the
 * four-column grid stays aligned only if a chip cannot change a cell's height.
 *
 * **Save as recipe reads Saved once a saved recipe already keeps this look**
 * (#1480), as the lightbox's Recipe tab has since F6 - without it the second
 * identical row was one press away from here.
 */
import { computed, nextTick, reactive, ref, useId, watch } from "vue";
import { VIcon } from "vuetify/components";

import { getPictureRecipe } from "../../api/comfyui";
import { listAdapters } from "../../api/modelShelf";
import { pictureThumbnailUrl } from "../../api/pictures";
import { listSavedRecipes } from "../../api/recipes";
import {
  getWorkflowCard,
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
  setWorkflowInputs,
  workflowCoverUrl,
} from "../../api/workflows";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { errorMessage } from "../../utils/apiError";
import { wouldDuplicate } from "../../utils/recipeKey";
import {
  PICTURE_INPUT_UNFILLED,
  bypassNotice,
  reasonsBlock,
} from "../../utils/runReasons";
import SaveRecipeDialog from "./SaveRecipeDialog.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import PicturePicker from "../widgets/PicturePicker.vue";
import RunReasonNotice from "./RunReasonNotice.vue";
import RunResetChip from "./RunResetChip.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /**
   * `{kind, pictureIds, workflowKey, pickWorkflow, name, coverUrl}` - see
   * `useRunDialogStore`. Replaced rather than mutated, so a new source is one
   * watcher tick and never a half-swapped form.
   */
  source: { type: Object, default: null },
  /** `{client_id, set_id, project_id, character_id}` from the grid. */
  context: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["close", "run", "open-settings"]);

/**
 * `MAX_RUNS_PER_REQUEST` (`pixlstash/routes/comfyui.py`), which `_plan`
 * applies to the TOTAL across every group. One `target` is one group, so here
 * the total is the count.
 */
const MAX_COUNT = 200;
/** `MAX_SEED_64` in `pixlstash/routes/workflows.py`: ComfyUI's own ceiling. */
const MAX_SEED = 2n ** 64n - 1n;
/** `MAX_DEFAULTS` in `pixlstash/routes/workflows.py`: the `values` ceiling. */
const MAX_VALUES = 200;
/** The parameters the design pins, in its order, addressed by widget name. */
const SCALAR_PINNED = ["steps", "cfg", "cfg_scale", "guidance"];
const SIZE_INPUTS = ["width", "height"];
const CHECKPOINT_INPUT = "ckpt_name";
/**
 * Said in the Save dialog when the form is set to keep the source's seed.
 *
 * A saved recipe's seed is one number it holds, and "the seed of whatever
 * picture this run started from" is not a number - `seed_mode` is the run's
 * own choice and no column keeps it. So that choice is dropped on save, and
 * dropping it in silence is what this line stops.
 */
const KEEP_SEED_ASIDE =
  "This run is set to keep the source picture's seed. A recipe cannot hold that choice, so it is not kept: runs from this recipe draw a new seed.";

const SEPARATOR = "/";

const blockerId = useId();
const picsLabelId = useId();
const stackId = useId();
/** Bumped per open, so a slower earlier read cannot write over a later one. */
let loadToken = 0;
const runDialog = useRunDialogStore();
const entityLists = useEntityListsStore();

const loading = ref(false);
const loadFailed = ref("");
const submitting = ref(false);
const submitError = ref("");
const preflighting = ref(false);
const preflightError = ref("");
/** What the server said this body would submit, at the count it was asked at. */
const plannedRuns = ref(0);

const card = ref(null);
const recipe = ref(null);
const cards = ref([]);
const adapters = ref([]);
const reasons = ref([]);
/**
 * What this run WILL do differently, which is not a reason it would not run.
 *
 * Kept apart from `reasons` because `reasonsBlock` reads that list: a bypassed
 * LoRA drawn as a refusal would block the very run the server is willing to
 * make. They are concatenated for display and nowhere else.
 */
const bypassed = ref([]);
const activeKey = ref("");
/** address -> the label it had on the card it was edited on. */
const editedLabels = reactive({});
/** address -> the owner's value, over the card's own default. */
const edits = reactive({});
const fellBack = ref([]);

const prompt = ref("");
const negative = ref("");
const count = ref(1);
const seedMode = ref("new");
/**
 * The chosen seed, as TEXT and never as a number.
 *
 * ComfyUI draws seeds up to 2**64-1 and a JavaScript `Number` loses digits
 * above 2**53, so `Number("18446744073709551615")` is a different seed and
 * `JSON.stringify` would send that different seed. The digits are carried
 * through untouched; Pydantic parses the string into a Python int exactly.
 * The same reason `GET /comfyui/pictures/{id}/recipe` serves `seed_text`
 * beside `seed` at all.
 */
const seed = ref("0");
const loras = ref([]);
const destinationSetId = ref("");

// ── Picture inputs (#1457) ───────────────────────────────────────────────
/**
 * Every picture input of the card, as the last pre-flight answered it
 * (`RunGroup.picture_inputs`): the WHOLE set, which is what makes the
 * whole-set `PUT /workflows/{key}/inputs` safe to call from here. A pin or a
 * moved selection is written through that route from this list and nothing
 * else, so a row the popup has not read is never deleted.
 */
const pictureInputs = ref([]);
/** `address -> {slot_label, input_name, picture_id}` picked for this run only. */
const picks = reactive({});
/** The input the picture picker is open for, or `null`. */
const pickerFor = ref(null);
/**
 * A setup gesture in flight, INCLUDING the pre-flight after its write: the
 * next whole-set PUT is built from `pictureInputs`, and until that pre-flight
 * lands the list still says what the card was before the write. Released
 * early, a second pin sent the first one back as a picker.
 */
const inputsBusy = ref(false);
/**
 * The card `pictureInputs` was read for. A write goes to `activeKey`, and a
 * stack-member switch moves that at once while the new card's inputs are still
 * being read - so a pin then would PUT one card's rows under another's key and
 * replace its setup with rows that match nothing.
 */
const inputsKey = ref("");
const inputsError = ref("");
/**
 * The stack checkbox, `null` until the owner touches it.
 *
 * Untouched it follows the server: ticked exactly when a selected picture is
 * fed into an input (decision 4). Deliberately NOT `useGenStackPrefsStore`:
 * its old `stackI2IOutputs` key is still on disks from before #1452, defaulted
 * on, and would silently re-read a choice made about a different feature.
 */
const stackChoice = ref(null);

/**
 * Whether the form holds work a stray click would destroy.
 *
 * `AppDialog` dismisses on a backdrop click and on Escape, and `load()` rebuilds
 * every field on the next open, so one misplaced click discards an edited
 * prompt, its LoRAs and every override with no undo. `persistent` is the
 * mechanism the dialog already has for exactly this.
 *
 * It also closes the Enter path: the keyboard contract suppresses `accept` on a
 * persistent dialog, "so a destructive or in-flight accept only fires from its
 * own button" - and this button queues up to 200 ComfyUI runs. `@accept` is not
 * wired at all here for the same reason; Escape is handled below so dismissing
 * an untouched form still works.
 */
/** The LoRA rows the owner changed, as `RunLora` takes them. */
const changedLoras = computed(() =>
  loras.value
    .filter(
      (row) =>
        row.sha256 &&
        (row.sha256 !== row.baseSha || row.strength !== row.baseStrength),
    )
    .map((row) => ({
      node_id: row.node_id,
      field: row.field,
      sha256: row.sha256,
      strength_model: Number.isFinite(row.strength) ? row.strength : null,
    })),
);

const dirty = computed(
  () =>
    prompt.value !== basePrompt.value ||
    negative.value !== baseNegative.value ||
    Object.keys(edits).length > 0 ||
    Object.keys(picks).length > 0 ||
    loras.value.length !== initialLoraCount.value ||
    changedLoras.value.length > 0,
);

/** How many LoRA rows the form opened with, for `dirty`. */
const initialLoraCount = ref(0);

/** Whether the Save as recipe dialog is up over this one. */
const saveOpen = ref(false);

const LAST_SET_KEY = "pixlstash:runDialogSetId";

/**
 * The card being run, and switching to another.
 *
 * A writable computed rather than a `watch` on the ref: the load path assigns
 * the key itself, and a watcher cannot tell that assignment from the owner
 * choosing the first workflow in "Run a workflow on these…", where the previous
 * value is the empty string either way. Here the two are different call sites.
 */
const workflowKey = computed({
  get: () => activeKey.value,
  set: (key) => {
    if (!key || key === activeKey.value) return;
    const hadCard = Boolean(card.value);
    activeKey.value = key;
    void switchCard(key, hadCard);
  },
});

async function switchCard(key, keepEdits) {
  // A pick is addressed by a slot of the card it was made on; another card's
  // slots are other slots, so nothing carries over - and neither do the rows,
  // which are the set a pin would be written from.
  clearPicks();
  pictureInputs.value = [];
  inputsKey.value = "";
  // The same generation the load uses. A card read still in flight when the
  // popup is closed and reopened on another source would otherwise resolve
  // into the new dialog and replace its card, and the pre-flight behind it
  // would replace the new dialog's refusals with the old gesture's.
  const token = loadToken;
  try {
    await loadCard(key, { keepEdits });
    if (token !== loadToken) return;
    await runPreflight(token);
  } catch (err) {
    if (token === loadToken) {
      loadFailed.value = errorMessage(err, "Could not read that workflow.");
    }
  }
}

const defaults = computed(() => card.value?.defaults || []);
const hasRecipe = computed(() => Boolean(recipe.value));
const loraSlots = computed(() => recipe.value?.lora_slots || []);
const pictureIds = computed(() => props.source?.pictureIds || []);
const kind = computed(() => props.source?.kind || "picture");
/**
 * The saved recipe this popup was opened on, when it was opened from one.
 *
 * It is a SOURCE, not a prefill: `POST /workflows/run` takes
 * `saved_recipe_id` and fills the row's prompt, LoRAs, overrides and seed in
 * underneath whatever this form sends, so the form shows the recipe and the
 * owner's edits win over it. The form is prefilled all the same, because
 * `runBody` sends every parameter it displays and a row the form did not show
 * would otherwise be overwritten by the card's own default.
 */
const savedRecipe = computed(() => props.source?.savedRecipe || null);
/** A card with no source picture picks where its output is filed. */
const picksDestination = computed(() => !pictureIds.value.length);

const title = computed(() =>
  kind.value === "card" ? "Run workflow" : "Run recipe",
);
const subtitle = computed(() => card.value?.name || "");
const sourceName = computed(
  () => props.source?.name || card.value?.name || "This run",
);
/**
 * The picture beside the form, as a browser can actually load it.
 *
 * A card's `covers` carry an API-RELATIVE `url` (`_covers`, `workflows.py`) and
 * an `<img src>` bypasses Axios, so nothing prepends `/api/v1` and nothing
 * appends the share token: used verbatim the browser asks the page origin for
 * a path no route serves and the cover is broken. `workflowCoverUrl` is the
 * one spelling of that fix (F1b hit it on the grid first).
 */
const coverUrl = computed(() => {
  if (props.source?.coverUrl) return props.source.coverUrl;
  const cover = card.value?.covers?.[0];
  return cover ? workflowCoverUrl(cover) : "";
});
const seedText = computed(() => recipe.value?.seed_text || "");

const sourceKindLine = computed(() => {
  const many = pictureIds.value.length;
  if (kind.value === "card") return "Workflow, with no picture behind it";
  if (many > 1) return `${many} pictures, all on this workflow`;
  return "This picture's recipe";
});

function address(field) {
  return `${field.slot_label}${SEPARATOR}${field.input_name}`;
}

/**
 * The value a field started at: the card's own default, and the picture's own
 * setting over it where the picture is what opened this. That is what the ↺
 * chip carries and what it puts back.
 */
function baseOf(field) {
  // Only while the card being run is the one that MADE the picture. A stack
  // member is a different graph, so the picture's own steps say nothing about
  // it, and showing them would make its defaults look like edits.
  const ownCard = recipe.value?.workflow_key
    ? recipe.value.workflow_key === activeKey.value
    : false;
  // And only when the name picks out ONE parameter. `recipe.settings` is
  // `{field: value}` with no slot, so a graph with two samplers both carrying
  // `steps` would otherwise show the same picture value in both rows and send
  // it to both.
  const fromPicture =
    ownCard && !ambiguousInputs.value.has(field.input_name)
      ? recipe.value?.settings?.[field.input_name]
      : undefined;
  return fromPicture !== undefined && fromPicture !== null
    ? fromPicture
    : field.value;
}

function currentValue(field) {
  const key = address(field);
  return key in edits ? edits[key] : baseOf(field);
}

function isEdited(field) {
  return address(field) in edits;
}

/**
 * One typed value, in the type the field started as.
 *
 * `undefined` is "this is not a value": an emptied number box reads as `""`,
 * and `Number("")` is 0, so a field the owner merely cleared would be recorded
 * as a deliberate zero and submitted as one. Anything unparseable is the same
 * answer, since `JSON.stringify(NaN)` is `null` and `RunValue` refuses it.
 */
function coerce(field, raw) {
  if (typeof baseOf(field) !== "number") return raw;
  const text = String(raw ?? "").trim();
  if (!text) return undefined;
  const value = Number(text);
  return Number.isFinite(value) ? value : undefined;
}

function setValue(field, value) {
  const key = address(field);
  // Not a value: the field goes back to what it started at rather than
  // recording a zero nobody typed.
  if (value === undefined || value === baseOf(field)) {
    delete edits[key];
    delete editedLabels[key];
    return;
  }
  edits[key] = value;
  editedLabels[key] = field.label;
}

function resetValue(field) {
  const key = address(field);
  delete edits[key];
  delete editedLabels[key];
}

/** Input names more than one of this card's parameters carries. */
const ambiguousInputs = computed(() => {
  const seen = new Set();
  const twice = new Set();
  for (const field of defaults.value) {
    if (seen.has(field.input_name)) twice.add(field.input_name);
    seen.add(field.input_name);
  }
  return twice;
});

const byInput = computed(() => {
  const map = {};
  for (const field of defaults.value) map[field.input_name] = field;
  return map;
});

const scalarFields = computed(() =>
  SCALAR_PINNED.map((name) => byInput.value[name]).filter(Boolean),
);
/**
 * Width and height, drawn as one "Size" cell — and only when BOTH are there.
 *
 * A card carrying one of them is drawn as an ordinary parameter instead. Half
 * a Size cell would be a control that lies about what it sets, and pinning the
 * half that exists without rendering it would hide the field from the form
 * entirely: it would be neither a pinned row nor one of "All N parameters".
 */
const sizeFields = computed(() => {
  const both = SIZE_INPUTS.map((name) => byInput.value[name]);
  return both.every(Boolean) ? both : [];
});
const checkpointField = computed(() => byInput.value[CHECKPOINT_INPUT] || null);
const pinnedAddresses = computed(
  () =>
    new Set(
      [...scalarFields.value, ...sizeFields.value, checkpointField.value]
        .filter(Boolean)
        .map(address),
    ),
);
/** Everything the pinned rows did not show, behind "All N parameters". */
const restFields = computed(() =>
  defaults.value.filter((field) => !pinnedAddresses.value.has(address(field))),
);

const sizeEdited = computed(() => sizeFields.value.some(isEdited));
function resetSize() {
  sizeFields.value.forEach(resetValue);
}

const basePrompt = computed(
  () => recipe.value?.positive_prompt || savedRecipe.value?.prompt || "",
);

/**
 * What to send as `prompt`, or `null` to leave the graph's own alone.
 *
 * An empty box is only an instruction to blank the prompt when there WAS one
 * on screen to blank. Without a recipe behind it the box starts empty because
 * nothing filled it, which is not the same thing.
 */
const promptOverride = computed(() => {
  const typed = prompt.value;
  if (typed) return typed;
  return basePrompt.value ? "" : null;
});
const baseNegative = computed(
  () => recipe.value?.negative_prompt || savedRecipe.value?.negative || "",
);

/**
 * The LoRAs as a recipe keeps them: by name and strength, not by slot.
 *
 * Only the rows the shelf could name. A saved LoRA is found again by its
 * digest after a rename, and a slot with no digest has nothing to save that
 * would still mean anything on another machine.
 */
const recipeLoras = computed(() =>
  loras.value
    .filter((row) => row.sha256)
    .map((row) => {
      const shelf = adapters.value.find((item) => item.sha256 === row.sha256);
      return {
        filename:
          shelf?.filename || shelf?.display_name || row.graphValue || row.sha256,
        sha256: row.sha256,
        strength: Number(row.strength) || 1,
      };
    }),
);

/**
 * The parameters the owner changed, each with the value it was changed from.
 *
 * The Save as recipe dialog prints "Steps 12 instead of the workflow's 8", so
 * it needs both numbers; `edits` alone carries only the new one.
 */
const recipeOverrides = computed(() =>
  defaults.value.filter(isEdited).map((field) => ({
    address: address(field),
    label: field.label,
    value: edits[address(field)],
    base: baseOf(field),
  })),
);

// ── Is this look already kept? (#1480) ───────────────────────────
//
// The lightbox's Recipe tab has answered this since F6 and this popup did not,
// so the duplicate the tab refuses was one press away from here. The same
// `keepsTheSameLook` as the tab, off the same read, because two copies of that
// comparison is how it went wrong the first time (`utils/recipeKey.js`).

/** The saved recipes of `activeKey`'s stack, and the key they were read for. */
const savedRecipes = ref([]);
const savedForKey = ref("");
const savedReasonId = useId();

/**
 * Exactly what pressing Save would write, which is what the match asks about.
 *
 * `recipeLoras`, not the graph's slots, because the save writes the digested
 * rows and no others - and **the negative prompt and the overrides too**,
 * because this is a form and the save writes those as well. Keying on the
 * look alone (prompt and LoRA names, which is what credit is grouped by)
 * would make **Saved** refuse a recipe that differs from the kept one in
 * every parameter it carries: not a duplicate, a variation, and unsaveable.
 * `wouldDuplicate` is that question, beside the look's own key in
 * `utils/recipeKey.js` so the comparisons stay in one file.
 */
const thisSave = computed(() => ({
  prompt: prompt.value,
  negative: negative.value,
  loras: recipeLoras.value,
  overrides: Object.fromEntries(
    recipeOverrides.value.map((row) => [row.address, row.value]),
  ),
}));

/**
 * The saved recipe a save from this form would duplicate, or null.
 *
 * It comes and goes as the owner types, which is the point: edit the prompt,
 * a LoRA strength or a parameter and this is a new recipe again, so Save
 * comes back.
 */
const keptAs = computed(() => {
  if (!activeKey.value || activeKey.value !== savedForKey.value) return null;
  return (
    savedRecipes.value.find((row) => wouldDuplicate(row, thisSave.value)) ||
    null
  );
});

watch(
  () => activeKey.value,
  async (key) => {
    savedRecipes.value = [];
    savedForKey.value = "";
    if (!key) return;
    try {
      const rows = await listSavedRecipes(key);
      // The picker may have moved on while the read was out.
      if (key !== activeKey.value) return;
      savedRecipes.value = rows;
      savedForKey.value = key;
    } catch (err) {
      // A footer that cannot say "Saved" is not a failure of the run form:
      // the popup's whole job is still on screen, so this is logged and
      // dropped, and the save goes on being offered.
      console.warn("Could not read this workflow's saved recipes:", err);
    }
  },
  { immediate: true },
);

/** The gesture, refused here rather than by a `disabled` nobody can reach. */
function onSave() {
  if (keptAs.value || !activeKey.value || submitting.value) return;
  saveOpen.value = true;
}

/** Straight into the list, so the footer answers without another read. */
function onSaved(row) {
  if (!row) return;
  savedRecipes.value = savedRecipes.value.some((known) => known.id === row.id)
    ? savedRecipes.value.map((known) => (known.id === row.id ? row : known))
    : [...savedRecipes.value, row];
}

const seedOptions = [
  { value: "new", label: "New for each picture" },
  { value: "keep", label: "Keep the original's" },
  { value: "fixed", label: "A seed I choose" },
];

const workflowOptions = computed(() => {
  // A stack comes whole, in its own order, from the card: members often share
  // a generated name, so each says what sets it apart from the others.
  const rows = numberAlike(
    (card.value?.members || []).map((member) => ({
      value: member.key,
      label: memberLabel(member),
    })),
  );
  if (card.value && !rows.length) {
    rows.push({ value: card.value.key, label: card.value.name });
  }
  // "Run a workflow on these…" opens with the whole library in the picker;
  // otherwise only the stack's own members, which is the switch the design
  // describes.
  for (const row of props.source?.pickWorkflow ? cards.value : []) {
    if (!rows.some((option) => option.value === row.key)) {
      rows.push({ value: row.key, label: row.name });
    }
  }
  return rows;
});

// The chips that only say a model differs, which `sets_apart` already names.
const MODEL_CHIPS = new Set(["other checkpoint", "other models"]);

/** "Name — what sets it apart": the models only it loads, then its chips. */
function memberLabel(member) {
  const models = member.sets_apart || [];
  const chips = (member.differs_by || []).filter(
    (chip) => !models.length || !MODEL_CHIPS.has(chip),
  );
  const apart = [...models, ...chips];
  return apart.length ? `${member.name} — ${apart.join(", ")}` : member.name;
}

/** Rows that still read alike are numbered, as the Pictures rows are. */
function numberAlike(rows) {
  const total = {};
  for (const row of rows) total[row.label] = (total[row.label] || 0) + 1;
  const seen = {};
  return rows.map((row) => {
    if (total[row.label] < 2) return row;
    seen[row.label] = (seen[row.label] || 0) + 1;
    return { ...row, label: `${row.label} (${seen[row.label]})` };
  });
}

const adapterOptions = computed(() =>
  adapters.value
    .filter((adapter) => adapter?.sha256)
    .map((adapter) => ({
      value: adapter.sha256,
      label: adapter.display_name || adapter.filename || adapter.sha256.slice(0, 12),
    }))
    .sort((a, b) => a.label.localeCompare(b.label)),
);

const setOptions = computed(() => [
  { value: "", label: "No set" },
  ...entityLists.pictureSets.map((row) => ({
    value: String(row.id),
    label: row.name,
  })),
]);

/**
 * Where the output is filed, said as it will actually happen.
 *
 * The run carries the grid's own view context, so with a set in view the new
 * pictures land in it. With none there is nothing to be next to: they are
 * imported into the library unfiled, which is worth saying rather than
 * promising they will appear beside their source.
 */
const destinationLine = computed(() => {
  const setId = props.context?.set_id;
  const row = entityLists.pictureSets.find(
    (item) => String(item.id) === String(setId),
  );
  if (row) return `${row.name}, beside the pictures they came from`;
  return "Your library, in no set";
});

const fellBackLine = computed(
  () =>
    `${fellBack.value.join(", ")} went back to this workflow's own ${
      fellBack.value.length === 1 ? "value" : "values"
    }.`,
);

/** A seed is a whole number, up to ComfyUI's 2**64-1, checked as digits. */
const seedError = computed(() => {
  if (seedMode.value !== "fixed") return "";
  const text = String(seed.value ?? "").trim();
  if (!/^\d+$/.test(text)) return "A seed is a whole number.";
  return BigInt(text) > MAX_SEED ? "That is larger than ComfyUI's biggest seed." : "";
});

const runBlocker = computed(() => {
  if (!activeKey.value) return "Choose a workflow first.";
  if (seedError.value) return seedError.value;
  if (!Number.isInteger(count.value) || count.value < 1 || count.value > MAX_COUNT)
    return `Between 1 and ${MAX_COUNT} runs at a time.`;
  if (preflightError.value) return preflightError.value;
  // Before the generic refusal: an empty slot is "no picture yet", said in
  // the words of the slot, and its fix is right there in the Pictures rows.
  if (unfilledInputs.value.length) {
    const titles = unfilledInputs.value.map(inputTitle).join(", ");
    return `Choose a picture for ${titles} first.`;
  }
  if (reasonsBlock(reasons.value)) return "This run cannot start; see below.";
  return "";
});
const canRun = computed(() => !runBlocker.value && !submitting.value);
/**
 * The refusals drawn as notices. `picture_input_unfilled` is not one of them:
 * the Pictures section draws that as an empty slot, and a second, louder copy
 * of it under the form would make a pin whose picture has gone read as an
 * error when it is only a choice not made yet (decision 7).
 */
const shownRefusals = computed(() =>
  reasons.value.filter((reason) => reason?.code !== PICTURE_INPUT_UNFILLED),
);
/** One list on screen: the refusals first, then what the run will do anyway. */
const runNotes = computed(() => [...shownRefusals.value, ...bypassed.value]);

/** The inputs nothing fills, which is what keeps the Run button back. */
const unfilledInputs = computed(() =>
  pictureInputs.value.filter((input) => input.fill === null),
);
/** Whether a selected picture is fed into the graph, per the server. */
const feedsSelection = computed(() =>
  pictureInputs.value.some((input) => input.fill === "selection"),
);
/** Whether "stack with the ones they came from" would be true of this run. */
const stackOffered = computed(
  () =>
    pictureIds.value.length === 1 ||
    (pictureIds.value.length > 1 && feedsSelection.value),
);
/**
 * Whether a setup gesture may start: nothing in flight, and the rows on screen
 * read for the card a write would go to.
 */
const setupReady = computed(
  () =>
    !submitting.value &&
    !inputsBusy.value &&
    !preflighting.value &&
    Boolean(activeKey.value) &&
    inputsKey.value === activeKey.value,
);
const stack = computed({
  get: () => stackChoice.value ?? feedsSelection.value,
  set: (value) => {
    stackChoice.value = Boolean(value);
  },
});

function thumbUrl(id) {
  return pictureThumbnailUrl(id);
}

/**
 * What a row is called. A loader with no title of its own is called by its
 * class, and two of those side by side would read as one input twice, so a
 * repeated name is numbered in the order the card lists them.
 */
function inputTitle(input) {
  const title = input?.title || "Picture";
  const same = pictureInputs.value.filter(
    (row) => (row.title || "Picture") === title,
  );
  if (same.length < 2) return title;
  const position = same.findIndex((row) => address(row) === address(input));
  return `${title} ${position + 1}`;
}

/** The line under a row's title: how this run fills it, in words. */
function inputLine(input) {
  const many = pictureIds.value.length;
  switch (input.fill) {
    case "selection":
      return many === 1
        ? "The picture this run starts from"
        : `Your selection: ${many} pictures, one run each`;
    case "request":
      return "Chosen for this run";
    case "fixed":
      return "Kept for every run of this workflow";
    case "graph":
      return "As the workflow has it";
    default:
      return input.picture_missing
        ? "The picture you kept here is gone. Choose another."
        : "No picture yet";
  }
}

function openPicker(input) {
  if (!setupReady.value) return;
  pickerFor.value = input;
}

/**
 * Put focus back on a row after a gesture that removed the control holding it
 * (the clear, the move), so a keyboard user is not dropped to the page.
 */
async function focusRow(key) {
  await nextTick();
  const tile = document.querySelector(`[data-input="${key}"]`);
  if (tile) tile.focus();
}

function clearPicks() {
  for (const key of Object.keys(picks)) delete picks[key];
}

/** The picker answered: this picture fills that slot, for this run. */
async function onPicked(picture) {
  const input = pickerFor.value;
  pickerFor.value = null;
  if (!input || !picture?.id) return;
  picks[address(input)] = {
    slot_label: input.slot_label,
    input_name: input.input_name,
    picture_id: picture.id,
  };
  await settle(runPreflight());
}

async function clearPick(input) {
  if (!setupReady.value) return;
  const key = address(input);
  delete picks[key];
  await settle(runPreflight());
  await focusRow(key);
}

/** Hold the setup controls until *step* - a write and its re-read - is done. */
async function settle(step) {
  inputsBusy.value = true;
  try {
    return await step;
  } finally {
    inputsBusy.value = false;
  }
}

/**
 * The card's whole setup as it stands, with one row changed.
 *
 * Every other row goes back exactly as it was read, pins by the content they
 * were stored with, so a write about one slot can never unpin another. The
 * one exception is a second Selection: a card has at most one, so moving it
 * here makes the old one a picker.
 */
function setupWith(input, change) {
  return pictureInputs.value.map((row) => {
    const entry = {
      slot_label: row.slot_label,
      input_name: row.input_name,
      mode: row.mode,
      pixel_sha: row.pixel_sha || null,
    };
    if (address(row) === address(input)) return { ...entry, ...change };
    if (change.mode === "selection" && row.mode === "selection") {
      return { ...entry, mode: "picker" };
    }
    return entry;
  });
}

async function writeSetup(entries) {
  inputsError.value = "";
  // Asked again here and not only by the controls: this is the one place a
  // whole-set write is sent, so it is the one place the guard cannot be
  // skipped by a caller that forgot to check.
  if (inputsKey.value !== activeKey.value) {
    inputsError.value = "Still reading this workflow's pictures; try again.";
    return false;
  }
  try {
    await setWorkflowInputs(activeKey.value, entries);
    return true;
  } catch (err) {
    inputsError.value = errorMessage(err, "Could not keep that for this workflow.");
    return false;
  }
}

/**
 * Pin or unpin the picture a row shows.
 *
 * Pinning writes it as the card's `fixed` input, by id - the server stores its
 * content. Unpinning keeps the picture for THIS run, so taking the pin off
 * never empties a slot the owner was about to run with.
 */
async function togglePin(input) {
  if (!setupReady.value) return;
  const key = address(input);
  const pinning = input.fill !== "fixed";
  await settle(
    (async () => {
      const ok = await writeSetup(
        setupWith(
          input,
          pinning
            ? { mode: "fixed", picture_id: input.picture_id, pixel_sha: null }
            : { mode: "picker" },
        ),
      );
      if (!ok) return;
      if (pinning) {
        delete picks[key];
      } else {
        picks[key] = {
          slot_label: input.slot_label,
          input_name: input.input_name,
          picture_id: input.picture_id,
        };
      }
      await runPreflight();
    })(),
  );
}

/**
 * Send the selection to this input, and remember that on the card.
 *
 * Remembered rather than per run (the plan's call on its open question 12):
 * it only arises on a card with two or more inputs and nothing pinned, and
 * asking again on every run of such a card is the cost that would be paid.
 */
async function useSelectionHere(input) {
  if (!setupReady.value) return;
  const key = address(input);
  await settle(
    (async () => {
      const ok = await writeSetup(setupWith(input, { mode: "selection" }));
      if (!ok) return;
      delete picks[key];
      await runPreflight();
    })(),
  );
  await focusRow(key);
}
/**
 * The button's number, from the server where it has answered.
 *
 * `RunPreflight.runs` is what `_plan` would actually submit for this body;
 * `count` is only what this form asked for. They agree on the one-group `target`
 * path, and the server is the one to believe when they do not.
 */
const runLabel = computed(() => {
  if (!Number.isInteger(count.value) || count.value < 1) return "Run";
  const planned = plannedRuns.value || count.value;
  return `Run ${planned}`;
});

/** The body both the pre-flight and the run take, so the two never disagree. */
function runBody() {
  // **Every parameter the form displays, not only the edited ones.**
  //
  // The run does NOT start from what this popup is showing: `_plan` builds the
  // graph from `resolve_source` - the imported file, the card's best-scored
  // picture, or the best stored instance - and then applies `body.values` and
  // nothing else. A card's `defaults` are a *display* figure (the mode over its
  // best pictures, `card_defaults`), and the opened picture's own settings are
  // a fact about that picture; neither reaches the graph on its own. Sending
  // only `edits` therefore ran a graph that disagreed with the form: open a
  // picture made at 45 steps on a card whose best picture used 20, press Run
  // untouched, and the form said 45 while the run did 20.
  //
  // Safe to send the lot: `_apply_addressed` leaves a wired input alone and
  // "an input the graph does not have is not invented", so an address this
  // graph lacks is inert rather than an error.
  const values = displayedValues();
  const body = {
    // `null` means "leave the graph's own text alone"; `""` means "blank it",
    // and `_apply_prompts` honours both literally. A card or a multi-picture
    // selection reads no recipe, so the box is empty because there was nothing
    // to prefill it with - sending that emptiness would wipe every positive
    // prompt node in the graph and generate from no prompt at all.
    prompt: promptOverride.value,
    negative: baseNegative.value ? negative.value : null,
    // Only the rows that DIFFER from the graph. An untouched slot needs no
    // override - ComfyUI loads what the graph already names - and a slot the
    // shelf could not name has no digest to send, which `RunLora.sha256`
    // requires. Sending those back would refuse the run over a LoRA nobody
    // touched.
    loras: changedLoras.value,
    values,
    // Only the pictures picked for this run: a pin, the stored selection and
    // the one open input a selection fills are all the server's to apply, so
    // an empty list is the ordinary body.
    inputs: Object.values(picks),
    stack: stack.value,
    count: count.value,
    seed_mode: seedMode.value,
    // The digits as they were typed. A Number here would round a 64-bit
    // seed into a different one on the way out.
    seed: seedMode.value === "fixed" ? String(seed.value).trim() : null,
    // Only when something is listening. `App.vue` mounts the grid under
    // `v-else`, so a run started from the Workflows view has no progress
    // runner attached, and a `client_id` naming a socket nobody reads makes
    // the run look followable when it is not.
    client_id: runDialog.hasRunner ? props.context?.client_id || null : null,
  };
  // Exactly one source, which the route checks before it reads anything: a
  // saved recipe names its own card, with pictures the card is `target`, and
  // without either it IS the source.
  if (savedRecipe.value) {
    body.saved_recipe_id = savedRecipe.value.id;
    // The picker still chooses which member of the stack runs: a recipe runs
    // on any workflow in the stack it was saved from, and `target` replaces
    // the group's card whatever named it.
    body.target = activeKey.value;
  } else if (pictureIds.value.length) {
    body.picture_ids = pictureIds.value;
    body.target = activeKey.value;
  } else {
    body.workflow_key = activeKey.value;
  }
  const setId = picksDestination.value ? destinationSetId.value : props.context?.set_id;
  const destination = {
    set_id: setId ? Number(setId) : null,
    project_id: picksDestination.value ? null : props.context?.project_id ?? null,
    character_id: picksDestination.value
      ? null
      : props.context?.character_id ?? null,
  };
  if (Object.values(destination).some((value) => value != null)) {
    body.destination = destination;
  }
  return body;
}

/**
 * Every parameter as the form currently shows it, addressed for the run.
 *
 * A `null` default is dropped rather than sent: `RunValue.value` is
 * `bool | int | float | str` and a null is a 422 on the whole request. A card
 * default with no value is a parameter nobody has a value for, so there is
 * nothing to override it with.
 */
function displayedValues() {
  const rows = [];
  for (const field of defaults.value) {
    const value = currentValue(field);
    if (value === null || value === undefined) continue;
    rows.push({
      slot_label: field.slot_label,
      input_name: field.input_name,
      value,
    });
  }
  return rows.slice(0, MAX_VALUES);
}

function addLora() {
  const used = new Set(loras.value.map((row) => `${row.field}@${row.node_id}`));
  const slot = loraSlots.value.find(
    (item) => !used.has(`${item.field}@${item.node_id}`),
  );
  if (!slot) return;
  loras.value.push(loraRow(slot, { added: true }));
}

/**
 * One LoRA row, with the graph's own file resolved to a shelf digest.
 *
 * `baseSha` / `baseStrength` are what the GRAPH has. A row equal to them is
 * not sent at all, which is how a slot the shelf cannot name still works: the
 * run carries no override for it and ComfyUI loads what the graph already
 * says. Only a row the owner actually changed becomes a `RunLora`.
 */
function loraRow(slot, { added = false } = {}) {
  const graphValue = String(slot.value ?? "");
  const sha256 =
    slot.by === "digest" ? graphValue : shelfDigestFor(graphValue);
  const strength = Number(slot.strengths?.model ?? 1);
  return {
    key: `${slot.field}@${slot.node_id}`,
    node_id: String(slot.node_id),
    field: String(slot.field),
    by: String(slot.by || "filename"),
    graphValue,
    added,
    baseSha: sha256,
    baseStrength: strength,
    sha256,
    strength,
  };
}

/**
 * The shelf digest for a filename a graph names, or `""`.
 *
 * The same two tiers `apply_adapter` matches on and in the same order - the
 * whole recorded name, then the basename - because ComfyUI counts from its
 * `loras` folder and the shelf from whatever folder it scanned. Matched on
 * exactly one row: two files of that name on the shelf is not a resolution,
 * it is a coin toss over which one the run would load.
 */
function shelfDigestFor(filename) {
  const wanted = filename.trim().toLowerCase();
  if (!wanted) return "";
  const base = wanted.split(/[\\/]/).pop();
  for (const key of [wanted, base]) {
    const hits = adapters.value.filter((adapter) => {
      const name = String(adapter.filename || "").toLowerCase();
      return adapter.sha256 && (name === key || name.split(/[\\/]/).pop() === key);
    });
    if (hits.length === 1) return String(hits[0].sha256);
  }
  return "";
}

/** The graph's LoRAs the shelf could not name, for the line under the rows. */
const unresolvedLoras = computed(() =>
  loras.value
    .filter((row) => !row.added && !row.baseSha && row.graphValue)
    .map((row) => row.graphValue),
);

/**
 * A row's options, with the graph's own unresolvable file among them.
 *
 * Without its current value in the list the select would render empty and read
 * as a slot nobody has filled, when in fact the graph fills it with a file the
 * shelf has never seen.
 */
function optionsFor(row) {
  if (row.baseSha || !row.graphValue || row.added) return adapterOptions.value;
  return [
    { value: "", label: `${row.graphValue} (not on your shelf)` },
    ...adapterOptions.value,
  ];
}

function removeLora(index) {
  loras.value.splice(index, 1);
}

/**
 * The `no_lora_loader` fix: take the LoRA out and ASK AGAIN.
 *
 * Without the second half the reason stays in `reasons`, `runBlocker` stays
 * set and the Run button stays disabled for ever - a fix button that makes the
 * refusal permanent. The server decides `wants_lora` from `body.loras`, so an
 * empty list genuinely clears it.
 */
async function dropLoras() {
  loras.value = [];
  await runPreflight();
}

async function loadAdapters() {
  if (adapters.value.length) return;
  try {
    adapters.value = await listAdapters();
  } catch (err) {
    // The picker degrades to the slots already in the graph; the run still
    // works, so this is a warning and not a blocker.
    console.warn("Could not read the model shelf's LoRAs:", err);
  }
}

/**
 * Load the card behind `key`, keeping every edit whose address it still has.
 *
 * The fields that do NOT survive are named rather than dropped in silence:
 * switching to a stack member is a deliberate comparison, and a steps value
 * that quietly went back to 8 is the thing the design asks to be told about.
 */
async function loadCard(key, { keepEdits = false } = {}) {
  const detail = await getWorkflowCard(key);
  const next = detail?.card || null;
  if (!keepEdits) {
    card.value = next;
    fellBack.value = [];
    return;
  }
  const addresses = new Set((next?.defaults || []).map(address));
  const lost = Object.keys(edits).filter((key2) => !addresses.has(key2));
  fellBack.value = lost.map((key2) => editedLabels[key2] || key2);
  for (const key2 of lost) {
    delete edits[key2];
    delete editedLabels[key2];
  }
  card.value = next;
}

async function runPreflight(token = loadToken) {
  const mine = () => token === loadToken;
  if (!activeKey.value) {
    reasons.value = [];
    bypassed.value = [];
    return;
  }
  preflighting.value = true;
  preflightError.value = "";
  const askedFor = activeKey.value;
  try {
    const answer = await preflightWorkflowRun(runBody());
    // The popup has moved to another card since this was asked: its answer is
    // about a card nobody is looking at, and its rows must not become the set
    // a pin on the new card is written from.
    if (!mine() || askedFor !== activeKey.value) return;
    reasons.value = (answer?.groups || []).flatMap((group) => group.reasons || []);
    bypassed.value = (answer?.groups || []).flatMap(bypassNotice);
    plannedRuns.value = Number(answer?.runs) || 0;
    // One group: this popup always runs one card (`target`, a key or a saved
    // recipe), so the first group's inputs are the card's.
    pictureInputs.value = answer?.groups?.[0]?.picture_inputs || [];
    inputsKey.value = askedFor;
  } catch (err) {
    // The route answers 400/404/422 here exactly as it does on the run, "so
    // the two never disagree" - so a 4xx is this body being refused and is
    // shown now rather than after the owner presses Run. Anything else (the
    // network, a 5xx) is the question not being asked, which is not a refusal:
    // the button stays live and the run itself answers.
    if (!mine()) return;
    reasons.value = [];
    bypassed.value = [];
    // What the card's inputs are is no longer known, so nothing may be
    // written from the last answer: that is how a pin reverted the one before.
    pictureInputs.value = [];
    inputsKey.value = "";
    const status = err?.response?.status;
    if (status >= 400 && status < 500) {
      preflightError.value = errorMessage(err, "This run would be refused.");
    } else {
      preflightError.value = "";
      console.warn("Could not pre-flight this run:", err);
    }
  } finally {
    if (mine()) preflighting.value = false;
  }
}

async function load() {
  const token = (loadToken += 1);
  const mine = () => token === loadToken;
  loading.value = true;
  loadFailed.value = "";
  submitError.value = "";
  reasons.value = [];
  fellBack.value = [];
  for (const key of Object.keys(edits)) delete edits[key];
  for (const key of Object.keys(editedLabels)) delete editedLabels[key];
  recipe.value = null;
  card.value = null;
  cards.value = [];
  loras.value = [];
  count.value = 1;
  seedMode.value = "new";
  saveOpen.value = false;
  pictureInputs.value = [];
  inputsKey.value = "";
  clearPicks();
  pickerFor.value = null;
  stackChoice.value = null;
  inputsError.value = "";
  try {
    if (props.source?.pickWorkflow) {
      cards.value = (await listWorkflowCards()).cards;
    }
    // One picture is a recipe to prefill from; several are a card the server
    // already agreed they share, so the card alone is the honest source.
    if (pictureIds.value.length === 1) {
      const data = await getPictureRecipe(pictureIds.value[0], { preflight: false });
      if (!mine()) return;
      recipe.value = data?.reason === "no_prompt_chunk" ? null : data;
    }
    const key = props.source?.workflowKey || recipe.value?.workflow_key || "";
    activeKey.value = key;
    if (key) await loadCard(key);
    if (!mine()) return;
    prompt.value = props.source?.emptyPrompt ? "" : basePrompt.value;
    negative.value = baseNegative.value;
    seed.value = seedText.value || "0";
    // EVERY slot the graph carries, not only the digest ones.
    //
    // `by: "digest"` is PixlStash's own loader node; every stock `LoraLoader`
    // names its file in a `lora_name` widget and is `by: "filename"`, so
    // filtering on digest showed no LoRAs at all on any ordinary workflow.
    // A filename is resolved against the shelf the way the backend's own
    // `apply_adapter` does it - exact, then basename - and a slot the shelf
    // cannot name is still SHOWN, because it is a fact about the graph; it
    // simply has no digest to send, which is exactly what leaving it untouched
    // means anyway.
    await loadAdapters();
    loras.value = loraSlots.value.map((slot) => loraRow(slot));
    initialLoraCount.value = loras.value.length;
    applySavedRecipe();
    destinationSetId.value =
      readLastSet() || (props.context?.set_id ? String(props.context.set_id) : "");
    void loadAdapters();
    // Both branches need the names: one to pick a set, the other to say which
    // one the output is going into.
    void entityLists.refresh("sets");
    await runPreflight();
  } catch (err) {
    if (mine()) {
      loadFailed.value = errorMessage(err, "Could not read what this would run.");
    }
  } finally {
    if (mine()) loading.value = false;
  }
}

/**
 * Show the saved recipe this popup was opened on.
 *
 * Each override is written into `edits` rather than merely displayed, because
 * `runBody` sends every parameter the form shows: a recipe value left out of
 * `edits` would be sent as the card's own default and the recipe's own value
 * would never reach the graph.
 *
 * LoRAs are deliberately NOT written here. A saved LoRA names a file and a
 * strength but no slot, and the slot only exists once the graph is resolved;
 * the route fills them in itself when the body carries none, which is what
 * this form sends when it was opened on a card with no picture behind it.
 *
 * **That is all-or-nothing on the route's side**: `body.loras` non-empty makes
 * the request's rows the whole LoRA set and the recipe's are not merged under
 * them. So editing one row of a saved recipe's LoRAs in this form replaces the
 * lot. It only arises once a card with no picture behind it shows LoRA rows at
 * all, which needs the card's own slots (`recipe.lora_slots` is the picture's).
 */
function applySavedRecipe() {
  const row = savedRecipe.value;
  if (!row) return;
  prompt.value = row.prompt || "";
  negative.value = row.negative || "";
  const byAddress = new Map(defaults.value.map((field) => [address(field), field]));
  for (const [key, value] of Object.entries(row.overrides || {})) {
    const field = byAddress.get(key);
    // An address this card does not carry is left alone rather than invented:
    // the recipe may have been saved on another member of the stack.
    if (!field) continue;
    setValue(field, value);
  }
  // A kept seed is shown as the chosen seed, so the form both says what the
  // recipe does and sends it: `POST /workflows/run` lets a request that names
  // `seed_mode` win over the row, and this form always names one.
  if (row.keep_seed && row.seed != null && row.seed !== "") {
    seedMode.value = "fixed";
    seed.value = String(row.seed);
  }
}

function readLastSet() {
  try {
    return window.localStorage?.getItem(LAST_SET_KEY) || "";
  } catch {
    return "";
  }
}

function rememberSet(value) {
  try {
    window.localStorage?.setItem(LAST_SET_KEY, value || "");
  } catch {
    // The preference simply will not persist this session.
  }
}

function onRequestClose() {
  if (submitting.value) return;
  emit("close");
}

/**
 * Escape closes this popup.
 *
 * A persistent dialog suppresses `AppDialog`'s own Escape, so the close is made
 * here. Saving a recipe is its own dialog now and answers its own Escape from
 * its own teleported subtree, so there is no nested mode in this footer left
 * to back out of first.
 */
function onEscape(event) {
  if (submitting.value) return;
  event.stopPropagation();
  emit("close");
}

async function submit() {
  if (!canRun.value) return;
  submitting.value = true;
  submitError.value = "";
  try {
    const answer = await runWorkflowCard(runBody());
    const prompts = Array.isArray(answer?.prompts) ? answer.prompts : [];
    if (!prompts.length) {
      reasons.value = (answer?.groups || []).flatMap((group) => group.reasons || []);
      // The run's own answer, so a slot emptied since the pre-flight (a pin
      // binned in another tab) shows as the empty slot it now is.
      if (answer?.groups?.[0]?.picture_inputs) {
        pictureInputs.value = answer.groups[0].picture_inputs;
        inputsKey.value = activeKey.value;
      }
      submitError.value = shownRefusals.value.length
        ? "Nothing was queued; see the reason below."
        : "Nothing was queued: a picture above still needs choosing.";
      return;
    }
    if (picksDestination.value) rememberSet(destinationSetId.value);
    emit("run", { prompts, pictureIds: pictureIds.value });
    emit("close");
  } catch (err) {
    // A submission error is a FORM error: keep the dialog and every input.
    submitError.value = errorMessage(err, "Could not start the run.");
  } finally {
    submitting.value = false;
  }
}

watch(
  () => [props.open, props.source],
  ([open]) => {
    if (open) void load();
  },
  { immediate: true },
);
</script>

<style scoped>
/* `aria-disabled`, so the button keeps focus and its reason stays reachable -
   but it must not look pressable, or the dead click is a surprise. The same
   dimming the native disabled state uses. */
:deep(.run-refused) {
  opacity: var(--opacity-disabled);
}

.rund {
  display: grid;
  /* 168px is the design's own source column, and the picture below fills it at
     168×252. Nothing else in the app draws a 2:3 thumbnail at a fixed size, so
     it stays here rather than becoming a token with one reader. */
  grid-template-columns: 168px minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
}

.rund-src {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.rund-pic {
  width: 168px;
  height: 252px;
  border-radius: var(--radius-md);
  object-fit: cover;
  display: block;
}

.rund-pic--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.rund-meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.rund-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.rund-sub,
.rund-kv dt {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.rund-kv {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.rund-kv dd {
  margin: 0;
  font-size: var(--text-sm);
}

.rund-mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.rund-form {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-5) var(--space-4);
  align-content: start;
}

.rund-f {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.rund-f--2 {
  grid-column: span 2;
}

.rund-f--3 {
  grid-column: span 3;
}

.rund-f--4 {
  grid-column: span 4;
}

/* The label row carries the ↺ chip, so an edited field never changes height
   and the four columns stay on one baseline. */
.rund-l {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--space-5);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.rund-sp {
  flex: 1;
}

/* The footer is one row that does not wrap, so a long recipe name has to be
   allowed to shrink and wrap rather than push the buttons out of it: a flex
   item's `min-width: auto` is what would stop it. */
.rund-kept {
  min-width: 0;
}

/* The "Strength" header has to sit over the strength box, so the two share one
   value: changed apart, the label stops lining up with the column it names. */
.rund-form {
  --rund-strength-w: 72px;
}

.rund-l2 {
  width: var(--rund-strength-w);
  text-align: right;
}

.rund-x-gap {
  width: var(--control-h-bar);
}

.rund-lora,
.rund-size {
  display: grid;
  grid-template-columns: minmax(0, 1fr) var(--rund-strength-w) var(--control-h-bar);
  gap: var(--space-3);
  align-items: center;
}

/* Width and height split the cell evenly. At `--rund-f--2` that is one grid
   column each, which is what a five-digit number needs and what the single
   cell never gave it. */
.rund-size {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-3);
}

/* ── Pictures in (#1457) ─────────────────────────────────────────────────
   A row is the tile, the words, then the pin and the clear in the same
   bar-button column the LoRA rows use, so the right edge lines up. The tile is
   72px: the size a picture is still recognisable at in a four-column form, and
   a component-local value for the same reason the 168px source column is. */
.rund-pics {
  gap: var(--space-3);
}

.rund-in {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) var(--control-h-bar) var(--control-h-bar);
  gap: var(--space-4);
  align-items: center;
}

.rund-in-tile {
  width: 72px;
  height: 72px;
  padding: 0;
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

button.rund-in-tile {
  cursor: pointer;
}

button.rund-in-tile:hover:not(.rund-in-tile--off) {
  background: var(--hover-wash);
}

button.rund-in-tile.rund-in-tile--off {
  opacity: var(--opacity-disabled);
  cursor: default;
}

/* An empty slot is drawn as a place to put something, not as a broken image. */
.rund-in-tile--empty {
  border-style: dashed;
}

.rund-in-img,
.rund-in-cell {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* The selection: up to four of its pictures, as a 2 x 2 contact sheet. */
.rund-in-tile--sel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-auto-rows: 1fr;
  gap: var(--space-1);
}

.rund-in-text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
  min-width: 0;
}

.rund-in-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.rund-in-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rund-in-sub--bad {
  color: rgb(var(--v-theme-surface-error));
}

.rund-in-move {
  margin-top: var(--space-1);
}

.rund-check {
  flex-direction: row;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.rund-check label {
  cursor: pointer;
}

.rund-box {
  width: 16px;
  height: 16px;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

/* One live region around the refusals: see RunReasonNotice. */
.rund-reasons {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.rund-more {
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding-top: var(--space-4);
}

.rund-disc > summary {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h);
  font-size: var(--text-sm);
  cursor: pointer;
}

.rund-rest {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
}

.rund-quiet,
.rund-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.rund-note {
  margin: 0;
  line-height: var(--leading-body);
}

.rund-note--bad {
  color: rgb(var(--v-theme-surface-error));
}
</style>
