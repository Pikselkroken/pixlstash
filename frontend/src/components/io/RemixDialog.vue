<template>
  <AppDialog
    :open="open"
    title="Generate variants"
    :subtitle="sourceLabel"
    :width="560"
    :persistent="submitting"
    @close="onRequestClose"
    @accept="submit"
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
           Side-by-side radio CARDS: stacked full-width they read as info
           boxes, not a choice (owner feedback 2026-07-29). Still a radio
           group with room for a subtitle and, when unavailable, a reason;
           v1.11's lock-replay mode joins the row and wraps when it does
           not fit. -->
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
            'remix-mode--caution': mode.caution,
          }"
          role="radio"
          :aria-checked="selectedMode === mode.id"
          :aria-disabled="!mode.available"
          :aria-busy="mode.busy"
          :aria-describedby="describedByFor(mode)"
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
            <!-- Status never rides on colour alone. -->
            <v-icon v-if="mode.caution" size="14" class="remix-mode-icon">
              mdi-alert-outline
            </v-icon>
            {{ mode.reason }}
          </span>
        </div>
      </div>

      <!-- The one refusal with somewhere else to go. Sits outside the
           radiogroup: it is an action, and a button inside a radio card would
           select the card. -->
      <p v-if="recipeUsesPixlStashNodes" class="remix-alert">
        <v-icon size="16" class="remix-alert-icon">mdi-alert-outline</v-icon>
        <span>
          Run this one in ComfyUI, where those nodes can reach the library
          directly.
          <button
            type="button"
            class="remix-link"
            :disabled="copyState === 'copying'"
            @click="copyWorkflowToClipboard"
          >
            {{ copyWorkflowLabel }}
          </button>
        </span>
      </p>

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
            @keydown="onFieldKeydown"
          ></textarea>
          <p class="remix-hint">
            Editing templates respond better to an instruction ("make it snowing")
            than to a description of the picture.
          </p>
        </div>

        <!-- Reachability is only knowable when the recipe pre-flight ran, but
             once known it blocks template runs just the same: no contact with
             ComfyUI means nothing generates (owner decision, 2026-07-29). -->
        <p v-if="comfyuiUnreachable" class="remix-alert">
          <v-icon size="16" class="remix-alert-icon">mdi-alert-outline</v-icon>
          <span>
            ComfyUI could not be reached, so nothing can be generated.
            <button
              type="button"
              class="remix-link"
              :disabled="recipeLoading"
              @click="recheckRecipe"
            >
              Check again
            </button>
          </span>
        </p>
      </template>

      <!-- ── Recipe mode ─────────────────────────────────────────────────
           Only blocking facts get a banner. "Imported" is not one: a watched
           folder pointed at the user's own ComfyUI output makes every
           self-generated image imported, so warning on it fires on the common
           case and reads as noise. The route in stays available as the Source
           row inside the disclosure, for whoever goes looking. -->
      <template v-else-if="selectedMode === 'recipe'">
        <p v-if="comfyuiUnreachable" id="remix-alert-unchecked" class="remix-alert">
          <v-icon size="16" class="remix-alert-icon">mdi-alert-outline</v-icon>
          <span>
            ComfyUI could not be reached, so nothing can be generated.
            <button
              type="button"
              class="remix-link"
              :disabled="recipeLoading"
              @click="recheckRecipe"
            >
              Check again
            </button>
          </span>
        </p>

        <details
          class="remix-disclosure"
          :open="disclosureOpen"
          @toggle="disclosureOpen = $event.target.open"
        >
          <summary class="remix-summary">Show what this will run</summary>
          <dl class="remix-recipe">
            <!-- First row on purpose: the summary asks what this will run, and
                 the node classes are the literal answer. Prompt and model are
                 attributes of it. -->
            <dt>Node types</dt>
            <dd class="remix-recipe-nodes">
              <template v-if="nodeClasses.length">
                {{ shownNodeClasses.join(", ") }}<template v-if="hiddenNodeClassCount">
                  <button type="button" class="remix-link" @click="nodeClassesExpanded = true">
                    +{{ hiddenNodeClassCount }} more
                  </button>
                </template>
              </template>
              <template v-else>unknown</template>
            </dd>
            <template v-if="sourceIsImported && recipe?.source_label">
              <dt>Source</dt>
              <dd>{{ recipe.source_label }}</dd>
            </template>
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
          <template v-if="seedMode === 'fixed'">
            <input
              v-model.number="seed"
              type="number"
              class="remix-num"
              min="0"
              :max="maxSeed"
              aria-label="Seed value"
              @keydown="onFieldKeydown"
            />
            <!-- The identical seed re-creates the identical image, which the
                 importer dedupes into silence - flagged, not forbidden. -->
            <span v-if="seedIsOriginal" class="remix-seed-note remix-seed-note--warn">
              <v-icon size="14" class="remix-mode-icon">mdi-alert-outline</v-icon>
              same as original
            </span>
          </template>
          <template v-else-if="seedMode === 'incremented'">
            <input
              v-model.number="seedDelta"
              type="number"
              class="remix-num remix-num--delta"
              :min="-maxSeed"
              :max="maxSeed"
              aria-label="Delta from the original seed"
              @keydown="onFieldKeydown"
            />
            <span class="remix-seed-note" aria-live="polite">
              = {{ incrementedSeed }}
            </span>
          </template>
        </div>
      </div>

      <p v-if="submitError" class="remix-error" role="alert">{{ submitError }}</p>
    </div>

    <template #footer>
      <AppButton variant="ghost" key-hint="esc" @click="onRequestClose">Cancel</AppButton>
      <AppButton
        ref="generateRef"
        variant="primary"
        key-hint="enter"
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
 * "Generate variants" - the Remix v1 entry point (v1.9 Lane D).
 *
 * Two ways to make a variant of one picture, chosen from side-by-side radio
 * CARDS (stacked full-width they read as info boxes, not a choice); v1.11's
 * third mode (lock-replay: reproduce the original exactly) joins the row:
 *
 * - **template** - run a saved i2i workflow with a prompt and a seed.
 * - **recipe** - "same workflow, new seed": replay the executable ComfyUI
 *   graph embedded in the source file. Offered only when the file actually
 *   carries one AND the server's pre-flight against the user's ComfyUI passes.
 *
 * **Recipe mode is a consent surface, not just a convenience** (review finding
 * R3, CWE-829). The graph is file metadata: whoever made the image authored it,
 * and replaying it executes it on the owner's ComfyUI, bounded only by which
 * node packs are installed. So the confirm step names the node classes that
 * will run and says when the file came from outside this instance. When the
 * pre-flight could not run at all - ComfyUI unreachable - the dialog refuses
 * outright: Generate is disabled in BOTH modes until "Check again" succeeds
 * (owner decision, 2026-07-29; the run would fail against a dead ComfyUI
 * anyway). The former run-unchecked acknowledgement is gone from this surface;
 * the API keeps `allow_unchecked` for programmatic callers and the backend
 * still refuses uninspected graphs without it.
 *
 * Deliberately absent: a strength/denoise slider. None of the shipped
 * templates exposes a denoise input - the Flux2 Klein edit graph samples from
 * an empty latent with the source entering as reference conditioning - so the
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
import {
  getPictureRecipe,
  getPictureWorkflow,
  listWorkflows,
  runImageToImage,
  runRecipe,
} from "../../api/comfyui";
import { getPictureMetadata } from "../../api/pictures";
import { errorDetail } from "../../utils/apiError";

