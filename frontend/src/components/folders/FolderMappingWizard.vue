<script setup>
/**
 * v1.11 Phase 3 - the import and mapping wizard: choose a folder, scan it
 * (Phase 2), name what its levels are, and review before anything commits.
 *
 * **"Cancel" and "Organise later" are different offers, and used to be one
 * button.** "Cancel and organise later" closed the dialog having imported
 * nothing, which on the "Add a library" path left the owner looking at a
 * library that had just been created and was completely empty - the outcome
 * people expect from *Cancel*, under a label promising the opposite. So:
 * Cancel brings nothing in, and Organise later (on the Preview step, and
 * again while the import runs) indexes everything and leaves only the folder
 * mapping for another day.
 *
 * Either way the read survives. Once a read has started, its task id is saved
 * to `useFolderMappingStore` - the server keeps the read's result in memory
 * for the process's lifetime (integration_architecture.md §20) - so closing
 * this dialog before committing does not lose the scan; the sidebar's "Finish
 * organising…" entry reopens this same wizard with `resume` set and picks up
 * where it left off. Only a completed commit clears that saved entry, because
 * only then is there nothing left to resume.
 *
 * `mode` ("reference" default, or "local_import") decides what the Preview
 * step's commit does with the scanned pictures - see integration_architecture.md
 * §22. "Add a library"'s "pictures" verdict drives `local_import`: it saves a
 * `resume` entry with an empty `taskId` and `mode: "local_import"` *before*
 * switching the active library and reloading, so the sidebar's own resume
 * mechanism reopens this wizard already pointed at the new library's root -
 * an empty `taskId` in `resume` is not "reattach", it is "start scanning this
 * known path fresh" (`FolderMappingScanStep` already treats a falsy
 * `resumeTaskId` that way).
 */
import { computed, ref, watch } from "vue";

import { useFolderMappingStore } from "../../stores/useFolderMappingStore";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import FolderMappingScanStep from "./FolderMappingScanStep.vue";
import FolderMappingTreeStep from "./FolderMappingTreeStep.vue";
import FolderMappingPreviewStep from "./FolderMappingPreviewStep.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  registeredPaths: { type: Array, default: () => [] },
  // Resume a previously started, not-yet-committed read.
  resume: { type: Object, default: null },
  // What the Preview step's commit does with the scan - ignored (in favour of
  // `resume.mode`) when resuming, since the mode was fixed the moment the
  // saved entry was created.
  mode: { type: String, default: "reference" },
});

const emit = defineEmits(["close", "committed"]);

const mappingStore = useFolderMappingStore();

const step = ref("choose");
const path = ref("");
const label = ref("");
const browserOpen = ref(false);
const readTaskId = ref("");
const resumeTaskId = ref("");
const readResult = ref(null);
const assignments = ref([]);
// "Drop this, organise later" from the mapping step: the Preview step's own
// Organise later (a commit with no assignments), started the moment it mounts.
const autoLater = ref(false);
const currentMode = ref(props.mode);
// Mirrors FolderMappingPreviewStep's own `committing`. A commit, once
// started, runs to completion server-side and cannot be un-started, so while
// this is true the dialog must not be dismissable by Escape or a backdrop
// click - see that component's `update:committing` for why.
const committing = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    if (props.resume) {
      path.value = props.resume.path;
      label.value = props.resume.label || "";
      resumeTaskId.value = props.resume.taskId;
      readTaskId.value = props.resume.taskId;
      // Absent on entries saved before `mode` existed - those were always
      // reference-folder reads.
      currentMode.value = props.resume.mode || "reference";
      step.value = "scan";
    } else {
      path.value = "";
      label.value = "";
      resumeTaskId.value = "";
      readTaskId.value = "";
      currentMode.value = props.mode;
      step.value = "choose";
      browserOpen.value = true;
    }
    readResult.value = null;
    assignments.value = [];
    autoLater.value = false;
    committing.value = false;
  },
  { immediate: true },
);

const title = computed(() => {
  switch (step.value) {
    case "scan":
      return "Here is what is in that folder";
    case "mapping":
      return "Name what your folders are";
    case "preview":
      return "Before anything is written";
    default:
      return "Where should your pictures live?";
  }
});

