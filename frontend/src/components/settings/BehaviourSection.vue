<script setup>
import { onUnmounted, ref, watch } from "vue";
import { getUserConfig, patchUserConfig } from "../../api/config";
import { getWorkerProgress } from "../../api/workers";
import { listTaggers, listTaggerPluginDiagnostics } from "../../api/taggers";
import { VSlider, VSwitch } from "vuetify/components";
import PluginsTable from "../widgets/PluginsTable.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsTwoCol from "./SettingsTwoCol.vue";
import SettingsFieldBlock from "./SettingsFieldBlock.vue";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const keepModelsInMemory = ref(true);
const keepModelsInMemoryLoading = ref(false);
const keepModelsInMemoryError = ref("");
const taggerPlugins = ref([]);
const taggerSettings = ref({});
const taggerLoading = ref(false);
// null until the diagnostics request answers; "" once it has answered 403.
const taggerPluginDir = ref(null);
// Both come from the diagnostics route rather than the plugin list: a load
// error is exception text from a third-party plugin and can name any path on
// the host, so it is owner-and-local like the folder itself.
const taggerPluginErrors = ref([]);
const taggerCliHint = ref("pixlstash-cli plugins install <name-or-path>");
const taggerCliAvailableHint = ref("pixlstash-cli plugins available");
const taggerCliSearchHint = ref(
  "pixlstash-cli plugins available <search-term>",
);
const taggerCliListHint = ref("pixlstash-cli plugins list");
const pluginInstallHelpOpen = ref(false);

// ── VRAM budget ───────────────────────────────────────────────────────────────
const VRAM_BUDGET_MIN_GB = 2;
const VRAM_BUDGET_STEP_GB = 2;
const maxVramGbValue = ref(VRAM_BUDGET_MIN_GB);
const maxVramGbMax = ref(VRAM_BUDGET_MIN_GB);
const maxVramGbLoading = ref(false);
const maxVramGbError = ref("");
const maxVramGbSuccess = ref("");
const maxVramGbSavedValue = ref(null);
const maxVramGbHydrating = ref(false);
const maxVramGbAutoSaveReady = ref(false);
let maxVramGbSaveTimer = null;

function deriveMaxVramSliderMax(totalVramGb) {
  const total = Number(totalVramGb);
  if (!Number.isFinite(total) || total <= 0) return VRAM_BUDGET_MIN_GB;
  const available = total - 2;
  const stepped =
    Math.floor(available / VRAM_BUDGET_STEP_GB) * VRAM_BUDGET_STEP_GB;
  // No fixed ceiling: the backend validates against the card too, and a 32 GB
  // card now defaults to 16 GB - a slider capped at 12 could not show it.
  return Math.max(VRAM_BUDGET_MIN_GB, stepped);
}

function clampAndSnapVramBudget(value, upperBound = maxVramGbMax.value) {
  const maxValue = Math.max(
    VRAM_BUDGET_MIN_GB,
    Number(upperBound) || VRAM_BUDGET_MIN_GB,
  );
  const parsed = Number(value);
  const base = Number.isFinite(parsed) ? parsed : VRAM_BUDGET_MIN_GB;
  const clamped = Math.min(maxValue, Math.max(VRAM_BUDGET_MIN_GB, base));
  const stepped =
    Math.round(clamped / VRAM_BUDGET_STEP_GB) * VRAM_BUDGET_STEP_GB;
  return Math.min(maxValue, Math.max(VRAM_BUDGET_MIN_GB, stepped));
}

async function fetchVramSliderBounds() {
  try {
    const progress = await getWorkerProgress();
    const processData = progress?.process || progress?.system || {};
    const totalVramGb =
      processData.vram_total_gb ??
      processData.vramTotalGb ??
      processData.total_vram_gb;
    const derived = deriveMaxVramSliderMax(totalVramGb);
    // Only ever increase the bound - a transient low reading must not shrink the
    // slider and cause Vuetify to auto-clamp (and overwrite) the saved budget.
    if (derived > maxVramGbMax.value) maxVramGbMax.value = derived;
  } catch {
    // Leave maxVramGbMax unchanged on failure.
  }
}

function scheduleMaxVramGbSave() {
  if (
    !props.open ||
    maxVramGbHydrating.value ||
    !maxVramGbAutoSaveReady.value
  ) {
    return;
  }
  maxVramGbSuccess.value = "";
  if (maxVramGbSaveTimer) clearTimeout(maxVramGbSaveTimer);
  maxVramGbSaveTimer = setTimeout(() => {
    maxVramGbSaveTimer = null;
    saveMaxVramGb();
  }, 500);
}

