<script setup>
import { computed, ref, watch } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  open: { type: Boolean, default: false },
  sidebarThumbnailSize: { type: Number, default: 48 },
  dateFormat: { type: String, default: "locale" },
  themeMode: { type: String, default: "light" },
});

const emit = defineEmits([
  "update:open",
  "update:sidebar-thumbnail-size",
  "update:date-format",
  "update:theme-mode",
  "update:hidden-tags",
  "update:apply-tag-filter",
]);

const dialogOpen = computed({
  get: () => props.open,
  set: (value) => emit("update:open", value),
});

const sidebarThumbnailSizeModel = computed({
  get: () => props.sidebarThumbnailSize ?? 48,
  set: (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(64, Math.max(32, parsed));
    const snapped = Math.round(clamped / 8) * 8;
    if (snapped === (props.sidebarThumbnailSize ?? 48)) return;
    emit("update:sidebar-thumbnail-size", snapped);
  },
});

const dateFormatModel = computed({
  get: () => props.dateFormat ?? "locale",
  set: (value) => {
    const nextValue = value ?? "locale";
    if (nextValue === (props.dateFormat ?? "locale")) return;
    emit("update:date-format", nextValue);
  },
});

const themeModeModel = computed({
  get: () => props.themeMode ?? "light",
  set: (value) => {
    const nextValue = value ?? "light";
    if (nextValue === (props.themeMode ?? "light")) return;
    emit("update:theme-mode", nextValue);
  },
});

const dateFormatOptions = [
  { title: "Locale default", value: "locale" },
  { title: "ISO (YYYY-MM-DD)", value: "iso" },
  { title: "European (DD/MM/YYYY)", value: "eu" },
  { title: "American (MM/DD/YYYY)", value: "us" },
  { title: "China (YYYY/MM/DD)", value: "ymd-slash" },
  { title: "Korea (YYYY.MM.DD)", value: "ymd-dot" },
  { title: "Japan (YYYY年MM月DD日)", value: "ymd-jp" },
];

const themeModeOptions = [
  { title: "Light", value: "light" },
  { title: "Dark", value: "dark" },
];

const settingsTab = ref("preferences");
const settingsUsername = ref("");
const settingsHasPassword = ref(false);
const settingsLoading = ref(false);
const settingsError = ref("");
const settingsSuccess = ref("");
const currentPassword = ref("");
const newPassword = ref("");
const showNewPassword = ref(false);
const tokensLoading = ref(false);
const tokensError = ref("");
const tokens = ref([]);
const tokenDescription = ref("");
const newlyCreatedToken = ref("");
const tokenDialogOpen = ref(false);
const tokenDeleteDialogOpen = ref(false);
const tokenToDelete = ref(null);
const smartScorePenalisedTags = ref([]);
const smartScoreTagInput = ref("");
const smartScoreTagsLoading = ref(false);
const smartScoreTagsError = ref("");
const smartScoreTagsSuccess = ref("");
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
const smartScoreScrapheapThreshold = ref(1.25);
const smartScoreScrapheapLookback = ref(30);
const smartScoreScrapheapLoading = ref(false);
const smartScoreScrapheapError = ref("");
const smartScoreScrapheapSuccess = ref("");
const smartScoreScrapheapHydrating = ref(false);
let smartScoreScrapheapSaveTimer = null;

const smartScoreImportanceOptions = [
  { value: 1, label: "Mild" },
  { value: 2, label: "Low" },
  { value: 3, label: "Moderate" },
  { value: 4, label: "High" },
  { value: 5, label: "Severe" },
];

function stepNumber(value, delta, options = {}) {
  const { min = null, max = null, precision = null } = options;
  const current = Number(value);
  const base = Number.isFinite(current) ? current : 0;
  let next = base + delta;
  if (min != null) next = Math.max(min, next);
  if (max != null) next = Math.min(max, next);
  if (precision != null && Number.isFinite(precision)) {
    next = Number(next.toFixed(precision));
  }
  return next;
}

function incrementScrapheapThreshold(delta) {
  smartScoreScrapheapThreshold.value = stepNumber(
    smartScoreScrapheapThreshold.value,
    delta,
    { min: 0.1, precision: 2 },
  );
}

