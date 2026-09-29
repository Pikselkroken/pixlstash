<script setup>
/**
 * Inline settings form for one tagger plugin, for a host that shows a plugin's
 * settings in the page rather than behind PluginSelect's gear (the Models tab
 * does this for the built-in PixlStash tagger).
 *
 * Shows TaggerParametersUI, "Reset to defaults" and Save, plus a
 * label-thresholds preview for pixlstash_tagger. Saves via
 * `PATCH /users/me/config` (`tagger_settings.plugins.<name>.params`) and emits
 * the merged settings, the same `update:settings` contract as PluginSelect.
 */
import { computed, ref, watch } from "vue";
import { getLabelThresholds } from "../../api/taggers";
import { patchUserConfig } from "../../api/config";
import TaggerParametersUI from "./TaggerParametersUI.vue";
import { errorDetail } from "../../utils/apiError";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";

const props = defineProps({
  /** Plugin object from GET /taggers (includes parameter_schema, etc.) */
  plugin: { type: Object, required: true },
  /** Current tagger_settings object. */
  settings: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["update:settings"]);

const params = computed(
  () => props.settings?.plugins?.[props.plugin.name]?.params ?? {},
);

const formParams = ref({});
const saving = ref(false);
const saveError = ref("");
const saved = ref(false);

const labelThresholdsOpen = ref(false);
const labelThresholdsData = ref([]);
const labelThresholdsLoading = ref(false);

function defaultParams() {
  const out = {};
  for (const field of props.plugin.parameter_schema ?? []) {
    out[field.name] = field.default ?? null;
  }
  return out;
}

// The rest of the Models tab saves as you go; this form does not, so it says
// when it holds edits that closing Settings would drop.
const dirty = computed(
  () =>
    JSON.stringify(formParams.value) !==
    JSON.stringify({ ...defaultParams(), ...params.value }),
);

function resetToDefaults() {
  formParams.value = defaultParams();
  saved.value = false;
}

// Compared by value, not the plugin or settings object: the pane's is_loaded
// poll replaces the plugin every few seconds, and a reseed on a new object with
// the same content would wipe an unsaved edit.
watch(
  [
    () => props.plugin.name,
    () => JSON.stringify(props.plugin.parameter_schema ?? []),
    () => JSON.stringify(params.value),
  ],
  () => {
    // Merge stored params over defaults so missing keys are filled.
    formParams.value = { ...defaultParams(), ...params.value };
  },
  { immediate: true },
);

async function save() {
  if (saving.value) return;
  saving.value = true;
  saveError.value = "";
  const name = props.plugin.name;
  const next = { ...formParams.value };
  try {
    await patchUserConfig({
      tagger_settings: { plugins: { [name]: { params: next } } },
    });
    emit("update:settings", {
      ...(props.settings || {}),
      plugins: {
        ...(props.settings?.plugins || {}),
        [name]: {
          ...(props.settings?.plugins?.[name] || {}),
          params: { ...params.value, ...next },
        },
      },
    });
    saved.value = true;
  } catch (e) {
    saveError.value = errorDetail(e) || "Failed to save settings.";
  } finally {
    saving.value = false;
  }
}

async function fetchLabelThresholds() {
  labelThresholdsLoading.value = true;
  try {
    // Preview the offset currently in the form, even before it is saved.
    const offset = formParams.value?.threshold_offset;
    labelThresholdsData.value = await getLabelThresholds(offset);
  } catch (e) {
    console.warn("Failed to load label thresholds preview", e);
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
  <div class="tagger-panel">
    <TaggerParametersUI
      v-model="formParams"
      :schema="plugin.parameter_schema"
      @update:model-value="saved = false"
    />

    <div class="tagger-panel-actions">
      <AppButton
        v-if="plugin.name === 'pixlstash_tagger'"
        variant="ghost"
        size="sm"
        icon-left="table-eye"
        @click="openLabelThresholds"
      >
        Preview label thresholds
      </AppButton>
      <span class="tagger-panel-status" role="status">
        <template v-if="dirty">Unsaved changes</template>
        <template v-else-if="saved">Saved.</template>
      </span>
      <AppButton variant="ghost" size="sm" @click="resetToDefaults">
        Reset to defaults
      </AppButton>
      <AppButton
        variant="primary"
        size="sm"
        :loading="saving"
        :disabled="!dirty"
        @click="save"
      >
        Save
      </AppButton>
    </div>

    <div v-if="saveError" class="tagger-panel-error" role="alert">
      {{ saveError }}
    </div>

    <AppDialog
      :open="labelThresholdsOpen"
      title="PixlStash Tagger - Label Thresholds"
      @close="labelThresholdsOpen = false"
    >
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
    </AppDialog>
  </div>
</template>

<style scoped>
.tagger-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.tagger-panel-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* Pushes Reset / Save to the right edge, as a dialog footer would. */
.tagger-panel-status {
  flex: 1 1 auto;
  text-align: right;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.tagger-panel-error {
  color: rgb(var(--v-theme-surface-error));
  font-size: var(--text-xs);
}

.label-thresholds-loading,
.label-thresholds-empty {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.label-thresholds-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
}

.label-thresholds-table th,
.label-thresholds-table td {
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}

.lth-penalised {
  color: rgb(var(--v-theme-surface-error));
}

.lth-boosted {
  color: rgb(var(--v-theme-surface-success));
}
</style>
