<template>
  <AppDialog
    :open="open"
    title="Generate variants"
    :subtitle="sourceLabel"
    :width="560"
    :persistent="submitting"
    @close="onRequestClose"
  >
    <div class="remix" @keydown.ctrl.enter.prevent="submit" @keydown.meta.enter.prevent="submit">
      <!-- Scope disclosure. The menu entry stays enabled at any selection
           count and always acts on the right-clicked image, so the scope has
           to be stated rather than silently applied. -->
      <p v-if="otherSelectedCount > 0" class="remix-scope">
        Generating from this image only. {{ otherSelectedCount }}
        {{ otherSelectedCount === 1 ? "other selected image is" : "other selected images are" }}
        not included.
        <button type="button" class="remix-link" @click="useBatchInstead">
          Use all {{ selectedImageIds.length }} →
        </button>
      </p>

      <!-- ── Mode ────────────────────────────────────────────────────────
           A radio LIST, not a segmented control: each mode needs room for a
           subtitle and, when unavailable, a reason. v1.11's lock-replay mode
           appends a third row here with no redesign. -->
      <div
        class="remix-modes"
        role="radiogroup"
        aria-label="Generation mode"
        @keydown="onModeKeydown"
      >
        <div
          v-for="(mode, index) in modes"
          :key="mode.id"
          :ref="(el) => setModeRef(el, index)"
          class="remix-mode"
          :class="{
            'remix-mode--on': selectedMode === mode.id,
            'remix-mode--off': !mode.available,
          }"
          role="radio"
          :aria-checked="selectedMode === mode.id"
          :aria-disabled="!mode.available"
          :aria-busy="mode.busy"
          :aria-describedby="mode.reason ? `remix-reason-${mode.id}` : undefined"
          :tabindex="index === focusedModeIndex ? 0 : -1"
          @click="selectMode(mode.id)"
          @keydown.enter.prevent="selectMode(mode.id)"
          @keydown.space.prevent="selectMode(mode.id)"
        >
          <span class="remix-mode-title">{{ mode.title }}</span>
          <span v-if="mode.subtitle" class="remix-mode-subtitle">{{ mode.subtitle }}</span>
          <!-- Always-visible text, not a title attribute: a hover-only reason
               is unreachable by keyboard and touch. -->
          <span v-if="mode.reason" :id="`remix-reason-${mode.id}`" class="remix-mode-reason">
            {{ mode.reason }}
          </span>
        </div>
      </div>

      <!-- Announced once when the check resolves badly; silent on success,
           because a success that changes nothing the user asked about is noise. -->
      <p class="remix-live" aria-live="polite">{{ liveMessage }}</p>

      <!-- ── Template mode ───────────────────────────────────────────── -->
      <template v-if="selectedMode === 'template'">
        <label class="remix-field">
          <span class="remix-label">Template</span>
          <div class="remix-select-wrap">
            <select
              v-model="selectedWorkflow"
              class="remix-select"
              :disabled="!templates.length"
            >
              <option v-for="wf in templates" :key="wf.name" :value="wf.name">
                {{ wf.display_name || wf.name }}
              </option>
            </select>
            <v-icon size="18" class="remix-select-chevron">mdi-chevron-down</v-icon>
          </div>
        </label>
        <p v-if="!templatesLoading && !templates.length" class="remix-note">
          No image-to-image templates found. Add one in Settings → Workflows.
        </p>

        <div v-if="templateTakesPrompt" class="remix-field">
          <div class="remix-label-row">
            <span class="remix-label">Prompt</span>
            <span v-if="promptIsDescription" class="remix-provenance">
              from image description
            </span>
            <button
              v-else-if="description"
              type="button"
              class="remix-link"
              @click="resetPrompt"
            >
              Reset to description
            </button>
          </div>
          <textarea
            ref="promptRef"
            v-model="prompt"
            class="remix-textarea"
            rows="3"
            :placeholder="promptPlaceholder"
            @keydown.stop
          ></textarea>
          <p class="remix-hint">
            Editing templates respond better to an instruction ("make it snowing")
            than to a description of the picture.
          </p>
        </div>
      </template>

      <!-- ── Recipe mode ─────────────────────────────────────────────── -->
      <template v-else-if="selectedMode === 'recipe'">
        <details class="remix-disclosure">
          <summary class="remix-summary">Show what this will run</summary>
          <dl class="remix-recipe">
            <template v-if="recipe?.positive_prompt">
              <dt>Prompt</dt>
              <dd class="remix-recipe-prompt">{{ recipe.positive_prompt }}</dd>
            </template>
            <template v-if="recipe?.models?.length">
              <dt>Model</dt>
              <dd>{{ recipe.models.join(", ") }}</dd>
            </template>
            <template v-if="recipe?.loras?.length">
              <dt>LoRAs</dt>
              <dd>{{ recipe.loras.join(", ") }}</dd>
            </template>
            <dt>Seed</dt>
            <dd>{{ seedTargetLabel }}</dd>
          </dl>
        </details>
        <p v-if="preflightPartial" class="remix-note">
          {{ preflightPartial }}
        </p>
      </template>

      <!-- ── Seed (both modes) ───────────────────────────────────────── -->
      <div v-if="selectedMode" class="remix-field">
        <span class="remix-label">Seed</span>
        <div class="remix-seed-row">
          <div class="remix-seg" role="radiogroup" aria-label="Seed mode">
            <button
              v-for="option in seedModes"
              :key="option.id"
              type="button"
              class="remix-seg-btn"
              :class="{ 'remix-seg-btn--on': seedMode === option.id }"
              role="radio"
              :aria-checked="seedMode === option.id"
              @click="seedMode = option.id"
            >
              <v-icon size="15">{{ option.icon }}</v-icon>
              {{ option.label }}
            </button>
          </div>
          <input
            v-if="seedMode === 'fixed'"
            v-model.number="seed"
            type="number"
            class="remix-num"
            min="0"
            :max="maxSeed"
            aria-label="Seed value"
            @keydown.stop
          />
        </div>
      </div>

      <p v-if="submitError" class="remix-error" role="alert">{{ submitError }}</p>
    </div>

    <template #footer>
      <span class="remix-shortcut">Ctrl+Enter to generate</span>
      <AppButton variant="ghost" @click="onRequestClose">Cancel</AppButton>
      <AppButton
        ref="generateRef"
        variant="primary"
        :icon-left="submitting ? 'loading' : 'auto-fix'"
        :disabled="!canSubmit"
        @click="submit"
      >
        Generate
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * "Generate variants" — the Remix v1 entry point (v1.9 Lane D).
 *
 * Two ways to make a variant of one picture, chosen from a radio LIST so that
 * v1.11's third mode (lock-replay: reproduce the original exactly) appends a
 * row rather than forcing a redesign:
 *
 * - **template** — run a saved i2i workflow with a prompt and a seed.
 * - **recipe** — "same workflow, new seed": replay the executable ComfyUI
 *   graph embedded in the source file. Offered only when the file actually
 *   carries one AND the server's pre-flight against the user's ComfyUI passes.
 *
 * Deliberately absent: a strength/denoise slider. None of the shipped
 * templates exposes a denoise input — the Flux2 Klein edit graph samples from
 * an empty latent with the source entering as reference conditioning — so the
 * control would move nothing. A slider that silently does nothing is worse
 * than no slider: it teaches a false model of cause and effect.
 *
 * The dialog closes on submit and hands progress to the app-wide ComfyUiRunner
 * rather than hosting its own bar, because abort is global (it clears the whole
 * ComfyUI queue) and a modal-local "Cancel" next to it would be a mislabel.
 */
