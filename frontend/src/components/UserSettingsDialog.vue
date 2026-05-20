<script setup>
import { computed, ref, watch } from "vue";
import { apiClient, isReadOnly, logout } from "../utils/apiClient";
import AccountSection from "./AccountSection.vue";
import AppearanceSection from "./AppearanceSection.vue";
import SmartScoreSection from "./SmartScoreSection.vue";
import WorkflowsSection from "./WorkflowsSection.vue";

const appVersion = __APP_VERSION__;

const props = defineProps({
  open: { type: Boolean, default: false },
  sidebarThumbnailSize: { type: Number, default: 32 },
  dateFormat: { type: String, default: "locale" },
  themeMode: { type: String, default: "light" },
  checkForUpdates: { type: Boolean, default: null },
  showKeyboardHint: { type: Boolean, default: true },
});

const emit = defineEmits([
  "update:open",
  "update:sidebar-thumbnail-size",
  "update:date-format",
  "update:theme-mode",
  "update:hidden-tags",
  "update:apply-tag-filter",
  "update:comfyui-configured",
  "update:check-for-updates",
  "update:show-keyboard-hint",
  "update:public-url",
]);

const dialogOpen = computed({
  get: () => props.open,
  set: (value) => emit("update:open", value),
});

const checkForUpdatesModel = computed({
  get: () => props.checkForUpdates ?? false,
  set: (value) => emit("update:check-for-updates", value),
});

const settingsTab = ref("appearance");
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
const wd14TaggerEnabled = ref(true);
const wd14TaggerLoading = ref(false);
const wd14TaggerError = ref("");
const customTaggerEnabled = ref(true);
const customTaggerLoading = ref(false);
const customTaggerError = ref("");
const WD14_THRESHOLD_DEFAULT = 85;
const CUSTOM_TAGGER_THRESHOLD_OFFSET_DEFAULT = 0;
const wd14ThresholdValue = ref(WD14_THRESHOLD_DEFAULT);
const wd14ThresholdLoading = ref(false);
const wd14ThresholdError = ref("");
const customTaggerThresholdOffsetValue = ref(
  CUSTOM_TAGGER_THRESHOLD_OFFSET_DEFAULT,
);
const customTaggerThresholdOffsetLoading = ref(false);
const customTaggerThresholdOffsetError = ref("");
const labelThresholdsDialogOpen = ref(false);
const labelThresholdsData = ref([]);
const labelThresholdsLoading = ref(false);
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

