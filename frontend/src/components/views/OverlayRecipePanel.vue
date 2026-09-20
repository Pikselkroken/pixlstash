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
        <!-- Design order: Workflow, Prompt, Models, Settings. The "Matches your
             saved recipe X" banner the design puts above this needs saved
             recipes, which do not exist yet, so it is absent rather than
             faked. -->
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
              <span class="recipe-chip-name">{{ model.name }}</span>
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

      <!-- The footer the design pins at the bottom. It holds Run… and Save;
           Save needs saved recipes, so today it is Run… (the Run popup, #1407)
           and, beside it, *Run another workflow…* (#1406) - the same popup with
           its workflow picker unset, which is what an A1111 picture, and any
           picture at all, can still do. -->
      <div class="recipe-foot">
        <!-- The reason in prose as well as on the button. A tooltip is not a
             sentence everyone gets: it needs a hover or a focus, and the
             design asks for the reason to be readable without either. -->
        <p v-if="runReason" :id="runReasonId" class="recipe-run-reason">
          {{ runReason }}
        </p>
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
            :aria-describedby="runReason ? runReasonId : undefined"
            @click="onRun"
          >
            <Tooltip :text="runTooltip" activator="parent" :describe="false" />
            <v-icon size="16">mdi-play</v-icon>
            Run…
          </AppButton>
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
 * **Four things the design draws are absent rather than faked**, because the
 * data behind each is a later step of that plan: the "Matches your saved recipe
 * X" banner and the Save button (saved recipes), the workflow's name and the
 * stack it is in (the workflow cards read), and the workflow's own value beside
 * an overridden setting (the workflow defaults read). Run… itself is the Run
 * popup (F5, #1407).
 */
import { computed, reactive, ref, useId, watch } from "vue";
import { useRouter } from "vue-router";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { getPictureWorkflow } from "../../api/comfyui";
import { pictureThumbnailUrl } from "../../api/pictures";
import { isReadOnly } from "../../utils/apiClient";
import { copyText } from "../../utils/clipboard";

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
};

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
const inputs = computed(() => props.recipe?.inputs || []);

/** A LoRA and friends wear the layers glyph; a checkpoint wears the cube. */
function isAdapter(model) {
  return /lora|adapter/i.test(model.widget || "");
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
  rows.push({
    label: "Seed",
    value: recipe.seedText || "none",
    absent: !recipe.seedText,
    mono: Boolean(recipe.seedText),
  });
  rows.push({
    label: "Negative",
    value: recipe.negativePrompt || "none",
    absent: !recipe.negativePrompt,
    note: recipe.negativePrompt || null,
  });
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
  const blob = new Blob([workflowJson.value], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "comfyui_workflow.json";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 100);
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

.recipe-run {
  flex: 1;
}
</style>
