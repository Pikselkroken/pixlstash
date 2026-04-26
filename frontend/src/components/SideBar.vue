<script setup>
import {
  computed,
  ref,
  onBeforeUnmount,
  onMounted,
  watch,
  nextTick,
} from "vue";
import ImageImporter from "./ImageImporter.vue";
import CharacterEditor from "./CharacterEditor.vue";
import PictureSetEditor from "./PictureSetEditor.vue";
import ProjectEditor from "./ProjectEditor.vue";
import ProjectFiles from "./ProjectFiles.vue";
import TaskManager from "./TaskManager.vue";
import UserSettingsDialog from "./UserSettingsDialog.vue";
import FolderTreeNode from "./FolderTreeNode.vue";
import ReferenceFolderEditor from "./ReferenceFolderEditor.vue";
import ImportFolderEditor from "./ImportFolderEditor.vue";
import unknownPerson from "../assets/unknown-person.png"; // Fallback avatar for characters without thumbnails
import { apiClient } from "../utils/apiClient";
import { extractSupportedImportFilesFromDataTransfer } from "../utils/media.js";

const appVersion = __APP_VERSION__;

const latestVersion = ref(null);
const latestVersionUrl = ref(null);

// PEP 440-aware version comparison: treats rc/a/b/dev as pre-releases.
function parseVersion(v) {
  const m = String(v).match(
    /^(\d+)\.(\d+)\.(\d+)(?:(\.?(?:a|b|rc|dev))(\d+))?/i,
  );
  if (!m) return null;
  const preTag = m[4]?.toLowerCase().replace(/^\./, "");
  const preWeight = { dev: -4, a: -3, b: -2, rc: -1 }[preTag] ?? 0;
  return [
    Number(m[1]),
    Number(m[2]),
    Number(m[3]),
    preWeight,
    Number(m[5] || 0),
  ];
}
function isRemoteNewer(current, remote) {
  const a = parseVersion(current);
  const b = parseVersion(remote);
  if (!a || !b) return false; // conservatively: don't advertise if we can't parse
  for (let i = 0; i < a.length; i++) {
    if (b[i] > a[i]) return true;
    if (b[i] < a[i]) return false;
  }
  return false;
}

const updateAvailable = computed(
  () => latestVersion.value && isRemoteNewer(appVersion, latestVersion.value),
);

const LATEST_VERSION_URL = "https://pixlstash.dev/latest-version.json";
const UPDATE_PAGE_URL = "https://pixlstash.dev/upgrade.html";

const props = defineProps({
  collapsed: { type: Boolean, default: false },
  selectedCharacter: { type: [String, Number, null], default: null },
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  scrapheapPicturesId: { type: String, required: true },
  selectedSet: { type: [Number, null], default: null },
  selectedSetIds: { type: Array, default: () => [] },
  selectedCharacterIds: { type: Array, default: () => [] },
  searchQuery: { type: String, default: "" },
  selectedSort: { type: String, default: "" },
  selectedDescending: { type: Boolean, default: false },
  selectedSimilarityCharacter: { type: [String, Number, null], default: null },
  backendUrl: { type: String, required: true },
  sidebarThumbnailSize: { type: Number, default: 48 },
  dateFormat: { type: String, default: "locale" },
  themeMode: { type: String, default: "light" },
  hasFolderFilter: { type: Boolean, default: false },
  checkForUpdates: { type: Boolean, default: null },
  installType: { type: String, default: "pip" },
  showKeyboardHint: { type: Boolean, default: true },
});

const emit = defineEmits([
  "select-character",
  "update:selected-sort",
  "update:search-query",
  "select-set",
  "import-finished",
  "set-error",
  "set-loading",
  "images-assigned-to-character",
  "faces-assigned-to-character",
  "images-moved",
  "search-images",
  "update:similarity-character",
  "update:similarity-options",
  "update:sidebar-thumbnail-size",
  "update:date-format",
  "update:theme-mode",
  "toggle-sidebar",
  "update:sort-options",
  "update:hidden-tags",
  "update:apply-tag-filter",
  "update:comfyui-configured",
  "open-import-dialog",
  "update:project-view-mode",
  "update:selected-project-id",
  "update:check-for-updates",
  "update:show-keyboard-hint",
  "select-folder",
  "update:folder-scanning",
]);

const imageImporterRef = ref(null);
const sidebarRootRef = ref(null);
const labelOverflow = ref({});
const labelRefs = new Map();
const labelObservers = new Map();

const dragOverSet = ref(null);
const dragOverProjectPictures = ref(false);

const peopleSectionCollapsed = ref(false);
const setsSectionCollapsed = ref(false);

// --- Sorting State ---
const sortOptions = ref([]);

// --- Character & Sidebar State ---
const characters = ref([]);
const categoryCounts = ref({
  [props.allPicturesId]: 0,
  [props.unassignedPicturesId]: 0,
  [props.scrapheapPicturesId]: 0,
});
// Counts keyed by project id (number) or UNASSIGNED_PROJECT_KEY (unassigned in project mode)
const UNASSIGNED_PROJECT_KEY = "UNASSIGNED";
const projectCounts = ref({});

const flashCountsNextFetch = ref(false);
const countNewTags = ref({});
const knownCountIds = new Set();

const characterThumbnails = ref({});
const setThumbnails = ref({});
const setThumbnailRetryCounts = ref({});
const setThumbnailRetryTimers = new Map();
const SET_THUMBNAIL_MAX_RETRIES = 2;
const expandedCharacters = ref({});

const dragOverCharacter = ref(null);
const nextCharacterNumber = ref(1);

// --- Picture Sets State ---
const pictureSets = ref([]);

// --- Project State ---
const projects = ref([]);
const projectViewMode = ref("global"); // 'global' | 'project'
const selectedProjectId = ref(null); // null = 'No project' in project view
// Tracks the view context when allPicturesId was last selected, so active state
// correctly distinguishes «All Pictures» (global) from «Project Pictures» (project).
const allPicturesLastMode = ref("global");
const allPicturesLastProjectId = ref(null);
const lastUsedProjectId = ref(null); // remembers last selected project for auto-select
const projectEditorOpen = ref(false);
const projectMenuOpen = ref(false);
const projectMenuRef = ref(null);
const collapsedProjectBtnRef = ref(null);
const collapsedProjectMenuRef = ref(null);
const collapsedProjectMenuPos = ref({ top: 0, left: 0 });
const projectEditorProject = ref(null);

// --- Move-to-project menus ---
const characterMoveMenuOpen = ref(false);
const characterMoveMenuBtnRef = ref(null);
const characterMenuPos = ref({ top: 0, left: 0 });
const setMoveMenuOpen = ref(false);
const setMoveMenuBtnRef = ref(null);
const setMenuPos = ref({ top: 0, left: 0 });

// --- Sidebar Context Menu ---
const sidebarCtxVisible = ref(false);
const sidebarCtxX = ref(0);
const sidebarCtxY = ref(0);
const sidebarCtxCharacter = ref(null); // { id, name } or null
const sidebarCtxSet = ref(null); // { id, name } or null
const sidebarCtxFolder = ref(null); // reference folder object or null
const sidebarCtxImportFolder = ref(null); // import folder object or null
const sidebarCtxDeleteIds = ref([]); // character IDs to delete via context menu

function openCharacterMoveMenu() {
  if (characterMoveMenuBtnRef.value) {
    const rect = characterMoveMenuBtnRef.value.getBoundingClientRect();
    characterMenuPos.value = {
      top: rect.bottom + 4,
      left: rect.left,
    };
  }
  characterMoveMenuOpen.value = !characterMoveMenuOpen.value;
}

function openSetMoveMenu() {
  if (setMoveMenuBtnRef.value) {
    const rect = setMoveMenuBtnRef.value.getBoundingClientRect();
    setMenuPos.value = {
      top: rect.bottom + 4,
      left: rect.left,
    };
  }
  setMoveMenuOpen.value = !setMoveMenuOpen.value;
}

// --- Character Editor State ---
const characterEditorOpen = ref(false);
const characterEditorCharacter = ref(null);

const setEditorOpen = ref(false);
const setEditorSet = ref(null);
const settingsDialogOpen = ref(false);
const taskManagerOpen = ref(false);

// --- Reference Folders (Folders tab) ---
const sidebarPrimaryTab = ref("library"); // 'library' | 'folders'
const referenceFolders = ref([]);
const referenceFoldersLoading = ref(false);
const importFolders = ref([]);
const importFoldersLoading = ref(false);
const inDocker = ref(false);
const referenceFoldersImageRoot = ref(null);
const expandedFolderIds = ref(new Set());
const folderBrowseCache = ref({}); // keyed by path → { entries, loading, image_count }
const selectedFolderKey = ref(null); // 'rf-{id}' | 'path-{path}' | 'if-{id}' | null
const selectedFolderReferenceId = ref(null); // numeric reference-folder id or null

// Reference folder editor state
const referenceFolderEditorOpen = ref(false);
const referenceFolderEditorFolder = ref(null); // null = create, object = edit
const importFolderEditorOpen = ref(false);
const importFolderEditorFolder = ref(null); // null = create, object = edit
const addFolderTypeDialogOpen = ref(false);

function openAddFolderTypeDialog() {
  addFolderTypeDialogOpen.value = true;
}

function chooseFolderType(type) {
  addFolderTypeDialogOpen.value = false;
  if (type === "import") {
    openImportFolderEditor();
    return;
  }
  openReferenceFolderEditor();
}

function openReferenceFolderEditor(rf = null) {
  referenceFolderEditorFolder.value = rf ?? null;
  referenceFolderEditorOpen.value = true;
}

function closeReferenceFolderEditor() {
  referenceFolderEditorOpen.value = false;
  referenceFolderEditorFolder.value = null;
}

function openImportFolderEditor(folder = null) {
  importFolderEditorFolder.value = folder ?? null;
  importFolderEditorOpen.value = true;
}

function closeImportFolderEditor() {
  importFolderEditorOpen.value = false;
  importFolderEditorFolder.value = null;
}

function showDockerRestartPrompt() {
  window.alert(
    "Docker mode: restart the PixlStash container with the new folder mount, then open PixlStash again.",
  );
}

async function referenceFolderSaved() {
  const createdNewFolder = !referenceFolderEditorFolder.value?.id;
  closeReferenceFolderEditor();
  await fetchReferenceFolders();
  // A newly added folder may be active-but-unscanned, so ensure polling runs.
  _startFolderStatusPoll();
  if (inDocker.value && createdNewFolder) {
    showDockerRestartPrompt();
  }
}

async function referenceFolderDeleted() {
  closeReferenceFolderEditor();
  // If we were browsing this folder, clear the selection
  selectedFolderKey.value = null;
  selectedFolderReferenceId.value = null;
  emit("select-folder", null);
  emit("update:folder-scanning", false);
  await fetchReferenceFolders();
}

async function importFolderSaved() {
  const createdNewFolder = !importFolderEditorFolder.value?.id;
  closeImportFolderEditor();
  await fetchImportFolders();
  if (inDocker.value && createdNewFolder) {
    showDockerRestartPrompt();
  }
  if (!selectedFolderKey.value?.startsWith("if-")) return;
  const selectedId = Number(selectedFolderKey.value.slice(3));
  if (!Number.isFinite(selectedId)) return;
  const selectedImportFolder = importFolders.value.find(
    (entry) => Number(entry.id) === selectedId,
  );
  if (!selectedImportFolder) {
    selectedFolderKey.value = null;
    selectedFolderReferenceId.value = null;
    emit("select-folder", null);
    emit("update:folder-scanning", false);
    return;
  }
  emit("select-folder", {
    importSourceFolder: selectedImportFolder.folder,
    label: selectedImportFolder.label || selectedImportFolder.folder,
  });
}

async function importFolderDeleted() {
  const deletedId = Number(importFolderEditorFolder.value?.id);
  closeImportFolderEditor();
  if (
    Number.isFinite(deletedId) &&
    selectedFolderKey.value === `if-${deletedId}`
  ) {
    selectedFolderKey.value = null;
    selectedFolderReferenceId.value = null;
    emit("select-folder", null);
    emit("update:folder-scanning", false);
  }
  await fetchImportFolders();
}

const registeredFolderPaths = computed(() =>
  referenceFolders.value.map((rf) => rf.folder.replace(/\/$/, "")),
);

const registeredImportFolderPaths = computed(() =>
  importFolders.value.map((entry) => entry.folder.replace(/\/$/, "")),
);

const selectedReferenceFolderForHeader = computed(() => {
  const id = Number(selectedFolderReferenceId.value);
  if (!Number.isFinite(id)) return null;
  return (
    referenceFolders.value.find((folder) => Number(folder.id) === id) || null
  );
});

const selectedImportFolderForHeader = computed(() => {
  if (!selectedFolderKey.value?.startsWith("if-")) return null;
  const id = Number(selectedFolderKey.value.slice(3));
  if (!Number.isFinite(id)) return null;
  return importFolders.value.find((entry) => Number(entry.id) === id) || null;
});

// Whether the currently selected reference folder is actively being scanned
// for the first time (active but never completed a pass).
const selectedFolderScanning = computed(() => {
  if (selectedFolderKey.value?.startsWith("if-")) {
    const id = Number(selectedFolderKey.value.slice(3));
    if (!Number.isFinite(id)) return false;
    const importFolder = importFolders.value.find(
      (entry) => Number(entry.id) === id,
    );
    return Boolean(importFolder && importFolder.last_checked == null);
  }
  const id = Number(selectedFolderReferenceId.value);
  if (!Number.isFinite(id)) return false;
  const rf = referenceFolders.value.find((f) => f.id === id);
  return Boolean(rf && rf.status === "active" && rf.last_scanned == null);
});

watch(selectedFolderScanning, (val) => {
  emit("update:folder-scanning", val);
});

async function fetchReferenceFolders() {
  referenceFoldersLoading.value = true;
  try {
    const res = await apiClient.get("/reference-folders");
    referenceFolders.value = res.data?.folders ?? [];
    inDocker.value = Boolean(res.data?.in_docker);
    referenceFoldersImageRoot.value = res.data?.image_root ?? null;
    // In non-Docker mode we eagerly browse roots so we know which have
    // subdirectories (controls whether the expand chevron is shown).
    if (!inDocker.value) {
      referenceFolders.value.forEach((rf) => browseFolderPath(rf.folder, true));
    }
    // If any folder is still pending, start polling for status updates.
    if (sidebarPrimaryTab.value === "folders") {
      _startFolderStatusPoll();
    }
  } catch (e) {
    console.error("Failed to fetch reference folders:", e);
  } finally {
    referenceFoldersLoading.value = false;
  }
}

async function fetchImportFolders() {
  importFoldersLoading.value = true;
  try {
    const res = await apiClient.get("/import-folders");
    importFolders.value = res.data?.folders ?? [];
    if (sidebarPrimaryTab.value === "folders") {
      _startFolderStatusPoll();
    }
  } catch (e) {
    console.error("Failed to fetch import folders:", e);
  } finally {
    importFoldersLoading.value = false;
  }
}

function toggleFolderExpanded(folderId) {
  const set = new Set(expandedFolderIds.value);
  if (set.has(folderId)) {
    set.delete(folderId);
  } else {
    set.add(folderId);
  }
  expandedFolderIds.value = set;
}

async function browseFolderPath(path, prefetchChildren = false) {
  if (inDocker.value) {
    // Filesystem browse is intentionally disabled in Docker mode.
    return;
  }
  const cached = folderBrowseCache.value[path];
  if (cached) {
    if (prefetchChildren && !cached.loading && !cached.error) {
      const childEntries = cached.entries ?? [];
      childEntries.forEach((entry) => {
        void browseFolderPath(entry.path, false);
      });
    }
    return;
  }
  folderBrowseCache.value = {
    ...folderBrowseCache.value,
    [path]: { entries: [], loading: true, image_count: null },
  };
  try {
    const res = await apiClient.get(
      `/filesystem/browse?path=${encodeURIComponent(path)}`,
    );
    const entries = res.data?.entries ?? [];
    const imageCount = Number(res.data?.image_count);
    folderBrowseCache.value = {
      ...folderBrowseCache.value,
      [path]: {
        entries,
        loading: false,
        image_count: Number.isFinite(imageCount) ? imageCount : null,
      },
    };
    if (prefetchChildren && entries.length > 0) {
      entries.forEach((entry) => {
        void browseFolderPath(entry.path, false);
      });
    }
  } catch (e) {
    folderBrowseCache.value = {
      ...folderBrowseCache.value,
      [path]: { entries: [], loading: false, image_count: null, error: true },
    };
  }
}

function handleFolderNodeSelect(key, payload) {
  selectedFolderKey.value = key;
  const payloadId = Number(payload?.referenceFolderId);
  if (Number.isFinite(payloadId)) {
    selectedFolderReferenceId.value = payloadId;
  } else if (key?.startsWith("rf-")) {
    const parsed = parseInt(key.slice(3), 10);
    selectedFolderReferenceId.value = Number.isFinite(parsed) ? parsed : null;
  } else {
    selectedFolderReferenceId.value = null;
  }
  emit("select-folder", payload);
  // Emit immediately on selection so ImageGrid updates before next poll tick.
  emit("update:folder-scanning", selectedFolderScanning.value);
}

async function handleFolderNodeToggle(path) {
  if (inDocker.value) {
    return;
  }
  if (expandedFolderIds.value.has(path)) {
    toggleFolderExpanded(path);
    return;
  }
  await browseFolderPath(path, true);
  const cached = folderBrowseCache.value[path];
  const childCount = cached?.entries?.length ?? 0;
  if (cached?.error || childCount === 0) {
    return;
  }
  toggleFolderExpanded(path);
}

watch(sidebarPrimaryTab, (tab) => {
  if (tab === "folders") {
    fetchReferenceFolders();
    fetchImportFolders();
    _startFolderStatusPoll();
  } else {
    _stopFolderStatusPoll();
  }
});

// Poll for folder status updates while the Folders tab is open.
// Keeps polling while any reference folder is transitioning/retrying
// (pending, mount_error, or first active scan) OR any import folder has not
// completed its first scan yet (last_checked === null).
let _folderStatusPollTimer = null;

function _anyFolderNeedsPolling() {
  const referenceNeedsPolling = referenceFolders.value.some(
    (rf) =>
      rf.status === "pending_mount" ||
      rf.status === "mount_error" ||
      (rf.status === "active" && rf.last_scanned == null),
  );
  const importNeedsPolling = importFolders.value.some(
    (entry) => entry.last_checked == null,
  );
  return referenceNeedsPolling || importNeedsPolling;
}

