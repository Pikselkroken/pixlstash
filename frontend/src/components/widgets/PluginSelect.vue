<script setup>
/**
 * Picks the active plugin of one capability, with a settings gear beside it.
 *
 * A field-shaped button opens the app's one menu (`styles/context-menu.css`):
 * an explicit None row, then a `menuitemradio` row per plugin. Each plugin row
 * leads with a loaded / not-loaded icon and carries a tooltip with the plugin's
 * description, so readiness and purpose can be read without choosing, which
 * saves. The gear configures the chosen plugin and refuses while None is.
 *
 * Was a radio table (issue #1344), then briefly a native <select>, whose popup
 * cannot take the menu styling or a tooltip per option.
 */
import { computed, ref } from "vue";
import { VIcon, VMenu } from "vuetify/components";
import { patchUserConfig } from "../../api/config";
import TaggerPluginSettingsDialog from "./TaggerPluginSettingsDialog.vue";
import { errorDetail } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import AppButton from "./AppButton.vue";
import Tooltip from "./Tooltip.vue";

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
const fieldLabel = computed(() =>
  props.kind === "tag" ? "Tag plugin" : "Description plugin",
);

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

/** A plugin's name as shown: `display_name` is optional for a plugin to set. */
const labelOf = (p) => p.display_name || p.name;

const loadedText = (p) => (p.is_loaded ? "Loaded" : "Not loaded");

/** What a plugin's row, and the field while it is chosen, say on hover. */
function tooltipOf(p) {
  const status = `${loadedText(p)}.`;
  return p.description ? `${p.description} ${status}` : status;
}

/**
 * The rows, None first. A saved plugin that is no longer installed gets a row
 * of its own, so the field names it rather than going blank or claiming None.
 */
const rows = computed(() => [
  { value: null, label: noneLabel.value, plugin: null },
  ...capablePlugins.value.map((p) => ({
    value: p.name,
    label: labelOf(p),
    plugin: p,
  })),
  ...(activePlugin.value && !activePluginInfo.value
    ? [
        {
          value: activePlugin.value,
          label: `${activePlugin.value} (not installed)`,
          plugin: null,
        },
      ]
    : []),
]);

const currentRow = computed(
  () => rows.value.find((r) => r.value === activePlugin.value) ?? rows.value[0],
);

const triggerAriaLabel = computed(() => {
  const p = currentRow.value.plugin;
  const status = p ? `, ${loadedText(p).toLowerCase()}` : "";
  return `${fieldLabel.value}: ${currentRow.value.label}${status}`;
});

function pluginParams(plugin) {
  return props.settings?.plugins?.[plugin.name]?.params ?? {};
}

const menuOpen = ref(false);
// The chosen row leaves with the menu and takes keyboard focus to <body>. The
// field is refocused once the close transition ends: sooner, the leaving menu
// takes it away again.
let refocusTrigger = false;
function onMenuAfterLeave() {
  if (!refocusTrigger) return;
  refocusTrigger = false;
  rowEl.value?.querySelector(".ps-trigger")?.focus();
}
// The row, not the field: a template ref on the button inside VMenu's
// activator slot came back null in the browser when the menu closed.
const rowEl = ref(null);
const settingActive = ref(false);
const activeError = ref("");

const dialogPlugin = ref(null);
const dialogOpen = ref(false);

// aria-disabled rather than disabled: a natively disabled button takes no hover
// or focus, so the reason in its tooltip could never be read.
const gearTooltip = computed(() => {
  if (activePluginInfo.value) {
    return `${labelOf(activePluginInfo.value)} settings`;
  }
  return activePlugin.value
    ? `${activePlugin.value} is not installed`
    : "Choose a plugin to configure it";
});

function openSettings() {
  if (!activePluginInfo.value) return;
  dialogPlugin.value = activePluginInfo.value;
  dialogOpen.value = true;
}

