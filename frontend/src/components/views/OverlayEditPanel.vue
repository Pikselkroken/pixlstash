<template>
  <!-- The body of the sidebar's Edit tab (#1381, design "Slim Form"): pick an
       edit workflow, say what should change, run it. The lightbox stays on the
       picture; the result joins its stack and the tab says where it went. -->
  <div class="edit-pane">
    <div v-if="loading" class="edit-note">Reading your workflows…</div>
    <div v-else-if="loadError" class="edit-note">{{ loadError }}</div>

    <div v-else-if="!cards.length" class="edit-empty">
      <p class="edit-empty-lead">
        None of your workflows takes a single picture as its input, so there is
        nothing to run this picture through.
      </p>
      <p class="edit-note">
        An image-to-image, inpaint or outpaint workflow with one open picture
        input shows up here.
      </p>
      <AppButton size="sm" block @click="router.push({ name: 'workflows' })">
        Open Workflows
      </AppButton>
    </div>

    <template v-else>
      <div class="edit-scroll">
        <div class="section-label section-label--on-dark edit-sec">
          <span :id="workflowLabelId">Workflow</span>
          <span v-if="workflowKey === rememberedKey" class="edit-aside"
            >last used</span
          >
        </div>
        <!-- The app's one menu in its on-dark skin, not a native <select>:
             an OS-drawn popup ignores the theme (PluginSelect was a <select>
             briefly for the same reason). A below-opening menu's min-width is
             capped at its activator's, so the oversized floor makes the menu
             exactly as wide as the field. -->
        <v-menu
          v-model="pickerOpen"
          location="bottom start"
          :offset="4"
          :min-width="10000"
          @after-leave="onPickerAfterLeave"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="withRef(menuProps, (el) => (pickerTriggerRef = el))"
              class="edit-select"
              type="button"
              :aria-labelledby="`${workflowLabelId} ${pickerValueId}`"
            >
              <span :id="pickerValueId" class="edit-select-name">{{
                chosenCard?.name || "Untitled workflow"
              }}</span>
              <span class="edit-kind">{{ chosenCard?.type_label }}</span>
              <v-icon class="edit-select-chevron" aria-hidden="true"
                >mdi-chevron-down</v-icon
              >
            </button>
          </template>
          <div
            class="ctx-menu ctx-menu--on-dark"
            role="menu"
            aria-label="Workflow"
            tabindex="-1"
            @keydown="onMenuKeydown"
          >
            <button
              v-for="card in cards"
              :key="card.key"
              class="ctx-item edit-option"
              type="button"
              role="menuitemradio"
              :aria-checked="card.key === workflowKey ? 'true' : 'false'"
              @click="choose(card.key)"
            >
              <span class="ctx-label-text">{{
                card.name || "Untitled workflow"
              }}</span>
              <span class="visually-hidden">, </span>
              <span class="edit-kind">{{ card.type_label }}</span>
            </button>
            <div class="ctx-sep" role="separator"></div>
            <p class="edit-menu-note">
              Upscalers are not listed here. Use More options… for those.
            </p>
          </div>
        </v-menu>

        <label
          :for="instructionFieldId"
          class="section-label section-label--on-dark edit-sec"
        >
          <span>What should change?</span>
        </label>
        <textarea
          :id="instructionFieldId"
          v-model="instruction"
          class="edit-textarea"
          rows="4"
          :aria-describedby="shortcutHintId"
          @keydown="onInstructionKeydown"
        ></textarea>

        <label class="edit-check">
          <input v-model="stack" type="checkbox" />
          Stack with the original
        </label>

        <template v-if="run">
          <div class="section-label section-label--on-dark edit-sec">
            <span>{{ runHeading }}</span>
            <span v-if="run.status === 'running'" class="edit-aside"
              >{{ comfyuiProgressPercent }}%</span
            >
          </div>
          <template v-if="run.status === 'running'">
            <div
              class="edit-bar"
              role="progressbar"
              aria-label="Edit progress"
              :aria-valuenow="comfyuiProgressPercent"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <i :style="{ width: `${comfyuiProgressPercent}%` }"></i>
            </div>
            <p class="edit-note">
              You can keep browsing.
              {{
                run.stack
                  ? "The result joins the original's stack when it lands."
                  : "The result is saved to your library when it lands."
              }}
            </p>
          </template>
          <p v-else-if="run.status === 'failed'" class="edit-note" role="alert">
            {{ run.message }}
          </p>
          <div v-else class="edit-last">
            <img
              v-if="run.resultId"
              class="edit-last-thumb"
              :src="pictureThumbnailUrl(run.resultId)"
              alt=""
            />
            <div class="edit-last-text">
              <div class="edit-last-instruction">
                {{ run.instruction ? `“${run.instruction}”` : "No instruction" }}
              </div>
              <div class="edit-note">
                {{ run.workflowName }}
                <template v-if="run.stack">· added to the original's stack</template>
              </div>
              <button
                v-if="run.stack"
                type="button"
                class="edit-link"
                @click="showResult"
              >
                Show it
              </button>
              <p v-if="run.message" class="edit-note">{{ run.message }}</p>
            </div>
          </div>
        </template>
      </div>

      <div class="edit-foot">
        <p v-if="submitError" class="edit-note" role="alert">
          {{ submitError }}
        </p>
        <AppButton
          variant="primary"
          size="sm"
          block
          :loading="submitting"
          @click="submit"
        >
          <v-icon v-if="!submitting" size="16">mdi-play</v-icon>
          Run edit
        </AppButton>
        <div class="edit-foot-row">
          <button type="button" class="edit-link" @click="onMoreOptions">
            More options…
          </button>
          <span :id="shortcutHintId" class="edit-note">Ctrl + Enter</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
