<script setup>
/**
 * The folder-structure read (Phase 2), shown in the same card the folder's
 * verdict was: "Working out what your folders mean", then what it found.
 * Nothing is written here; see integration_architecture.md §20. Starts the
 * read on mount (or reattaches to `resumeTaskId`) and polls it to
 * completion. Cancelling the read is the wizard's job - it is the one that
 * knows whether there is anything else to undo.
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { VProgressCircular } from "vuetify/components";

import {
  getFolderStructureReadStatus,
  startFolderStructureRead,
} from "../../api/folderStructure";
import { errorDetail } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import FolderMappingCard from "./FolderMappingCard.vue";

const props = defineProps({
  path: { type: String, required: true },
  // Resume an already-started (or already-settled) read instead of starting
  // a new one - the sidebar's "finish organising" re-enters here.
  resumeTaskId: { type: String, default: "" },
  // Passed through to the read as `match_existing`. False before the library
  // exists - see api/folderStructure.js.
  matchExisting: { type: Boolean, default: true },
});

const emit = defineEmits(["task", "ready", "cancel"]);

const taskId = ref("");
const status = ref("");
const stage = ref("");
const processed = ref(0);
const total = ref(0);
const result = ref(null);
const loadError = ref("");

let pollTimer = null;
let disposed = false;

const isDone = computed(() => status.value === "completed" || status.value === "cancelled");
const isIndeterminate = computed(() => stage.value === "walking" || total.value === 0);
const progressPercent = computed(() =>
  total.value ? Math.min(100, Math.round((processed.value / total.value) * 100)) : 0,
);

const summary = computed(() => {
  if (!result.value) return null;
  const tallies = { project: 0, person: 0, set: 0, tag: 0, folder: 0 };
  let narrowed = 0;
  let silent = 0;
  for (const level of result.value.levels || []) {
    // Level 1 is always the single root folder and never carries a reading
    // (integration_architecture.md §20) - counting it as "silent" would be
    // noise on every single scan, not a finding.
    if (level.depth === 1) continue;
    for (const folder of level.folders || []) {
      const kind = folder.proposal?.kind;
      const candidates = folder.proposal?.candidates || [];
      if (kind && tallies[kind] !== undefined) tallies[kind] += 1;
      else if (candidates.length > 0) narrowed += 1;
      else silent += 1;
    }
  }
  return { tallies, narrowed, silent };
});

async function poll() {
  if (disposed || !taskId.value) return;
  try {
    const body = await getFolderStructureReadStatus(taskId.value);
    if (disposed) return;
    status.value = body.status;
    stage.value = body.stage;
    processed.value = body.processed;
    total.value = body.total;
    if (body.status === "failed") {
      loadError.value = body.error || "The scan failed.";
      return;
    }
    if (body.status === "completed" || body.status === "cancelled") {
      result.value = body.result;
      return;
    }
    pollTimer = setTimeout(poll, 300);
  } catch (error) {
    if (disposed) return;
    loadError.value = errorDetail(error) || "Could not read that folder.";
  }
}

async function begin() {
  loadError.value = "";
  try {
    if (props.resumeTaskId) {
      taskId.value = props.resumeTaskId;
    } else {
      const started = await startFolderStructureRead(props.path, {
        matchExisting: props.matchExisting,
      });
      taskId.value = started.task_id;
    }
    emit("task", taskId.value);
    poll();
  } catch (error) {
    loadError.value = errorDetail(error) || "Could not start scanning that folder.";
  }
}

function proceed() {
  if (!result.value) return;
  emit("ready", { taskId: taskId.value, result: result.value });
}

onMounted(begin);
onUnmounted(() => {
  disposed = true;
  if (pollTimer) clearTimeout(pollTimer);
});
</script>

<template>
  <div class="scan-step">
    <p v-if="loadError" class="scan-step__error" role="alert">{{ loadError }}</p>

    <FolderMappingCard
      :title="isDone ? 'What it found' : 'Working out what your folders mean'"
      :lead="
        isDone
          ? ''
          : 'Reading up to 20 pictures from each folder: who is in them, which have caption files beside them, which names repeat.'
      "
    >
      <template v-if="!isDone">
        <div class="scan-step__bar">
          <VProgressCircular
            v-if="isIndeterminate"
            indeterminate
            size="20"
            width="2"
            color="accent"
          />
          <div v-else class="scan-step__track">
            <div class="scan-step__fill" :style="{ width: progressPercent + '%' }" />
          </div>
        </div>
        <div class="scan-step__progress-note">
          <template v-if="!isIndeterminate">{{ processed }} of {{ total }} folders</template>
          <template v-else>reading the folder tree…</template>
        </div>
      </template>

      <template v-else>
        <div v-if="result" class="scan-step__stats">
          <div class="scan-step__stat">
            <div class="scan-step__stat-value">{{ result.picture_count.toLocaleString() }}</div>
            <div class="scan-step__stat-label">pictures, in {{ result.folder_count.toLocaleString() }} folders</div>
          </div>
          <div v-if="result.truncated" class="scan-step__stat scan-step__stat--warn">
            <div class="scan-step__stat-value">stopped early</div>
            <div class="scan-step__stat-label">the tree was bigger than this pass covers</div>
          </div>
          <div v-if="result.unreadable_folders" class="scan-step__stat scan-step__stat--warn">
            <div class="scan-step__stat-value">{{ result.unreadable_folders }}</div>
            <div class="scan-step__stat-label">folder(s) could not be read</div>
          </div>
        </div>
        <ul v-if="summary" class="scan-step__summary">
        <li v-if="summary.tallies.person">✓ {{ summary.tallies.person }} folder(s) read as Person</li>
        <li v-if="summary.tallies.set">✓ {{ summary.tallies.set }} folder(s) read as Set</li>
        <li v-if="summary.tallies.project">✓ {{ summary.tallies.project }} folder(s) read as Project</li>
        <li v-if="summary.tallies.tag">✓ {{ summary.tallies.tag }} folder(s) read as Tag</li>
        <li v-if="summary.narrowed" class="scan-step__summary-muted">
          - {{ summary.narrowed }} narrowed to a choice - you'll pick
        </li>
          <li v-if="summary.silent" class="scan-step__summary-muted">
            - {{ summary.silent }} with nothing to say
          </li>
        </ul>
      </template>

      <template #actions>
        <AppButton
          variant="primary"
          :loading="!isDone && !loadError"
          :disabled="!isDone"
          @click="proceed"
        >
          Set up my library
        </AppButton>
        <AppButton variant="secondary" @click="emit('cancel')">
          Cancel
        </AppButton>
      </template>
    </FolderMappingCard>
  </div>
</template>

<style scoped>
.scan-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.scan-step__error {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  font-size: var(--text-sm);
}

.scan-step__stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.scan-step__stat {
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.scan-step__stat--warn {
  border-color: rgb(var(--v-theme-warning));
}

.scan-step__stat-value {
  /* NOT --font-pixel. Tiny5 is a 5-pixel display face: "11,886" came out as
     unreadable blocks (visual-language.md - brand-only, never reading text). */
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.scan-step__stat-label {
  margin-top: var(--space-1);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.65);
}

.scan-step__bar {
  display: flex;
  align-items: center;
}

.scan-step__track {
  flex: 1;
  height: 6px;
  border-radius: var(--radius-pill, 999px);
  background: rgb(var(--v-theme-border));
  overflow: hidden;
}

.scan-step__fill {
  height: 100%;
  background: rgb(var(--v-theme-accent));
  transition: width 0.2s ease;
}

.scan-step__progress-note {
  margin-top: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.65);
}

.scan-step__summary {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.scan-step__summary-muted {
  color: rgba(var(--v-theme-on-background), 0.65);
}
</style>
