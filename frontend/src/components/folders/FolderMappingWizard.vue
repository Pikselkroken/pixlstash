<script setup>
/**
 * v1.11 Phase 3 - "Add a library", start to finish, in one dialog: point at
 * a folder, hear what it is, and for a folder of pictures with no library
 * yet, read it (Phase 2), name what its levels are, review, and only then
 * build the library.
 *
 * **Nothing exists until "Yes, build this library".** The read runs against a
 * plain folder while some other library is active, so Cancel or the header
 * close anywhere before that point cancels the read, clears any saved entry
 * and creates nothing - the outcome people expect from *Cancel*. "Organise
 * later" is a different offer: it builds the library and indexes everything,
 * leaving only the folder mapping for another day.
 *
 * Building is three things in a row: `addLibrary`, then a `useFolderMappingStore`
 * entry with `autoCommit: true` and the accepted assignments, then the same
 * switch-and-reload `LibrariesSection.switchTo()` uses. The switch reloads the
 * page, and the saved entry (it is `localStorage` backed) is what brings this
 * wizard back on the other side: `SideBar` auto-opens it with `resume` set,
 * straight into the Preview step, which commits on mount. The entry is
 * re-saved without `autoCommit` as soon as that commit has started, so a
 * deferred or interrupted commit resumes through the sidebar's "Finish
 * organising…" row at the scan card, as any saved read does, and never
 * commits twice. Only a completed commit clears the entry.
 *
 * The dialog itself never changes shape between the verdict and the scan:
 * both are drawn in `FolderMappingCard` under the same path field, and the
 * width is one number for every step.
 */
import { computed, ref, watch } from "vue";

import { addLibrary } from "../../api/libraries";
import {
  cancelFolderStructureRead,
  getFolderStructureReadStatus,
} from "../../api/folderStructure";
import { errorDetail } from "../../utils/apiError";
import { useFolderMappingStore } from "../../stores/useFolderMappingStore";
import {
  useLibrariesStore,
  useLibrarySwitchStore,
} from "../../stores/useLibrariesStore";
import AppDialog from "../widgets/AppDialog.vue";
import FolderMappingChooseStep from "./FolderMappingChooseStep.vue";
import FolderMappingTreeStep from "./FolderMappingTreeStep.vue";
import FolderMappingPreviewStep from "./FolderMappingPreviewStep.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  // Resume a saved entry: a read to reattach to, or an `autoCommit` entry
  // saved right before the switch that built the library.
  resume: { type: Object, default: null },
});

const emit = defineEmits(["close", "committed"]);

const mappingStore = useFolderMappingStore();
const librariesStore = useLibrariesStore();
const switchStore = useLibrarySwitchStore();

const step = ref("choose");
const path = ref("");
const label = ref("");
const readTaskId = ref("");
const readResult = ref(null);
const assignments = ref([]);
const pictureCount = ref(0);
// The library exists (a resumed entry) - the Preview step commits directly.
// Before that, the Preview step's "build" is this component's `build()`.
const libraryExists = ref(false);
const autoCommit = ref(false);
const building = ref(false);
const buildError = ref("");
// Bumped on every open, so the step components mount fresh each time rather
// than showing the previous open's answer.
const session = ref(0);
// Mirrors FolderMappingPreviewStep's own `committing`. A commit, once
// started, runs to completion server-side and cannot be un-started, so while
// this is true the dialog must not be dismissable by Escape or a backdrop
// click - see that component's `update:committing` for why.
const committing = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    session.value += 1;
    const entry = props.resume;
    path.value = entry?.path ?? "";
    label.value = entry?.label ?? "";
    readTaskId.value = entry?.taskId ?? "";
    // A read someone else already finished (the desktop startup screen does one
    // while the GPU runtime downloads). Its RESULT travels, not its task: the
    // task lives in the server's memory and the backend restarts before the app
    // loads, so asking for it again answered "Task not found" while the answer
    // was sitting right here.
    readResult.value = entry?.result ?? null;
    assignments.value = entry?.assignments ?? [];
    pictureCount.value = entry?.pictureCount ?? 0;
    libraryExists.value = Boolean(entry);
    autoCommit.value = Boolean(entry?.autoCommit);
    building.value = false;
    buildError.value = "";
    committing.value = false;
    step.value = autoCommit.value
      ? "preview"
      : readResult.value
        ? "mapping"
        : "choose";
    if (!entry) librariesStore.refresh();
    // The read's result did not survive the reload; "Back to the mapping"
    // after a failed commit needs it, and one poll brings it back.
    if (autoCommit.value && entry.taskId) {
      getFolderStructureReadStatus(entry.taskId)
        .then((body) => {
          if (body.result) readResult.value = body.result;
        })
        .catch((error) => {
          console.warn("Could not reload the folder-structure read", {
            taskId: entry.taskId,
            error,
          });
        });
    }
  },
  { immediate: true },
);