/**
 * The Edit tab of the lightbox sidebar (#1381).
 *
 * Built to the "Slim Form" design: three fields and a button. Everything past
 * the instruction (LoRAs, seed, size) stays in the Run popup, one click away
 * behind *More options…*, which opens it with this picture, this workflow and
 * the typed instruction filled in.
 *
 * **Which cards count.** Image to Image, Inpaint and Outpaint only - the card
 * types whose graph starts from a picture and takes an instruction. Upscalers
 * are left out (a form with a dead text box) and stay on *Use as input for…*.
 * `GET /workflows` does not say how many picture inputs a card leaves open, so
 * the list filters on type and the run's own answer is the check: a card with
 * two open inputs queues nothing and comes back with `picture_input_unfilled`,
 * which is shown under the button.
 *
 * **After a run the lightbox stays put.** The run is handed to the grid's
 * progress runner WITHOUT a source picture, because a source picture is what
 * makes the runner step the lightbox to the newest stack member when the
 * output lands - the Run popup's behaviour, and the opposite of this design.
 * *Show it* does that step when asked.
 *
 * One run is shown at a time. A second Run while the first is going is queued
 * by ComfyUI as usual, and the tab follows the newest.
 */
import { computed, onBeforeUnmount, ref, useId, watch } from "vue";
import { useRouter } from "vue-router";
import AppButton from "../widgets/AppButton.vue";
import { listWorkflowCards, runWorkflowCard } from "../../api/workflows";
import { getPictureMetadata, pictureThumbnailUrl } from "../../api/pictures";
import { listStackPictures } from "../../api/stacks";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { errorMessage } from "../../utils/apiError";
import { readReason } from "../../utils/runReasons";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import { withRef } from "../../utils/withRef";
import { selectNewestStackMember } from "../../utils/stack";

const props = defineProps({
  /** The open picture. */
  pictureId: { type: [Number, String], default: null },
  /** Whether the tab is the one on screen; the card list is read on first show. */
  active: { type: Boolean, default: false },
  /** The grid's ComfyUI progress, `{status, message}`. */
  comfyuiProgress: { type: Object, default: null },
  comfyuiProgressPercent: { type: Number, default: 0 },
});