import { computed, nextTick, ref, watch } from "vue";
import { VIcon } from "vuetify/components";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import { getPictureRecipe, listWorkflows, runImageToImage, runRecipe } from "../../api/comfyui";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The right-clicked picture. The dialog always acts on this one. */
  image: { type: Object, default: null },
  /** The grid selection, used only to disclose that it is NOT being used. */
  selectedImageIds: { type: Array, default: () => [] },
  /** Ties ComfyUI progress events back to this tab. */
  clientId: { type: String, default: "" },
  backendUrl: { type: String, default: "" },
  /** Whether generated outputs join the source's stack. */
  stackOutputs: { type: Boolean, default: true },
});

const emit = defineEmits(["close", "run", "use-batch"]);

const MAX_SEED_32 = 4294967295;
// Recipe replay needs more than 32 bits — the shipped Flux2 Klein template's
// own noise_seed is 432262096973502 — but the ceiling offered here is
// MAX_SAFE_INTEGER, not ComfyUI's 2^64-1: above 2^53 a JavaScript number
// cannot hold the value exactly, so the field would quietly round whatever the
// user typed and pin a different seed than the one on screen. The API still
// accepts the full range for programmatic callers.
const MAX_SEED_RECIPE = Number.MAX_SAFE_INTEGER;
const MODE_KEY = "comfyui_remix_mode";
const SEED_MODE_KEY = "comfyui_remix_seed_mode";
const SEED_KEY = "comfyui_remix_seed";