async function _pollFolderStatus() {
  try {
    const [referenceRes, importRes] = await Promise.all([
      apiClient.get("/reference-folders"),
      apiClient.get("/import-folders"),
    ]);
    const folders = referenceRes.data?.folders ?? [];
    const updatedImportFolders = importRes.data?.folders ?? [];
    // Detect folders whose first scan just completed so we can refresh
    // the browse cache (image counts were zero before).
    const justScanned = referenceFolders.value.filter((rf) => {
      const updated = folders.find((f) => f.id === rf.id);
      return updated && rf.last_scanned == null && updated.last_scanned != null;
    });
    // Merge status + last_scanned updates into existing list.
    referenceFolders.value = referenceFolders.value.map((rf) => {
      const updated = folders.find((f) => f.id === rf.id);
      return updated
        ? { ...rf, status: updated.status, last_scanned: updated.last_scanned }
        : rf;
    });
    // Add any newly created folders that weren't in the list yet.
    for (const f of folders) {
      if (!referenceFolders.value.find((rf) => rf.id === f.id)) {
        referenceFolders.value = [...referenceFolders.value, f];
        if (!inDocker.value) {
          browseFolderPath(f.folder, true);
        }
      }
    }
    // Refresh browse cache for folders whose initial scan just finished.
    if (!inDocker.value) {
      for (const rf of justScanned) {
        // Evict stale cache entry so browseFolderPath re-fetches.
        const next = { ...folderBrowseCache.value };
        delete next[rf.folder];
        folderBrowseCache.value = next;
        browseFolderPath(rf.folder, true);
      }
    }

    // Refresh import-folder counts and first-scan state.
    importFolders.value = updatedImportFolders;

    // Stop polling when nothing is still transitioning.
    if (!_anyFolderNeedsPolling()) {
      _stopFolderStatusPoll();
    }
  } catch {
    // Ignore transient errors — just try again next tick.
  }
}

function _startFolderStatusPoll() {
  _stopFolderStatusPoll();
  if (!_anyFolderNeedsPolling()) return;
  void _pollFolderStatus();
  _folderStatusPollTimer = setInterval(_pollFolderStatus, 3000);
}

function _stopFolderStatusPoll() {
  if (_folderStatusPollTimer !== null) {
    clearInterval(_folderStatusPollTimer);
    _folderStatusPollTimer = null;
  }
}

onBeforeUnmount(() => _stopFolderStatusPoll());

function selectFoldersTab() {
  sidebarPrimaryTab.value = "folders";
  // Reset project mode so the grid isn't filtered by a project while on the
  // folders tab. Setting projectViewMode triggers its own watcher which emits
  // update:project-view-mode to App.vue.
  projectViewMode.value = "global";
  // Clear character / set selection so the folders view starts unfiltered.
  emit("select-character", { id: props.allPicturesId, label: "All Pictures" });
  emit("select-set", null);
}

function selectLibraryTab(mode) {
  sidebarPrimaryTab.value = "library";
  if (mode === "project") {
    switchToProjectView();
  } else {
    projectViewMode.value = "global";
  }
  // Clear the folder filter so the library view is not restricted to a folder.
  selectedFolderReferenceId.value = null;
  emit("select-folder", null);
  emit("update:folder-scanning", false);
}
function updateLabelOverflow(key, el = null) {
  const element = el || labelRefs.get(key);
  if (!element) return;
  const width = element.clientWidth;
  const isOverflowing = width > 0 && element.scrollWidth > width + 1;
  if (labelOverflow.value[key] !== isOverflowing) {
    labelOverflow.value = { ...labelOverflow.value, [key]: isOverflowing };
  }
}

function registerLabelRef(key, el) {
  const existingObserver = labelObservers.get(key);
  if (existingObserver) {
    existingObserver.disconnect();
    labelObservers.delete(key);
  }

  if (!el) {
    labelRefs.delete(key);
    if (labelOverflow.value[key] !== undefined) {
      const next = { ...labelOverflow.value };
      delete next[key];
      labelOverflow.value = next;
    }
    return;
  }

  labelRefs.set(key, el);
  const observer = new ResizeObserver(() => updateLabelOverflow(key, el));
  observer.observe(el);
  labelObservers.set(key, observer);
  requestAnimationFrame(() => updateLabelOverflow(key, el));
}

function labelNeedsTooltip(key) {
  return Boolean(labelOverflow.value[key]);
}

function refreshLabelOverflows() {
  for (const [key, el] of labelRefs.entries()) {
    updateLabelOverflow(key, el);
  }
}

function mergeTooltipRef(refProps, key) {
  return (el) => {
    if (refProps?.ref) {
      if (typeof refProps.ref === "function") {
        refProps.ref(el);
      } else {
        refProps.ref.value = el;
      }
    }
    registerLabelRef(key, el);
  };
}

const sidebarNotice = ref(null);
const sidebarNoticeTargetId = ref(null);
const sidebarNoticeTargetType = ref("set");
const sidebarNoticePosition = ref(null);
const setItemRefs = ref(new Map());
const characterItemRefs = ref(new Map());
let sidebarNoticeTimeout = null;
const sidebarError = ref(null);
const sidebarErrorTargetId = ref(null);
const sidebarErrorTargetType = ref("set");
const sidebarErrorPosition = ref(null);
let sidebarErrorTimeout = null;

function registerSetRef(setId, el) {
  if (!setId) return;
  if (el) {
    setItemRefs.value.set(setId, el);
  } else {
    setItemRefs.value.delete(setId);
  }
}

function registerCharacterRef(characterId, el) {
  if (!characterId) return;
  if (el) {
    characterItemRefs.value.set(characterId, el);
  } else {
    characterItemRefs.value.delete(characterId);
  }
}

function updateSidebarNoticePosition() {
  if (!sidebarNotice.value || !sidebarNoticeTargetId.value) {
    sidebarNoticePosition.value = null;
    return;
  }
  const targetMap =
    sidebarNoticeTargetType.value === "character"
      ? characterItemRefs.value
      : setItemRefs.value;
  const target = targetMap.get(sidebarNoticeTargetId.value);
  if (!target) return;
  const rect = target.getBoundingClientRect();
  sidebarNoticePosition.value = {
    top: rect.top + rect.height / 2,
    left: rect.right + 12,
  };
}

function updateSidebarErrorPosition() {
  if (!sidebarError.value || !sidebarErrorTargetId.value) {
    sidebarErrorPosition.value = null;
    return;
  }
  const targetMap =
    sidebarErrorTargetType.value === "character"
      ? characterItemRefs.value
      : setItemRefs.value;
  const target = targetMap.get(sidebarErrorTargetId.value);
  if (!target) return;
  const rect = target.getBoundingClientRect();
  const sidebarRect = sidebarRootRef.value
    ? sidebarRootRef.value.getBoundingClientRect()
    : null;
  const baseLeft = sidebarRect ? sidebarRect.right + 12 : rect.right + 12;
  sidebarErrorPosition.value = {
    top: rect.top + rect.height / 2,
    left: baseLeft,
  };
}

function createSet() {
  const defaultProjectId =
    projectViewMode.value === "project" ? selectedProjectId.value : null;
  setEditorSet.value =
    defaultProjectId !== null ? { project_id: defaultProjectId } : null;
  setEditorOpen.value = true;
}

function toggleProjectMenu() {
  if (
    !projectMenuOpen.value &&
    props.collapsed &&
    collapsedProjectBtnRef.value
  ) {
    const rect = collapsedProjectBtnRef.value.getBoundingClientRect();
    collapsedProjectMenuPos.value = { top: rect.top, left: rect.right + 4 };
  }
  projectMenuOpen.value = !projectMenuOpen.value;
}

function selectProject(id) {
  selectedProjectId.value = id;
  projectMenuOpen.value = false;
}

function createProject() {
  projectMenuOpen.value = false;
  projectEditorProject.value = null;
  projectEditorOpen.value = true;
}

function exportProject(project) {
  projectMenuOpen.value = false;
  const url = `${props.backendUrl}/projects/${project.id}/export`;
  const a = document.createElement("a");
  a.href = url;
  a.download = `${project.name}.zip`;
  a.click();
}

function openProjectEditor(project) {
  projectEditorProject.value = project;
  projectEditorOpen.value = true;
}

function closeProjectEditor() {
  projectEditorOpen.value = false;
  projectEditorProject.value = null;
}

async function projectSaved(newProjectId) {
  closeProjectEditor();
  await fetchProjects();
  if (newProjectId != null) {
    selectedProjectId.value = newProjectId;
    projectViewMode.value = "project";
  }
}

async function projectDeleted(deletedId) {
  closeProjectEditor();
  if (selectedProjectId.value === deletedId) {
    selectedProjectId.value = null;
  }
  projectViewMode.value = "global";
  await fetchProjects();
  await fetchCharacters();
  await fetchSidebarData();
}

const sortedProjects = computed(() =>
  [...projects.value].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
  ),
);

const sortedCharacters = computed(() => {
  return [...characters.value]
    .filter((c) => c && typeof c.name === "string" && c.name.trim() !== "")
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    );
});

const selectedCharacterObj = computed(() => {
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== props.allPicturesId &&
    props.selectedCharacter !== props.unassignedPicturesId &&
    props.selectedCharacter !== props.scrapheapPicturesId
  ) {
    const char =
      characters.value.find((c) => c.id === props.selectedCharacter) || null;
    if (char && typeof char.name === "string" && char.name.length > 0) {
      return {
        ...char,
        name: char.name.charAt(0).toUpperCase() + char.name.slice(1),
      };
    }
    return char;
  }
  return null;
});

const selectedSetObj = computed(() => {
  const primarySetId =
    Array.isArray(props.selectedSetIds) && props.selectedSetIds.length
      ? props.selectedSetIds[0]
      : props.selectedSet;
  if (!primarySetId) return null;
  return pictureSets.value.find((pset) => pset.id === primarySetId) || null;
});

const selectedSetIdSet = computed(
  () =>
    new Set(
      (Array.isArray(props.selectedSetIds) ? props.selectedSetIds : [])
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0),
    ),
);

const hasSingleSelectedSet = computed(() => selectedSetIdSet.value.size === 1);

const selectedCharacterIdSet = computed(
  () =>
    new Set(
      (Array.isArray(props.selectedCharacterIds)
        ? props.selectedCharacterIds
        : []
      )
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0),
    ),
);

const hasSingleSelectedCharacter = computed(
  () => selectedCharacterIdSet.value.size === 1,
);

const nonReferenceSets = computed(() =>
  pictureSets.value.filter((pset) => !pset.reference_character),
);

const selectedProjectObj = computed(() =>
  projectViewMode.value === "project" && selectedProjectId.value !== null
    ? projects.value.find((p) => p.id === selectedProjectId.value) || null
    : null,
);

const visibleCharacters = computed(() => {
  if (projectViewMode.value === "global") return sortedCharacters.value;
  return sortedCharacters.value.filter(
    (c) => c.project_id === selectedProjectId.value,
  );
});

const projectMenuCharacterGroups = computed(() => {
  if (projectViewMode.value !== "project" || selectedProjectId.value === null)
    return [];
  const all = sortedCharacters.value;
  const globalItems = all.filter((c) => c.project_id === null);
  const projectsSorted = [...projects.value].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  const groups = [];
  if (globalItems.length > 0) {
    groups.push({ label: "Global", projectId: null, items: globalItems });
  }
  for (const proj of projectsSorted) {
    const items = all.filter((c) => c.project_id === proj.id);
    if (items.length > 0) {
      groups.push({ label: proj.name, projectId: proj.id, items });
    }
  }
  return groups;
});

const projectMenuSetGroups = computed(() => {
  if (projectViewMode.value !== "project" || selectedProjectId.value === null)
    return [];
  const all = nonReferenceSets.value;
  const globalItems = all.filter((s) => s.project_id === null);
  const projectsSorted = [...projects.value].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  const groups = [];
  if (globalItems.length > 0) {
    groups.push({ label: "Global", projectId: null, items: globalItems });
  }
  for (const proj of projectsSorted) {
    const items = all.filter((s) => s.project_id === proj.id);
    if (items.length > 0) {
      groups.push({ label: proj.name, projectId: proj.id, items });
    }
  }
  return groups;
});

const visibleSets = computed(() => {
  if (projectViewMode.value === "global") return nonReferenceSets.value;
  return nonReferenceSets.value.filter(
    (s) => s.project_id === selectedProjectId.value,
  );
});

// --- Similarity Character Dropdown State ---
const SIMILARITY_SORT_KEY = "CHARACTER_LIKENESS"; // Adjust if backend uses a different key
const DATE_SORT_KEY = "DATE";

const similarityCharacterOptions = computed(() => {
  let options = sortedCharacters.value.map((c) => ({
    text: c.name,
    value: c.id,
    thumbnail: characterThumbnails.value?.[c.id] || null,
  }));
  return options;
});

watch(
  similarityCharacterOptions,
  (options) => {
    emit("update:similarity-options", options);
  },
  { immediate: true },
);

const similarityCharacterModel = computed({
  get: () => props.selectedSimilarityCharacter,
  set: (value) => emit("update:similarity-character", value ?? null),
});

const sidebarThumbnailSizeModel = computed({
  get: () => props.sidebarThumbnailSize ?? 64,
  set: (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(64, Math.max(24, parsed));
    const snapped = Math.round(clamped / 8) * 8;
    emit("update:sidebar-thumbnail-size", snapped);
  },
});

const dateFormatModel = computed({
  get: () => props.dateFormat ?? "locale",
  set: (value) => emit("update:date-format", value ?? "locale"),
});

const themeModeModel = computed({
  get: () => props.themeMode ?? "light",
  set: (value) => emit("update:theme-mode", value ?? "light"),
});

const showKeyboardHintModel = computed({
  get: () => props.showKeyboardHint ?? true,
  set: (value) => emit("update:show-keyboard-hint", value ?? true),
});

const sidebarThumbnailSizeLarge = computed(
  () => sidebarThumbnailSizeModel.value + 8,
);

const sidebarThumbStyle = computed(() => ({
  "--sidebar-thumb-size": `${sidebarThumbnailSizeModel.value}px`,
}));

const isSearchActive = computed(() => {
  const query = typeof props.searchQuery === "string" ? props.searchQuery : "";
  return query.trim().length > 0;
});

const reactiveSelectedDescending = ref(props.selectedDescending);

watch(
  () => props.selectedDescending,
  (newValue, oldValue) => {
    reactiveSelectedDescending.value = newValue;
  },
);

const descendingModel = computed({
  get: () => {
    return reactiveSelectedDescending.value;
  },
  set: (value) => {
    reactiveSelectedDescending.value = value;
    emit("update:selected-sort", { sort: sortModel.value, descending: value });
  },
});

const sortModel = computed({
  get: () => props.selectedSort,
  set: (value) =>
    emit("update:selected-sort", {
      sort: value != null ? String(value) : "",
      descending: descendingModel.value,
    }),
});

const searchModel = computed({
  get: () => props.searchQuery,
  set: (value) => emit("update:search-query", value ?? ""),
});

// --- Character Editor Dialog Functions ---
function openCharacterEditor(char = null) {
  characterEditorCharacter.value = char;
  characterEditorOpen.value = true;
}

function closeCharacterEditor() {
  characterEditorOpen.value = false;
  characterEditorCharacter.value = null;
}

// --- Picture Set Editor ---
function openSetEditor(set = null) {
  setEditorSet.value = set;
  setEditorOpen.value = true;
}

function closeSetEditor() {
  setEditorOpen.value = false;
  setEditorSet.value = null;
}

function openSettingsDialog() {
  settingsDialogOpen.value = true;
}

function selectCharacter(id, label = null, event = null) {
  clearCountNew(id);
  const isSpecial =
    id === props.allPicturesId ||
    id === props.unassignedPicturesId ||
    id === props.scrapheapPicturesId;
  const isMultiToggle = !isSpecial && Boolean(event?.ctrlKey || event?.metaKey);

  if (!isMultiToggle) {
    if (id === props.allPicturesId) {
      allPicturesLastMode.value = projectViewMode.value;
      allPicturesLastProjectId.value = selectedProjectId.value;
    }
    emit("select-set", null);
    emit("select-character", { id, label, ids: isSpecial ? [] : [Number(id)] });
    return;
  }

  // Ctrl/Cmd-click: toggle this character in the multi-selection
  const numericId = Number(id);
  const currentIds = new Set(selectedCharacterIdSet.value);

  if (currentIds.size === 0) {
    // Nothing selected yet — treat as plain click
    emit("select-set", null);
    emit("select-character", { id, label, ids: [numericId] });
    return;
  }

  if (currentIds.has(numericId)) {
    currentIds.delete(numericId);
  } else {
    currentIds.add(numericId);
  }

  const nextIds = Array.from(currentIds).sort((a, b) => a - b);
  if (!nextIds.length) {
    emit("select-character", { id: props.allPicturesId, label: null, ids: [] });
    return;
  }

  // Keep the primary view unchanged on ctrl-click
  const primaryId = props.selectedCharacter ?? nextIds[0];
  emit("select-character", { id: primaryId, label: null, ids: nextIds });
}

function selectSet(setId, label = null, event = null) {
  emit("select-character", null);
  const numericSetId = Number(setId);
  if (!Number.isFinite(numericSetId) || numericSetId <= 0) {
    emit("select-set", null);
    return;
  }

  const isMultiToggle = Boolean(event?.ctrlKey || event?.metaKey);
  if (!isMultiToggle) {
    emit("select-set", {
      id: numericSetId,
      label,
      ids: [numericSetId],
      names: { [numericSetId]: label || String(numericSetId) },
    });
    return;
  }

  const nextIds = new Set(selectedSetIdSet.value);
  if (nextIds.has(numericSetId)) {
    nextIds.delete(numericSetId);
  } else {
    nextIds.add(numericSetId);
  }
  const ids = Array.from(nextIds).sort((a, b) => a - b);
  if (!ids.length) {
    emit("select-set", null);
    return;
  }
  const primarySet = pictureSets.value.find((pset) => pset.id === ids[0]);
  const setNames = {};
  for (const sid of ids) {
    const found = pictureSets.value.find((p) => p.id === sid);
    if (found) setNames[sid] = found.name;
  }
  emit("select-set", {
    id: ids[0],
    label: primarySet?.name || label,
    ids,
    names: setNames,
  });
}

async function deleteCharacter() {
  if (!props.selectedCharacter) return;
  if (!window.confirm("Delete this character?")) return;
  try {
    await apiClient.delete(`/characters/${props.selectedCharacter}`);

    // Remove the deleted character from the characters array
    characters.value = characters.value.filter(
      (char) => char.id !== props.selectedCharacter,
    );

    await fetchCharacters(); // Refresh sidebar
  } catch (e) {
    setError(e.message);
  }
}

