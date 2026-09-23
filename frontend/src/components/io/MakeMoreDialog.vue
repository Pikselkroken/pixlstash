<template>
  <AppDialog
    :open="open"
    title="Make more like these"
    :persistent="submitting"
    @close="onRequestClose"
    @accept="submit"
  >
    <p v-if="loadFailed" class="mmd-note mmd-note--bad" role="alert">
      {{ loadFailed }}
    </p>
    <p v-else-if="loading" class="mmd-note" role="status">
      Reading what these would run…
    </p>

    <template v-else>
      <p class="mmd-lede">
        {{ pictureIds.length }}
        {{ pictureIds.length === 1 ? "picture uses" : "pictures use" }}
        <b>{{ groups.length }} different {{ groups.length === 1 ? "recipe" : "recipes" }}</b
        >. Each makes more of its own, with a new seed.
      </p>

      <!-- A group that cannot run stays on screen rather than being filtered
           out: the count would not add up, and nothing would say why. -->
      <div
        v-for="group in groups"
        :key="group.workflow_key || group.picture_ids.join(',')"
        class="mmd-row"
        :class="{ 'mmd-row--off': group.reasons.length }"
      >
        <span class="mmd-thumbs">
          <img
            v-for="id in group.picture_ids.slice(0, 2)"
            :key="id"
            class="mmd-thumb"
            :src="thumbUrl(id)"
            alt=""
          />
        </span>
        <span class="mmd-label">
          {{ nameOf(group) }}
          <span class="mmd-sub">{{ subOf(group) }}</span>
        </span>
        <span class="mmd-count">
          {{ group.picture_ids.length }}
          {{ group.picture_ids.length === 1 ? "picture" : "pictures" }}
        </span>
      </div>

      <div class="mmd-controls">
        <div class="mmd-field">
          <!-- Plain "Count": per recipe for a card that only starts from
               its pictures' recipe, per PICTURE for one its pictures are fed
               into (#1457). The button's total says which it came to. -->
          <span class="mmd-l">Count</span>
          <AppInput
            v-model.number="count"
            aria-label="Count"
            type="number"
            min="1"
            :max="String(MAX_RUNS)"
            :disabled="submitting"
            @keydown.stop
          />
        </div>
        <div class="mmd-field">
          <span class="mmd-l">Seed</span>
          <AppSelect
            v-model="seedMode"
            label="Seed"
            hide-label
            compact
            :options="SEED_OPTIONS"
            :disabled="submitting"
          />
        </div>
      </div>

      <!-- `alert` is assertive and interrupts; a notice about a run that IS
           going ahead is a `status`. Only the presence of a real refusal
           earns the interruption. -->
      <div
        v-if="blockedReasons.length"
        class="mmd-reasons"
        :role="anyRefusal ? 'alert' : 'status'"
      >
      <RunReasonNotice
        v-for="entry in blockedReasons"
        :key="`${entry.key}:${entry.reason.code}`"
        :reason="entry.reason"
        :subject="entry.subject"
        :busy="loading"
        @settings="emit('open-settings', 'compute')"
        @retry="load"
      />
      </div>
      <p v-if="submitError" class="mmd-note mmd-note--bad" role="alert">
        {{ submitError }}
      </p>
    </template>

    <template #footer>
      <p v-if="blocker" :id="blockerId" class="mmd-note mmd-note--bad">
        {{ blocker }}
      </p>
      <span class="mmd-sp" />
      <AppButton :disabled="submitting" @click="onRequestClose">Cancel</AppButton>
      <!-- `aria-disabled`, not `disabled`, so the reason stays reachable by
           keyboard; `submit()` does the refusing. -->
      <AppButton
        variant="primary"
        icon-left="play"
        :loading="submitting"
        :class="{ 'run-refused': !canRun }"
        :aria-disabled="canRun ? undefined : 'true'"
        :aria-describedby="blocker ? blockerId : undefined"
        @click="submit"
      >
        {{ totalRuns > 0 ? `Make ${totalRuns}` : "Make" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * "Make more like these…" over a selection whose pictures do NOT share one
 * recipe (v1.12 F5).
 *
 * When they do share one, the caller opens the single Run popup instead: this
 * dialog exists for the case that one cannot answer, so it offers only what is
 * shared - how many of each, and the seed - and states the recipes the server
 * grouped the selection into.
 */
import { computed, ref, useId, watch } from "vue";

import { pictureThumbnailUrl } from "../../api/pictures";
import {
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
} from "../../api/workflows";
import { errorMessage } from "../../utils/apiError";
import { BLOCKS_BATCH, bypassNotice } from "../../utils/runReasons";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import RunReasonNotice from "./RunReasonNotice.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `{pictureIds}` — see `useRunDialogStore`. */
  source: { type: Object, default: null },
  /** `{client_id, set_id, project_id, character_id}` from the grid. */
  context: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["close", "run", "open-settings"]);

/**
 * `MAX_RUNS_PER_REQUEST` (`pixlstash/routes/comfyui.py`), which `_plan`
 * applies to the TOTAL across every group - so the count alone is not what
 * has to stay under it here.
 */
const MAX_RUNS = 200;
const SEED_OPTIONS = [
  { value: "new", label: "New" },
  { value: "keep", label: "Keep each picture's own" },
];

const blockerId = useId();
const loading = ref(false);
const loadFailed = ref("");
const submitting = ref(false);
const submitError = ref("");
const groups = ref([]);
const names = ref({});
const count = ref(1);
const seedMode = ref("new");

const pictureIds = computed(() => props.source?.pictureIds || []);

const runnable = computed(() =>
  groups.value.filter((group) => !group.reasons.length),
);
/**
 * How many runs this would start: `count` per recipe, **times its pictures
 * where they are fed into it**.
 *
 * `_submit_every` runs each card `count` times, and the pictures only choose
 * which cards run - unless the card takes a picture, when the server feeds each
 * selected picture into it and repeats the run per picture (#1457). Which of
 * the two a card is, is the server's answer (`fill: "selection"` on one of its
 * `picture_inputs`), never worked out here. So three pictures on two recipes at
 * a count of four is eight when neither takes a picture, and twelve when both do.
 */
const totalRuns = computed(() =>
  missingModels.value || !validCount.value
    ? 0
    : runnable.value.reduce((sum, group) => sum + passesOf(group), 0) *
      count.value,
);

/** Whether the server feeds this group's own pictures into its graph. */
function feedsItsPictures(group) {
  return (group.picture_inputs || []).some((input) => input.fill === "selection");
}

/** How many times one `count` runs this group: once, or once per picture. */
function passesOf(group) {
  return feedsItsPictures(group) ? group.picture_ids.length : 1;
}

const validCount = computed(
  () => Number.isInteger(count.value) && count.value > 0,
);

/** The whole request has to fit under the server's ceiling, not each count. */
const overRunCap = computed(() => totalRuns.value > MAX_RUNS);
/** A missing model stops the WHOLE batch, mixed or not — as the server does. */
const missingModels = computed(() =>
  groups.value.some((group) =>
    (group.reasons || []).some((reason) => reason.code === BLOCKS_BATCH),
  ),
);
const blocker = computed(() => {
  if (!validCount.value) return "How many of each? A whole number, at least 1.";
  if (overRunCap.value)
    return `That is ${totalRuns.value} runs; at most ${MAX_RUNS} start at once.`;
  if (missingModels.value) return "Missing models block the whole run.";
  if (!runnable.value.length) return "None of these can run; see the reasons above.";
  return "";
});

const canRun = computed(() => !submitting.value && !blocker.value);

/** Whether anything on screen is a refusal rather than a notice. */
const anyRefusal = computed(() =>
  groups.value.some((group) => (group.reasons || []).length),
);

/**
 * Every refusal, named by the card it is about so a mixed batch reads - and
 * beside them what a card that IS going to run will do differently (#1463).
 *
 * `bypassNotice` is appended here and NOT to `group.reasons`: `runnable` counts
 * groups with no reasons, so a notice put in that list would take a card the
 * server is willing to run out of the total.
 */
const blockedReasons = computed(() =>
  groups.value.flatMap((group) =>
    [...(group.reasons || []), ...bypassNotice(group)].map((reason) => ({
      key: group.workflow_key || group.picture_ids.join(","),
      subject: nameOf(group),
      reason,
    })),
  ),
);

function thumbUrl(id) {
  return pictureThumbnailUrl(id);
}

function nameOf(group) {
  if (!group.workflow_key) return "No workflow";
  return names.value[group.workflow_key] || "Unsaved recipe";
}

function subOf(group) {
  if (group.reasons.length) return "Cannot run";
  return feedsItsPictures(group)
    ? "Its own recipe, run on each of these pictures"
    : "Its own recipe, with a new seed";
}

/** The body both the pre-flight and the run take, so the two never disagree. */
function runBody() {
  const body = {
    picture_ids: pictureIds.value,
    count: count.value,
    seed_mode: seedMode.value,
    client_id: props.context?.client_id || null,
  };
  const destination = {
    set_id: props.context?.set_id ?? null,
    project_id: props.context?.project_id ?? null,
    character_id: props.context?.character_id ?? null,
  };
  if (Object.values(destination).some((value) => value != null)) {
    body.destination = destination;
  }
  return body;
}

/** Bumped per open, so a slower earlier read cannot write over a later one. */
let loadToken = 0;
/** Whether the caller's handed-over pre-flight has been spent. */
let reused = false;

async function load() {
  const token = (loadToken += 1);
  const mine = () => token === loadToken;
  loading.value = true;
  loadFailed.value = "";
  submitError.value = "";
  try {
    // The caller pre-flighted this selection to decide which popup to open,
    // so it already holds the answer; asking again would cost a second
    // /object_info read for one gesture. It is only reused on the FIRST load -
    // a Retry re-asks, which is the whole point of a retry.
    const handed = reused ? null : props.source?.preflight;
    const [answer, library] = await Promise.all([
      handed || preflightWorkflowRun(runBody()),
      listWorkflowCards().catch(() => ({ cards: [] })),
    ]);
    reused = true;
    if (!mine()) return;
    groups.value = (answer?.groups || []).map((group) => ({
      ...group,
      picture_ids: group.picture_ids || [],
      reasons: group.reasons || [],
    }));
    names.value = Object.fromEntries(
      (library.cards || []).map((row) => [row.key, row.name]),
    );
  } catch (err) {
    if (mine()) {
      loadFailed.value = errorMessage(err, "Could not read what these would run.");
    }
  } finally {
    if (mine()) loading.value = false;
  }
}

function onRequestClose() {
  if (submitting.value) return;
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
      groups.value = (answer?.groups || []).map((group) => ({
        ...group,
        picture_ids: group.picture_ids || [],
        reasons: group.reasons || [],
      }));
      submitError.value = "Nothing was queued; see the reason below.";
      return;
    }
    emit("run", { prompts, pictureIds: pictureIds.value });
    emit("close");
  } catch (err) {
    submitError.value = errorMessage(err, "Could not start the runs.");
  } finally {
    submitting.value = false;
  }
}

