<script setup>
/**
 * Wizard step 3 ("Preview") — the accepted mapping, before anything is
 * written. Commits nothing until "Yes, build this library" is pressed; see
 * integration_architecture.md §22. Moves, renames and copies zero files
 * either way — committing registers the folder for in-place indexing and
 * writes database rows only.
 */
import { computed, onUnmounted, ref, watch } from "vue";

import {
  getFolderStructureCommitStatus,
  startFolderStructureCommit,
} from "../../api/folderStructure";
import { errorDetail } from "../../utils/apiError";
import { FACET_KINDS } from "../../utils/folderMappingKinds";
import AppButton from "../widgets/AppButton.vue";

const props = defineProps({
  path: { type: String, required: true },
  readTaskId: { type: String, required: true },
  assignments: { type: Array, required: true },
  label: { type: String, default: "" },
  pictureCount: { type: Number, default: 0 },
});

const emit = defineEmits(["back", "cancel", "committed", "update:committing"]);

const committing = ref(false);
// The wizard makes its dialog undismissable while this is true: a commit,
// once started, runs to completion server-side regardless of what this
// screen does next (§22), so Escape or a backdrop click must not be able to
// quietly abandon the UI while it keeps running — that is what let the same
// read's task id come back through the sidebar's resume flow and get
// committed a second time.
watch(committing, (value) => emit("update:committing", value));
const commitError = ref("");
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

async function commit() {
  committing.value = true;
  commitError.value = "";
  try {
    const started = await startFolderStructureCommit(
      props.readTaskId,
      props.assignments,
      props.label,
    );
    poll(started.task_id);
  } catch (error) {
    committing.value = false;
    commitError.value = errorDetail(error) || "Could not start the import.";
  }
}

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
      <AppButton variant="secondary" size="sm" :disabled="committing" @click="emit('back')">
        Back to the mapping
      </AppButton>
    </div>

    <div class="preview-step__groups">
      <div v-for="kind in FACET_KINDS" :key="kind.value" class="preview-step__group">
        <template v-if="grouped.get(kind.value)?.size">
          <div class="preview-step__group-title">
            <v-icon size="15">{{ kind.icon }}</v-icon>
            {{ grouped.get(kind.value).size }}
            {{ grouped.get(kind.value).size === 1 ? kind.label : kind.plural }}
          </div>
          <div class="preview-step__chips">
            <span v-for="name in [...grouped.get(kind.value).keys()].slice(0, 24)" :key="name" class="preview-step__chip">
              {{ name }}
            </span>
            <span v-if="grouped.get(kind.value).size > 24" class="preview-step__chip preview-step__chip--muted">
              {{ grouped.get(kind.value).size - 24 }} more
            </span>
          </div>
        </template>
      </div>
    </div>

    <div class="preview-step__card">
      <div class="preview-step__card-title">What happens when you press the button</div>
      <div class="preview-step__facts">
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark preview-step__fact-mark--yes">✓</span>
          {{ pictureCount.toLocaleString() }} picture(s) are indexed where they already are
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark preview-step__fact-mark--yes">✓</span>
          {{ ungroupedCount.size }} project(s), set(s) and people are created or matched
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark">—</span>
          no file is copied, moved or renamed
        </div>
        <div class="preview-step__fact">
          <span class="preview-step__fact-mark">—</span>
          no folder is created inside your library
        </div>
      </div>
    </div>

    <p v-if="commitError" class="preview-step__error" role="alert">{{ commitError }}</p>

    <div v-if="committing" class="preview-step__progress">
      <v-progress-circular indeterminate size="18" width="2" color="accent" />
      <span>
        <template v-if="stage === 'indexing'">indexing pictures — {{ processed }} of {{ total }}</template>
        <template v-else-if="stage === 'registering'">registering the folder…</template>
        <template v-else-if="stage === 'assigning'">creating projects, people, sets and tags…</template>
        <template v-else>working…</template>
      </span>
    </div>

    <div class="preview-step__actions">
      <AppButton variant="primary" :loading="committing" @click="commit">
        Yes, build this library
      </AppButton>
      <AppButton variant="secondary" :disabled="committing" @click="emit('back')">
        Back to the mapping
      </AppButton>
      <AppButton variant="ghost" :disabled="committing" @click="emit('cancel')">
        Cancel and organise later
      </AppButton>
    </div>
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
  margin: 0;
  font-family: var(--font-pixel);
  font-size: var(--text-xl);
  font-weight: var(--weight-regular);
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

.preview-step__chip {
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill, 999px);
  background: rgb(var(--v-theme-panel));
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