function incrementScrapheapLookback(delta) {
  smartScoreScrapheapLookback.value = stepNumber(
    smartScoreScrapheapLookback.value,
    delta,
    { min: 1, precision: 0 },
  );
}

async function fetchSettingsAuth() {
  settingsLoading.value = true;
  settingsError.value = "";
  try {
    const res = await apiClient.get("/users/me/auth");
    settingsUsername.value = res.data?.username || "";
    settingsHasPassword.value = Boolean(res.data?.has_password);
  } catch (e) {
    settingsError.value = "Failed to load account settings.";
  } finally {
    settingsLoading.value = false;
  }
}

function resetSettingsForm() {
  settingsError.value = "";
  settingsSuccess.value = "";
  currentPassword.value = "";
  newPassword.value = "";
  showNewPassword.value = false;
  tokensError.value = "";
  tokenDescription.value = "";
  newlyCreatedToken.value = "";
  tokenDialogOpen.value = false;
  tokenDeleteDialogOpen.value = false;
  tokenToDelete.value = null;
  smartScoreTagInput.value = "";
  smartScoreTagsError.value = "";
  smartScoreTagsSuccess.value = "";
  hiddenTagInput.value = "";
  hiddenTagsError.value = "";
  hiddenTagsSuccess.value = "";
  keepModelsInMemoryError.value = "";
  smartScoreScrapheapError.value = "";
  smartScoreScrapheapSuccess.value = "";
}

function clampImportance(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 3;
  return Math.min(5, Math.max(1, Math.round(num)));
}

function SmartScoreTags(tags) {
  const d = new Map();
  if (Array.isArray(tags)) {
    for (const item of tags) {
      if (item == null) continue;
      if (typeof item === "object") {
        const clean = String(item.tag || "")
          .trim()
          .toLowerCase();
        if (!clean) continue;
        d.set(clean, clampImportance(item.weight));
      } else {
        const clean = String(item).trim().toLowerCase();
        if (!clean) continue;
        d.set(clean, 3);
      }
    }
  } else if (tags && typeof tags === "object") {
    for (const [tag, weight] of Object.entries(tags)) {
      if (tag == null) continue;
      const clean = String(tag).trim().toLowerCase();
      if (!clean) continue;
      const nextWeight = clampImportance(weight);
      const existing = d.get(clean);
      if (existing == null || nextWeight > existing) {
        d.set(clean, nextWeight);
      }
    }
  }
  return Array.from(d.entries())
    .map(([tag, weight]) => ({ tag, weight }))
    .sort((a, b) => a.tag.localeCompare(b.tag));
}