const seedModes = [
  { id: "random", label: "Random", icon: "mdi-dice-multiple-outline" },
  { id: "fixed", label: "Fixed", icon: "mdi-lock-outline" },
];

const templates = ref([]);
const templatesLoading = ref(false);
const selectedWorkflow = ref("");
const prompt = ref("");
const description = ref("");
const promptTouched = ref(false);

const recipe = ref(null);
const recipeLoading = ref(false);
const recipeError = ref("");

const selectedMode = ref("");
const focusedModeIndex = ref(0);
const modeEls = ref([]);
const liveMessage = ref("");

const seedMode = ref(
  sessionStorage.getItem(SEED_MODE_KEY) === "fixed" ? "fixed" : "random",
);
const savedSeed = Number(sessionStorage.getItem(SEED_KEY));
const seed = ref(Number.isFinite(savedSeed) && savedSeed >= 0 ? savedSeed : 0);

const submitting = ref(false);
const submitError = ref("");

const promptRef = ref(null);
const generateRef = ref(null);
/** The element focus returns to on close — never document.body. */
let returnFocusEl = null;

watch(seedMode, (v) => sessionStorage.setItem(SEED_MODE_KEY, v));
watch(seed, (v) => sessionStorage.setItem(SEED_KEY, String(v)));

const sourceLabel = computed(() => {
  const img = props.image;
  if (!img) return "";
  return img.file_name || img.filename || (img.id != null ? `#${img.id}` : "");
});

const otherSelectedCount = computed(() => {
  const ids = props.selectedImageIds || [];
  if (ids.length <= 1) return 0;
  return ids.filter((id) => String(id) !== String(props.image?.id)).length;
});

const maxSeed = computed(() =>
  selectedMode.value === "recipe" ? MAX_SEED_RECIPE : MAX_SEED_32,
);

/**
 * Why recipe mode is unavailable, phrased so the three causes send the user to
 * three different places. "Could not check" and "checked and it is broken" are
 * deliberately different sentences.
 */
const recipeReason = computed(() => {
  if (recipeLoading.value) return "Checking your ComfyUI…";
  if (recipeError.value) return recipeError.value;
  const info = recipe.value;
  if (!info) return "";
  if (!info.available) {
    if (info.reason === "no_seed_input") {
      return "This workflow has no random seed, so a re-run would produce the identical image.";
    }
    return "No executable workflow embedded in this image. Only images generated by ComfyUI carry one.";
  }
  const pre = info.preflight || {};
  if (pre.ok === false) {
    const missing = [
      ...(pre.missing_node_classes || []),
      ...(pre.missing_models || []).map((m) => m.value),
      ...(pre.missing_input_images || []).map((m) => m.value),
    ].filter(Boolean);
    const shown = missing.slice(0, 3).join(", ");
    const rest = missing.length > 3 ? ` +${missing.length - 3} more` : "";
    return `Your ComfyUI is missing: ${shown}${rest}`;
  }
  return "";
});

const recipeAvailable = computed(
  () =>
    !recipeLoading.value &&
    !recipeError.value &&
    Boolean(recipe.value?.available) &&
    recipe.value?.preflight?.ok !== false,
);

/**
 * A caveat on a mode that IS still offered. Kept apart from `recipeReason`
 * because "we could not check" is not "we checked and it is broken": an
 * unreachable ComfyUI proves nothing about the recipe, so the mode stays
 * selectable and the run's own error is the authority.
 */
