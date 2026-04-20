<script setup>
import { computed, ref, watch } from "vue";
import { apiClient, logout } from "../utils/apiClient";

const appVersion = __APP_VERSION__;

const props = defineProps({
  open: { type: Boolean, default: false },
  sidebarThumbnailSize: { type: Number, default: 48 },
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
    const clamped = Math.min(64, Math.max(24, parsed));
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
  { title: "ISO (YYYY-MM-DD, 24h)", value: "iso" },
  { title: "European (DD/MM/YYYY, 24h)", value: "eu" },
  { title: "British (DD/MM/YYYY, AM/PM)", value: "british" },
  { title: "American (MM/DD/YYYY, AM/PM)", value: "us" },
  { title: "China (YYYY/MM/DD, 24h)", value: "ymd-slash" },
  { title: "Korea (YYYY.MM.DD, 24h)", value: "ymd-dot" },
  { title: "Japan (YYYY年MM月DD日, 24h)", value: "ymd-jp" },
];

const themeModeOptions = [
  { title: "Light", value: "light" },
  { title: "Dark", value: "dark" },
];

const checkForUpdatesModel = computed({
  get: () => props.checkForUpdates ?? false,
  set: (value) => emit("update:check-for-updates", value),
});

const showKeyboardHintModel = computed({
  get: () => props.showKeyboardHint ?? true,
  set: (value) => {
    if (value === (props.showKeyboardHint ?? true)) return;
    emit("update:show-keyboard-hint", value);
  },
});

const settingsTab = ref("appearance");
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
const tokenCopied = ref(false);
const tokenDialogOpen = ref(false);
const tokenDeleteDialogOpen = ref(false);
const tokenToDelete = ref(null);
// Share token creation scope fields
const tokenScope = ref("ALL");
const tokenResourceType = ref(null);
const tokenResourceId = ref(null);
const tokenExpiresAt = ref(null);
const shareResourceOptions = ref([]);
const shareResourceLoading = ref(false);
const shareLinkCopied = ref(false);
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
const maxVramGbValue = ref(VRAM_BUDGET_MIN_GB);
const maxVramGbMax = ref(VRAM_BUDGET_MIN_GB);
const maxVramGbLoading = ref(false);
const maxVramGbError = ref("");
const maxVramGbSuccess = ref("");
const maxVramGbSavedValue = ref(null);
const maxVramGbHydrating = ref(false);
const maxVramGbAutoSaveReady = ref(false);
let maxVramGbSaveTimer = null;
const comfyuiHost = ref("");
const comfyuiPort = ref("");
const comfyuiEditHost = ref("");
const comfyuiEditPort = ref("");
const comfyuiConfigDialogOpen = ref(false);
const comfyuiUrlLoading = ref(false);
const comfyuiUrlError = ref("");
const comfyuiUrlSuccess = ref("");
const workflowImportInputRef = ref(null);
const workflowImportDialogOpen = ref(false);
const workflowImportError = ref("");
const workflowImportName = ref("");
const workflowImportPayload = ref(null);
const workflowImportInputs = ref([]);
const workflowImportOutputs = ref([]);
const workflowImportImageTarget = ref("");
const workflowImportCaptionTarget = ref("");
const workflowImportOutputTargets = ref([]);
const workflowImportSaving = ref(false);
const workflowList = ref([]);
const workflowListLoading = ref(false);
const workflowListError = ref("");

const referenceFolders = ref([]);
const refFoldersLoading = ref(false);
const refFoldersError = ref("");
const inDocker = ref(false);
const hasPendingFolders = ref(false);
const imageRoot = ref(null);
const addFolderPath = ref("");
const addFolderLabel = ref("");
const addFolderError = ref("");
const addFolderLoading = ref(false);
const editFolderDialogOpen = ref(false);
const editingFolder = ref(null);
const editFolderLabel = ref("");
const editFolderAllowDelete = ref(false);
const editFolderSyncCaptions = ref(false);
const editFolderError = ref("");
const editFolderLoading = ref(false);
const deleteFolderConfirmOpen = ref(false);
const folderToDelete = ref(null);
const deleteFolderLoading = ref(false);
const restartLoading = ref(false);
const serverRestarting = ref(false);
const browseDialogOpen = ref(false);
const browsePath = ref("/");
const browseEntries = ref([]);
const browseLoading = ref(false);
const browseError = ref("");
const browseShowHidden = ref(false);

const smartScoreImportanceOptions = [
  { value: 1, label: "Mild" },
  { value: 2, label: "Low" },
  { value: 3, label: "Moderate" },
  { value: 4, label: "High" },
  { value: 5, label: "Severe" },
];

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
  tokenScope.value = "ALL";
  tokenResourceType.value = null;
  tokenResourceId.value = null;
  tokenExpiresAt.value = null;
  shareResourceOptions.value = [];
  shareLinkCopied.value = false;
  smartScoreTagInput.value = "";
  smartScoreTagsError.value = "";
  smartScoreTagsSuccess.value = "";
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
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  comfyuiConfigDialogOpen.value = false;
  workflowImportError.value = "";
  workflowImportName.value = "";
  workflowImportPayload.value = null;
  workflowImportInputs.value = [];
  workflowImportOutputs.value = [];
  workflowImportImageTarget.value = "";
  workflowImportCaptionTarget.value = "";
  workflowImportOutputTargets.value = [];
  workflowImportSaving.value = false;
  workflowListError.value = "";
  refFoldersError.value = "";
  addFolderPath.value = "";
  addFolderLabel.value = "";
  addFolderError.value = "";
  editFolderDialogOpen.value = false;
  editingFolder.value = null;
  editFolderError.value = "";
  deleteFolderConfirmOpen.value = false;
  folderToDelete.value = null;
  browseDialogOpen.value = false;
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
    const comfyUrl = String(res.data?.comfyui_url || "").trim();
    if (comfyUrl) {
      const parsed = parseComfyuiUrl(comfyUrl);
      if (parsed) {
        comfyuiHost.value = parsed.host;
        comfyuiPort.value = parsed.port;
      } else {
        comfyuiHost.value = "";
        comfyuiPort.value = "";
      }
    } else {
      comfyuiHost.value = "";
      comfyuiPort.value = "";
    }
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
    smartScoreTagsError.value = "Failed to load smart score settings.";
    hiddenTagsError.value = "Failed to load hidden tag settings.";
  } finally {
    smartScoreTagsLoading.value = false;
    hiddenTagsLoading.value = false;
  }
}

function parseComfyuiUrl(value) {
  if (!value) return null;
  try {
    const normalized = value.includes("://") ? value : `http://${value}`;
    const parsed = new URL(normalized);
    const host = parsed.hostname || "127.0.0.1";
    const port = parsed.port || "8188";
    return { host, port };
  } catch (e) {
    return null;
  }
}

function openComfyuiConfigDialog() {
  comfyuiEditHost.value = comfyuiHost.value;
  comfyuiEditPort.value = comfyuiPort.value;
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  comfyuiConfigDialogOpen.value = true;
}

