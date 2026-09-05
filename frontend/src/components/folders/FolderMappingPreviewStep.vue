<script setup>
/**
 * Wizard step 3 ("Preview") - the accepted mapping, before anything is
 * written. Commits nothing until "Yes, build this library" is pressed; see
 * integration_architecture.md §22. Moves, renames and copies zero files
 * either way - committing registers the folder for in-place indexing and
 * writes database rows only.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import {
  getFolderStructureCommitStatus,
  startFolderStructureCommit,
  stopFolderStructureCommit,
} from "../../api/folderStructure";
import { errorDetail } from "../../utils/apiError";
import { FACET_KINDS, kindStyle } from "../../utils/folderMappingKinds";
import AppButton from "../widgets/AppButton.vue";

const props = defineProps({
  path: { type: String, required: true },
  readTaskId: { type: String, required: true },
  /**
   * The read's own result, for a commit whose task no longer exists. The
   * desktop's first run reads the folder on one server process and restarts
   * onto the GPU runtime before the owner answers, so the task is gone and the
   * result is all there is. Ignored whenever `readTaskId` is set.
   */
  readResult: { type: Object, default: null },
  assignments: { type: Array, required: true },
  label: { type: String, default: "" },
  pictureCount: { type: Number, default: 0 },
  // "reference" registers the scanned root as an external reference folder;
  // "local_import" imports its pictures as ordinary managed pictures of the
  // active library instead (v1.11 Phase 3, "Bring them in" on a freshly
  // created library - integration_architecture.md §22).
  mode: { type: String, default: "reference" },
  // Whether the library this commit writes into exists yet. "Add a library"
  // shows this step BEFORE building the library: then "Yes, build this
  // library" and "Organise later" emit `build` with the assignments to send
  // and nothing is committed here. Once the library exists (the wizard is
  // resumed after the switch) they commit directly, as ever.
  libraryExists: { type: Boolean, default: true },
  // Resumed right after the switch that built the library: commit the
  // `assignments` as soon as this step mounts, so the owner lands on the
  // running import rather than on a button they already pressed.
  commitOnMount: { type: Boolean, default: false },
});

const emit = defineEmits([
  "back",
  "build",
  "cancel",
  "committed",
  "commit-started",
  "update:committing",
]);

const committing = ref(false);
// The wizard makes its dialog undismissable while this is true: a commit,
// once started, runs to completion server-side regardless of what this
// screen does next (§22), so Escape or a backdrop click must not be able to
// quietly abandon the UI while it keeps running - that is what let the same
// read's task id come back through the sidebar's resume flow and get
// committed a second time.
watch(committing, (value) => emit("update:committing", value));
const commitError = ref("");
const commitTaskId = ref("");
const stage = ref("");
const processed = ref(0);
const total = ref(0);

let pollTimer = null;
let disposed = false;

const grouped = computed(() => {
  const byKind = new Map(FACET_KINDS.map((k) => [k.value, new Map()]));
  for (const assignment of props.assignments) {
    const bucket = byKind.get(assignment.kind);
    if (!bucket) continue;
    const name = assignment.relative_path.split("/").pop();
    if (!bucket.has(name)) bucket.set(name, assignment);
  }
  return byKind;
});

// "No folder is created inside your library" is the reference-folder framing:
// the scanned root stays external and the library's own directory is
// untouched. For a local import the scanned root already IS the library's own
// root (that is the only case the server allows it), so the fact worth
// stating instead is what these pictures become - ordinary library pictures,
// not an external reference folder the owner could later "stop using".
const lastFact = computed(() =>
  props.mode === "local_import"
    ? "these pictures become ordinary pictures of this library, not an external reference folder"
    : "no folder is created inside your library",
);

const ungroupedCount = computed(() => {
  const named = new Set();
  for (const [, bucket] of grouped.value) {
    for (const name of bucket.keys()) named.add(name);
  }
  return named;
});