const title = computed(() => {
  switch (step.value) {
    case "mapping":
      return "Create the PixlStash database";
    case "preview":
      return "Before anything is written";
    default:
      return "Add a library";
  }
});

function onScanStarted({ path: chosen, label: chosenLabel }) {
  path.value = chosen;
  label.value = chosenLabel;
}

function onTaskStarted(taskId) {
  readTaskId.value = taskId;
  if (!libraryExists.value) return;
  // A resumed read whose entry predates task ids, or was saved without one.
  mappingStore.save({
    taskId,
    path: path.value,
    label: label.value,
    mode: "local_import",
  });
}

function onScanReady({ taskId, result }) {
  readTaskId.value = taskId;
  readResult.value = result;
  pictureCount.value = result.picture_count || 0;
  step.value = "mapping";
}

function onMappingNext(built) {
  assignments.value = built;
  step.value = "preview";
}

/**
 * "Drop this, organise later": index everything, map nothing. For a library
 * that does not exist yet that is `build([])`; for one that does (a resumed
 * read, or the empty library offering its own folder) there is nothing to
 * add, so it is the Preview step's commit with no assignments.
 */
function later() {
  if (!libraryExists.value) {
    build([]);
    return;
  }
  assignments.value = [];
  autoCommit.value = true;
  step.value = "preview";
}

/**
 * The library does not exist yet: create it, remember what to commit, and
 * switch to it. The commit itself runs after the reload - see the header.
 */
async function build(accepted) {
  if (building.value) return;
  building.value = true;
  buildError.value = "";
  try {
    const library = await addLibrary(path.value, label.value);
    assignments.value = accepted;
    mappingStore.save({
      taskId: readTaskId.value,
      path: path.value,
      label: label.value,
      mode: "local_import",
      assignments: accepted,
      pictureCount: pictureCount.value,
      autoCommit: true,
    });
    emit("close");
    await switchStore.begin(library, librariesStore.activeLibrary, null);
  } catch (error) {
    buildError.value = errorDetail(error) || "Could not add that folder.";
  } finally {
    building.value = false;
  }
}

function onCommitStarted() {
  // Started is as good as done for the entry's purposes: from here on a
  // reopen must reattach to the read, not commit it again.
  const entry = mappingStore.pending;
  if (!entry?.autoCommit) return;
  mappingStore.save({
    taskId: entry.taskId,
    path: entry.path,
    label: entry.label,
    mode: entry.mode,
  });
}

function close() {
  // A commit that has started cannot be cancelled (§22) and keeps running
  // server-side either way, so this must be a no-op while `committing` is
  // true. The dialog is `:persistent` for its whole life: Escape belongs to
  // the mapping step (it clears the selection) and a backdrop click must
  // never mean "organise later" by accident. AppDialog's header close button
  // still calls this unconditionally, so the guard lives here.
  if (committing.value) return;
  if (!libraryExists.value) {
    // Nothing was built: leave nothing behind, and stop the read if it is
    // still running - a settled read is kept server-side either way.
    if (readTaskId.value && !readResult.value) {
      cancelFolderStructureRead(readTaskId.value).catch((error) => {
        console.warn("Could not cancel the folder-structure read", {
          taskId: readTaskId.value,
          error,
        });
      });
    }
    mappingStore.clear();
  }
  // A resumed entry is left alone on purpose: its read (or its library) is
  // real, and the sidebar's row must still offer it.
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
    :subtitle="
      step === 'choose'
        ? 'Point PixlStash at a folder. Nothing inside it is moved.'
        : ''
    "
    :width="840"
    :pad-body="step !== 'mapping'"
    :persistent="true"
    @close="close"
  >
    <p v-if="buildError" class="mapping-wizard__error" role="alert">
      {{ buildError }}
    </p>

    <FolderMappingChooseStep
      v-if="step === 'choose'"
      :key="session"
      :resume="resume"
      @scan="onScanStarted"
      @task="onTaskStarted"
      @ready="onScanReady"
      @cancel="close"
      @close="emit('close')"
    />

    <FolderMappingTreeStep
      v-else-if="step === 'mapping' && readResult"
      :result="readResult"
      @next="onMappingNext"
      @later="later"
    />

    <FolderMappingPreviewStep
      v-else-if="step === 'preview'"
      :path="path"
      :read-task-id="readTaskId"
      :read-result="readResult"
      :assignments="assignments"
      :label="label"
      mode="local_import"
      :picture-count="pictureCount"
      :library-exists="libraryExists"
      :commit-on-mount="autoCommit"
      @back="step = 'mapping'"
      @build="build"
      @commit-started="onCommitStarted"
      @cancel="close"
      @committed="onCommitted"
      @update:committing="committing = $event"
    />
  </AppDialog>
</template>

<style scoped>
.mapping-wizard__error {
  /* The mapping step's body is flush; the padding is the dialog's own. */
  margin: 0 0 var(--space-4);
  flex-shrink: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  font-size: var(--text-sm);
}
</style>