import { API_BASE_URL } from "../../utils/apiClient";
const props = defineProps({
  open: { type: Boolean, default: false },
  /** The right-clicked picture. The dialog always acts on this one. */
  image: { type: Object, default: null },
  /** The grid selection, used only to disclose that it is NOT being used. */
  selectedImageIds: { type: Array, default: () => [] },
  /** Ties ComfyUI progress events back to this tab. */
  clientId: { type: String, default: "" },
  backendUrl: { type: String, default: () => API_BASE_URL },
  /** Whether generated outputs join the source's stack. */
  stackOutputs: { type: Boolean, default: true },
});

const emit = defineEmits(["close", "run", "use-batch"]);

const MAX_SEED_32 = 4294967295;
// Recipe replay needs more than 32 bits - the shipped Flux2 Klein template's
// own noise_seed is 432262096973502 - but the ceiling offered here is
// MAX_SAFE_INTEGER, not ComfyUI's 2^64-1: above 2^53 a JavaScript number
// cannot hold the value exactly, so the field would quietly round whatever the
// user typed and pin a different seed than the one on screen. The API still
// accepts the full range for programmatic callers.
const MAX_SEED_RECIPE = Number.MAX_SAFE_INTEGER;
const MODE_KEY = "comfyui_remix_mode";
const SEED_MODE_KEY = "comfyui_remix_seed_mode";
const SEED_KEY = "comfyui_remix_seed";
// Typical graphs carry 8-20 distinct classes; 12 shows most of them whole while
// capping a pathological one. The remainder expands in place rather than
// nesting a second disclosure.
const MAX_NODE_CLASSES_SHOWN = 12;