async function saveComfyuiUrl() {
  comfyuiUrlLoading.value = true;
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  const host = String(comfyuiEditHost.value || "").trim();
  const port = String(comfyuiEditPort.value || "").trim();
  // Empty host is treated as "not configured" — save null.
  if (!host) {
    try {
      await apiClient.patch("/users/me/config", { comfyui_url: null });
      comfyuiHost.value = "";
      comfyuiPort.value = "";
      emit("update:comfyui-configured", false);
      comfyuiConfigDialogOpen.value = false;
    } catch (e) {
      comfyuiUrlError.value =
        e?.response?.data?.detail ||
        e?.message ||
        "Failed to update ComfyUI URL.";
    } finally {
      comfyuiUrlLoading.value = false;
    }
    return;
  }
  const portNumber = Number(port);
  if (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535) {
    comfyuiUrlError.value = "Port must be between 1 and 65535.";
    comfyuiUrlLoading.value = false;
    return;
  }
  const nextUrl = `http://${host}:${portNumber}/`;
  try {
    await apiClient.patch("/users/me/config", { comfyui_url: nextUrl });
    comfyuiHost.value = host;
    comfyuiPort.value = String(portNumber);
    emit("update:comfyui-configured", true);
    comfyuiUrlSuccess.value = "Saved.";
    setTimeout(() => {
      if (comfyuiUrlSuccess.value === "Saved.") {
        comfyuiUrlSuccess.value = "";
        comfyuiConfigDialogOpen.value = false;
      }
    }, 1200);
  } catch (e) {
    comfyuiUrlError.value =
      e?.response?.data?.detail ||
      e?.message ||
      "Failed to update ComfyUI URL.";
  } finally {
    comfyuiUrlLoading.value = false;
  }
}

async function clearComfyuiUrl() {
  comfyuiUrlLoading.value = true;
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  try {
    await apiClient.patch("/users/me/config", { comfyui_url: null });
    comfyuiHost.value = "";
    comfyuiPort.value = "";
    comfyuiEditHost.value = "";
    comfyuiEditPort.value = "";
    emit("update:comfyui-configured", false);
    comfyuiConfigDialogOpen.value = false;
  } catch (e) {
    comfyuiUrlError.value =
      e?.response?.data?.detail || e?.message || "Failed to clear ComfyUI URL.";
  } finally {
    comfyuiUrlLoading.value = false;
  }
}

async function fetchWorkflowList() {
  workflowListLoading.value = true;
  workflowListError.value = "";
  try {
    const res = await apiClient.get("/comfyui/workflows");
    workflowList.value = Array.isArray(res.data?.workflows)
      ? res.data.workflows
      : [];
  } catch (e) {
    workflowListError.value = "Failed to load workflows.";
  } finally {
    workflowListLoading.value = false;
  }
}

async function fetchReferenceFolders() {
  refFoldersLoading.value = true;
  refFoldersError.value = "";
  try {
    const res = await apiClient.get("/reference-folders");
    referenceFolders.value = Array.isArray(res.data?.folders)
      ? res.data.folders
      : [];
    inDocker.value = Boolean(res.data?.in_docker);
    hasPendingFolders.value = Boolean(res.data?.has_pending);
    imageRoot.value = res.data?.image_root ?? null;
  } catch (e) {
    refFoldersError.value = "Failed to load reference folders.";
  } finally {
    refFoldersLoading.value = false;
  }
}

async function addReferenceFolder() {
  const path = addFolderPath.value.trim();
  if (!path) return;
  addFolderLoading.value = true;
  addFolderError.value = "";
  try {
    await apiClient.post("/reference-folders", {
      folder: path,
      label: addFolderLabel.value.trim() || undefined,
    });
    addFolderPath.value = "";
    addFolderLabel.value = "";
    await fetchReferenceFolders();
  } catch (e) {
    addFolderError.value =
      e?.response?.data?.detail || "Failed to add reference folder.";
  } finally {
    addFolderLoading.value = false;
  }
}

function openEditFolder(rf) {
  editingFolder.value = rf;
  editFolderLabel.value = rf.label || "";
  editFolderAllowDelete.value = Boolean(rf.allow_delete_file);
  editFolderSyncCaptions.value = Boolean(rf.sync_captions);
  editFolderError.value = "";
  editFolderDialogOpen.value = true;
}

async function saveEditFolder() {
  if (!editingFolder.value) return;
  editFolderLoading.value = true;
  editFolderError.value = "";
  try {
    await apiClient.patch(`/reference-folders/${editingFolder.value.id}`, {
      label: editFolderLabel.value.trim() || null,
      allow_delete_file: editFolderAllowDelete.value,
      sync_captions: editFolderSyncCaptions.value,
    });
    editFolderDialogOpen.value = false;
    editingFolder.value = null;
    await fetchReferenceFolders();
  } catch (e) {
    editFolderError.value =
      e?.response?.data?.detail || "Failed to update reference folder.";
  } finally {
    editFolderLoading.value = false;
  }
}

function confirmDeleteFolder(rf) {
  folderToDelete.value = rf;
  deleteFolderConfirmOpen.value = true;
}

async function deleteFolder() {
  if (!folderToDelete.value) return;
  deleteFolderLoading.value = true;
  try {
    await apiClient.delete(`/reference-folders/${folderToDelete.value.id}`);
    deleteFolderConfirmOpen.value = false;
    folderToDelete.value = null;
    await fetchReferenceFolders();
  } catch (e) {
    // keep dialog open, show nothing (folder may already be gone)
    deleteFolderConfirmOpen.value = false;
  } finally {
    deleteFolderLoading.value = false;
  }
}

