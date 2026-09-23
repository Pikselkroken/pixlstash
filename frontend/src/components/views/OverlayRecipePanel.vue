<template>
  <!-- The body of the sidebar's Recipe tab. Not a collapsible section: the tab
       band is the disclosure, and a second chevron inside it would be a control
       that hides the thing the reader just chose. -->
  <div class="recipe-pane">
    <div v-if="!recipe" class="recipe-empty">
      Nothing here was made in ComfyUI, so there is no recipe to show.
    </div>

    <template v-else>
      <div class="recipe-scroll">
        <!-- Design order: the "Matches your saved recipe X" banner, then
             Workflow, Prompt, Models, Settings. -->
        <!-- The name is the way THERE. Saying a recipe keeps this look and
             then leaving the reader to go and find it was the dead end #1480
             names; this is the same gesture Open makes for the workflow, one
             query further, and it stays plain text when there is no topology
             to send anybody to - exactly where Open is not offered either. -->
        <div v-if="matched" class="recipe-match">
          <v-icon size="16">mdi-bookmark</v-icon>
          <span
            >Matches your saved recipe
            <component
              :is="recipe.topologyHash ? 'button' : 'b'"
              class="recipe-match-name"
              :class="{ 'recipe-match-name--linked': !!recipe.topologyHash }"
              :type="recipe.topologyHash ? 'button' : undefined"
              @click="recipe.topologyHash ? openSavedRecipes() : undefined"
            >
              <Tooltip
                v-if="recipe.topologyHash"
                text="Show this recipe in the Workflows view"
                activator="parent"
                :describe="false"
              />
              {{ matched.name || "Untitled" }}</component
            >
          </span>
        </div>
        <div class="section-label section-label--on-dark recipe-sec">
          <span>Workflow</span>
          <button
            v-if="recipe.topologyHash"
            class="recipe-sec-act"
            type="button"
            @click="openWorkflowsView"
          >
            <Tooltip
              text="Show this workflow in the Workflows view"
              activator="parent"
              :describe="false"
            />
            Open
            <v-icon size="14">mdi-chevron-right</v-icon>
          </button>
        </div>
        <!-- The design names the workflow and the stack it is in. Both come
             from the workflow cards read, which is not built yet, so this says
             what the file itself says: how big the graph is. -->
        <div class="recipe-workflow">{{ recipe.summary }}</div>

        <template v-if="recipe.positive_prompt">
          <div class="section-label section-label--on-dark recipe-sec">
            <span>Prompt</span>
            <button class="recipe-sec-act" type="button" @click="copyPrompt">
              <Tooltip
                text="Copy the prompt"
                activator="parent"
                :describe="false"
              />
              <v-icon size="14">mdi-content-copy</v-icon>
            </button>
          </div>
          <p class="recipe-prompt">{{ recipe.positive_prompt }}</p>
        </template>

        <!-- Models. A chip that reached a shelf row is a link to it; one that
             did not is the same chip without the affordance, because "this file
             is not on your shelf" is information and an inert chip says it. -->
        <template v-if="models.length">
          <div class="section-label section-label--on-dark recipe-sec">
            <span>Models</span>
          </div>
          <div class="recipe-chip-row">
            <component
              :is="model.model_id ? 'button' : 'span'"
              v-for="(model, index) in models"
              :key="`${model.name}-${model.widget}-${index}`"
              class="recipe-chip"
              :class="{ 'recipe-chip--linked': !!model.model_id }"
              :type="model.model_id ? 'button' : undefined"
              @click="
                model.model_id ? openModelOnShelf(model.model_id) : undefined
              "
            >
              <Tooltip :text="modelTooltip(model)" activator="parent" />
              <v-icon size="12" class="recipe-chip-kind">{{
                isAdapter(model) ? "mdi-layers" : "mdi-cube-outline"
              }}</v-icon>
              <span class="recipe-chip-name">{{ modelLabel(model) }}</span>
              <!-- The precision `modelLabel` just stripped out of the name.
                   Drawn like the strength beside it - mono, dimmed, flex:none -
                   because it is a machine word read character by character and
                   it must not be what the chip ellipsises. The raw filename is
                   still in the hover text, so nothing is hidden. -->
              <span v-if="quantBadge(model.quant)" class="recipe-chip-quant">{{
                quantBadge(model.quant).label === "FP8"
                  ? quantBadge(model.quant).title
                  : quantBadge(model.quant).label
              }}</span>
              <v-icon v-if="model.verified" size="12" class="recipe-chip-badge"
                >mdi-check-decagram</v-icon
              >
              <span
                v-if="model.strength != null"
                class="recipe-chip-strength"
                >{{ formatStrength(model.strength) }}</span
              >
            </component>
          </div>
        </template>

        <!-- Settings, Seed and Negative are one key/value grid, as drawn. The
             design also shows the workflow's own value beside an overridden one
             ("workflow: 8"); that comparison needs the workflow defaults read,
             which is not built yet. -->
        <template v-if="settingRows.length">
          <div class="section-label section-label--on-dark recipe-sec">
            <span>Settings</span>
          </div>
          <dl class="inspector-kv recipe-kv">
            <div
              v-for="row in settingRows"
              :key="row.label"
              class="recipe-kv-item"
            >
              <Tooltip v-if="row.note" :text="row.note" activator="parent" />
              <dt>{{ row.label }}</dt>
              <dd
                :class="{
                  'recipe-kv-none': row.absent,
                  'recipe-kv-mono': row.mono,
                }"
              >
                {{ row.value }}
              </dd>
            </div>
          </dl>
        </template>

        <!-- The resolution lock, which #1313 asks for and the design does not
             draw: which picture each input of the run actually loaded, not
             which one its picker would choose today. Only a run PixlStash
             submitted has these. -->
        <template v-if="inputs.length">
          <div class="section-label section-label--on-dark recipe-sec">
            <span>Inputs</span>
          </div>
          <div class="recipe-input-row">
            <div
              v-for="input in inputs"
              :key="inputKey(input)"
              class="recipe-input"
              :class="{ 'recipe-input--gone': !shownAsPicture(input) }"
            >
              <Tooltip :text="inputTooltip(input)" activator="parent" />
              <img
                v-if="shownAsPicture(input)"
                class="recipe-input-thumb"
                :src="pictureThumbnailUrl(input.input_picture_id)"
                :alt="inputTooltip(input)"
                @error="unloadable.add(inputKey(input))"
              />
              <v-icon
                v-else
                size="18"
                class="recipe-input-thumb recipe-input-icon"
                >mdi-image-off-outline</v-icon
              >
            </div>
          </div>
        </template>
        <!-- The raw graph, with Copy and Download. The v1.12 design drops this
             in favour of Open, but pasting a workflow into ComfyUI and saving
             the JSON are both things people actually do with it, so it is kept
             from the Metadata panel's old ComfyUI box - collapsed, so it does
             not crowd the reading above it. -->
        <details class="recipe-details" @toggle="onWorkflowToggle">
          <summary class="recipe-summary">
            <span class="recipe-summary-title">{{
              graph && graph.isApiFormat ? "API Workflow JSON" : "Workflow JSON"
            }}</span>
            <span class="recipe-summary-actions">
              <!-- Copy only for the editor's format. The Metadata panel this
                   box came from guarded it the same way: an API graph is not
                   what the ComfyUI editor opens, so offering to copy one for
                   pasting back is offering something that does not work.
                   Download stays either way - the file is worth keeping. -->
              <button
                v-if="graph && !graph.isApiFormat"
                class="recipe-sec-act"
                type="button"
                @click.stop="copyWorkflow"
              >
                <v-icon size="14">mdi-content-copy</v-icon>
                Copy
              </button>
              <button
                v-if="graph"
                class="recipe-sec-act"
                type="button"
                @click.stop="downloadWorkflow"
              >
                <v-icon size="14">mdi-download</v-icon>
                Download
              </button>
            </span>
          </summary>
          <p v-if="graphState === 'loading'" class="recipe-empty">Reading…</p>
          <p v-else-if="graphState === 'none'" class="recipe-empty">
            This picture carries no graph that ComfyUI can open.
          </p>
          <textarea
            v-else
            class="recipe-textarea"
            readonly
            :value="workflowJson"
          ></textarea>
        </details>
      </div>

      <!-- The footer the design pins at the bottom: Run… (the Run popup,
           #1407), Save as recipe, and *Run another workflow…* (#1406) - the
           same popup with its workflow picker unset, which is what an A1111
           picture, and any picture at all, can still do. -->
      <div class="recipe-foot">
        <!-- The reason in prose as well as on the button. A tooltip is not a
             sentence everyone gets: it needs a hover or a focus, and the
             design asks for the reason to be readable without either. -->
        <p v-if="runReason" :id="runReasonId" class="recipe-run-reason">
          {{ runReason }}
        </p>
        <ul
          v-if="conversionProblems.length"
          :id="problemsId"
          class="recipe-run-problems"
        >
          <li v-for="problem in conversionProblems" :key="problem">
            {{ problem }}
          </li>
        </ul>
        <!-- Only when it is not the sentence above: the two refusals coincide
             on a read-only session, and printing it twice would read as two
             separate problems. -->
        <p
          v-if="inputReasonIsOwn"
          :id="inputReasonId"
          class="recipe-run-reason"
        >
          {{ useAsInputReason }}
        </p>
        <div class="recipe-foot-actions">
          <!-- `aria-disabled`, not `disabled`: a natively-disabled button is
               out of the tab order, so a keyboard reader could never reach the
               reason `aria-describedby` points at. AppButton already inks both
               spellings the same way. -->
          <AppButton
            variant="primary"
            size="sm"
            class="recipe-run"
            block
            :aria-disabled="runReason ? 'true' : undefined"
            :aria-describedby="runDescribedBy"
            @click="onRun"
          >
            <Tooltip :text="runTooltip" activator="parent" :describe="false" />
            <v-icon size="16">mdi-play</v-icon>
            Run…
          </AppButton>
          <!-- Saved, not "Save as recipe", once a recipe already keeps this
               look: the gesture is done and offering it again would make a
               second row saying the same thing.

               `aria-disabled`, not `disabled`, for this pane's own stated
               reason: a natively-disabled button is out of the tab order, so
               a keyboard reader could never reach the sentence that says why
               it is inert. The click is refused in the handler instead. -->
          <AppButton
            v-if="canSave"
            size="sm"
            block
            :icon-left="matched ? 'check' : 'bookmark-plus-outline'"
            :aria-disabled="matched ? 'true' : undefined"
            :aria-describedby="matched ? savedReasonId : undefined"
            @click="onSave"
          >
            <Tooltip
              :text="
                matched
                  ? `Already kept as “${matched.name || 'Untitled'}”`
                  : 'Keep this look as a recipe'
              "
              activator="parent"
              :describe="false"
            />
            {{ matched ? "Saved" : "Save as recipe" }}
          </AppButton>
          <p v-if="matched" :id="savedReasonId" class="recipe-run-reason">
            Already kept as “{{ matched.name || "Untitled" }}”.
          </p>
          <AppButton
            v-if="comfyuiConfigured"
            variant="secondary"
            size="sm"
            block
            :aria-disabled="useAsInputReason ? 'true' : undefined"
            :aria-describedby="inputDescribedBy"
            :tooltip="
              useAsInputReason ||
              'Run another workflow, starting from this picture\'s recipe'
            "
            @click="onUseAsInput"
          >
            <v-icon size="16">mdi-sitemap-outline</v-icon>
            Run another workflow…
          </AppButton>
        </div>
      </div>
    </template>
  </div>

  <!-- Mounted only while it is up: it is a dialog nobody has asked for until
       they press the button, and an unmounted one costs no reads. -->
  <SaveRecipeDialog
    v-if="saveOpen"
    :open="saveOpen"
    :workflow-key="recipe?.workflowKey || ''"
    :prompt="recipe?.positive_prompt || ''"
    :negative="recipe?.negativePrompt || ''"
    :loras="saveLoras"
    :seed="recipe?.seedText || ''"
    :settings-aside="settingsAside"
    :source-picture-id="Number(pictureId) || null"
    @close="saveOpen = false"
    @saved="onSaved"
  />
</template>

<script setup>
/**
 * The Recipe tab of the lightbox sidebar (#1313).
 *
 * Read-only, and fed entirely from the `comfyMetadata` the overlay already
 * fetches for the picture - one file read, not a second one for this tab.
 * Drawn to the v1.12 Workflows & Recipes design ("From a picture: an Info tab
 * and a Recipe tab"): Workflow with Open, Prompt, Models as chips with
 * strengths, then Settings, Seed and Negative in one key/value grid, with the
 * action pinned in a footer.
 *
 * **Two things the design draws are still absent rather than faked**, because
 * the data behind each is a later step of that plan: the workflow's name and
 * the stack it is in (the workflow cards read), and the workflow's own value
 * beside an overridden setting (the workflow defaults read). Run… itself is
 * the Run popup (F5, #1407), and the "Matches your saved recipe X" banner and
 * the Save button are live (F6, #1408).
 */
import { computed, reactive, ref, useId, watch } from "vue";
import { useRouter } from "vue-router";
import SaveRecipeDialog from "../io/SaveRecipeDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { getPictureWorkflow } from "../../api/comfyui";
import { listAdapters } from "../../api/modelShelf";
import { listSavedRecipes } from "../../api/recipes";
import { pictureThumbnailUrl } from "../../api/pictures";
import { isReadOnly } from "../../utils/apiClient";
import { copyText } from "../../utils/clipboard";
import { downloadBlob } from "../../utils/downloadFile";
import { deriveModelName, quantBadge } from "../../utils/modelShelf";
import { keepsTheSameLook } from "../../utils/recipeKey";
import { resolveRecipeLoras } from "../../utils/recipeLoras";

const props = defineProps({
  recipe: { type: Object, default: null },
  /** The open picture, for the lazy workflow-graph read. */
  pictureId: { type: [Number, String], default: null },
  canGenerateVariants: { type: Boolean, default: false },
  /**
   * Whether this machine has a ComfyUI at all. Separate from
   * `canGenerateVariants`, which folds in the session and the picture too: the
   * refusals have to be told apart to be worth showing, and "not connected" is
   * the wrong thing to say to a share-link reader on a machine that is.
   */
  comfyuiConfigured: { type: Boolean, default: false },
});

const emit = defineEmits(["run", "use-as-input"]);

const runReasonId = useId();
const inputReasonId = useId();
const problemsId = useId();

/**
 * Why this recipe cannot be run again, or null when it can.
 *
 * Three different "no"s, and they are not interchangeable to the person
 * reading them: this machine has no ComfyUI to run anything on, this session
 * may not run anything, or **this picture is not a thing ComfyUI can run** -
 * which is the A1111 case the Recipe tab otherwise fills in completely. The
 * graph reasons come from the recipe read's own `reason` so the words here
 * cannot drift from the decision the server made.
 */
const RUN_REASONS = {
  a1111:
    "This picture was made in A1111 or Forge, so there is no ComfyUI graph " +
    "here to run again.",
  no_seed_input:
    "This graph has no seed to change, so running it again would make the " +
    "same picture.",
  pixlstash_nodes:
    "This graph calls back into PixlStash, so PixlStash will not replay it.",
  editor_graph:
    "This picture carries only ComfyUI's editor view of its workflow, and " +
    "PixlStash could not rebuild that into a graph ComfyUI can run.",
  // Not `editor_graph`: nothing is wrong with this workflow, ComfyUI was not
  // running to be asked about it. Saying the first about the second reads as
  // permanent and sends the reader looking at their file.
  comfyui_unreachable:
    "PixlStash needs ComfyUI to read this picture's workflow, and could not " +
    "reach it. Start ComfyUI and open this picture again.",
};

/**
 * What stopped the rebuild, one sentence each.
 *
 * Printed under the refusal because every one of them is actionable in a way
 * the refusal itself is not: an uninstalled node pack is a thing to go and
 * install, and "could not rebuild it" on its own sends the reader nowhere.
 */
const conversionProblems = computed(
  () => props.recipe?.conversionProblems || [],
);

/**
 * Whether this recipe's graph was never resolved, so the fields read off a
 * resolved graph are *unknown* rather than *absent*.
 *
 * Only the editor-graph refusal: an A1111 picture's fields ARE read, and an
 * ordinary ComfyUI one's are too.
 */
const UNRESOLVED_REASONS = new Set(["editor_graph", "comfyui_unreachable"]);
const unreadable = computed(() => UNRESOLVED_REASONS.has(props.recipe?.reason));

const runReason = computed(() => {
  if (!props.recipe) return null;
  // Order matters, because these are ranked by what the reader can do about
  // them. What the PICTURE is comes first: an A1111 picture is not runnable on
  // any machine, in any session, so saying anything about this one would send
  // the reader off to fix something that is not the problem.
  if (props.recipe.source === "a1111") return RUN_REASONS.a1111;
  // `=== false`, not falsy: a recipe that does not carry the field at all is
  // not a refusal. Every payload the app produces carries it; a caller that
  // does not is not told its picture is broken.
  if (props.recipe.available === false) {
    return (
      RUN_REASONS[props.recipe.reason] ||
      "This picture's recipe cannot be run again."
    );
  }
  // Then the SESSION. A share link can read a recipe - the route is
  // picture-scoped - so this is a real reader seeing a real recipe, and
  // telling them ComfyUI is not connected would be false on a machine where
  // it is, and would send them to a settings screen they cannot open.
  if (isReadOnly.value) {
    return "This is a read-only view of the picture, so nothing can be run from it.";
  }
  // Then the MACHINE.
  if (!props.comfyuiConfigured) {
    return "ComfyUI is not connected, so this recipe cannot be run from here.";
  }
  if (!props.canGenerateVariants) {
    return "This recipe cannot be run from here.";
  }
  return null;
});

/**
 * Why another workflow cannot be run from this picture, or null when it can.
 *
 * **It is not "use this picture as an input", whatever the control used to
 * say.** That went with the rail run panel in v1.12 F5: `POST /workflows/run`
 * has no `inputs` field and uploads nothing into ComfyUI's input folder, so a
 * button promising it would send a picture and get a run that never read it -
 * the backend's own words for why it declines to take the field. What the
 * button does now is open the Run popup with its workflow picker unset.
 *
 * The two context menus that carry the same action are the contract: both
 * fence the entry on `comfyuiConfigured` (no ComfyUI, nothing to be an input
 * FOR, so the entry does not exist) and both render it disabled for a
 * read-only session rather than hiding it. This matches them, so the action
 * behaves the same way wherever the reader meets it.
 */
const READ_ONLY_REASON =
  "This is a read-only view of the picture, so nothing can be run from it.";

const useAsInputReason = computed(() =>
  isReadOnly.value ? READ_ONLY_REASON : null,
);

/**
 * Where each button's `aria-describedby` points.
 *
 * The two refusals coincide on a read-only session and diverge everywhere
 * else - a read-only reader looking at an A1111 picture is told two different
 * things, one per button. One sentence is rendered once and shared when they
 * agree; when they differ each button gets its own, so neither is described by
 * a sentence about the other.
 */
/**
 * What describes the Run button, refusal AND reasons.
 *
 * `aria-describedby` takes a list, and shipping only the sentence gives a
 * screen-reader user the half that - by the list's own reason for existing -
 * sends them nowhere: they hear that the rebuild failed and never which node
 * class is missing.
 */
const runDescribedBy = computed(() => {
  if (!runReason.value) return undefined;
  return conversionProblems.value.length
    ? `${runReasonId} ${problemsId}`
    : runReasonId;
});

const inputReasonIsOwn = computed(
  () => !!useAsInputReason.value && useAsInputReason.value !== runReason.value,
);
const inputDescribedBy = computed(() => {
  if (!useAsInputReason.value) return undefined;
  return inputReasonIsOwn.value ? inputReasonId : runReasonId;
});

function onRun() {
  // `aria-disabled` leaves the button clickable, so the refusal is here.
  if (runReason.value) return;
  emit("run");
}

function onUseAsInput() {
  if (useAsInputReason.value) return;
  emit("use-as-input");
}

const router = useRouter();
/** Input rows whose thumbnail failed to load; see `shownAsPicture`. */
const unloadable = reactive(new Set());

// Keyed by node ref and position, which repeat from one picture to the next, and
// this component is never re-created as the lightbox walks the filmstrip - so a
// stale entry would mark that slot "gone" for every later picture.
watch(
  () => props.recipe,
  () => unloadable.clear(),
);

const models = computed(() => props.recipe?.modelSlots || []);

// ── Saved recipes: does one of them already keep this look? ─────────────────
//
// The banner and the Save button are one question: a recipe whose prompt and
// LoRA file names are this picture's is the recipe this picture was made by,
// which is exactly what the server's own credit matches on. The keys live in
// `utils/recipeKey.js` so the two sides cannot drift; matched here rather than
// asked of the server because there is no route that answers "which recipe
// does this picture match".

const saveOpen = ref(false);
const savedRecipes = ref([]);

/**
 * Whether keeping this look is even offered.
 *
 * A recipe is filed under a card, so a picture the hub has not keyed has
 * nowhere to put one; a read-only reader is refused for the reason every other
 * action here refuses them.
 */
const canSave = computed(
  () => Boolean(props.recipe?.workflowKey) && !isReadOnly.value,
);

/**
 * The key `savedRecipes` holds the recipes OF.
 *
 * A ref, and the banner is gated on it: `ImageOverlay` nulls the recipe before
 * each read, so the key goes key → undefined → key on every filmstrip step.
 * Clearing the rows on the undefined would make the step re-read; keeping them
 * without this gate would let the previous card's recipes match the next
 * picture. So the rows stay and the comparison refuses until the key is back.
 */
const loadedKey = ref("");

/** This picture's look, in the shape both sides of the match take. */
const thisLook = computed(() => ({
  prompt: props.recipe?.positive_prompt,
  loras: props.recipe?.loraNames,
}));

const matched = computed(() => {
  if (!props.recipe) return null;
  // Only against the card these rows were read for.
  if (props.recipe.workflowKey !== loadedKey.value) return null;
  return (
    savedRecipes.value.find((row) => keepsTheSameLook(row, thisLook.value)) ||
    null
  );
});

/** The LoRAs a new recipe would keep, resolved against the model shelf. */
const saveLoras = ref([]);

const savedReasonId = useId();

/**
 * What this pane cannot hand the Save dialog, said rather than dropped.
 *
 * A recipe's settings are *overrides*, addressed `slot_label/input_name`, and
 * `extract_recipe_extras` gives this tab `{field: value}` with no slot - so
 * the numbers on screen cannot be turned into a recipe's overrides here. The
 * Run popup can, because it reads the card's parameter list.
 */
const settingsAside = computed(() =>
  Object.keys(props.recipe?.settings || {}).length
    ? "The settings above are not kept: a recipe keeps the ones you change in the Run popup, which is where this workflow's parameters are named."
    : "",
);

/** The gesture, refused here rather than by a `disabled` nobody can reach. */
function onSave() {
  if (matched.value) return;
  void openSave();
}

/**
 * Build the LoRA list the Save dialog is handed, and open it.
 *
 * **Every LoRA the picture names is kept, digest or no digest** - dropping the
 * ones the shelf cannot identify made the recipe's key disagree with the
 * picture's, so a saved row never matched the picture it came from. The
 * resolution itself is `utils/recipeLoras.js`, shared with the Recipes tab,
 * which does the same job from the cover picture of an unsaved look.
 */
async function openSave() {
  let shelf = [];
  try {
    shelf = await listAdapters();
  } catch (err) {
    console.warn("Could not read the model shelf's LoRAs:", err);
  }
  saveLoras.value = resolveRecipeLoras(
    props.recipe?.loraNames,
    models.value,
    shelf,
  );
  saveOpen.value = true;
}

async function loadSavedRecipes() {
  const key = props.recipe?.workflowKey;
  // **Not for a read-only reader.** Every route under `/recipes` is
  // OWNER_ONLY by decision, so this would be a guaranteed 403 - and the
  // filmstrip would fire it on every step.
  if (isReadOnly.value) {
    savedRecipes.value = [];
    loadedKey.value = "";
    return;
  }
  // No card on this picture: nothing to read, and `matched` already refuses
  // because the key it gates on is not this one.
  if (!key) return;
  // Already read for this card. The whole stack's recipes do not change
  // because the reader stepped to the next picture on the same workflow, and
  // this read is not cheap: it resolves the stack and groups every kept
  // picture of every variant in it to work out the credit.
  if (key === loadedKey.value) return;
  loadedKey.value = key;
  try {
    const rows = await listSavedRecipes(key);
    // The key may have moved on while the read was out - the filmstrip steps
    // through pictures faster than a round trip.
    if (key === props.recipe?.workflowKey) savedRecipes.value = rows;
  } catch (err) {
    // A banner that cannot be drawn is not a failure of the tab: the reading
    // above it is what the reader came for, so this is logged and dropped.
    console.warn("Could not read your saved recipes for this picture:", err);
    savedRecipes.value = [];
    loadedKey.value = "";
  }
}

watch(() => props.recipe?.workflowKey, loadSavedRecipes, { immediate: true });

function onSaved(row) {
  // Straight into the list, so the footer says Saved without another read.
  // **By id, because the dialog may have replaced one of these rows rather
  // than added one** - appending a replacement would leave the old content in
  // the list under the same id, and the banner would go on naming a look the
  // row no longer keeps.
  if (!row) return;
  savedRecipes.value = savedRecipes.value.some((known) => known.id === row.id)
    ? savedRecipes.value.map((known) => (known.id === row.id ? row : known))
    : [...savedRecipes.value, row];
}
const inputs = computed(() => props.recipe?.inputs || []);

/** A LoRA and friends wear the layers glyph; a checkpoint wears the cube. */
function isAdapter(model) {
  return /lora|adapter/i.test(model.widget || "");
}

/**
 * What to CALL a model here: the same derived name the model shelf and the
 * workflow card show, off the same parser.
 *
 * The overlay is read a click away from the card the picture came off, so a
 * chip reading `t5xxl_fp8_e4m3fn.safetensors` under a card chip reading
 * `t5xxl` is one model named two ways. The raw string stays in the hover text
 * (`modelTooltip`), which is where it is actually useful - it is what a reader
 * pastes into a ComfyUI node, and for a slot named by digest it is the digest.
 *
 * Derived HERE and not served, unlike the workflow card's slot name: this
 * response's `name` is the shelf lookup's own key and the panel needs the raw
 * value for the tooltip, so stripping it server-side would take away the one
 * string the chip cannot do without.
 */
function modelLabel(model) {
  // The shelf row's own name first, where the owner gave it one: the chip
  // then reads as the row it opens.
  return model.display_name || deriveModelName(model.name) || model.name;
}

/**
 * The design's rows, in its order, from whatever the graph actually set.
 *
 * `Sampler` and `Size` are each two graph settings written as one value
 * ("euler · sgm", "832×1216"), which is how the design draws them and how
 * ComfyUI users say them. A setting the graph does not carry is left out
 * entirely; Seed and Negative are the exception and say "none", because their
 * absence is a fact about the recipe worth reading.
 */
/**
 * The design's Settings block, from the one settings reading the app has.
 *
 * `extract_recipe_extras` returns `{field: value}` - the FIRST node naming a
 * field wins, which its own docstring owns as "what this graph says" rather
 * than "the settings of the pass that made this picture". A1111 pictures come
 * back through the same field with A1111's own names, so the rows are rendered
 * from whatever keys arrive, in a preferred order, rather than branching on the
 * source.
 */
const SETTING_LABELS = {
  steps: "Steps",
  cfg: "CFG",
  cfg_scale: "CFG",
  guidance: "Guidance",
  sampler_name: "Sampler",
  sampler: "Sampler",
  scheduler: "Scheduler",
  denoise: "Denoise",
  size: "Size",
};

// Drawn in this order when present; anything else follows, in the order the
// backend listed it, so a field nobody has mapped is still shown.
const SETTING_ORDER = [
  "steps",
  "cfg",
  "cfg_scale",
  "guidance",
  "sampler_name",
  "sampler",
  "scheduler",
  "denoise",
];

const settingRows = computed(() => {
  const recipe = props.recipe;
  if (!recipe) return [];
  const settings = { ...(recipe.settings || {}) };

  const rows = [];
  const push = (label, text) => {
    if (text === null || text === undefined || text === "") return;
    rows.push({ label, value: text });
  };

  for (const field of SETTING_ORDER) {
    if (!(field in settings)) continue;
    push(SETTING_LABELS[field] || field, format(settings[field]));
    delete settings[field];
  }
  // Two settings written as one value, the way the design draws it and the way
  // ComfyUI users say it. A1111 already sends `size` as one string.
  const width = settings.width;
  const height = settings.height;
  delete settings.width;
  delete settings.height;
  if (width != null && height != null) {
    push("Size", `${format(width)}\u00d7${format(height)}`);
  }
  for (const [field, value] of Object.entries(settings)) {
    push(SETTING_LABELS[field] || field, format(value));
  }

  // Always shown: "no negative prompt" and "no seed" are both worth reading.
  //
  // **"none" is a claim about the graph, so it is only made about a graph that
  // was read.** The negative prompt and the sampler settings come off the
  // resolved API graph; a picture whose editor graph would not rebuild has no
  // resolved graph, so PixlStash did not read them rather than found them
  // absent. Printing "none" there tells the reader the workflow has no
  // negative prompt, which is a different and possibly false thing. The seed
  // IS read off the editor graph directly, so it keeps its own answer.
  rows.push({
    label: "Seed",
    value: recipe.seedText || "none",
    absent: !recipe.seedText,
    mono: Boolean(recipe.seedText),
  });
  rows.push(
    unreadable.value
      ? {
          label: "Negative",
          value: "not read",
          absent: true,
          note:
            "PixlStash could not rebuild this workflow, so its negative " +
            "prompt and settings were not read off it.",
        }
      : {
          label: "Negative",
          value: recipe.negativePrompt || "none",
          absent: !recipe.negativePrompt,
          note: recipe.negativePrompt || null,
        },
  );
  return rows;
});

function format(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return String(Number(value.toFixed(4)));
  return String(value);
}

/** Two decimals, the way every LoRA strength in the app is written. */
function formatStrength(strength) {
  return Number(strength).toFixed(2);
}

const runTooltip = computed(
  () => runReason.value || "Run this recipe again, with everything editable",
);

function modelTooltip(model) {
  if (model.verified) {
    return `${model.name} - named by digest, so this is that exact file. Open it on the model shelf.`;
  }
  if (model.model_id) {
    return `${model.name} - matched by name, so this is a file called that. Open it on the model shelf.`;
  }
  return `${model.name} - not on your model shelf.`;
}

function inputKey(input) {
  return `${input.node_ref}-${input.position}`;
}

/**
 * A row that still has a picture to show.
 *
 * `input_picture_id` is nulled server-side once the input has left the library,
 * and `unloadable` catches the rest: a row whose picture is still in the vault
 * but whose thumbnail has not been written (or has been swept) would otherwise
 * draw the browser's broken-image glyph, which reads as the panel being broken
 * rather than as the picture being gone.
 */
function shownAsPicture(input) {
  return Boolean(input.input_picture_id) && !unloadable.has(inputKey(input));
}

function inputTooltip(input) {
  const where = `${input.node_ref} · ${input.position + 1}`;
  return shownAsPicture(input)
    ? `Input ${where}: picture ${input.input_picture_id}`
    : `Input ${where}: the picture this run loaded can no longer be shown`;
}

function openModelOnShelf(modelId) {
  router.push({ name: "models", query: { model: String(modelId) } });
}

function openWorkflowsView() {
  router.push({
    name: "workflows",
    query: { topology: props.recipe.topologyHash },
  });
}

/** The same place, with the rail open on the recipes rather than the card. */
function openSavedRecipes() {
  router.push({
    name: "workflows",
    query: { topology: props.recipe.topologyHash, tab: "recipes" },
  });
}

// ── The graph's bytes, read only when the box is opened ────────────────────
//
// The recipe read answers what the picture was made with; the editor's graph is
// a separate route because it is the one thing here that is large, and shipping
// it on every filmstrip step for a box that is collapsed would be the expensive
// half of a cheap read.
const graph = ref(null);
const graphState = ref("idle"); // idle | loading | ready | none

watch(
  () => props.pictureId,
  () => {
    graph.value = null;
    graphState.value = "idle";
  },
);

async function onWorkflowToggle(event) {
  if (!event.target.open || graphState.value !== "idle") return;
  if (!props.pictureId) return;
  graphState.value = "loading";
  try {
    const data = await getPictureWorkflow(props.pictureId);
    graph.value = {
      workflow: data?.workflow,
      isApiFormat: data?.is_api_format,
    };
    graphState.value = data?.workflow ? "ready" : "none";
  } catch (e) {
    // A 404 is the honest "this file carries no graph to open".
    if (e?.response?.status !== 404) {
      console.error("Failed to read the workflow graph:", e);
    }
    graphState.value = "none";
  }
}

const workflowJson = computed(() => stringify(graph.value?.workflow));

function stringify(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

async function copyWorkflow() {
  if (!(await copyText(workflowJson.value))) {
    console.warn("Failed to copy the workflow JSON.");
  }
}

function downloadWorkflow() {
  downloadBlob(
    new Blob([workflowJson.value], { type: "application/json" }),
    "comfyui_workflow.json",
  );
}

async function copyPrompt() {
  if (!(await copyText(props.recipe?.positive_prompt || ""))) {
    console.warn("Failed to copy the prompt.");
  }
}
</script>

<style scoped>
/* The lightbox pane is a fixed-height column that does not scroll itself, so
   the tab body is what scrolls and the footer stays pinned under it
   (`AppInspector`'s `lightbox` contract). */
.recipe-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.recipe-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

.recipe-empty {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

/* A section label with an action on its right, the shape the design draws for
   "Workflow … Open ›". Type and ink come from the shared `.section-label`. */
.recipe-sec {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
  padding: var(--space-4) 0 var(--space-1);
}

.recipe-scroll > .recipe-sec:first-child {
  padding-top: 0;
}

.recipe-sec-act {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  text-transform: none;
  letter-spacing: normal;
  font-weight: var(--weight-medium);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
}

.recipe-sec-act:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
  color: rgb(var(--v-theme-on-dark-surface));
}

/* The design's banner: quiet, above the reading, and never an alert - it says
   something good happened once, not that something is wrong. */
.recipe-match {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.recipe-match-name {
  color: rgb(var(--v-theme-on-dark-surface));
}

/* All of this is the LINKED variant's alone, so the plain `<b>` is left
   exactly as it was. `font: inherit` because the linked name is a `<button>`
   and nothing resets a button's font app-wide - without it the name is the
   browser's own 13.33px sans in the middle of a sentence - and the weight is
   what `font: inherit` has just taken off the `<b>` it replaces.

   Underlined rather than coloured: the banner sits on the dark panel, where
   the theme's link colour is not one of the on-dark tokens. */
.recipe-match-name--linked {
  font: inherit;
  font-weight: var(--weight-semibold);
  padding: 0;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.recipe-match-name--linked:hover {
  text-decoration-thickness: 2px;
}

.recipe-workflow {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-dark-surface));
}

/* A paragraph, not a scrolling box: the design reads the prompt as text. */
.recipe-prompt {
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.45;
  color: rgb(var(--v-theme-on-dark-surface));
  overflow-wrap: anywhere;
}

.recipe-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}

.recipe-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  max-width: 100%;
  font-size: var(--text-2xs);
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
}