function resetSettingsForm() {
  hiddenTagInput.value = "";
  hiddenTagsError.value = "";
  hiddenTagsSuccess.value = "";
  keepModelsInMemoryError.value = "";
  wd14TaggerError.value = "";
  customTaggerError.value = "";
  wd14ThresholdError.value = "";
  customTaggerThresholdOffsetError.value = "";
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
    !dialogOpen.value ||
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
    wd14TaggerEnabled.value = res.data?.wd14_tagger_enabled !== false;
    customTaggerEnabled.value = res.data?.custom_tagger_enabled !== false;
    const parsedWd14Threshold = Number(res.data?.wd14_threshold);
    wd14ThresholdValue.value =
      Number.isFinite(parsedWd14Threshold) && parsedWd14Threshold > 0
        ? Math.round(parsedWd14Threshold * 100)
        : WD14_THRESHOLD_DEFAULT;
    const parsedCustomOffset = Number(res.data?.custom_tagger_threshold_offset);
    customTaggerThresholdOffsetValue.value = Number.isFinite(parsedCustomOffset)
      ? Math.round(parsedCustomOffset * 100)
      : CUSTOM_TAGGER_THRESHOLD_OFFSET_DEFAULT;
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

async function setWd14TaggerEnabled(value) {
  wd14TaggerLoading.value = true;
  wd14TaggerError.value = "";
  try {
    const nextValue = Boolean(value);
    await apiClient.patch("/users/me/config", {
      wd14_tagger_enabled: nextValue,
    });
    wd14TaggerEnabled.value = nextValue;
  } catch (e) {
    wd14TaggerError.value =
      e?.response?.data?.detail || "Failed to update WD14 tagger setting.";
  } finally {
    wd14TaggerLoading.value = false;
  }
}

async function setCustomTaggerEnabled(value) {
  customTaggerLoading.value = true;
  customTaggerError.value = "";
  try {
    const nextValue = Boolean(value);
    await apiClient.patch("/users/me/config", {
      custom_tagger_enabled: nextValue,
    });
    customTaggerEnabled.value = nextValue;
  } catch (e) {
    customTaggerError.value =
      e?.response?.data?.detail || "Failed to update custom tagger setting.";
  } finally {
    customTaggerLoading.value = false;
  }
}

async function saveWd14Threshold() {
  wd14ThresholdLoading.value = true;
  wd14ThresholdError.value = "";
  try {
    const pct = Math.min(
      100,
      Math.max(1, Math.round(Number(wd14ThresholdValue.value))),
    );
    await apiClient.patch("/users/me/config", { wd14_threshold: pct / 100 });
    wd14ThresholdValue.value = pct;
  } catch (e) {
    wd14ThresholdError.value =
      e?.response?.data?.detail || "Failed to update WD14 tagger threshold.";
  } finally {
    wd14ThresholdLoading.value = false;
  }
}

async function saveCustomTaggerThresholdOffset() {
  customTaggerThresholdOffsetLoading.value = true;
  customTaggerThresholdOffsetError.value = "";
  try {
    const pct = Math.min(
      50,
      Math.max(-50, Math.round(Number(customTaggerThresholdOffsetValue.value))),
    );
    await apiClient.patch("/users/me/config", {
      custom_tagger_threshold_offset: pct / 100,
    });
    customTaggerThresholdOffsetValue.value = pct;
  } catch (e) {
    customTaggerThresholdOffsetError.value =
      e?.response?.data?.detail ||
      "Failed to update PixlStash tagger threshold offset.";
  } finally {
    customTaggerThresholdOffsetLoading.value = false;
  }
}

async function openLabelThresholdsDialog() {
  labelThresholdsDialogOpen.value = true;
  labelThresholdsLoading.value = true;
  try {
    const res = await apiClient.get("/tagger/label-thresholds");
    labelThresholdsData.value = res.data;
  } catch (_) {
    labelThresholdsData.value = [];
  } finally {
    labelThresholdsLoading.value = false;
  }
}

watch(
  () => dialogOpen.value,
  (isOpen) => {
    if (isOpen) {
      resetSettingsForm();
      settingsTab.value = "appearance";
      if (!isReadOnly.value) {
        fetchBehaviourSettings();
      }
    }
  },
);

watch(maxVramGbValue, () => {
  scheduleMaxVramGbSave();
});
</script>

<template>
  <v-dialog
    v-model="dialogOpen"
    max-width="950"
    @click:outside="dialogOpen = false"
  >
    <div class="settings-dialog-shell">
      <v-btn
        icon
        size="36px"
        class="settings-dialog-close"
        @click="dialogOpen = false"
      >
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="settings-dialog-card">
        <v-card-title class="settings-dialog-title">
          Settings
          <span class="settings-dialog-version">v{{ appVersion }}</span>
          <v-btn
            variant="text"
            size="small"
            class="settings-logout-btn"
            title="Log out"
            @click="logout"
          >
            <v-icon size="16" class="settings-logout-icon">mdi-logout</v-icon>
            Log out
          </v-btn>
        </v-card-title>
        <v-tabs
          v-model="settingsTab"
          density="compact"
          class="settings-tabs"
          show-arrows
        >
          <v-tab value="appearance">Appearance</v-tab>
          <v-tab v-if="!isReadOnly" value="behaviour">Behaviour</v-tab>
          <v-tab v-if="!isReadOnly" value="smart-score">Smart Score</v-tab>
          <v-tab v-if="!isReadOnly" value="workflows">Workflows</v-tab>
          <v-tab v-if="!isReadOnly" value="account">Account Settings</v-tab>
        </v-tabs>
        <v-card-text class="settings-dialog-body">
          <v-window v-model="settingsTab" class="settings-tab-body">
            <v-window-item value="appearance">
              <AppearanceSection
                :sidebar-thumbnail-size="props.sidebarThumbnailSize"
                :theme-mode="props.themeMode"
                :date-format="props.dateFormat"
                :show-keyboard-hint="props.showKeyboardHint"
                @update:sidebar-thumbnail-size="emit('update:sidebar-thumbnail-size', $event)"
                @update:theme-mode="emit('update:theme-mode', $event)"
                @update:date-format="emit('update:date-format', $event)"
                @update:show-keyboard-hint="emit('update:show-keyboard-hint', $event)"
              />
            </v-window-item>
            <v-window-item value="behaviour">
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
                <div class="settings-section-title">Taggers</div>
                <div class="settings-section-desc">
                  Enable or disable each tagger and set its confidence
                  threshold. Changes apply immediately.
                </div>
                <div class="settings-tagger-row">
                  <v-tooltip text="Primarily trained on anime" location="top">
                    <template #activator="{ props: tooltipProps }">
                      <v-checkbox
                        v-bind="tooltipProps"
                        v-model="wd14TaggerEnabled"
                        class="settings-tag-filter-toggle settings-tagger-checkbox"
                        density="compact"
                        hide-details
                        :disabled="wd14TaggerLoading"
                        label="WD14 Tagger"
                        @update:model-value="setWd14TaggerEnabled"
                      />
                    </template>
                  </v-tooltip>
                  <div
                    class="settings-stepper"
                    :class="{
                      'settings-stepper--disabled':
                        wd14ThresholdLoading || !wd14TaggerEnabled,
                    }"
                  >
                    <span class="settings-stepper-label">Threshold (%)</span>
                    <div class="settings-stepper-controls">
                      <button
                        class="settings-stepper-btn"
                        :disabled="
                          wd14ThresholdLoading ||
                          !wd14TaggerEnabled ||
                          wd14ThresholdValue <= 1
                        "
                        @click="
                          () => {
                            wd14ThresholdValue = Math.max(
                              1,
                              wd14ThresholdValue - 1,
                            );
                            saveWd14Threshold();
                          }
                        "
                      >
                        −
                      </button>
                      <span class="settings-stepper-value">{{
                        wd14ThresholdValue
                      }}</span>
                      <button
                        class="settings-stepper-btn"
                        :disabled="
                          wd14ThresholdLoading ||
                          !wd14TaggerEnabled ||
                          wd14ThresholdValue >= 100
                        "
                        @click="
                          () => {
                            wd14ThresholdValue = Math.min(
                              100,
                              wd14ThresholdValue + 1,
                            );
                            saveWd14Threshold();
                          }
                        "
                      >
                        +
                      </button>
                    </div>
                  </div>
                  <v-tooltip
                    text="Primarily trained on real/realistic images"
                    location="top"
                  >
                    <template #activator="{ props: tooltipProps }">
                      <v-checkbox
                        v-bind="tooltipProps"
                        v-model="customTaggerEnabled"
                        class="settings-tag-filter-toggle settings-tagger-checkbox"
                        density="compact"
                        hide-details
                        :disabled="customTaggerLoading"
                        label="PixlStash Tagger"
                        @update:model-value="setCustomTaggerEnabled"
                      />
                    </template>
                  </v-tooltip>
                  <div
                    class="settings-stepper"
                    :class="{
                      'settings-stepper--disabled':
                        customTaggerThresholdOffsetLoading ||
                        !customTaggerEnabled,
                    }"
                  >
                    <span class="settings-stepper-label">Offset (%pt)</span>
                    <div class="settings-stepper-controls">
                      <button
                        class="settings-stepper-btn"
                        :disabled="
                          customTaggerThresholdOffsetLoading ||
                          !customTaggerEnabled ||
                          customTaggerThresholdOffsetValue <= -50
                        "
                        @click="
                          () => {
                            customTaggerThresholdOffsetValue = Math.max(
                              -50,
                              customTaggerThresholdOffsetValue - 1,
                            );
                            saveCustomTaggerThresholdOffset();
                          }
                        "
                      >
                        −
                      </button>
                      <span class="settings-stepper-value">{{
                        customTaggerThresholdOffsetValue
                      }}</span>
                      <button
                        class="settings-stepper-btn"
                        :disabled="
                          customTaggerThresholdOffsetLoading ||
                          !customTaggerEnabled ||
                          customTaggerThresholdOffsetValue >= 50
                        "
                        @click="
                          () => {
                            customTaggerThresholdOffsetValue = Math.min(
                              50,
                              customTaggerThresholdOffsetValue + 1,
                            );
                            saveCustomTaggerThresholdOffset();
                          }
                        "
                      >
                        +
                      </button>
                    </div>
                  </div>
                  <button
                    class="settings-threshold-preview-btn"
                    title="Preview tag thresholds"
                    @click="openLabelThresholdsDialog"
                  >
                    <v-icon size="16">mdi-table-eye</v-icon>
                  </button>
                </div>
                <div
                  v-if="
                    wd14TaggerError ||
                    wd14ThresholdError ||
                    customTaggerError ||
                    customTaggerThresholdOffsetError
                  "
                  class="settings-error"
                >
                  {{
                    wd14TaggerError ||
                    wd14ThresholdError ||
                    customTaggerError ||
                    customTaggerThresholdOffsetError
                  }}
                </div>
              </div>

              <!-- Label thresholds preview dialog -->
              <v-dialog
                v-model="labelThresholdsDialogOpen"
                max-width="520"
                @click:outside="labelThresholdsDialogOpen = false"
              >
                <v-card class="label-thresholds-dialog">
                  <v-card-title class="label-thresholds-dialog-title">
                    PixlStash Tagger — Label Thresholds
                  </v-card-title>
                  <v-card-text class="label-thresholds-dialog-body">
                    <div
                      v-if="labelThresholdsLoading"
                      class="label-thresholds-loading"
                    >
                      Loading…
                    </div>
                    <div
                      v-else-if="!labelThresholdsData.length"
                      class="label-thresholds-empty"
                    >
                      No label thresholds found. Ensure the PixlStash tagger
                      model is installed.
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
                  </v-card-text>
                </v-card>
              </v-dialog>

              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Model Memory</div>
                <div class="settings-section-desc">
                  Keep models loaded in RAM/VRAM for faster processing. Turn off
                  to unload models when idle and save memory.
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
                  <span class="settings-slider-value"
                    >{{ maxVramGbValue }} GB</span
                  >
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
            </v-window-item>
            <v-window-item value="smart-score">
              <SmartScoreSection :open="dialogOpen" />
            </v-window-item>
            <v-window-item value="workflows">
              <WorkflowsSection
                :open="dialogOpen"
                @update:comfyui-configured="
                  emit('update:comfyui-configured', $event)
                "
              />
            </v-window-item>
            <v-window-item value="account">
              <AccountSection
                :open="dialogOpen"
                @update:public-url="emit('update:public-url', $event)"
              />
            </v-window-item>
          </v-window>
        </v-card-text>
      </v-card>
    </div>
  </v-dialog>
