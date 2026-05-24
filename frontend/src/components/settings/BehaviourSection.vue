<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";
import TagPluginsTable from "../widgets/TagPluginsTable.vue";
import DescriptionPluginsTable from "../widgets/DescriptionPluginsTable.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  checkForUpdates: { type: Boolean, default: null },
});

const emit = defineEmits([
  "update:hidden-tags",
  "update:apply-tag-filter",
  "update:check-for-updates",
]);

const checkForUpdatesModel = computed({
  get: () => props.checkForUpdates ?? false,
  set: (value) => emit("update:check-for-updates", value),
});

const hiddenTags = ref([]);
const hiddenTagInput = ref("");
const hiddenTagsLoading = ref(false);
const hiddenTagsError = ref("");
const hiddenTagsSuccess = ref("");
const applyTagFilter = ref(false);
const applyTagFilterLoading = ref(false);
const keepModelsInMemory = ref(true);
const keepModelsInMemoryLoading = ref(false);
const keepModelsInMemoryError = ref("");
const taggerPlugins = ref([]);
const taggerSettings = ref({});
const taggerLoading = ref(false);
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

function resetForm() {
  hiddenTagInput.value = "";
  hiddenTagsError.value = "";
  hiddenTagsSuccess.value = "";
  keepModelsInMemoryError.value = "";
  maxVramGbError.value = "";
  maxVramGbSuccess.value = "";
  maxVramGbValue.value = VRAM_BUDGET_MIN_GB;
  maxVramGbMax.value = VRAM_BUDGET_MIN_GB;
  maxVramGbSavedValue.value = null;
  maxVramGbAutoSaveReady.value = false;
  if (maxVramGbSaveTimer) {
    clearTimeout(maxVramGbSaveTimer);
    maxVramGbSaveTimer = null;
  }
}

function deriveMaxVramSliderMax(totalVramGb) {
  const total = Number(totalVramGb);
  if (!Number.isFinite(total) || total <= 0) {
    return VRAM_BUDGET_MIN_GB;
  }
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
    const res = await apiClient.get("/workers/progress");
    const processData = res.data?.process || res.data?.system || {};
    const totalVramGb =
      processData.vram_total_gb ??
      processData.vramTotalGb ??
      processData.total_vram_gb;
    const derived = deriveMaxVramSliderMax(totalVramGb);
    // Only ever increase maxVramGbMax — never reduce it. A transient low
    // available-VRAM reading must not shrink the slider and cause Vuetify to
    // auto-clamp (and thus overwrite) the user's saved budget.
    if (derived > maxVramGbMax.value) {
      maxVramGbMax.value = derived;
    }
  } catch (e) {
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
  if (maxVramGbSaveTimer) {
    clearTimeout(maxVramGbSaveTimer);
    maxVramGbSaveTimer = null;
  }
  maxVramGbSaveTimer = setTimeout(() => {
    maxVramGbSaveTimer = null;
    saveMaxVramGb();
  }, 500);
}

async function saveMaxVramGb() {
  if (maxVramGbHydrating.value) return;
  maxVramGbLoading.value = true;
  maxVramGbError.value = "";
  // Snap to step and enforce the minimum only — do not cap against maxVramGbMax
  // so that a stale or temporarily-low slider bound cannot corrupt the saved value.
  const nextValue = clampAndSnapVramBudget(
    maxVramGbValue.value,
    Math.max(maxVramGbMax.value, maxVramGbValue.value),
  );
  if (maxVramGbSavedValue.value === nextValue) {
    maxVramGbLoading.value = false;
    return;
  }
  try {
    await apiClient.patch("/users/me/config", {
      max_vram_gb: nextValue,
    });
    maxVramGbSavedValue.value = nextValue;
    maxVramGbValue.value = nextValue;
    maxVramGbSuccess.value = "Saved. Applied immediately.";
  } catch (e) {
    maxVramGbError.value =
      e?.response?.data?.detail || "Failed to update VRAM budget.";
  } finally {
    maxVramGbLoading.value = false;
    if (maxVramGbSuccess.value) {
      setTimeout(() => {
        if (maxVramGbSuccess.value === "Saved. Applied immediately.") {
          maxVramGbSuccess.value = "";
        }
      }, 2000);
    }
  }
}