async function poll(taskId) {
  if (disposed) return;
  try {
    const body = await getFolderStructureCommitStatus(taskId);
    if (disposed) return;
    stage.value = body.stage;
    processed.value = body.processed;
    total.value = body.total;
    if (body.status === "failed") {
      committing.value = false;
      commitError.value = body.error || "The import failed.";
      return;
    }
    if (body.status === "abandoned" || body.status === "deferred") {
      // Neither is a failure and neither is a finished mapping, so this
      // reports no result: the pictures indexed before the stop stay, and
      // the wizard closes leaving the saved read for another day.
      committing.value = false;
      emit("cancel");
      return;
    }
    if (body.status === "completed") {
      committing.value = false;
      emit("committed", body.result);
      return;
    }
    pollTimer = setTimeout(() => poll(taskId), 300);
  } catch (error) {
    if (disposed) return;
    committing.value = false;
    commitError.value = errorDetail(error) || "The import failed.";
  }
}

async function commit(assignments = props.assignments) {
  if (!props.libraryExists) {
    emit("build", assignments);
    return;
  }
  committing.value = true;
  commitError.value = "";
  try {
    const started = await startFolderStructureCommit(
      props.readTaskId,
      assignments,
      props.label,
      props.mode,
      props.readResult,
    );
    commitTaskId.value = started.task_id;
    emit("commit-started", started.task_id);
    poll(started.task_id);
  } catch (error) {
    committing.value = false;
    commitError.value = errorDetail(error) || "Could not start the import.";
  }
}

/**
 * "Organise later" - index everything now, decide what the folders mean some
 * other day.
 *
 * Before the import starts this is a commit with NO assignments, which is the
 * whole correction: closing the wizard instead used to leave a library that
 * had been created and never filled, and an empty library is what "Cancel"
 * means, not what "later" means. Once the import is running the same words
 * mean the same thing - keep every picture already indexed, apply none of the
 * mapping - which is what `stop=defer` does server-side.
 */
async function organiseLater() {
  if (!committing.value) {
    commit([]);
    return;
  }
  try {
    await stopFolderStructureCommit(commitTaskId.value, "defer");
  } catch (error) {
    commitError.value = errorDetail(error) || "Could not stop the import.";
  }
}

/** Give up on the import. What was indexed before now stays indexed. */
async function abort() {
  try {
    await stopFolderStructureCommit(commitTaskId.value, "abort");
  } catch (error) {
    commitError.value = errorDetail(error) || "Could not stop the import.";
  }
}

onMounted(() => {
  if (props.commitOnMount) commit();
});

onUnmounted(() => {
  disposed = true;
  if (pollTimer) clearTimeout(pollTimer);
});
</script>