const SEED_DELTA_KEY = "comfyui_remix_seed_delta";

const templates = ref([]);
const templatesLoading = ref(false);
const selectedWorkflow = ref("");
const prompt = ref("");
const description = ref("");
const promptTouched = ref(false);

const recipe = ref(null);
const recipeLoading = ref(false);
const recipeError = ref("");
const nodeClassesExpanded = ref(false);
const disclosureOpen = ref(false);

const selectedMode = ref("");
const focusedModeIndex = ref(0);
const modeEls = ref([]);
const liveMessage = ref("");

const savedSeedMode = sessionStorage.getItem(SEED_MODE_KEY);
const seedMode = ref(
  ["fixed", "incremented"].includes(savedSeedMode) ? savedSeedMode : "random",
);
const savedSeed = Number(sessionStorage.getItem(SEED_KEY));
const seed = ref(Number.isFinite(savedSeed) && savedSeed >= 0 ? savedSeed : 0);
const savedDelta = Number(sessionStorage.getItem(SEED_DELTA_KEY));
const seedDelta = ref(Number.isFinite(savedDelta) && savedDelta !== 0 ? savedDelta : 1);

const submitting = ref(false);
const submitError = ref("");

const promptRef = ref(null);
const generateRef = ref(null);
/** The element focus returns to on close - never document.body. */
let returnFocusEl = null;
let loadGeneration = 0;

watch(seedMode, (v) => sessionStorage.setItem(SEED_MODE_KEY, v));
watch(seed, (v) => sessionStorage.setItem(SEED_KEY, String(v)));
watch(seedDelta, (v) => sessionStorage.setItem(SEED_DELTA_KEY, String(v)));

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
 * The seed the original run actually used: the recipe route's `seed` (the
 * sampler's own widget), falling back to the first patchable seed input.
 * `null` - never 0, which is a legal seed - when there is nothing to read.
 */
const originalSeed = computed(() => {
  const info = recipe.value;
  if (!info?.available) return null;
  const v = info.seed ?? info.seed_inputs?.[0]?.value;
  return Number.isFinite(v) && v >= 0 ? v : null;
});

/**
 * Incremented is only offered where there is an original to increment from:
 * recipe mode with a readable seed. Templates draw their own seeds, so the
 * option would be an offer the dialog cannot honour.
 */
const seedModes = computed(() => {
  const modes = [{ id: "random", label: "Random", icon: "mdi-dice-multiple-outline" }];
  if (selectedMode.value === "recipe" && originalSeed.value != null) {
    modes.push({ id: "incremented", label: "Incremented", icon: "mdi-plus-minus" });
  }
  modes.push({ id: "fixed", label: "Fixed", icon: "mdi-lock-outline" });
  return modes;
});

// A sticky "incremented" preference must not survive into a context that
// cannot honour it (template mode, or a recipe with no readable seed).
watch(seedModes, (list) => {
  if (!list.some((o) => o.id === seedMode.value)) seedMode.value = "random";
});

const incrementedSeed = computed(() => {
  if (originalSeed.value == null) return null;
  const delta = Number(seedDelta.value) || 0;
  return Math.min(maxSeed.value, Math.max(0, originalSeed.value + delta));
});

/** Flag - not forbid - a fixed seed that would re-create the original exactly.
 * Recipe mode only: a template run with the original's seed is a different
 * graph, so nothing identical comes out of it. */
const seedIsOriginal = computed(
  () =>
    selectedMode.value === "recipe" &&
    originalSeed.value != null &&
    Number(seed.value) === originalSeed.value,
);

/** The graph calls back into PixlStash, so it runs in ComfyUI, not here. */
const recipeUsesPixlStashNodes = computed(
  () => recipe.value?.reason === "pixlstash_nodes",
);