async function saveMaxVramGb() {
  if (maxVramGbHydrating.value) return;
  maxVramGbLoading.value = true;
  maxVramGbError.value = "";
  const nextValue = clampAndSnapVramBudget(
    maxVramGbValue.value,
    Math.max(maxVramGbMax.value, maxVramGbValue.value),
  );
  if (maxVramGbSavedValue.value === nextValue) {
    maxVramGbLoading.value = false;
    return;
  }
  try {
    await patchUserConfig({ max_vram_gb: nextValue });
    maxVramGbSavedValue.value = nextValue;
    maxVramGbValue.value = nextValue;
    maxVramGbSuccess.value = "Saved.";
  } catch (e) {
    maxVramGbError.value = errorDetail(e) || "Failed to update VRAM budget.";
  } finally {
    maxVramGbLoading.value = false;
    if (maxVramGbSuccess.value) {
      setTimeout(() => {
        if (maxVramGbSuccess.value === "Saved.") maxVramGbSuccess.value = "";
      }, 2000);
    }
  }
}

async function fetchBehaviourSettings() {
  keepModelsInMemoryError.value = "";
  try {
    const cfg = await getUserConfig();
    if (typeof cfg?.keep_models_in_memory === "boolean") {
      keepModelsInMemory.value = cfg.keep_models_in_memory;
    } else {
      keepModelsInMemory.value = true;
    }
    await fetchVramSliderBounds();
    maxVramGbHydrating.value = true;
    const parsedMaxVram = Number(cfg?.max_vram_gb);
    const initialValue =
      Number.isFinite(parsedMaxVram) && parsedMaxVram > 0
        ? parsedMaxVram
        : VRAM_BUDGET_MIN_GB;
    if (initialValue > maxVramGbMax.value) maxVramGbMax.value = initialValue;
    const snappedValue = clampAndSnapVramBudget(
      initialValue,
      maxVramGbMax.value,
    );
    maxVramGbValue.value = snappedValue;
    maxVramGbSavedValue.value = snappedValue;
    maxVramGbAutoSaveReady.value = true;
    maxVramGbHydrating.value = false;
  } catch {
    maxVramGbAutoSaveReady.value = false;
    keepModelsInMemoryError.value = "Failed to load behaviour settings.";
  }
}

async function setKeepModelsInMemory(value) {
  keepModelsInMemoryLoading.value = true;
  keepModelsInMemoryError.value = "";
  try {
    const nextValue = Boolean(value);
    await patchUserConfig({ keep_models_in_memory: nextValue });
    keepModelsInMemory.value = nextValue;
  } catch (e) {
    keepModelsInMemoryError.value =
      errorDetail(e) || "Failed to update model memory setting.";
  } finally {
    keepModelsInMemoryLoading.value = false;
  }
}

async function fetchTaggerPlugins() {
  taggerLoading.value = true;
  try {
    const body = await listTaggers();
    taggerPlugins.value = body?.plugins ?? [];
    taggerSettings.value = body?.settings ?? {};
  } catch {
    // Both, not just the list: a failed refresh that cleared the plugins but
    // kept the settings showed the previous library's values against an empty
    // table (review of #937).
    taggerPlugins.value = [];
    taggerSettings.value = {};
  } finally {
    taggerLoading.value = false;
  }
  // Separate request: these name host paths, so the route is local-owner-only
  // and 403s for a remote or share-scoped caller. That is not an error - there
  // is simply nothing to show them.
  try {
    const diagnostics = await listTaggerPluginDiagnostics();
    taggerPluginDir.value = diagnostics?.plugin_dirs?.user ?? "";
    taggerPluginErrors.value = diagnostics?.load_errors ?? [];
    taggerCliHint.value = diagnostics?.cli_hint || taggerCliHint.value;
    taggerCliAvailableHint.value =
      diagnostics?.cli_available_hint || taggerCliAvailableHint.value;
    taggerCliSearchHint.value =
      diagnostics?.cli_search_hint || taggerCliSearchHint.value;
    taggerCliListHint.value =
      diagnostics?.cli_list_hint || taggerCliListHint.value;
  } catch {
    taggerPluginDir.value = "";
    taggerPluginErrors.value = [];
  }
}

async function refreshTaggerLoaded() {
  try {
    const body = await listTaggers();
    const fresh = body?.plugins ?? [];
    // Only update is_loaded on existing entries to avoid layout churn.
    taggerPlugins.value = taggerPlugins.value.map((p) => {
      const update = fresh.find((f) => f.name === p.name);
      return update ? { ...p, is_loaded: update.is_loaded } : p;
    });
  } catch {
    // Ignore poll errors silently.
  }
}

let taggerPollTimer = null;

function startTaggerPoll() {
  stopTaggerPoll();
  taggerPollTimer = setInterval(refreshTaggerLoaded, 5000);
}

function stopTaggerPoll() {
  if (taggerPollTimer !== null) {
    clearInterval(taggerPollTimer);
    taggerPollTimer = null;
  }
}

