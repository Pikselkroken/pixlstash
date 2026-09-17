<template>
  <div
    v-if="recipe"
    class="sidebar-section sidebar-section--recipe"
    :class="{ 'sidebar-section--collapsed': collapsed }"
  >
    <div
      class="section-header section-header--collapsible section-label section-label--on-dark"
      @click="collapsed = !collapsed"
    >
      <span>Recipe</span>
      <v-icon size="16" style="opacity: 0.6">{{
        collapsed ? "mdi-chevron-right" : "mdi-chevron-down"
      }}</v-icon>
    </div>

    <div v-if="!collapsed" class="recipe-body">
      <div class="recipe-subtitle">{{ recipe.summary }}</div>

      <!-- Models. A chip that reached a shelf row is a link to it; one that did
           not is the same chip without the affordance, because "this file is
           not on your shelf" is information and an inert chip says it. -->
      <div v-if="models.length" class="recipe-group">
        <div class="recipe-label">Models</div>
        <div class="recipe-chip-row">
          <component
            :is="model.model_id ? 'button' : 'span'"
            v-for="(model, index) in models"
            :key="`${model.name}-${model.widget}-${index}`"
            class="recipe-chip"
            :class="{ 'recipe-chip--linked': !!model.model_id }"
            :type="model.model_id ? 'button' : undefined"
            @click="model.model_id ? openModelOnShelf(model.model_id) : undefined"
          >
            <Tooltip :text="modelTooltip(model)" activator="parent" />
            <v-icon v-if="model.verified" size="12" class="recipe-chip-badge"
              >mdi-check-decagram</v-icon
            >
            <span class="recipe-chip-name">{{ model.name }}</span>
            <span v-if="model.strength != null" class="recipe-chip-strength">{{
              formatStrength(model.strength)
            }}</span>
          </component>
        </div>
      </div>

      <div v-if="recipe.positive_prompt" class="recipe-group">
        <div class="recipe-label">Prompt</div>
        <textarea
          class="recipe-textarea recipe-prompt"
          readonly
          :value="recipe.positive_prompt"
        ></textarea>
      </div>

      <div v-if="settings.length" class="recipe-group">
        <div class="recipe-label">Settings</div>
        <dl class="recipe-settings">
          <div
            v-for="(setting, index) in settings"
            :key="`${setting.label}-${index}`"
            class="recipe-setting"
          >
            <Tooltip :text="setting.node || setting.label" activator="parent" />
            <dt>{{ settingLabel(setting.label) }}</dt>
            <dd>{{ formatValue(setting.value) }}</dd>
          </div>
        </dl>
      </div>

      <!-- The resolution lock: which picture each input of the run actually
           loaded, not which one its picker would choose today. Only a run
           PixlStash submitted has these, so the group is absent for a scanned
           import rather than empty. -->
      <div v-if="inputs.length" class="recipe-group">
        <div class="recipe-label">Inputs</div>
        <div class="recipe-input-row">
          <div
            v-for="input in inputs"
            :key="`${input.node_ref}-${input.position}`"
            class="recipe-input"
            :class="{ 'recipe-input--gone': !input.input_picture_id }"
          >
            <Tooltip :text="inputTooltip(input)" activator="parent" />
            <img
              v-if="input.input_picture_id"
              class="recipe-input-thumb"
              :src="pictureThumbnailUrl(input.input_picture_id)"
              alt=""
            />
            <v-icon v-else size="18" class="recipe-input-thumb recipe-input-icon"
              >mdi-image-off-outline</v-icon
            >
          </div>
        </div>
      </div>

      <div v-if="canGenerateVariants" class="recipe-actions">
        <button
          class="recipe-action recipe-action--primary"
          type="button"
          @click="emit('generate-variants')"
        >
          <Tooltip
            text="Run this recipe again with a fresh seed"
            activator="parent"
            :describe="false"
          />
          <v-icon size="14">mdi-auto-fix</v-icon>
          Generate variants…
        </button>
      </div>

      <details v-if="recipe.workflow" class="recipe-details">
        <summary class="recipe-summary">
          <span class="recipe-summary-title">{{
            recipe.isApiFormat ? "API Workflow JSON" : "Workflow JSON"
          }}</span>
          <span class="recipe-summary-actions">
            <button
              class="recipe-action"
              type="button"
              @click.stop="copyWorkflow"
            >
              <v-icon size="14">mdi-content-copy</v-icon>
              Copy
            </button>
            <button
              class="recipe-action"
              type="button"
              @click.stop="downloadWorkflow"
            >
              <v-icon size="14">mdi-download</v-icon>
              Download
            </button>
            <button
              v-if="recipe.topologyHash"
              class="recipe-action"
              type="button"
              @click.stop="openWorkflowsView"
            >
              <Tooltip
                text="Show this workflow in the Workflows view"
                activator="parent"
                :describe="false"
              />
              <v-icon size="14">mdi-open-in-new</v-icon>
              Open
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
  </div>