const copyState = ref("idle");
const copyWorkflowLabel = computed(
  () =>
    ({
      copying: "Copying…",
      copied: "Copied - paste into ComfyUI",
      failed: "Copy failed",
    })[copyState.value] || "Copy workflow",
);

/**
 * Put the picture's own UI-format graph on the clipboard, which is the format
 * ComfyUI accepts on paste. Fetched on demand rather than with the recipe: it
 * is the whole graph, and only this one refusal ever needs it.
 */
async function copyWorkflowToClipboard() {
  const id = props.image?.id;
  if (!id || copyState.value === "copying") return;
  copyState.value = "copying";
  try {
    const data = await getPictureWorkflow(id, { baseUrl: props.backendUrl });
    const graph = data?.workflow;
    if (!graph) throw new Error("No workflow in the response");
    await navigator.clipboard.writeText(JSON.stringify(graph, null, 2));
    copyState.value = "copied";
    liveMessage.value = "Workflow copied. Paste it into ComfyUI.";
  } catch (err) {
    // Clipboard writes fail on an insecure origin, and the fetch can 404 on a
    // file whose chunk is unreadable. Either way say so - a button that
    // silently does nothing reads as a broken button.
    copyState.value = "failed";
    liveMessage.value =
      "Could not copy the workflow. Open the picture's ComfyUI details to read it.";
    console.error("Failed to copy workflow:", err);
  }
}

/**
 * Why recipe mode is unavailable or guarded, phrased so each cause sends the
 * user to a different place. "Could not check" and "checked and it is broken"
 * are deliberately different sentences: the first says nothing about the graph,
 * which is precisely why it is the one that needs an acknowledgement.
 */
const recipeReason = computed(() => {
  if (recipeLoading.value) return "Checking your ComfyUI…";
  if (recipeError.value) return recipeError.value;
  const info = recipe.value;
  if (!info) return "";
  if (!info.available) {
    if (info.reason === "pixlstash_nodes") {
      return (
        "This workflow uses PixlStash nodes, so it reads and writes the " +
        "library as it runs. Re-running it here would use the projects, sets " +
        "and pictures it was saved with, not this picture's."
      );
    }
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
  if (pre.checked === false) {
    return (
      "Could not reach ComfyUI, so the workflow in this file was not checked. " +
      "Generate is disabled until ComfyUI can be reached."
    );
  }
  return "";
});

/**
 * No contact with ComfyUI means nothing generates, in either mode (owner
 * decision, 2026-07-29). Only knowable when the pre-flight actually ran; a
 * picture with no embedded recipe reports nothing, and a template run then
 * fails at submit with the error kept in the form.
 */
const comfyuiUnreachable = computed(
  () => recipe.value?.preflight?.checked === false,
);

/**
 * The recipe row has four states, not two. One computed rather than a pair of
 * booleans, because a pair drifts out of sync:
 *
 * - `loading`     - the check is in flight.
 * - `blocked`     - no graph, no seed, or a pre-flight that ran and FAILED.
 *                   The row is aria-disabled: the option genuinely cannot be
 *                   chosen.
 * - `unreachable` - the pre-flight could not run at all. The row stays
 *                   selectable (marking it disabled would be a lie to
 *                   assistive tech and would hide "Check again"), but Generate
 *                   is disabled in both modes until a re-check succeeds.
 * - `ready`       - checked and clean.
 */
const recipeState = computed(() => {
  if (recipeLoading.value) return "loading";
  if (recipeError.value) return "blocked";
  const info = recipe.value;
  if (!info?.available) return "blocked";
  if (info.preflight?.ok === false) return "blocked";
  if (info.preflight?.checked === false) return "unreachable";
  return "ready";
});

/** Selectable, which is NOT the same as runnable. See `canSubmit`. */
const recipeSelectable = computed(
  () => recipeState.value === "ready" || recipeState.value === "unreachable",
);

const sourceIsImported = computed(() =>
  Boolean(recipe.value?.source_is_imported),
);

const nodeClasses = computed(() =>
  Array.isArray(recipe.value?.node_classes) ? recipe.value.node_classes : [],
);

const shownNodeClasses = computed(() =>
  nodeClassesExpanded.value
    ? nodeClasses.value
    : nodeClasses.value.slice(0, MAX_NODE_CLASSES_SHOWN),
);

const hiddenNodeClassCount = computed(
  () => nodeClasses.value.length - shownNodeClasses.value.length,
);

/**
 * A partially-skipped check must not read as a clean bill of health. This is
 * NOT the `needs_ack` state: the pre-flight ran, it just could not enumerate
 * every field. Conflating the two would put the acknowledgement in front of a
 * common, mostly-benign case and teach the user to tick it without reading.
 */
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
    available: recipeSelectable.value,
    // Offered, with a warning. Distinct from unavailable: it must not take the
    // 38% opacity that says "you cannot have this".
    caution: recipeState.value === "unreachable",
    busy: recipeLoading.value,
    reason: recipeReason.value,
  },
  {
    id: "template",
    title: "Pick a template",
    subtitle: "Choose a workflow and write your own prompt",
    available: true,
    caution: false,
    busy: false,
    reason: "",
  },
]);