<template>
  <div class="preview-step">
    <div class="preview-step__header">
      <div>
        <h2 class="preview-step__title">This is what your folders become</h2>
        <p class="preview-step__lead">nothing written yet</p>
      </div>
      <AppButton
        variant="secondary"
        size="sm"
        :disabled="committing"
        @click="emit('back')"
      >
        Back to the mapping
      </AppButton>
    </div>

    <div class="preview-step__groups">
      <div
        v-for="kind in FACET_KINDS"
        :key="kind.value"
        class="preview-step__group"
        :style="kindStyle(kind.value)"
      >
        <template v-if="grouped.get(kind.value)?.size">
          <div class="preview-step__group-title">
            <v-icon size="15">{{ kind.icon }}</v-icon>
            {{ grouped.get(kind.value).size }}
            {{ grouped.get(kind.value).size === 1 ? kind.label : kind.plural }}
          </div>
          <div class="preview-step__chips">
            <span
              v-for="name in [...grouped.get(kind.value).keys()].slice(0, 24)"
              :key="name"
              class="preview-step__chip"
            >
              {{ name }}
            </span>
            <span
              v-if="grouped.get(kind.value).size > 24"
              class="preview-step__chip preview-step__chip--muted"
            >
              {{ grouped.get(kind.value).size - 24 }} more
            </span>
          </div>
        </template>
      </div>
    </div>

    <div class="preview-step__card">
      <div class="preview-step__card-title">
        What happens when you press the button
      </div>
      <div class="preview-step__facts">
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark preview-step__fact-mark--yes"
            >✓</span
          >
          {{ pictureCount.toLocaleString() }} picture(s) are indexed where they
          already are
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark preview-step__fact-mark--yes"
            >✓</span
          >
          {{ ungroupedCount.size }} project(s), set(s) and people are created or
          matched
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark"> - </span>
          no file is copied, moved or renamed
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark"> - </span>
          {{ lastFact }}
        </div>
      </div>
    </div>

    <p v-if="commitError" class="preview-step__error" role="alert">
      {{ commitError }}
    </p>

    <div v-if="committing" class="preview-step__progress">
      <v-progress-circular indeterminate size="18" width="2" color="accent" />
      <span>
        <template v-if="stage === 'indexing'"
          >indexing pictures - {{ processed }} of {{ total }}</template
        >
        <template v-else-if="stage === 'registering'"
          >registering the folder…</template
        >
        <template v-else-if="stage === 'assigning'"
          >creating projects, people, sets and tags…</template
        >
        <template v-else>working…</template>
      </span>
    </div>

    <div class="preview-step__actions">
      <AppButton v-if="!committing" variant="primary" @click="commit()">
        Yes, build this library
      </AppButton>
      <AppButton v-if="!committing" variant="secondary" @click="emit('back')">
        Back to the mapping
      </AppButton>
      <!-- Organise later stays available WHILE the import runs: it is the
           answer to "this is taking ages and I do not want to watch", and it
           means the same thing at both moments - index it all, map it later.
           Both stops are disabled for the seconds between `committing` going
           true and the server answering with a task id: there is nothing to
           stop yet, and pressing them then sent `stop("")`, which fails with
           "Could not stop the import." while the mapping commits anyway.
           Disabled rather than a silent early return, because a live button
           that does nothing is the same bug with the error message removed. -->
      <AppButton
        variant="secondary"
        :disabled="committing && !commitTaskId"
        @click="organiseLater"
      >
        Organise later
      </AppButton>
      <AppButton
        v-if="committing"
        variant="ghost"
        :disabled="!commitTaskId"
        @click="abort"
      >
        Abort
      </AppButton>
      <AppButton v-else variant="ghost" @click="emit('cancel')">
        Cancel
      </AppButton>
    </div>
    <p class="preview-step__actions-note">
      <template v-if="committing">
        Organise later keeps every picture indexed so far and leaves the folder
        mapping for another day. Abort gives up on the import - nothing already
        indexed is removed, and no file is touched either way.
      </template>
      <template v-else>
        Organise later brings the pictures in now and leaves naming the folders
        until later. Cancel brings nothing in at all.
      </template>
    </p>
  </div>
</template>

<style scoped>
.preview-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  overflow-y: auto;
}

.preview-step__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.preview-step__title {
  /* NOT --font-pixel: a step heading is chrome. Tiny5 is the wordmark, a brand
     moment and an empty-state headline - nothing a person reads a sentence in. */
  margin: 0;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.preview-step__lead {
  margin: var(--space-1) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.65);
}

.preview-step__groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.preview-step__group-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  margin-bottom: var(--space-2);
}

.preview-step__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.preview-step__group-title .v-icon {
  color: rgb(var(--kind));
}

.preview-step__chip {
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill, 999px);
  background: rgb(var(--v-theme-panel));
  box-shadow: inset 3px 0 0 rgb(var(--kind));
  font-size: var(--text-xs);
}

.preview-step__chip--muted {
  color: rgba(var(--v-theme-on-background), 0.55);
}

.preview-step__card {
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.preview-step__card-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  margin-bottom: var(--space-4);
}

.preview-step__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.preview-step__fact {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.preview-step__fact-mark {
  color: rgba(var(--v-theme-on-background), 0.5);
}

.preview-step__fact-mark--yes {
  color: rgb(var(--v-theme-success));
}

.preview-step__actions-note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.65);
}

.preview-step__error {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  font-size: var(--text-sm);
}

.preview-step__progress {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.72);
}

.preview-step__actions {
  display: flex;
  gap: var(--space-3);
}
</style>
