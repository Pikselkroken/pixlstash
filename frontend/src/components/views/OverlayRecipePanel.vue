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
        <details v-if="recipe.workflow" class="recipe-details">
          <summary class="recipe-summary">
            <span class="recipe-summary-title">{{
              recipe.isApiFormat ? "API Workflow JSON" : "Workflow JSON"
            }}</span>
            <span class="recipe-summary-actions">
              <button
                class="recipe-sec-act"
                type="button"
                @click.stop="copyWorkflow"
              >
                <v-icon size="14">mdi-content-copy</v-icon>
                Copy
              </button>
              <button
                class="recipe-sec-act"
                type="button"
                @click.stop="downloadWorkflow"
              >
                <v-icon size="14">mdi-download</v-icon>
                Download
              </button>
            </span>
          </summary>
          <textarea
            class="recipe-textarea"
            readonly
            :value="workflowJson"
          ></textarea>
        </details>
      </div>

      <!-- The footer the design pins at the bottom. It holds Run… and Save;
           Save needs saved recipes and Run… is the Run popup, neither of which
           exists yet, so today this is the one replay the app already has. -->
      <div class="recipe-foot">
        <AppButton
          variant="primary"
          size="sm"
          class="recipe-run"
          :disabled="!canGenerateVariants"
          @click="emit('generate-variants')"
        >
          <Tooltip :text="runTooltip" activator="parent" :describe="false" />
          <v-icon size="16">mdi-play</v-icon>
          Generate variants…
        </AppButton>
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
 * stack it is in (the workflow cards read), the workflow's own value beside an
 * overridden setting (the workflow defaults read), and Run… as the Run popup.
 * Generate variants is the replay the app ships today and stands in its place.
 */
import { computed, reactive, watch } from "vue";
import { useRouter } from "vue-router";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { copyText } from "../../utils/clipboard";

const props = defineProps({
  recipe: { type: Object, default: null },
  canGenerateVariants: { type: Boolean, default: false },
});

const emit = defineEmits(["generate-variants"]);

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
const settingRows = computed(() => {
  const recipe = props.recipe;
  if (!recipe) return [];
  const byLabel = new Map();
  for (const row of recipe.settings || []) {
    if (!byLabel.has(row.label)) byLabel.set(row.label, row);
  }
  const value = (label) => byLabel.get(label)?.value;
  const note = (...labels) =>
    labels
      .map((label) => byLabel.get(label)?.node)
      .filter(Boolean)
      .join(" · ") || null;

  const rows = [];
  const push = (label, text, extra = {}) => {
    if (text === null || text === undefined || text === "") return;
    rows.push({ label, value: text, ...extra });
  };

  push("Steps", format(value("steps")), { note: note("steps") });
  push("CFG", format(value("cfg") ?? value("guidance")), {
    note: note("cfg", "guidance"),
  });
  push("Sampler", join(value("sampler_name"), value("scheduler")), {
    note: note("sampler_name", "scheduler"),
  });
  push("Denoise", format(value("denoise")), { note: note("denoise") });
  const size =
    value("width") != null && value("height") != null
      ? `${format(value("width"))}×${format(value("height"))}`
      : null;
  push("Size", size, { note: note("width", "height") });

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

function join(left, right) {
  const parts = [left, right].filter(
    (part) => part !== null && part !== undefined && part !== "",
  );
  return parts.length ? parts.map(format).join(" · ") : null;
}

function format(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return String(Number(value.toFixed(4)));
  return String(value);
}

/** Two decimals, the way every LoRA strength in the app is written. */
function formatStrength(strength) {
  return Number(strength).toFixed(2);
}

const runTooltip = computed(() =>
  props.canGenerateVariants
    ? "Run this recipe again with a fresh seed"
    : "ComfyUI is not connected, so this recipe cannot be run from here",
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

const workflowJson = computed(() => stringify(props.recipe?.workflow));

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
  gap: var(--space-3);
  padding-top: var(--space-4);
  margin-top: var(--space-4);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
}

.recipe-run {
  flex: 1;
}
</style>