const emit = defineEmits(["show-picture", "more-options"]);
const router = useRouter();

/** The card types this tab lists, as `workflow_type` spells them. */
const EDIT_TYPES = new Set(["img2img", "inpaint", "outpaint"]);

const LAST_KEY_PREFIX = "pixlstash:editTabWorkflow:";

const workflowLabelId = useId();
const pickerValueId = useId();
const instructionFieldId = useId();
const shortcutHintId = useId();

const runDialog = useRunDialogStore();
const libraries = useLibrariesStore();

const cards = ref([]);
const loaded = ref(false);
const loading = ref(false);
const loadError = ref("");
const workflowKey = ref("");
const rememberedKey = ref("");
// Kept across filmstrip steps on purpose, so one instruction can be run on
// several pictures in a row. It is never filled from the picture's recipe.
const instruction = ref("");
const stack = ref(true);
const submitting = ref(false);
const submitError = ref("");
/**
 * The run this tab started last, or null.
 * `{sourceId, workflowName, instruction, stack, status, message, beforeIds,
 * resultId}` - `status` is running | done | failed.
 */
const run = ref(null);

const pickerOpen = ref(false);
const pickerTriggerRef = ref(null);
// The chosen row leaves with the menu and takes focus to <body>; the field is
// refocused once the close transition ends, as PluginSelect does.
let refocusPicker = false;

const chosenCard = computed(
  () => cards.value.find((card) => card.key === workflowKey.value) || null,
);

function choose(key) {
  workflowKey.value = key;
  pickerOpen.value = false;
  refocusPicker = true;
}

function onPickerAfterLeave() {
  if (!refocusPicker) return;
  refocusPicker = false;
  pickerTriggerRef.value?.focus?.();
}

const storageKey = computed(
  () => `${LAST_KEY_PREFIX}${libraries.activeLibrary?.uuid || ""}`,
);

// The library list can arrive after the cards did (a lightbox opened from a
// deep link), which moves the key from "no library" to the real one. Read the
// remembered card again under it, or the first run would be remembered under
// one key and looked up under another. A library SWITCH reloads the page.
watch(storageKey, () => {
  if (!loaded.value) return;
  rememberedKey.value = readRemembered();
  if (cards.value.some((card) => card.key === rememberedKey.value)) {
    workflowKey.value = rememberedKey.value;
  }
});

const runHeading = computed(() => {
  if (run.value?.status === "running") return "Running";
  if (run.value?.status === "failed") return "Edit failed";
  return "Last edit";
});

function readRemembered() {
  try {
    return window.localStorage?.getItem(storageKey.value) || "";
  } catch (err) {
    console.warn("Could not read the Edit tab's last workflow:", err);
    return "";
  }
}

function remember(key) {
  rememberedKey.value = key;
  try {
    window.localStorage?.setItem(storageKey.value, key);
  } catch (err) {
    console.warn("Could not remember the Edit tab's workflow:", err);
  }
}