async function deleteCharacterById(id) {
  const char = characters.value.find((c) => c.id === id);
  if (!char) return;
  if (!window.confirm(`Delete character "${char.name}"?`)) return;
  try {
    await apiClient.delete(`/characters/${id}`);
    characters.value = characters.value.filter((c) => c.id !== id);
    await fetchCharacters();
  } catch (e) {
    setError(e.message);
  }
}

async function deleteCharactersByIds(ids) {
  if (!ids?.length) return;
  const single = ids.length === 1;
  const charName = single
    ? characters.value.find((c) => Number(c.id) === ids[0])?.name
    : null;
  const msg = single
    ? `Delete character "${charName}"?`
    : `Delete ${ids.length} characters?`;
  if (!window.confirm(msg)) return;
  try {
    await Promise.all(ids.map((id) => apiClient.delete(`/characters/${id}`)));
    characters.value = characters.value.filter(
      (c) => !ids.includes(Number(c.id)),
    );
    await fetchCharacters();
  } catch (e) {
    setError(e.message);
  }
}

async function deleteSetById(id) {
  const set = pictureSets.value.find((s) => s.id === id);
  if (!set) return;
  if (
    !window.confirm(
      `Delete picture set "${set.name}"? This will unassign all their images.`,
    )
  )
    return;
  try {
    await apiClient.delete(`${props.backendUrl}/picture_sets/${id}`);
    emit("select-set", null);
    await fetchPictureSets();
    await fetchSidebarData();
  } catch (e) {
    alert("Failed to delete set: " + (e.message || e));
  }
}

async function deleteSetsByIds(ids) {
  if (!ids?.length) return;

  const normalizedIds = Array.from(
    new Set(ids.map((id) => Number(id)).filter((id) => Number.isFinite(id))),
  );
  if (!normalizedIds.length) return;

  const single = normalizedIds.length === 1;
  const setName = single
    ? pictureSets.value.find((s) => Number(s.id) === normalizedIds[0])?.name
    : null;
  const msg = single
    ? `Delete picture set "${setName}"? This will unassign all their images.`
    : `Delete ${normalizedIds.length} picture sets? This will unassign all their images.`;
  if (!window.confirm(msg)) return;

  try {
    await Promise.all(
      normalizedIds.map((id) =>
        apiClient.delete(`${props.backendUrl}/picture_sets/${id}`),
      ),
    );
    emit("select-set", null);
    await fetchPictureSets();
    await fetchSidebarData();
  } catch (e) {
    alert("Failed to delete picture set(s): " + (e.message || e));
  }
}

async function deleteReferenceFolderById(id) {
  const folder = referenceFolders.value.find((rf) => rf.id === id);
  if (!folder) return;
  const folderLabel = folder.label || folder.folder;
  if (!window.confirm(`Remove reference folder "${folderLabel}"?`)) return;
  try {
    await apiClient.delete(`/reference-folders/${id}`);
    if (selectedFolderKey.value === `rf-${id}`) {
      selectedFolderKey.value = null;
      emit("select-folder", null);
    }
    await fetchReferenceFolders();
  } catch (e) {
    alert("Failed to remove reference folder: " + (e.message || e));
  }
}

async function deleteImportFolderById(id) {
  const folder = importFolders.value.find((entry) => entry.id === id);
  if (!folder) return;
  const folderLabel = folder.label || folder.folder;
  if (!window.confirm(`Remove import folder "${folderLabel}"?`)) return;
  try {
    await apiClient.delete(`/import-folders/${id}`);
    if (selectedFolderKey.value === `if-${id}`) {
      selectedFolderKey.value = null;
      selectedFolderReferenceId.value = null;
      emit("select-folder", null);
      emit("update:folder-scanning", false);
    }
    await fetchImportFolders();
  } catch (e) {
    alert("Failed to remove import folder: " + (e.message || e));
  }
}

function openSidebarCtxMenu(type, item, event) {
  if (type === "character") {
    sidebarCtxCharacter.value = item;
    sidebarCtxSet.value = null;
    sidebarCtxFolder.value = null;
    sidebarCtxImportFolder.value = null;
    const numId = Number(item.id);
    // If the right-clicked char is part of a multi-selection, offer bulk delete
    if (
      selectedCharacterIdSet.value.has(numId) &&
      selectedCharacterIdSet.value.size > 1
    ) {
      sidebarCtxDeleteIds.value = Array.from(selectedCharacterIdSet.value);
    } else {
      sidebarCtxDeleteIds.value = [numId];
    }
  } else {
    sidebarCtxDeleteIds.value = [];
    if (type === "set") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = item;
      sidebarCtxFolder.value = null;
      sidebarCtxImportFolder.value = null;
    } else if (type === "folder") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = item;
      sidebarCtxImportFolder.value = null;
    } else if (type === "import-folder") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = null;
      sidebarCtxImportFolder.value = item;
    }
  }
  sidebarCtxX.value = event.clientX;
  sidebarCtxY.value = event.clientY;
  sidebarCtxVisible.value = true;
}

function closeSidebarCtxMenu() {
  sidebarCtxVisible.value = false;
}

function createCharacter() {
  // Find the next available unique name in the format "Character 0001"
  const existingNames = new Set(characters.value.map((c) => c.name));
  let num = 1;
  let name;
  do {
    name = `Character ${num.toString().padStart(4, "0")}`;
    num++;
  } while (existingNames.has(name));
  // Open the editor with default values
  openCharacterEditor({
    id: null,
    name: name,
    description: "",
    extra_metadata: "",
    project_id:
      projectViewMode.value === "project" ? selectedProjectId.value : null,
  });
}

const pendingImportTarget = ref(null);

function getImportedPictureIds(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  return Array.from(
    new Set(
      results
        .map((entry) => entry?.picture_id)
        .filter((id) => id !== null && id !== undefined),
    ),
  );
}

function getRequestErrorDetail(errorLike) {
  return (
    errorLike?.response?.data?.detail ||
    errorLike?.message ||
    String(errorLike || "")
  );
}

function isAlreadyInSetError(errorLike) {
  const detail = String(getRequestErrorDetail(errorLike)).toLowerCase();
  return detail.includes("already in set") || detail.includes("already");
}

async function associateImportedPictures(pictureIds, target) {
  if (!target || !pictureIds.length) return;
  if (target.type === "set") {
    const outcomes = await Promise.allSettled(
      pictureIds.map((id) =>
        apiClient.post(
          `${props.backendUrl}/picture_sets/${target.id}/members/${id}`,
        ),
      ),
    );
    await fetchPictureSets();
    const hardFailures = outcomes.filter(
      (result) =>
        result.status === "rejected" && !isAlreadyInSetError(result.reason),
    );
    if (hardFailures.length) {
      throw new Error(getRequestErrorDetail(hardFailures[0].reason));
    }
    return;
  }
  if (target.type === "character") {
    await apiClient.post(`${props.backendUrl}/characters/${target.id}/faces`, {
      picture_ids: pictureIds,
    });
    await fetchSidebarData();
    await fetchCharacterThumbnail(target.id);
  }
}

async function handleImportFinished(payload) {
  emit("import-finished", payload);
  const target = pendingImportTarget.value;
  if (!target) return;
  pendingImportTarget.value = null;
  const pictureIds = getImportedPictureIds(payload);
  if (!pictureIds.length) return;
  try {
    await associateImportedPictures(pictureIds, target);
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    let targetName = "";
    if (target.type === "character") {
      targetName =
        characters.value.find((c) => c.id === target.id)?.name || "Character";
    } else if (target.type === "set") {
      targetName =
        pictureSets.value.find((s) => s.id === target.id)?.name || "Set";
    }
    const normalizedDetail = String(detail || "").toLowerCase();
    const prefix = normalizedDetail.includes("already")
      ? `Already associated with ${targetName}`
      : `Failed to associate imported pictures with ${targetName}`;
    setError(`${prefix}: ${detail}`, target.id, target.type);
  }
}

function openImportDialog() {
  emit("open-import-dialog");
}

function startLocalImport(files, projectId = null) {
  const list = Array.isArray(files) ? files : [];
  if (!list.length) return;
  const options = projectId != null ? { projectId } : {};
  imageImporterRef.value?.startImport(list, options);
}

function setLoading(isLoading) {
  emit("set-loading", isLoading);
}

function setError(message, targetId = null, targetType = "set") {
  sidebarError.value = message;
  sidebarErrorTargetId.value = targetId;
  sidebarErrorTargetType.value = targetType;
  nextTick(() => updateSidebarErrorPosition());
  emit("set-error", message);
  if (sidebarErrorTimeout) {
    clearTimeout(sidebarErrorTimeout);
    sidebarErrorTimeout = null;
  }
  sidebarErrorTimeout = setTimeout(() => {
    sidebarError.value = null;
    sidebarErrorTargetId.value = null;
    sidebarErrorPosition.value = null;
    sidebarErrorTimeout = null;
  }, 3500);
}

function showNotice(
  message,
  targetId = null,
  targetType = "set",
  duration = 4000,
) {
  if (sidebarNoticeTimeout) {
    clearTimeout(sidebarNoticeTimeout);
    sidebarNoticeTimeout = null;
  }
  sidebarNotice.value = message;
  sidebarNoticeTargetId.value = targetId;
  sidebarNoticeTargetType.value = targetType;
  nextTick(() => updateSidebarNoticePosition());
  sidebarNoticeTimeout = setTimeout(() => {
    sidebarNotice.value = null;
    sidebarNoticeTargetId.value = null;
    sidebarNoticePosition.value = null;
    sidebarNoticeTimeout = null;
  }, duration);
}

function dragOverSetItem(setId) {
  dragOverSet.value = setId;
}

function dragLeaveSetItem() {
  dragOverSet.value = null;
}

function isCountSelected(id) {
  if (!id) return false;
  return props.selectedCharacter === id;
}

const isAllPicturesRowActive = computed(() => {
  if (props.hasFolderFilter) return false;
  if (props.selectedCharacter !== props.allPicturesId) return false;
  if (selectedSetIdSet.value.size > 0) return false;
  return true;
});

const isUnassignedPicturesRowActive = computed(
  () =>
    !props.hasFolderFilter &&
    props.selectedCharacter === props.unassignedPicturesId,
);

const allPicturesRowLabel = computed(() => {
  if (projectViewMode.value === "global") return "All Pictures";
  return "Project Pictures";
});

function isCountNew(id) {
  return Boolean(id && countNewTags.value[id]);
}

function clearCountNew(id) {
  if (!id) return;
  countNewTags.value[id] = false;
}

function markCountNew(id) {
  if (!id) return;
  if (isCountSelected(id)) return;
  countNewTags.value[id] = true;
}

function setCategoryCount(id, value, shouldFlash) {
  if (!id) return;
  const prevValue = categoryCounts.value[id];
  categoryCounts.value[id] = value;
  if (!knownCountIds.has(id)) {
    knownCountIds.add(id);
    return;
  }
  if (shouldFlash && typeof value === "number" && value > prevValue) {
    markCountNew(id);
  }
}