onUnmounted(() => {
  stopTaggerPoll();
  if (maxVramGbSaveTimer) clearTimeout(maxVramGbSaveTimer);
});

watch(maxVramGbValue, () => {
  scheduleMaxVramGbSave();
});

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      fetchBehaviourSettings();
      fetchTaggerPlugins();
      startTaggerPoll();
    } else {
      stopTaggerPoll();
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="behaviour-pane">
    <SettingsSection
      title="Model Memory"
      desc="Keep models loaded in RAM/VRAM for faster processing. Turn off to unload models when idle and save memory."
      first
    >
      <v-switch
        v-model="keepModelsInMemory"
        color="accent"
        density="compact"
        hide-details
        :disabled="keepModelsInMemoryLoading"
        label="Keep models in memory and VRAM"
        @update:model-value="setKeepModelsInMemory"
      />
      <div v-if="keepModelsInMemoryError" class="settings-error">
        {{ keepModelsInMemoryError }}
      </div>
    </SettingsSection>

    <SettingsSection title="VRAM Budget (GB)">
      <div class="vram-row">
        <span class="vram-value">{{ maxVramGbValue }} GB</span>
        <div class="vram-track">
          <v-slider
            v-model="maxVramGbValue"
            :min="VRAM_BUDGET_MIN_GB"
            :max="maxVramGbMax"
            :step="VRAM_BUDGET_STEP_GB"
            hide-details
            density="compact"
            color="accent"
            track-color="rgba(var(--v-theme-on-surface), 0.2)"
            :disabled="maxVramGbLoading || maxVramGbHydrating"
          />
        </div>
        <span class="vram-meta">
          max {{ maxVramGbMax }} GB
          <template v-if="maxVramGbError">
            · <span class="vram-err">{{ maxVramGbError }}</span>
          </template>
          <template v-else-if="maxVramGbSuccess">
            · {{ maxVramGbSuccess }}
          </template>
        </span>
      </div>
    </SettingsSection>

    <SettingsSection
      class="tagger-section"
      title="Auto-tagging"
      desc="Plugins that generate tags and captions automatically. Hint: you can also pick taggers for selected pictures in the tag panel or context menu."
    >
      <SettingsTwoCol class="tagger-cols">
        <SettingsFieldBlock class="tagger-col" title="Tag plugin" top>
          <div v-if="taggerLoading" class="settings-tagger-loading">
            Loading…
          </div>
          <PluginsTable
            v-else
            kind="tag"
            :plugins="taggerPlugins"
            :settings="taggerSettings"
            @update:settings="(s) => (taggerSettings.value = s)"
          />
        </SettingsFieldBlock>
        <SettingsFieldBlock class="tagger-col" title="Description plugin" top>
          <div v-if="taggerLoading" class="settings-tagger-loading">
            Loading…
          </div>
          <PluginsTable
            v-else
            kind="description"
            :plugins="taggerPlugins"
            :settings="taggerSettings"
            @update:settings="(s) => (taggerSettings.value = s)"
          />
        </SettingsFieldBlock>
      </SettingsTwoCol>
      <ul
        v-if="taggerPluginErrors.length"
        class="settings-tagger-plugin-errors"
      >
        <li v-for="(p, i) in taggerPluginErrors" :key="`${p.name}-${i}`">
          <strong>{{ p.name }}</strong> failed to load: {{ p.message }}
        </li>
      </ul>
      <div class="settings-tagger-plugin-help">
        <v-btn variant="text" size="small" prepend-icon="mdi-help-circle-outline" @click="pluginInstallHelpOpen = true">
          How to install plugins
        </v-btn>
      </div>
    </SettingsSection>

    <v-dialog
      v-model="pluginInstallHelpOpen"
      max-width="560"
      @click:outside="pluginInstallHelpOpen = false"
    >
      <v-card class="plugin-install-card">
        <v-card-title class="plugin-install-title">
          How to install plugins
        </v-card-title>
        <v-card-text class="plugin-install-help-body">
          <p>
            Only install plugins you trust. They run with the same access as
            PixlStash.
          </p>

          <h3>Find plugins</h3>
          <p>
            Browse the
            <a
              class="plugin-catalogue-link"
              href="https://github.com/Pikselkroken/PixlStash-plugins"
              target="_blank"
              rel="noopener noreferrer"
            >
              official plugin catalogue
              <v-icon size="x-small" aria-hidden="true">mdi-open-in-new</v-icon>
            </a>
            or use the CLI to list published plugins, search the catalogue, and
            show installed plugins.
          </p>
          <div class="plugin-install-commands">
            <pre class="plugin-install-command"><code>{{ taggerCliAvailableHint }}</code></pre>
            <pre class="plugin-install-command"><code>{{ taggerCliSearchHint }}</code></pre>
            <pre class="plugin-install-command"><code>{{ taggerCliListHint }}</code></pre>
          </div>

          <h3>Install with the CLI</h3>
          <pre class="plugin-install-command"><code>{{ taggerCliHint }}</code></pre>
          <p>
            Use a repository name or local file/folder, then restart PixlStash.
          </p>

          <h3>Install manually</h3>
          <p v-if="taggerPluginDir">
            Put the plugin file or folder in
            <code class="settings-tagger-plugin-path">{{ taggerPluginDir }}</code>
            and restart PixlStash.
          </p>
          <p v-else>
            Put the plugin file or folder in PixlStash's custom plugin folder
            and restart. Open this screen locally to see the exact path.
          </p>
        </v-card-text>
        <v-card-actions class="plugin-install-actions">
          <v-spacer />
          <v-btn variant="text" @click="pluginInstallHelpOpen = false">
            Close
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
/* The plugin lists scroll, the pane does not: the pane is a column, the
   Auto-tagging section takes the leftover height, and each column's plugin
   table is the only thing that overflows. The scroll box wraps the whole
   table, header included, so PluginsTable pins its header row (see the
   `position: sticky` there). Without this the two tables push the whole
   Settings pane past its fixed height and the dialog grows a scrollbar. */
.behaviour-pane {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.tagger-section {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

/* A floor, not 0: on a short window the pane is allowed to overflow and scroll
   as it used to rather than crush the tables to nothing. */
.tagger-cols {
  flex: 1 1 auto;
  min-height: 96px;
}

/* min-width as well as min-height: a grid item's automatic minimum is its
   min-content, so a table that refuses to wrap widens its own 1fr track and
   pushes the whole pane sideways. That is a horizontal scrollbar on the pane,
   not on the table, and it is why PluginsTable lets a long plugin name wrap. */
.tagger-col {
  min-width: 0;
  min-height: 0;
}

.tagger-col :deep(.field-block__control) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.settings-tagger-loading {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.55);
  padding: var(--space-3) 0;
}

.settings-tagger-plugin-dir {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.55);
  padding-top: var(--space-3);
}

.settings-tagger-plugin-help {
  padding-top: var(--space-2);
}

.plugin-install-card {
  color: rgb(var(--v-theme-on-surface));
  background: rgb(var(--v-theme-surface));
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-4);
}