async function loadCards() {
  if (loaded.value || loading.value) return;
  loading.value = true;
  loadError.value = "";
  try {
    // One-offs too: a workflow pulled from ComfyUI with few pictures is one,
    // and it is exactly the edit workflow somebody has only just added.
    const { cards: all } = await listWorkflowCards({ includeOneOffs: true });
    cards.value = all.filter((card) => EDIT_TYPES.has(card.type));
    rememberedKey.value = readRemembered();
    const known = cards.value.some((card) => card.key === rememberedKey.value);
    // The last one used from this tab, else the first Image to Image card in
    // the grid's own order, else whatever edit card comes first.
    workflowKey.value = known
      ? rememberedKey.value
      : (cards.value.find((card) => card.type === "img2img") || cards.value[0])
          ?.key || "";
    loaded.value = true;
  } catch (err) {
    loadError.value = errorMessage(err, "Could not read your workflows.");
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.active,
  (active) => {
    if (active) void loadCards();
  },
  { immediate: true },
);

/** The source picture's stack members' ids, or `[source]` when it has none. */
async function stackMemberIds(sourceId) {
  const meta = await getPictureMetadata(sourceId);
  const stackId = meta?.stack_id ?? meta?.stackId ?? null;
  if (stackId == null) return { members: [], ids: new Set([String(sourceId)]) };
  const members = (await listStackPictures(stackId)) || [];
  return { members, ids: new Set(members.map((row) => String(row.id))) };
}

/**
 * The picture this run made: the newest stack member that was not there
 * before the run. Null while the output has not been imported yet.
 */
async function findResult(current) {
  const { members } = await stackMemberIds(current.sourceId);
  const fresh = members.filter((row) => !current.beforeIds.has(String(row.id)));
  return selectNewestStackMember(fresh)?.id ?? null;
}

let resolveTimer = null;
// ponytail: polls the stack a few times after ComfyUI finishes, because the
// output is imported a moment later; a `picture_imported` hook would be exact.
const RESOLVE_ATTEMPTS = 5;
const RESOLVE_DELAY_MS = 1500;

async function resolveResult(current, attempt = 1) {
  if (run.value !== current || !current.stack) return;
  try {
    current.resultId = await findResult(current);
  } catch (err) {
    console.warn(
      `Could not look up the result of the edit on picture ${current.sourceId}:`,
      err,
    );
  }
  if (run.value !== current) return;
  run.value = { ...current };
  if (!current.resultId && attempt < RESOLVE_ATTEMPTS) {
    resolveTimer = setTimeout(
      () => resolveResult(run.value, attempt + 1),
      RESOLVE_DELAY_MS,
    );
  }
}

// The grid's runner is the one follower of ComfyUI progress, so this tab reads
// its state rather than opening a socket of its own.
watch(
  () => props.comfyuiProgress?.status,
  (status) => {
    const current = run.value;
    if (!current || current.status !== "running") return;
    if (status === "completed") {
      run.value = { ...current, status: "done" };
      void resolveResult(run.value);
    } else if (status === "failed") {
      run.value = {
        ...current,
        status: "failed",
        message: props.comfyuiProgress?.message || "ComfyUI could not run it.",
      };
    }
  },
);

onBeforeUnmount(() => clearTimeout(resolveTimer));

async function showResult() {
  const current = run.value;
  if (!current) return;
  if (!current.resultId) {
    try {
      current.resultId = await findResult(current);
    } catch (err) {
      console.warn(
        `Could not look up the result of the edit on picture ${current.sourceId}:`,
        err,
      );
    }
    run.value = { ...current };
  }
  if (current.resultId) {
    emit("show-picture", current.resultId);
  } else {
    run.value = {
      ...current,
      message: "It is not in the stack yet; try again in a moment.",
    };
  }
}

function onInstructionKeydown(event) {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    void submit();
  }
}

function onMoreOptions() {
  const id = Number(props.pictureId);
  if (!Number.isFinite(id) || id <= 0) return;
  emit("more-options", {
    pictureId: id,
    workflowKey: workflowKey.value,
    // Trimmed as Run trims it, so a blank box means the same thing on both
    // paths: no instruction.
    prompt: instruction.value.trim(),
  });
}