.recipe-chip--linked {
  cursor: pointer;
}

.recipe-chip--linked:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
  color: rgb(var(--v-theme-on-dark-surface));
}

.recipe-chip-kind {
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  flex: none;
}

.recipe-chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The verified mark is olive, the dark-surface one: the panel is dark in both
   themes, so the per-theme primary would be wrong in light. */
.recipe-chip-badge {
  color: rgb(var(--v-theme-dark-surface-primary));
  flex: none;
}

.recipe-chip-quant,
.recipe-chip-strength {
  flex: none;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

/* `.inspector-kv` is the inspector's own two-column grid; this only sets what
   is local to the tab. */
.recipe-kv-item {
  min-width: 0;
}

.recipe-kv-item dd {
  overflow-wrap: anywhere;
}

.recipe-kv-mono {
  font-family: var(--font-mono);
}

/* An absence is not a status, so it stays neutral and merely quiet. */
.recipe-kv-none {
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
}

.recipe-input-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.recipe-input {
  /* 40px = 5 steps of the 8px grid. Evidence, not a target: these tiles are
     not interactive, so the 44px touch minimum does not apply. */
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgba(var(--v-theme-shadow), 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
}

.recipe-input-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.recipe-input-icon {
  color: rgba(var(--v-theme-on-dark-surface), 0.45);
}

.recipe-input--gone {
  border: 1px dashed rgba(var(--v-theme-on-dark-surface), 0.25);
}

.recipe-details {
  margin-top: var(--space-4);
  background: rgba(var(--v-theme-shadow), 0.25);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
}

.recipe-details summary {
  cursor: pointer;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
}

.recipe-details summary::-webkit-details-marker {
  display: none;
}

.recipe-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.recipe-summary-title {
  color: rgb(var(--v-theme-on-dark-surface));
  font-weight: var(--weight-medium);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recipe-summary-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  flex: none;
}

.recipe-textarea {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  min-height: 160px;
  max-height: 280px;
  margin-top: var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  line-height: 1.4;
  padding: var(--space-3);
  resize: vertical;
  overflow: auto;
  white-space: pre;
  word-break: normal;
}

.recipe-details:not([open]) .recipe-textarea {
  display: none;
}

/* Pinned under the scrolling body, as the design draws it. */
.recipe-foot {
  flex: none;
  display: flex;
  /* A column now: the reason sits above the buttons, which stay in a row. */
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  margin-top: var(--space-4);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
}

/* Stacked, not side by side: the pane is --stats-panel-w (288px) and these two
   labels are ~314px together, so a row wraps raggedly with the first button
   stretched. The design's own footer holds "Run…" and "Save", which do fit. */
.recipe-foot-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* The same secondary ink the rest of the pane's prose uses. */
.recipe-run-reason {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

/* The same voice as the refusal above it, indented as its detail rather than
   set apart as a warning: these are things to go and fix, not an alarm. */
.recipe-run-problems {
  margin: 0;
  padding-left: var(--space-4);
  /* The footer is `flex: none` against the scrolling body, so an unbounded
     list would push the prompt and the models off the pane. One uninstalled
     pack is one sentence, but two packs is easily a dozen. */
  max-height: var(--space-9);
  overflow-y: auto;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.recipe-run {
  flex: 1;
}
</style>