/**
 * The sentences a screen reader must hear on landing: the row's own reason plus
 * whichever alerts are actually rendered.
 */
function describedByFor(mode) {
  const ids = [];
  if (mode.reason) ids.push(`remix-reason-${mode.id}`);
  if (mode.id === "recipe" && selectedMode.value === "recipe") {
    if (comfyuiUnreachable.value) ids.push("remix-alert-unchecked");
  }
  return ids.length ? ids.join(" ") : undefined;
}

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
  // No contact with ComfyUI, no generation - in either mode.
  if (comfyuiUnreachable.value) return false;
  if (selectedMode.value === "recipe") return recipeState.value === "ready";
  if (selectedMode.value === "template") return Boolean(selectedWorkflow.value);
  return false;
});

watch(prompt, (next) => {
  if (next !== description.value) promptTouched.value = true;
});

// Announce a bad or unchecked pre-flight once, politely - the user may be
// mid-prompt and must not be interrupted. A clean result announces nothing,
// because a success that changes nothing the user asked about is noise.
watch(recipeState, (state) => {
  if (state === "loading") return;
  if (state === "blocked") {
    liveMessage.value = `Same workflow, new seed is unavailable. ${recipeReason.value}`;
  } else if (state === "unreachable") {
    liveMessage.value =
      "ComfyUI could not be reached. Generate is disabled until it can be; " +
      "use Check again once ComfyUI is up.";
  } else {
    liveMessage.value = "";
  }
});

watch(
  [() => props.open, () => props.image?.id],
  ([isOpen]) => {
    if (isOpen) {
      void onOpen();
    } else {
      // Invalidate every async loader from the just-closed dialog.
      loadGeneration += 1;
    }
  },
  { immediate: true },
);

function setModeRef(el, index) {
  if (el) modeEls.value[index] = el;
}

async function onOpen() {
  const generation = (loadGeneration += 1);
  const imageId = props.image?.id;
  const backendUrl = props.backendUrl;
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
  await Promise.all([
    loadTemplates(generation, imageId, backendUrl),
    loadRecipe(generation, imageId),
    loadDescription(generation, imageId),
  ]);
  if (!isCurrentLoad(generation, imageId)) return;
  // Fixed defaults to the seed the original run used - flagged as "same as
  // original" until edited - rather than whatever a previous dialog pinned.
  if (originalSeed.value != null) seed.value = originalSeed.value;
  selectedMode.value = resolveInitialMode();
  focusedModeIndex.value = Math.max(
    0,
    modes.value.findIndex((m) => m.id === selectedMode.value),
  );
  await nextTick();
  focusInitial();
}