const recipeCaveat = computed(() => {
  if (recipe.value?.preflight?.checked === false) {
    return "Could not reach ComfyUI to check this will run. Templates still work.";
  }
  return "";
});

/** A partially-skipped check must not read as a clean bill of health. */
const preflightPartial = computed(() => {
  const skipped = recipe.value?.preflight?.unchecked_fields || 0;
  if (!skipped) return "";
  return `${skipped} model field${skipped === 1 ? "" : "s"} could not be checked; ComfyUI will have the final say.`;
});

const seedTargetLabel = computed(() => {
  const inputs = recipe.value?.seed_inputs || [];
  if (!inputs.length) return "none";
  return inputs
    .map((s) => `${s.class_type || "node"} #${s.node_id}.${s.field}`)
    .join(", ");
});

const modes = computed(() => [
  {
    id: "recipe",
    title: "Same workflow, new seed",
    subtitle: recipe.value?.available ? recipe.value?.summary : "",
    available: recipeAvailable.value,
    busy: recipeLoading.value,
    reason: recipeAvailable.value ? recipeCaveat.value : recipeReason.value,
  },
  {
    id: "template",
    title: "Pick a template",
    subtitle: "Choose a workflow and write your own prompt",
    available: true,
    busy: false,
    reason: "",
  },
]);

const activeTemplate = computed(() =>
  templates.value.find((w) => w.name === selectedWorkflow.value),
);

/**
 * Mirror the shipped SelectionBar rule: a workflow with no {{caption}}
 * placeholder ignores the prompt entirely, so showing the field would invite
 * the user to write carefully into a void.
 */
const templateTakesPrompt = computed(() => {
  const missing = activeTemplate.value?.missing_placeholders || [];
  return !missing.includes("{{caption}}");
});

const promptIsDescription = computed(
  () => !promptTouched.value && Boolean(description.value) && prompt.value === description.value,
);

const promptPlaceholder = computed(() =>
  description.value
    ? "Describe the change you want…"
    : "Describe the change you want (this image has no description yet)…",
);

const canSubmit = computed(() => {
  if (submitting.value || !props.image?.id) return false;
  if (selectedMode.value === "recipe") return recipeAvailable.value;
  if (selectedMode.value === "template") return Boolean(selectedWorkflow.value);
  return false;
});

watch(prompt, (next) => {
  if (next !== description.value) promptTouched.value = true;
});

// Announce a bad pre-flight once, politely — the user may be mid-prompt and
// must not be interrupted. A clean result announces nothing.
watch(recipeAvailable, (available) => {
  if (recipeLoading.value) return;
  liveMessage.value = available
    ? ""
    : `Same workflow, new seed is unavailable. ${recipeReason.value}`;
});

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) void onOpen();
  },
  { immediate: true },
);

function setModeRef(el, index) {
  if (el) modeEls.value[index] = el;
}

async function onOpen() {
  returnFocusEl = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  submitError.value = "";
  submitting.value = false;
  liveMessage.value = "";
  promptTouched.value = false;
  recipe.value = null;
  recipeError.value = "";
  description.value = normaliseDescription(props.image?.description);
  prompt.value = description.value;
  // Nothing is preselected until the check resolves: a mode that flips out
  // from under the user mid-interaction is worse than a moment of no default.
  selectedMode.value = "";
  await Promise.all([loadTemplates(), loadRecipe()]);
  selectedMode.value = resolveInitialMode();
  focusedModeIndex.value = Math.max(
    0,
    modes.value.findIndex((m) => m.id === selectedMode.value),
  );
  await nextTick();
  focusInitial();
}

/**
 * A pending or sentinel description is not usable prompt text.
 * The backend encodes "generating…" in the same field as a sentinel string.
 */
function normaliseDescription(value) {
  if (typeof value !== "string") return "";
  if (value.startsWith("__description::")) return "";
  return value.trim();
}

