<script setup>
/**
 * Inline settings form for one tagger plugin, for a host that shows a plugin's
 * settings in the page rather than behind PluginSelect's gear (the Models tab
 * does this for the built-in PixlStash tagger).
 *
 * Drawn as a settings section of its own (`title`), with its verbs in the
 * section's heading row, and its fields in two columns: the Models pane is a
 * fixed height, and a setting the plugin adds must not make it scroll.
 *
 * Shows TaggerParametersUI, "Reset to defaults" and Save, plus a
 * label-thresholds preview for pixlstash_tagger. Saves via
 * `PATCH /users/me/config` (`tagger_settings.plugins.<name>.params`) and emits
 * the merged settings, the same `update:settings` contract as PluginSelect.
 *
 * Switching `whole_face_crop` on only changes pictures tagged afterwards, so
 * after that save the panel offers to re-check the library's faces (#1662).
 */
import { computed, ref, watch } from "vue";
import { getLabelThresholds, retagFaceCrops } from "../../api/taggers";
import { patchUserConfig } from "../../api/config";
import { useConfirm } from "../../composables/useConfirm";
import { useNoticeStore } from "../../stores/useNoticeStore";
import TaggerParametersUI from "./TaggerParametersUI.vue";
import { errorDetail } from "../../utils/apiError";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";
import SettingsSection from "../settings/SettingsSection.vue";

const props = defineProps({
  /** The section's heading. */
  title: { type: String, default: "" },
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

// Asked only after the save has landed, so the answer never decides whether
// the setting is stored. A failure leaves the setting on; saving it off and on
// again offers the re-check again.
async function offerFaceCropRetag() {
  try {
    const { count } = await retagFaceCrops({ dryRun: true });
    if (!count) return;
    const many = count !== 1;
    const ok = await useConfirm().confirm({
      title: `Re-check ${count} ${many ? "pictures" : "picture"} with faces?`,
      message:
        "Whole-face close-up only applies to pictures tagged from now on. " +
        (many
          ? `Re-checking redoes the face quality tags on these ${count} in the ` +
            "background. Their current face quality tags disappear until each " +
            "picture is done. "
          : "Re-checking redoes its face quality tags in the background. They " +
            "disappear until it is done. ") +
        "It can't be stopped once started, and on a large library it takes a " +
        "while; follow it in the sidebar's Tasks tab. Other tags and your " +
        "review decisions are kept.",
      confirmLabel: "Re-check now",
      cancelLabel: "Not now",
    });
    if (!ok) return;
    await retagFaceCrops();
    useNoticeStore().success(
      `Re-checking ${count} ${many ? "pictures" : "picture"} with faces. ` +
        "Follow it in the sidebar's Tasks tab.",
    );
  } catch (e) {
    console.error("Whole-face close-up re-check failed:", errorDetail(e) || e);
    useNoticeStore().error(
      "Couldn't start the face re-check. Whole-face close-up is still on; " +
        "save it off and on again to retry.",
    );
  }
}

async function save() {
  if (saving.value) return;
  saving.value = true;
  saveError.value = "";
  const name = props.plugin.name;
  const next = { ...formParams.value };
  // The backend reads this setting off the PixlStash tagger only.
  const turnedOnWholeFace =
    name === "pixlstash_tagger" &&
    !params.value.whole_face_crop &&
    next.whole_face_crop === true;
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
    if (turnedOnWholeFace) offerFaceCropRetag();
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
  <SettingsSection :title="title" class="tagger-panel">
    <template #action>
      <!-- Icon-only so the heading row keeps the title on one line beside
           the status, Reset and Save; the tooltip is also its name. -->
      <AppButton
        v-if="plugin.name === 'pixlstash_tagger'"
        variant="ghost"
        size="sm"
        icon-left="table-eye"
        icon-only
        tooltip="Preview label thresholds"
        @click="openLabelThresholds"
      />
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
    </template>

    <TaggerParametersUI
      v-model="formParams"
      :schema="plugin.parameter_schema"
      :columns="2"
      @update:model-value="saved = false"
    />

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
  </SettingsSection>
</template>

<style scoped>
.tagger-panel-status {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
}

.tagger-panel-error {
  margin-top: var(--space-2);
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