</template>

<style scoped>
.settings-dialog-card {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: 12px;
  color-scheme: dark;
  display: flex !important;
  flex-direction: column !important;
  flex: 1;
  min-height: 420px;
  overflow: hidden;
  max-height: calc(92dvh - 20px);
}

.settings-dialog-shell {
  position: relative;
  width: 100%;
  min-height: 440px;
  max-height: 92dvh;
  display: flex;
  flex-direction: column;
  overflow: visible;
  padding-top: 20px;
}

.settings-dialog-close {
  position: absolute;
  top: 2px;
  right: -18px;
  background-color: rgb(var(--v-theme-primary));
  border: none;
  color: rgb(var(--v-theme-on-primary));
  cursor: pointer;
  z-index: 2;
}

.settings-dialog-close:hover {
  background-color: rgb(var(--v-theme-accent));
}

.settings-dialog-title {
  font-weight: 700;
  font-size: 1.2rem;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.settings-logout-btn {
  margin-left: auto;
  font-size: 0.78rem;
  opacity: 0.6;
  text-transform: none;
  letter-spacing: 0;
}

.settings-logout-btn:hover {
  opacity: 1;
}

.settings-logout-icon {
  margin-right: 3px;
}

.settings-dialog-version {
  font-size: 0.75rem;
  font-weight: 400;
  opacity: 0.5;
}

.settings-tabs {
  margin-top: 4px;
  overflow: hidden;
  flex-shrink: 0;
}

:deep(.settings-tabs .v-slide-group__content) {
  overflow-x: auto;
  scrollbar-width: none;
}

:deep(.settings-tabs .v-slide-group__content::-webkit-scrollbar) {
  display: none;
}

:deep(.settings-tabs .v-tab) {
  border: 1px solid transparent;
  box-shadow: none;
  font-size: 0.75rem;
  min-width: 0;
  padding: 0 8px;
}

:deep(.settings-tabs .v-tab--selected) {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
  box-shadow: none;
}

:deep(.settings-tabs .v-tab--active) {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
  box-shadow: none;
}

:deep(.settings-tabs .v-tab--selected::before),
:deep(.settings-tabs .v-tab--active::before) {
  opacity: 0;
}

:deep(.settings-tabs .v-tab .v-btn__overlay),
:deep(.settings-tabs .v-tab .v-btn__underlay) {
  opacity: 0;
}

:deep(.settings-tabs .v-tab:focus-visible) {
  outline: none;
  box-shadow: none;
  border-color: rgba(var(--v-theme-on-surface), 0.18);
}

:deep(.settings-tabs .v-tab:focus),
:deep(.settings-tabs .v-tab:active),
:deep(.settings-tabs .v-tab--active),
:deep(.settings-tabs .v-tab--selected) {
  outline: none;
  box-shadow: none;
}

:deep(.settings-tabs .v-tab--selected:focus-visible) {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
}

.settings-tab-body {
  padding-top: 6px;
  overflow: visible;
}

.settings-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  line-height: 1;
  overflow-y: auto !important;
  flex: 1 !important;
  min-height: 0 !important;
}