function normalizeHiddenTags(tags) {
  const values = Array.isArray(tags)
    ? tags
    : tags && typeof tags === "object"
      ? Object.keys(tags)
      : [];
  const seen = new Set();
  const cleaned = [];
  for (const tag of values) {
    if (tag == null) continue;
    const clean = String(tag).trim().toLowerCase();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    cleaned.push(clean);
  }
  return cleaned.sort((a, b) => a.localeCompare(b));
}

function areStringListsEqual(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b)) return false;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

async function fetchBehaviourSettings() {
  hiddenTagsLoading.value = true;
  hiddenTagsError.value = "";
  try {
    const res = await apiClient.get("/users/me/config");
    const nextHiddenTags = normalizeHiddenTags(res.data?.hidden_tags);
    const currentHiddenTags = normalizeHiddenTags(hiddenTags.value);
    if (!areStringListsEqual(nextHiddenTags, currentHiddenTags)) {
      hiddenTags.value = nextHiddenTags;
      emit("update:hidden-tags", hiddenTags.value);
    }
    const nextApplyTagFilter = Boolean(res.data?.apply_tag_filter);
    if (applyTagFilter.value !== nextApplyTagFilter) {
      applyTagFilter.value = nextApplyTagFilter;
      emit("update:apply-tag-filter", applyTagFilter.value);
    }
    if (typeof res.data?.keep_models_in_memory === "boolean") {
      keepModelsInMemory.value = res.data.keep_models_in_memory;
    } else {
      keepModelsInMemory.value = true;
    }
    await fetchVramSliderBounds();
    maxVramGbHydrating.value = true;
    const parsedMaxVram = Number(res.data?.max_vram_gb);
    const initialValue =
      Number.isFinite(parsedMaxVram) && parsedMaxVram > 0
        ? parsedMaxVram
        : VRAM_BUDGET_MIN_GB;
    // Ensure the slider's upper bound is at least the saved value before we
    // assign it to v-model. Vuetify auto-clamps v-model to [min, max], so if
    // maxVramGbMax is still at the fallback minimum (e.g. fetchVramSliderBounds
    // failed), we'd otherwise silently reduce — and then save — the user's budget.
    if (initialValue > maxVramGbMax.value) {
      maxVramGbMax.value = initialValue;
    }
    const snappedValue = clampAndSnapVramBudget(
      initialValue,
      maxVramGbMax.value,
    );
    maxVramGbValue.value = snappedValue;
    maxVramGbSavedValue.value = snappedValue;
    maxVramGbAutoSaveReady.value = true;
    maxVramGbError.value = "";
    maxVramGbSuccess.value = "";
    maxVramGbHydrating.value = false;
  } catch (e) {
    maxVramGbAutoSaveReady.value = false;
    hiddenTagsError.value = "Failed to load behaviour settings.";
  } finally {
    hiddenTagsLoading.value = false;
  }
}

async function saveHiddenTags(nextTags) {
  hiddenTagsLoading.value = true;
  hiddenTagsError.value = "";
  hiddenTagsSuccess.value = "";
  try {
    const normalized = normalizeHiddenTags(nextTags);
    await apiClient.patch("/users/me/config", {
      hidden_tags: normalized,
    });
    hiddenTags.value = normalized;
    emit("update:hidden-tags", hiddenTags.value);
    hiddenTagsSuccess.value = "Saved.";
  } catch (e) {
    hiddenTagsError.value =
      e?.response?.data?.detail || "Failed to update hidden tags.";
  } finally {
    hiddenTagsLoading.value = false;
    if (hiddenTagsSuccess.value) {
      setTimeout(() => {
        hiddenTagsSuccess.value = "";
      }, 2000);
    }
  }
}

async function addHiddenTag() {
  const trimmed = hiddenTagInput.value.trim().toLowerCase();
  if (!trimmed) return;
  const next = normalizeHiddenTags([...hiddenTags.value, trimmed]);
  hiddenTagInput.value = "";
  await saveHiddenTags(next);
}

async function removeHiddenTag(tag) {
  const next = normalizeHiddenTags(
    hiddenTags.value.filter((entry) => entry !== tag),
  );
  await saveHiddenTags(next);
}