async function submit() {
  const id = Number(props.pictureId);
  if (submitting.value || !workflowKey.value) return;
  if (!Number.isFinite(id) || id <= 0) return;
  submitting.value = true;
  submitError.value = "";
  clearTimeout(resolveTimer);
  const card = cards.value.find((row) => row.key === workflowKey.value);
  const text = instruction.value.trim();
  try {
    // Before the run, so the member it adds can be told apart afterwards.
    const before = stack.value
      ? (await stackMemberIds(id)).ids
      : new Set([String(id)]);
    // Filed where the Run popup would file it: the set, project and person in
    // view, which the grid keeps on the store. Sent only when there is one.
    const view = runDialog.context || {};
    const destination = {
      set_id: view.set_id ?? null,
      project_id: view.project_id ?? null,
      character_id: view.character_id ?? null,
    };
    const answer = await runWorkflowCard({
      picture_ids: [id],
      target: workflowKey.value,
      // An empty box leaves the workflow's own prompt alone: `""` would blank
      // every positive prompt node in the graph.
      prompt: text || null,
      stack: stack.value,
      ...(Object.values(destination).some((value) => value != null)
        ? { destination }
        : {}),
      client_id: runDialog.hasRunner
        ? runDialog.context?.client_id || null
        : null,
    });
    const prompts = Array.isArray(answer?.prompts) ? answer.prompts : [];
    if (!prompts.length) {
      const reasons = (answer?.groups || []).flatMap((g) => g.reasons || []);
      const said = reasons.map((reason) => readReason(reason).text);
      submitError.value = said.length
        ? `Nothing was queued: ${said.join(" ")}`
        : "Nothing was queued.";
      return;
    }
    remember(workflowKey.value);
    run.value = {
      sourceId: id,
      workflowName: card?.name || "Untitled workflow",
      instruction: text,
      stack: stack.value,
      status: "running",
      message: "",
      beforeIds: before,
      resultId: null,
    };
    // No picture ids: see the component's docstring.
    runDialog.started(prompts, []);
  } catch (err) {
    submitError.value = errorMessage(err, "Could not start the edit.");
  } finally {
    submitting.value = false;
  }
}

defineExpose({ submit });
</script>

<style scoped>
.edit-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.edit-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

.edit-sec {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-5) 0 var(--space-3);
}

.edit-scroll > .edit-sec:first-child {
  padding-top: 0;
}

.edit-aside {
  text-transform: none;
  letter-spacing: normal;
  font-weight: var(--weight-medium);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.edit-select,
.edit-textarea {
  width: 100%;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  font-family: inherit;
  font-size: var(--text-sm);
}

/* The field box, as a button so it opens the app's menu. */
.edit-select {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--control-h);
  padding: 0 var(--space-3);
  text-align: left;
  cursor: pointer;
}

.edit-select:focus-visible {
  outline-color: rgb(var(--v-theme-on-dark-surface));
}

.edit-select-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.edit-select-chevron {
  flex-shrink: 0;
  font-size: var(--gutter-glyph);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

/* The workflow's type, as the design's small chip on the field and each row. */
.edit-kind {
  flex-shrink: 0;
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
  white-space: nowrap;
}

.edit-option[aria-checked="true"] {
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
}

.edit-menu-note {
  margin: 0;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
  white-space: normal;
}

.edit-textarea {
  display: block;
  min-height: 76px;
  padding: var(--space-3);
  line-height: var(--leading-snug);
  resize: vertical;
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.4) transparent;
}

.edit-check {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-4);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-check input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
}

.edit-bar {
  height: 4px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
  overflow: hidden;
}

.edit-bar i {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-dark-surface-accent));
  transition: width var(--dur-2) var(--ease-standard);
}

.edit-note {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.edit-empty-lead {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-empty .edit-note {
  margin-bottom: var(--space-4);
}

.edit-last {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
}

.edit-last-thumb {
  width: 48px;
  height: 32px;
  flex: none;
  object-fit: cover;
  border-radius: var(--radius-sm);
}

.edit-last-text {
  flex: 1;
  min-width: 0;
}

.edit-last-text .edit-note {
  margin-top: var(--space-1);
}

.edit-last-instruction {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-dark-surface));
  overflow-wrap: anywhere;
}

.edit-link {
  padding: 0;
  font-family: inherit;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.edit-link:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-foot {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  margin-top: var(--space-4);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
}

.edit-foot .edit-note {
  margin: 0;
}

.edit-foot-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