.settings-dialog-body
  :deep(.v-select .v-field--variant-filled .v-field__input) {
  padding-top: 4px;
  padding-bottom: 4px;
  min-height: 0;
}

.settings-dialog-body :deep(.v-select .v-field--variant-filled) {
  --v-input-control-height: 34px;
}

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

.settings-tagger-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
}

.settings-tagger-checkbox {
  flex: 1;
  min-width: 0;
}

.settings-stepper {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
}

.settings-stepper-label {
  font-size: 0.8em;
  color: rgba(var(--v-theme-on-surface), 0.68);
  white-space: nowrap;
  line-height: 1;
}

.settings-stepper-controls {
  display: flex;
  align-items: center;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.28);
  border-radius: 4px;
  overflow: hidden;
  height: 26px;
}

.settings-stepper-btn {
  width: 26px;
  height: 100%;
  border: none;
  border-radius: 0;
  appearance: none;
  -webkit-appearance: none;
  background: rgba(var(--v-theme-on-surface), 0.06);
  cursor: pointer;
  font-size: 1em;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
  flex-shrink: 0;
  padding: 0;
  color: inherit;
  outline: none;
}

.settings-stepper-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.14);
}

.settings-stepper-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.settings-stepper-value {
  min-width: 30px;
  text-align: center;
  font-size: 0.88em;
  line-height: 1;
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 2px;
}