async function choose(value) {
  menuOpen.value = false;
  refocusTrigger = true;
  if (value === activePlugin.value || settingActive.value) return;
  settingActive.value = true;
  activeError.value = "";
  try {
    await patchUserConfig({
      tagger_settings: { [activeKey.value]: value },
    });
    emit("update:settings", {
      ...props.settings,
      [activeKey.value]: value,
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
    <div v-if="capablePlugins.length" ref="rowEl" class="ps-row">
      <!-- A below-opening menu's min-width is capped at its activator's width,
           so an oversized floor makes the menu exactly as wide as the field. -->
      <VMenu
        v-model="menuOpen"
        location="bottom start"
        :offset="4"
        :min-width="10000"
        @after-leave="onMenuAfterLeave"
      >
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            class="ps-trigger"
            type="button"
            :aria-label="triggerAriaLabel"
            :aria-busy="settingActive ? 'true' : undefined"
          >
            <Tooltip
              v-if="currentRow.plugin"
              :text="tooltipOf(currentRow.plugin)"
              activator="parent"
            />
            <VIcon
              v-if="currentRow.plugin"
              class="ps-loaded"
              :class="{ 'ps-loaded--on': currentRow.plugin.is_loaded }"
              aria-hidden="true"
              >{{
                currentRow.plugin.is_loaded
                  ? "mdi-check-circle"
                  : "mdi-circle-outline"
              }}</VIcon
            >
            <span class="ps-trigger-label">{{ currentRow.label }}</span>
            <VIcon class="ps-chevron" aria-hidden="true"
              >mdi-chevron-down</VIcon
            >
          </button>
        </template>

        <div
          class="ctx-menu"
          role="menu"
          :aria-label="fieldLabel"
          tabindex="-1"
          @keydown="onMenuKeydown"
        >
          <button
            v-for="row in rows"
            :key="String(row.value)"
            class="ctx-item"
            type="button"
            role="menuitemradio"
            :aria-checked="row.value === activePlugin ? 'true' : 'false'"
            @click="choose(row.value)"
          >
            <Tooltip
              v-if="row.plugin"
              :text="tooltipOf(row.plugin)"
              location="end"
              activator="parent"
            />
            <!-- The leading slot is reserved on every row so the labels share
                 a left edge; None and a missing plugin leave it empty. -->
            <span class="ps-loaded-slot">
              <VIcon
                v-if="row.plugin"
                class="ps-loaded"
                :class="{ 'ps-loaded--on': row.plugin.is_loaded }"
                role="img"
                :aria-label="loadedText(row.plugin)"
                >{{
                  row.plugin.is_loaded
                    ? "mdi-check-circle"
                    : "mdi-circle-outline"
                }}</VIcon
              >
            </span>
            <span class="ctx-label-text">{{ row.label }}</span>
            <VIcon v-if="row.value === activePlugin" class="ctx-check"
              >mdi-check</VIcon
            >
          </button>
        </div>
      </VMenu>
      <AppButton
        variant="secondary"
        icon-only
        icon-left="cog"
        :tooltip="gearTooltip"
        :aria-disabled="activePluginInfo ? undefined : 'true'"
        @click="openSettings"
      />
    </div>
    <div v-else class="ps-empty">
      No {{ kind }} plugins registered.
      <template v-if="activePlugin">
        {{ activePlugin }} is still selected.
      </template>
    </div>

    <div v-if="activeError" class="ps-error" role="alert">
      {{ activeError }}
    </div>

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

/* The field box AppSelect draws (`--control-h`, `--radius-sm`, input fill), as
   a button, so it opens the app's menu rather than a native popup. min-width: 0
   so a long name ellipsises instead of widening the Settings column. */
.ps-trigger {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  height: var(--control-h);
  padding: 0 var(--space-4);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  text-align: left;
  cursor: pointer;
}

.ps-trigger-label {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ps-chevron {
  flex-shrink: 0;
  font-size: var(--gutter-glyph);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The menu row's leading glyph box (`--gutter-glyph`). Not `.ctx-icon`: that
   slot turns olive on the chosen row, and loaded is not the same fact as
   chosen. */
.ps-loaded-slot {
  display: inline-flex;
  flex-shrink: 0;
  width: var(--gutter-glyph);
  height: var(--gutter-glyph);
}

.ps-loaded {
  flex-shrink: 0;
  width: var(--gutter-glyph);
  height: var(--gutter-glyph);
  font-size: var(--gutter-glyph);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.ps-loaded--on {
  color: rgb(var(--v-theme-surface-success));
}

.ps-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
  margin-top: var(--space-2);
}

.ps-empty {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  padding: var(--space-3) 0;
}
</style>