function chooseFolder(selected) {
  browserOpen.value = false;
  path.value = selected;
  label.value = selected.split(/[\\/]/).filter(Boolean).pop() || selected;
  step.value = "scan";
}

function onTaskStarted(taskId) {
  readTaskId.value = taskId;
  mappingStore.save({
    taskId,
    path: path.value,
    label: label.value,
    mode: currentMode.value,
  });
}

function onScanReady({ taskId, result }) {
  readTaskId.value = taskId;
  readResult.value = result;
  step.value = "mapping";
}

function onMappingNext(built) {
  assignments.value = built;
  autoLater.value = false;
  step.value = "preview";
}

function onMappingLater() {
  assignments.value = [];
  autoLater.value = true;
  step.value = "preview";
}

function organiseLater() {
  // A commit that has started cannot be cancelled (§22) and keeps running
  // server-side either way, so this must be a no-op while `committing` is
  // true. The dialog is `:persistent` for its whole life: Escape belongs to
  // the mapping step (it clears the selection) and a backdrop click must
  // never mean "organise later" by accident. AppDialog's header close button
  // still calls this unconditionally, so the guard lives here.
  if (committing.value) return;
  // The store entry is left alone on purpose - see the header comment. There
  // is nothing to keep only when the owner never got past picking a folder.
  emit("close");
}

function onCommitted(result) {
  mappingStore.clear();
  emit("committed", result);
}
</script>

<template>
  <AppDialog
    :open="open"
    :title="title"
    :width="step === 'choose' ? 560 : 840"
    :pad-body="step !== 'mapping'"
    :persistent="true"
    @close="organiseLater"
  >
    <div v-if="step === 'choose'" class="mapping-wizard__choose">
      <p class="mapping-wizard__choose-lead">
        Point PixlStash at a folder you already have. Every file stays
        exactly where it is, and the folder names you already use become the
        organisation.
      </p>
      <div class="mapping-wizard__choose-path">
        <AppInput
          v-model="path"
          class="mapping-wizard__choose-field"
          label="Folder"
          placeholder="/home/me/Pictures"
          @enter="path && chooseFolder(path)"
        />
        <AppButton size="sm" variant="secondary" @click="browserOpen = true">
          Browse…
        </AppButton>
      </div>
      <div class="mapping-wizard__choose-actions">
        <AppButton variant="primary" :disabled="!path" @click="chooseFolder(path)">
          Continue
        </AppButton>
        <AppButton variant="secondary" @click="organiseLater">
          Cancel
        </AppButton>
      </div>
    </div>

    <FolderMappingScanStep
      v-else-if="step === 'scan'"
      :path="path"
      :resume-task-id="resumeTaskId"
      @task="onTaskStarted"
      @ready="onScanReady"
      @cancel="organiseLater"
    />

    <FolderMappingTreeStep
      v-else-if="step === 'mapping' && readResult"
      :result="readResult"
      @next="onMappingNext"
      @later="onMappingLater"
    />

    <FolderMappingPreviewStep
      v-else-if="step === 'preview'"
      :path="path"
      :read-task-id="readTaskId"
      :assignments="assignments"
      :label="label"
      :mode="currentMode"
      :picture-count="readResult?.picture_count || 0"
      :organise-later-on-mount="autoLater"
      @back="step = 'mapping'"
      @cancel="organiseLater"
      @committed="onCommitted"
      @update:committing="committing = $event"
    />
  </AppDialog>

  <FolderBrowser
    :open="browserOpen && step === 'choose'"
    allow-create-folder
    :registered-paths="registeredPaths"
    already-registered-label="Already a reference folder"
    :initial-path="path || null"
    @select="chooseFolder"
    @close="browserOpen = false"
  />
</template>

<style scoped>
.mapping-wizard__choose {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.mapping-wizard__choose-lead {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.72);
}

.mapping-wizard__choose-path {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}

.mapping-wizard__choose-field {
  flex: 1;
  min-width: 0;
}

.mapping-wizard__choose-actions {
  display: flex;
  gap: var(--space-3);
}
</style>