// --- Sidebar & Character Data ---
async function fetchSidebarData() {
  const shouldFlash = flashCountsNextFetch.value;
  // Fetch total image count for END key logic
  try {
    // All images summary
    const resAll = await apiClient.get(
      `${props.backendUrl}/characters/${props.allPicturesId}/summary`,
    );
    const data = await resAll.data;
    setCategoryCount(props.allPicturesId, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching all images summary:", e);
  }
  try {
    // Unassigned images summary
    const unassignedSummaryUrl =
      projectViewMode.value === "project"
        ? `${props.backendUrl}/characters/${props.unassignedPicturesId}/summary?project_id=${selectedProjectId.value != null ? selectedProjectId.value : "UNASSIGNED"}`
        : `${props.backendUrl}/characters/${props.unassignedPicturesId}/summary`;
    const resUnassigned = await apiClient.get(unassignedSummaryUrl);
    const data = await resUnassigned.data;
    setCategoryCount(props.unassignedPicturesId, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching unassigned images summary:", e);
  }
  try {
    const resScrapheap = await apiClient.get(
      `${props.backendUrl}/characters/${props.scrapheapPicturesId}/summary`,
    );
    const data = await resScrapheap.data;
    setCategoryCount(props.scrapheapPicturesId, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching scrapheap images summary:", e);
  }
  await Promise.all(
    characters.value.map(async (char) => {
      try {
        const res = await apiClient.get(
          `${props.backendUrl}/characters/${char.id}/summary`,
        );
        const data = await res.data;
        setCategoryCount(char.id, data.image_count, shouldFlash);
      } catch {}
    }),
  );
  // Fetch counts for each project and the unassigned bucket
  try {
    const countRequests = [
      apiClient
        .get(`${props.backendUrl}/projects/UNASSIGNED/summary`)
        .then((r) => {
          projectCounts.value[UNASSIGNED_PROJECT_KEY] = r.data.image_count;
        }),
      ...projects.value.map((p) =>
        apiClient
          .get(`${props.backendUrl}/projects/${p.id}/summary`)
          .then((r) => {
            projectCounts.value[p.id] = r.data.image_count;
          }),
      ),
    ];
    await Promise.all(countRequests);
  } catch (e) {
    console.warn("Error fetching project counts:", e);
  }
  flashCountsNextFetch.value = false;
}

async function fetchCharacters() {
  setLoading(true);
  setError(null);
  try {
    const res = await apiClient.get(`${props.backendUrl}/characters`);
    const chars = await res.data;
    characters.value = chars;
    for (const char of chars) {
      fetchCharacterThumbnail(char.id);
    }
  } catch (e) {
    setError(e.message);
  } finally {
    setLoading(false);
  }
}

function refreshSidebar(options = {}) {
  if (options?.flashCounts) {
    flashCountsNextFetch.value = true;
  }
  fetchCharacters();
  fetchPictureSets();
  fetchProjects();
  fetchSidebarData();
}

async function fetchCharacterThumbnail(characterId) {
  try {
    const cacheBuster = Date.now();
    const thumbUrl = `/characters/${characterId}/thumbnail?cb=${cacheBuster}`;
    const res = await apiClient.get(thumbUrl, { responseType: "blob" });

    // Create an object URL for the blob
    const blobUrl = URL.createObjectURL(res.data);
    characterThumbnails.value[characterId] = blobUrl;
  } catch (e) {
    console.error(`Failed to fetch thumbnail for character ${characterId}:`, e);
    characterThumbnails.value[characterId] = null;
  }
}

// --- Sorting & Pagination ---
async function fetchSortOptions() {
  try {
    const res = await apiClient.get(`${props.backendUrl}/sort_mechanisms`);

    const options = await res.data;

    // Filter out CHARACTER_LIKENESS if there are no characters
    const filteredOptions = options.filter((opt) => {
      if (opt.key === SIMILARITY_SORT_KEY) {
        return sortedCharacters.value.length > 0; // Only include if characters exist
      }
      return true;
    });

    // Map options to the desired format
    sortOptions.value = filteredOptions.map((opt) => ({
      label: opt.description,
      value: opt.key,
    }));

    // Reset sortModel if it is not in the available options
    if (!sortOptions.value.some((opt) => opt.value === sortModel.value)) {
      sortModel.value = sortOptions.value.length
        ? sortOptions.value[0].value
        : null;
    }
    emit("update:sort-options", sortOptions.value);
  } catch (e) {
    console.error("Error fetching sort options:", e);
    sortOptions.value = [];
    emit("update:sort-options", []);
  }
}

// --- Picture Sets ---
async function fetchProjects() {
  try {
    const res = await apiClient.get(`${props.backendUrl}/projects`);
    projects.value = Array.isArray(res.data) ? res.data : [];
  } catch (e) {
    console.error("Error fetching projects:", e);
    projects.value = [];
  }
}

async function fetchPictureSets() {
  try {
    const setUrl =
      projectViewMode.value === "project"
        ? `${props.backendUrl}/picture_sets?project_id=${selectedProjectId.value != null ? selectedProjectId.value : "UNASSIGNED"}`
        : `${props.backendUrl}/picture_sets`;
    const res = await apiClient.get(setUrl);

    const sets = await res.data; // Axios responses use `data` for the payload
    pictureSets.value = Array.isArray(sets) ? [...sets] : [];
    await updateSetThumbnails(pictureSets.value);
  } catch (e) {
    console.error("Error fetching picture sets:", e);
    pictureSets.value = [...pictureSets.value]; // force reactivity on error
  }
}

async function updateSetThumbnails(sets) {
  const nextMap = {};
  const nextRetryCounts = {};
  for (const set of sets || []) {
    const baseUrl = set?.thumbnail_url || null;
    if (!baseUrl) {
      nextMap[set.id] = null;
      nextRetryCounts[set.id] = 0;
      clearSetThumbnailRetryTimer(set.id);
      continue;
    }
    const topIds = Array.isArray(set?.top_picture_ids)
      ? set.top_picture_ids
      : [];
    const versionKey = topIds.length
      ? topIds.join("-")
      : (set.picture_count ?? 0);
    const url = baseUrl.startsWith("http")
      ? baseUrl
      : `${props.backendUrl}${baseUrl}`;
    const nextUrl = `${url}?v=${encodeURIComponent(versionKey)}`;
    nextMap[set.id] = nextUrl;
    const previousBaseUrl = stripSetThumbnailRetryParams(
      setThumbnails.value?.[set.id] || null,
    );
    if (previousBaseUrl === nextUrl) {
      nextRetryCounts[set.id] =
        Number(setThumbnailRetryCounts.value?.[set.id]) || 0;
    } else {
      nextRetryCounts[set.id] = 0;
      clearSetThumbnailRetryTimer(set.id);
    }
  }
  setThumbnails.value = nextMap;
  setThumbnailRetryCounts.value = nextRetryCounts;
}

function getSetThumbnail(setId) {
  return setThumbnails.value?.[setId] || null;
}

function hasSetThumbnail(pset) {
  if (!pset || !pset.id) return false;
  if (!pset.picture_count) return false;
  return Boolean(getSetThumbnail(pset.id));
}

function stripSetThumbnailRetryParams(url) {
  if (!url || typeof url !== "string") return null;
  return url
    .replace(/[?&]retry=\d+/g, "")
    .replace(/[?&]retry_ts=\d+/g, "")
    .replace(/[?&]{2,}/g, "&")
    .replace(/[?&]$/, "");
}

function clearSetThumbnailRetryTimer(setId) {
  const timer = setThumbnailRetryTimers.get(setId);
  if (!timer) return;
  clearTimeout(timer);
  setThumbnailRetryTimers.delete(setId);
}

function handleSetThumbnailLoad(setId) {
  if (!setId) return;
  clearSetThumbnailRetryTimer(setId);
  if ((setThumbnailRetryCounts.value?.[setId] || 0) === 0) return;
  setThumbnailRetryCounts.value = {
    ...setThumbnailRetryCounts.value,
    [setId]: 0,
  };
}

function handleSetThumbnailError(setId) {
  if (!setId) return;
  const currentUrl = getSetThumbnail(setId);
  if (!currentUrl) {
    setThumbnails.value = { ...setThumbnails.value, [setId]: null };
    return;
  }

  const attempts = Number(setThumbnailRetryCounts.value?.[setId]) || 0;
  if (attempts >= SET_THUMBNAIL_MAX_RETRIES) {
    clearSetThumbnailRetryTimer(setId);
    setThumbnails.value = { ...setThumbnails.value, [setId]: null };
    return;
  }

  const nextAttempt = attempts + 1;
  setThumbnailRetryCounts.value = {
    ...setThumbnailRetryCounts.value,
    [setId]: nextAttempt,
  };

  clearSetThumbnailRetryTimer(setId);
  const retryDelayMs = 120 + nextAttempt * 180;
  const timer = setTimeout(() => {
    // Do not override if this set has been refreshed or cleared meanwhile.
    if (getSetThumbnail(setId) !== currentUrl) {
      setThumbnailRetryTimers.delete(setId);
      return;
    }
    const base = stripSetThumbnailRetryParams(currentUrl);
    const joiner = base && base.includes("?") ? "&" : "?";
    const retriedUrl = `${base}${joiner}retry=${nextAttempt}&retry_ts=${Date.now()}`;
    setThumbnails.value = {
      ...setThumbnails.value,
      [setId]: retriedUrl,
    };
    setThumbnailRetryTimers.delete(setId);
  }, retryDelayMs);
  setThumbnailRetryTimers.set(setId, timer);
}

async function handleDeleteSet() {
  const ids = Array.from(selectedSetIdSet.value);
  if (!ids.length) return;
  await deleteSetsByIds(ids);
}

async function handleDropOnSet(setId, event) {
  dragOverSet.value = null;
  // If this is an internal grid drag (has application/json payload), skip the
  // file-import path — browsers also populate dataTransfer.files for <img> drags.
  const isInternalDrag =
    event?.dataTransfer?.types?.includes("application/json");
  if (
    !isInternalDrag &&
    event?.dataTransfer?.files &&
    event.dataTransfer.files.length > 0
  ) {
    const files = await extractSupportedImportFilesFromDataTransfer(
      event.dataTransfer,
    );
    if (!files.length) return;
    pendingImportTarget.value = { type: "set", id: setId };
    const targetSet = pictureSets.value.find((s) => s.id === setId);
    const options =
      targetSet?.project_id != null ? { projectId: targetSet.project_id } : {};
    imageImporterRef.value?.startImport(files, options);
    return;
  }
  // Get the dragged image IDs from the drag event
  let draggedIds = [];
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (data.imageIds && Array.isArray(data.imageIds)) {
      draggedIds = data.imageIds;
    }
  } catch (e) {
    console.error("Could not parse drag data:", e);
    return;
  }

  if (draggedIds.length === 0) {
    return;
  }

  const targetSet = pictureSets.value.find((s) => s.id === setId);
  if (!targetSet) return;

  try {
    // Add each image to the set
    const addPromises = draggedIds.map(async (picId) => {
      const res = await apiClient.post(
        `${props.backendUrl}/picture_sets/${setId}/members/${picId}`,
      );
    });

    await Promise.all(addPromises);

    // Refresh the picture sets to update counts
    await fetchPictureSets();

    // Emit event to parent to remove images from grid
    emit("images-moved", { imageIds: draggedIds });
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    if (typeof detail === "string" && detail.includes("already in set")) {
      showNotice("Picture already in set", setId);
      return;
    }
    setError("Failed to add images to set: " + detail, setId, "set");
  }
}

function handleDragOverCharacter(id) {
  dragOverCharacter.value = id;
}

function handleDragLeaveCharacter() {
  dragOverCharacter.value = null;
}

async function onCharacterDrop(characterId, event) {
  dragOverCharacter.value = null;
  // If this is an internal grid drag (has application/json payload), skip the
  // file-import path — browsers also populate dataTransfer.files for <img> drags.
  const isInternalDrag =
    event?.dataTransfer?.types?.includes("application/json");
  if (
    !isInternalDrag &&
    event?.dataTransfer?.files &&
    event.dataTransfer.files.length > 0
  ) {
    const files = await extractSupportedImportFilesFromDataTransfer(
      event.dataTransfer,
    );
    if (!files.length) return;
    pendingImportTarget.value = { type: "character", id: characterId };
    const options =
      selectedProjectId.value != null
        ? { projectId: selectedProjectId.value }
        : {};
    imageImporterRef.value?.startImport(files, options);
    return;
  }
  // Accept faceIds or imageIds from drag event
  let faceIds = [];
  let imageIds = [];
  let dragType = null;
  try {
    const rawDataStr = event.dataTransfer.getData("application/json");
    const data = JSON.parse(rawDataStr);
    dragType = data.type || null;
    if (
      dragType === "face-bbox" &&
      data.faceIds &&
      Array.isArray(data.faceIds)
    ) {
      faceIds = data.faceIds;
    }
    if (data.imageIds && Array.isArray(data.imageIds)) {
      imageIds = data.imageIds;
    }
    emit("images-assigned-to-character", { characterId, imageIds });
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    console.error("Error parsing drag data:", detail);
    if (typeof detail === "string") {
      showNotice(detail, characterId, "character");
      return;
    }
    setError(
      "Failed to add images to set: " + detail,
      characterId,
      "character",
    );
    return;
  }

  if (dragType === "face-bbox" && faceIds.length > 0) {
    // Assign faces to character
    try {
      const body = { face_ids: faceIds };
      const res = await apiClient.post(
        `${props.backendUrl}/characters/${characterId}/faces`,
        body,
      );
      await fetchSidebarData();
      await fetchCharacterThumbnail(characterId);
      emit("faces-assigned-to-character", { characterId, faceIds });
    } catch (e) {
      alert("Failed to assign faces to character: " + (e.message || e));
    }
    return;
  }

  if (imageIds.length === 0) {
    return;
  }

  try {
    // Fallback: assign images to character
    const body = { picture_ids: imageIds };
    const res = await apiClient.post(
      `${props.backendUrl}/characters/${characterId}/faces`,
      body,
    );
    await fetchSidebarData();
    await fetchCharacterThumbnail(characterId);
    emit("images-assigned-to-character", { characterId, imageIds });
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    console.error("Error assignning character:", detail);
    if (typeof detail === "string") {
      showNotice(detail, characterId, "character");
      return;
    }
    setError(
      "Failed to add images to set: " + detail,
      characterId,
      "character",
    );
    return;
  }
}

function handleDropOnCharacter(payload) {
  dragOverCharacter.value = null;
  if (!payload || !payload.characterId) return;
  onCharacterDrop(payload.characterId, payload.event);
}

// --- Character Management ---
async function characterSaved() {
  if (characterEditorCharacter.value && !characterEditorCharacter.value.id) {
    characters.value.push(characterEditorCharacter.value);
    // New character was created, increment nextCharacterNumber
    nextCharacterNumber.value++;
  }
  await fetchCharacters(); // Refresh characters
  await fetchSortOptions(); // Ensure sort options include similarity when characters exist
  await fetchPictureSets(); // Refresh picture sets to include reference sets
  closeCharacterEditor();
}

function checkForUpdatesNow() {
  const url = `${LATEST_VERSION_URL}?v=${encodeURIComponent(appVersion)}&i=${encodeURIComponent(props.installType ?? "pip")}`;
  fetch(url)
    .then((r) => r.json())
    .then((data) => {
      const remote = data?.version;
      if (remote && isRemoteNewer(appVersion, remote)) {
        latestVersion.value = remote;
        latestVersionUrl.value = `${UPDATE_PAGE_URL}?v=${encodeURIComponent(appVersion)}&i=${encodeURIComponent(props.installType ?? "pip")}`;
      }
    })
    .catch(() => {});
}

let versionCheckInterval = null;

function startVersionCheckInterval() {
  if (versionCheckInterval) return;
  versionCheckInterval = setInterval(
    () => {
      if (props.checkForUpdates === true) {
        checkForUpdatesNow();
      }
    },
    24 * 60 * 60 * 1000,
  );
}

function stopVersionCheckInterval() {
  if (versionCheckInterval) {
    clearInterval(versionCheckInterval);
    versionCheckInterval = null;
  }
}

watch(
  () => props.checkForUpdates,
  (val) => {
    if (val === true) {
      if (!latestVersion.value) {
        checkForUpdatesNow();
      }
      startVersionCheckInterval();
    } else {
      stopVersionCheckInterval();
    }
  },
);

onMounted(() => {
  // Fetch latest version directly from pixlstash.dev when the user has opted in.
  // Also handled by a watcher above for when the prop resolves after mount.
  if (props.checkForUpdates === true) {
    checkForUpdatesNow();
    startVersionCheckInterval();
  }

  const handleNoticeReflow = () => {
    updateSidebarNoticePosition();
    updateSidebarErrorPosition();
  };
  if (sidebarRootRef.value) {
    sidebarRootRef.value.addEventListener("scroll", handleNoticeReflow, {
      passive: true,
    });
  }
  window.addEventListener("resize", handleNoticeReflow);
  sidebarNoticeCleanup = () => {
    if (sidebarRootRef.value) {
      sidebarRootRef.value.removeEventListener("scroll", handleNoticeReflow);
    }
    window.removeEventListener("resize", handleNoticeReflow);
  };

  const handleProjectMenuOutsideClick = (e) => {
    if (
      (projectMenuRef.value && projectMenuRef.value.contains(e.target)) ||
      (collapsedProjectMenuRef.value &&
        collapsedProjectMenuRef.value.contains(e.target))
    ) {
      return;
    }
    projectMenuOpen.value = false;
    const inCharMenu = e.target.closest(".sidebar-move-menu");
    if (
      !(
        characterMoveMenuBtnRef.value &&
        characterMoveMenuBtnRef.value.contains(e.target)
      ) &&
      !inCharMenu
    ) {
      characterMoveMenuOpen.value = false;
    }
    if (
      !(
        setMoveMenuBtnRef.value && setMoveMenuBtnRef.value.contains(e.target)
      ) &&
      !inCharMenu
    ) {
      setMoveMenuOpen.value = false;
    }
  };
  document.addEventListener("mousedown", handleProjectMenuOutsideClick);
  const _origCleanup = sidebarNoticeCleanup;
  sidebarNoticeCleanup = () => {
    _origCleanup();
    document.removeEventListener("mousedown", handleProjectMenuOutsideClick);
  };
});

function onSidebarCtxOutside(event) {
  if (!sidebarCtxVisible.value) return;
  closeSidebarCtxMenu();
}

function onSidebarCtxKeydown(event) {
  if (!sidebarCtxVisible.value) return;
  if (event.key === "Escape") {
    event.stopImmediatePropagation();
    closeSidebarCtxMenu();
  }
}

document.addEventListener("mousedown", onSidebarCtxOutside);
document.addEventListener("keydown", onSidebarCtxKeydown, true);

let sidebarNoticeCleanup = null;
onBeforeUnmount(() => {
  stopVersionCheckInterval();
  document.removeEventListener("mousedown", onSidebarCtxOutside);
  document.removeEventListener("keydown", onSidebarCtxKeydown, true);
  if (sidebarNoticeCleanup) {
    sidebarNoticeCleanup();
    sidebarNoticeCleanup = null;
  }
  for (const timer of setThumbnailRetryTimers.values()) {
    clearTimeout(timer);
  }
  setThumbnailRetryTimers.clear();
  for (const observer of labelObservers.values()) {
    observer.disconnect();
  }
  labelObservers.clear();
  labelRefs.clear();
});

watch(
  [sortedCharacters, pictureSets],
  () => {
    nextTick(() => refreshLabelOverflows());
  },
  { deep: true },
);

// Ensure similarityCharacter is valid when switching to CHARACTER_LIKENESS
watch(
  () => sortModel.value,
  (newSort) => {
    if (newSort === SIMILARITY_SORT_KEY) {
      // Check if the current similarityCharacter is valid
      if (
        !sortedCharacters.value.some(
          (char) => char.id === similarityCharacterModel.value,
        )
      ) {
        similarityCharacterModel.value =
          sortedCharacters.value.length > 0
            ? sortedCharacters.value[0].id
            : null; // Default to the first character or null
      }
    }
  },
);

watch(
  () => sortedCharacters.value.length,
  () => {
    fetchSortOptions();
  },
  { immediate: true },
);

watch(
  [() => sortedCharacters.value, () => props.selectedSort],
  ([chars, selectedSort]) => {
    const hasCharacters = Array.isArray(chars) && chars.length > 0;
    if (!hasCharacters && selectedSort === SIMILARITY_SORT_KEY) {
      sortModel.value = DATE_SORT_KEY;
      similarityCharacterModel.value = null;
      return;
    }

    if (hasCharacters && selectedSort === SIMILARITY_SORT_KEY) {
      if (!similarityCharacterModel.value) {
        similarityCharacterModel.value = chars[0].id;
      }
    }
  },
  { immediate: true },
);

watch(
  () => props.selectedCharacter,
  (nextId) => {
    clearCountNew(nextId);
  },
);

watch(projectViewMode, (v) => {
  emit("update:project-view-mode", v);
  // Re-fetch sets with the correct scope (all sets in global, scoped sets in
  // project view). Without this, a set removed from a project while in
  // project view could be absent from the global list because the 800 ms
  // debounced sidebar refresh fired the set-fetch while still in project mode
  // (so only project-scoped sets were returned), and the subsequent mode
  // switch only called fetchSidebarData() which doesn't reload pictureSets.
  void fetchPictureSets();
  void fetchSidebarData();
});
watch(selectedProjectId, (v) => {
  emit("update:selected-project-id", v);
  if (v !== null) lastUsedProjectId.value = v;
  // Re-fetch sets for the newly selected project.
  void fetchPictureSets();
  void fetchSidebarData();
});

function switchToProjectView() {
  projectViewMode.value = "project";
  if (selectedProjectId.value === null && sortedProjects.value.length > 0) {
    const restore =
      lastUsedProjectId.value &&
      sortedProjects.value.find((p) => p.id === lastUsedProjectId.value);
    selectedProjectId.value = restore
      ? lastUsedProjectId.value
      : sortedProjects.value[0].id;
  }
}

async function toggleCharacterProjectMembership(charId) {
  const char = characters.value.find((c) => c.id === charId);
  const newProjectId =
    char?.project_id === selectedProjectId.value
      ? null
      : selectedProjectId.value;
  try {
    await apiClient.patch(`${props.backendUrl}/characters/${charId}`, {
      project_id: newProjectId,
    });
    const idx = characters.value.findIndex((c) => c.id === charId);
    if (idx !== -1) {
      characters.value[idx] = {
        ...characters.value[idx],
        project_id: newProjectId,
      };
    }
  } catch (e) {
    console.error("Failed to update character project membership:", e);
  }
}

async function toggleSetProjectMembership(setId) {
  const set = pictureSets.value.find((s) => s.id === setId);
  const newProjectId =
    set?.project_id === selectedProjectId.value
      ? null
      : selectedProjectId.value;
  try {
    await apiClient.patch(`${props.backendUrl}/picture_sets/${setId}`, {
      project_id: newProjectId,
    });
    const idx = pictureSets.value.findIndex((s) => s.id === setId);
    if (idx !== -1) {
      pictureSets.value[idx] = {
        ...pictureSets.value[idx],
        project_id: newProjectId,
      };
    }
  } catch (e) {
    console.error("Failed to update set project membership:", e);
  }
}

async function handleDropOnProjectPictures(event) {
  if (projectViewMode.value !== "project" || selectedProjectId.value === null) {
    return;
  }
  let draggedIds = [];
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (data.imageIds && Array.isArray(data.imageIds)) {
      draggedIds = data.imageIds;
    }
  } catch (e) {
    console.error("Could not parse drag data:", e);
    return;
  }
  if (draggedIds.length === 0) return;
  try {
    await Promise.all(
      draggedIds.map((picId) =>
        apiClient.patch(`${props.backendUrl}/pictures/${picId}`, {
          project_id: selectedProjectId.value,
        }),
      ),
    );
    emit("images-moved", { imageIds: draggedIds });
  } catch (e) {
    console.error("Failed to assign pictures to project:", e);
  }
}

const currentProjectId = computed(() =>
  projectViewMode.value === "project" ? selectedProjectId.value : null,
);

function openCurrentSelectionEditor() {
  if (selectedCharacterObj.value) {
    openCharacterEditor(selectedCharacterObj.value);
  } else if (selectedSetObj.value) {
    openSetEditor(selectedSetObj.value);
  }
}

defineExpose({
  refreshSidebar,
  openSettingsDialog,
  startLocalImport,
  currentProjectId,
  openCurrentSelectionEditor,
});
</script>

<template>
  <ImageImporter
    ref="imageImporterRef"
    :backend-url="props.backendUrl"
    :selected-character-id="props.selectedCharacter"
    :all-pictures-id="props.allPicturesId"
    :unassigned-pictures-id="props.unassignedPicturesId"
    @import-finished="handleImportFinished"
  />
  <CharacterEditor
    :open="characterEditorOpen"
    :character="characterEditorCharacter"
    :backendUrl="props.backendUrl"
    :projects="projects"
    @close="closeCharacterEditor"
    @saved="characterSaved"
  />
  <PictureSetEditor
    :open="setEditorOpen"
    :set="setEditorSet"
    :backendUrl="props.backendUrl"
    :projects="projects"
    @close="closeSetEditor"
    @refresh-sidebar="refreshSidebar"
  />
  <ProjectEditor
    :open="projectEditorOpen"
    :project="projectEditorProject"
    :backend-url="props.backendUrl"
    @close="closeProjectEditor"
    @saved="projectSaved"
    @deleted="projectDeleted"
  />
  <UserSettingsDialog
    v-model:open="settingsDialogOpen"
    v-model:sidebar-thumbnail-size="sidebarThumbnailSizeModel"
    v-model:date-format="dateFormatModel"
    v-model:theme-mode="themeModeModel"
    :checkForUpdates="props.checkForUpdates"
    v-model:show-keyboard-hint="showKeyboardHintModel"
    @update:hidden-tags="(value) => emit('update:hidden-tags', value)"
    @update:apply-tag-filter="(value) => emit('update:apply-tag-filter', value)"
    @update:comfyui-configured="
      (value) => emit('update:comfyui-configured', value)
    "
    @update:check-for-updates="
      (value) => emit('update:check-for-updates', value)
    "
  />
  <ReferenceFolderEditor
    :open="referenceFolderEditorOpen"
    :folder="referenceFolderEditorFolder"
    :in-docker="inDocker"
    :registered-paths="registeredFolderPaths"
    :registered-folders="referenceFolders"
    :image-root="referenceFoldersImageRoot"
    @close="closeReferenceFolderEditor"
    @saved="referenceFolderSaved"
    @deleted="referenceFolderDeleted"
  />
  <ImportFolderEditor
    :open="importFolderEditorOpen"
    :folder="importFolderEditorFolder"
    :in-docker="inDocker"
    :registered-paths="registeredImportFolderPaths"
    :registered-folders="importFolders"
    :registered-reference-folders="referenceFolders"
    :image-root="referenceFoldersImageRoot"
    @close="closeImportFolderEditor"
    @saved="importFolderSaved"
    @deleted="importFolderDeleted"
  />

  <v-dialog v-model="addFolderTypeDialogOpen" max-width="420">
    <v-card class="folder-type-card">
      <v-card-title class="folder-type-title">Add Folder</v-card-title>
      <v-card-text class="folder-type-body">
        <p class="folder-type-subtitle">Choose folder type</p>
        <div class="folder-type-options">
          <button
            class="folder-type-option"
            @click="chooseFolderType('reference')"
          >
            <v-icon size="18">mdi-folder-network-outline</v-icon>
            <span class="folder-type-option-text">
              <strong>Reference folder</strong>
              <small>Browse and filter existing files in place.</small>
            </span>
          </button>
          <button
            class="folder-type-option"
            @click="chooseFolderType('import')"
          >
            <v-icon size="18">mdi-folder-download-outline</v-icon>
            <span class="folder-type-option-text">
              <strong>Import folder</strong>
              <small>Watch for new files and import them automatically.</small>
            </span>
          </button>
        </div>
      </v-card-text>
      <v-card-actions class="folder-type-actions">
        <v-spacer />
        <v-btn variant="text" @click="addFolderTypeDialogOpen = false"
          >Cancel</v-btn
        >
      </v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="taskManagerOpen" width="min(96vw, 1700px)">
    <TaskManager :active="taskManagerOpen" @close="taskManagerOpen = false" />
  </v-dialog>

  <aside
    ref="sidebarRootRef"
    class="sidebar"
    :class="{ 'sidebar-collapsed': props.collapsed }"
    :style="sidebarThumbStyle"
  >
    <div class="sidebar-brand">
      <div class="sidebar-brand-left">
        <a
          v-if="!props.collapsed"
          href="https://pikselkroken.github.io/pixlstash/"
          target="_blank"
          rel="noopener noreferrer"
          class="sidebar-brand-logo-link"
        >
          <img
            src="/Logo.png"
            alt="PixlStash logo"
            class="sidebar-brand-logo"
          />
        </a>
        <div v-if="!props.collapsed" class="sidebar-brand-text">
          <span class="sidebar-brand-title">PixlStash</span>
          <a
            v-if="updateAvailable"
            :href="latestVersionUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="sidebar-update-available"
            >&#x2191; v{{ latestVersion }} available</a
          >
        </div>
      </div>
      <v-btn
        icon
        class="sidebar-brand-toggle"
        :title="props.collapsed ? 'Show sidebar' : 'Hide sidebar'"
        @click.stop="emit('toggle-sidebar')"
      >
        <v-icon>mdi-dock-left</v-icon>
      </v-btn>
    </div>
    <div
      v-if="props.collapsed"
      class="sidebar-collapsed-project-wrap"
      ref="projectMenuRef"
    >
      <div
        :class="[
          'sidebar-collapsed-item',
          {
            active: projectViewMode === 'project' && selectedProjectId !== null,
          },
        ]"
        style="margin: 0 auto"
        :title="
          projectViewMode === 'global'
            ? 'Global (all projects)'
            : selectedProjectId === null
              ? 'No project'
              : (selectedProjectObj?.name ?? 'Project')
        "
        ref="collapsedProjectBtnRef"
        @click.stop="toggleProjectMenu"
      >
        <v-icon size="20">{{
          projectViewMode === "global" ? "mdi-earth" : "mdi-folder-outline"
        }}</v-icon>
      </div>
      <Teleport to="body">
        <div
          v-if="projectMenuOpen && props.collapsed"
          ref="collapsedProjectMenuRef"
          class="sidebar-collapsed-project-menu"
          :style="{
            top: collapsedProjectMenuPos.top + 'px',
            left: collapsedProjectMenuPos.left + 'px',
          }"
        >
          <div
            class="sidebar-project-menu-item"
            :class="{ active: projectViewMode === 'global' }"
            @click="
              projectViewMode = 'global';
              projectMenuOpen = false;
            "
          >
            <v-icon size="14">mdi-earth</v-icon>
            <span class="sidebar-project-menu-item-label">Global</span>
          </div>
          <div
            v-for="p in sortedProjects"
            :key="p.id"
            class="sidebar-project-menu-item"
            :class="{
              active:
                projectViewMode === 'project' && selectedProjectId === p.id,
            }"
            @click="
              projectViewMode = 'project';
              selectProject(p.id);
            "
          >
            <v-icon size="14">mdi-folder</v-icon>
            <span class="sidebar-project-menu-item-label">{{ p.name }}</span>
          </div>
          <div
            class="sidebar-project-menu-add"
            @click="
              createProject;
              projectMenuOpen = false;
            "
          >
            <v-icon size="14">mdi-plus</v-icon>
            Add new project
          </div>
        </div>
      </Teleport>
    </div>
    <div v-else class="sidebar-view-tabs-row">
      <div class="sidebar-view-tabs">
        <button
          class="sidebar-view-tab"
          :class="{
            active:
              sidebarPrimaryTab === 'library' && projectViewMode === 'global',
          }"
          @click="selectLibraryTab('global')"
        >
          <v-icon size="14">mdi-earth</v-icon>
          Global
        </button>
        <button
          class="sidebar-view-tab"
          :class="{
            active:
              sidebarPrimaryTab === 'library' && projectViewMode === 'project',
          }"
          @click="selectLibraryTab('project')"
        >
          <v-icon size="14">mdi-folder-outline</v-icon>
          Projects
        </button>
        <button
          class="sidebar-view-tab"
          :class="{ active: sidebarPrimaryTab === 'folders' }"
          @click="selectFoldersTab()"
        >
          <v-icon size="14">mdi-folder-network-outline</v-icon>
          Folders
        </button>
      </div>
    </div>
    <div class="sidebar-scroll">
      <template v-if="props.collapsed">
        <div class="sidebar-collapsed-list">
          <div
            :class="[
              'sidebar-collapsed-item',
              {
                active: isAllPicturesRowActive,
              },
            ]"
            title="All Pictures"
            @click="selectCharacter(props.allPicturesId, 'All Pictures')"
          >
            <v-icon>mdi-image-multiple</v-icon>
          </div>
          <div
            :class="[
              'sidebar-collapsed-item',
              {
                active: isUnassignedPicturesRowActive,
              },
            ]"
            title="Unassigned Pictures"
            @click="
              selectCharacter(props.unassignedPicturesId, 'Unassigned Pictures')
            "
          >
            <v-icon>mdi-account-off-outline</v-icon>
          </div>
          <div class="sidebar-collapsed-divider"></div>
          <button
            v-for="char in visibleCharacters"
            :key="char.id"
            :class="[
              'sidebar-collapsed-thumb',
              {
                active:
                  props.selectedCharacter === char.id && !props.hasFolderFilter,
                droppable: dragOverCharacter === char.id,
              },
            ]"
            :ref="(el) => registerCharacterRef(char.id, el)"
            :title="char.name || 'Character'"
            @click="selectCharacter(char.id, char.name || 'Character')"
            @dragover.prevent="handleDragOverCharacter(char.id)"
            @dragleave="handleDragLeaveCharacter"
            @drop.prevent="
              handleDropOnCharacter({ characterId: char.id, event: $event })
            "
          >
            <img
              :src="characterThumbnails[char.id] || unknownPerson"
              alt=""
              :width="sidebarThumbnailSizeModel"
              :height="sidebarThumbnailSizeModel"
              class="sidebar-character-thumb"
            />
          </button>
          <div
            v-if="visibleSets.length"
            class="sidebar-collapsed-divider"
          ></div>
          <div
            v-for="pset in visibleSets"
            :key="pset.id"
            :class="[
              'sidebar-collapsed-item',
              {
                active: selectedSetIdSet.has(pset.id) && !props.hasFolderFilter,
                droppable: dragOverSet === pset.id,
              },
            ]"
            :title="`${pset.name || 'Picture Set'} (Ctrl/Cmd + click to multi-select)`"
            @click="selectSet(pset.id, pset.name || 'Picture Set', $event)"
            @dragover.prevent="dragOverSetItem(pset.id)"
            @dragleave="dragLeaveSetItem"
            @drop.prevent="handleDropOnSet(pset.id, $event)"
          >
            <img
              v-if="hasSetThumbnail(pset)"
              :src="getSetThumbnail(pset.id)"
              alt=""
              class="sidebar-set-thumb-image sidebar-set-thumb-image--collapsed"
              :width="sidebarThumbnailSizeModel"
              :height="sidebarThumbnailSizeModel"
              @load="handleSetThumbnailLoad(pset.id)"
              @error="handleSetThumbnailError(pset.id)"
            />
            <v-icon width="40" size="40" v-else>mdi-image-album</v-icon>
          </div>
        </div>
      </template>
      <template v-else>
        <!-- Folders tab panel -->
        <div v-if="sidebarPrimaryTab === 'folders'" class="sidebar-tab-panel">
          <!-- Compact toolbar row -->
          <div class="sidebar-folders-tab-toolbar">
            <span class="sidebar-folders-tab-toolbar-label">Folders</span>
            <span class="sidebar-header-spacer"></span>
            <v-icon
              class="add-character-inline"
              title="Add folder"
              @click.stop="openAddFolderTypeDialog()"
            >
              mdi-plus
            </v-icon>
          </div>

          <div
            v-if="referenceFoldersLoading || importFoldersLoading"
            class="sidebar-folders-loading"
          >
            <v-progress-circular indeterminate size="24" />
          </div>
          <div
            v-else-if="
              referenceFolders.length === 0 && importFolders.length === 0
            "
            class="sidebar-no-projects-empty"
          >
            <v-icon size="52" class="sidebar-no-projects-icon"
              >mdi-folder-network-outline</v-icon
            >
            <p class="sidebar-no-projects-text">No folders configured.</p>
            <v-btn
              color="primary"
              size="small"
              prepend-icon="mdi-plus"
              rounded="lg"
              class="sidebar-no-projects-btn sidebar-no-projects-btn--folders"
              @click="openAddFolderTypeDialog()"
            >
              Add folder
            </v-btn>
          </div>
          <div v-else class="sidebar-folders-list">
            <div
              v-if="referenceFolders.length"
              class="sidebar-folder-section-header"
            >
              <div class="sidebar-folder-section-title">Reference folders</div>
              <v-icon
                v-if="selectedReferenceFolderForHeader"
                size="13"
                class="sidebar-folder-section-edit-btn"
                title="Edit selected reference folder"
                @click.stop="
                  openReferenceFolderEditor(selectedReferenceFolderForHeader)
                "
              >
                mdi-pencil-outline
              </v-icon>
            </div>
            <div
              v-for="rf in referenceFolders"
              :key="rf.id"
              class="sidebar-folder-root"
            >
              <div
                class="sidebar-folder-row sidebar-folder-root-row"
                :class="{ active: selectedFolderKey === 'rf-' + rf.id }"
                :title="rf.folder"
                @contextmenu.prevent="openSidebarCtxMenu('folder', rf, $event)"
                @click="
                  if (!inDocker) {
                    if (!expandedFolderIds.has(rf.id))
                      toggleFolderExpanded(rf.id);
                    browseFolderPath(rf.folder, true);
                  }
                  handleFolderNodeSelect('rf-' + rf.id, {
                    referenceFolderId: rf.id,
                    pathPrefix: rf.folder,
                    label: rf.label || rf.folder,
                  });
                "
              >
                <v-icon
                  size="16"
                  class="sidebar-folder-chevron"
                  :style="{
                    visibility:
                      !inDocker &&
                      (!folderBrowseCache[rf.folder] ||
                        folderBrowseCache[rf.folder].loading ||
                        (folderBrowseCache[rf.folder]?.entries?.length ?? 0) >
                          0)
                        ? 'visible'
                        : 'hidden',
                  }"
                  @click.stop="
                    if (!inDocker) {
                      toggleFolderExpanded(rf.id);
                      browseFolderPath(rf.folder, true);
                    }
                  "
                >
                  {{
                    expandedFolderIds.has(rf.id)
                      ? "mdi-chevron-down"
                      : "mdi-chevron-right"
                  }}
                </v-icon>
                <v-icon size="16" class="sidebar-folder-icon"
                  >mdi-folder-network-outline</v-icon
                >
                <span class="sidebar-folder-label">{{
                  rf.label || rf.folder
                }}</span>
                <span
                  v-if="rf.status === 'mount_error'"
                  class="sidebar-folder-status-badge sidebar-folder-status--mount_error"
                  :title="
                    inDocker
                      ? 'Mount error — check Docker volume'
                      : 'Folder not accessible'
                  "
                >
                  <v-icon size="12">mdi-alert-circle-outline</v-icon>
                </span>
                <span
                  v-else-if="rf.status === 'pending_mount'"
                  class="sidebar-folder-status-badge sidebar-folder-status--pending_mount"
                  :title="
                    inDocker
                      ? 'Pending restart — restart server to mount'
                      : 'Scan pending — will start automatically'
                  "
                >
                  <v-icon size="12">mdi-clock-outline</v-icon>
                </span>
                <span
                  v-else-if="rf.status === 'active' && rf.last_scanned == null"
                  class="sidebar-folder-status-badge sidebar-folder-status--scanning"
                  title="Scanning…"
                >
                  <v-progress-circular indeterminate size="10" width="1.5" />
                </span>
                <span
                  v-else-if="
                    folderBrowseCache[rf.folder]?.loading ||
                    (folderBrowseCache[rf.folder]?.image_count ?? 0) > 0
                  "
                  class="sidebar-folder-count-badge"
                  title="Direct images in folder"
                >
                  {{
                    folderBrowseCache[rf.folder]?.loading
                      ? "..."
                      : (folderBrowseCache[rf.folder]?.image_count ?? 0)
                  }}
                </span>
              </div>
              <div
                v-if="!inDocker && expandedFolderIds.has(rf.id)"
                class="sidebar-folder-children"
              >
                <div
                  v-if="folderBrowseCache[rf.folder]?.loading"
                  class="sidebar-folder-loading-row"
                >
                  <v-progress-circular indeterminate size="14" />
                </div>
                <template v-else>
                  <template
                    v-for="entry in folderBrowseCache[rf.folder]?.entries ?? []"
                    :key="entry.path"
                  >
                    <FolderTreeNode
                      :entry="entry"
                      :rf-id="rf.id"
                      :depth="1"
                      :selected-folder-key="selectedFolderKey"
                      :folder-browse-cache="folderBrowseCache"
                      :expanded-folder-ids="expandedFolderIds"
                      @select="handleFolderNodeSelect"
                      @toggle="handleFolderNodeToggle"
                    />
                  </template>
                  <div
                    v-if="folderBrowseCache[rf.folder]?.error"
                    class="sidebar-folder-empty-row sidebar-folder-error-row"
                  >
                    <v-icon size="13">mdi-alert-circle-outline</v-icon> Cannot
                    browse (Docker mode or permission error)
                  </div>
                </template>
              </div>
            </div>

            <div
              v-if="importFolders.length"
              class="sidebar-folder-section-header"
            >
              <div class="sidebar-folder-section-title">Import folders</div>
              <v-icon
                v-if="selectedImportFolderForHeader"
                size="13"
                class="sidebar-folder-section-edit-btn"
                title="Edit selected import folder"
                @click.stop="
                  openImportFolderEditor(selectedImportFolderForHeader)
                "
              >
                mdi-pencil-outline
              </v-icon>
            </div>
            <div
              v-for="importFolder in importFolders"
              :key="importFolder.id"
              class="sidebar-folder-root"
            >
              <div
                class="sidebar-folder-row sidebar-folder-root-row"
                :class="{
                  active: selectedFolderKey === 'if-' + importFolder.id,
                }"
                :title="importFolder.folder"
                @contextmenu.prevent="
                  openSidebarCtxMenu('import-folder', importFolder, $event)
                "
                @click="
                  handleFolderNodeSelect('if-' + importFolder.id, {
                    importSourceFolder: importFolder.folder,
                    label: importFolder.label || importFolder.folder,
                  })
                "
              >
                <v-icon
                  size="16"
                  class="sidebar-folder-chevron"
                  style="visibility: hidden"
                >
                  mdi-chevron-right
                </v-icon>
                <v-icon size="16" class="sidebar-folder-icon"
                  >mdi-folder-download-outline</v-icon
                >
                <span class="sidebar-folder-label">
                  {{ importFolder.label || importFolder.folder }}
                </span>
                <span
                  v-if="importFolder.delete_after_import"
                  class="sidebar-folder-status-badge sidebar-folder-status--pending_mount"
                  title="Delete source file after successful import"
                >
                  <v-icon size="12">mdi-delete-outline</v-icon>
                </span>
                <span
                  class="sidebar-folder-count-badge"
                  title="Imported pictures from folder"
                >
                  {{ importFolder.picture_count ?? 0 }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- Library tab panel (Global / Projects) -->
        <div v-else class="sidebar-tab-panel">
          <div
            v-if="projectViewMode === 'project' && projects.length === 0"
            class="sidebar-no-projects-empty"
          >
            <v-icon size="52" class="sidebar-no-projects-icon"
              >mdi-folder-plus-outline</v-icon
            >
            <p class="sidebar-no-projects-text">
              Create a project to organise your library into separate
              collections.
            </p>
            <v-btn
              color="primary"
              size="small"
              prepend-icon="mdi-plus"
              rounded="lg"
              class="sidebar-no-projects-btn"
              @click="createProject"
            >
              Create new project
            </v-btn>
          </div>
          <template v-if="projectViewMode !== 'project' || projects.length > 0">
            <div
              v-if="projectViewMode === 'project'"
              class="sidebar-project-menu-wrap"
              ref="projectMenuRef"
            >
              <button
                class="sidebar-project-trigger"
                @click.stop="toggleProjectMenu"
              >
                <v-icon size="14">mdi-folder-multiple-outline</v-icon>
                <span class="sidebar-project-trigger-label">
                  {{ selectedProjectObj?.name ?? "—" }}
                </span>
                <v-icon size="14" class="sidebar-project-trigger-chevron">
                  {{ projectMenuOpen ? "mdi-chevron-up" : "mdi-chevron-down" }}
                </v-icon>
              </button>
              <div v-if="projectMenuOpen" class="sidebar-project-menu">
                <div
                  v-for="p in sortedProjects"
                  :key="p.id"
                  class="sidebar-project-menu-item"
                  :class="{ active: selectedProjectId === p.id }"
                  @click="selectProject(p.id)"
                >
                  <span class="sidebar-project-menu-item-label">{{
                    p.name
                  }}</span>
                  <v-icon
                    size="14"
                    class="sidebar-project-menu-item-action"
                    @click.stop="exportProject(p)"
                    title="Export project as ZIP"
                    >mdi-download-outline</v-icon
                  >
                  <v-icon
                    size="14"
                    class="sidebar-project-menu-item-action"
                    @click.stop="openProjectEditor(p)"
                    title="Edit project"
                    >mdi-pencil</v-icon
                  >
                </div>
                <div class="sidebar-project-menu-add" @click="createProject">
                  <v-icon size="14">mdi-plus</v-icon>
                  Add new project
                </div>
              </div>
            </div>
            <div
              v-if="projectViewMode === 'global'"
              class="sidebar-section-divider"
            ></div>
            <div
              class="sidebar-all-pictures-row"
              :class="{
                'drag-over-project':
                  projectViewMode === 'project' &&
                  selectedProjectId !== null &&
                  dragOverProjectPictures,
              }"
              @dragover.prevent="
                projectViewMode === 'project' && selectedProjectId !== null
                  ? (dragOverProjectPictures = true)
                  : null
              "
              @dragleave="dragOverProjectPictures = false"
              @drop.prevent="
                projectViewMode === 'project' && selectedProjectId !== null
                  ? (dragOverProjectPictures = false) ||
                    handleDropOnProjectPictures($event)
                  : null
              "
            >
              <div
                :class="[
                  'sidebar-list-item',
                  { active: isAllPicturesRowActive },
                ]"
                @click="
                  selectCharacter(props.allPicturesId, allPicturesRowLabel)
                "
              >
                <span class="sidebar-list-icon">
                  <v-icon size="44">mdi-image-multiple</v-icon>
                </span>
                <span class="sidebar-list-label">{{
                  allPicturesRowLabel
                }}</span>
                <span class="sidebar-list-count">{{
                  projectViewMode === "global"
                    ? (categoryCounts[props.allPicturesId] ?? "")
                    : (projectCounts[
                        selectedProjectId ?? UNASSIGNED_PROJECT_KEY
                      ] ?? "")
                }}</span>
              </div>
            </div>

            <div>
              <div
                :class="[
                  'sidebar-list-item',
                  { active: isUnassignedPicturesRowActive },
                ]"
                @click="
                  selectCharacter(
                    props.unassignedPicturesId,
                    'Unassigned Pictures',
                  )
                "
              >
                <span class="sidebar-list-icon">
                  <v-icon size="44">mdi-account-off-outline</v-icon>
                </span>
                <span class="sidebar-list-label">Unassigned Pictures</span>
                <span class="sidebar-list-count">{{
                  categoryCounts[props.unassignedPicturesId] ?? ""
                }}</span>
              </div>
            </div>

            <div
              class="sidebar-section-block"
              :style="
                peopleSectionCollapsed
                  ? {}
                  : {
                      flex: `${Math.max(1, visibleCharacters.length)} 1 0`,
                      minHeight:
                        visibleCharacters.length === 0 ? '70px' : undefined,
                    }
              "
            >
              <div
                class="sidebar-section-header sidebar-section-header--collapsible"
                @click.stop="peopleSectionCollapsed = !peopleSectionCollapsed"
              >
                <v-icon class="sidebar-section-chevron" size="16">{{
                  peopleSectionCollapsed
                    ? "mdi-chevron-right"
                    : "mdi-chevron-down"
                }}</v-icon>
                People
                <span class="sidebar-header-spacer"></span>
                <div class="sidebar-header-actions" @click.stop>
                  <button
                    v-if="selectedCharacterIdSet.size > 1"
                    class="clear-selection-inline"
                    @click.stop="
                      selectCharacter(props.allPicturesId, 'All Pictures')
                    "
                    title="Clear character selection"
                  >
                    <v-icon size="16">mdi-selection-off</v-icon>
                  </button>
                  <v-icon
                    v-if="selectedCharacterObj && hasSingleSelectedCharacter"
                    class="edit-character-inline"
                    @click.stop="openCharacterEditor(selectedCharacterObj)"
                    title="Edit selected character"
                  >
                    mdi-pencil
                  </v-icon>
                  <v-icon
                    v-if="
                      props.selectedCharacter &&
                      props.selectedCharacter !== props.allPicturesId &&
                      props.selectedCharacter !== props.unassignedPicturesId &&
                      props.selectedCharacter !== props.scrapheapPicturesId
                    "
                    class="delete-character-inline"
                    color="white"
                    @click.stop="deleteCharacter"
                    title="Delete selected character"
                  >
                    mdi-trash-can-outline
                  </v-icon>
                  <span
                    v-if="
                      projectViewMode === 'project' &&
                      selectedProjectId !== null
                    "
                    ref="characterMoveMenuBtnRef"
                    class="sidebar-move-to-project-wrap"
                    @click.stop
                  >
                    <v-icon
                      class="add-character-inline"
                      @click.stop="openCharacterMoveMenu()"
                      title="Add or remove people from this project"
                    >
                      mdi-plus
                    </v-icon>
                    <Teleport to="body">
                      <div
                        v-if="characterMoveMenuOpen"
                        class="sidebar-move-menu"
                        :style="{
                          top: characterMenuPos.top + 'px',
                          left: characterMenuPos.left + 'px',
                        }"
                      >
                        <div
                          class="sidebar-move-menu-item sidebar-move-menu-item--create"
                          @click.stop="
                            createCharacter();
                            characterMoveMenuOpen = false;
                          "
                        >
                          <v-icon size="16" class="sidebar-move-menu-check"
                            >mdi-plus-circle-outline</v-icon
                          >
                          Create new
                        </div>
                        <template
                          v-for="group in projectMenuCharacterGroups"
                          :key="group.label"
                        >
                          <div
                            class="sidebar-move-menu-group-header"
                            :class="{
                              'sidebar-move-menu-group-header--current':
                                group.projectId === selectedProjectId,
                            }"
                          >
                            {{ group.label }}
                          </div>
                          <div
                            v-for="char in group.items"
                            :key="char.id"
                            class="sidebar-move-menu-item"
                            :class="{
                              'sidebar-move-menu-item--checked':
                                char.project_id === selectedProjectId,
                            }"
                            @click.stop="
                              toggleCharacterProjectMembership(char.id)
                            "
                          >
                            <v-icon size="16" class="sidebar-move-menu-check">{{
                              char.project_id === selectedProjectId
                                ? "mdi-checkbox-marked"
                                : "mdi-checkbox-blank-outline"
                            }}</v-icon>
                            {{ char.name }}
                          </div>
                        </template>
                      </div>
                    </Teleport>
                  </span>
                  <v-icon
                    v-if="projectViewMode !== 'project'"
                    class="add-character-inline"
                    @click.stop="createCharacter"
                    title="Add character"
                  >
                    mdi-plus
                  </v-icon>
                </div>
              </div>
              <div
                v-if="!peopleSectionCollapsed"
                class="sidebar-section-scroll"
              >
                <div
                  v-if="sidebarError"
                  class="sidebar-error-bubble"
                  :style="
                    sidebarErrorPosition
                      ? {
                          top: `${sidebarErrorPosition.top}px`,
                          left: `${sidebarErrorPosition.left}px`,
                        }
                      : { top: '72px', left: '20px' }
                  "
                >
                  {{ sidebarError }}
                </div>
                <div
                  v-if="visibleCharacters.length === 0"
                  class="sidebar-collections-help-row"
                >
                  <span class="sidebar-collections-help">
                    Click the + button to add one.
                  </span>
                </div>
                <div
                  v-if="visibleCharacters.length > 0"
                  v-for="char in visibleCharacters"
                  :key="char.id"
                  class="sidebar-character-group"
                >
                  <div
                    :class="[
                      'sidebar-list-item',
                      {
                        active:
                          (selectedCharacterIdSet.size > 0
                            ? selectedCharacterIdSet.has(char.id)
                            : selectedCharacter === char.id) &&
                          !props.hasFolderFilter,
                        droppable: dragOverCharacter === char.id,
                      },
                    ]"
                    :ref="(el) => registerCharacterRef(char.id, el)"
                    :title="`${char.name || 'Character'} (Ctrl/Cmd + click to multi-select)`"
                    @click="
                      selectCharacter(char.id, char.name || 'Character', $event)
                    "
                    @contextmenu.prevent="
                      openSidebarCtxMenu('character', char, $event)
                    "
                    @dragover.prevent="handleDragOverCharacter(char.id)"
                    @dragleave="handleDragLeaveCharacter"
                    @drop.prevent="
                      handleDropOnCharacter({
                        characterId: char.id,
                        event: $event,
                      })
                    "
                  >
                    <span class="sidebar-list-icon">
                      <img
                        :src="
                          characterThumbnails[char.id]
                            ? characterThumbnails[char.id]
                            : unknownPerson
                        "
                        alt=""
                        :width="sidebarThumbnailSizeModel"
                        :height="sidebarThumbnailSizeModel"
                        class="sidebar-character-thumb"
                      />
                    </span>
                    <span class="sidebar-list-label">
                      <v-tooltip
                        location="top"
                        :disabled="!labelNeedsTooltip(`char-${char.id}`)"
                      >
                        <template #activator="{ props }">
                          <span
                            v-bind="props"
                            :ref="mergeTooltipRef(props, `char-${char.id}`)"
                            class="sidebar-list-label-text"
                          >
                            {{
                              char.name.charAt(0).toUpperCase() +
                              char.name.slice(1)
                            }}
                          </span>
                        </template>
                        <span>{{ char.name }}</span>
                      </v-tooltip>
                    </span>
                    <span class="sidebar-character-actions">
                      <span class="sidebar-list-count">
                        <span
                          v-if="isCountNew(char.id)"
                          class="sidebar-new-tag"
                        >
                          new
                        </span>
                        <span>
                          {{ categoryCounts[char.id] ?? "" }}
                        </span>
                      </span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <div
              class="sidebar-section-block"
              :style="
                setsSectionCollapsed
                  ? {}
                  : { flex: `${Math.max(1, visibleSets.length)} 1 0` }
              "
            >
              <div
                class="sidebar-section-header sidebar-section-header--collapsible"
                @click.stop="setsSectionCollapsed = !setsSectionCollapsed"
              >
                <v-icon class="sidebar-section-chevron" size="16">{{
                  setsSectionCollapsed
                    ? "mdi-chevron-right"
                    : "mdi-chevron-down"
                }}</v-icon>
                Sets
                <span class="sidebar-header-spacer"></span>
                <div class="sidebar-header-actions">
                  <button
                    v-if="selectedSetIdSet.size > 1"
                    class="clear-selection-inline"
                    @click.stop="emit('select-set', null)"
                    title="Clear set selection"
                  >
                    <v-icon size="16">mdi-selection-off</v-icon>
                  </button>
                  <v-icon
                    v-if="selectedSetObj && hasSingleSelectedSet"
                    class="edit-set-inline"
                    @click.stop="openSetEditor(selectedSetObj)"
                    title="Edit selected set"
                  >
                    mdi-pencil
                  </v-icon>
                  <v-icon
                    v-if="selectedSetIdSet.size > 0"
                    class="delete-character-inline"
                    color="white"
                    @click.stop="handleDeleteSet"
                    :title="
                      selectedSetIdSet.size > 1
                        ? `Delete ${selectedSetIdSet.size} selected sets`
                        : 'Delete selected set'
                    "
                  >
                    mdi-trash-can-outline
                  </v-icon>
                  <span
                    v-if="
                      projectViewMode === 'project' &&
                      selectedProjectId !== null
                    "
                    ref="setMoveMenuBtnRef"
                    class="sidebar-move-to-project-wrap"
                    @click.stop
                  >
                    <v-icon
                      class="add-character-inline"
                      @click.stop="openSetMoveMenu()"
                      title="Add or remove sets from this project"
                    >
                      mdi-plus
                    </v-icon>
                    <Teleport to="body">
                      <div
                        v-if="setMoveMenuOpen"
                        class="sidebar-move-menu"
                        :style="{
                          top: setMenuPos.top + 'px',
                          left: setMenuPos.left + 'px',
                        }"
                      >
                        <div
                          class="sidebar-move-menu-item sidebar-move-menu-item--create"
                          @click.stop="
                            createSet();
                            setMoveMenuOpen = false;
                          "
                        >
                          <v-icon size="16" class="sidebar-move-menu-check"
                            >mdi-plus-circle-outline</v-icon
                          >
                          Create new
                        </div>
                        <template
                          v-for="group in projectMenuSetGroups"
                          :key="group.label"
                        >
                          <div
                            class="sidebar-move-menu-group-header"
                            :class="{
                              'sidebar-move-menu-group-header--current':
                                group.projectId === selectedProjectId,
                            }"
                          >
                            {{ group.label }}
                          </div>
                          <div
                            v-for="pset in group.items"
                            :key="pset.id"
                            class="sidebar-move-menu-item"
                            :class="{
                              'sidebar-move-menu-item--checked':
                                pset.project_id === selectedProjectId,
                            }"
                            @click.stop="toggleSetProjectMembership(pset.id)"
                          >
                            <v-icon size="16" class="sidebar-move-menu-check">{{
                              pset.project_id === selectedProjectId
                                ? "mdi-checkbox-marked"
                                : "mdi-checkbox-blank-outline"
                            }}</v-icon>
                            {{ pset.name }}
                          </div>
                        </template>
                      </div>
                    </Teleport>
                  </span>
                  <v-icon
                    v-if="projectViewMode !== 'project'"
                    class="add-character-inline"
                    @click.stop="createSet"
                    title="Create new set"
                  >
                    mdi-plus
                  </v-icon>
                </div>
              </div>
              <div v-if="!setsSectionCollapsed" class="sidebar-section-scroll">
                <div
                  v-if="visibleSets.length === 0"
                  class="sidebar-collections-help-row"
                >
                  <span class="sidebar-collections-help">
                    Click the + button to add one.
                  </span>
                </div>
                <template v-for="(pset, idx) in visibleSets" :key="pset.id">
                  <div
                    :class="[
                      'sidebar-list-item',
                      'sidebar-set-item',
                      {
                        active:
                          selectedSetIdSet.has(pset.id) &&
                          !props.hasFolderFilter,
                        droppable: dragOverSet === pset.id,
                      },
                    ]"
                    :ref="(el) => registerSetRef(pset.id, el)"
                    :title="`${pset.name || 'Picture Set'} (Ctrl/Cmd + click to multi-select)`"
                    @click="
                      selectSet(pset.id, pset.name || 'Picture Set', $event)
                    "
                    @contextmenu.prevent="
                      openSidebarCtxMenu('set', pset, $event)
                    "
                    @dragover.prevent="dragOverSetItem(pset.id)"
                    @dragleave="dragLeaveSetItem"
                    @drop.prevent="handleDropOnSet(pset.id, $event)"
                  >
                    <span class="sidebar-list-icon">
                      <img
                        v-if="hasSetThumbnail(pset)"
                        :src="getSetThumbnail(pset.id)"
                        alt=""
                        class="sidebar-set-thumb-image sidebar-set-thumb-image--large"
                        :width="sidebarThumbnailSizeLarge"
                        :height="sidebarThumbnailSizeLarge"
                        @load="handleSetThumbnailLoad(pset.id)"
                        @error="handleSetThumbnailError(pset.id)"
                      />
                      <v-icon v-else size="44">mdi-image-album</v-icon>
                    </span>
                    <span class="sidebar-list-label">
                      <v-tooltip
                        location="top"
                        :disabled="!labelNeedsTooltip(`set-${pset.id}`)"
                      >
                        <template #activator="{ props }">
                          <span
                            v-bind="props"
                            :ref="mergeTooltipRef(props, `set-${pset.id}`)"
                            class="sidebar-list-label-text"
                          >
                            {{ pset.name }}
                          </span>
                        </template>
                        <span>{{ pset.name }}</span>
                      </v-tooltip>
                    </span>
                    <span class="sidebar-list-count">
                      {{ pset.picture_count ?? 0 }}
                    </span>
                  </div>
                </template>
              </div>
            </div>
            <div
              v-if="projectViewMode === 'project' && selectedProjectId !== null"
              class="sidebar-section-block"
            >
              <ProjectFiles
                :projectId="selectedProjectId"
                :backendUrl="props.backendUrl"
              />
            </div>
          </template>
        </div>
        <!-- end Library tab panel -->
      </template>
    </div>
    <!-- end sidebar-scroll -->
    <div class="sidebar-sticky-footer">
      <div
        class="sidebar-footer-btn sidebar-footer-btn--settings"
        title="Settings"
        @click.stop="openSettingsDialog"
      >
        <v-icon size="20">mdi-cog-outline</v-icon>
        <span class="sidebar-footer-btn-label">Settings</span>
      </div>
      <div
        class="sidebar-footer-btn sidebar-footer-btn--upload"
        title="Import photos"
        @click.stop="openImportDialog"
      >
        <v-icon size="20">mdi-cloud-upload-outline</v-icon>
        <span class="sidebar-footer-btn-label">Import</span>
      </div>
      <div
        class="sidebar-footer-btn sidebar-footer-btn--tasks"
        title="Task Manager"
        @click.stop="taskManagerOpen = true"
      >
        <v-icon size="20">mdi-timeline-clock-outline</v-icon>
        <span class="sidebar-footer-btn-label">Tasks</span>
      </div>
      <div
        :class="[
          'sidebar-footer-btn',
          'sidebar-footer-btn--scrapheap',
          {
            active:
              props.selectedCharacter === props.scrapheapPicturesId &&
              !props.hasFolderFilter,
          },
        ]"
        title="Scrapheap"
        @click.stop="selectCharacter(props.scrapheapPicturesId, 'Scrapheap')"
      >
        <v-icon size="20">mdi-trash-can-outline</v-icon>
        <span class="sidebar-footer-btn-label">Scrapheap</span>
      </div>
    </div>
  </aside>
  <div
    v-if="sidebarNotice && sidebarNoticePosition"
    class="sidebar-inline-notice"
    :style="{
      top: `${sidebarNoticePosition.top}px`,
      left: `${sidebarNoticePosition.left}px`,
    }"
  >
    {{ sidebarNotice }}
  </div>

  <!-- ── Sidebar context menu ──────────────────────────────────── -->
  <Teleport to="body">
    <div
      v-if="sidebarCtxVisible"
      class="sidebar-ctx-menu"
      :style="{ left: sidebarCtxX + 'px', top: sidebarCtxY + 'px' }"
      @contextmenu.prevent
      @mousedown.stop
    >
      <template v-if="sidebarCtxCharacter">
        <button
          class="sidebar-ctx-item"
          :disabled="sidebarCtxDeleteIds.length > 1"
          :class="{
            'sidebar-ctx-item--disabled': sidebarCtxDeleteIds.length > 1,
          }"
          @click="
            sidebarCtxDeleteIds.length === 1 &&
            (openCharacterEditor(sidebarCtxCharacter), closeSidebarCtxMenu())
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          @click="
            deleteCharactersByIds(sidebarCtxDeleteIds);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-trash-can-outline</v-icon
          >
          {{
            sidebarCtxDeleteIds.length > 1
              ? `Delete ${sidebarCtxDeleteIds.length} characters`
              : "Delete"
          }}
        </button>
      </template>
      <template v-if="sidebarCtxSet">
        <button
          class="sidebar-ctx-item"
          @click="
            openSetEditor(sidebarCtxSet);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          @click="
            deleteSetById(sidebarCtxSet.id);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-trash-can-outline</v-icon
          >
          Delete
        </button>
      </template>
      <template v-if="sidebarCtxFolder">
        <button
          class="sidebar-ctx-item"
          @click="
            openReferenceFolderEditor(sidebarCtxFolder);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          @click="
            deleteReferenceFolderById(sidebarCtxFolder.id);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-trash-can-outline</v-icon
          >
          Remove
        </button>
      </template>
      <template v-if="sidebarCtxImportFolder">
        <button
          class="sidebar-ctx-item"
          @click="
            openImportFolderEditor(sidebarCtxImportFolder);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          @click="
            deleteImportFolderById(sidebarCtxImportFolder.id);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-trash-can-outline</v-icon
          >
          Remove
        </button>
      </template>
    </div>
  </Teleport>