.plugin-install-title {
  padding: var(--space-5) var(--space-5) var(--space-3);
  font-family: var(--font-ui);
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}

.plugin-install-help-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5) var(--space-2);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.plugin-install-help-body p {
  margin: 0;
}

.plugin-install-help-body h3 {
  margin: var(--space-2) 0 0;
  color: rgba(var(--v-theme-on-surface), 0.65);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.plugin-install-help-body :not(pre) > code {
  padding: 0 var(--space-1);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.08);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: inherit;
  overflow-wrap: anywhere;
}

.plugin-catalogue-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.plugin-catalogue-link:hover,
.plugin-catalogue-link:active {
  text-decoration-thickness: 2px;
}

.plugin-catalogue-link:focus-visible {
  border-radius: var(--radius-sm);
  outline: none;
  box-shadow: var(--focus-ring);
}

.plugin-install-commands {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.plugin-install-command {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-surface));
  background: rgb(var(--v-theme-background));
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: var(--weight-regular);
  line-height: var(--leading-snug);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  word-break: break-word;
}

.plugin-install-command code {
  color: inherit;
  font: inherit;
}

.plugin-install-actions {
  min-height: 40px;
  padding: var(--space-3) var(--space-5) var(--space-4);
}

.settings-tagger-plugin-path {
  overflow-wrap: anywhere;
}

@media (max-width: 480px) {
  .plugin-install-title {
    padding-right: var(--space-4);
    padding-left: var(--space-4);
  }

  .plugin-install-help-body {
    padding-right: var(--space-4);
    padding-left: var(--space-4);
  }

  .plugin-install-actions {
    padding-right: var(--space-4);
    padding-left: var(--space-4);
  }
}

/* A plugin's load error is exception text from third-party code and has no
   length limit, so it is bounded and scrolled too - otherwise it is a second
   unbounded thing in this section and squeezes the tables it sits under. */
.settings-tagger-plugin-errors {
  list-style: none;
  padding: 0;
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  overflow-wrap: anywhere;
  flex-shrink: 0;
  max-height: 72px;
  overflow-y: auto;
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: var(--text-xs);
  margin-top: var(--space-2);
}

/* Compact one-line VRAM control: value · slider · inline max/status. */
.vram-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.vram-value {
  min-width: 52px;
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-surface));
}

.vram-track {
  flex: 1;
}

.vram-meta {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
}

.vram-err {
  color: rgb(var(--v-theme-error));
}
</style>