async function setApplyTagFilter(value) {
  applyTagFilterLoading.value = true;
  hiddenTagsError.value = "";
  try {
    const nextValue = Boolean(value);
    await apiClient.patch("/users/me/config", {
      apply_tag_filter: nextValue,
    });
    applyTagFilter.value = nextValue;
    emit("update:apply-tag-filter", applyTagFilter.value);
  } catch (e) {
    hiddenTagsError.value =
      e?.response?.data?.detail || "Failed to update tag filter.";
  } finally {
    applyTagFilterLoading.value = false;
  }
}

async function setKeepModelsInMemory(value) {
  keepModelsInMemoryLoading.value = true;
  keepModelsInMemoryError.value = "";
  try {
    const nextValue = Boolean(value);
    await apiClient.patch("/users/me/config", {
      keep_models_in_memory: nextValue,
    });
    keepModelsInMemory.value = nextValue;
  } catch (e) {
    keepModelsInMemoryError.value =
      e?.response?.data?.detail || "Failed to update model memory setting.";
  } finally {
    keepModelsInMemoryLoading.value = false;
  }
}

async function fetchTaggerPlugins() {
  taggerLoading.value = true;
  try {
    const res = await apiClient.get("/taggers");
    taggerPlugins.value = res.data?.plugins ?? [];
    taggerSettings.value = res.data?.settings ?? {};
  } catch {
    taggerPlugins.value = [];
  } finally {
    taggerLoading.value = false;
  }
}

async function refreshTaggerLoaded() {
  try {
    const res = await apiClient.get("/taggers");
    const fresh = res.data?.plugins ?? [];
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

onUnmounted(stopTaggerPoll);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      resetForm();
      fetchBehaviourSettings();
      fetchTaggerPlugins();
      startTaggerPoll();
    } else {
      stopTaggerPoll();
    }
  },
  { immediate: true },
);

watch(maxVramGbValue, () => {
  scheduleMaxVramGbSave();
});
</script>