</template>

<style scoped>
.sidebar-project-header {
  padding-top: 4px;
  padding-bottom: 4px;
  justify-content: center;
}

.sidebar-section-divider {
  height: 1px;
  background: rgba(var(--v-theme-border), 0.35);
}

.sidebar-collections-help-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 4px 4px;
}

.sidebar-collections-help {
  font-size: 0.9rem;
  font-style: italic;
  color: rgba(var(--v-theme-sidebar-text), 0.5);
}

.sidebar-view-tabs-row {
  display: flex;
  align-items: center;
  padding: 0 4px 0 4px;
  position: relative;
  z-index: 1;
  margin-top: 6px;
  margin-bottom: 0;
  gap: 0;
}

.sidebar-view-tabs-icon {
  flex-shrink: 0;
  color: rgba(var(--v-theme-sidebar-text), 0.45);
}

.sidebar-view-tabs-label {
  flex-shrink: 0;
  font-size: 0.72rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-sidebar-text), 0.35);
  white-space: nowrap;
}

.sidebar-view-tabs-arrow {
  font-size: 0.8em;
  opacity: 0.7;
}

.sidebar-view-tabs {
  display: flex;
  gap: 0;
  flex: 1;
}

.sidebar-view-tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 4px 4px;
  flex: 1;
  border-radius: 0;
  border: none;
  border-bottom: none;
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: pointer;
  background: rgba(var(--v-theme-surface), 0.1);
  color: rgba(var(--v-theme-sidebar-text), 0.7);
  transition:
    background 0.15s,
    color 0.15s;
  white-space: nowrap;
  margin-right: -1px;
  position: relative;
}

