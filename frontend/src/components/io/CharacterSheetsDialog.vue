<template>
  <AppDialog
    :open="open"
    :title="title"
    :subtitle="subtitle"
    fullscreen
    :pad-body="false"
    :persistent="held"
    @close="close"
  >
    <template #header-right>
      <AppSelect
        v-if="step"
        v-model="step.workflowId"
        label="Sheet workflow"
        hide-label
        compact
        :placeholder="step.workflowsPending ? 'Checking workflows…' : 'No sheet workflow'"
        :disabled="!step.workflowOptions.length"
        :options="step.workflowOptions"
      />
    </template>

    <CharacterSheetStep
      v-if="open && (character || item)"
      ref="step"
      :kind="kind"
      :character="character"
      :picture-count="character?.image_count ?? 0"
      :saved="savedFor()"
      @cut="onCut"
      @done="onItemDone"
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
      <!-- Esc and the X keep the draw for later; this drops it. -->
      <AppButton
        :disabled="held"
        tooltip="Stop the sheets still drawing and forget this draw"
        data-testid="csd-cancel"
        @click="cancel"
      >
        Cancel
      </AppButton>
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
    v-if="!item"
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
 * dialog finds the sheets and the runs still going. Cancel instead stops the
 * runs and forgets the draw.
 *
 * With a `kind` other than `character` it draws an outfit (worn by
 * `character`), a location or a prop instead: the same step, four angles a
 * sheet (utils/sheetKinds.js), and `done` carries the set the panels went in.
 */
import { computed, nextTick, ref } from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppSelect from "../widgets/AppSelect.vue";
import { ITEM_KINDS } from "../../utils/sheetKinds";
import CharacterSheetStep from "./CharacterSheetStep.vue";
import { useSheetDrawsStore } from "../../stores/useSheetDrawsStore";
import FaceClustersDialog from "./FaceClustersDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `character`, `outfit`, `location` or `prop`. */
  kind: { type: String, default: "character" },
  /** `{ id, name, image_count? }` of the person: whose sheets, or whose
   * outfit; none for a location or a prop. */
  character: { type: Object, default: null },
  returnFocusTo: { type: Object, default: null },
});

const emit = defineEmits(["close", "done"]);
// The draws closed mid-way, by person or item, kept for the next open and
// for the Studio's placeholders.
const sheetDraws = useSheetDrawsStore();

const step = ref(null);
const facesOpen = ref(false);
const facesOpener = ref(null);
const name = computed(() => props.character?.name || "");
const item = computed(() => ITEM_KINDS[props.kind] || null);
const title = computed(() => {
  if (!item.value) return `Character sheets for ${name.value}`;
  return name.value ? `${item.value.title} for ${name.value}` : item.value.title;
});
const subtitle = computed(() => {
  if (item.value) return `a new ${item.value.noun}`;
  return step.value?.loraName ? `with ${step.value.loraName}` : "";
});
/** Where a closed draw is kept: per person for their sheets and outfits, one
 * each for a location and a prop. */
const savedKey = computed(() =>
  item.value ? `${props.kind}:${props.character?.id ?? ""}` : props.character?.id,
);
// Mid-cut the sheets are still on show: closing then would offer them again.
// A zoom or the faces dialog over this one takes Escape and the close itself.
// AppDialog's X ignores `persistent`, so close() checks this too.
const held = computed(
  () => !!step.value?.working || !!step.value?.zooming || facesOpen.value,
);

/** The draw kept for this person, if it is theirs: a deleted person's id can
 * come back on a new one, so the name has to match too. */
function savedFor() {
  const entry = sheetDraws.get(savedKey.value);
  return entry?.name === name.value ? entry.snapshot : null;
}

function close() {
  if (held.value) return;
  const snapshot = step.value?.snapshot;
  if ((props.character || item.value) && snapshot) {
    // Kept where the Studio reads it, for a placeholder while it draws.
    sheetDraws.keep(savedKey.value, {
      name: name.value,
      kind: props.kind,
      character: props.character ? { id: props.character.id, name: name.value } : null,
      label: item.value
        ? snapshot.form?.name?.trim() || item.value.newName
        : `${name.value}'s sheets`,
      snapshot,
    });
  }
  leave();
}

/** Stop what is still drawing and forget the draw: reopening starts fresh.
 * The sheets already drawn stay in the Scrapheap. */
function cancel() {
  if (held.value) return;
  step.value?.stopAll?.();
  sheetDraws.forget(savedKey.value);
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
  sheetDraws.forget(savedKey.value);
  // An item's step goes on to make its set and says so with `done`.
  if (item.value) return;
  emit("done", { character: props.character, pictureIds: ids });
  leave();
}

/** An item's panels are in their set (or, if that failed, only in the
 * library, and the step says so before it is closed). */
function onItemDone(ids, set) {
  // A character's step says done once its faces are read; the dialog left
  // on its cut.
  if (!item.value) return;
  emit("done", { kind: props.kind, character: props.character, pictureIds: ids, set });
  if (set) leave();
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
