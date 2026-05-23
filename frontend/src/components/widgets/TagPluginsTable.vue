<script setup>
/**
 * Table of tag-capable plugins with enabled toggle and settings gear.
 *
 * Columns: Enabled | Name (+ description tooltip) | Loaded | Settings
 *
 * Emits patch requests via PATCH /users/me/config tagger_settings.
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

const tagPlugins = computed(() => props.plugins.filter((p) => p.supports_tags));

function pluginEnabled(plugin) {
  return Boolean(props.settings?.plugins?.[plugin.name]?.enabled);
}

function pluginParams(plugin) {
  return props.settings?.plugins?.[plugin.name]?.params ?? {};
}

const togglingMap = ref({});
const errorMap = ref({});

const dialogPlugin = ref(null);
const dialogOpen = ref(false);

function openSettings(plugin) {
  dialogPlugin.value = plugin;
  dialogOpen.value = true;
}

async function setEnabled(plugin, value) {
  togglingMap.value = { ...togglingMap.value, [plugin.name]: true };
  errorMap.value = { ...errorMap.value, [plugin.name]: "" };
  try {
    await apiClient.patch("/users/me/config", {
      tagger_settings: {
        plugins: { [plugin.name]: { enabled: Boolean(value) } },
      },
    });
    const next = deepMergeSettings(props.settings, {
      plugins: { [plugin.name]: { enabled: Boolean(value) } },
    });
    emit("update:settings", next);
  } catch (e) {
    errorMap.value = {
      ...errorMap.value,
      [plugin.name]: e?.response?.data?.detail || "Failed to update.",
    };
  } finally {
    togglingMap.value = { ...togglingMap.value, [plugin.name]: false };
  }
}

function onParamsSaved({ name, params }) {
  const next = deepMergeSettings(props.settings, {
    plugins: { [name]: { params } },
  });
  emit("update:settings", next);
}

/** Shallow deep-merge of tagger_settings sub-trees. */
function deepMergeSettings(base, patch) {
  const result = { ...(base || {}) };
  if (patch.plugins) {
    result.plugins = { ...(result.plugins || {}) };
    for (const [name, entry] of Object.entries(patch.plugins)) {
      result.plugins[name] = { ...(result.plugins[name] || {}), ...entry };
      if (entry.params) {
        result.plugins[name].params = {
          ...(result.plugins[name].params || {}),
          ...entry.params,
        };
      }
    }
  }
  return result;
}
</script>

<template>
  <div class="tag-plugins-table">
    <table class="tpt-table">
      <thead>
        <tr>
          <th class="tpt-col-enabled">Enabled</th>
          <th class="tpt-col-name">Plugin</th>
          <th class="tpt-col-loaded">Loaded</th>
          <th class="tpt-col-actions"></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="plugin in tagPlugins"
          :key="plugin.name"
          class="tpt-row"
          :class="{ 'tpt-row--unavailable': !!plugin.load_error }"
        >
          <td class="tpt-col-enabled">
            <v-checkbox
              :model-value="pluginEnabled(plugin)"
              density="compact"
              hide-details
              :disabled="!!plugin.load_error || !!togglingMap[plugin.name]"
              @update:model-value="(v) => setEnabled(plugin, v)"
            />
          </td>

          <td class="tpt-col-name">
            <v-tooltip
              v-if="plugin.description"
              :text="plugin.description"
              location="top"
              max-width="280"
            >
              <template #activator="{ props: tip }">
                <span v-bind="tip" class="tpt-plugin-name">
                  {{ plugin.display_name }}
                  <v-icon size="13" class="tpt-info-icon"
                    >mdi-information-outline</v-icon
                  >
                </span>
              </template>
            </v-tooltip>
            <span v-else class="tpt-plugin-name">{{
              plugin.display_name
            }}</span>
            <span v-if="plugin.load_error" class="tpt-unavailable-label">
              (unavailable)
            </span>
            <div v-if="errorMap[plugin.name]" class="tpt-error">
              {{ errorMap[plugin.name] }}
            </div>
          </td>

          <td class="tpt-col-loaded">
            <v-icon
              :color="plugin.is_loaded ? 'success' : 'default'"
              size="16"
              :title="plugin.is_loaded ? 'Loaded' : 'Not loaded'"
            >
              {{ plugin.is_loaded ? "mdi-check-circle" : "mdi-circle-outline" }}
            </v-icon>
          </td>

          <td class="tpt-col-actions">
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

        <tr v-if="!tagPlugins.length">
          <td colspan="4" class="tpt-empty">No tag plugins registered.</td>
        </tr>
      </tbody>
    </table>

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
.tag-plugins-table {
  width: 100%;
}

.tpt-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.tpt-table th {
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.55);
  padding: 4px 8px 6px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}

.tpt-table td {
  padding: 4px 8px;
  vertical-align: middle;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.tpt-row--unavailable td {
  opacity: 0.5;
}

.tpt-col-enabled {
  width: 52px;
}

.tpt-col-loaded {
  width: 44px;
  text-align: center;
}

.tpt-col-actions {
  width: 36px;
  text-align: right;
}

.tpt-plugin-name {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: default;
}

.tpt-info-icon {
  opacity: 0.5;
}

.tpt-unavailable-label {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  margin-left: 4px;
}

.tpt-error {
  font-size: 11px;
  color: rgb(var(--v-theme-error));
  margin-top: 2px;
}

.tpt-empty {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  padding: 10px 8px;
}
</style>