.sidebar-view-tab:last-child {
  margin-right: 0;
}

.sidebar-view-tab.active {
  background: rgba(var(--v-theme-tertiary), 0.48);
  color: rgb(var(--v-theme-sidebar-text));
  border-color: none;
  border-bottom: none;
  z-index: 2;
}

.sidebar-view-tab:hover:not(.active) {
  background: rgba(var(--v-theme-surface), 0.5);
  color: rgba(var(--v-theme-sidebar-text), 0.85);
}

.sidebar-tab-panel {
  margin: 0;
  padding: 0;
  flex: 1 1 0;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: rgba(var(--v-theme-tertiary), 0.16);
}

.sidebar-section-block {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 0 0 auto;
}

.sidebar-section-scroll {
  flex: 1 1 0;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-color: rgb(var(--v-theme-accent)) rgba(var(--v-theme-shadow), 0.15);
  background: rgba(var(--v-theme-shadow), 0.18);
  border-top: 1px dashed rgba(var(--v-theme-border), 0.55);
  border-bottom: 1px dashed rgba(var(--v-theme-border), 0.55);
}

.sidebar-section-scroll::-webkit-scrollbar {
  width: 8px;
}

.sidebar-section-scroll::-webkit-scrollbar-thumb {
  background: rgb(var(--v-theme-accent));
  border-radius: 8px;
}

