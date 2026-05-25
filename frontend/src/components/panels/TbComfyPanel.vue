<template>
  <div class="tb-comfyui-panel popup-panel">
    <div class="tb-comfyui-header">Generate with ComfyUI (T2I)</div>
    <div class="tb-comfyui-body">
      <div v-if="tbComfyuiWorkflowLoading" class="tb-comfyui-note">
        Loading workflows...
      </div>
      <div v-else>
        <div v-if="tbComfyuiWorkflowError" class="tb-comfyui-error">
          {{ tbComfyuiWorkflowError }}
        </div>
        <template v-if="tbValidComfyWorkflows.length">
          <label class="tb-comfyui-label">Workflow</label>
          <select v-model="tbComfyuiSelectedWorkflow" class="tb-comfyui-select">
            <option
              v-for="wf in tbValidComfyWorkflows"
              :key="wf.name"
              :value="wf.name"
            >
              {{ wf.display_name || wf.name }}
            </option>
          </select>
          <label class="tb-comfyui-label">Caption</label>
          <textarea
            v-model="tbComfyuiCaption"
            class="tb-comfyui-textarea"
            rows="4"
            placeholder="Optional caption for {{caption}}"
            @keydown.stop
          ></textarea>
          <label class="tb-comfyui-label">Seed</label>
          <div class="tb-comfyui-seed-row">
            <button
              type="button"
              class="tb-comfyui-seed-btn"
              :class="{ active: tbComfyuiSeedMode === 'random' }"
              @click="tbComfyuiSeedMode = 'random'"
            >
              Random
            </button>
            <button
              type="button"
              class="tb-comfyui-seed-btn"
              :class="{ active: tbComfyuiSeedMode === 'fixed' }"
              @click="tbComfyuiSeedMode = 'fixed'"
            >
              Fixed
            </button>
            <input
              v-if="tbComfyuiSeedMode === 'fixed'"
              v-model.number="tbComfyuiSeed"
              type="number"
              class="tb-comfyui-seed-input"
              min="0"
              max="4294967295"
              @keydown.stop
            />
            <button
              class="tb-comfyui-run-btn"
              type="button"
              :disabled="!tbCanRunComfyWorkflow"
              @click="tbRunComfyuiOnGrid"
            >
              <v-icon size="14">mdi-play</v-icon> Run
            </button>
          </div>
        </template>
        <div v-else class="tb-comfyui-note">No valid T2I workflows found.</div>
        <div v-if="tbComfyuiRunError" class="tb-comfyui-error">
          {{ tbComfyuiRunError }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from "vue";
import { apiClient } from "../../utils/apiClient";

const props = defineProps({
  backendUrl: { type: String, default: "" },
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
  return tbComfyuiWorkflows.value.filter((w) => w?.workflow_type === "t2i");
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
    const res = await apiClient.get("/comfyui/workflows");
    const workflows = res.data?.workflows;
    tbComfyuiWorkflows.value = Array.isArray(workflows) ? workflows : [];
  } catch (err) {
    tbComfyuiWorkflowError.value =
      err?.response?.data?.detail || err?.message || String(err);
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
  padding: 12px 14px;
  min-width: 260px;
  gap: 10px;
}

.tb-comfyui-header {
  font-size: 1em;
  font-weight: 500;
}

.tb-comfyui-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tb-comfyui-label {
  font-size: 0.82em;
  font-weight: 500;
  color: rgba(var(--v-theme-on-background), 0.65);
  margin-bottom: 2px;
  display: block;
}

.tb-comfyui-select,
.tb-comfyui-textarea,
.tb-comfyui-seed-input {
  width: 100%;
  background: rgba(var(--v-theme-surface), 0.5);
  border: 1px solid rgba(var(--v-theme-on-background), 0.2);
  border-radius: 4px;
  color: rgb(var(--v-theme-on-background));
  font-family: inherit;
  font-size: 0.88em;
  padding: 4px 8px;
  box-sizing: border-box;
}

.tb-comfyui-seed-row {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 4px;
}

.tb-comfyui-seed-btn {
  background: rgba(var(--v-theme-surface), 0.3);
  border: 1px solid rgba(var(--v-theme-on-background), 0.2);
  border-radius: 4px;
  color: rgb(var(--v-theme-on-background));
  cursor: pointer;
  padding: 3px 9px;
  font-family: inherit;
  font-size: 0.82em;
  transition: background 0.15s;
}

.tb-comfyui-seed-btn.active {
  background: rgba(var(--v-theme-primary), 0.35);
  border-color: rgba(var(--v-theme-primary), 0.6);
}

.tb-comfyui-seed-input {
  flex: 1;
  min-width: 70px;
}

.tb-comfyui-run-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 4px;
  background: rgba(var(--v-theme-primary), 0.25);
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  font-family: inherit;
  font-size: 0.85em;
  font-weight: 500;
  transition: background 0.15s;
}

.tb-comfyui-run-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.tb-comfyui-note {
  font-size: 0.85em;
  opacity: 0.65;
  padding: 2px 0;
}

.tb-comfyui-error {
  font-size: 0.82em;
  color: rgb(var(--v-theme-error));
  padding: 2px 0;
}
</style>
