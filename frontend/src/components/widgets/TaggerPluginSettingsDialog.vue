<script setup>
/**
 * Settings dialog for a single tagger plugin.
 *
 * Shows:
 *   - Plugin description
 *   - TaggerParametersUI for its parameter schema
 *   - "Reset to defaults" button
 *   - Save / Cancel actions
 *   - "Downloaded models" panel (rendered when list_downloaded_artifacts is non-empty)
 *   - Label-thresholds preview (for pixlstash_tagger only)
 */
import { computed, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";
import TaggerParametersUI from "./TaggerParametersUI.vue";

const props = defineProps({
  /** Plugin object from GET /taggers (includes parameter_schema, etc.) */
  plugin: { type: Object, default: null },
  /** Current params from tagger_settings.plugins[name].params */
  params: { type: Object, default: () => ({}) },
  modelValue: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue", "saved"]);

const open = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});

const formParams = ref({});
const saving = ref(false);
const saveError = ref("");

const labelThresholdsOpen = ref(false);
const labelThresholdsData = ref([]);
const labelThresholdsLoading = ref(false);

function defaultParams() {
  const out = {};
  for (const field of props.plugin?.parameter_schema ?? []) {
    out[field.name] = field.default ?? null;
  }
  return out;
}

function resetToDefaults() {
  formParams.value = defaultParams();
}

watch(
  () => [props.plugin, props.params, props.modelValue],
  ([plugin, params, isOpen]) => {
    if (isOpen && plugin) {
      // Merge stored params over defaults so missing keys are filled.
      formParams.value = { ...defaultParams(), ...params };
      saveError.value = "";
    }
  },
  { immediate: true },
);

async function save() {
  if (!props.plugin) return;
  saving.value = true;
  saveError.value = "";
  try {
    await apiClient.patch("/users/me/config", {
      tagger_settings: {
        plugins: {
          [props.plugin.name]: { params: { ...formParams.value } },
        },
      },
    });
    emit("saved", { name: props.plugin.name, params: { ...formParams.value } });
    open.value = false;
  } catch (e) {
    saveError.value = e?.response?.data?.detail || "Failed to save settings.";
  } finally {
    saving.value = false;
  }
}

async function fetchLabelThresholds() {
  labelThresholdsLoading.value = true;
  try {
    // Preview the offset currently in the form, even before it is saved.
    const offset = formParams.value?.threshold_offset;
    const res = await apiClient.get("/tagger/label-thresholds", {
      params: offset != null ? { offset } : {},
    });
    labelThresholdsData.value = res.data;
  } catch {
    labelThresholdsData.value = [];
  } finally {
    labelThresholdsLoading.value = false;
  }
}

function openLabelThresholds() {
  labelThresholdsOpen.value = true;
  fetchLabelThresholds();
}

// Keep the open preview in sync with edits to the offset.
watch(
  () => formParams.value?.threshold_offset,
  () => {
    if (labelThresholdsOpen.value) fetchLabelThresholds();
  },
);
</script>

<template>
  <v-dialog v-model="open" max-width="460" @click:outside="open = false">
    <v-card v-if="plugin" class="tagger-settings-dialog">
      <v-card-title class="tagger-settings-title">
        {{ plugin.display_name }} — Settings
      </v-card-title>

      <v-card-text class="tagger-settings-body">
        <p v-if="plugin.description" class="tagger-settings-desc">
          {{ plugin.description }}
        </p>

        <TaggerParametersUI
          v-model="formParams"
          :schema="plugin.parameter_schema"
        />

        <!-- Label-thresholds preview for pixlstash_tagger -->
        <div
          v-if="plugin.name === 'pixlstash_tagger'"
          class="tagger-settings-threshold-row"
        >
          <v-btn
            variant="text"
            size="small"
            prepend-icon="mdi-table-eye"
            @click="openLabelThresholds"
          >
            Preview label thresholds
          </v-btn>
        </div>

        <!-- Downloaded artifacts panel (dormant in 1.3a) -->
        <div
          v-if="
            plugin.downloaded_artifacts && plugin.downloaded_artifacts.length
          "
          class="tagger-settings-artifacts"
        >
          <div class="tagger-settings-artifacts-title">Downloaded models</div>
          <div
            v-for="artifact in plugin.downloaded_artifacts"
            :key="artifact.name"
            class="tagger-settings-artifact-row"
          >
            <span>{{ artifact.label || artifact.name }}</span>
            <v-btn
              variant="text"
              size="x-small"
              color="error"
              icon="mdi-delete"
              :title="`Delete ${artifact.label || artifact.name}`"
              @click="
                $emit('delete-artifact', {
                  plugin: plugin.name,
                  artifact: artifact.name,
                })
              "
            />
          </div>
        </div>

        <div v-if="saveError" class="tagger-settings-error">
          {{ saveError }}
        </div>
      </v-card-text>

      <v-card-actions class="tagger-settings-actions">
        <v-btn variant="text" size="small" @click="resetToDefaults">
          Reset to defaults
        </v-btn>
        <v-spacer />
        <v-btn variant="text" size="small" @click="open = false">Cancel</v-btn>
        <v-btn
          variant="flat"
          size="small"
          color="primary"
          :loading="saving"
          @click="save"
        >
          Save
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- Label thresholds sub-dialog -->
  <v-dialog
    v-model="labelThresholdsOpen"
    max-width="520"
    @click:outside="labelThresholdsOpen = false"
  >
    <v-card class="label-thresholds-dialog">
      <v-card-title class="label-thresholds-dialog-title">
        PixlStash Tagger — Label Thresholds
      </v-card-title>
      <v-card-text class="label-thresholds-dialog-body">
        <div v-if="labelThresholdsLoading" class="label-thresholds-loading">
          Loading…
        </div>
        <div
          v-else-if="!labelThresholdsData.length"
          class="label-thresholds-empty"
        >
          No label thresholds found. Ensure the PixlStash tagger model is
          installed.
        </div>
        <table v-else class="label-thresholds-table">
          <thead>
            <tr>
              <th class="lth-col-name">Tag</th>
              <th class="lth-col-base">Base threshold</th>
              <th class="lth-col-eff">After offset</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in labelThresholdsData" :key="row.label">
              <td class="lth-col-name">{{ row.label }}</td>
              <td class="lth-col-base">
                {{ (row.base_threshold * 100).toFixed(1) }}%
              </td>
              <td
                class="lth-col-eff"
                :class="
                  row.effective_threshold > row.base_threshold
                    ? 'lth-penalised'
                    : row.effective_threshold < row.base_threshold
                      ? 'lth-boosted'
                      : ''
                "
              >
                {{ (row.effective_threshold * 100).toFixed(1) }}%
              </td>
            </tr>
          </tbody>
        </table>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.tagger-settings-dialog {
  background: rgb(var(--v-theme-surface));
}

.tagger-settings-title {
  font-size: 15px;
  font-weight: 600;
  padding: 16px 20px 8px;
}

.tagger-settings-body {
  padding: 8px 20px 4px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.tagger-settings-desc {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin: 0;
}

.tagger-settings-threshold-row {
  margin-top: 4px;
}

.tagger-settings-artifacts {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  padding-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tagger-settings-artifacts-title {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.tagger-settings-artifact-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
}

.tagger-settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  margin-top: 4px;
}

.tagger-settings-actions {
  padding: 8px 16px 12px;
}

/* Label thresholds sub-dialog (shared styles copied from BehaviourSection) */
.label-thresholds-dialog {
  background: rgb(var(--v-theme-surface));
}

.label-thresholds-dialog-title {
  font-size: 14px;
  font-weight: 600;
  padding: 14px 18px 8px;
}

.label-thresholds-dialog-body {
  padding: 4px 18px 14px;
  max-height: 400px;
  overflow-y: auto;
}

.label-thresholds-loading,
.label-thresholds-empty {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.label-thresholds-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.label-thresholds-table th,
.label-thresholds-table td {
  text-align: left;
  padding: 4px 8px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}

.lth-penalised {
  color: rgb(var(--v-theme-error));
}

.lth-boosted {
  color: rgb(var(--v-theme-success));
}
</style>