async function restartServer() {
  restartLoading.value = true;
  try {
    await apiClient.post("/server/restart");
  } catch (_) {
    // Server terminates mid-request — network error is expected
  }
  restartLoading.value = false;
  serverRestarting.value = true;
  // Wait for the server process to go down before polling
  await new Promise((r) => setTimeout(r, 2000));
  // Poll until the server responds again, then reload
  for (;;) {
    try {
      await apiClient.get("/reference-folders");
      window.location.reload();
      return;
    } catch (_) {
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
}

async function openBrowseDialog() {
  browseError.value = "";
  browseEntries.value = [];
  browsePath.value = "";
  browseShowHidden.value = false;
  browseDialogOpen.value = true;
  await browseDir(null);
}

async function browseDir(path) {
  browseLoading.value = true;
  browseError.value = "";
  try {
    const res = await apiClient.get("/filesystem/browse", {
      params: { path: path ?? undefined, show_hidden: browseShowHidden.value },
    });
    browseEntries.value = res.data?.entries ?? [];
    browsePath.value = res.data?.path ?? path ?? "/";
  } catch (e) {
    browseError.value =
      e?.response?.data?.detail || "Cannot browse this directory.";
    browseEntries.value = [];
  } finally {
    browseLoading.value = false;
  }
}

watch(browseShowHidden, () => {
  if (browseDialogOpen.value) {
    browseDir(browsePath.value || null);
  }
});

const registeredFolderPaths = computed(() =>
  referenceFolders.value.map((rf) => rf.folder.replace(/\/$/, "")),
);

function browseEntryDisabledReason(entryPath) {
  const norm = entryPath.replace(/\/$/, "");
  if (imageRoot.value) {
    const root = imageRoot.value.replace(/\/$/, "");
    if (norm === root) return "PixlStash data folder";
  }
  for (const registered of registeredFolderPaths.value) {
    if (norm === registered) return "Already a reference folder";
  }
  return null;
}

function selectBrowsedPath() {
  addFolderPath.value = browsePath.value;
  browseDialogOpen.value = false;
}

async function deleteWorkflow(workflow) {
  if (!workflow?.name) return;
  const confirmed = window.confirm(
    `Delete workflow '${workflow.display_name || workflow.name}'?`,
  );
  if (!confirmed) return;
  try {
    await apiClient.delete(
      `/comfyui/workflows/${encodeURIComponent(workflow.name)}`,
    );
    await fetchWorkflowList();
  } catch (e) {
    workflowListError.value =
      e?.response?.data?.detail || "Failed to delete workflow.";
  }
}

function openWorkflowImport() {
  workflowImportError.value = "";
  workflowImportInputRef.value?.click();
}

async function handleWorkflowFileChange(event) {
  const file = event?.target?.files?.[0];
  if (!file) return;
  workflowImportError.value = "";
  workflowImportPayload.value = null;
  workflowImportInputs.value = [];
  workflowImportOutputs.value = [];
  workflowImportImageTarget.value = "";
  workflowImportCaptionTarget.value = "";
  workflowImportOutputTargets.value = [];
  workflowImportName.value = file.name.replace(/\.json$/i, "");
  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    const inputs = extractWorkflowInputs(payload);
    const outputs = extractWorkflowOutputs(payload);
    workflowImportPayload.value = payload;
    workflowImportInputs.value = inputs;
    workflowImportOutputs.value = outputs;
    if (!inputs.length) {
      workflowImportError.value =
        "No inputs found. This workflow may not be in prompt format.";
    }
    const { imageTarget, captionTarget } = guessWorkflowTargets(inputs);
    workflowImportImageTarget.value = imageTarget || "";
    workflowImportCaptionTarget.value = hasCaptionInputs(inputs)
      ? captionTarget || ""
      : "";
    workflowImportOutputTargets.value = guessWorkflowOutputTargets(
      payload,
      outputs,
    );
    workflowImportDialogOpen.value = true;
  } catch (e) {
    workflowImportError.value = "Failed to parse workflow JSON.";
  } finally {
    event.target.value = "";
  }
}

function extractWorkflowInputs(payload) {
  const entries = [];
  if (!payload || typeof payload !== "object") return entries;

  const isNodeDisabled = (node) => {
    if (!node || typeof node !== "object") return false;
    if (node.disabled === true || node.is_disabled === true) return true;
    if (node.flags && typeof node.flags === "object") {
      if (node.flags.disabled === true) return true;
    }
    return false;
  };

  const prompt =
    payload.prompt && typeof payload.prompt === "object"
      ? payload.prompt
      : null;
  if (prompt) {
    Object.entries(prompt).forEach(([nodeId, node]) => {
      if (isNodeDisabled(node)) return;
      const inputs =
        node?.inputs && typeof node.inputs === "object" ? node.inputs : null;
      if (!inputs) return;
      Object.entries(inputs).forEach(([key, value]) => {
        if (value == null) return;
        if (typeof value !== "string" && typeof value !== "number") return;
        const nodeType = node?.class_type || node?.type || "Node";
        entries.push({
          id: `prompt:${nodeId}:${key}`,
          label: `${nodeType} · ${key}`,
          type: "prompt",
          nodeId,
          inputKey: key,
          nodeType,
        });
      });
    });
  }

  if (!prompt && !Array.isArray(payload.nodes)) {
    const values = Object.values(payload);
    const looksLikeGraph =
      values.length > 0 &&
      values.every(
        (node) =>
          node &&
          typeof node === "object" &&
          node.inputs &&
          typeof node.inputs === "object" &&
          (node.class_type || node.type),
      );
    if (looksLikeGraph) {
      Object.entries(payload).forEach(([nodeId, node]) => {
        if (isNodeDisabled(node)) return;
        const nodeType = node?.class_type || node?.type || "Node";
        const inputs =
          node?.inputs && typeof node.inputs === "object" ? node.inputs : null;
        if (!inputs) return;
        Object.entries(inputs).forEach(([key, value]) => {
          if (value == null) return;
          if (typeof value !== "string" && typeof value !== "number") return;
          entries.push({
            id: `graph:${nodeId}:${key}`,
            label: `${nodeType} · ${key}`,
            type: "graph",
            nodeId,
            inputKey: key,
            nodeType,
          });
        });
      });
    }
  }

  if (Array.isArray(payload.nodes)) {
    payload.nodes.forEach((node, nodeIndex) => {
      if (isNodeDisabled(node)) return;
      const nodeType = node?.type || node?.class_type || "Node";
      if (node?.inputs && typeof node.inputs === "object") {
        Object.entries(node.inputs).forEach(([key, value]) => {
          if (value == null) return;
          if (typeof value !== "string" && typeof value !== "number") return;
          entries.push({
            id: `node:${nodeIndex}:${key}`,
            label: `${nodeType} · ${key}`,
            type: "node_input",
            nodeIndex,
            inputKey: key,
            nodeType,
          });
        });
      }
      if (Array.isArray(node?.widgets_values)) {
        node.widgets_values.forEach((value, widgetIndex) => {
          if (typeof value !== "string") return;
          entries.push({
            id: `widget:${nodeIndex}:${widgetIndex}`,
            label: `${nodeType} · Widget ${widgetIndex + 1}`,
            type: "widget",
            nodeIndex,
            widgetIndex,
            nodeType,
          });
        });
      }
    });
  }

  return entries;
}

function extractWorkflowOutputs(payload) {
  const entries = [];
  if (!payload || typeof payload !== "object") return entries;

  const isNodeDisabled = (node) => {
    if (!node || typeof node !== "object") return false;
    if (node.disabled === true || node.is_disabled === true) return true;
    if (node.flags && typeof node.flags === "object") {
      if (node.flags.disabled === true) return true;
    }
    return false;
  };

  const prompt =
    payload.prompt && typeof payload.prompt === "object"
      ? payload.prompt
      : null;
  if (prompt) {
    Object.entries(prompt).forEach(([nodeId, node]) => {
      if (isNodeDisabled(node)) return;
      const nodeType = node?.class_type || node?.type || "Node";
      if (nodeType !== "SaveImage") return;
      entries.push({
        id: String(nodeId),
        label: `${nodeType} · ${nodeId}`,
        type: "prompt",
        nodeId,
        nodeType,
      });
    });
  }

  if (!prompt && !Array.isArray(payload.nodes)) {
    const values = Object.values(payload);
    const looksLikeGraph =
      values.length > 0 &&
      values.every(
        (node) =>
          node &&
          typeof node === "object" &&
          node.inputs &&
          typeof node.inputs === "object" &&
          (node.class_type || node.type),
      );
    if (looksLikeGraph) {
      Object.entries(payload).forEach(([nodeId, node]) => {
        if (isNodeDisabled(node)) return;
        const nodeType = node?.class_type || node?.type || "Node";
        if (nodeType !== "SaveImage") return;
        entries.push({
          id: String(nodeId),
          label: `${nodeType} · ${nodeId}`,
          type: "graph",
          nodeId,
          nodeType,
        });
      });
    }
  }

  if (Array.isArray(payload.nodes)) {
    payload.nodes.forEach((node, nodeIndex) => {
      if (isNodeDisabled(node)) return;
      const nodeType = node?.type || node?.class_type || "Node";
      if (nodeType !== "SaveImage") return;
      entries.push({
        id: String(nodeIndex),
        label: `${nodeType} · ${nodeIndex + 1}`,
        type: "node",
        nodeIndex,
        nodeType,
      });
    });
  }

  return entries;
}

function guessWorkflowTargets(entries) {
  const loadImageTarget = entries.find((entry) =>
    /loadimage/i.test(entry.nodeType || ""),
  );
  const imageTarget =
    loadImageTarget ||
    entries.find((entry) =>
      /image/i.test(entry.nodeType || entry.inputKey || entry.label || ""),
    );
  const captionTarget = entries.find((entry) =>
    /cliptextencode|prompt|text|caption/i.test(
      entry.nodeType || entry.inputKey || entry.label || "",
    ),
  );
  return {
    imageTarget: imageTarget?.id || "",
    captionTarget: captionTarget?.id || "",
  };
}

function hasCaptionInputs(entries) {
  return entries.some((entry) =>
    /cliptextencode|prompt|text|caption/i.test(
      entry.nodeType || entry.inputKey || entry.label || "",
    ),
  );
}

function guessWorkflowOutputTargets(payload, outputs) {
  const safeOutputs = outputs ?? [];
  const rawTargets =
    payload?.pixlstash_output_nodes ??
    payload?.pixlstash_output_node ??
    payload?.output_node_ids ??
    payload?.output_node_id ??
    null;
  const available = new Set(safeOutputs.map((entry) => entry.id));
  const normalizeTargets = (value) => {
    if (value == null) return [];
    const list = Array.isArray(value) ? value : [value];
    return list
      .map((item) => String(item))
      .filter((item) => !available.size || available.has(item));
  };
  const explicit = normalizeTargets(rawTargets);
  if (explicit.length) return explicit;
  return safeOutputs.map((entry) => entry.id);
}

function getWorkflowInputPreview(payload, targetId) {
  if (!payload || !targetId) return "";
  const entry = workflowImportInputs.value.find((item) => item.id === targetId);
  if (!entry) return "";
  if (entry.type === "prompt") {
    const node = payload.prompt?.[entry.nodeId];
    return node?.inputs?.[entry.inputKey] ?? "";
  }
  if (entry.type === "graph") {
    const node = payload?.[entry.nodeId];
    return node?.inputs?.[entry.inputKey] ?? "";
  }
  if (entry.type === "node_input") {
    const node = payload.nodes?.[entry.nodeIndex];
    return node?.inputs?.[entry.inputKey] ?? "";
  }
  if (entry.type === "widget") {
    const node = payload.nodes?.[entry.nodeIndex];
    if (!node?.widgets_values) return "";
    return node.widgets_values[entry.widgetIndex] ?? "";
  }
  return "";
}

function applyWorkflowPlaceholders(payload, imageTargetId, captionTargetId) {
  const cloned = JSON.parse(JSON.stringify(payload));
  const replacements = [{ id: imageTargetId, value: "{{image_path}}" }];
  if (captionTargetId) {
    replacements.push({ id: captionTargetId, value: "{{caption}}" });
  }
  replacements.forEach(({ id, value }) => {
    const entry = workflowImportInputs.value.find((item) => item.id === id);
    if (!entry) return;
    if (entry.type === "prompt") {
      if (!cloned.prompt || !cloned.prompt[entry.nodeId]) return;
      const inputs = cloned.prompt[entry.nodeId].inputs || {};
      inputs[entry.inputKey] = value;
      cloned.prompt[entry.nodeId].inputs = inputs;
      return;
    }
    if (entry.type === "graph") {
      if (!cloned[entry.nodeId] || !cloned[entry.nodeId].inputs) return;
      cloned[entry.nodeId].inputs[entry.inputKey] = value;
      return;
    }
    if (entry.type === "node_input") {
      const node = cloned.nodes?.[entry.nodeIndex];
      if (!node || !node.inputs) return;
      node.inputs[entry.inputKey] = value;
      return;
    }
    if (entry.type === "widget") {
      const node = cloned.nodes?.[entry.nodeIndex];
      if (!node || !Array.isArray(node.widgets_values)) return;
      node.widgets_values[entry.widgetIndex] = value;
    }
  });
  return cloned;
}

function applyWorkflowOutputTargets(payload, outputTargets) {
  if (!payload || typeof payload !== "object") return payload;
  const targets = Array.isArray(outputTargets)
    ? outputTargets.filter(Boolean).map((value) => String(value))
    : [];
  if (targets.length) {
    payload.pixlstash_output_nodes = targets;
    if (payload.pixlstash_output_node != null) {
      delete payload.pixlstash_output_node;
    }
  } else {
    if (payload.pixlstash_output_nodes != null) {
      delete payload.pixlstash_output_nodes;
    }
    if (payload.pixlstash_output_node != null) {
      delete payload.pixlstash_output_node;
    }
  }
  return payload;
}

async function confirmWorkflowImport() {
  if (!workflowImportPayload.value) return;
  const name = String(workflowImportName.value || "").trim();
  if (!name) {
    workflowImportError.value = "Workflow name is required.";
    return;
  }
  workflowImportSaving.value = true;
  workflowImportError.value = "";
  try {
    const listRes = await apiClient.get("/comfyui/workflows");
    const existing = Array.isArray(listRes.data?.workflows)
      ? listRes.data.workflows
      : [];
    const exists = existing.some(
      (workflow) =>
        workflow?.name === `${name}.json` || workflow?.name === name,
    );
    let overwrite = false;
    if (exists) {
      overwrite = window.confirm(`Workflow '${name}' exists. Overwrite it?`);
      if (!overwrite) {
        workflowImportSaving.value = false;
        return;
      }
    }

    const updated = applyWorkflowPlaceholders(
      workflowImportPayload.value,
      workflowImportImageTarget.value,
      workflowImportCaptionTarget.value,
    );
    const outputTargets = Array.isArray(workflowImportOutputTargets.value)
      ? workflowImportOutputTargets.value
      : [];
    const updatedWithOutputs = applyWorkflowOutputTargets(
      updated,
      outputTargets,
    );
    await apiClient.post("/comfyui/workflows/import", {
      name,
      workflow: updatedWithOutputs,
      overwrite,
    });
    workflowImportDialogOpen.value = false;
    await fetchWorkflowList();
  } catch (e) {
    workflowImportError.value =
      e?.response?.data?.detail || "Failed to import workflow.";
  } finally {
    workflowImportSaving.value = false;
  }
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

function copyToken() {
  if (!newlyCreatedToken.value) return;
  navigator.clipboard.writeText(newlyCreatedToken.value);
  tokenCopied.value = true;
  setTimeout(() => {
    tokenCopied.value = false;
  }, 2000);
}

async function loadShareResourceOptions(type) {
  if (!type) {
    shareResourceOptions.value = [];
    return;
  }
  shareResourceLoading.value = true;
  try {
    let items = [];
    if (type === "picture_set") {
      const res = await apiClient.get("/picture_sets");
      items = (res.data || []).map((s) => ({ id: s.id, label: s.name }));
    } else if (type === "character") {
      const res = await apiClient.get("/characters");
      items = (res.data || []).map((c) => ({ id: c.id, label: c.name }));
    } else if (type === "project") {
      const res = await apiClient.get("/projects");
      items = (res.data || []).map((p) => ({ id: p.id, label: p.name }));
    }
    shareResourceOptions.value = items;
  } catch {
    shareResourceOptions.value = [];
  } finally {
    shareResourceLoading.value = false;
  }
}

watch(tokenResourceType, (type) => {
  tokenResourceId.value = null;
  loadShareResourceOptions(type);
});

const shareUrl = computed(() => {
  if (!newlyCreatedToken.value || tokenScope.value !== "READ") return null;
  const base = window.location.origin + window.location.pathname;
  return `${base}?token=${newlyCreatedToken.value}`;
});

async function copyShareLink() {
  if (!shareUrl.value) return;
  try {
    await navigator.clipboard.writeText(shareUrl.value);
    shareLinkCopied.value = true;
    setTimeout(() => { shareLinkCopied.value = false; }, 2000);
  } catch {
    // Clipboard not available
  }
}

async function createUserToken() {
  tokensError.value = "";
  const description = tokenDescription.value.trim() || null;
  tokensLoading.value = true;
  try {
    const res = await apiClient.post("/users/me/token", {
      description,
      scope: tokenScope.value,
      resource_type: tokenScope.value === "READ" ? tokenResourceType.value : null,
      resource_id: tokenScope.value === "READ" ? tokenResourceId.value : null,
      expires_at: tokenExpiresAt.value || null,
    });
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
      } catch {
        // Storing credentials is best-effort; ignore failures.
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
      settingsTab.value = "appearance";
      fetchSettingsAuth();
      fetchUserTokens();
      fetchSmartScoreSettings();
      fetchWorkflowList();
      fetchReferenceFolders();
    }
  },
);

watch(maxVramGbValue, () => {
  scheduleMaxVramGbSave();
});

const workflowImageInputOptions = computed(() => [
  { title: "None (text-to-image)", value: "" },
  ...(workflowImportInputs.value || []).map((entry) => ({
    title: entry.label,
    value: entry.id,
  })),
]);

const workflowCaptionInputOptions = computed(() => [
  { title: "No caption", value: "" },
  ...workflowImageInputOptions.value,
]);

const workflowOutputNodeOptions = computed(() =>
  (workflowImportOutputs.value || []).map((entry) => ({
    title: entry.label,
    value: entry.id,
  })),
);

const workflowImportImagePreview = computed(() => {
  return getWorkflowInputPreview(
    workflowImportPayload.value,
    workflowImportImageTarget.value,
  );
});

const workflowImportCaptionPreview = computed(() => {
  return getWorkflowInputPreview(
    workflowImportPayload.value,
    workflowImportCaptionTarget.value,
  );
});
</script>

<template>
  <v-dialog
    v-model="dialogOpen"
    max-width="950"
    @click:outside="dialogOpen = false"
  >
    <div class="settings-dialog-shell">
      <!-- Restarting overlay -->
      <div v-if="serverRestarting" class="settings-restarting-overlay">
        <v-progress-circular indeterminate size="48" color="primary" />
        <p class="settings-restarting-text">Restarting PixlStash…</p>
      </div>
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
          <v-tab value="behaviour">Behaviour</v-tab>
          <v-tab value="smart-score">Smart Score</v-tab>
          <v-tab value="workflows">Workflows</v-tab>
          <v-tab value="folders">Folders</v-tab>
          <v-tab value="account">Account Settings</v-tab>
        </v-tabs>
        <v-card-text class="settings-dialog-body">
          <v-window v-model="settingsTab" class="settings-tab-body">
            <v-window-item value="appearance">
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div
                  class="settings-section-title"
                  title="Adjust the sidebar thumbnail size."
                >
                  Sidebar Thumbnails
                </div>
                <div class="settings-slider-row">
                  <span class="settings-slider-value">
                    {{ sidebarThumbnailSizeModel }}px
                  </span>
                  <v-slider
                    v-model="sidebarThumbnailSizeModel"
                    :min="24"
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
                <div
                  class="settings-section-title"
                  title="Choose a light or dark theme."
                >
                  Theme
                </div>
                <v-select
                  v-model="themeModeModel"
                  :items="themeModeOptions"
                  item-title="title"
                  item-value="value"
                  density="compact"
                  variant="filled"
                  class="settings-add-tag-input"
                  hide-details
                />
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div
                  class="settings-section-title"
                  title="Choose how dates are shown in the grid and overlays."
                >
                  Date Format
                </div>
                <v-select
                  v-model="dateFormatModel"
                  :items="dateFormatOptions"
                  item-title="title"
                  item-value="value"
                  density="compact"
                  variant="filled"
                  class="settings-add-tag-input"
                  hide-details
                />
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <v-checkbox
                  v-model="showKeyboardHintModel"
                  density="compact"
                  hide-details
                  label="Show keyboard shortcut (F1) indicator"
                />
              </div>
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
                    <v-tooltip :text="entry.tag" location="top">
                      <template #activator="{ props: tooltipProps }">
                        <span
                          class="settings-tag-label"
                          v-bind="tooltipProps"
                          >{{ entry.tag }}</span
                        >
                      </template>
                    </v-tooltip>
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
                      density="compact"
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
            </v-window-item>
            <v-window-item value="workflows">
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">ComfyUI Host</div>
                <div class="settings-section-desc">
                  Configure the local ComfyUI server used for workflows.
                </div>
                <div class="settings-comfyui-display">
                  <div class="settings-comfyui-row">
                    <span class="settings-comfyui-label">Host</span>
                    <span class="settings-comfyui-value">{{
                      comfyuiHost || "Not configured"
                    }}</span>
                  </div>
                  <div class="settings-comfyui-row">
                    <span class="settings-comfyui-label">Port</span>
                    <span class="settings-comfyui-value">{{
                      comfyuiPort || "—"
                    }}</span>
                  </div>
                  <v-btn
                    variant="outlined"
                    color="primary"
                    size="small"
                    class="settings-action-btn"
                    style="margin-top: 8px"
                    @click="openComfyuiConfigDialog"
                  >
                    Configure
                  </v-btn>
                </div>
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Import Workflow</div>
                <div class="settings-section-desc">
                  Import a ComfyUI workflow JSON and map its image/caption
                  inputs.
                </div>
                <div class="settings-form">
                  <v-btn
                    variant="outlined"
                    color="primary"
                    class="settings-action-btn"
                    @click="openWorkflowImport"
                  >
                    Import Workflow
                  </v-btn>
                  <div v-if="workflowImportError" class="settings-error">
                    {{ workflowImportError }}
                  </div>
                  <div v-else class="settings-success">
                    {{ "\u00a0" }}
                  </div>
                </div>
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Saved Workflows</div>
                <div class="settings-section-desc">
                  Manage your saved ComfyUI workflows.
                </div>
                <div class="settings-form">
                  <div v-if="workflowListLoading" class="settings-success">
                    Loading workflows...
                  </div>
                  <div v-else-if="workflowListError" class="settings-error">
                    {{ workflowListError }}
                  </div>
                  <div
                    v-else-if="!workflowList.length"
                    class="settings-success"
                  >
                    No workflows saved yet.
                  </div>
                  <div v-else class="settings-tag-list">
                    <div
                      v-for="workflow in workflowList"
                      :key="workflow.name"
                      class="settings-tag-chip settings-tag-chip--row"
                    >
                      <span class="settings-tag-label">
                        {{ workflow.display_name || workflow.name }}
                      </span>
                      <span
                        class="settings-tag-label"
                        :style="{ opacity: workflow.valid ? 0.65 : 1 }"
                      >
                        {{
                          workflow.valid
                            ? `valid ${workflow.workflow_type || "i2i"}`
                            : "invalid"
                        }}
                      </span>
                      <v-btn
                        v-if="workflow.source !== 'built-in'"
                        icon
                        variant="text"
                        class="settings-tag-delete"
                        @click="deleteWorkflow(workflow)"
                      >
                        <v-icon size="16">mdi-delete</v-icon>
                      </v-btn>
                    </div>
                  </div>
                </div>
              </div>
            </v-window-item>
            <v-window-item value="folders">
              <!-- Pending restart banner -->
              <div
                v-if="hasPendingFolders"
                class="settings-folders-restart-banner"
              >
                <v-icon size="18" style="margin-right: 6px">mdi-restart</v-icon>
                Restart PixlStash to mount the new folder(s).
                <v-btn
                  size="small"
                  color="warning"
                  variant="flat"
                  class="settings-folders-restart-btn"
                  :loading="restartLoading"
                  @click="restartServer"
                >
                  Restart PixlStash
                </v-btn>
              </div>

              <v-divider
                v-if="hasPendingFolders"
                class="settings-section-divider"
              />

              <!-- Folder list -->
              <div class="settings-section">
                <div class="settings-section-title">Reference Folders</div>
                <div class="settings-section-desc">
                  Index images that live outside your vault — directly from
                  their original location on disk. Files are not copied.
                </div>
                <div class="settings-form">
                  <div v-if="refFoldersLoading" class="settings-success">
                    Loading folders…
                  </div>
                  <div v-else-if="refFoldersError" class="settings-error">
                    {{ refFoldersError }}
                  </div>
                  <div
                    v-else-if="!referenceFolders.length"
                    class="settings-success"
                  >
                    No reference folders added yet.
                  </div>
                  <div v-else class="settings-tag-list">
                    <div
                      v-for="rf in referenceFolders"
                      :key="rf.id"
                      class="settings-tag-chip settings-tag-chip--row"
                    >
                      <v-icon
                        size="15"
                        :color="
                          rf.status === 'mount_error'
                            ? 'error'
                            : rf.status === 'pending_mount'
                              ? 'warning'
                              : 'success'
                        "
                        style="flex-shrink: 0"
                        :title="
                          rf.status === 'mount_error'
                            ? 'Mount error'
                            : rf.status === 'pending_mount'
                              ? 'Pending restart'
                              : 'Active'
                        "
                      >
                        {{
                          rf.status === "mount_error"
                            ? "mdi-alert-circle-outline"
                            : rf.status === "pending_mount"
                              ? "mdi-clock-outline"
                              : "mdi-check-circle-outline"
                        }}
                      </v-icon>
                      <span class="settings-tag-label" :title="rf.folder">
                        {{ rf.label || rf.folder }}
                      </span>
                      <span
                        class="settings-tag-label settings-folders-path"
                        :title="rf.folder"
                      >
                        {{ rf.folder }}
                      </span>
                      <v-btn
                        icon
                        variant="text"
                        class="settings-tag-delete"
                        title="Edit"
                        @click="openEditFolder(rf)"
                      >
                        <v-icon size="15">mdi-pencil-outline</v-icon>
                      </v-btn>
                      <v-btn
                        icon
                        variant="text"
                        class="settings-tag-delete"
                        title="Remove"
                        @click="confirmDeleteFolder(rf)"
                      >
                        <v-icon size="15">mdi-delete</v-icon>
                      </v-btn>
                    </div>
                  </div>
                </div>
              </div>

              <v-divider class="settings-section-divider" />

              <!-- Add folder -->
              <div class="settings-section">
                <div class="settings-section-title">Add Reference Folder</div>
                <div class="settings-section-desc">
                  <span v-if="inDocker">
                    Enter the <strong>host path</strong> to the folder (as
                    configured via <code>--path-map</code>).
                  </span>
                  <span v-else>
                    Enter the folder path directly or click
                    <strong>Browse…</strong> to pick a directory.
                  </span>
                </div>
                <div class="settings-form">
                  <v-text-field
                    v-model="addFolderPath"
                    label="Folder path"
                    density="compact"
                    variant="outlined"
                    hide-details
                    placeholder="/path/to/folder"
                    class="settings-folders-input"
                  />
                  <v-text-field
                    v-model="addFolderLabel"
                    label="Display label (optional)"
                    density="compact"
                    variant="outlined"
                    hide-details
                    placeholder="My Photos"
                    class="settings-folders-input"
                    style="margin-top: 8px"
                  />
                  <div class="settings-folders-add-actions">
                    <v-btn
                      v-if="!inDocker"
                      variant="outlined"
                      size="small"
                      prepend-icon="mdi-folder-open-outline"
                      class="settings-action-btn"
                      @click="openBrowseDialog"
                    >
                      Browse…
                    </v-btn>
                    <v-btn
                      variant="flat"
                      color="primary"
                      size="small"
                      prepend-icon="mdi-plus"
                      class="settings-action-btn"
                      :loading="addFolderLoading"
                      :disabled="!addFolderPath.trim()"
                      @click="addReferenceFolder"
                    >
                      Add Folder
                    </v-btn>
                  </div>
                  <div v-if="addFolderError" class="settings-error">
                    {{ addFolderError }}
                  </div>
                </div>
              </div>
            </v-window-item>
            <v-window-item value="account">
              <div class="settings-section">
                <div
                  class="settings-section-title"
                  title="Change your password or manage sign-in options."
                >
                  Account
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
                    density="compact"
                    variant="filled"
                    hide-details
                    autocomplete="current-password"
                    name="current-password"
                  />
                  <v-text-field
                    v-model="newPassword"
                    label="New password"
                    :type="showNewPassword ? 'text' : 'password'"
                    density="compact"
                    variant="filled"
                    hide-details
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
                <div
                  class="settings-section-title"
                  title="Manage tokens for authenticated API access."
                >
                  API Tokens
                </div>
                <div class="settings-tokens">
                  <v-text-field
                    v-model="tokenDescription"
                    label="Token description"
                    density="compact"
                    variant="filled"
                    class="settings-add-tag-input"
                    hide-details
                    :disabled="tokensLoading"
                    @keydown.enter.prevent="createUserToken"
                  />
                  <v-select
                    v-model="tokenScope"
                    :items="[{ title: 'Full access', value: 'ALL' }, { title: 'Read-only share', value: 'READ' }]"
                    item-title="title"
                    item-value="value"
                    label="Access type"
                    density="compact"
                    variant="filled"
                    hide-details
                    :disabled="tokensLoading"
                  />
                  <template v-if="tokenScope === 'READ'">
                    <v-select
                      v-model="tokenResourceType"
                      :items="[{ title: 'Picture Set', value: 'picture_set' }, { title: 'Character', value: 'character' }, { title: 'Project', value: 'project' }]"
                      item-title="title"
                      item-value="value"
                      label="Resource type"
                      density="compact"
                      variant="filled"
                      hide-details
                      clearable
                      :disabled="tokensLoading"
                    />
                    <v-select
                      v-if="tokenResourceType"
                      v-model="tokenResourceId"
                      :items="shareResourceOptions"
                      item-title="label"
                      item-value="id"
                      label="Resource"
                      density="compact"
                      variant="filled"
                      hide-details
                      :loading="shareResourceLoading"
                      :disabled="tokensLoading || shareResourceLoading"
                    />
                    <v-text-field
                      v-model="tokenExpiresAt"
                      label="Expires at (optional, e.g. 2027-01-01)"
                      density="compact"
                      variant="filled"
                      hide-details
                      :disabled="tokensLoading"
                    />
                  </template>
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
                        <v-chip
                          v-if="token.scope"
                          size="x-small"
                          :color="token.scope === 'ALL' ? 'default' : 'info'"
                          class="settings-token-scope-chip"
                        >
                          {{ token.scope === 'ALL' ? 'Full access' : `Read · ${token.resource_type ?? ''} ${token.resource_id != null ? '#' + token.resource_id : ''}`.trim() }}
                        </v-chip>
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
                        size="small"
                        density="compact"
                        variant="text"
                        class="settings-token-delete"
                        :disabled="tokensLoading"
                        @click="confirmDeleteToken(token)"
                      >
                        <v-icon size="16">mdi-delete</v-icon>
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

  <v-dialog v-model="tokenDialogOpen" max-width="520">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title">New API Token</v-card-title>
      <v-card-text class="settings-dialog-body">
        <div class="settings-token-warning">
          Copy this token now. You won’t be able to see it again.
        </div>
        <div class="settings-token-value-row">
          <div class="settings-token-value">{{ newlyCreatedToken }}</div>
          <v-btn
            icon
            variant="text"
            size="small"
            class="settings-token-copy-btn"
            :title="tokenCopied ? 'Copied!' : 'Copy token'"
            @click="copyToken"
          >
            <v-icon size="18">{{
              tokenCopied ? "mdi-check" : "mdi-content-copy"
            }}</v-icon>
          </v-btn>
        </div>
        <template v-if="shareUrl">
          <div class="settings-token-warning" style="margin-top:8px;">
            Share this URL — anyone with it gets read access to the selected resource.
          </div>
          <div class="settings-token-value-row">
            <div class="settings-token-value" style="word-break:break-all;font-size:11px;">{{ shareUrl }}</div>
            <v-btn
              icon
              variant="text"
              size="small"
              class="settings-token-copy-btn"
              :title="shareLinkCopied ? 'Copied!' : 'Copy share link'"
              @click="copyShareLink"
            >
              <v-icon size="18">{{
                shareLinkCopied ? "mdi-check" : "mdi-link"
              }}</v-icon>
            </v-btn>
          </div>
        </template>
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

  <v-dialog v-model="tokenDeleteDialogOpen" max-width="420">
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

  <input
    ref="workflowImportInputRef"
    type="file"
    accept="application/json"
    style="display: none"
    @change="handleWorkflowFileChange"
  />

  <v-dialog v-model="workflowImportDialogOpen" max-width="640">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title">
        Import Workflow
      </v-card-title>
      <v-card-text class="settings-dialog-body">
        <v-text-field
          v-model="workflowImportName"
          label="Workflow name"
          density="compact"
          variant="filled"
        />
        <v-select
          v-model="workflowImportImageTarget"
          :items="workflowImageInputOptions"
          item-title="title"
          item-value="value"
          label="Image input"
          density="compact"
          variant="filled"
        />
        <div v-if="workflowImportImagePreview" class="settings-token-warning">
          Current value: {{ workflowImportImagePreview }}
        </div>
        <v-select
          v-model="workflowImportCaptionTarget"
          :items="workflowCaptionInputOptions"
          item-title="title"
          item-value="value"
          label="Caption input"
          density="compact"
          variant="filled"
        />
        <div v-if="workflowImportCaptionPreview" class="settings-token-warning">
          Current value: {{ workflowImportCaptionPreview }}
        </div>
        <v-select
          v-model="workflowImportOutputTargets"
          :items="workflowOutputNodeOptions"
          item-title="title"
          item-value="value"
          label="SaveImage outputs"
          multiple
          density="compact"
          variant="filled"
          :disabled="!workflowOutputNodeOptions.length"
        />
        <div
          v-if="!workflowOutputNodeOptions.length"
          class="settings-token-warning"
        >
          No SaveImage nodes detected. Outputs will be auto-detected.
        </div>
        <div v-else class="settings-success">
          Leave empty to use all SaveImage nodes.
        </div>
        <div v-if="workflowImportError" class="settings-error">
          {{ workflowImportError }}
        </div>
      </v-card-text>
      <v-card-actions class="settings-dialog-actions">
        <v-spacer />
        <v-btn variant="text" @click="workflowImportDialogOpen = false">
          Cancel
        </v-btn>
        <v-btn
          variant="outlined"
          color="primary"
          :loading="workflowImportSaving"
          :disabled="workflowImportSaving"
          @click="confirmWorkflowImport"
        >
          Import
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="comfyuiConfigDialogOpen" max-width="420">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title"
        >Configure ComfyUI</v-card-title
      >
      <v-card-text class="settings-dialog-body">
        <v-text-field
          v-model="comfyuiEditHost"
          label="Host"
          density="compact"
          variant="filled"
          :disabled="comfyuiUrlLoading"
          placeholder="e.g. 127.0.0.1"
        />
        <v-text-field
          v-model="comfyuiEditPort"
          label="Port"
          density="compact"
          variant="filled"
          :disabled="comfyuiUrlLoading"
          placeholder="e.g. 8188"
          @keydown.enter.prevent="saveComfyuiUrl"
        />
        <div v-if="comfyuiUrlError" class="settings-error">
          {{ comfyuiUrlError }}
        </div>
        <div v-else-if="comfyuiUrlSuccess" class="settings-success">
          {{ comfyuiUrlSuccess }}
        </div>
      </v-card-text>
      <v-card-actions class="settings-dialog-actions">
        <v-btn
          variant="outlined"
          color="error"
          :loading="comfyuiUrlLoading"
          :disabled="comfyuiUrlLoading"
          @click="clearComfyuiUrl"
        >
          Clear
        </v-btn>
        <v-spacer />
        <v-btn
          variant="text"
          :disabled="comfyuiUrlLoading"
          @click="comfyuiConfigDialogOpen = false"
        >
          Cancel
        </v-btn>
        <v-btn
          variant="outlined"
          color="primary"
          :loading="comfyuiUrlLoading"
          :disabled="comfyuiUrlLoading"
          @click="saveComfyuiUrl"
        >
          Save
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- Edit reference folder dialog -->
  <v-dialog v-model="editFolderDialogOpen" max-width="480">
    <v-card class="settings-inner-dialog">
      <v-card-title class="settings-inner-dialog-title"
        >Edit Reference Folder</v-card-title
      >
      <v-card-text>
        <div class="settings-section-desc" style="margin-bottom: 12px">
          <strong>Path:</strong> {{ editingFolder?.folder }}
        </div>
        <v-text-field
          v-model="editFolderLabel"
          label="Display label"
          density="compact"
          variant="outlined"
          placeholder="Leave blank to show path"
          hide-details
        />
        <div style="margin-top: 16px">
          <v-checkbox
            v-model="editFolderSyncCaptions"
            label="Sync caption files (write tags back to .txt sidecar)"
            density="compact"
            hide-details
          />
          <div style="margin-top: 4px; font-size: 0.78rem; opacity: 0.7">
            When enabled, tag changes made in PixlStash are written back to a
            <code>.txt</code> sidecar file next to each image.
          </div>
        </div>
        <div style="margin-top: 12px">
          <v-checkbox
            v-model="editFolderAllowDelete"
            label="Allow deleting source files from PixlStash"
            density="compact"
            hide-details
            color="error"
          />
          <div
            v-if="editFolderAllowDelete"
            class="settings-error"
            style="margin-top: 4px; font-size: 0.78rem"
          >
            Warning: enabling this allows PixlStash to permanently delete files
            from your disk.
          </div>
        </div>
        <div
          v-if="editFolderError"
          class="settings-error"
          style="margin-top: 8px"
        >
          {{ editFolderError }}
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="editFolderDialogOpen = false"
          >Cancel</v-btn
        >
        <v-btn
          variant="outlined"
          color="primary"
          :loading="editFolderLoading"
          @click="saveEditFolder"
        >
          Save
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- Delete reference folder confirmation -->
  <v-dialog v-model="deleteFolderConfirmOpen" max-width="420">
    <v-card class="settings-inner-dialog">
      <v-card-title class="settings-inner-dialog-title"
        >Remove Reference Folder?</v-card-title
      >
      <v-card-text>
        <p>
          Remove
          <strong>{{ folderToDelete?.label || folderToDelete?.folder }}</strong>
          from PixlStash?
        </p>
        <p class="settings-section-desc" style="margin-top: 6px">
          The original files on disk will not be deleted. Only the index entries
          will be removed from PixlStash.
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="deleteFolderConfirmOpen = false"
          >Cancel</v-btn
        >
        <v-btn
          variant="flat"
          color="error"
          :loading="deleteFolderLoading"
          @click="deleteFolder"
        >
          Remove
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- Browse dialog (native only) -->
  <v-dialog v-model="browseDialogOpen" max-width="720">
    <v-card class="settings-inner-dialog">
      <v-card-title class="settings-inner-dialog-title"
        >Browse for Folder</v-card-title
      >
      <v-card-text style="padding: 0">
        <div class="settings-browse-path">
          <v-icon size="16" style="opacity: 0.6; margin-right: 4px"
            >mdi-folder</v-icon
          >
          <span class="settings-browse-path-text">{{ browsePath }}</span>
        </div>
        <div class="settings-browse-entries">
          <div v-if="browseLoading" class="settings-browse-loading">
            <v-progress-circular indeterminate size="24" />
          </div>
          <div
            v-else-if="browseError"
            class="settings-error"
            style="padding: 12px"
          >
            {{ browseError }}
          </div>
          <div v-else-if="!browseEntries.length" class="settings-browse-empty">
            No subdirectories
          </div>
          <div
            v-for="entry in browseEntries"
            :key="entry.path"
            :class="[
              'settings-browse-entry',
              browseEntryDisabledReason(entry.path)
                ? 'settings-browse-entry--disabled'
                : '',
            ]"
            :title="
              browseEntryDisabledReason(entry.path) || 'Browse into this folder'
            "
            @click="
              !browseEntryDisabledReason(entry.path) && browseDir(entry.path)
            "
          >
            <v-icon size="16" style="opacity: 0.65; flex-shrink: 0"
              >mdi-folder</v-icon
            >
            <span class="settings-browse-entry-name">{{ entry.name }}</span>
            <span
              v-if="browseEntryDisabledReason(entry.path)"
              class="settings-browse-entry-conflict"
              >{{ browseEntryDisabledReason(entry.path) }}</span
            >
          </div>
        </div>
      </v-card-text>
      <v-card-actions>
        <v-btn
          variant="text"
          size="small"
          prepend-icon="mdi-chevron-up"
          :disabled="browsePath === '/' || browseLoading"
          @click="browseDir(browsePath.replace(/\/[^/]+\/?$/, '') || '/')"
        >
          Up
        </v-btn>
        <v-btn
          variant="text"
          size="small"
          :prepend-icon="
            browseShowHidden ? 'mdi-eye-outline' : 'mdi-eye-off-outline'
          "
          :title="
            browseShowHidden ? 'Hide hidden folders' : 'Show hidden folders'
          "
          @click="browseShowHidden = !browseShowHidden"
        >
          Hidden
        </v-btn>
        <v-spacer />
        <v-btn variant="text" @click="browseDialogOpen = false">Cancel</v-btn>
        <v-btn
          variant="outlined"
          color="primary"
          class="settings-browse-select-btn"
          :title="browsePath"
          @click="selectBrowsedPath"
        >
          <span class="settings-browse-select-label"
            >Select "{{
              browsePath.replace(/\/$/, "").split("/").pop() || browsePath
            }}"</span
          >
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
  color: rgba(0, 0, 0, 0.6);
  white-space: nowrap;
  line-height: 1;
}