// The count changes what the pre-flight would report nothing about, so it is
// deliberately NOT a reason to re-ask: only opening, and a retry, read again.
watch(
  () => [props.open, props.source],
  ([open]) => {
    if (!open) return;
    // The controls reset HERE and not in `load()`, which Retry shares: a count
    // of 8 silently becoming 1 because the owner re-asked an unreachable
    // ComfyUI is the button quietly changing what it promises.
    count.value = 1;
    seedMode.value = "new";
    reused = false;
    void load();
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

.mmd-lede {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.mmd-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

/* The thumbnails fade, the WORDS do not. The row's text is "Cannot run" and
   the picture count - the two things it exists to say - and `--text-xs` at
   60% under a further 55% is about a third of the ink the 4:1 floor needs. */
.mmd-row--off .mmd-thumbs {
  opacity: 0.55;
}

.mmd-thumbs {
  display: flex;
  gap: var(--space-1);
}

.mmd-thumb {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  object-fit: cover;
  display: block;
}

.mmd-label {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  font-size: var(--text-sm);
}

.mmd-note--bad {
  color: rgb(var(--v-theme-surface-error));
}

.mmd-sub,
.mmd-count,
.mmd-l,
.mmd-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.mmd-note {
  margin: 0;
  line-height: var(--leading-body);
}

/* One live region around the refusals: see RunReasonNotice. */
.mmd-reasons {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.mmd-controls {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.mmd-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.mmd-sp {
  flex: 1;
}
</style>
