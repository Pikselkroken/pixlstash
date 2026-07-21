<script setup>
/**
 * Table of description-capable plugins with active radio and settings gear.
 *
 * Columns: Active (radio) | Name (+ description tooltip) | Loaded | Settings
 *
 * Exactly one plugin may be the active description plugin (or none).
 */
import { computed, ref } from "vue";
import { apiClient } from "../../utils/apiClient";
import TaggerPluginSettingsDialog from "./TaggerPluginSettingsDialog.vue";

const props = defineProps({
  /** Array of plugin objects from GET /taggers. */
  plugins: { type: Array, default: () => [] },
  /** Current tagger_settings object. */
  settings: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["update:settings"]);

const descPlugins = computed(() =>
  props.plugins.filter((p) => p.supports_descriptions),
);

const activePlugin = computed(
  () => props.settings?.active_description_plugin ?? null,
);

function pluginParams(plugin) {
  return props.settings?.plugins?.[plugin.name]?.params ?? {};
}

const settingActive = ref(false);
const activeError = ref("");

const dialogPlugin = ref(null);
const dialogOpen = ref(false);

function openSettings(plugin) {
  dialogPlugin.value = plugin;
  dialogOpen.value = true;
}

async function setActive(pluginName) {
  settingActive.value = true;
  activeError.value = "";
  // Toggle off if already active
  const next = activePlugin.value === pluginName ? null : pluginName;
  try {
    await apiClient.patch("/users/me/config", {
      tagger_settings: { active_description_plugin: next },
    });
    emit("update:settings", {
      ...props.settings,
      active_description_plugin: next,
    });
  } catch (e) {
    activeError.value = e?.response?.data?.detail || "Failed to update.";
  } finally {
    settingActive.value = false;
  }
}

function onParamsSaved({ name, params }) {
  const next = {
    ...(props.settings || {}),
    plugins: {
      ...(props.settings?.plugins || {}),
      [name]: {
        ...(props.settings?.plugins?.[name] || {}),
        params: {
          ...(props.settings?.plugins?.[name]?.params || {}),
          ...params,
        },
      },
    },
  };
  emit("update:settings", next);
}
</script>

<template>
  <div class="desc-plugins-table">
    <table class="dpt-table">
      <thead>
        <tr>
          <th class="dpt-col-active">Active</th>
          <th class="dpt-col-name">Plugin</th>
          <th class="dpt-col-loaded">Loaded</th>
          <th class="dpt-col-actions"></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="plugin in descPlugins"
          :key="plugin.name"
          class="dpt-row"
          :class="{ 'dpt-row--unavailable': !!plugin.load_error }"
        >
          <td class="dpt-col-active">
            <input
              type="radio"
              :name="`active-desc-plugin`"
              :value="plugin.name"
              :checked="activePlugin === plugin.name"
              :disabled="!!plugin.load_error || settingActive"
              class="dpt-radio"
              @change="setActive(plugin.name)"
            />
          </td>

          <td class="dpt-col-name">
            <v-tooltip
              v-if="plugin.description"
              :text="plugin.description"
              location="top"
              max-width="280"
            >
              <template #activator="{ props: tip }">
                <span v-bind="tip" class="dpt-plugin-name">
                  {{ plugin.display_name }}
                  <v-icon size="13" class="dpt-info-icon"
                    >mdi-information-outline</v-icon
                  >
                </span>
              </template>
            </v-tooltip>
            <span v-else class="dpt-plugin-name">{{
              plugin.display_name
            }}</span>
            <span v-if="plugin.load_error" class="dpt-unavailable-label">
              (unavailable)
            </span>
          </td>

          <td class="dpt-col-loaded">
            <v-icon
              :color="plugin.is_loaded ? 'success' : 'default'"
              size="16"
              :title="plugin.is_loaded ? 'Loaded' : 'Not loaded'"
            >
              {{ plugin.is_loaded ? "mdi-check-circle" : "mdi-circle-outline" }}
            </v-icon>
          </td>

          <td class="dpt-col-actions">
            <v-btn
              variant="text"
              size="x-small"
              icon="mdi-cog"
              title="Plugin settings"
              :disabled="!!plugin.load_error"
              @click="openSettings(plugin)"
            />
          </td>
        </tr>

        <tr v-if="!descPlugins.length">
          <td colspan="4" class="dpt-empty">
            No description plugins registered.
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="activeError" class="dpt-error">{{ activeError }}</div>

    <TaggerPluginSettingsDialog
      v-if="dialogPlugin"
      v-model="dialogOpen"
      :plugin="dialogPlugin"
      :params="pluginParams(dialogPlugin)"
      @saved="onParamsSaved"
    />
  </div>
</template>

<style scoped>
.desc-plugins-table {
  width: 100%;
}

.dpt-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

.dpt-table th {
  text-align: left;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.55);
  padding: var(--space-2) var(--space-3) var(--space-2);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}

.dpt-table td {
  padding: var(--space-2) var(--space-3);
  vertical-align: middle;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.dpt-row--unavailable td {
  opacity: 0.5;
}

.dpt-col-active {
  width: 44px;
}

.dpt-col-loaded {
  width: 44px;
  text-align: center;
}

.dpt-col-actions {
  width: 36px;
  text-align: right;
}

.dpt-radio {
  cursor: pointer;
  accent-color: rgb(var(--v-theme-primary));
  width: 16px;
  height: 16px;
}

.dpt-plugin-name {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: default;
}

.dpt-info-icon {
  opacity: 0.5;
}

.dpt-unavailable-label {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.45);
  margin-left: var(--space-2);
}

.dpt-error {
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-error));
  margin-top: var(--space-2);
  padding-left: var(--space-3);
}

.dpt-empty {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.45);
  padding: var(--space-3) var(--space-3);
}
</style>