function resolveInitialMode() {
  const sticky = sessionStorage.getItem(MODE_KEY);
  if (sticky === "recipe" && recipeAvailable.value) return "recipe";
  if (sticky === "template") return "template";
  // The user right-clicked THIS image; the recipe is the highest-fidelity
  // expression of "from this", so it wins when it is genuinely runnable.
  return recipeAvailable.value ? "recipe" : "template";
}

function focusInitial() {
  // Template mode opens on the prompt (the first real decision); recipe mode
  // has nothing to edit, so it opens on Generate and remix is one keypress.
  if (selectedMode.value === "template" && templateTakesPrompt.value) {
    promptRef.value?.focus();
    return;
  }
  generateRef.value?.$el?.focus?.();
}

async function loadTemplates() {
  templatesLoading.value = true;
  try {
    const data = await listWorkflows({ baseUrl: props.backendUrl });
    const all = Array.isArray(data?.workflows) ? data.workflows : [];
    templates.value = all.filter((w) => w?.valid && w?.workflow_type === "i2i");
    if (!templates.value.some((w) => w.name === selectedWorkflow.value)) {
      selectedWorkflow.value = templates.value[0]?.name || "";
    }
  } catch (err) {
    templates.value = [];
    console.error("Failed to list ComfyUI workflows for remix:", err);
  } finally {
    templatesLoading.value = false;
  }
}

async function loadRecipe() {
  if (!props.image?.id) return;
  recipeLoading.value = true;
  recipeError.value = "";
  try {
    recipe.value = await getPictureRecipe(props.image.id, {
      baseUrl: props.backendUrl,
    });
  } catch (err) {
    recipe.value = null;
    recipeError.value =
      err?.response?.data?.detail ||
      "Could not check this image for an embedded workflow.";
    console.error("Failed to read remix recipe:", err);
  } finally {
    recipeLoading.value = false;
  }
}

function selectMode(id) {
  const mode = modes.value.find((m) => m.id === id);
  // Traversal reaches an unavailable row so its reason is discoverable by a
  // keyboard-only user; only activation is blocked.
  if (!mode || !mode.available) return;
  selectedMode.value = id;
  sessionStorage.setItem(MODE_KEY, id);
}

function onModeKeydown(event) {
  const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"];
  if (!keys.includes(event.key)) return;
  event.preventDefault();
  const last = modes.value.length - 1;
  let next;
  if (event.key === "Home") next = 0;
  else if (event.key === "End") next = last;
  else if (event.key === "ArrowDown" || event.key === "ArrowRight")
    next = focusedModeIndex.value >= last ? 0 : focusedModeIndex.value + 1;
  else next = focusedModeIndex.value <= 0 ? last : focusedModeIndex.value - 1;
  focusedModeIndex.value = next;
  modeEls.value[next]?.focus();
}

function resetPrompt() {
  prompt.value = description.value;
  promptTouched.value = false;
}

function useBatchInstead() {
  emit("use-batch");
  close();
}

function onRequestClose() {
  // While a submission is in flight the dialog is persistent: closing here
  // would leave the user unable to tell whether the run queued.
  if (submitting.value) return;
  close();
}

function close() {
  emit("close");
  nextTick(() => {
    if (returnFocusEl && document.contains(returnFocusEl)) returnFocusEl.focus();
  });
}

async function submit() {
  if (!canSubmit.value) return;
  submitting.value = true;
  submitError.value = "";
  try {
    const body =
      selectedMode.value === "recipe"
        ? await runRecipe(
            {
              picture_id: props.image.id,
              seed_mode: seedMode.value,
              seed: seedMode.value === "fixed" ? seed.value : undefined,
              client_id: props.clientId || undefined,
              stack: props.stackOutputs,
            },
            { baseUrl: props.backendUrl },
          )
        : await runImageToImage(
            {
              picture_ids: [props.image.id],
              workflow_name: selectedWorkflow.value,
              caption: templateTakesPrompt.value ? prompt.value : "",
              seed_mode: seedMode.value,
              seed: seedMode.value === "fixed" ? seed.value : undefined,
              client_id: props.clientId || undefined,
              stack: props.stackOutputs,
            },
            { baseUrl: props.backendUrl },
          );
    const prompts = Array.isArray(body?.prompts) ? body.prompts : [];
    emit("run", {
      prompts,
      pictureId: props.image.id,
      pictureIds: [props.image.id],
    });
    submitting.value = false;
    close();
  } catch (err) {
    // A submission error is a FORM error: keep the dialog and every input.
    submitting.value = false;
    submitError.value =
      err?.response?.data?.detail || err?.message || "Could not start the run.";
  }
}
</script>