.sidebar-section-scroll::-webkit-scrollbar-track {
  background: rgba(var(--v-theme-shadow), 0.15);
}

.sidebar-no-projects-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 40px 24px;
  text-align: center;
}

.sidebar-no-projects-icon {
  opacity: 0.35;
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-no-projects-text {
  font-size: 0.9rem;
  color: rgba(var(--v-theme-sidebar-text), 0.6);
  margin: 0;
  line-height: 1.5;
}

.sidebar-no-projects-btn :deep(.v-btn__content) {
  font-size: 0.82rem;
  padding: 2px 4px 2px 4px;
}

.sidebar-no-projects-btn--folders {
  min-width: 190px;
}

.sidebar-no-projects-btn--folders :deep(.v-btn__content) {
  white-space: nowrap;
  font-size: 0.8rem;
}

.sidebar-collapsed-project-wrap {
  position: relative;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.sidebar-collapsed-project-menu {
  position: fixed;
  z-index: 300;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  border-radius: 6px;
  box-shadow: none;
  overflow: hidden;
  min-width: 180px;
  white-space: nowrap;
}

.sidebar-collapsed-project-menu .sidebar-project-menu-item,
.sidebar-collapsed-project-menu .sidebar-project-menu-add {
  color: rgb(var(--v-theme-on-surface));
}

.sidebar-project-menu-wrap {
  position: relative;
  padding: 0;
  border-bottom: none;
}

.sidebar-project-trigger {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 0;
  border: none;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.7);
  background: rgba(var(--v-theme-tertiary), 0.38);
  color: rgba(var(--v-theme-sidebar-text), 0.75);
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  transition:
    background 0.15s,
    color 0.15s;
  text-align: left;
}

.sidebar-project-trigger:hover {
  background: rgba(var(--v-theme-accent), 0.12);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-accent), 0.2);
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-project-trigger-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.sidebar-project-trigger-chevron {
  flex-shrink: 0;
  opacity: 0.6;
}

.sidebar-project-menu {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 200;
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-tertiary)) 38%,
    rgb(var(--v-theme-sidebar))
  );
  border: none;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.5);
  border-radius: 0;
  box-shadow: 0 4px 8px rgba(var(--v-theme-shadow), 0.15);
  overflow: hidden;
}

.sidebar-project-menu-item {
  display: flex;
  align-items: center;
  padding: 7px 10px;
  cursor: pointer;
  font-size: 0.85rem;
  color: rgb(var(--v-theme-on-tertiary));
  transition:
    background 0.12s,
    color 0.12s,
    box-shadow 0.12s;
  gap: 6px;
}

.sidebar-project-menu-item:hover {
  background: rgba(var(--v-theme-accent), 0.12);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-accent), 0.2);
  color: rgb(var(--v-theme-on-tertiary));
}

.sidebar-project-menu-item.active {
  background: rgba(var(--v-theme-tertiary), 0.3);
  color: rgb(var(--v-theme-on-tertiary));
  font-weight: 600;
}

.sidebar-project-menu-item-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-project-menu-item-action {
  flex-shrink: 0;
  opacity: 0;
  color: rgb(var(--v-theme-on-tertiary));
  transition: opacity 0.12s;
}

.sidebar-project-menu-item:hover .sidebar-project-menu-item-action {
  opacity: 0.6;
}

.sidebar-project-menu-item-action:hover {
  opacity: 1 !important;
}

.sidebar-project-menu-add {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  color: rgba(var(--v-theme-on-tertiary), 0.7);
  border-top: 1px solid rgba(var(--v-theme-border), 0.3);
  transition:
    background 0.12s,
    color 0.12s,
    box-shadow 0.12s;
}

.sidebar-project-menu-add:hover {
  background: rgba(var(--v-theme-accent), 0.12);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-accent), 0.2);
  color: rgb(var(--v-theme-on-tertiary));
}

.sidebar-project-select {
  flex: 1;
  width: 100%;
  margin-left: 0;
}

.sidebar-native-select {
  background: rgba(var(--v-theme-surface), 0.3);
  color: rgb(var(--v-theme-on-surface));
  border-radius: 4px;
  min-height: 32px;
  height: 32px;
  font-size: 1em;
  box-shadow: 2px 2px 6px rgba(var(--v-theme-shadow), 0.2);
  margin-left: 6px;
  box-sizing: border-box;
  padding-left: 8px;
  padding-right: 8px;
  border: 1px solid rgba(var(--v-theme-border), 0.5);
  width: 230px;
  transition: border 0.15s;
}
.sidebar-native-select:focus {
  border: 1.5px solid rgb(var(--v-theme-accent));
}
.sidebar-native-select-chevron {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  pointer-events: none;
  color: rgb(var(--v-theme-on-surface));
  display: flex;
  align-items: center;
  height: 18px;
  z-index: 2;
}

.sidebar-search-result-label {
  display: flex;
  align-items: center;
  min-height: 32px;
  padding: 0 12px;
  margin-left: 6px;
  border-radius: 4px;
  background: rgba(var(--v-theme-surface), 0.2);
  color: rgba(var(--v-theme-on-surface), 0.7);
  border: 1px dashed rgba(var(--v-theme-border), 0.5);
  font-size: 0.95em;
}
/* Sidebar right edge for counts */
.sidebar {
  width: 240px;
  --sidebar-right-edge: 8px;
  --sidebar-header-action-right-edge: 0px;
  --sidebar-thumb-size: 36px;
  --sidebar-thumb-size-large: calc(var(--sidebar-thumb-size) + 8px);
  --sidebar-space-y: 6px;
  --sidebar-item-radius: 8px;
  color: rgb(var(--v-theme-sidebar-text));
  background: rgb(var(--v-theme-sidebar));
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  min-height: 0;
  height: 100%;
  max-height: 100%;
  overflow: hidden;
  scrollbar-color: rgb(var(--v-theme-accent)) rgba(var(--v-theme-shadow), 0.15);
  box-sizing: border-box;
}

.sidebar.sidebar-collapsed {
  width: calc(var(--sidebar-thumb-size) + 20px);
  overflow: hidden;
}

.sidebar.sidebar-collapsed .sidebar-brand {
  justify-content: center;
}

.sidebar.sidebar-collapsed .sidebar-brand-toggle:hover {
  justify-content: center;
  background-color: rgba(var(--v-theme-accent), 0.4);
}

.sidebar.sidebar-collapsed .sidebar-brand-left {
  display: none;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px 2px 2px;
  margin-bottom: 2px;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.35);
}

.sidebar-brand-left {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 2px 2px 10px;
}

.sidebar-brand-logo {
  width: 40px;
  height: 40px;
  object-fit: contain;
  transition:
    filter 0.2s ease,
    transform 0.2s ease;
}

.sidebar-brand-logo-link {
  display: flex;
  align-items: center;
  border-radius: 6px;
  outline: none;
}

.sidebar-brand-logo-link:hover .sidebar-brand-logo {
  filter: drop-shadow(0 0 8px rgba(var(--v-theme-accent), 0.9))
    drop-shadow(0 0 16px rgba(var(--v-theme-accent), 0.5));
  transform: scale(1.08);
}

.sidebar-brand-toggle:hover {
  background-color: rgb(var(--v-theme-accent));
}

.sidebar-brand-title {
  font-family: "PressStart2P", monospace;
  font-size: 0.95em;
  color: color-mix(
    in srgb,
    rgb(var(--v-theme-sidebar-text)) 90%,
    rgb(var(--v-theme-accent))
  );
}

.sidebar-brand-text {
  display: flex;
  flex-direction: column;
  justify-content: center;
  position: relative;
}

.sidebar-update-available {
  position: absolute;
  top: 100%;
  left: 0;
  font-size: 0.65rem;
  line-height: 1;
  color: rgba(var(--v-theme-accent), 0.8);
  text-decoration: none;
  white-space: nowrap;
}

.sidebar-update-available:hover {
  text-decoration: underline;
}

.sidebar-brand-task-btn {
  min-width: 30px;
  min-height: 30px;
  width: 30px;
  height: 30px;
  padding: 0;
  border-radius: 8px;
  background: transparent;
  border: none;
  box-shadow: none;
  opacity: 0.6;
}

.sidebar-brand-task-btn:hover {
  opacity: 1;
  background-color: rgba(var(--v-theme-accent), 0.25);
}

.sidebar-brand-toggle {
  min-width: 36px;
  min-height: 36px;
  width: 36px;
  height: 36px;
  padding: 0;
  border-radius: 8px;
  background: transparent;
  border: none;
  box-shadow: none;
}

.sidebar-brand-toggle:focus,
.sidebar-brand-toggle:focus-visible,
.sidebar-brand-toggle:active {
  outline: none;
  box-shadow: none;
}

.sidebar-collapsed-list {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sidebar-space-y);
  padding: 4px 0 8px;
  overflow-y: auto;
  flex: 1 1 auto;
  min-height: 0;
}

.sidebar-collapsed-spacer {
  flex: 1 1 auto;
  width: 100%;
}

.sidebar-collapsed-item {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--sidebar-item-radius);
  cursor: pointer;
  color: rgba(var(--v-theme-sidebar-text), 0.84);
  transition:
    background-color 0.18s ease,
    color 0.18s ease,
    transform 0.18s ease;
}

.sidebar-collapsed-item.active {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  box-shadow: inset 0 0 0 3px rgb(var(--v-theme-primary));
}

.sidebar-collapsed-item.droppable {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  box-shadow: inset 0 0 0 3px rgb(var(--v-theme-primary));
}

.sidebar-collapsed-item:hover {
  filter: brightness(1.03);
  background-color: rgba(var(--v-theme-accent), 0.18);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-accent), 0.22);
  transform: translateY(-1px);
}

.sidebar-collapsed-thumb {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  border-radius: var(--sidebar-item-radius);
  border: none;
  padding: 0;
  background: transparent;
  cursor: pointer;
  outline: none;
  box-shadow: none;
  transition:
    background 0.18s ease,
    box-shadow 0.18s ease,
    filter 0.18s ease;
}

.sidebar-collapsed-thumb img {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  object-fit: contain;
  border-radius: var(--sidebar-item-radius);
  display: block;
  position: relative;
  z-index: 1;
}

.sidebar-collapsed-thumb::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: 8px;
  pointer-events: none;
  opacity: 0;
  z-index: 2;
  box-shadow: inset 0 0 0 3px transparent;
  transition:
    box-shadow 0.18s ease,
    opacity 0.18s ease;
}

.sidebar-collapsed-thumb .sidebar-character-thumb {
  filter: drop-shadow(0 2px 6px rgba(var(--v-theme-shadow), 0.35));
}

.sidebar-collapsed-thumb:focus,
.sidebar-collapsed-thumb:focus-visible,
.sidebar-collapsed-thumb:active,
.sidebar-collapsed-thumb img:focus,
.sidebar-collapsed-thumb img:focus-visible,
.sidebar-collapsed-thumb img:active {
  outline: none;
  box-shadow: none;
}

.sidebar-collapsed-thumb.active {
  background: rgb(var(--v-theme-primary));
}

.sidebar-collapsed-thumb.active::after {
  opacity: 1;
  box-shadow: inset 0 0 0 3px rgb(var(--v-theme-primary));
}

.sidebar-collapsed-thumb:hover {
  filter: brightness(1.03);
  background-color: rgba(var(--v-theme-accent), 0.18);
  transform: translateY(-1px) scale(1.02);
}

.sidebar-collapsed-thumb:hover::after {
  opacity: 1;
  box-shadow: inset 0 0 0 3px rgba(var(--v-theme-accent), 0.7);
}

.sidebar-collapsed-thumb.droppable {
  background: rgb(var(--v-theme-primary));
}

.sidebar-collapsed-thumb.droppable::after {
  opacity: 1;
  box-shadow: inset 0 0 0 3px rgb(var(--v-theme-primary));
}

.sidebar-collapsed-divider {
  width: 100%;
  height: 1px;
  margin-top: 1px;
  margin-bottom: 1px;
  background: rgba(var(--v-theme-background), 0.3);
}

@media (max-width: 900px) {
  .sidebar {
    width: 240px !important;
    height: 100dvh;
    max-height: 100dvh;
  }

  .sidebar.sidebar-collapsed {
    display: none;
  }
}

.sidebar-section-header {
  position: relative;
  font-size: 1rem;
  font-weight: 600;
  min-height: 38px;
  padding: 2px 12px;
  padding-right: var(--sidebar-header-action-right-edge) !important;
  display: flex;
  align-items: center;
  color: rgba(var(--v-theme-sidebar-text), 0.58);
}

.sidebar-section-header--collapsible {
  cursor: pointer;
  user-select: none;
}

.sidebar-section-header--collapsible:hover {
  color: rgba(var(--v-theme-sidebar-text), 0.85);
}

.sidebar-section-chevron {
  margin-right: 4px;
  flex-shrink: 0;
  color: rgba(var(--v-theme-sidebar-text), 0.5);
  transition: transform 0.15s;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.sidebar-list-item,
.sidebar-list-item.active {
  display: flex;
  align-items: center;
  min-height: max(30px, calc(var(--sidebar-thumb-size) + 6px));
  padding: 3px 10px;
  padding-right: var(--sidebar-right-edge) !important;
  cursor: pointer;
  border-radius: 0;
  margin-bottom: 0;
  font-size: 0.875rem;
  font-weight: 500;
  background: transparent;
  color: rgba(var(--v-theme-sidebar-text), 0.76);
  transition:
    background 0.18s,
    color 0.18s,
    box-shadow 0.18s;
}

.sidebar-footer-spacer {
  flex: 1 1 auto;
}

.sidebar-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: visible;
  overflow-y: auto;
  padding: 0px 0 0;
  scrollbar-color: rgb(var(--v-theme-accent)) rgba(var(--v-theme-shadow), 0.15);
  display: flex;
  flex-direction: column;
  align-items: stretch;
}

