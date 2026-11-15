<template>
  <AppDialog
    :open="open"
    :title="`Character sheets for ${name}`"
    :subtitle="step?.loraName ? `with ${step.loraName}` : ''"
    fullscreen
    :pad-body="false"
    :persistent="held"
    @close="close"
  >
    <template #header-right>
      <AppSelect
        v-if="step?.workflowOptions?.length"
        v-model="step.workflowId"
        label="Sheet workflow"
        hide-label
        compact
        :options="step.workflowOptions"
      />
    </template>

    <CharacterSheetStep
      v-if="open && character"
      ref="step"
      :character="character"
      :picture-count="character.image_count ?? 0"
      :saved="savedFor(character)"
      @cut="onCut"
      @faces="openFaces"
    />

    <template #footer>
      <span
        class="csd-lead"
        :class="{ 'csd-lead--warn': step?.footer?.warn }"
        :title="step?.footer?.lead"
        :role="step?.footer?.warn ? 'status' : undefined"
        data-testid="csd-lead"
        ><v-icon v-if="step?.footer?.warn" size="16">mdi-alert-outline</v-icon>
        {{ step?.footer?.lead }}</span
      >
      <p v-if="step?.error" class="csd-bad" role="alert">{{ step.error }}</p>
      <AppButton
        v-if="step?.footer?.back"
        :disabled="!!step?.working"
        @click="step.footer.back.run()"
      >
        {{ step.footer.back.label }}
      </AppButton>
      <AppButton
        v-if="step?.footer?.secondary"
        :disabled="!step.footer.secondary.enabled"
        @click="step.footer.secondary.run()"
      >
        {{ step.footer.secondary.label }}
      </AppButton>
      <AppButton
        variant="primary"
        :loading="!!step?.working"
        :disabled="!step?.footer?.primary?.enabled"
        data-testid="csd-primary"
        @click="step.footer.primary.run()"
      >
        {{ step?.footer?.primary?.label || "Draw sheets" }}
      </AppButton>
    </template>
  </AppDialog>
  <FaceClustersDialog
    :open="facesOpen"
    :character="character"
    :return-focus-to="facesOpener"
    @close="onFacesClosed"
  />
</template>

<script setup>
/**
 * The person's context menu "Generate character sheets…": the Sheets step of
 * a training set's build screen (CharacterSheetStep) on its own, so sheets
 * are drawn, picked and cut into pictures on the person without making a set.
 * The step owns the work; this dialog only hosts it and its footer, as
 * views/TrainingSetBuild.vue does.
 *
 * Closing mid-draw keeps the step's `snapshot` per person, so reopening the
 * dialog finds the sheets and the runs still going.
 */
import { computed, nextTick, ref } from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppSelect from "../widgets/AppSelect.vue";
import CharacterSheetStep from "./CharacterSheetStep.vue";
import FaceClustersDialog from "./FaceClustersDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `{ id, name, image_count? }` of the person. */
  character: { type: Object, default: null },
  returnFocusTo: { type: Object, default: null },
});

const emit = defineEmits(["close", "done"]);

const step = ref(null);
const facesOpen = ref(false);
const facesOpener = ref(null);
const name = computed(() => props.character?.name || "");
// Mid-cut the sheets are still on show: closing then would offer them again.
// A zoom or the faces dialog over this one takes Escape and the close itself.
// AppDialog's X ignores `persistent`, so close() checks this too.
const held = computed(
  () => !!step.value?.working || !!step.value?.zooming || facesOpen.value,
);

/** The draw kept for this person, if it is theirs: a deleted person's id can
 * come back on a new one, so the name has to match too. */
function savedFor(character) {
  const entry = saved.get(character.id);
  return entry?.name === character.name ? entry.snapshot : null;
}

function close() {
  if (held.value) return;
  const snapshot = step.value?.snapshot;
  if (props.character && snapshot) {
    saved.set(props.character.id, { name: props.character.name, snapshot });
  }
  leave();
}

function leave() {
  emit("close");
  const row = props.returnFocusTo;
  nextTick(() => {
    if (row?.isConnected) row.focus();
  });
}

/** The sheets are cut into pictures on the person: the draw is finished.
 * The build screen then waits for their faces for its picker; nothing here
 * reads them, so the dialog closes at once. */
function onCut(ids) {
  saved.delete(props.character.id);
  emit("done", { character: props.character, pictureIds: ids });
  leave();
}

function openFaces(event) {
  facesOpener.value = event?.currentTarget ?? null;
  facesOpen.value = true;
}

/** The reference may have moved: the step redraws its reference faces. */
function onFacesClosed() {
  facesOpen.value = false;
  step.value?.reloadReferences?.();
}
</script>

<script>
/** person id -> `{name, snapshot}`: the sheet step's last `snapshot`,
 * shared by every mount.
 * ponytail: held for the app session only; a reload forgets an unfinished
 * draw (its sheets stay in the Scrapheap). Store it on the character if that
 * turns out to matter. */
const saved = new Map();
</script>

<style scoped>
.csd-lead {
  display: block;
  min-width: 0;
  flex: 1 1 auto;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.csd-lead--warn {
  color: rgb(var(--v-theme-on-surface));
}

.csd-lead--warn .v-icon {
  color: rgb(var(--v-theme-surface-warning));
  vertical-align: -3px;
}

.csd-bad {
  margin: 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
}
</style>