</template>

<script setup>
/**
 * The Recipe section of the lightbox sidebar (#1313).
 *
 * Read-only, and fed entirely from the `comfyMetadata` the overlay already
 * fetches for the picture - one file read, not a second one for this panel.
 * It shows what the picture was made with: the models and the strengths they
 * were loaded at, the prompt, the sampler settings, the resolution lock, and
 * the workflow itself. Everything that acts is delegated: Generate variants is
 * the grid's existing Remix dialog, and the two links are the shelf and the
 * Workflows view, each opened at the row this picture points to.
 */
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import Tooltip from "../widgets/Tooltip.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { copyText } from "../../utils/clipboard";

const props = defineProps({
  recipe: { type: Object, default: null },
  canGenerateVariants: { type: Boolean, default: false },
});

const emit = defineEmits(["generate-variants"]);

const router = useRouter();
const collapsed = ref(false);

const models = computed(() => props.recipe?.modelSlots || []);
const settings = computed(() => props.recipe?.settings || []);
const inputs = computed(() => props.recipe?.inputs || []);

const workflowJson = computed(() => stringify(props.recipe?.workflow));

/** `sampler_name` is the graph's spelling; "Sampler" is the reader's. */
const SETTING_LABELS = {
  cfg: "CFG",
  denoise: "Denoise",
  guidance: "Guidance",
  height: "Height",
  sampler_name: "Sampler",
  scheduler: "Scheduler",
  steps: "Steps",
  width: "Width",
};

function settingLabel(label) {
  return SETTING_LABELS[label] || label;
}

function formatValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return String(Number(value.toFixed(4)));
  return String(value);
}

/** Two decimals, the way every LoRA strength in the app is written. */
function formatStrength(strength) {
  return Number(strength).toFixed(2);
}

function modelTooltip(model) {
  if (model.verified) {
    return `${model.name} - named by digest, so this is that exact file. Open it on the model shelf.`;
  }
  if (model.model_id) {
    return `${model.name} - matched by name, so this is a file called that. Open it on the model shelf.`;
  }
  return `${model.name} - not on your model shelf.`;
}

function inputTooltip(input) {
  const where = `${input.node_ref} · ${input.position + 1}`;
  return input.input_picture_id
    ? `Input ${where}: picture ${input.input_picture_id}`
    : `Input ${where}: the picture this run loaded is no longer in this library`;
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
</script>

<style scoped>
/* Type and ink come from the shared `.section-label`; this is layout only. */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
  padding: var(--space-1) 0;
}

.section-header--collapsible {
  cursor: pointer;
  user-select: none;
}

.section-header--collapsible:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}

.recipe-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.recipe-subtitle {
  font-size: var(--text-2xs);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
}

.recipe-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.recipe-label {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
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
  padding: var(--space-2) 6px; /* 6px horizontal has no token: between --space-2 (4px) and --space-3 (8px) */
}

.recipe-chip--linked {
  cursor: pointer;
}

.recipe-chip--linked:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
  color: rgb(var(--v-theme-on-dark-surface));
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
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.recipe-settings {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: var(--space-2) var(--space-3);
  font-size: var(--text-2xs);
}

.recipe-setting {
  min-width: 0;
}

.recipe-setting dt {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.recipe-setting dd {
  color: rgb(var(--v-theme-on-dark-surface));
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recipe-input-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.recipe-input {
  width: 44px;
  height: 44px;
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

.recipe-actions {
  display: flex;
  gap: var(--space-2);
}

.recipe-action {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
  font-size: var(--text-2xs);
  padding: var(--space-1) var(--space-1);
  border-radius: var(--radius-sm);
}

.recipe-action:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
  color: rgb(var(--v-theme-on-dark-surface));
}

.recipe-action--primary {
  padding: var(--space-2) var(--space-3);
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
  color: rgb(var(--v-theme-on-dark-surface));
}

.recipe-details {
  background: rgba(var(--v-theme-shadow), 0.25);
  border-radius: var(--radius-md);
  padding: var(--space-3) 10px; /* 10px horizontal has no token: between --space-3 (8px) and --space-4 (12px) */
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
  gap: 10px; /* no token: 10px is between --space-3 (8px) and --space-4 (12px) */
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

.recipe-prompt {
  min-height: 70px;
  max-height: 160px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
