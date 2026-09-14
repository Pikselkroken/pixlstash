<template>
  <div class="tbm tb-comfyui-panel">
    <span class="tbm-caret tbm-caret--end"></span>
    <div class="tbm-header">
      <v-icon size="18" class="tbm-header-icon">mdi-auto-fix</v-icon>
      <span class="tbm-title">Generate from a text prompt</span>
    </div>

    <div v-if="tbComfyuiWorkflowLoading" class="tbm-section tb-comfyui-note">
      Loading workflows…
    </div>
    <template v-else-if="tbValidComfyWorkflows.length">
      <div class="tbm-section">
        <label class="tbm-field">
          <span class="tbm-label">Workflow</span>
          <div class="tbm-select-wrap">
            <select v-model="tbComfyuiSelectedWorkflow" class="tbm-select">
              <option
                v-for="wf in tbValidComfyWorkflows"
                :key="wf.name"
                :value="wf.name"
              >
                {{ wf.display_name || wf.name }}
              </option>
            </select>
            <v-icon size="18" class="tbm-select-chevron"
              >mdi-chevron-down</v-icon
            >
          </div>
        </label>
      </div>

      <div class="tbm-section">
        <span class="tbm-label">Prompt</span>
        <textarea
          v-model="tbComfyuiCaption"
          class="tbm-textarea"
          rows="4"
          placeholder="Describe the image to generate…"
          @keydown.stop
        ></textarea>
      </div>

      <div class="tbm-section tb-gen-run">
        <div class="tb-gen-seed">
          <span class="tbm-label">Seed</span>
          <div class="tb-gen-seed-row">
            <Segmented
              v-model="tbComfyuiSeedMode"
              :options="SEED_MODE_OPTIONS"
              variant="icon-label"
              full
              class="tb-gen-seed-mode"
              aria-label="Seed mode"
            />
            <input
              v-if="tbComfyuiSeedMode === 'fixed'"
              v-model.number="tbComfyuiSeed"
              type="number"
              class="tbm-num tb-gen-seed-input"
              min="0"
              max="4294967295"
              @keydown.stop
            />
          </div>
        </div>
        <button
          class="tbm-action tbm-action--primary tbm-action--lg"
          type="button"
          :disabled="!tbCanRunComfyWorkflow"
          @click="tbRunComfyuiOnGrid"
        >
          <v-icon size="16">mdi-play</v-icon>
          Run
        </button>
      </div>
    </template>
    <div v-else class="tbm-section tb-comfyui-note">
      No valid T2I workflows found.
    </div>

    <div
      v-if="tbComfyuiWorkflowError || tbComfyuiRunError"
      class="tbm-section tb-comfyui-error"
    >
      {{ tbComfyuiWorkflowError || tbComfyuiRunError }}
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from "vue";
import { listWorkflows } from "../../api/comfyui";
import { errorDetail } from "../../utils/apiError";
import { API_BASE_URL } from "../../utils/apiClient";
import Segmented from "../widgets/Segmented.vue";
const props = defineProps({
  backendUrl: { type: String, default: () => API_BASE_URL },
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["run-grid"]);

const tbComfyuiWorkflows = ref([]);
const tbComfyuiWorkflowLoading = ref(false);
const tbComfyuiWorkflowError = ref("");
const tbComfyuiSelectedWorkflow = ref("");
const tbComfyuiCaption = ref("");
const tbComfyuiRunError = ref("");
const tbComfyuiSeedMode = ref(
  sessionStorage.getItem("comfyui_t2i_seed_mode") === "fixed"
    ? "fixed"
    : "random",
);
const SEED_MODE_OPTIONS = [
  { id: "random", label: "Random", icon: "dice-multiple-outline" },
  { id: "fixed", label: "Fixed", icon: "lock-outline" },
];
const _tbSavedSeed = Number(sessionStorage.getItem("comfyui_t2i_seed"));
const tbComfyuiSeed = ref(
  Number.isFinite(_tbSavedSeed) && _tbSavedSeed >= 0 ? _tbSavedSeed : 0,
);

watch(tbComfyuiSeedMode, (val) =>
  sessionStorage.setItem("comfyui_t2i_seed_mode", val),
);
watch(tbComfyuiSeed, (val) =>
  sessionStorage.setItem("comfyui_t2i_seed", String(val)),
);

const tbValidComfyWorkflows = computed(() => {
  if (!Array.isArray(tbComfyuiWorkflows.value)) return [];
  // What run_t2i accepts, not workflow_type, until runs use detection (#1307).
  return tbComfyuiWorkflows.value.filter((w) =>
    w?.missing_placeholders?.includes("{{image_path}}"),
  );
});

const tbCanRunComfyWorkflow = computed(() => !!tbComfyuiSelectedWorkflow.value);

watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    tbComfyuiRunError.value = "";
    await tbFetchComfyWorkflows();
    if (
      !tbComfyuiSelectedWorkflow.value &&
      tbValidComfyWorkflows.value.length
    ) {
      tbComfyuiSelectedWorkflow.value = String(
        tbValidComfyWorkflows.value[0].name,
      );
    }
  },
  { immediate: true },
);

async function tbFetchComfyWorkflows() {
  if (tbComfyuiWorkflowLoading.value) return;
  tbComfyuiWorkflowLoading.value = true;
  tbComfyuiWorkflowError.value = "";
  try {
    const body = await listWorkflows();
    const workflows = body?.workflows;
    tbComfyuiWorkflows.value = Array.isArray(workflows) ? workflows : [];
  } catch (err) {
    tbComfyuiWorkflowError.value =
      errorDetail(err) || err?.message || String(err);
    tbComfyuiWorkflows.value = [];
  } finally {
    tbComfyuiWorkflowLoading.value = false;
  }
}

function tbRunComfyuiOnGrid() {
  if (!tbCanRunComfyWorkflow.value) return;
  emit("run-grid", {
    workflowName: tbComfyuiSelectedWorkflow.value,
    caption: tbComfyuiCaption.value || "",
    seedMode: tbComfyuiSeedMode.value,
    seed: tbComfyuiSeed.value,
  });
}
</script>

<style scoped>
.tb-comfyui-panel {
  width: 392px;
  max-width: 92vw;
}

.tb-gen-run {
  display: flex;
  align-items: flex-end;
  gap: var(--space-4);
}
.tb-gen-seed {
  flex: 1;
  min-width: 0;
}
.tb-gen-seed-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.tb-gen-seed-mode {
  flex: 1;
}
.tb-gen-seed-input {
  width: 96px;
  flex-shrink: 0;
}

.tb-comfyui-note {
  font-size: var(--text-base);
  color: rgba(var(--v-theme-on-panel), 0.65);
}

.tb-comfyui-error {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-error));
}
</style>
