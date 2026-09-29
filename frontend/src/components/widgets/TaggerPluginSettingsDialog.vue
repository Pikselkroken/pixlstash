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
 *
 * The built-in PixlStash tagger does not open this: its settings, with the
 * label-thresholds preview, are inline in the Models tab
 * (TaggerPluginSettingsPanel).
 */
import { computed, ref, watch } from "vue";
import { patchUserConfig } from "../../api/config";
import TaggerParametersUI from "./TaggerParametersUI.vue";
import { errorDetail } from "../../utils/apiError";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";

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
  // Enter reaches this through the dialog's accept, which :loading cannot block.
  if (!props.plugin || saving.value) return;
  saving.value = true;
  saveError.value = "";
  try {
    await patchUserConfig({
      tagger_settings: {
        plugins: {
          [props.plugin.name]: { params: { ...formParams.value } },
        },
      },
    });
    emit("saved", { name: props.plugin.name, params: { ...formParams.value } });
    open.value = false;
  } catch (e) {
    saveError.value = errorDetail(e) || "Failed to save settings.";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <AppDialog
    :open="open && !!plugin"
    :title="plugin ? `${plugin.display_name} - Settings` : ''"
    @close="open = false"
    @accept="save"
  >
    <template v-if="plugin">
      <p v-if="plugin.description" class="tagger-settings-desc">
        {{ plugin.description }}
      </p>

      <TaggerParametersUI
        v-model="formParams"
        :schema="plugin.parameter_schema"
      />

      <!-- Downloaded artifacts panel (dormant in 1.3a) -->
      <div
        v-if="plugin.downloaded_artifacts && plugin.downloaded_artifacts.length"
        class="tagger-settings-artifacts"
      >
        <div class="tagger-settings-artifacts-title">Downloaded models</div>
        <div
          v-for="artifact in plugin.downloaded_artifacts"
          :key="artifact.name"
          class="tagger-settings-artifact-row"
        >
          <span>{{ artifact.label || artifact.name }}</span>
          <AppButton
            variant="ghost"
            size="sm"
            icon-only
            icon-left="delete"
            :tooltip="`Delete ${artifact.label || artifact.name}`"
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
    </template>

    <template #footer>
      <AppButton
        variant="ghost"
        class="tagger-settings-reset"
        @click="resetToDefaults"
      >
        Reset to defaults
      </AppButton>
      <AppButton variant="secondary" @click="open = false">Cancel</AppButton>
      <AppButton variant="primary" :loading="saving" @click="save">
        Save
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.tagger-settings-desc {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin: 0;
}

.tagger-settings-artifacts {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  padding-top: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.tagger-settings-artifacts-title {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.tagger-settings-artifact-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--text-sm);
}

.tagger-settings-error {
  color: rgb(var(--v-theme-surface-error));
  font-size: var(--text-xs);
}

/* Reset sits on the footer's left edge, apart from Cancel / Save. */
.tagger-settings-reset {
  margin-right: auto;
}
</style>