function isCurrentLoad(generation, imageId) {
  return (
    generation === loadGeneration &&
    props.open &&
    String(props.image?.id ?? "") === String(imageId ?? "")
  );
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

/**
 * The grid hands this dialog its own row object, and the grid LISTING carries
 * no `description` field at all - so without this fetch the prompt claimed
 * "this image has no description yet" for pictures that plainly have one.
 * Only runs when the prop didn't already provide a usable description.
 */
async function loadDescription(generation, imageId) {
  if (description.value || !imageId) return;
  try {
    const data = await getPictureMetadata(imageId);
    if (!isCurrentLoad(generation, imageId)) return;
    const fetched = normaliseDescription(data?.description);
    if (!fetched) return;
    description.value = fetched;
    if (!promptTouched.value && !prompt.value.trim()) prompt.value = fetched;
  } catch (err) {
    // A missing description only costs the prefill; the dialog stays usable.
    console.error("Failed to fetch picture description for remix:", err);
  }
}

function resolveInitialMode() {
  // `ready`, not merely selectable: an unreachable-ComfyUI recipe row cannot
  // run, and the sticky preference does not get to land the user there either.
  const runnable = recipeState.value === "ready";
  const sticky = sessionStorage.getItem(MODE_KEY);
  if (sticky === "recipe" && runnable) return "recipe";
  if (sticky === "template") return "template";
  // The user right-clicked THIS image; the recipe is the highest-fidelity
  // expression of "from this", so it wins when it is genuinely runnable.
  return runnable ? "recipe" : "template";
}

function focusInitial() {
  // Template mode opens on the prompt (the first real decision); recipe mode
  // has nothing to edit, so it opens on Generate and remix is one keypress.
  if (selectedMode.value === "template" && templateTakesPrompt.value) {
    promptRef.value?.focus();
    return;
  }
  // A disabled Generate is not focusable and focus would fall to document.body,
  // which this dialog never allows. Land on the selected mode row instead - the
  // reason Generate is disabled is written on it.
  if (!canSubmit.value) {
    modeEls.value[focusedModeIndex.value]?.focus?.();
    return;
  }
  generateRef.value?.$el?.focus?.();
}

async function loadTemplates(generation, imageId, backendUrl) {
  templatesLoading.value = true;
  try {
    const data = await listWorkflows({ baseUrl: backendUrl });
    if (!isCurrentLoad(generation, imageId)) return;
    const all = Array.isArray(data?.workflows) ? data.workflows : [];
    templates.value = all.filter((w) => w?.valid && w?.workflow_type === "i2i");
    if (!templates.value.some((w) => w.name === selectedWorkflow.value)) {
      selectedWorkflow.value = templates.value[0]?.name || "";
    }
  } catch (err) {
    if (!isCurrentLoad(generation, imageId)) return;
    templates.value = [];
    console.error("Failed to list ComfyUI workflows for remix:", err);
  } finally {
    if (isCurrentLoad(generation, imageId)) templatesLoading.value = false;
  }
}

async function loadRecipe(generation, imageId) {
  if (!imageId) return;
  recipeLoading.value = true;
  recipeError.value = "";
  try {
    const nextRecipe = await getPictureRecipe(imageId);
    if (!isCurrentLoad(generation, imageId)) return;
    recipe.value = nextRecipe;
  } catch (err) {
    if (!isCurrentLoad(generation, imageId)) return;
    recipe.value = null;
    recipeError.value =
      errorDetail(err) ||
      "Could not check this image for an embedded workflow.";
    console.error("Failed to read remix recipe:", err);
  } finally {
    if (isCurrentLoad(generation, imageId)) recipeLoading.value = false;
  }
  if (!isCurrentLoad(generation, imageId)) return;
  resetRecipeDisclosure();
}

/**
 * Re-seed everything that describes the freshly-read recipe. The disclosure
 * always starts shut: the routine re-roll is a two-click flow, and the one
 * state that used to force it open (an unreachable ComfyUI) already disables
 * Generate outright, so opening it there only buries the banner that says so.
 */
function resetRecipeDisclosure() {
  nodeClassesExpanded.value = false;
  disclosureOpen.value = false;
  // A "Copied" left over from the previous picture would claim this one's
  // workflow is on the clipboard.
  copyState.value = "idle";
}

/** Retry the pre-flight - the only way out of the unreachable refusal. */
async function recheckRecipe() {
  if (recipeLoading.value) return;
  // Cleared first so an unchanged outcome still re-announces.
  liveMessage.value = "";
  const generation = loadGeneration;
  const imageId = props.image?.id;
  await loadRecipe(generation, imageId);
  if (!isCurrentLoad(generation, imageId)) return;
  await nextTick();
  if (recipeState.value === "unreachable") {
    liveMessage.value = "Still could not reach ComfyUI. Generate stays disabled.";
    return;
  }
  if (recipeState.value === "ready") {
    liveMessage.value = "ComfyUI checked. This workflow can run.";
    await nextTick();
    // The button the user was standing on has unmounted.
    generateRef.value?.$el?.focus?.();
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

/**
 * Escape must reach AppDialog (dismiss) and Enter must reach it (accept -
 * the textarea is exempt there, so Enter still makes newlines in the prompt);
 * every other key is walled off from the app-level shortcut owners.
 * Ctrl/Meta+Enter submits from inside a field, where the root handler would
 * never hear it.
 */
function onFieldKeydown(e) {
  if (e.key === "Escape") return;
  if (e.key === "Enter") {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      e.stopPropagation();
      submit();
    }
    return;
  }
  e.stopPropagation();
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
  if (!canSubmit.value) {
    // Enter / Ctrl+Enter must not fail silently: name the blocker.
    if (comfyuiUnreachable.value) {
      liveMessage.value =
        "Generate is disabled: ComfyUI could not be reached.";
    }
    return;
  }
  submitting.value = true;
  submitError.value = "";
  try {
    const body =
      selectedMode.value === "recipe"
        ? await runRecipe(
            {
              picture_id: props.image.id,
              // Incremented is a client-side convenience over the same API:
              // it submits as a fixed seed at original + delta.
              seed_mode: seedMode.value === "random" ? "random" : "fixed",
              seed:
                seedMode.value === "fixed"
                  ? seed.value
                  : seedMode.value === "incremented"
                    ? incrementedSeed.value
                    : undefined,
              client_id: props.clientId || undefined,
              stack: props.stackOutputs,
              // Deliberately no allow_unchecked: an uninspected graph cannot
              // be submitted from this surface at all, and the backend
              // refuses it independently.
            },
          )
        : await runImageToImage(
            {
              picture_ids: [props.image.id],
              workflow_name: selectedWorkflow.value,
              caption: templateTakesPrompt.value ? prompt.value : "",
              seed_mode: seedMode.value === "random" ? "random" : "fixed",
              seed: seedMode.value === "fixed" ? seed.value : undefined,
              client_id: props.clientId || undefined,
              stack: props.stackOutputs,
            },
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
      errorDetail(err) || err?.message || "Could not start the run.";
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
  padding: 0;
  font: inherit;
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-accent));
}

.remix-link:focus-visible {
  outline: none;
  border-radius: var(--radius-sm);
  box-shadow: var(--focus-ring);
}

.remix-link:disabled {
  opacity: 0.5;
  cursor: default;
}

/* ── Mode cards ────────────────────────────────────────────────────────── */
.remix-modes {
  display: grid;
  /* Side by side so the cards read as a choice, not stacked info boxes
     (owner feedback 2026-07-29). auto-fit lets a third mode wrap. */
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-3);
  align-items: stretch;
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

.remix-mode--on {
  background: var(--active-wash);
  border-color: rgb(var(--v-theme-accent));
}

/* "Offered, with a warning" - deliberately NOT --off: this row can still be
   chosen, so it must not take the opacity drop that says otherwise. */
.remix-mode--caution {
  border-color: rgba(var(--v-theme-warning), 0.5);
}

.remix-mode--caution.remix-mode--on {
  border-color: rgb(var(--v-theme-accent));
}

.remix-mode-icon {
  color: rgb(var(--v-theme-warning));
  vertical-align: -2px;
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

/* Node class names are identifiers, so they take the mono face. Plain text
   rather than chips: twenty chips in a 560px dialog is noise, and it would
   imply an interactivity that is not there. */
.remix-recipe-nodes {
  font-family: var(--font-mono);
  line-height: var(--leading-snug);
  overflow-wrap: anywhere;
}

/* ── Caution banner + acknowledgement ──────────────────────────────────── */
/* Text is on-surface, NOT on-warning: `on-<x>` is only correct on a solid,
   full-opacity `<x>` fill. Over an 8% tint it measures around 1.4:1. The icon
   carries the warning colour, which clears the 3:1 UI floor in both themes. */
.remix-alert {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgba(var(--v-theme-warning), 0.5);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-warning), 0.08);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
}

.remix-alert-icon {
  flex-shrink: 0;
  margin-top: var(--space-1);
  color: rgb(var(--v-theme-warning));
}

/* ── Seed ──────────────────────────────────────────────────────────────── */
.remix-seed-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.remix-seed-note {
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
}

/* The delta stays narrow so the resulting seed fits beside it. */
.remix-num--delta {
  flex: 0 1 110px;
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
  transition: background var(--dur-1) var(--ease-standard);
}

.remix-seg-btn:hover {
  background: var(--hover-wash);
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

.remix-error {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-error));
}

</style>