<style scoped>
.remix {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.remix-scope {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--hover-wash);
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.remix-link {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-accent));
  cursor: pointer;
}

.remix-link:focus-visible {
  outline: none;
  border-radius: var(--radius-sm);
  box-shadow: var(--focus-ring);
}

/* ── Mode list ─────────────────────────────────────────────────────────── */
.remix-modes {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.remix-mode {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  /* Full-width and comfortably past the 44px touch target. */
  min-height: 44px;
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.remix-mode:hover {
  background: var(--hover-wash);
}

.remix-mode:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.remix-mode--on {
  background: var(--active-wash);
  border-color: rgb(var(--v-theme-accent));
}

/* The affordance recedes; the reason text below does NOT (see .remix-mode-reason). */
.remix-mode--off {
  cursor: default;
  border-color: rgb(var(--v-theme-divider));
}

.remix-mode--off:hover {
  background: none;
}

.remix-mode--off .remix-mode-title,
.remix-mode--off .remix-mode-subtitle {
  opacity: 0.38;
}

.remix-mode-title {
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  line-height: var(--leading-snug);
}

.remix-mode-subtitle {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* Deliberately NOT at 38%: this is the one thing on a disabled row that has to
   be read, and 38% of on-surface will not clear the body contrast floor. */
.remix-mode-reason {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.remix-live {
  /* Announced, not shown: the reason is already rendered on its row. */
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

/* ── Fields ────────────────────────────────────────────────────────────── */
.remix-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.remix-label-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
}

.remix-label {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.remix-provenance {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.remix-select-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.remix-select {
  width: 100%;
  appearance: none;
  padding: var(--space-3) var(--space-7) var(--space-3) var(--space-3);
  font-size: var(--text-base);
  font-family: var(--font-ui);
  color: rgb(var(--v-theme-on-surface));
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.remix-select:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.remix-select-chevron {
  position: absolute;
  right: var(--space-3);
  pointer-events: none;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.remix-textarea {
  width: 100%;
  resize: vertical;
  padding: var(--space-3);
  font-size: var(--text-base);
  font-family: var(--font-ui);
  line-height: var(--leading-body);
  color: rgb(var(--v-theme-on-surface));
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.remix-textarea:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.remix-hint,
.remix-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* ── Recipe disclosure ─────────────────────────────────────────────────── */
.remix-disclosure {
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
}

.remix-summary {
  cursor: pointer;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.remix-summary:focus-visible {
  outline: none;
  border-radius: var(--radius-sm);
  box-shadow: var(--focus-ring);
}

.remix-recipe {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-2) var(--space-4);
  margin: var(--space-4) 0 0;
  font-size: var(--text-xs);
}

.remix-recipe dt {
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.remix-recipe dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.remix-recipe-prompt {
  font-family: var(--font-mono);
  line-height: var(--leading-snug);
}

/* ── Seed ──────────────────────────────────────────────────────────────── */
.remix-seed-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.remix-seg {
  display: inline-flex;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  overflow: hidden;
}

.remix-seg-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  font-family: var(--font-ui);
  color: rgba(var(--v-theme-on-surface), 0.7);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.remix-seg-btn:hover {
  background: var(--hover-wash);
}

.remix-seg-btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.remix-seg-btn--on {
  background: var(--active-wash);
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
}

.remix-num {
  /* Flexes rather than taking a fixed width: a replayed recipe seed can be 15
     digits, which overflows the toolbar panel's shipped 96px field, and a
     third hardcoded width would be drift. */
  flex: 1;
  min-width: 0;
  padding: var(--space-3);
  font-size: var(--text-sm);
  font-family: var(--font-mono);
  color: rgb(var(--v-theme-on-surface));
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.remix-num:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.remix-error {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-error));
}

.remix-shortcut {
  margin-right: auto;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}
</style>