.settings-stepper--disabled .settings-stepper-label {
  opacity: 0.4;
}

.settings-stepper--disabled .settings-stepper-controls {
  opacity: 0.4;
}

.settings-threshold-preview-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.28);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  outline: none;
  padding: 0;
  color: rgba(var(--v-theme-on-surface), 0.68);
  flex-shrink: 0;
  transition:
    background 0.15s,
    color 0.15s;
}

.settings-threshold-preview-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.1);
  color: rgba(var(--v-theme-on-surface), 0.92);
}

/* Label thresholds dialog */
.label-thresholds-dialog {
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
}

.label-thresholds-dialog-title {
  font-size: 1rem;
  font-weight: 600;
  padding-bottom: 4px;
}

.label-thresholds-dialog-body {
  padding-top: 0;
  max-height: 420px;
  overflow-y: auto;
}

.label-thresholds-loading,
.label-thresholds-empty {
  font-size: 0.875rem;
  opacity: 0.6;
  padding: 12px 0;
  text-align: center;
}

.label-thresholds-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.label-thresholds-table th,
.label-thresholds-table td {
  padding: 4px 8px;
  vertical-align: middle;
  text-align: left;
}

.label-thresholds-table th {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.5;
  border-bottom: 1px solid rgba(var(--v-theme-on-background), 0.1);
  padding-bottom: 6px;
}

.label-thresholds-table tr:hover td {
  background: rgba(var(--v-theme-on-background), 0.04);
}

.lth-col-name {
  width: 60%;
}

.lth-col-base,
.lth-col-eff {
  width: 20%;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.lth-boosted {
  color: rgb(var(--v-theme-primary));
}

.lth-penalised {
  color: rgb(var(--v-theme-error));
}

.settings-comfyui-display {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

.settings-comfyui-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.93em;
}

.settings-comfyui-label {
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.7);
  min-width: 36px;
}

