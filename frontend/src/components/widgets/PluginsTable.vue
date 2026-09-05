<script setup>
/**
 * Table of plugins of one capability, with an active radio and a settings gear.
 *
 * Columns: Active (radio) | Name (+ description tooltip) | Loaded | Settings
 *
 * Exactly one plugin may be active for the capability (or none).
 *
 * Replaces the former TagPluginsTable / DescriptionPluginsTable, which were the
 * same table twice over: they differed only in which capability flag they
 * filtered on, which config key they wrote, and - as pure drift, not intent - a
 * 52px vs 44px radio column and the error line's spacing. Those two now render
 * identically, which is the point of having one component.
 */
import { computed, ref } from "vue";
import { patchUserConfig } from "../../api/config";
import TaggerPluginSettingsDialog from "./TaggerPluginSettingsDialog.vue";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  /** Array of plugin objects from GET /taggers. */
  plugins: { type: Array, default: () => [] },
  /** Current tagger_settings object. */
  settings: { type: Object, default: () => ({}) },
  /**
   * Which capability this table lists: "tag" or "description". Drives the
   * plugin filter (`supports_tags` / `supports_descriptions`), the config key
   * (`active_tag_plugin` / `active_description_plugin`), the radio group name,
   * and the empty-state wording.
   */
  kind: {
    type: String,
    required: true,
    validator: (v) => ["tag", "description"].includes(v),
  },
});

const emit = defineEmits(["update:settings"]);

const supportsFlag = computed(() =>
  props.kind === "tag" ? "supports_tags" : "supports_descriptions",
);
const activeKey = computed(() => `active_${props.kind}_plugin`);

const capablePlugins = computed(() =>
  props.plugins.filter((p) => p[supportsFlag.value]),
);

const activePlugin = computed(() => props.settings?.[activeKey.value] ?? null);

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
    await patchUserConfig({
      tagger_settings: { [activeKey.value]: next },
    });
    emit("update:settings", {
      ...props.settings,
      [activeKey.value]: next,
    });
  } catch (e) {
    activeError.value = errorDetail(e) || "Failed to update.";
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
  <div class="plugins-table">
    <table class="pt-table">
      <thead>
        <tr>
          <th class="pt-col-active">Active</th>
          <th class="pt-col-name">Plugin</th>
          <th class="pt-col-loaded">Loaded</th>
          <th class="pt-col-actions"></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="plugin in capablePlugins"
          :key="plugin.name"
          class="pt-row"
        >
          <td class="pt-col-active">
            <input
              type="radio"
              :name="`active-${kind}-plugin`"
              :value="plugin.name"
              :checked="activePlugin === plugin.name"
              :disabled="settingActive"
              class="pt-radio"
              @change="setActive(plugin.name)"
            />
          </td>

          <td class="pt-col-name">
            <v-tooltip
              v-if="plugin.description"
              :text="plugin.description"
              location="top"
              max-width="280"
            >
              <template #activator="{ props: tip }">
                <span v-bind="tip" class="pt-plugin-name">
                  {{ plugin.display_name }}
                  <v-icon size="13" class="pt-info-icon"
                    >mdi-information-outline</v-icon
                  >
                </span>
              </template>
            </v-tooltip>
            <span v-else class="pt-plugin-name">{{ plugin.display_name }}</span>
          </td>

          <td class="pt-col-loaded">
            <v-icon
              :color="plugin.is_loaded ? 'success' : 'default'"
              size="16"
              :title="plugin.is_loaded ? 'Loaded' : 'Not loaded'"
            >
              {{ plugin.is_loaded ? "mdi-check-circle" : "mdi-circle-outline" }}
            </v-icon>
          </td>

          <td class="pt-col-actions">
            <v-btn
              variant="text"
              size="x-small"
              icon="mdi-cog"
              title="Plugin settings"
              @click="openSettings(plugin)"
            />
          </td>
        </tr>

        <tr v-if="!capablePlugins.length">
          <td colspan="4" class="pt-empty">No {{ kind }} plugins registered.</td>
        </tr>
      </tbody>
    </table>

    <div v-if="activeError" class="pt-error">{{ activeError }}</div>

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
.plugins-table {
  width: 100%;
}

.pt-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

/* Sticky because the table is scrolled inside its Settings column (see
   BehaviourSection): the scroll box wraps the whole table, so an unpinned
   header row would scroll out of a viewport only a few rows tall. The rule
   under it is a box-shadow rather than a border - `border-collapse: collapse`
   hands the border to the table, which does not travel with a sticky cell. */
.pt-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  text-align: left;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.55);
  background: rgb(var(--v-theme-surface));
  padding: var(--space-2) var(--space-3) var(--space-2);
  box-shadow: inset 0 -1px 0 rgba(var(--v-theme-on-surface), 0.12);
}

.pt-table td {
  padding: var(--space-2) var(--space-3);
  vertical-align: middle;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.pt-col-active {
  width: 52px;
}

.pt-col-loaded {
  width: 44px;
  text-align: center;
}

.pt-col-actions {
  width: 36px;
  text-align: right;
}

/* The name column is whatever these three leave it, and inside a ~280px
   Settings column that is the difference between a plugin name on one line and
   two - so the fixed-width columns take the tighter padding step. */
.pt-table .pt-col-active,
.pt-table .pt-col-loaded,
.pt-table .pt-col-actions {
  padding-left: var(--space-2);
  padding-right: var(--space-2);
}

/* The name column is whatever is left of a ~280px Settings column, so a long
   plugin name wraps rather than holding the table open: nowrap here made the
   table wider than its column, and the column wider than the pane. */
.pt-plugin-name {
  overflow-wrap: anywhere;
  cursor: default;
}

.pt-info-icon {
  margin-left: var(--space-2);
  opacity: 0.5;
  vertical-align: -2px;
}

.pt-radio {
  cursor: pointer;
  accent-color: rgb(var(--v-theme-primary));
  width: 16px;
  height: 16px;
}

.pt-error {
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-error));
  margin-top: var(--space-2);
}

.pt-empty {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.45);
  padding: var(--space-3) var(--space-3);
}
</style>