.settings-stepper-controls {
  display: flex;
  align-items: center;
  border: 1px solid rgba(0, 0, 0, 0.25);
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
  background: rgba(0, 0, 0, 0.05);
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
  background: rgba(0, 0, 0, 0.12);
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
  border-left: 1px solid rgba(0, 0, 0, 0.15);
  border-right: 1px solid rgba(0, 0, 0, 0.15);
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
  border: 1px solid rgba(0, 0, 0, 0.2);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  outline: none;
  padding: 0;
  color: rgba(0, 0, 0, 0.5);
  flex-shrink: 0;
  transition:
    background 0.15s,
    color 0.15s;
}

.settings-threshold-preview-btn:hover {
  background: rgba(0, 0, 0, 0.07);
  color: rgba(0, 0, 0, 0.8);
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
  gap: 4px;
}

.settings-token-loading {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-token-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 220px;
  overflow-y: auto;
  padding-right: 4px;
}

.settings-token-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(var(--v-theme-surface), 0.2);
}

.settings-token-meta {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.settings-token-desc {
  font-weight: 600;
}

.settings-token-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 0.78em;
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

.settings-restarting-overlay {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  background: rgba(var(--v-theme-surface), 0.88);
  backdrop-filter: blur(4px);
  border-radius: inherit;
}

.settings-restarting-text {
  font-size: 1rem;
  opacity: 0.8;
  margin: 0;
}

/* ── Reference Folders settings ─────────────────────────────────── */
.settings-folders-restart-banner {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 16px;
  background: rgba(var(--v-theme-warning), 0.12);
  border-left: 3px solid rgb(var(--v-theme-warning));
  font-size: 0.85rem;
  margin: 0 0 4px;
}

.settings-folders-restart-btn {
  margin-left: auto;
}

.settings-folders-path {
  opacity: 0.52;
  font-size: 0.77rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.settings-folders-input {
  width: 100%;
}

.settings-folders-add-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.settings-browse-path {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  font-size: 0.82rem;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.25);
  font-family: monospace;
  word-break: break-all;
}

.settings-browse-path-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-browse-select-btn {
  max-width: 260px;
  min-width: 0;
}

.settings-browse-select-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
  min-width: 0;
}

.settings-browse-entries {
  min-height: 200px;
  max-height: 320px;
  overflow-y: auto;
}

.settings-browse-loading {
  display: flex;
  justify-content: center;
  padding: 32px;
}

.settings-browse-empty {
  padding: 24px 16px;
  font-size: 0.82rem;
  opacity: 0.5;
  text-align: center;
}

.settings-browse-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  cursor: pointer;
  font-size: 0.84rem;
}

.settings-browse-entry:hover:not(.settings-browse-entry--disabled) {
  background: rgba(var(--v-theme-accent), 0.1);
}

.settings-browse-entry--disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.settings-browse-entry-conflict {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 0.72rem;
  opacity: 0.7;
  font-style: italic;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}

.settings-browse-entry-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