function serializeSmartScoreTags(entries) {
  const d = SmartScoreTags(entries);
  const payload = {};
  for (const entry of d) {
    payload[entry.tag] = clampImportance(entry.weight);
  }
  return { d, payload };
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

async function fetchSmartScoreSettings() {
  smartScoreTagsLoading.value = true;
  smartScoreTagsError.value = "";
  hiddenTagsLoading.value = true;
  hiddenTagsError.value = "";
  try {
    const res = await apiClient.get("/users/me/config");
    smartScorePenalisedTags.value = SmartScoreTags(
      res.data?.smart_score_penalised_tags,
    );
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
    smartScoreScrapheapHydrating.value = true;
    const threshold = Number(res.data?.auto_scrapheap_smart_score_threshold);
    if (Number.isFinite(threshold)) {
      smartScoreScrapheapThreshold.value = threshold;
    }
    const lookback = Number(res.data?.auto_scrapheap_lookback_minutes);
    if (Number.isFinite(lookback)) {
      smartScoreScrapheapLookback.value = Math.max(1, Math.round(lookback));
    }
  } catch (e) {
    smartScoreTagsError.value = "Failed to load smart score settings.";
    hiddenTagsError.value = "Failed to load hidden tag settings.";
  } finally {
    smartScoreTagsLoading.value = false;
    hiddenTagsLoading.value = false;
    smartScoreScrapheapHydrating.value = false;
  }
}

async function saveSmartScoreScrapheapSettings() {
  smartScoreScrapheapLoading.value = true;
  smartScoreScrapheapError.value = "";
  smartScoreScrapheapSuccess.value = "";
  try {
    const threshold = Number(smartScoreScrapheapThreshold.value);
    const lookback = Number(smartScoreScrapheapLookback.value);
    if (!Number.isFinite(threshold) || threshold <= 0) {
      throw new Error("Threshold must be a positive number.");
    }
    if (!Number.isFinite(lookback) || lookback < 1) {
      throw new Error("Lookback must be at least 1 minute.");
    }
    await apiClient.patch("/users/me/config", {
      auto_scrapheap_smart_score_threshold: threshold,
      auto_scrapheap_lookback_minutes: Math.round(lookback),
    });
    smartScoreScrapheapSuccess.value = "Saved.";
  } catch (e) {
    smartScoreScrapheapError.value =
      e?.response?.data?.detail || e?.message || "Failed to update settings.";
  } finally {
    smartScoreScrapheapLoading.value = false;
    if (smartScoreScrapheapSuccess.value) {
      setTimeout(() => {
        smartScoreScrapheapSuccess.value = "";
      }, 2000);
    }
  }
}

function scheduleSmartScoreScrapheapSave() {
  if (smartScoreScrapheapHydrating.value) return;
  if (smartScoreScrapheapSaveTimer) {
    clearTimeout(smartScoreScrapheapSaveTimer);
  }
  smartScoreScrapheapSaveTimer = setTimeout(() => {
    smartScoreScrapheapSaveTimer = null;
    saveSmartScoreScrapheapSettings();
  }, 600);
}

async function saveSmartScoreTags(nextTags) {
  smartScoreTagsLoading.value = true;
  smartScoreTagsError.value = "";
  smartScoreTagsSuccess.value = "";
  try {
    const { d, payload } = serializeSmartScoreTags(nextTags);
    await apiClient.patch("/users/me/config", {
      smart_score_penalised_tags: payload,
    });
    smartScorePenalisedTags.value = d;
    smartScoreTagsSuccess.value = "Saved.";
  } catch (e) {
    smartScoreTagsError.value =
      e?.response?.data?.detail || "Failed to update smart score tags.";
  } finally {
    smartScoreTagsLoading.value = false;
    if (smartScoreTagsSuccess.value) {
      setTimeout(() => {
        smartScoreTagsSuccess.value = "";
      }, 2000);
    }
  }
}

async function addSmartScoreTag() {
  const trimmed = smartScoreTagInput.value.trim().toLowerCase();
  if (!trimmed) return;
  const next = SmartScoreTags([
    ...smartScorePenalisedTags.value,
    { tag: trimmed, weight: 3 },
  ]);
  smartScoreTagInput.value = "";
  await saveSmartScoreTags(next);
}

async function removeSmartScoreTag(tag) {
  const next = SmartScoreTags(
    smartScorePenalisedTags.value.filter((t) => t.tag !== tag),
  );
  await saveSmartScoreTags(next);
}

async function updateSmartScoreTagWeight(tag, weight) {
  const next = SmartScoreTags(
    smartScorePenalisedTags.value.map((entry) =>
      entry.tag === tag ? { ...entry, weight: clampImportance(weight) } : entry,
    ),
  );
  await saveSmartScoreTags(next);
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

function formatTokenTimestamp(value) {
  if (!value) return "Never used";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Never used";
  return date.toLocaleString();
}

async function fetchUserTokens() {
  tokensLoading.value = true;
  tokensError.value = "";
  try {
    const res = await apiClient.get("/users/me/token");
    tokens.value = Array.isArray(res.data) ? res.data : [];
  } catch (e) {
    tokensError.value = "Failed to load tokens.";
  } finally {
    tokensLoading.value = false;
  }
}

async function createUserToken() {
  tokensError.value = "";
  const description = tokenDescription.value.trim() || null;
  tokensLoading.value = true;
  try {
    const res = await apiClient.post("/users/me/token", { description });
    newlyCreatedToken.value = res.data?.token || "";
    tokenDialogOpen.value = Boolean(newlyCreatedToken.value);
    tokenDescription.value = "";
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = e?.response?.data?.detail || "Failed to create token.";
  } finally {
    tokensLoading.value = false;
  }
}

function confirmDeleteToken(token) {
  tokenToDelete.value = token;
  tokenDeleteDialogOpen.value = true;
}

async function deleteUserToken() {
  if (!tokenToDelete.value) {
    tokenDeleteDialogOpen.value = false;
    return;
  }
  tokensLoading.value = true;
  tokensError.value = "";
  try {
    await apiClient.delete(`/users/me/token/${tokenToDelete.value.id}`);
    tokenDeleteDialogOpen.value = false;
    tokenToDelete.value = null;
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = e?.response?.data?.detail || "Failed to delete token.";
  } finally {
    tokensLoading.value = false;
  }
}

async function submitPasswordChange() {
  settingsError.value = "";
  settingsSuccess.value = "";
  if (!newPassword.value || newPassword.value.trim().length < 8) {
    settingsError.value = "New password must be at least 8 characters long.";
    return;
  }
  if (settingsHasPassword.value && !currentPassword.value) {
    settingsError.value = "Current password is required.";
    return;
  }
  settingsLoading.value = true;
  try {
    const newPasswordValue = newPassword.value.trim();
    await apiClient.post("/users/me/auth", {
      current_password: currentPassword.value || null,
      new_password: newPasswordValue,
    });
    settingsSuccess.value = "Password updated.";
    currentPassword.value = "";
    newPassword.value = "";
    settingsHasPassword.value = true;
    if (
      typeof window !== "undefined" &&
      "credentials" in navigator &&
      "PasswordCredential" in window &&
      settingsUsername.value &&
      newPasswordValue
    ) {
      try {
        const credential = new PasswordCredential({
          id: settingsUsername.value,
          name: settingsUsername.value,
          password: newPasswordValue,
        });
        await navigator.credentials.store(credential);
      } catch (credentialError) {
        console.debug("Credential store failed:", credentialError);
      }
    }
  } catch (e) {
    settingsError.value =
      e?.response?.data?.detail || "Failed to update password.";
  } finally {
    settingsLoading.value = false;
  }
}

watch(
  () => dialogOpen.value,
  (isOpen) => {
    if (isOpen) {
      resetSettingsForm();
      settingsTab.value = "preferences";
      fetchSettingsAuth();
      fetchUserTokens();
      fetchSmartScoreSettings();
    }
  },
);

watch([smartScoreScrapheapThreshold, smartScoreScrapheapLookback], () => {
  scheduleSmartScoreScrapheapSave();
});
</script>

<template>
  <v-dialog
    v-model="dialogOpen"
    width="800"
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
        <v-card-title class="settings-dialog-title">Settings</v-card-title>
        <v-tabs
          v-model="settingsTab"
          density="comfortable"
          class="settings-tabs"
        >
          <v-tab value="preferences">Preferences</v-tab>
          <v-tab value="smart-score">Smart Score</v-tab>
          <v-tab value="account">Account Settings</v-tab>
        </v-tabs>
        <v-card-text class="settings-dialog-body">
          <v-window v-model="settingsTab" class="settings-tab-body">
            <v-window-item value="preferences">
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Sidebar Thumbnails</div>
                <div class="settings-section-desc">
                  Adjust the sidebar thumbnail size.
                </div>
                <div class="settings-slider-row">
                  <span class="settings-slider-value">
                    {{ sidebarThumbnailSizeModel }}px
                  </span>
                  <v-slider
                    v-model="sidebarThumbnailSizeModel"
                    :min="32"
                    :max="64"
                    :step="8"
                    hide-details
                    track-color="#666"
                    thumb-color="primary"
                    class="settings-slider"
                  />
                </div>
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Theme</div>
                <div class="settings-section-desc">
                  Choose a light or dark theme.
                </div>
                <v-select
                  v-model="themeModeModel"
                  :items="themeModeOptions"
                  item-title="title"
                  item-value="value"
                  density="comfortable"
                  variant="filled"
                  class="settings-add-tag-input"
                  hide-details
                />
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Date Format</div>
                <div class="settings-section-desc">
                  Choose how dates are shown in the grid and overlays.
                </div>
                <v-select
                  v-model="dateFormatModel"
                  :items="dateFormatOptions"
                  item-title="title"
                  item-value="value"
                  density="comfortable"
                  variant="filled"
                  class="settings-add-tag-input"
                  hide-details
                />
              </div>
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
                  density="comfortable"
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
                <div class="settings-section-title">Tag Filter</div>
                <div class="settings-section-desc">
                  Tags listed here are filtered from the GUI entirely.
                </div>
                <v-checkbox
                  v-model="applyTagFilter"
                  class="settings-tag-filter-toggle"
                  density="comfortable"
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
                      density="comfortable"
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
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Penalised Tags</div>
                <div class="settings-section-desc">
                  Tags listed here reduce Smart Score when present on a picture.
                  Adjust the importance to control how much they hurt the score.
                </div>
                <div class="settings-tag-list">
                  <div
                    v-for="entry in smartScorePenalisedTags"
                    :key="entry.tag"
                    class="settings-tag-chip settings-tag-chip--row"
                  >
                    <span class="settings-tag-label">{{ entry.tag }}</span>
                    <v-select
                      class="settings-tag-importance"
                      :items="smartScoreImportanceOptions"
                      item-title="label"
                      item-value="value"
                      density="compact"
                      variant="plain"
                      hide-details
                      :disabled="smartScoreTagsLoading"
                      :model-value="entry.weight"
                      @update:model-value="
                        (value) => updateSmartScoreTagWeight(entry.tag, value)
                      "
                    />
                    <v-btn
                      icon
                      variant="text"
                      class="settings-tag-delete"
                      :disabled="smartScoreTagsLoading"
                      @click="removeSmartScoreTag(entry.tag)"
                    >
                      <v-icon size="16">mdi-close</v-icon>
                    </v-btn>
                  </div>
                  <div
                    v-if="
                      !smartScoreTagsLoading && !smartScorePenalisedTags.length
                    "
                    class="settings-token-empty"
                  >
                    No penalised tags yet.
                  </div>
                </div>
                <div class="settings-form">
                  <div class="settings-add-tag-row">
                    <v-text-field
                      v-model="smartScoreTagInput"
                      label="Add penalised tag"
                      density="comfortable"
                      variant="filled"
                      class="settings-add-tag-input"
                      :disabled="smartScoreTagsLoading"
                      @keydown.enter.prevent="addSmartScoreTag"
                    />
                    <v-btn
                      variant="outlined"
                      color="primary"
                      class="settings-action-btn"
                      :loading="smartScoreTagsLoading"
                      :disabled="smartScoreTagsLoading"
                      @click="addSmartScoreTag"
                    >
                      Add Tag
                    </v-btn>
                  </div>
                  <div v-if="smartScoreTagsError" class="settings-error">
                    {{ smartScoreTagsError }}
                  </div>
                  <div
                    v-else-if="smartScoreTagsSuccess"
                    class="settings-success"
                  >
                    {{ smartScoreTagsSuccess }}
                  </div>
                  <div v-else class="settings-success">
                    {{ "&nbsp;" }}
                  </div>
                </div>
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Auto Scrapheap</div>
                <div class="settings-section-desc">
                  Newly tagged pictures can be auto-moved to the scrapheap when
                  their smart score is below the threshold.
                </div>
                <div class="settings-form">
                  <div class="settings-number-grid">
                    <div class="settings-number-row">
                      <v-text-field
                        v-model.number="smartScoreScrapheapThreshold"
                        label="Smart score threshold"
                        density="comfortable"
                        variant="filled"
                        type="number"
                        step="0.05"
                        min="0.1"
                        class="settings-number-input"
                        :disabled="smartScoreScrapheapLoading"
                      />
                      <div class="settings-number-spinner">
                        <v-btn
                          icon
                          variant="text"
                          class="settings-number-btn"
                          :disabled="smartScoreScrapheapLoading"
                          @click="incrementScrapheapThreshold(0.05)"
                        >
                          <v-icon size="14">mdi-chevron-up</v-icon>
                        </v-btn>
                        <v-btn
                          icon
                          variant="text"
                          class="settings-number-btn"
                          :disabled="smartScoreScrapheapLoading"
                          @click="incrementScrapheapThreshold(-0.05)"
                        >
                          <v-icon size="14">mdi-chevron-down</v-icon>
                        </v-btn>
                      </div>
                    </div>
                    <div class="settings-number-row">
                      <v-text-field
                        v-model.number="smartScoreScrapheapLookback"
                        label="Lookback window (minutes)"
                        density="comfortable"
                        variant="filled"
                        type="number"
                        step="1"
                        min="1"
                        class="settings-number-input"
                        :disabled="smartScoreScrapheapLoading"
                      />
                      <div class="settings-number-spinner">
                        <v-btn
                          icon
                          variant="text"
                          class="settings-number-btn"
                          :disabled="smartScoreScrapheapLoading"
                          @click="incrementScrapheapLookback(1)"
                        >
                          <v-icon size="14">mdi-chevron-up</v-icon>
                        </v-btn>
                        <v-btn
                          icon
                          variant="text"
                          class="settings-number-btn"
                          :disabled="smartScoreScrapheapLoading"
                          @click="incrementScrapheapLookback(-1)"
                        >
                          <v-icon size="14">mdi-chevron-down</v-icon>
                        </v-btn>
                      </div>
                    </div>
                  </div>
                  <div v-if="smartScoreScrapheapError" class="settings-error">
                    {{ smartScoreScrapheapError }}
                  </div>
                  <div
                    v-else-if="smartScoreScrapheapSuccess"
                    class="settings-success"
                  >
                    {{ smartScoreScrapheapSuccess }}
                  </div>
                  <div v-else class="settings-success">{{ "&nbsp;" }}</div>
                </div>
              </div>
            </v-window-item>
            <v-window-item value="account">
              <div class="settings-section">
                <div class="settings-section-title">Account</div>
                <div class="settings-section-desc">
                  Change your password or manage sign-in options.
                </div>
                <div class="settings-account-meta">
                  <span class="settings-account-label">Username</span>
                  <span class="settings-account-value">
                    {{ settingsUsername || "Not set" }}
                  </span>
                </div>
                <div class="settings-form">
                  <input
                    v-if="settingsUsername"
                    type="text"
                    name="username"
                    :value="settingsUsername"
                    autocomplete="username"
                    style="
                      position: absolute;
                      opacity: 0;
                      height: 0;
                      width: 0;
                      pointer-events: none;
                    "
                    tabindex="-1"
                  />
                  <v-text-field
                    v-if="settingsHasPassword"
                    v-model="currentPassword"
                    label="Current password"
                    type="password"
                    density="comfortable"
                    variant="filled"
                    autocomplete="current-password"
                    name="current-password"
                  />
                  <v-text-field
                    v-model="newPassword"
                    label="New password"
                    :type="showNewPassword ? 'text' : 'password'"
                    density="comfortable"
                    variant="filled"
                    autocomplete="new-password"
                    name="new-password"
                    :append-inner-icon="
                      showNewPassword ? 'mdi-eye-off' : 'mdi-eye'
                    "
                    @click:append-inner="showNewPassword = !showNewPassword"
                  />
                  <div v-if="settingsError" class="settings-error">
                    {{ settingsError }}
                  </div>
                  <div v-if="settingsSuccess" class="settings-success">
                    {{ settingsSuccess }}
                  </div>
                  <v-btn
                    variant="outlined"
                    color="primary"
                    class="settings-action-btn"
                    :loading="settingsLoading"
                    :disabled="settingsLoading"
                    @click="submitPasswordChange"
                  >
                    Update Password
                  </v-btn>
                </div>
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">API Tokens</div>
                <div class="settings-section-desc">
                  Manage tokens for authenticated API access.
                </div>
                <div class="settings-tokens">
                  <v-text-field
                    v-model="tokenDescription"
                    label="Token description"
                    density="comfortable"
                    variant="filled"
                    class="settings-add-tag-input"
                    :disabled="tokensLoading"
                    @keydown.enter.prevent="createUserToken"
                  />
                  <v-btn
                    variant="outlined"
                    color="primary"
                    class="settings-action-btn"
                    :loading="tokensLoading"
                    :disabled="tokensLoading"
                    @click="createUserToken"
                  >
                    Create Token
                  </v-btn>
                  <div v-if="tokensError" class="settings-error">
                    {{ tokensError }}
                  </div>
                  <div v-else class="settings-success">{{ "&nbsp;" }}</div>
                  <div class="settings-token-list">
                    <div
                      v-for="token in tokens"
                      :key="token.id"
                      class="settings-token-row"
                    >
                      <div class="settings-token-meta">
                        <span class="settings-token-desc">
                          {{ token.description || "Token" }}
                        </span>
                        <span class="settings-token-sub">
                          <span>
                            Created:
                            {{ formatTokenTimestamp(token.created_at) }}
                          </span>
                          <span>
                            Last used:
                            {{ formatTokenTimestamp(token.last_used) }}
                          </span>
                        </span>
                      </div>
                      <v-btn
                        icon
                        variant="text"
                        class="settings-token-delete"
                        :disabled="tokensLoading"
                        @click="confirmDeleteToken(token)"
                      >
                        <v-icon size="18">mdi-delete</v-icon>
                      </v-btn>
                    </div>
                    <div
                      v-if="!tokensLoading && !tokens.length"
                      class="settings-token-empty"
                    >
                      No API tokens.
                    </div>
                  </div>
                </div>
              </div>
            </v-window-item>
          </v-window>
        </v-card-text>
      </v-card>
    </div>
  </v-dialog>

  <v-dialog v-model="tokenDialogOpen" width="520">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title">New API Token</v-card-title>
      <v-card-text class="settings-dialog-body">
        <div class="settings-token-warning">
          Copy this token now. You won’t be able to see it again.
        </div>
        <div class="settings-token-value">{{ newlyCreatedToken }}</div>
      </v-card-text>
      <v-card-actions class="settings-dialog-actions">
        <v-spacer />
        <v-btn
          variant="outlined"
          color="primary"
          @click="tokenDialogOpen = false"
        >
          Close
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="tokenDeleteDialogOpen" width="420">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title">Delete token?</v-card-title>
      <v-card-text class="settings-dialog-body">
        This will permanently revoke the selected token.
      </v-card-text>
      <v-card-actions class="settings-dialog-actions">
        <v-spacer />
        <v-btn variant="text" @click="tokenDeleteDialogOpen = false">
          Cancel
        </v-btn>
        <v-btn
          color="error"
          variant="outlined"
          :loading="tokensLoading"
          @click="deleteUserToken"
        >
          Delete
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.settings-dialog-card {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: 12px;
  color-scheme: dark;
}

.settings-dialog-shell {
  position: relative;
  width: 100%;
}

.settings-dialog-close {
  position: absolute;
  top: -16px;
  right: -16px;
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
}

.settings-tabs {
  margin-top: 4px;
}

:deep(.settings-tabs .v-tab) {
  border: 1px solid transparent;
  box-shadow: none;
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
}

.settings-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  line-height: 1;
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
  gap: 10px;
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
  gap: 10px;
}

.settings-token-loading {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-token-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 220px;
  overflow-y: auto;
  padding-right: 4px;
}

.settings-token-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(var(--v-theme-surface), 0.2);
}

.settings-token-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-token-desc {
  font-weight: 600;
}

.settings-token-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 0.8em;
  color: rgba(var(--v-theme-on-surface), 0.7);
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
  flex: 0 0 120px;
  min-width: 120px;
  max-width: 120px;
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
}

:deep(.settings-tag-importance .v-field__append-inner) {
  align-self: center;
  margin-left: 2px;
  padding-top: 0;
  padding-bottom: 0;
  height: 28px;
  display: flex;
  align-items: center;
}

:deep(.settings-tag-importance .v-field__overlay),
:deep(.settings-tag-importance .v-field__underlay),
:deep(.settings-tag-importance .v-field__outline) {
  opacity: 0;
}

:deep(.settings-tag-importance .v-select__selection-text) {
  font-size: 0.85rem;
  line-height: 1.1;
}

:deep(.settings-tag-importance .v-field__input input) {
  font-size: 0.85rem;
}

.settings-tag-label {
  font-size: 1em;
  flex: 1;
  min-width: 0;
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

.settings-token-value {
  word-break: break-all;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  background: rgba(var(--v-theme-surface), 0.2);
  border-radius: 8px;
  padding: 2px 4px;
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
