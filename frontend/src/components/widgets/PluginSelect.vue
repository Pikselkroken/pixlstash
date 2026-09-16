<script setup>
/**
 * Dropdown picking the active plugin of one capability, with a settings gear.
 *
 * Exactly one plugin may be active for the capability, or none: the first
 * option is an explicit None. The gear configures the plugin currently chosen
 * and is disabled while None is. Under the row, one muted line says whether the
 * chosen plugin is loaded and what it does.
 *
 * Was a radio table (issue #1344): single selection took a row per plugin and
 * two columns of them made the Auto-tagging section the busiest in Settings.
 */
import { computed, ref } from "vue";
import { patchUserConfig } from "../../api/config";
import TaggerPluginSettingsDialog from "./TaggerPluginSettingsDialog.vue";
import { errorDetail } from "../../utils/apiError";
import AppButton from "./AppButton.vue";
import AppSelect from "./AppSelect.vue";

const props = defineProps({
  /** Array of plugin objects from GET /taggers. */
  plugins: { type: Array, default: () => [] },
  /** Current tagger_settings object. */
  settings: { type: Object, default: () => ({}) },
  /**
   * Which capability this picker lists: "tag" or "description". Drives the
   * plugin filter (`supports_tags` / `supports_descriptions`), the config key
   * (`active_tag_plugin` / `active_description_plugin`), and the wording.
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

const noneLabel = computed(() =>
  props.kind === "tag"
    ? "None — no automatic tagging"
    : "None — no automatic captioning",
);

const capablePlugins = computed(() =>
  props.plugins.filter((p) => p[supportsFlag.value]),
);

const activePlugin = computed(() => props.settings?.[activeKey.value] ?? null);

/** The chosen plugin's object, or null for None or a name no longer listed. */
const activePluginInfo = computed(
  () => capablePlugins.value.find((p) => p.name === activePlugin.value) ?? null,
);

/** "Loaded · what it does" for the chosen plugin; empty for None. */
const hint = computed(() => {
  const p = activePluginInfo.value;
  if (!p) return "";
  const status = p.is_loaded ? "Loaded" : "Not loaded";
  return p.description ? `${status} · ${p.description}` : status;
});

// A native <select> speaks strings, so None travels as "" and maps to null.
const options = computed(() => [
  { value: "", label: noneLabel.value },
  ...capablePlugins.value.map((p) => ({
    value: p.name,
    label: p.display_name,
  })),
  // A saved plugin that is no longer installed: say so rather than leave the
  // field blank, or show None when the setting is not None.
  ...(activePlugin.value && !activePluginInfo.value
    ? [
        {
          value: activePlugin.value,
          label: `${activePlugin.value} (not installed)`,
        },
      ]
    : []),
]);

function pluginParams(plugin) {
  return props.settings?.plugins?.[plugin.name]?.params ?? {};
}

// Also what puts the dropdown back after a failed save: the native <select> has
// already moved, and toggling `disabled` re-renders AppSelect, which re-applies
// its unchanged value.
const settingActive = ref(false);
const activeError = ref("");

const dialogPlugin = ref(null);
const dialogOpen = ref(false);

// aria-disabled rather than disabled: a natively disabled button takes no hover
// or focus, so the reason in its tooltip could never be read.
const gearTooltip = computed(() => {
  if (activePluginInfo.value) {
    return `${activePluginInfo.value.display_name} settings`;
  }
  return activePlugin.value
    ? `${activePlugin.value} is not installed`
    : "Choose a plugin to configure it";
});

// With nothing chosen this sets no plugin, so the dialog stays unrendered.
function openSettings() {
  dialogPlugin.value = activePluginInfo.value;
  dialogOpen.value = true;
}

async function setActive(value) {
  const pluginName = value || null;
  settingActive.value = true;
  activeError.value = "";
  try {
    await patchUserConfig({
      tagger_settings: { [activeKey.value]: pluginName },
    });
    emit("update:settings", {
      ...props.settings,
      [activeKey.value]: pluginName,
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
  <div class="plugin-select">
    <div v-if="capablePlugins.length" class="ps-row">
      <AppSelect
        class="ps-field"
        :model-value="activePlugin ?? ''"
        :options="options"
        :label="kind === 'tag' ? 'Tag plugin' : 'Description plugin'"
        hide-label
        :disabled="settingActive"
        @update:model-value="setActive"
      />
      <AppButton
        variant="secondary"
        icon-only
        icon-left="cog"
        :tooltip="gearTooltip"
        :aria-disabled="activePluginInfo ? undefined : 'true'"
        @click="openSettings"
      />
    </div>
    <div v-else class="ps-empty">No {{ kind }} plugins registered.</div>

    <div v-if="hint" class="ps-hint">{{ hint }}</div>

    <div v-if="activeError" class="ps-error">{{ activeError }}</div>

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
.plugin-select {
  width: 100%;
}

.ps-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* min-width: 0 so a long option label cannot hold the row wider than its
   Settings column: a <select>'s min-content is its widest option. */
.ps-field {
  flex: 1 1 auto;
  min-width: 0;
}

.ps-hint {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  overflow-wrap: anywhere;
}

.ps-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  margin-top: var(--space-2);
}

.ps-empty {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  padding: var(--space-3) 0;
}
</style>