.settings-comfyui-value {
  color: rgb(var(--v-theme-on-surface));
  font-family: monospace;
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

.settings-account-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0 2px;
}

.settings-account-label {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.settings-account-value {
  font-weight: 600;
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

.settings-number-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  align-items: start;
}

.settings-number-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 4px;
}

.settings-number-spinner {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-self: center;
  transform: translateY(-10px);
}

.settings-number-btn {
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 6px;
  width: 24px;
  height: 22px;
  min-width: 24px;
}

.settings-number-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.16);
}

.settings-number-input {
  width: 100%;
}

:deep(.settings-number-input input[type="number"]) {
  -moz-appearance: textfield;
  appearance: textfield;
}

:deep(.settings-number-input input[type="number"]::-webkit-inner-spin-button),
:deep(.settings-number-input input[type="number"]::-webkit-outer-spin-button) {
  -webkit-appearance: none;
  margin: 0;
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.9em;
}

.settings-success {
  color: rgb(var(--v-theme-accent));
  font-size: 0.9em;
}

.settings-public-url-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.settings-watermark-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.settings-watermark-preview {
  max-height: 36px;
  max-width: 120px;
  object-fit: contain;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  padding: 2px;
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

.settings-tokens {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.token-field {
  font-size: 0.85em;
}

.token-field :deep(.v-label) {
  font-size: 0.85em;
}

.token-field :deep(.v-field__input) {
  font-size: 0.85em;
}

.settings-token-loading {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-token-list {
  max-height: 200px;
  overflow-y: auto;
  padding-right: 4px;
}

.settings-token-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82em;
}

.settings-token-table thead th {
  text-align: left;
  padding: 2px 8px 4px;
  font-size: 0.78em;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.5);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  white-space: nowrap;
}

.settings-token-row td {
  padding: 3px 8px;
  vertical-align: middle;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.settings-token-row:last-child td {
  border-bottom: none;
}

.settings-token-desc {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 140px;
}

.settings-token-sub {
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
}

.settings-token-expired {
  color: rgb(var(--v-theme-error));
  font-weight: 600;
}

.settings-token-actions {
  text-align: right;
  white-space: nowrap;
  padding-left: 0;
}

.settings-token-delete {
  color: rgba(var(--v-theme-error), 0.9);
}

.settings-token-empty {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.6);
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

.settings-tag-importance {
  flex: 0 1 90px;
  min-width: 0;
  max-width: 90px;
  overflow: hidden;
}

:deep(.settings-tag-importance .v-field) {
  min-height: 28px;
  height: 28px;
  padding-top: 0;
  padding-bottom: 0;
  font-size: 0.9em;
  background: transparent;
  box-shadow: none;
  border: none;
}

:deep(.settings-tag-importance .v-field__input) {
  min-height: 28px;
  height: 28px;
  padding-top: 0;
  padding-bottom: 0;
  padding-right: 4px;
  font-size: 0.85rem;
  min-width: 0;
  overflow: hidden;
}

:deep(.settings-tag-importance .v-field__append-inner) {
  align-self: center;
  margin-left: 2px;
  padding-top: 0;
  padding-bottom: 0;
  height: 28px;
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

:deep(.settings-tag-importance .v-field__overlay),
:deep(.settings-tag-importance .v-field__underlay),
:deep(.settings-tag-importance .v-field__outline) {
  opacity: 0;
}

:deep(.settings-tag-importance .v-select__selection-text) {
  font-size: 0.85rem;
  line-height: 1.1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  display: block;
}

:deep(.settings-tag-importance .v-field__input input) {
  font-size: 0.85rem;
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

.settings-token-dialog {
  padding-bottom: 8px;
}

.settings-token-warning {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-bottom: 6px;
}

.settings-token-value-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.settings-token-value {
  flex: 1;
  word-break: break-all;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  background: rgba(var(--v-theme-surface), 0.2);
  border-radius: 8px;
  padding: 2px 4px;
}

.settings-token-copy-btn {
  flex-shrink: 0;
  opacity: 0.7;
}

.settings-token-copy-btn:hover {
  opacity: 1;
}

.settings-section-divider {
  margin: 4px 0 8px;
}

.settings-privacy-note {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: 4px;
}

.settings-dialog-actions {
  padding-top: 0;
}
</style>