.sidebar-scroll::-webkit-scrollbar {
  width: 8px;
}

.sidebar-scroll::-webkit-scrollbar-thumb {
  background: rgb(var(--v-theme-accent));
  border-radius: 8px;
}

.sidebar-scroll::-webkit-scrollbar-track {
  background: rgba(var(--v-theme-shadow), 0.15);
}

.sidebar-sticky-footer {
  display: flex;
  align-items: stretch;
  border-top: 1px solid rgba(var(--v-theme-border), 0.3);
  background: rgb(var(--v-theme-sidebar));
  flex-shrink: 0;
}

.sidebar-footer-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 10px 4px;
  cursor: pointer;
  color: rgba(var(--v-theme-sidebar-text), 0.55);
  transition:
    background 0.15s,
    color 0.15s;
  font-size: 0.72rem;
  font-weight: 500;
}

.sidebar-footer-btn:hover {
  color: rgb(var(--v-theme-sidebar-text));
  background: rgba(var(--v-theme-sidebar-text), 0.08);
}

.sidebar-footer-btn--upload:hover {
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-footer-btn--scrapheap:hover {
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-footer-btn--scrapheap.active {
  color: rgb(var(--v-theme-error));
  opacity: 1;
}

.sidebar-collapsed .sidebar-sticky-footer {
  flex-direction: column;
}

.sidebar-collapsed .sidebar-footer-btn {
  flex: 0 0 auto;
  padding: 8px 4px;
}

.sidebar-collapsed .sidebar-footer-btn-label {
  display: none;
}

.sidebar-footer {
  padding: 4px 0 0 0;
}

.sidebar-footer-item {
  margin-bottom: 0;
}

.sidebar-list-item.active {
  background: rgba(var(--v-theme-primary), 0.6);
  color: rgb(var(--v-theme-on-primary));
  position: relative;
  border-radius: 0;
}

.sidebar-list-item.active .sidebar-list-count {
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-list-item:hover {
  background: rgba(var(--v-theme-accent), 0.12);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-accent), 0.2);
}

.sidebar-list-item.droppable {
  filter: brightness(1.2);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-header-spacer {
  flex: 1 1 auto;
}

.sidebar-header-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 48px;
  justify-content: flex-end;
  margin-left: auto;
  padding-right: var(--sidebar-header-action-right-edge) !important;
}

.sidebar-header-actions .v-icon {
  min-width: 26px;
  min-height: 26px;
  justify-content: center;
  text-align: center;
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-move-to-project-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.sidebar-move-menu {
  position: fixed;
  z-index: 9999;
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-tertiary)) 38%,
    rgb(var(--v-theme-sidebar))
  );
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  border-radius: 6px;
  min-width: 180px;
  max-width: 300px;
  max-height: 420px;
  overflow-y: auto;
  padding: 4px 0;
  box-shadow: 0 4px 16px rgba(var(--v-theme-shadow), 0.3);
  white-space: nowrap;
}

.sidebar-move-menu-group-header {
  padding: 6px 10px 3px;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-tertiary), 0.45);
  margin-top: 4px;
}

.sidebar-move-menu-group-header:first-child {
  margin-top: 0;
}

.sidebar-move-menu-group-header--current {
  color: rgba(var(--v-theme-primary), 0.8);
}

.sidebar-move-menu-item {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 12px;
  font-size: 0.85rem;
  cursor: pointer;
  color: rgb(var(--v-theme-on-tertiary));
  overflow: hidden;
  text-overflow: ellipsis;
  transition: background 0.12s;
}

.sidebar-move-menu-check {
  flex-shrink: 0;
  opacity: 0.7;
}

.sidebar-move-menu-item--create {
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.3);
  margin-bottom: 2px;
  font-style: italic;
  opacity: 0.85;
}

.sidebar-move-menu-item--checked .sidebar-move-menu-check {
  opacity: 1;
}

.sidebar-move-menu-item:hover {
  background: rgba(var(--v-theme-tertiary), 0.25);
}

.sidebar-list-icon {
  display: flex;
  align-items: center;
  margin-right: 8px;
  justify-content: center;
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  overflow: visible;
}

.sidebar-list-icon .v-icon,
.sidebar-collapsed-item .v-icon,
.sidebar-brand-toggle .v-icon,
.sidebar-brand-task-btn .v-icon {
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-list-label {
  flex: 1;
  min-width: 0;
  text-align: left;
  padding-left: 0;
}

.sidebar-list-label-text {
  display: block;
  width: 100%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-character-thumb {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  object-fit: contain;
  border-radius: var(--sidebar-item-radius);
  background: transparent;
  border: 1px solid rgba(var(--v-theme-border), 0.45);
  display: inline-block;
  filter: drop-shadow(0 2px 6px rgba(var(--v-theme-shadow), 0.35));
  transition:
    transform 0.18s ease,
    box-shadow 0.18s ease,
    border-color 0.18s ease;
}

.sidebar-set-thumb-image {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  border-radius: var(--sidebar-item-radius);
  object-fit: cover;
  background: transparent;
  border: none;
  box-shadow: none;
  display: block;
  box-sizing: border-box;
  transition:
    transform 0.18s ease,
    box-shadow 0.18s ease;
}

.sidebar-set-thumb-image--collapsed {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  margin: 0;
  border: none;
  box-shadow: none;
}

.sidebar-set-thumb-image--large {
  width: var(--sidebar-thumb-size-large);
  height: var(--sidebar-thumb-size-large);
  border-radius: var(--sidebar-item-radius);
}

.sidebar-list-item:hover .sidebar-character-thumb,
.sidebar-list-item:hover .sidebar-set-thumb-image {
  transform: scale(1.02);
  box-shadow: 0 3px 9px rgba(var(--v-theme-shadow), 0.2);
}

.sidebar-list-item:hover .sidebar-character-thumb {
  border-color: rgba(var(--v-theme-accent), 0.45);
}

.sidebar-collapsed-item,
.sidebar-collapsed-thumb {
  position: relative;
  overflow: hidden;
}

.sidebar-character-group {
  display: flex;
  flex-direction: column;
  width: 100%;
}

.sidebar-error-bubble {
  position: fixed;
  top: 72px;
  left: 20px;
  transform: translateY(-50%);
  z-index: 1200;
  color: rgb(var(--v-theme-on-error));
  background: rgba(var(--v-theme-error), 0.8);
  padding: 10px 16px;
  border-radius: 14px;
  font-size: 0.9em;
  line-height: 1.3;
  box-shadow: 0 8px 20px rgba(var(--v-theme-shadow), 0.25);
  pointer-events: none;
  max-width: 360px;
  white-space: normal;
  word-break: break-word;
}

.sidebar-list-count {
  font-size: 0.75rem;
  color: rgba(var(--v-theme-sidebar-text), 0.55);
  min-width: 2.4em;
  text-align: right;
  margin: 0;
  font-weight: 400;
  letter-spacing: 0.01em;
  align-self: center;
  display: inline-flex;
  justify-content: flex-end;
}

.sidebar-new-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.65em;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 2px 2px;
  margin-right: 4px;
  border-radius: 4px;
  color: rgb(var(--v-theme-on-primary));
  background: rgba(var(--v-theme-primary), 0.7);
  position: relative;
  top: -2px;
}

.sidebar-character-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  justify-content: flex-end;
}

.sidebar-character-actions .sidebar-list-count {
  margin: 0;
}

.sidebar-character-toggle {
  cursor: pointer;
  color: rgb(var(--v-theme-sidebar-text));
  opacity: 0.8;
  margin-right: 4px;
}

.sidebar-character-toggle:hover {
  opacity: 1;
  color: rgb(var(--v-theme-on-primary));
}

.add-character-inline {
  color: rgb(var(--v-theme-sidebar-text)) !important;
  font-size: 1.1rem;
  cursor: pointer;
  background: transparent;
  border-radius: 8px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s;
}

.add-character-inline:hover {
  background: rgb(var(--v-theme-accent));
}

.clear-selection-inline {
  color: rgb(var(--v-theme-primary)) !important;
  font-size: 1rem;
  cursor: pointer;
  background: transparent;
  border: none;
  padding: 0;
  border-radius: 8px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 26px;
  transition: background 0.2s;
}

.clear-selection-inline:hover {
  background: rgba(var(--v-theme-primary), 0.18);
}

.edit-character-inline,
.edit-set-inline {
  color: rgb(var(--v-theme-sidebar-text)) !important;
  font-size: 1.2rem;
  cursor: pointer;
  background: transparent;
  border-radius: 8px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 26px;
  transition:
    background 0.2s,
    color 0.2s;
}

.edit-character-inline:hover,
.edit-set-inline:hover {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary)) !important;
}

.sidebar-all-pictures-row {
  padding-top: 2px;
  display: flex;
  align-items: stretch;
  width: 100%;
  transition: outline 0.15s;
}

.sidebar-all-pictures-row.drag-over-project {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
  background: rgba(var(--v-theme-primary), 0.15);
}

.sidebar-all-pictures-row .sidebar-list-item {
  flex: 1 1 0;
  width: 0;
  min-width: 0;
  overflow: hidden;
}

.sidebar-all-pictures-row:hover {
  background: rgba(var(--v-theme-accent), 0.6);
}

.sidebar-all-pictures-row:has(.sidebar-list-item.active) {
  background: rgba(var(--v-theme-primary), 0.6);
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-all-pictures-row .sidebar-list-item:hover,
.sidebar-all-pictures-row .sidebar-list-item.active {
  background: transparent;
}

.sidebar-all-pictures-actions {
  display: flex;
  flex-direction: row;
  align-items: stretch;
  gap: 0;
  flex-shrink: 0;
}

.sidebar-inline-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  width: max(52px, calc(var(--sidebar-thumb-size) + 8px));
  flex-shrink: 0;
  border-radius: 0;
  color: rgb(var(--v-theme-sidebar-text));
  opacity: 0.55;
  transition:
    background 0.18s,
    opacity 0.18s,
    color 0.18s;
}

.sidebar-inline-btn .v-icon {
  color: inherit;
}

.sidebar-inline-btn:hover {
  opacity: 1;
}

.sidebar-inline-btn--upload:hover {
  color: rgb(var(--v-theme-success));
}

.sidebar-inline-btn--scrapheap:hover {
  color: rgb(var(--v-theme-error));
}

.sidebar-inline-btn--scrapheap.active {
  opacity: 1;
  color: rgb(var(--v-theme-error));
}

.delete-character-inline {
  color: rgb(var(--v-theme-sidebar-text)) !important;
  font-size: 1.1rem;
  cursor: pointer;
  background: transparent;
  border-radius: 8px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 26px;
  transition:
    background 0.2s,
    color 0.2s;
}

.delete-character-inline:hover {
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error)) !important;
}

.sidebar-sort {
  display: flex;
  flex-direction: column;
}

.sidebar-sort-select {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: 6px !important;
  min-height: 36px !important;
  height: 36px !important;
  font-size: 0.97em;
  box-shadow: none;
  margin-top: 0px;
  margin-bottom: 2px;
  align-items: center;
  padding-left: 6px;
  padding-right: 6px;
}

/* Remove extra height from v-input root for select */
.sidebar-sort-select .v-input__control,
.sidebar-sort-select .v-field {
  min-height: 32px !important;
  height: 32px !important;
  border-radius: 12px !important;
  box-shadow: none !important;
}

.sidebar-sort-select .v-field__input {
  min-height: 28px !important;
  height: 28px !important;
  padding-top: 2px !important;
  padding-bottom: 2px !important;
  align-items: center;
}

.sidebar-set-item {
  position: relative;
  overflow: visible;
}

.sidebar-inline-notice {
  position: fixed;
  transform: translateY(-50%);
  background: rgba(var(--v-theme-secondary), 0.75);
  color: rgb(var(--v-theme-on-secondary));
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 0.9em;
  white-space: nowrap;
  pointer-events: none;
  z-index: 1000 !important;
}

@media (max-width: 900px) {
  .sidebar {
    width: 100%;
    min-height: 100%;
    height: 100%;
  }

  .sidebar-list-item,
  .sidebar-list-item.active {
    min-height: 56px;
    padding: 6px 10px;
  }

  .sidebar-section-header {
    min-height: 48px;
    padding: 6px 8px;
  }

  .sidebar-list-icon {
    width: var(--sidebar-thumb-size);
    height: var(--sidebar-thumb-size);
  }

  .sidebar-character-thumb {
    width: var(--sidebar-thumb-size);
    height: var(--sidebar-thumb-size);
  }

  .add-character-inline,
  .delete-character-inline,
  .edit-character-inline,
  .edit-set-inline,
  .clear-selection-inline {
    width: 36px;
    height: 36px;
  }

  .sidebar-header-actions .v-icon {
    min-width: 44px;
    min-height: 44px;
  }
}

/* ── Reference Folders panel ──────────────────────────────── */
.sidebar-folders-tab-toolbar {
  display: flex;
  align-items: center;
  padding: 2px 8px 2px 12px;
  min-height: 26px;
  gap: 4px;
  flex-shrink: 0;
}

.sidebar-folders-tab-toolbar-label {
  font-size: 0.74rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: rgba(var(--v-theme-sidebar-text), 0.4);
  user-select: none;
}

.sidebar-folders-loading {
  display: flex;
  justify-content: center;
  padding: 32px;
}

.sidebar-folders-list {
  flex: 1 1 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 4px 0;
  scrollbar-color: rgb(var(--v-theme-accent)) rgba(var(--v-theme-shadow), 0.15);
}

.sidebar-folder-section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 8px 4px 12px;
}

.sidebar-folder-section-title {
  flex: 1 1 auto;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: rgba(var(--v-theme-sidebar-text), 0.45);
}

.sidebar-folder-section-edit-btn {
  width: 22px;
  justify-content: flex-end;
  flex-shrink: 0;
  opacity: 0.66;
  cursor: pointer;
  color: rgba(var(--v-theme-sidebar-text), 0.66);
  transition:
    opacity 0.15s,
    color 0.15s;
}

.sidebar-folder-section-edit-btn:hover {
  opacity: 1;
  color: rgb(var(--v-theme-primary));
}

.sidebar-folder-root {
  display: flex;
  flex-direction: column;
}

.sidebar-folder-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  cursor: pointer;
  font-size: 0.82rem;
  color: rgba(var(--v-theme-sidebar-text), 0.85);
  user-select: none;
}

.sidebar-folder-root-row {
  font-weight: 600;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.2);
}

.sidebar-folder-row:hover {
  background: rgba(var(--v-theme-accent), 0.1);
}

.sidebar-folder-row.active {
  background: rgba(var(--v-theme-primary), 0.6);
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-folder-children {
  padding-left: 4px;
  border-left: 1px dashed rgba(var(--v-theme-border), 0.35);
  margin-left: 11px;
}

.sidebar-folder-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.sidebar-folder-chevron,
.sidebar-folder-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.sidebar-folder-status-badge {
  flex-shrink: 0;
  margin-left: 2px;
  opacity: 0.75;
}

.sidebar-folder-count-badge {
  flex-shrink: 0;
  margin-left: 4px;
  min-width: 22px;
  text-align: right;
  font-size: 0.74rem;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-sidebar-text), 0.6);
}

.sidebar-folder-row.active .sidebar-folder-count-badge {
  color: rgba(var(--v-theme-on-primary), 0.9);
}

.sidebar-folder-status--active {
  color: rgba(var(--v-theme-sidebar-text), 0.4);
  cursor: pointer;
  border-radius: 3px;
  transition:
    color 0.15s,
    opacity 0.15s;
}

.sidebar-folder-status--active:hover {
  color: rgb(var(--v-theme-sidebar-text));
  opacity: 1;
}

.sidebar-folder-status--pending_mount {
  color: rgb(var(--v-theme-warning, 255, 152, 0));
}

.sidebar-folder-status--scanning {
  color: rgba(var(--v-theme-sidebar-text), 0.5);
  display: flex;
  align-items: center;
}

.sidebar-folder-status--mount_error {
  color: rgb(var(--v-theme-error, 244, 67, 54));
}

.sidebar-folder-loading-row {
  display: flex;
  justify-content: center;
  padding: 8px;
}

.sidebar-folder-empty-row {
  padding: 4px 8px;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-sidebar-text), 0.45);
  font-style: italic;
}

.sidebar-folder-error-row {
  color: rgba(var(--v-theme-error, 244, 67, 54), 0.8);
}

/* ── Sidebar context menu ────────────────────────────── */
.sidebar-ctx-menu {
  position: fixed;
  z-index: 2000;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 6px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.22);
  padding: 4px 0;
  min-width: 140px;
  user-select: none;
}

.sidebar-ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 14px;
  font-size: 13px;
  color: rgb(var(--v-theme-on-surface));
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
  transition: background 0.1s;
}

.sidebar-ctx-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.sidebar-ctx-item--danger {
  color: rgb(var(--v-theme-error));
}

.sidebar-ctx-item--disabled {
  opacity: 0.38;
  cursor: default;
  pointer-events: none;
}

.sidebar-ctx-icon {
  flex-shrink: 0;
  opacity: 0.7;
  flex-shrink: 0;
  opacity: 0;
  cursor: pointer;
  transition: opacity 0.15s;
  color: rgba(var(--v-theme-sidebar-text), 0.7);
  padding: 2px;
}

.folder-type-card {
  border-radius: 14px;
}

.folder-type-title {
  font-size: 1.04rem;
  font-weight: 700;
  padding: 16px 18px 8px;
}

.folder-type-body {
  padding: 8px 18px 0;
}

.folder-type-subtitle {
  margin: 0 0 10px;
  font-size: 0.83rem;
  color: rgba(var(--v-theme-on-surface), 0.62);
}

.folder-type-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.folder-type-option {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  border: 1px solid rgba(var(--v-theme-border), 0.35);
  border-radius: 10px;
  background: rgba(var(--v-theme-surface), 0.4);
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
  color: rgb(var(--v-theme-on-surface));
}

.folder-type-option:hover {
  border-color: rgba(var(--v-theme-primary), 0.6);
  background: rgba(var(--v-theme-primary), 0.08);
}

.folder-type-option-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.folder-type-option-text strong {
  font-size: 0.88rem;
  font-weight: 700;
}

.folder-type-option-text small {
  font-size: 0.75rem;
  color: rgba(var(--v-theme-on-surface), 0.62);
}

.folder-type-actions {
  padding: 10px 16px 14px;
}
</style>