<template>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Updates</div>
    <v-checkbox
      v-model="checkForUpdatesModel"
      density="compact"
      hide-details
      label="Check for updates automatically (anonymous count only)"
    />
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Tag Plugins</div>
    <div class="settings-section-desc">
      Select which plugin generates tags automatically. Hint: you can pick
      taggers for selected pictures in the tag panel or context menu.
    </div>
    <div v-if="taggerLoading" class="settings-tagger-loading">Loading…</div>
    <TagPluginsTable
      v-else
      :plugins="taggerPlugins"
      :settings="taggerSettings"
      @update:settings="(s) => (taggerSettings.value = s)"
    />
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Description Plugin</div>
    <div class="settings-section-desc">
      Select which plugin generates image descriptions (captions) automatically.
    </div>
    <div v-if="taggerLoading" class="settings-tagger-loading">Loading…</div>
    <DescriptionPluginsTable
      v-else
      :plugins="taggerPlugins"
      :settings="taggerSettings"
      @update:settings="(s) => (taggerSettings.value = s)"
    />
  </div>

  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Model Memory</div>
    <div class="settings-section-desc">
      Keep models loaded in RAM/VRAM for faster processing. Turn off to unload
      models when idle and save memory.
    </div>
    <v-checkbox
      v-model="keepModelsInMemory"
      class="settings-tag-filter-toggle"
      density="compact"
      hide-details
      :disabled="keepModelsInMemoryLoading"
      label="Keep models in memory and VRAM"
      @update:model-value="setKeepModelsInMemory"
    />
    <div v-if="keepModelsInMemoryError" class="settings-error">
      {{ keepModelsInMemoryError }}
    </div>
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div
      class="settings-section-title"
      title="Maximum VRAM budget for tagging tasks. Changes apply immediately."
    >
      VRAM Budget (GB)
    </div>
    <div class="settings-slider-row">
      <span class="settings-slider-value">{{ maxVramGbValue }} GB</span>
      <v-slider
        v-model="maxVramGbValue"
        :min="VRAM_BUDGET_MIN_GB"
        :max="maxVramGbMax"
        :step="VRAM_BUDGET_STEP_GB"
        hide-details
        track-color="#666"
        thumb-color="primary"
        class="settings-slider"
        :disabled="maxVramGbLoading || maxVramGbHydrating"
      />
    </div>
    <div class="settings-section-desc">
      Range: {{ VRAM_BUDGET_MIN_GB }}–{{ maxVramGbMax }} GB (step
      {{ VRAM_BUDGET_STEP_GB }} GB)
    </div>
    <div class="settings-form">
      <div v-if="maxVramGbError" class="settings-error">
        {{ maxVramGbError }}
      </div>
      <div v-else-if="maxVramGbSuccess" class="settings-success">
        {{ maxVramGbSuccess }}
      </div>
      <div v-else class="settings-success">
        {{ "\u00a0" }}
      </div>
    </div>
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Tag Filter</div>
    <div class="settings-section-desc">
      Tags listed here are filtered from the GUI entirely.
    </div>
    <v-checkbox
      v-model="applyTagFilter"
      class="settings-tag-filter-toggle"
      density="compact"
      hide-details
      :disabled="applyTagFilterLoading"
      label="Apply tag filter to all pictures and videos"
      @update:model-value="setApplyTagFilter"
    />
    <div class="settings-tag-list">
      <div
        v-for="tag in hiddenTags"
        :key="tag"
        class="settings-tag-chip settings-tag-chip--row"
      >
        <span class="settings-tag-label">{{ tag }}</span>
        <v-btn
          icon
          variant="text"
          class="settings-tag-delete"
          :disabled="hiddenTagsLoading"
          @click="removeHiddenTag(tag)"
        >
          <v-icon size="16">mdi-close</v-icon>
        </v-btn>
      </div>
      <div
        v-if="!hiddenTagsLoading && !hiddenTags.length"
        class="settings-token-empty"
      >
        No hidden tags yet.
      </div>
    </div>
    <div class="settings-form">
      <div class="settings-add-tag-row">
        <v-text-field
          v-model="hiddenTagInput"
          label="Add tag filter"
          density="compact"
          variant="filled"
          class="settings-add-tag-input"
          :disabled="hiddenTagsLoading"
          @keydown.enter.prevent="addHiddenTag"
        />
        <v-btn
          variant="outlined"
          color="primary"
          class="settings-action-btn"
          :loading="hiddenTagsLoading"
          :disabled="hiddenTagsLoading"
          @click="addHiddenTag"
        >
          Add Tag
        </v-btn>
      </div>
      <div v-if="hiddenTagsError" class="settings-error">
        {{ hiddenTagsError }}
      </div>
      <div v-else-if="hiddenTagsSuccess" class="settings-success">
        {{ hiddenTagsSuccess }}
      </div>
      <div v-else class="settings-success">
        {{ "&nbsp;" }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-section {
  display: flex;
  line-height: 1;
  flex-direction: column;
  gap: 6px;
}

.settings-section-title {
  font-weight: 600;
}

.settings-section-desc {
  font-size: 0.92em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-section-divider {
  margin: 4px 0 8px;
}

.settings-tagger-loading {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.55);
  padding: 6px 0;
}

.settings-tag-filter-toggle {
  flex-shrink: 0;
}

.settings-slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
  padding-right: 8px;
}

.settings-slider-value {
  min-width: 64px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
}

.settings-slider {
  flex: 1 1 auto;
  margin-right: 6px;
  overflow: visible;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-add-tag-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.settings-add-tag-input {
  flex: 1 1 auto;
}

.settings-tag-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.settings-tag-list .settings-token-empty {
  grid-column: 1 / -1;
}

.settings-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.settings-tag-chip--row {
  width: 100%;
  justify-content: space-between;
  padding-right: 4px;
}

.settings-tag-label {
  font-size: 1em;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

.settings-tag-delete {
  color: rgba(var(--v-theme-on-surface), 0.65);
  min-width: 0;
  height: 12px;
  width: 12px;
  padding: 2;
}

.settings-tag-delete:hover {
  color: rgba(var(--v-theme-error), 0.9);
  min-width: 0;
  padding: 2;
}

.settings-token-empty {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.9em;
}

.settings-success {
  color: rgb(var(--v-theme-accent));
  font-size: 0.9em;
}

.settings-action-btn {
  align-self: flex-start;
  background-color: rgb(var(--v-theme-primary)) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}

.settings-action-btn:hover {
  background-color: rgb(var(--v-theme-accent)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}
</style>
