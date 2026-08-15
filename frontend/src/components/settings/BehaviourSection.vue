<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import { getUserConfig, patchUserConfig } from "../../api/config";
import { getWorkerProgress } from "../../api/workers";
import { listTaggers, listTaggerPluginDirs } from "../../api/taggers";
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
const taggerPluginDir = ref("");
// PluginsTable filters on the capability flags, and a plugin that failed to
// load has neither — so these rows render nowhere unless we list them here.
const taggerPluginErrors = computed(() =>
  taggerPlugins.value.filter((p) => p.load_error),
);

// ── VRAM budget ───────────────────────────────────────────────────────────────
const VRAM_BUDGET_MIN_GB = 2;
const VRAM_BUDGET_STEP_GB = 2;
const VRAM_BUDGET_MAX_GB = 12;
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
  return Math.min(VRAM_BUDGET_MAX_GB, Math.max(VRAM_BUDGET_MIN_GB, stepped));
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
    // Only ever increase the bound — a transient low reading must not shrink the
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
    taggerPlugins.value = [];
    taggerSettings.value = {};
  } finally {
    taggerLoading.value = false;
  }
  // Separate request: the folder is a host path, so it is local-owner-only and
  // 403s for a remote or share-scoped caller. That is not an error — there is
  // simply no folder to show them.
  try {
    const dirs = await listTaggerPluginDirs();
    taggerPluginDir.value = dirs?.plugin_dirs?.user ?? "";
  } catch {
    taggerPluginDir.value = "";
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
  <div>
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
      title="Auto-tagging"
      desc="Plugins that generate tags and captions automatically. Hint: you can also pick taggers for selected pictures in the tag panel or context menu."
    >
      <SettingsTwoCol>
        <SettingsFieldBlock title="Tag plugin" top>
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
        <SettingsFieldBlock title="Description plugin" top>
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
          <strong>{{ p.name }}</strong> failed to load: {{ p.load_error }}
        </li>
      </ul>
      <div v-if="taggerPluginDir" class="settings-tagger-plugin-dir">
        Add your own plugin by dropping a <code>.py</code> file (or a folder
        with an <code>__init__.py</code>) into
        <code class="settings-tagger-plugin-path">{{ taggerPluginDir }}</code
        >, creating the folder if it does not exist. Restart the server
        afterwards — plugins are only discovered at start-up. Plugins run as
        ordinary Python with your permissions, so only add ones you trust.
      </div>
    </SettingsSection>
  </div>
</template>

<style scoped>
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

.settings-tagger-plugin-path {
  overflow-wrap: anywhere;
}

.settings-tagger-plugin-errors {
  list-style: none;
  padding: 0;
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  overflow-wrap: anywhere;
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
