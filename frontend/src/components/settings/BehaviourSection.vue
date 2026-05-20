<script setup>
import { computed, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";

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

function resetForm() {
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
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      resetForm();
      fetchBehaviourSettings();
    }
  },
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
    <div class="settings-section-title">Taggers</div>
    <div class="settings-section-desc">
      Enable or disable each tagger and set its confidence threshold. Changes
      apply immediately.
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
                wd14ThresholdValue = Math.max(1, wd14ThresholdValue - 1);
                saveWd14Threshold();
              }
            "
          >
            −
          </button>
          <span class="settings-stepper-value">{{ wd14ThresholdValue }}</span>
          <button
            class="settings-stepper-btn"
            :disabled="
              wd14ThresholdLoading ||
              !wd14TaggerEnabled ||
              wd14ThresholdValue >= 100
            "
            @click="
              () => {
                wd14ThresholdValue = Math.min(100, wd14ThresholdValue + 1);
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
            customTaggerThresholdOffsetLoading || !customTaggerEnabled,
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
        <div v-if="labelThresholdsLoading" class="label-thresholds-loading">
          Loading…
        </div>
        <div
          v-else-if="!labelThresholdsData.length"
          class="label-thresholds-empty"
        >
          No label thresholds found. Ensure the PixlStash tagger model is
          installed.
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
