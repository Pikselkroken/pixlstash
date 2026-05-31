<script setup>
import {
  computed,
  ref,
  onBeforeUnmount,
  onMounted,
  watch,
  nextTick,
} from "vue";
import ImageImporter from "../io/ImageImporter.vue";
import CharacterEditor from "../editors/CharacterEditor.vue";
import PictureSetEditor from "../editors/PictureSetEditor.vue";
import ProjectEditor from "../editors/ProjectEditor.vue";
import ProjectFiles from "./ProjectFiles.vue";
import UserSettingsDialog from "../settings/UserSettingsDialog.vue";
import FolderTreeNode from "../editors/FolderTreeNode.vue";
import FolderEditor from "../editors/FolderEditor.vue";
import ShareDialog from "../io/ShareDialog.vue";
import unknownPerson from "../../assets/unknown-person.png"; // Fallback avatar for characters without thumbnails
import {
  apiClient,
  appendShareToken,
  isReadOnly,
  sessionContext,
} from "../../utils/apiClient";
import { extractSupportedImportFilesFromDataTransfer } from "../../utils/media.js";
import {
  SET_ICONS,
  SET_COLORS,
  SET_ICON_CATEGORIES,
  ICON_CARDS,
} from "../../utils/setAppearance.js";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";

// Publishes id → name maps for the ImageGrid breadcrumb. The sidebar is the
// authoritative name source (it fetches these lists); see useEntityNamesStore.
const entityNames = useEntityNamesStore();

const appVersion = __APP_VERSION__;

const latestVersion = ref(null);
const latestVersionUrl = ref(null);
const latestSecurityLevel = ref(null);

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

const securityUpdateClass = computed(() => {
  if (!latestSecurityLevel.value) return "sidebar-update-available";
  const high = ["critical", "high"].includes(
    latestSecurityLevel.value.toLowerCase(),
  );
  return high
    ? "sidebar-update-available sidebar-update-security sidebar-update-security--high"
    : "sidebar-update-available sidebar-update-security";
});

const securityUpdateTitle = computed(() => {
  if (!latestSecurityLevel.value) return undefined;
  return `v${latestVersion.value} includes a ${latestSecurityLevel.value}-severity security fix. Update as soon as possible.`;
});

const LATEST_VERSION_URL = "https://pixlstash.dev/latest-version.json";
const UPDATE_PAGE_URL = "https://pixlstash.dev/upgrade.html";

const props = defineProps({
  docked: { type: Boolean, default: false },
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
  publicUrl: { type: String, default: null },
  embedWatermark: { type: Boolean, default: false },
  sidebarThumbnailSize: { type: Number, default: 48 },
  dateFormat: { type: String, default: "locale" },
  themeMode: { type: String, default: "light" },
  hasFolderFilter: { type: Boolean, default: false },
  activeFolderKey: { type: String, default: null },
  externalProjectViewMode: { type: String, default: null },
  externalSelectedProjectId: { type: Number, default: null },
  checkForUpdates: { type: Boolean, default: null },
  installType: { type: String, default: "pip" },
  dockerVariant: { type: String, default: "gpu" },
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
  "toggle-dock",
  "update:sort-options",
  "update:hidden-tags",
  "update:apply-tag-filter",
  "update:comfyui-configured",
  "update:public-url",
  "update:embed-watermark",
  "open-import-dialog",
  "update:project-view-mode",
  "update:selected-project-id",
  "view-project",
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

// Project tree expansion state (Projects tab flat tree)
const expandedProjectIds = ref(new Set());
const projectTreePeopleCollapsed = ref(new Set()); // project IDs where People is collapsed
const projectTreeSetsCollapsed = ref(new Set()); // project IDs where Sets is collapsed

function toggleProjectExpanded(id) {
  const next = new Set(expandedProjectIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expandedProjectIds.value = next;
}

function toggleProjectTreePeople(id) {
  const next = new Set(projectTreePeopleCollapsed.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  projectTreePeopleCollapsed.value = next;
}

function toggleProjectTreeSets(id) {
  const next = new Set(projectTreeSetsCollapsed.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  projectTreeSetsCollapsed.value = next;
}

function selectProjectNode(p) {
  // Explicit entry click → navigate. Unlike a tab switch, clicking a specific
  // project IS a navigation: emit `view-project` so App pushes /project/:id.
  // `applyRouteToStores` then sets projectViewMode/selectedProjectId from the
  // route (the single source of truth), which scopes the grid to the project.
  selectProject(p.id); // update sidebar-local highlight/scope
  emit("view-project", p.id);
}

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
const projectMenuSection = ref(null); // 'projects' | 'folders' | null
const projectMenuSubPos = ref({ top: 0, left: 0 });
const projectMenuRef = ref(null);
const collapsedProjectBtnRef = ref(null);
const collapsedProjectMenuRef = ref(null);
const collapsedProjectSubMenuRef = ref(null);
const collapsedProjectMenuPos = ref({ top: 0, left: 0 });

const dockedScrollRef = ref(null);
const dockedScrollHeight = ref(0);

const collapsedCharBtnRef = ref(null);
const collapsedCharMenuRef = ref(null);
const collapsedCharMenuOpen = ref(false);
const collapsedCharMenuPos = ref({ top: 0, left: 0 });

const collapsedSetBtnRef = ref(null);
const collapsedSetMenuRef = ref(null);
const collapsedSetMenuOpen = ref(false);
const collapsedSetMenuPos = ref({ top: 0, left: 0 });
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
const sidebarCtxSet = ref(null); // { id, name, set_icon, set_color } or null
const setCtxIconMenuOpen = ref(false);
const setCtxColorMenuOpen = ref(false);
const setCtxAppearanceMenuPos = ref({ top: 0, left: 0, openUp: false });
const sidebarCtxFolder = ref(null); // reference folder object or null
const sidebarCtxImportFolder = ref(null); // import folder object or null
const sidebarCtxProject = ref(null); // { id, name } or null
const sidebarCtxAllPictures = ref(false); // true when ctx opened from All Pictures row
const sidebarCtxDeleteIds = ref([]); // character IDs to delete via context menu

// Computed style for the main context menu — opens upward when near the bottom.
const sidebarCtxMenuStyle = computed(() => {
  const MENU_W = 165;
  const MENU_H = 190; // actual menu height estimate
  const x = Math.min(sidebarCtxX.value, window.innerWidth - MENU_W - 8);
  if (sidebarCtxY.value + MENU_H > window.innerHeight - 8) {
    return {
      left: x + "px",
      bottom: window.innerHeight - sidebarCtxY.value + "px",
    };
  }
  return { left: x + "px", top: sidebarCtxY.value + "px" };
});

// Computed style for the icon/color appearance sub-panels.
const setCtxAppearanceStyle = computed(() => {
  const pos = setCtxAppearanceMenuPos.value;
  if (pos.openUp) {
    return { left: pos.left + "px", bottom: pos.bottom + "px" };
  }
  return { left: pos.left + "px", top: pos.top + "px" };
});
// Shared resource IDs — drives the share-link icon overlay on sidebar items
const sharedCharacterIds = ref(new Set());
const sharedSetIds = ref(new Set());
const sharedProjectIds = ref(new Set());

// Confirm-revoke-all dialog state
const revokeSharesDialogOpen = ref(false);
const revokeSharesPending = ref(null); // { resourceType, resourceId, label }

// Share dialog state
const shareDialogOpen = ref(false);
const shareDialogPending = ref(null); // { resourceType, resourceId, label }

function openCharacterMoveMenu(event) {
  const el = event?.currentTarget ?? event?.target;
  if (el) {
    const rect = el.getBoundingClientRect();
    characterMenuPos.value = { top: rect.bottom + 4, left: rect.left };
  }
  characterMoveMenuOpen.value = !characterMoveMenuOpen.value;
}

function openSetMoveMenu(event) {
  const el = event?.currentTarget ?? event?.target;
  if (el) {
    const rect = el.getBoundingClientRect();
    setMenuPos.value = { top: rect.bottom + 4, left: rect.left };
  }
  setMoveMenuOpen.value = !setMoveMenuOpen.value;
}

// --- Character Editor State ---
const characterEditorOpen = ref(false);
const characterEditorCharacter = ref(null);

const setEditorOpen = ref(false);
const setEditorSet = ref(null);
const settingsDialogOpen = ref(false);
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
const referenceFoldersCollapsed = ref(false);
const importFoldersCollapsed = ref(false);
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

  // If a new import folder was just created, navigate to it so the user
  // sees the "scanning" state rather than whatever was previously shown.
  if (createdNewFolder) {
    const newFolder = importFolders.value.reduce(
      (best, entry) => (!best || entry.id > best.id ? entry : best),
      null,
    );
    if (newFolder) {
      selectedFolderKey.value = `if-${newFolder.id}`;
      emit("select-folder", {
        importSourceFolder: newFolder.folder,
        importFolderId: newFolder.id,
        label: newFolder.label || newFolder.folder,
      });
      emit("update:folder-scanning", Boolean(newFolder.last_checked == null));
      return;
    }
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
    importFolderId: selectedImportFolder.id,
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

const collapsedProjectBtnTitle = computed(() => {
  if (sidebarPrimaryTab.value === "folders") {
    if (!selectedFolderKey.value) return "Folders";
    if (selectedFolderKey.value.startsWith("rf-")) {
      const id = Number(selectedFolderKey.value.slice(3));
      const rf = referenceFolders.value.find((f) => f.id === id);
      return rf ? rf.label || rf.folder : "Folder";
    }
    if (selectedFolderKey.value.startsWith("if-")) {
      const id = Number(selectedFolderKey.value.slice(3));
      const imf = importFolders.value.find((f) => Number(f.id) === id);
      return imf ? imf.label || imf.folder : "Folder";
    }
    return "Folder";
  }
  if (projectViewMode.value === "global") return "Global (all projects)";
  if (selectedProjectId.value === null) return "No project";
  return selectedProjectObj.value?.name ?? "Project";
});

async function fetchReferenceFolders() {
  referenceFoldersLoading.value = true;
  try {
    const res = await apiClient.get("/reference-folders");
    referenceFolders.value = res.data?.folders ?? [];
    entityNames.mergeRefFolderLabels(referenceFolders.value);
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
    entityNames.mergeImportFolderLabels(importFolders.value);
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
  // Stateless tab switch: only change which list the sidebar shows. Do NOT
  // emit select-* / navigate / clear the grid's selection — switching a tab
  // must leave the current view intact so the user can drag pictures from it
  // onto entries in this tab.
  sidebarPrimaryTab.value = "folders";
  projectViewMode.value = "global";
}

function selectLibraryTab(mode) {
  // Stateless tab switch (see selectFoldersTab): sidebar-display only, no
  // navigation and no grid-filter mutation.
  sidebarPrimaryTab.value = "library";
  if (mode === "project") {
    switchToProjectView();
  } else {
    projectViewMode.value = "global";
  }
  // Clear only the sidebar's own folder highlight (display state); the grid
  // view is unchanged and still driven by the route.
  selectedFolderReferenceId.value = null;
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

  // Pick an icon and color not already in use by sibling sets.
  const siblingScope =
    defaultProjectId !== null
      ? nonReferenceSets.value.filter((s) => s.project_id === defaultProjectId)
      : nonReferenceSets.value;
  const usedIcons = new Set(
    siblingScope.map((s) => s.set_icon).filter(Boolean),
  );
  const usedColors = new Set(
    siblingScope.map((s) => s.set_color).filter(Boolean),
  );
  const autoIcon =
    SET_ICONS.find((i) => !usedIcons.has(i.value))?.value ??
    SET_ICONS[siblingScope.length % SET_ICONS.length].value;
  const autoColor =
    SET_COLORS.find((c) => !usedColors.has(c.value))?.value ??
    SET_COLORS[siblingScope.length % SET_COLORS.length].value;

  setEditorSet.value = {
    ...(defaultProjectId !== null ? { project_id: defaultProjectId } : {}),
    set_icon: autoIcon,
    set_color: autoColor,
  };
  setEditorOpen.value = true;
}

function toggleProjectMenu() {
  if (!projectMenuOpen.value && props.docked && collapsedProjectBtnRef.value) {
    const rect = collapsedProjectBtnRef.value.getBoundingClientRect();
    collapsedProjectMenuPos.value = _flyoutPos(rect);
    if (
      referenceFolders.value.length === 0 &&
      importFolders.value.length === 0
    ) {
      fetchReferenceFolders();
      fetchImportFolders();
    }
  }
  projectMenuSection.value = null;
  projectMenuOpen.value = !projectMenuOpen.value;
}

let _projectSubCloseTimer = null;

function openProjectSubMenu(section, event) {
  clearTimeout(_projectSubCloseTimer);
  const rect = event.currentTarget.getBoundingClientRect();
  projectMenuSubPos.value = { top: rect.top - 4, left: rect.right + 4 };
  projectMenuSection.value = section;
}

function scheduleCloseProjectSubMenu() {
  _projectSubCloseTimer = setTimeout(() => {
    projectMenuSection.value = null;
  }, 180);
}

function cancelCloseProjectSubMenu() {
  clearTimeout(_projectSubCloseTimer);
}

function _flyoutPos(rect) {
  const menuMaxH = window.innerHeight * 0.6;
  const menuMinW = 200;
  const left = rect.right + 4;
  // Clamp top so menu doesn't go below viewport
  const top = Math.min(rect.top, window.innerHeight - menuMaxH - 8);
  return { top: Math.max(8, top), left };
}

function toggleCollapsedCharMenu() {
  collapsedSetMenuOpen.value = false;
  projectMenuOpen.value = false;
  projectMenuSection.value = null;
  if (!collapsedCharMenuOpen.value && collapsedCharBtnRef.value) {
    const rect = collapsedCharBtnRef.value.getBoundingClientRect();
    collapsedCharMenuPos.value = _flyoutPos(rect);
  }
  collapsedCharMenuOpen.value = !collapsedCharMenuOpen.value;
}

function toggleCollapsedSetMenu() {
  collapsedCharMenuOpen.value = false;
  projectMenuOpen.value = false;
  projectMenuSection.value = null;
  if (!collapsedSetMenuOpen.value && collapsedSetBtnRef.value) {
    const rect = collapsedSetBtnRef.value.getBoundingClientRect();
    collapsedSetMenuPos.value = _flyoutPos(rect);
  }
  collapsedSetMenuOpen.value = !collapsedSetMenuOpen.value;
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
  const includeAttachments =
    !isReadOnly.value || Boolean(sessionContext.value?.include_attachments);
  let url = `${props.backendUrl}/projects/${project.id}/export`;
  if (!includeAttachments) {
    url += "?include_attachments=false";
  }
  url = appendShareToken(url);
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

async function deleteProjectById(project) {
  if (
    !window.confirm(
      `Delete project "${project.name}"? This will remove all its people, sets, and attachments.`,
    )
  )
    return;
  try {
    await apiClient.delete(`${props.backendUrl}/projects/${project.id}`);
    await projectDeleted(project.id);
  } catch (e) {
    alert("Failed to delete project: " + (e.message || e));
  }
}

const sortedProjects = computed(() =>
  [...projects.value].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
  ),
);

// Auto-expand projects the first time they appear in the tree.
// This keeps all projects open by default without preventing the user from
// manually collapsing them.
const _seenProjectIds = new Set();
watch(
  () => sortedProjects.value.map((p) => p.id),
  (ids) => {
    const newIds = ids.filter((id) => !_seenProjectIds.has(id));
    if (newIds.length === 0) return;
    newIds.forEach((id) => _seenProjectIds.add(id));
    expandedProjectIds.value = new Set([
      ...expandedProjectIds.value,
      ...newIds,
    ]);
  },
  { immediate: true },
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

// When the session is scoped to a specific resource type via a share token,
// this reflects that type ('character', 'picture_set', 'project') or null.
const scopedResourceType = computed(() =>
  sessionContext.value?.scope === "READ"
    ? (sessionContext.value?.resource_type ?? null)
    : null,
);

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
  return sortedCharacters.value
    .filter((c) => c.has_reference_faces === true)
    .map((c) => ({
      text: c.name,
      value: c.id,
      thumbnail: characterThumbnails.value?.[c.id] || null,
    }));
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
  get: () => props.sidebarThumbnailSize ?? 48,
  set: (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(64, Math.max(16, parsed));
    const snapped = Math.round(clamped / 4) * 4;
    emit("update:sidebar-thumbnail-size", snapped);
  },
});

// Dock layout: how many rows of chars/sets can fit in the available scroll height.
// setsCollapsed: collapse sets to a single flyout button first.
// charsCollapsed: also collapse chars if sets-as-menu still doesn't free enough space.
const _dockRowH = computed(() => sidebarThumbnailSizeModel.value + 4);
const _DOCK_DIV = 3; // divider height (1px + 2px margins)
const _addBtn = computed(() => (isReadOnly.value ? 0 : 1)); // extra [+] row when editable
const setsCollapsed = computed(() => {
  if (!props.docked || sidebarPrimaryTab.value === "folders") return false;
  const h = _dockRowH.value;
  const charCount = visibleCharacters.value.length;
  const setCount = visibleSets.value.length;
  if (!setCount || dockedScrollHeight.value === 0) return false;
  const fixedH = 2 * h + _DOCK_DIV; // allPictures + scrapheap + divider after allPictures
  const charH = (charCount + _addBtn.value) * h; // always include [+] row when editable
  const setDividerH = _DOCK_DIV;
  const allSetsH = (setCount + _addBtn.value) * h;
  return fixedH + charH + setDividerH + allSetsH > dockedScrollHeight.value;
});
const charsCollapsed = computed(() => {
  if (!props.docked || sidebarPrimaryTab.value === "folders") return false;
  if (!setsCollapsed.value) return false;
  const h = _dockRowH.value;
  const charCount = visibleCharacters.value.length;
  const setCount = visibleSets.value.length;
  if (!charCount || dockedScrollHeight.value === 0) return false;
  const fixedH = 2 * h + _DOCK_DIV;
  const charH = (charCount + _addBtn.value) * h;
  const setDividerH = setCount > 0 ? _DOCK_DIV : 0;
  const collapsedSetH = setCount > 0 ? h : 0; // sets become 1 button
  return (
    fixedH + charH + setDividerH + collapsedSetH > dockedScrollHeight.value
  );
});

const sidebarFolderRootIconSize = computed(() =>
  Math.round(sidebarThumbnailSizeModel.value * 0.75),
);
const sidebarFolderChildIconSize = computed(() =>
  Math.round(sidebarThumbnailSizeModel.value * 0.5),
);

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
    const numId = Number(id);
    const singleChar = isSpecial
      ? null
      : characters.value.find((c) => c.id === numId);
    // Compute the full project context so App.vue can apply everything
    // atomically without relying on any pre-existing store state.
    let projectContext;
    if (id === props.allPicturesId) {
      // "All Pictures" keeps whatever project scope was active when clicked.
      projectContext = {
        mode: projectViewMode.value,
        projectId: selectedProjectId.value ?? null,
      };
    } else if (isSpecial) {
      // Scrapheap / Unassigned are always outside a project.
      projectContext = { mode: "global", projectId: null };
    } else {
      const charProjectId = singleChar?.project_id ?? null;
      // In the global tab, never push a project-scoped route even if the
      // character belongs to a project.  Only the project tab should do that.
      projectContext = {
        mode:
          projectViewMode.value === "project" && charProjectId != null
            ? "project"
            : "global",
        projectId: projectViewMode.value === "project" ? charProjectId : null,
      };
    }
    emit("select-set", null);
    emit("select-character", {
      id,
      label,
      ids: isSpecial ? [] : [numId],
      projectIds: singleChar ? { [numId]: singleChar.project_id ?? null } : {},
      projectContext,
    });
    return;
  }

  // Ctrl/Cmd-click: toggle this character in the multi-selection
  const numericId = Number(id);
  const currentIds = new Set(selectedCharacterIdSet.value);

  if (currentIds.size === 0) {
    // Nothing selected yet — treat as plain click
    const singleChar0 = characters.value.find((c) => c.id === numericId);
    const charProjectId0 = singleChar0?.project_id ?? null;
    emit("select-set", null);
    emit("select-character", {
      id,
      label,
      ids: [numericId],
      projectIds: singleChar0 ? { [numericId]: charProjectId0 } : {},
      projectContext: {
        mode:
          projectViewMode.value === "project" && charProjectId0 != null
            ? "project"
            : "global",
        projectId: projectViewMode.value === "project" ? charProjectId0 : null,
      },
    });
    return;
  }

  if (currentIds.has(numericId)) {
    currentIds.delete(numericId);
  } else {
    currentIds.add(numericId);
  }

  const nextIds = Array.from(currentIds).sort((a, b) => a - b);
  if (!nextIds.length) {
    emit("select-character", {
      id: props.allPicturesId,
      label: null,
      ids: [],
      projectIds: {},
    });
    return;
  }

  // Keep the primary view unchanged on ctrl-click
  const primaryId = props.selectedCharacter ?? nextIds[0];
  const multiProjectIds = {};
  for (const cid of nextIds) {
    const c = characters.value.find((ch) => ch.id === cid);
    multiProjectIds[cid] = c?.project_id ?? null;
  }
  emit("select-character", {
    id: primaryId,
    label: null,
    ids: nextIds,
    projectIds: multiProjectIds,
  });
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
    const singleSet = pictureSets.value.find((s) => s.id === numericSetId);
    const setProjectId = singleSet?.project_id ?? null;
    // In the global tab, never push a project-scoped route even if the
    // set belongs to a project.  Only the project tab should do that.
    emit("select-set", {
      id: numericSetId,
      label,
      ids: [numericSetId],
      names: { [numericSetId]: label || String(numericSetId) },
      projectIds: { [numericSetId]: setProjectId },
      projectContext: {
        mode:
          projectViewMode.value === "project" && setProjectId != null
            ? "project"
            : "global",
        projectId: projectViewMode.value === "project" ? setProjectId : null,
      },
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
  const setProjectIds = {};
  for (const sid of ids) {
    const found = pictureSets.value.find((p) => p.id === sid);
    if (found) {
      setNames[sid] = found.name;
      setProjectIds[sid] = found.project_id ?? null;
    }
  }
  emit("select-set", {
    id: ids[0],
    label: primarySet?.name || label,
    ids,
    names: setNames,
    projectIds: setProjectIds,
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
  if (isReadOnly.value && (type === "folder" || type === "import-folder"))
    return;
  if (type === "character") {
    sidebarCtxCharacter.value = item;
    sidebarCtxSet.value = null;
    sidebarCtxFolder.value = null;
    sidebarCtxImportFolder.value = null;
    sidebarCtxProject.value = null;
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
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = false;
    } else if (type === "folder") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = item;
      sidebarCtxImportFolder.value = null;
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = false;
    } else if (type === "import-folder") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = null;
      sidebarCtxImportFolder.value = item;
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = false;
    } else if (type === "project") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = null;
      sidebarCtxImportFolder.value = null;
      sidebarCtxProject.value = item;
      sidebarCtxAllPictures.value = false;
    } else if (type === "all-pictures") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = null;
      sidebarCtxImportFolder.value = null;
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = true;
    }
  }
  sidebarCtxX.value = event.clientX;
  sidebarCtxY.value = event.clientY;
  sidebarCtxVisible.value = true;
}

function closeSidebarCtxMenu() {
  sidebarCtxVisible.value = false;
  setCtxIconMenuOpen.value = false;
  setCtxColorMenuOpen.value = false;
}

function openSetCtxIconMenu(event) {
  setCtxColorMenuOpen.value = false;
  const rect = event.currentTarget.getBoundingClientRect();
  const PANEL_W = 260;
  const PANEL_H = 500;
  const left = Math.max(
    0,
    Math.min(rect.right + 4, window.innerWidth - PANEL_W - 8),
  );
  if (rect.top + PANEL_H > window.innerHeight - 8) {
    setCtxAppearanceMenuPos.value = {
      left,
      openUp: true,
      bottom: window.innerHeight - rect.bottom,
    };
  } else {
    setCtxAppearanceMenuPos.value = { left, openUp: false, top: rect.top };
  }
  setCtxIconMenuOpen.value = true;
}

function openSetCtxColorMenu(event) {
  setCtxIconMenuOpen.value = false;
  const rect = event.currentTarget.getBoundingClientRect();
  const PANEL_W = 200;
  const PANEL_H = 270;
  const left = Math.max(
    0,
    Math.min(rect.right + 4, window.innerWidth - PANEL_W - 8),
  );
  if (rect.top + PANEL_H > window.innerHeight - 8) {
    setCtxAppearanceMenuPos.value = {
      left,
      openUp: true,
      bottom: window.innerHeight - rect.bottom,
    };
  } else {
    setCtxAppearanceMenuPos.value = { left, openUp: false, top: rect.top };
  }
  setCtxColorMenuOpen.value = true;
}

async function applySetAppearance(setId, icon, color) {
  setCtxIconMenuOpen.value = false;
  setCtxColorMenuOpen.value = false;
  const payload = {};
  if (icon !== null) payload.set_icon = icon;
  if (color !== null) payload.set_color = color;
  try {
    await apiClient.patch(`${props.backendUrl}/picture_sets/${setId}`, payload);
    refreshSidebar();
  } catch (e) {
    console.error("Failed to update set appearance", e);
  }
  closeSidebarCtxMenu();
}

async function shareResource(resourceType, resourceId, label) {
  closeSidebarCtxMenu();
  shareDialogPending.value = { resourceType, resourceId, label };
  shareDialogOpen.value = true;
}

function createCharacter() {
  // Find the next available unique name in the format "Character 0001"
  const existingCharacters = Array.isArray(characters.value)
    ? characters.value
    : [];
  const existingNames = new Set(existingCharacters.map((c) => c.name));
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
        const characterSummaryParams =
          projectViewMode.value === "project"
            ? {
                project_id:
                  char.project_id != null ? char.project_id : "UNASSIGNED",
              }
            : null;
        const res = await apiClient.get(
          `${props.backendUrl}/characters/${char.id}/summary`,
          characterSummaryParams
            ? { params: characterSummaryParams }
            : undefined,
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
    const nextCharacters = Array.isArray(chars) ? chars : [];
    if (!Array.isArray(chars)) {
      console.warn("Unexpected /characters response; expected an array:", chars);
    }
    characters.value = nextCharacters;
    entityNames.mergeCharacterNames(nextCharacters);
    for (const char of nextCharacters) {
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
  fetchSharedIds();
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

    const payload = res.data;
    const options = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.sort_mechanisms)
        ? payload.sort_mechanisms
        : Array.isArray(payload?.options)
          ? payload.options
          : [];

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
  // A token scoped to a non-project resource cannot access the projects list.
  if (
    isReadOnly.value &&
    sessionContext.value?.resource_type != null &&
    sessionContext.value.resource_type !== "project"
  ) {
    projects.value = [];
    return;
  }
  try {
    const res = await apiClient.get(`${props.backendUrl}/projects`);
    projects.value = Array.isArray(res.data) ? res.data : [];
    entityNames.mergeProjectNames(projects.value);
  } catch (e) {
    console.error("Error fetching projects:", e);
    projects.value = [];
  }
}

async function fetchPictureSets() {
  try {
    // Always fetch all sets — in the flat project tree each project filters
    // its own sets client-side, so we must not scope this call to a single project.
    const setUrl = `${props.backendUrl}/picture_sets`;
    const res = await apiClient.get(setUrl);

    const sets = await res.data; // Axios responses use `data` for the payload
    pictureSets.value = Array.isArray(sets) ? [...sets] : [];
    entityNames.mergeSetNames(pictureSets.value);
    await updateSetThumbnails(pictureSets.value);
  } catch (e) {
    console.error("Error fetching picture sets:", e);
    pictureSets.value = [...pictureSets.value]; // force reactivity on error
  }
}

async function fetchSharedIds() {
  if (isReadOnly.value) return; // only owner can query tokens
  try {
    const [charRes, setRes, projRes] = await Promise.all([
      apiClient.get("/users/me/shared-resource-ids?resource_type=character"),
      apiClient.get("/users/me/shared-resource-ids?resource_type=picture_set"),
      apiClient.get("/users/me/shared-resource-ids?resource_type=project"),
    ]);
    sharedCharacterIds.value = new Set(charRes.data?.ids ?? []);
    sharedSetIds.value = new Set(setRes.data?.ids ?? []);
    sharedProjectIds.value = new Set(projRes.data?.ids ?? []);
  } catch (e) {
    console.warn("[SideBar] fetchSharedIds error:", e);
  }
}

async function revokeAllShares(resourceType, resourceId) {
  try {
    await apiClient.delete(
      `/users/me/tokens/by-resource?resource_type=${encodeURIComponent(resourceType)}&resource_id=${encodeURIComponent(resourceId)}`,
    );
    // Remove from local set so icon disappears immediately
    if (resourceType === "character")
      sharedCharacterIds.value.delete(resourceId);
    else if (resourceType === "picture_set")
      sharedSetIds.value.delete(resourceId);
    else if (resourceType === "project")
      sharedProjectIds.value.delete(resourceId);
    // Trigger reactivity
    sharedCharacterIds.value = new Set(sharedCharacterIds.value);
    sharedSetIds.value = new Set(sharedSetIds.value);
    sharedProjectIds.value = new Set(sharedProjectIds.value);
  } catch (e) {
    console.error("[SideBar] revokeAllShares error:", e);
  }
}

function openRevokeSharesDialog(resourceType, resourceId, label) {
  revokeSharesPending.value = { resourceType, resourceId, label };
  revokeSharesDialogOpen.value = true;
  closeSidebarCtxMenu();
}

async function confirmRevokeShares() {
  if (!revokeSharesPending.value) return;
  const { resourceType, resourceId } = revokeSharesPending.value;
  revokeSharesDialogOpen.value = false;
  revokeSharesPending.value = null;
  await revokeAllShares(resourceType, resourceId);
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
    const nextUrl = appendShareToken(
      `${url}?v=${encodeURIComponent(versionKey)}`,
    );
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

// --- Project drop target (assign dragged pictures to a specific project) ---
const dragOverProjectId = ref(null);

function handleDragOverProject(id) {
  dragOverProjectId.value = id;
}

function handleDragLeaveProject() {
  dragOverProjectId.value = null;
}

async function onProjectDrop(projectId, event) {
  dragOverProjectId.value = null;
  if (projectId == null) return;
  // Internal grid drag carries the selected image ids as application/json.
  let imageIds = [];
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (Array.isArray(data.imageIds)) imageIds = data.imageIds;
  } catch (e) {
    console.error("Could not parse drag data for project drop:", e);
    return;
  }
  if (!imageIds.length) return;
  try {
    // Picture↔Project is many-to-many (PictureProjectMember); membership is
    // created via the batch /pictures/project endpoint with mode "add".
    // (Patching a picture's direct project_id column does NOT create the
    // membership the project view queries — that returns 200 but shows nothing.)
    await apiClient.patch(`${props.backendUrl}/pictures/project`, {
      picture_ids: imageIds,
      project_id: projectId,
      mode: "add",
    });
    emit("images-moved", { imageIds });
  } catch (e) {
    console.error("Failed to assign pictures to project:", e);
  }
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

const VERSION_CHECK_STORAGE_KEY = "pixlstash:lastVersionCheck";
const VERSION_CHECK_SECURITY_KEY = "pixlstash:lastSecurityLevel";
const VERSION_CHECK_DISMISSED_KEY = "pixlstash:dismissedUpdateVersion";
const VERSION_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;

const updateDismissed = ref(
  localStorage.getItem(VERSION_CHECK_DISMISSED_KEY) === latestVersion.value,
);

function dismissUpdateAlert() {
  localStorage.setItem(VERSION_CHECK_DISMISSED_KEY, latestVersion.value);
  updateDismissed.value = true;
}

function checkForUpdatesNow() {
  const last = parseInt(
    localStorage.getItem(VERSION_CHECK_STORAGE_KEY) ?? "0",
    10,
  );
  const lastSecurity = localStorage.getItem(VERSION_CHECK_SECURITY_KEY) ?? "";
  const isHighSecurity = ["critical", "high"].includes(
    lastSecurity.toLowerCase(),
  );
  // Bypass the 24h throttle when the last known release was a High/Critical
  // security patch so it re-checks (and re-shows) on every page load.
  if (Date.now() - last < VERSION_CHECK_INTERVAL_MS && !isHighSecurity) return;

  const url = `${LATEST_VERSION_URL}?v=${encodeURIComponent(appVersion)}&i=${encodeURIComponent(props.installType ?? "pip")}`;
  fetch(url)
    .then((r) => r.json())
    .then((data) => {
      localStorage.setItem(VERSION_CHECK_STORAGE_KEY, String(Date.now()));
      localStorage.setItem(VERSION_CHECK_SECURITY_KEY, data?.security ?? "");
      const remote = data?.version;
      if (remote && isRemoteNewer(appVersion, remote)) {
        const dismissed = localStorage.getItem(VERSION_CHECK_DISMISSED_KEY);
        latestVersion.value = remote;
        latestVersionUrl.value = `${UPDATE_PAGE_URL}?v=${encodeURIComponent(appVersion)}&i=${encodeURIComponent(props.installType ?? "pip")}`;
        latestSecurityLevel.value = data?.security ?? null;
        updateDismissed.value = dismissed === remote;
      }
    })
    .catch(() => {});
}

let versionCheckInterval = null;

function startVersionCheckInterval() {
  if (versionCheckInterval) return;
  versionCheckInterval = setInterval(() => {
    if (props.checkForUpdates === true) {
      checkForUpdatesNow();
    }
  }, VERSION_CHECK_INTERVAL_MS);
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
  // When the session is scoped to a project via a share token, initialise
  // SideBar's internal project view state before any data is fetched.
  // This path DOES emit so App.vue can push the correct route.
  const isProjectShareToken =
    scopedResourceType.value === "project" &&
    sessionContext.value?.resource_id != null;
  if (isProjectShareToken) {
    projectViewMode.value = "project";
    selectedProjectId.value = sessionContext.value.resource_id;
  } else {
    // Restore project state from the current route on page load.  App.vue's
    // applyRouteToStores() runs (via an immediate watcher) before this
    // component is created, so the externalProjectViewMode/Id props already
    // reflect the correct route when we reach this point.
    // _initializing suppresses the watchers so this one-time restore does NOT
    // emit navigation events back to App.vue.
    if (
      props.externalProjectViewMode != null ||
      props.externalSelectedProjectId != null
    ) {
      _initializing = true;
      if (props.externalProjectViewMode != null)
        projectViewMode.value = props.externalProjectViewMode;
      if (props.externalSelectedProjectId != null) {
        lastUsedProjectId.value = props.externalSelectedProjectId;
        selectedProjectId.value = props.externalSelectedProjectId;
      }
      nextTick(() => {
        _initializing = false;
      });
    }
  }

  // Track scroll area height for adaptive dock layout.
  _dockedScrollObserver = new ResizeObserver((entries) => {
    for (const entry of entries) {
      dockedScrollHeight.value = entry.contentRect.height;
    }
  });
  if (dockedScrollRef.value) {
    _dockedScrollObserver.observe(dockedScrollRef.value);
  }

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
        collapsedProjectMenuRef.value.contains(e.target)) ||
      (collapsedProjectSubMenuRef.value &&
        collapsedProjectSubMenuRef.value.contains(e.target))
    ) {
      return;
    }
    projectMenuOpen.value = false;
    if (
      collapsedCharMenuRef.value &&
      !collapsedCharMenuRef.value.contains(e.target) &&
      !(
        collapsedCharBtnRef.value &&
        collapsedCharBtnRef.value.contains(e.target)
      )
    ) {
      collapsedCharMenuOpen.value = false;
    }
    if (
      collapsedSetMenuRef.value &&
      !collapsedSetMenuRef.value.contains(e.target) &&
      !(collapsedSetBtnRef.value && collapsedSetBtnRef.value.contains(e.target))
    ) {
      collapsedSetMenuOpen.value = false;
    }
    const inCharMenu = e.target.closest(".sidebar-move-menu");
    const inCharBtn = e.target.closest(".sidebar-move-to-project-wrap");
    if (!inCharBtn && !inCharMenu) {
      characterMoveMenuOpen.value = false;
    }
    if (!inCharBtn && !inCharMenu) {
      setMoveMenuOpen.value = false;
    }
    // Close icon/color appearance sub-menus when clicking outside
    const inAppearancePanel = e.target.closest(".sidebar-ctx-appearance-panel");
    const inCtxMenu = e.target.closest(".sidebar-ctx-menu");
    if (!inAppearancePanel && !inCtxMenu) {
      setCtxIconMenuOpen.value = false;
      setCtxColorMenuOpen.value = false;
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
  if (event.target.closest(".sidebar-ctx-appearance-panel")) return;
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
let _dockedScrollObserver = null;
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
  if (_dockedScrollObserver) {
    _dockedScrollObserver.disconnect();
    _dockedScrollObserver = null;
  }
});

// Close flyout menus when their section switches from menu to individual rows.
watch(charsCollapsed, (collapsed) => {
  if (!collapsed) collapsedCharMenuOpen.value = false;
});
watch(setsCollapsed, (collapsed) => {
  if (!collapsed) collapsedSetMenuOpen.value = false;
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

// Set to true during onMounted route-state restoration so the watchers below
// do not emit navigation events back to App.vue for the one-time page-load
// restore.  Reset to false via nextTick() after the flush cycle completes.
let _initializing = false;

watch(projectViewMode, () => {
  if (_initializing) return;
  // Stateless tabs: switching the Global ↔ Project mode is a sidebar-display
  // operation only. It changes which list of entries the sidebar renders but
  // must NOT touch the grid — the grid view follows the route (the single
  // source of truth), driven only by explicit entry clicks. We therefore do
  // NOT emit update:project-view-mode here. Re-fetching the sets is purely to
  // populate the sidebar's own scoped list (all sets in global, project-scoped
  // sets in project view).
  void fetchPictureSets();
  void fetchSidebarData();
});
watch(selectedProjectId, (v) => {
  if (_initializing) return;
  // Display-only (see watch(projectViewMode) above): no emit to App.
  // Navigation to a project happens via the explicit `view-project`
  // entry-click event, not by changing the sidebar's scope here.
  if (v !== null) lastUsedProjectId.value = v;
  // Re-fetch sets for the newly selected project (sidebar list scope).
  void fetchPictureSets();
  void fetchSidebarData();
});

// Keep the sidebar's current-project in sync with the route (the single source
// of truth). Navigating to a project via the breadcrumb / deep-link / browser
// back-forward updates externalSelectedProjectId; without mirroring it here the
// sidebar's project highlight + scope would stay on the last project that was
// selected *in the sidebar*. The init block already seeds this once on mount;
// this handles every subsequent route change. (Switching the Projects tab does
// NOT change the prop, so the stateless browse-scope is preserved.)
watch(
  () => props.externalSelectedProjectId,
  (v) => {
    if (_initializing) return;
    const next = v ?? null;
    if (next !== selectedProjectId.value) selectedProjectId.value = next;
  },
);

// Sync the sidebar's folder highlight with the active route.
// When App.vue navigates to /ref-folder/:id or /import-folder/:id it passes
// the matching key via the activeFolderKey prop so we can switch to the
// folders tab and emit the correct filter payload.
watch(
  () => props.activeFolderKey,
  async (newKey, oldKey) => {
    if (!newKey) {
      // Route left a folder view — clear the sidebar's folder highlight.
      if (oldKey && selectedFolderKey.value === oldKey) {
        selectedFolderKey.value = null;
        selectedFolderReferenceId.value = null;
      }
      return;
    }
    if (selectedFolderKey.value === newKey) return; // already in sync

    sidebarPrimaryTab.value = "folders";
    await fetchReferenceFolders();
    await fetchImportFolders();

    // Guard: user may have navigated away while fetches were in flight.
    if (props.activeFolderKey !== newKey) return;

    if (newKey.startsWith("rf-")) {
      const id = parseInt(newKey.slice(3), 10);
      const folder = referenceFolders.value.find((f) => f.id === id);
      if (folder) {
        handleFolderNodeSelect(newKey, {
          referenceFolderId: folder.id,
          pathPrefix: folder.folder,
          label: folder.label || folder.folder,
        });
      }
    } else if (newKey.startsWith("if-")) {
      const id = parseInt(newKey.slice(3), 10);
      const folder = importFolders.value.find((f) => f.id === id);
      if (folder) {
        handleFolderNodeSelect(newKey, {
          importSourceFolder: folder.folder,
          importFolderId: folder.id,
          label: folder.label || folder.folder,
        });
      }
    }
  },
  { immediate: true },
);

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
    // Many-to-many membership via the batch endpoint (see onProjectDrop).
    await apiClient.patch(`${props.backendUrl}/pictures/project`, {
      picture_ids: draggedIds,
      project_id: selectedProjectId.value,
      mode: "add",
    });
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
    :thumbnailUrl="
      setEditorSet ? (setThumbnails[setEditorSet.id] ?? null) : null
    "
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
    @update:public-url="(value) => emit('update:public-url', value)"
    @update:check-for-updates="
      (value) => emit('update:check-for-updates', value)
    "
  />
  <FolderEditor
    type="reference"
    :open="referenceFolderEditorOpen"
    :folder="referenceFolderEditorFolder"
    :in-docker="inDocker"
    :docker-variant="props.dockerVariant"
    :registered-paths="registeredFolderPaths"
    :registered-folders="referenceFolders"
    :registered-sibling-folders="importFolders"
    :image-root="referenceFoldersImageRoot"
    @close="closeReferenceFolderEditor"
    @saved="referenceFolderSaved"
    @deleted="referenceFolderDeleted"
  />
  <FolderEditor
    type="import"
    :open="importFolderEditorOpen"
    :folder="importFolderEditorFolder"
    :in-docker="inDocker"
    :docker-variant="props.dockerVariant"
    :registered-paths="registeredImportFolderPaths"
    :registered-folders="importFolders"
    :registered-sibling-folders="referenceFolders"
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

  <aside
    ref="sidebarRootRef"
    class="sidebar"
    :class="{ 'sidebar-docked': props.docked }"
    :style="sidebarThumbStyle"
  >
    <div class="sidebar-brand">
      <div class="sidebar-brand-left">
        <a
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
        <div v-if="!props.docked" class="sidebar-brand-text">
          <span class="sidebar-brand-title">PixlStash</span>
          <div
            v-if="updateAvailable && !updateDismissed"
            class="sidebar-update-wrapper"
          >
            <a
              :href="latestVersionUrl"
              target="_blank"
              rel="noopener noreferrer"
              :class="securityUpdateClass"
              :title="securityUpdateTitle"
              >&#x2191; v{{ latestVersion
              }}{{
                latestSecurityLevel ? " security \u26a0\ufe0f" : " available"
              }}</a
            ><button
              class="sidebar-update-dismiss"
              :title="`Dismiss v${latestVersion} update alert`"
              @click.prevent="dismissUpdateAlert"
            >
              &times;
            </button>
          </div>
        </div>
      </div>
      <button
        class="sidebar-brand-toggle"
        :title="props.docked ? 'Switch to full sidebar' : 'Switch to dock'"
        @click.stop="emit('toggle-dock')"
      >
        <v-icon>{{
          props.docked ? "mdi-chevron-right" : "mdi-chevron-left"
        }}</v-icon>
      </button>
    </div>
    <div v-if="props.docked" class="sidebar-collapsed-divider"></div>
    <div
      v-if="props.docked"
      class="sidebar-collapsed-project-wrap"
      ref="projectMenuRef"
    >
      <div
        class="sidebar-collapsed-row sidebar-collapsed-row--has-flyout sidebar-collapsed-row--project"
      >
        <div
          class="sidebar-collapsed-item sidebar-collapsed-item--has-flyout"
          style="margin: 0 auto"
          :title="collapsedProjectBtnTitle"
          ref="collapsedProjectBtnRef"
          @click.stop="toggleProjectMenu"
        >
          <v-icon size="20">{{
            sidebarPrimaryTab === "folders"
              ? "mdi-folder-network-outline"
              : projectViewMode === "global"
                ? "mdi-earth"
                : "mdi-folder-outline"
          }}</v-icon>
        </div>
      </div>
      <Teleport to="body">
        <div
          v-if="projectMenuOpen && props.docked"
          ref="collapsedProjectMenuRef"
          class="sidebar-collapsed-project-menu"
          :style="{
            top: collapsedProjectMenuPos.top + 'px',
            left: collapsedProjectMenuPos.left + 'px',
          }"
          @mouseleave="scheduleCloseProjectSubMenu"
        >
          <!-- Global -->
          <div
            class="sidebar-project-menu-item"
            :class="{
              active:
                projectViewMode === 'global' && sidebarPrimaryTab !== 'folders',
            }"
            @mouseenter="scheduleCloseProjectSubMenu"
            @click="
              selectLibraryTab('global');
              selectCharacter(props.allPicturesId, 'All Pictures');
              projectMenuOpen = false;
            "
          >
            <v-icon size="14">mdi-earth</v-icon>
            <span class="sidebar-project-menu-item-label">Global</span>
          </div>

          <div class="sidebar-project-menu-separator"></div>

          <!-- Projects row → flyout submenu on hover or click -->
          <div
            class="sidebar-project-menu-item sidebar-project-menu-has-sub"
            :class="{
              active:
                projectViewMode === 'project' &&
                sidebarPrimaryTab !== 'folders',
              'sub-open': projectMenuSection === 'projects',
            }"
            @mouseenter="openProjectSubMenu('projects', $event)"
            @click.stop="openProjectSubMenu('projects', $event)"
          >
            <v-icon size="14">mdi-folder-outline</v-icon>
            <span class="sidebar-project-menu-item-label">Projects</span>
            <v-icon size="12" class="sidebar-project-menu-chevron"
              >mdi-chevron-right</v-icon
            >
          </div>

          <!-- Folders row → flyout submenu on hover or click -->
          <div
            class="sidebar-project-menu-item sidebar-project-menu-has-sub"
            :class="{
              active: sidebarPrimaryTab === 'folders',
              'sub-open': projectMenuSection === 'folders',
            }"
            @mouseenter="openProjectSubMenu('folders', $event)"
            @click.stop="openProjectSubMenu('folders', $event)"
          >
            <v-icon size="14">mdi-folder-network-outline</v-icon>
            <span class="sidebar-project-menu-item-label">Folders</span>
            <v-icon size="12" class="sidebar-project-menu-chevron"
              >mdi-chevron-right</v-icon
            >
          </div>
        </div>
      </Teleport>

      <!-- Flyout submenu -->
      <Teleport to="body">
        <div
          v-if="projectMenuSection && projectMenuOpen && props.docked"
          ref="collapsedProjectSubMenuRef"
          class="sidebar-collapsed-project-submenu"
          :style="{
            top: projectMenuSubPos.top + 'px',
            left: projectMenuSubPos.left + 'px',
          }"
          @mouseenter="cancelCloseProjectSubMenu"
          @mouseleave="scheduleCloseProjectSubMenu"
        >
          <!-- Projects submenu -->
          <template v-if="projectMenuSection === 'projects'">
            <div
              v-if="!isReadOnly"
              class="sidebar-project-menu-item sidebar-project-menu-add"
              @click="
                createProject();
                projectMenuSection = null;
              "
            >
              <v-icon size="14">mdi-plus</v-icon>
              <span class="sidebar-project-menu-item-label"
                >Add new project</span
              >
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
                selectLibraryTab('project');
                selectProjectNode(p);
                projectMenuSection = null;
              "
            >
              <v-icon size="14">mdi-folder</v-icon>
              <span class="sidebar-project-menu-item-label">{{ p.name }}</span>
            </div>
          </template>

          <!-- Folders submenu -->
          <template v-if="projectMenuSection === 'folders'">
            <div
              v-if="!isReadOnly"
              class="sidebar-project-menu-item sidebar-project-menu-add"
              @click="
                openAddFolderTypeDialog();
                projectMenuOpen = false;
                projectMenuSection = null;
              "
            >
              <v-icon size="14">mdi-plus</v-icon>
              <span class="sidebar-project-menu-item-label">Add folder</span>
            </div>
            <div
              v-if="referenceFolders.length"
              class="sidebar-project-menu-section-label"
            >
              Reference Folders
            </div>
            <div
              v-for="rf in referenceFolders"
              :key="'rf-' + rf.id"
              class="sidebar-project-menu-item"
              :class="{
                active:
                  sidebarPrimaryTab === 'folders' &&
                  selectedFolderKey === 'rf-' + rf.id,
              }"
              @click="
                selectFoldersTab();
                handleFolderNodeSelect('rf-' + rf.id, {
                  referenceFolderId: rf.id,
                  pathPrefix: rf.folder,
                  label: rf.label || rf.folder,
                });
                projectMenuOpen = false;
                projectMenuSection = null;
              "
            >
              <v-icon size="14">mdi-folder-network-outline</v-icon>
              <span class="sidebar-project-menu-item-label">{{
                rf.label || rf.folder
              }}</span>
            </div>
            <div
              v-if="importFolders.length"
              class="sidebar-project-menu-section-label"
            >
              Import Folders
            </div>
            <div
              v-for="imf in importFolders"
              :key="'if-' + imf.id"
              class="sidebar-project-menu-item"
              :class="{
                active:
                  sidebarPrimaryTab === 'folders' &&
                  selectedFolderKey === 'if-' + imf.id,
              }"
              @click="
                selectFoldersTab();
                handleFolderNodeSelect('if-' + imf.id, {
                  importSourceFolder: imf.folder,
                  importFolderId: imf.id,
                  label: imf.label || imf.folder,
                });
                projectMenuOpen = false;
                projectMenuSection = null;
              "
            >
              <v-icon size="14">mdi-folder-open-outline</v-icon>
              <span class="sidebar-project-menu-item-label">{{
                imf.label || imf.folder
              }}</span>
            </div>
          </template>
        </div>
      </Teleport>
    </div>
    <div v-if="props.docked" class="sidebar-collapsed-divider"></div>
    <div v-else-if="!scopedResourceType" class="sidebar-view-tabs-row">
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
          v-if="!isReadOnly"
          class="sidebar-view-tab"
          :class="{ active: sidebarPrimaryTab === 'folders' }"
          @click="selectFoldersTab()"
        >
          <v-icon size="14">mdi-folder-network-outline</v-icon>
          Folders
        </button>
      </div>
    </div>
    <div class="sidebar-scroll" ref="dockedScrollRef">
      <template v-if="props.docked">
        <div class="sidebar-collapsed-list">
          <div
            v-if="sidebarPrimaryTab !== 'folders'"
            :class="[
              'sidebar-collapsed-row',
              { active: isAllPicturesRowActive },
            ]"
          >
            <div
              :class="[
                'sidebar-collapsed-item',
                { active: isAllPicturesRowActive },
              ]"
              title="All Pictures"
              @click="selectCharacter(props.allPicturesId, 'All Pictures')"
            >
              <v-icon>mdi-image-multiple</v-icon>
            </div>
          </div>
          <div
            v-if="sidebarPrimaryTab !== 'folders'"
            class="sidebar-collapsed-divider"
          ></div>

          <!-- Characters: individual dock buttons when space allows, flyout menu when space is tight -->
          <template
            v-if="
              visibleCharacters.length &&
              sidebarPrimaryTab !== 'folders' &&
              !charsCollapsed
            "
          >
            <div
              v-for="char in visibleCharacters"
              :key="char.id"
              :class="[
                'sidebar-collapsed-row',
                {
                  active:
                    props.selectedCharacter === char.id &&
                    !props.hasFolderFilter,
                },
              ]"
            >
              <div
                :class="[
                  'sidebar-collapsed-item',
                  {
                    active:
                      props.selectedCharacter === char.id &&
                      !props.hasFolderFilter,
                  },
                ]"
                :title="`${char.name || 'Character'} (Ctrl/Cmd + click to multi-select)`"
                @click="selectCharacter(char.id, char.name || 'Character', $event)"
                @contextmenu.prevent="
                  openSidebarCtxMenu('character', char, $event)
                "
              >
                <img
                  v-if="characterThumbnails[char.id]"
                  :src="characterThumbnails[char.id]"
                  alt=""
                  :width="sidebarThumbnailSizeModel"
                  :height="sidebarThumbnailSizeModel"
                  class="sidebar-character-thumb"
                />
                <v-icon v-else>mdi-account</v-icon>
              </div>
            </div>
            <div v-if="!isReadOnly" class="sidebar-collapsed-row">
              <div
                class="sidebar-collapsed-item sidebar-collapsed-item--add sidebar-collapsed-item--add-person"
                title="Add person"
                @click="createCharacter()"
              >
                <i
                  class="mdi mdi-account sidebar-collapsed-item--add-bg-icon"
                  aria-hidden="true"
                ></i>
                <v-icon class="sidebar-collapsed-item--add-plus"
                  >mdi-plus</v-icon
                >
              </div>
            </div>
          </template>
          <div
            v-else-if="
              !visibleCharacters.length &&
              !isReadOnly &&
              sidebarPrimaryTab !== 'folders'
            "
            class="sidebar-collapsed-row"
          >
            <div
              class="sidebar-collapsed-item sidebar-collapsed-item--add sidebar-collapsed-item--add-person"
              title="Add person"
              @click="createCharacter()"
            >
              <i
                class="mdi mdi-account sidebar-collapsed-item--add-bg-icon"
                aria-hidden="true"
              ></i>
              <v-icon class="sidebar-collapsed-item--add-plus">mdi-plus</v-icon>
            </div>
          </div>
          <div
            v-else-if="
              visibleCharacters.length && sidebarPrimaryTab !== 'folders'
            "
            :class="[
              'sidebar-collapsed-row',
              'sidebar-collapsed-row--has-flyout',
              {
                active:
                  selectedCharacterIdSet.size > 0 && !props.hasFolderFilter,
              },
            ]"
          >
            <div
              :class="[
                'sidebar-collapsed-item',
                'sidebar-collapsed-item--has-flyout',
                {
                  active:
                    selectedCharacterIdSet.size > 0 && !props.hasFolderFilter,
                },
              ]"
              :title="
                selectedCharacterObj ? selectedCharacterObj.name : 'People'
              "
              ref="collapsedCharBtnRef"
              @click.stop="toggleCollapsedCharMenu"
            >
              <img
                v-if="
                  selectedCharacterObj &&
                  characterThumbnails[selectedCharacterObj.id]
                "
                :src="characterThumbnails[selectedCharacterObj.id]"
                alt=""
                :width="sidebarThumbnailSizeModel"
                :height="sidebarThumbnailSizeModel"
                class="sidebar-character-thumb"
              />
              <v-icon v-else>mdi-account-group</v-icon>
            </div>
          </div>
          <Teleport to="body">
            <div
              v-if="collapsedCharMenuOpen"
              ref="collapsedCharMenuRef"
              class="sidebar-collapsed-flyout-menu"
              :style="{
                top: collapsedCharMenuPos.top + 'px',
                left: collapsedCharMenuPos.left + 'px',
              }"
            >
              <div class="sidebar-collapsed-flyout-header">
                <span>People</span>
                <v-icon
                  v-if="!isReadOnly"
                  size="14"
                  class="sidebar-collapsed-flyout-header-add"
                  title="Add character"
                  @click.stop="
                    createCharacter();
                    collapsedCharMenuOpen = false;
                  "
                  >mdi-plus</v-icon
                >
              </div>
              <div class="sidebar-collapsed-flyout-scroll">
                <div
                  v-for="char in visibleCharacters"
                  :key="char.id"
                  :class="[
                    'sidebar-collapsed-flyout-item',
                    {
                      active:
                        props.selectedCharacter === char.id &&
                        !props.hasFolderFilter,
                    },
                  ]"
                  @click="
                    selectCharacter(char.id, char.name || 'Character', $event);
                    collapsedCharMenuOpen = false;
                  "
                  @contextmenu.prevent="
                    openSidebarCtxMenu('character', char, $event)
                  "
                >
                  <img
                    :src="characterThumbnails[char.id] || unknownPerson"
                    alt=""
                    class="sidebar-collapsed-flyout-thumb"
                  />
                  <span class="sidebar-collapsed-flyout-label">{{
                    char.name || "Character"
                  }}</span>
                  <div
                    v-if="!isReadOnly"
                    class="sidebar-collapsed-flyout-item-actions"
                  >
                    <v-icon
                      size="14"
                      title="Edit"
                      @click.stop="
                        openCharacterEditor(char);
                        collapsedCharMenuOpen = false;
                      "
                      >mdi-pencil-outline</v-icon
                    >
                    <v-icon
                      size="14"
                      title="More"
                      @click.stop="
                        openSidebarCtxMenu('character', char, $event)
                      "
                      >mdi-dots-vertical</v-icon
                    >
                  </div>
                </div>
              </div>
            </div>
          </Teleport>

          <!-- Picture Sets: individual dock buttons when space allows, flyout menu when space is tight -->
          <div
            v-if="visibleSets.length && sidebarPrimaryTab !== 'folders'"
            class="sidebar-collapsed-divider"
          ></div>
          <template
            v-if="
              visibleSets.length &&
              sidebarPrimaryTab !== 'folders' &&
              !setsCollapsed
            "
          >
            <div
              v-for="pset in visibleSets"
              :key="pset.id"
              :class="[
                'sidebar-collapsed-row',
                {
                  active:
                    selectedSetIdSet.has(pset.id) && !props.hasFolderFilter,
                },
              ]"
            >
              <div
                :class="[
                  'sidebar-collapsed-item',
                  {
                    active:
                      selectedSetIdSet.has(pset.id) && !props.hasFolderFilter,
                  },
                ]"
                :title="pset.name || 'Picture Set'"
                @click="selectSet(pset.id, pset.name || 'Picture Set', $event)"
                @contextmenu.prevent="openSidebarCtxMenu('set', pset, $event)"
              >
                <v-icon
                  v-if="pset.set_icon && pset.set_icon !== ICON_CARDS"
                  :color="pset.set_color || undefined"
                  >{{ pset.set_icon }}</v-icon
                >
                <img
                  v-else-if="hasSetThumbnail(pset)"
                  :src="getSetThumbnail(pset.id)"
                  alt=""
                  class="sidebar-set-thumb-image sidebar-set-thumb-image--collapsed"
                  :style="
                    pset.set_color
                      ? {
                          filter: `drop-shadow(0 0 3px ${pset.set_color}) drop-shadow(0 0 8px ${pset.set_color})`,
                        }
                      : {}
                  "
                  :width="sidebarThumbnailSizeModel"
                  :height="sidebarThumbnailSizeModel"
                  @load="handleSetThumbnailLoad(pset.id)"
                  @error="handleSetThumbnailError(pset.id)"
                />
                <v-icon v-else :color="pset.set_color || undefined"
                  >mdi-image-album</v-icon
                >
              </div>
            </div>
            <div v-if="!isReadOnly" class="sidebar-collapsed-row">
              <div
                class="sidebar-collapsed-item sidebar-collapsed-item--add sidebar-collapsed-item--add-set"
                title="Add picture set"
                @click="createSet()"
              >
                <i
                  class="mdi mdi-image-album sidebar-collapsed-item--add-bg-icon"
                  aria-hidden="true"
                ></i>
                <v-icon class="sidebar-collapsed-item--add-plus"
                  >mdi-plus</v-icon
                >
              </div>
            </div>
          </template>
          <div
            v-else-if="visibleSets.length && sidebarPrimaryTab !== 'folders'"
            :class="[
              'sidebar-collapsed-row',
              'sidebar-collapsed-row--has-flyout',
              { active: selectedSetIdSet.size > 0 && !props.hasFolderFilter },
            ]"
          >
            <div
              :class="[
                'sidebar-collapsed-item',
                'sidebar-collapsed-item--has-flyout',
                {
                  active: selectedSetIdSet.size > 0 && !props.hasFolderFilter,
                },
              ]"
              :title="selectedSetObj ? selectedSetObj.name : 'Picture Sets'"
              ref="collapsedSetBtnRef"
              @click.stop="toggleCollapsedSetMenu"
            >
              <template v-if="selectedSetObj">
                <v-icon
                  v-if="
                    selectedSetObj.set_icon &&
                    selectedSetObj.set_icon !== ICON_CARDS
                  "
                  :color="selectedSetObj.set_color || undefined"
                  >{{ selectedSetObj.set_icon }}</v-icon
                >
                <img
                  v-else-if="hasSetThumbnail(selectedSetObj)"
                  :src="getSetThumbnail(selectedSetObj.id)"
                  alt=""
                  class="sidebar-set-thumb-image sidebar-set-thumb-image--collapsed"
                  :style="
                    selectedSetObj.set_color
                      ? {
                          filter: `drop-shadow(0 0 3px ${selectedSetObj.set_color}) drop-shadow(0 0 8px ${selectedSetObj.set_color})`,
                        }
                      : {}
                  "
                  :width="sidebarThumbnailSizeModel"
                  :height="sidebarThumbnailSizeModel"
                />
                <v-icon v-else :color="selectedSetObj.set_color || undefined"
                  >mdi-image-album</v-icon
                >
              </template>
              <v-icon v-else>mdi-image-album</v-icon>
            </div>
          </div>
          <Teleport to="body">
            <div
              v-if="collapsedSetMenuOpen"
              ref="collapsedSetMenuRef"
              class="sidebar-collapsed-flyout-menu"
              :style="{
                top: collapsedSetMenuPos.top + 'px',
                left: collapsedSetMenuPos.left + 'px',
              }"
            >
              <div class="sidebar-collapsed-flyout-header">
                <span>Picture Sets</span>
                <v-icon
                  v-if="!isReadOnly"
                  size="14"
                  class="sidebar-collapsed-flyout-header-add"
                  title="Add picture set"
                  @click.stop="
                    createSet();
                    collapsedSetMenuOpen = false;
                  "
                  >mdi-plus</v-icon
                >
              </div>
              <div class="sidebar-collapsed-flyout-scroll">
                <div
                  v-for="pset in visibleSets"
                  :key="pset.id"
                  :class="[
                    'sidebar-collapsed-flyout-item',
                    {
                      active:
                        selectedSetIdSet.has(pset.id) && !props.hasFolderFilter,
                    },
                  ]"
                  @click="
                    selectSet(pset.id, pset.name || 'Picture Set', $event);
                    collapsedSetMenuOpen = false;
                  "
                  @contextmenu.prevent="openSidebarCtxMenu('set', pset, $event)"
                >
                  <v-icon
                    v-if="pset.set_icon && pset.set_icon !== ICON_CARDS"
                    size="28"
                    :color="pset.set_color || undefined"
                    >{{ pset.set_icon }}</v-icon
                  >
                  <img
                    v-else-if="hasSetThumbnail(pset)"
                    :src="getSetThumbnail(pset.id)"
                    alt=""
                    class="sidebar-collapsed-flyout-thumb"
                    :style="
                      pset.set_color
                        ? {
                            filter: `drop-shadow(0 0 3px ${pset.set_color}) drop-shadow(0 0 8px ${pset.set_color})`,
                          }
                        : {}
                    "
                    @load="handleSetThumbnailLoad(pset.id)"
                    @error="handleSetThumbnailError(pset.id)"
                  />
                  <v-icon v-else size="28">mdi-image-album</v-icon>
                  <span class="sidebar-collapsed-flyout-label">{{
                    pset.name || "Picture Set"
                  }}</span>
                  <div
                    v-if="!isReadOnly"
                    class="sidebar-collapsed-flyout-item-actions"
                  >
                    <v-icon
                      size="14"
                      title="Edit"
                      @click.stop="
                        openSetEditor(pset);
                        collapsedSetMenuOpen = false;
                      "
                      >mdi-pencil-outline</v-icon
                    >
                    <v-icon
                      size="14"
                      title="More"
                      @click.stop="openSidebarCtxMenu('set', pset, $event)"
                      >mdi-dots-vertical</v-icon
                    >
                  </div>
                </div>
              </div>
            </div>
          </Teleport>

          <!-- Scrap Heap at bottom of dock -->
          <div v-if="!isReadOnly" class="sidebar-collapsed-spacer"></div>
          <div
            v-if="!isReadOnly"
            :class="[
              'sidebar-collapsed-row',
              {
                active:
                  props.selectedCharacter === props.scrapheapPicturesId &&
                  !props.hasFolderFilter,
              },
            ]"
          >
            <div
              :class="[
                'sidebar-collapsed-item',
                'sidebar-collapsed-item--scrapheap',
                {
                  active:
                    props.selectedCharacter === props.scrapheapPicturesId &&
                    !props.hasFolderFilter,
                },
              ]"
              title="Scrapheap"
              @click="selectCharacter(props.scrapheapPicturesId, 'Scrapheap')"
            >
              <v-icon>mdi-trash-can-outline</v-icon>
            </div>
          </div>
        </div>
      </template>
      <template v-else>
        <!-- Folders tab panel -->
        <div v-if="sidebarPrimaryTab === 'folders'" class="sidebar-tab-panel">
          <!-- Add folder button matching New project style -->
          <div
            v-if="!isReadOnly"
            class="sidebar-project-tree-add"
            @click="openAddFolderTypeDialog()"
          >
            <v-icon size="14">mdi-plus</v-icon>
            Add folder
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
          <div
            v-else
            class="sidebar-folders-list"
            :style="{
              '--sidebar-folder-child-icon-size':
                sidebarFolderChildIconSize + 'px',
            }"
          >
            <div
              v-if="referenceFolders.length"
              class="sidebar-folder-section-header sidebar-folder-section-header--ref"
              @click="referenceFoldersCollapsed = !referenceFoldersCollapsed"
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
              <v-icon
                class="sidebar-project-tree-expand-indicator"
                :class="{ expanded: !referenceFoldersCollapsed }"
                size="14"
                >mdi-chevron-down</v-icon
              >
            </div>
            <div
              v-for="rf in referenceFolders"
              v-show="!referenceFoldersCollapsed"
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
              class="sidebar-folder-section-header sidebar-folder-section-header--import"
              @click="importFoldersCollapsed = !importFoldersCollapsed"
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
              <v-icon
                class="sidebar-project-tree-expand-indicator"
                :class="{ expanded: !importFoldersCollapsed }"
                size="14"
                >mdi-chevron-down</v-icon
              >
            </div>
            <div
              v-for="importFolder in importFolders"
              v-show="!importFoldersCollapsed"
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
                    importFolderId: importFolder.id,
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
          <!-- ══ GLOBAL tab content ══ -->
          <template v-if="projectViewMode === 'global'">
            <div v-if="!scopedResourceType" class="sidebar-all-pictures-row">
              <div
                :class="[
                  'sidebar-list-item',
                  { active: isAllPicturesRowActive },
                ]"
                @click="
                  selectCharacter(props.allPicturesId, allPicturesRowLabel)
                "
                @contextmenu.prevent="
                  openSidebarCtxMenu('all-pictures', null, $event)
                "
              >
                <span class="sidebar-list-icon sidebar-list-icon--toplevel"
                  ><v-icon size="18">mdi-image-multiple</v-icon></span
                >
                <span class="sidebar-list-label">{{
                  allPicturesRowLabel
                }}</span>
                <span class="sidebar-list-count">{{
                  categoryCounts[props.allPicturesId] ?? ""
                }}</span>
              </div>
            </div>

            <div v-if="!isReadOnly" class="sidebar-all-pictures-row">
              <div
                :class="[
                  'sidebar-list-item',
                  {
                    active:
                      props.selectedCharacter === props.scrapheapPicturesId &&
                      !props.hasFolderFilter,
                  },
                ]"
                @click="selectCharacter(props.scrapheapPicturesId, 'Scrapheap')"
              >
                <span class="sidebar-list-icon sidebar-list-icon--toplevel"
                  ><v-icon size="18">mdi-trash-can-outline</v-icon></span
                >
                <span class="sidebar-list-label">Scrapheap</span>
                <span class="sidebar-list-count">{{
                  categoryCounts[props.scrapheapPicturesId] || ""
                }}</span>
              </div>
            </div>

            <div class="sidebar-section-divider" />

            <div
              v-if="scopedResourceType !== 'picture_set'"
              class="sidebar-section-block"
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
                    v-if="
                      selectedCharacterObj &&
                      hasSingleSelectedCharacter &&
                      !isReadOnly
                    "
                    class="edit-character-inline"
                    @click.stop="openCharacterEditor(selectedCharacterObj)"
                    title="Edit selected character"
                    >mdi-pencil</v-icon
                  >
                  <v-icon
                    v-if="
                      !isReadOnly &&
                      props.selectedCharacter &&
                      props.selectedCharacter !== props.allPicturesId &&
                      props.selectedCharacter !== props.unassignedPicturesId &&
                      props.selectedCharacter !== props.scrapheapPicturesId
                    "
                    class="delete-character-inline"
                    color="white"
                    @click.stop="deleteCharacter"
                    title="Delete selected character"
                    >mdi-trash-can-outline</v-icon
                  >
                  <v-icon
                    v-if="!isReadOnly"
                    class="add-character-inline"
                    @click.stop="createCharacter"
                    title="Add character"
                    >mdi-plus</v-icon
                  >
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
                  <span class="sidebar-collections-help"
                    >Click the + button to add one.</span
                  >
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
                            >{{
                              char.name.charAt(0).toUpperCase() +
                              char.name.slice(1)
                            }}</span
                          >
                        </template>
                        <span>{{ char.name }}</span>
                      </v-tooltip>
                    </span>
                    <span class="sidebar-character-actions">
                      <v-icon
                        v-if="sharedCharacterIds.has(char.id)"
                        class="sidebar-shared-icon"
                        size="11"
                        title="Has active share links"
                        >mdi-link-variant</v-icon
                      >
                      <span class="sidebar-list-count">
                        <span v-if="isCountNew(char.id)" class="sidebar-new-tag"
                          >new</span
                        >
                        <span>{{ categoryCounts[char.id] ?? "" }}</span>
                      </span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <div class="sidebar-section-divider" style="margin-top: 8px" />

            <div
              v-if="scopedResourceType !== 'character'"
              class="sidebar-section-block"
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
                    v-if="selectedSetObj && hasSingleSelectedSet && !isReadOnly"
                    class="edit-set-inline"
                    @click.stop="openSetEditor(selectedSetObj)"
                    title="Edit selected set"
                    >mdi-pencil</v-icon
                  >
                  <v-icon
                    v-if="!isReadOnly && selectedSetIdSet.size > 0"
                    class="delete-character-inline"
                    color="white"
                    @click.stop="handleDeleteSet"
                    :title="
                      selectedSetIdSet.size > 1
                        ? `Delete ${selectedSetIdSet.size} selected sets`
                        : 'Delete selected set'
                    "
                    >mdi-trash-can-outline</v-icon
                  >
                  <v-icon
                    v-if="!isReadOnly"
                    class="add-character-inline"
                    @click.stop="createSet"
                    title="Create new set"
                    >mdi-plus</v-icon
                  >
                </div>
              </div>
              <div v-if="!setsSectionCollapsed" class="sidebar-section-scroll">
                <div
                  v-if="visibleSets.length === 0"
                  class="sidebar-collections-help-row"
                >
                  <span class="sidebar-collections-help"
                    >Click the + button to add one.</span
                  >
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
                      <v-icon
                        v-if="pset.set_icon && pset.set_icon !== ICON_CARDS"
                        :size="sidebarThumbnailSizeLarge - 2"
                        :color="pset.set_color || undefined"
                        >{{ pset.set_icon }}</v-icon
                      >
                      <img
                        v-else-if="hasSetThumbnail(pset)"
                        :src="getSetThumbnail(pset.id)"
                        alt=""
                        class="sidebar-set-thumb-image sidebar-set-thumb-image--large"
                        :width="sidebarThumbnailSizeLarge"
                        :height="sidebarThumbnailSizeLarge"
                        :style="
                          pset.set_color
                            ? {
                                filter: `drop-shadow(0 0 3px ${pset.set_color}) drop-shadow(0 0 8px ${pset.set_color})`,
                              }
                            : {}
                        "
                        @load="handleSetThumbnailLoad(pset.id)"
                        @error="handleSetThumbnailError(pset.id)"
                      />
                      <v-icon v-else :size="sidebarThumbnailSizeLarge - 2"
                        >mdi-image-album</v-icon
                      >
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
                            >{{ pset.name }}</span
                          >
                        </template>
                        <span>{{ pset.name }}</span>
                      </v-tooltip>
                    </span>
                    <v-icon
                      v-if="sharedSetIds.has(pset.id)"
                      class="sidebar-shared-icon"
                      size="11"
                      title="Has active share links"
                      >mdi-link-variant</v-icon
                    >
                    <span class="sidebar-list-count">{{
                      pset.picture_count ?? 0
                    }}</span>
                  </div>
                </template>
              </div>
            </div>
          </template>

          <!-- ══ PROJECTS tab content — flat tree ══ -->
          <template v-if="projectViewMode === 'project'">
            <div v-if="projects.length === 0" class="sidebar-no-projects-empty">
              <v-icon size="52" class="sidebar-no-projects-icon"
                >mdi-folder-plus-outline</v-icon
              >
              <p class="sidebar-no-projects-text">
                Create a project to organise your library into separate
                collections.
              </p>
              <v-btn
                v-if="!isReadOnly"
                color="primary"
                size="small"
                prepend-icon="mdi-plus"
                rounded="lg"
                class="sidebar-no-projects-btn"
                @click="createProject"
                >Create new project</v-btn
              >
            </div>

            <template v-if="projects.length > 0">
              <!-- Add project button -->
              <div
                v-if="!isReadOnly"
                class="sidebar-project-tree-add"
                @click="createProject"
              >
                <v-icon size="14">mdi-plus</v-icon>
                New project
              </div>

              <!-- Project tree nodes -->
              <div
                v-for="p in sortedProjects"
                :key="p.id"
                class="sidebar-project-tree-node"
              >
                <!-- Project row -->
                <div
                  :class="[
                    'sidebar-project-tree-row',
                    {
                      active:
                        selectedProjectId === p.id &&
                        props.selectedCharacter === props.allPicturesId &&
                        selectedSetIdSet.size === 0 &&
                        !props.hasFolderFilter,
                      droppable: dragOverProjectId === p.id,
                    },
                  ]"
                  :title="`${p.name} (drop pictures here to add them to this project)`"
                  @click="selectProjectNode(p)"
                  @contextmenu.prevent="
                    openSidebarCtxMenu('project', p, $event)
                  "
                  @dragover.prevent="handleDragOverProject(p.id)"
                  @dragleave="handleDragLeaveProject"
                  @drop.prevent="onProjectDrop(p.id, $event)"
                >
                  <span class="sidebar-project-tree-name-group">
                    <span class="sidebar-project-tree-label">{{ p.name }}</span>
                    <v-icon
                      class="sidebar-project-tree-expand-indicator"
                      :class="{ expanded: expandedProjectIds.has(p.id) }"
                      size="14"
                      @click.stop="toggleProjectExpanded(p.id)"
                      >mdi-chevron-down</v-icon
                    >
                  </span>
                  <v-icon
                    v-if="sharedProjectIds.has(p.id)"
                    size="11"
                    class="sidebar-shared-icon"
                    title="Has active share links"
                    >mdi-link-variant</v-icon
                  >
                  <span class="sidebar-project-tree-actions" @click.stop>
                    <v-icon
                      v-if="!isReadOnly"
                      size="13"
                      class="sidebar-project-tree-action-btn"
                      @click.stop="openProjectEditor(p)"
                      title="Edit project"
                      >mdi-pencil</v-icon
                    >
                    <v-icon
                      size="13"
                      class="sidebar-project-tree-action-btn"
                      @click.stop="exportProject(p)"
                      title="Export project as ZIP"
                      >mdi-download-outline</v-icon
                    >
                    <v-icon
                      v-if="!isReadOnly"
                      size="13"
                      class="sidebar-project-tree-action-btn sidebar-project-tree-action-btn--danger"
                      @click.stop="deleteProjectById(p)"
                      title="Delete project"
                      >mdi-trash-can-outline</v-icon
                    >
                  </span>
                  <span class="sidebar-list-count">{{
                    projectCounts[p.id] ?? ""
                  }}</span>
                </div>

                <!-- Expanded content -->
                <template v-if="expandedProjectIds.has(p.id)">
                  <!-- People sub-section -->
                  <div class="sidebar-project-tree-subsection">
                    <div
                      class="sidebar-project-tree-subheader"
                      @click.stop="toggleProjectTreePeople(p.id)"
                    >
                      <v-icon
                        v-show="
                          sortedCharacters.filter((c) => c.project_id === p.id)
                            .length > 0
                        "
                        size="14"
                        class="sidebar-project-tree-sub-chevron"
                        >{{
                          projectTreePeopleCollapsed.has(p.id)
                            ? "mdi-chevron-right"
                            : "mdi-chevron-down"
                        }}</v-icon
                      >
                      <span class="sidebar-project-tree-subheader-label"
                        >People</span
                      >
                      <span class="sidebar-header-spacer"></span>
                      <div class="sidebar-header-actions" @click.stop>
                        <span
                          ref="characterMoveMenuBtnRef"
                          class="sidebar-move-to-project-wrap"
                          v-if="!isReadOnly"
                          @click.stop
                        >
                          <v-icon
                            class="add-character-inline"
                            @click.stop="
                              selectProject(p.id);
                              openCharacterMoveMenu($event);
                            "
                            title="Add or remove people from this project"
                            >mdi-plus</v-icon
                          >
                          <Teleport to="body">
                            <div
                              v-if="
                                characterMoveMenuOpen &&
                                selectedProjectId === p.id
                              "
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
                                <v-icon
                                  size="16"
                                  class="sidebar-move-menu-check"
                                  >mdi-plus-circle-outline</v-icon
                                >Create new
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
                                  <v-icon
                                    size="16"
                                    class="sidebar-move-menu-check"
                                    >{{
                                      char.project_id === selectedProjectId
                                        ? "mdi-checkbox-marked"
                                        : "mdi-checkbox-blank-outline"
                                    }}</v-icon
                                  >
                                  {{ char.name }}
                                </div>
                              </template>
                            </div>
                          </Teleport>
                        </span>
                      </div>
                    </div>
                    <template v-if="!projectTreePeopleCollapsed.has(p.id)">
                      <div
                        v-for="char in sortedCharacters.filter(
                          (c) => c.project_id === p.id,
                        )"
                        :key="char.id"
                        :class="[
                          'sidebar-list-item',
                          'sidebar-project-tree-child',
                          {
                            active:
                              (selectedCharacterIdSet.size > 0
                                ? selectedCharacterIdSet.has(char.id)
                                : selectedCharacter === char.id) &&
                              !props.hasFolderFilter,
                            droppable: dragOverCharacter === char.id,
                          },
                        ]"
                        :title="`${char.name || 'Character'} (Ctrl/Cmd + click to multi-select)`"
                        @click="
                          selectCharacter(
                            char.id,
                            char.name || 'Character',
                            $event,
                          )
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
                            <template #activator="{ props: tipProps }">
                              <span
                                v-bind="tipProps"
                                :ref="
                                  mergeTooltipRef(tipProps, `char-${char.id}`)
                                "
                                class="sidebar-list-label-text"
                                >{{
                                  char.name.charAt(0).toUpperCase() +
                                  char.name.slice(1)
                                }}</span
                              >
                            </template>
                            <span>{{ char.name }}</span>
                          </v-tooltip>
                        </span>
                        <span class="sidebar-character-actions">
                          <v-icon
                            v-if="sharedCharacterIds.has(char.id)"
                            class="sidebar-shared-icon"
                            size="11"
                            title="Has active share links"
                            >mdi-link-variant</v-icon
                          >
                          <span class="sidebar-list-count">
                            <span
                              v-if="isCountNew(char.id)"
                              class="sidebar-new-tag"
                              >new</span
                            >
                            <span>{{ categoryCounts[char.id] ?? "" }}</span>
                          </span>
                        </span>
                      </div>
                    </template>
                  </div>

                  <!-- Sets sub-section -->
                  <div class="sidebar-project-tree-subsection">
                    <div
                      class="sidebar-project-tree-subheader"
                      @click.stop="toggleProjectTreeSets(p.id)"
                    >
                      <v-icon
                        v-show="
                          nonReferenceSets.filter((s) => s.project_id === p.id)
                            .length > 0
                        "
                        size="14"
                        class="sidebar-project-tree-sub-chevron"
                        >{{
                          projectTreeSetsCollapsed.has(p.id)
                            ? "mdi-chevron-right"
                            : "mdi-chevron-down"
                        }}</v-icon
                      >
                      <span class="sidebar-project-tree-subheader-label"
                        >Sets</span
                      >
                      <span class="sidebar-header-spacer"></span>
                      <div class="sidebar-header-actions" @click.stop>
                        <span
                          ref="setMoveMenuBtnRef"
                          class="sidebar-move-to-project-wrap"
                          v-if="!isReadOnly"
                          @click.stop
                        >
                          <v-icon
                            class="add-character-inline"
                            @click.stop="
                              selectProject(p.id);
                              openSetMoveMenu($event);
                            "
                            title="Add or remove sets from this project"
                            >mdi-plus</v-icon
                          >
                          <Teleport to="body">
                            <div
                              v-if="
                                setMoveMenuOpen && selectedProjectId === p.id
                              "
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
                                <v-icon
                                  size="16"
                                  class="sidebar-move-menu-check"
                                  >mdi-plus-circle-outline</v-icon
                                >Create new
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
                                  @click.stop="
                                    toggleSetProjectMembership(pset.id)
                                  "
                                >
                                  <v-icon
                                    size="16"
                                    class="sidebar-move-menu-check"
                                    >{{
                                      pset.project_id === selectedProjectId
                                        ? "mdi-checkbox-marked"
                                        : "mdi-checkbox-blank-outline"
                                    }}</v-icon
                                  >
                                  {{ pset.name }}
                                </div>
                              </template>
                            </div>
                          </Teleport>
                        </span>
                      </div>
                    </div>
                    <template v-if="!projectTreeSetsCollapsed.has(p.id)">
                      <div
                        v-for="pset in nonReferenceSets.filter(
                          (s) => s.project_id === p.id,
                        )"
                        :key="pset.id"
                        :class="[
                          'sidebar-list-item',
                          'sidebar-set-item',
                          'sidebar-project-tree-child',
                          {
                            active:
                              selectedSetIdSet.has(pset.id) &&
                              !props.hasFolderFilter,
                            droppable: dragOverSet === pset.id,
                          },
                        ]"
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
                          <v-icon
                            v-if="pset.set_icon && pset.set_icon !== ICON_CARDS"
                            :size="sidebarThumbnailSizeLarge - 2"
                            :color="pset.set_color || undefined"
                            >{{ pset.set_icon }}</v-icon
                          >
                          <img
                            v-else-if="hasSetThumbnail(pset)"
                            :src="getSetThumbnail(pset.id)"
                            alt=""
                            class="sidebar-set-thumb-image sidebar-set-thumb-image--large"
                            :width="sidebarThumbnailSizeLarge"
                            :height="sidebarThumbnailSizeLarge"
                            :style="
                              pset.set_color
                                ? {
                                    filter: `drop-shadow(0 0 3px ${pset.set_color}) drop-shadow(0 0 8px ${pset.set_color})`,
                                  }
                                : {}
                            "
                            @load="handleSetThumbnailLoad(pset.id)"
                            @error="handleSetThumbnailError(pset.id)"
                          />
                          <v-icon v-else :size="sidebarThumbnailSizeLarge - 2"
                            >mdi-image-album</v-icon
                          >
                        </span>
                        <span class="sidebar-list-label">
                          <v-tooltip
                            location="top"
                            :disabled="!labelNeedsTooltip(`set-${pset.id}`)"
                          >
                            <template #activator="{ props: tipProps }">
                              <span
                                v-bind="tipProps"
                                :ref="
                                  mergeTooltipRef(tipProps, `set-${pset.id}`)
                                "
                                class="sidebar-list-label-text"
                                >{{ pset.name }}</span
                              >
                            </template>
                            <span>{{ pset.name }}</span>
                          </v-tooltip>
                        </span>
                        <v-icon
                          v-if="sharedSetIds.has(pset.id)"
                          class="sidebar-shared-icon"
                          size="11"
                          title="Has active share links"
                          >mdi-link-variant</v-icon
                        >
                        <span class="sidebar-list-count">{{
                          pset.picture_count ?? 0
                        }}</span>
                      </div>
                    </template>
                  </div>

                  <!-- Files sub-section -->
                  <div
                    v-if="!isReadOnly || sessionContext?.include_attachments"
                    class="sidebar-project-tree-subsection sidebar-project-tree-files"
                    :style="{
                      '--sidebar-tree-icon-size':
                        sidebarThumbnailSizeModel + 'px',
                    }"
                  >
                    <ProjectFiles
                      :projectId="p.id"
                      :backendUrl="props.backendUrl"
                      compact
                    />
                  </div>
                </template>
              </div>
            </template>
          </template>
        </div>
      </template>
    </div>
    <!-- end sidebar-scroll -->
    <div v-if="isReadOnly" class="sidebar-readonly-notice">
      <v-icon size="12">mdi-lock-outline</v-icon>
      <span class="sidebar-readonly-notice-label">Read-only view</span>
      <span class="sidebar-readonly-notice-sep">&middot;</span>
      <a
        href="https://pixlstash.dev"
        target="_blank"
        rel="noopener noreferrer"
        class="sidebar-readonly-notice-btn"
      >
        <img
          src="/Logo.png"
          class="sidebar-readonly-notice-logo"
          alt="PixlStash"
        />
        <span>PixlStash</span>
      </a>
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
      :style="sidebarCtxMenuStyle"
      @contextmenu.prevent
      @mousedown.stop
    >
      <!-- ── Read-only indicator ───────────────────────────────── -->
      <div v-if="isReadOnly" class="ctx-readonly-header">
        <span class="ctx-readonly-pill">
          <v-icon size="10">mdi-lock-outline</v-icon>
          Read only
        </span>
      </div>
      <template v-if="sidebarCtxAllPictures">
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            shareResource(null, null, 'All Pictures');
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-share-variant-outline</v-icon
          >
          Share
        </button>
      </template>
      <template v-if="sidebarCtxCharacter">
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            shareResource(
              'character',
              sidebarCtxCharacter.id,
              sidebarCtxCharacter.name,
            )
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-share-variant-outline</v-icon
          >
          Share
        </button>
        <button
          class="sidebar-ctx-item"
          :disabled="sidebarCtxDeleteIds.length > 1 || isReadOnly"
          :class="{
            'sidebar-ctx-item--disabled':
              sidebarCtxDeleteIds.length > 1 || isReadOnly,
          }"
          @click="
            sidebarCtxDeleteIds.length === 1 &&
            !isReadOnly &&
            (openCharacterEditor(sidebarCtxCharacter), closeSidebarCtxMenu())
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <button
          v-if="sharedCharacterIds.has(sidebarCtxCharacter.id)"
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            openRevokeSharesDialog(
              'character',
              sidebarCtxCharacter.id,
              sidebarCtxCharacter.name,
            )
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-link-variant-off</v-icon
          >
          Remove all shares
        </button>
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          :disabled="isReadOnly"
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
          :disabled="isReadOnly"
          @click="
            shareResource('picture_set', sidebarCtxSet.id, sidebarCtxSet.name)
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-share-variant-outline</v-icon
          >
          Share
        </button>
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            openSetEditor(sidebarCtxSet);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <!-- Icon sub-menu -->
        <button
          class="sidebar-ctx-item sidebar-ctx-item--has-arrow"
          :disabled="isReadOnly"
          @click.stop="openSetCtxIconMenu($event)"
        >
          <v-icon
            size="15"
            class="sidebar-ctx-icon"
            :color="sidebarCtxSet.set_color || undefined"
          >
            {{
              sidebarCtxSet.set_icon && sidebarCtxSet.set_icon !== ICON_CARDS
                ? sidebarCtxSet.set_icon
                : "mdi-layers-triple"
            }}
          </v-icon>
          Icon
          <span class="sidebar-ctx-arrow">›</span>
        </button>
        <Teleport to="body">
          <div
            v-if="setCtxIconMenuOpen"
            class="sidebar-ctx-appearance-panel"
            :style="setCtxAppearanceStyle"
            @click.stop
            @mousedown.stop
          >
            <div class="sidebar-ctx-icon-section-wrap">
              <!-- Icon grid (ICON_CARDS excluded) -->
              <div class="sidebar-ctx-icon-grid">
                <template v-for="cat in SET_ICON_CATEGORIES" :key="cat.label">
                  <div class="sidebar-ctx-cat-header">{{ cat.label }}</div>
                  <template
                    v-for="ic in cat.icons.filter(
                      (i) => i.value !== ICON_CARDS,
                    )"
                    :key="ic.value"
                  >
                    <button
                      class="sidebar-ctx-icon-btn"
                      :class="{ selected: sidebarCtxSet.set_icon === ic.value }"
                      :title="ic.label"
                      @click="
                        applySetAppearance(sidebarCtxSet.id, ic.value, null)
                      "
                    >
                      <v-icon
                        size="18"
                        :color="sidebarCtxSet.set_color || undefined"
                        >{{ ic.value }}</v-icon
                      >
                    </button>
                  </template>
                </template>
              </div>
              <!-- or divider -->
              <div class="sidebar-ctx-icon-or-divider">
                <div class="sidebar-ctx-icon-or-line"></div>
                <span class="sidebar-ctx-icon-or-text">or</span>
                <div class="sidebar-ctx-icon-or-line"></div>
              </div>
              <!-- Thumbnail stack to the right -->
              <div class="sidebar-ctx-icon-cards-aside">
                <div class="sidebar-ctx-cat-header">Thumbnail</div>
                <button
                  class="sidebar-ctx-icon-btn--cards-large"
                  :class="{
                    selected:
                      !sidebarCtxSet.set_icon ||
                      sidebarCtxSet.set_icon === ICON_CARDS,
                  }"
                  title="Thumbnail Stack"
                  @click="
                    applySetAppearance(sidebarCtxSet.id, ICON_CARDS, null)
                  "
                >
                  <img
                    v-if="setThumbnails[sidebarCtxSet.id]"
                    :src="setThumbnails[sidebarCtxSet.id]"
                    class="sidebar-ctx-icon-thumb"
                    alt="Thumbnail"
                  />
                  <v-icon
                    v-else
                    size="32"
                    :color="sidebarCtxSet.set_color || undefined"
                    >mdi-layers-triple</v-icon
                  >
                </button>
              </div>
            </div>
          </div>
        </Teleport>
        <!-- Color sub-menu -->
        <button
          class="sidebar-ctx-item sidebar-ctx-item--has-arrow"
          :disabled="isReadOnly"
          @click.stop="openSetCtxColorMenu($event)"
        >
          <span
            class="sidebar-ctx-color-dot"
            :style="{ background: sidebarCtxSet.set_color || '#888' }"
          />
          Color
          <span class="sidebar-ctx-arrow">›</span>
        </button>
        <Teleport to="body">
          <div
            v-if="setCtxColorMenuOpen"
            class="sidebar-ctx-appearance-panel"
            :style="setCtxAppearanceStyle"
            @click.stop
            @mousedown.stop
          >
            <div class="sidebar-ctx-color-section-header">Color</div>
            <div class="sidebar-ctx-color-grid">
              <button
                v-for="col in SET_COLORS"
                :key="col.value"
                class="sidebar-ctx-color-swatch"
                :class="{ selected: sidebarCtxSet.set_color === col.value }"
                :style="{ background: col.value }"
                :title="col.label"
                @click="applySetAppearance(sidebarCtxSet.id, null, col.value)"
              />
            </div>
          </div>
        </Teleport>
        <button
          v-if="sharedSetIds.has(sidebarCtxSet.id)"
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            openRevokeSharesDialog(
              'picture_set',
              sidebarCtxSet.id,
              sidebarCtxSet.name,
            )
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-link-variant-off</v-icon
          >
          Remove all shares
        </button>
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          :disabled="isReadOnly"
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
      <template v-if="sidebarCtxProject">
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            shareResource(
              'project',
              sidebarCtxProject.id,
              sidebarCtxProject.name,
            );
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-share-variant-outline</v-icon
          >
          Share
        </button>
        <button
          class="sidebar-ctx-item"
          @click="
            exportProject(sidebarCtxProject);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-download-outline</v-icon
          >
          Export as ZIP
        </button>
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            openProjectEditor(sidebarCtxProject);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <button
          v-if="sharedProjectIds.has(sidebarCtxProject.id)"
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            openRevokeSharesDialog(
              'project',
              sidebarCtxProject.id,
              sidebarCtxProject.name,
            )
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-link-variant-off</v-icon
          >
          Remove all shares
        </button>
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            deleteProjectById(sidebarCtxProject);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-trash-can-outline</v-icon
          >
          Delete
        </button>
      </template>
      <template v-if="sidebarCtxFolder && !isReadOnly">
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
      <template v-if="sidebarCtxImportFolder && !isReadOnly">
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

  <!-- Share dialog -->
  <ShareDialog
    v-model="shareDialogOpen"
    :resource-type="shareDialogPending?.resourceType"
    :resource-id="shareDialogPending?.resourceId"
    :resource-label="shareDialogPending?.label"
    :embed-watermark="props.embedWatermark"
    :backend-url="props.backendUrl"
    :public-url="props.publicUrl"
    @update:embed-watermark="emit('update:embed-watermark', $event)"
  />

  <!-- ── Revoke all shares confirm dialog ──────────────────────── -->
  <v-dialog v-model="revokeSharesDialogOpen" max-width="400">
    <v-card class="share-dialog-card">
      <v-card-title class="share-dialog-title">
        <v-icon size="18" class="share-dialog-title-icon"
          >mdi-link-variant-off</v-icon
        >
        Remove all shares
      </v-card-title>
      <v-card-text class="share-dialog-body">
        <p class="share-dialog-hint">
          This will revoke all active share links for
          <strong>{{ revokeSharesPending?.label }}</strong
          >. Anyone with an existing link will lose access immediately.
        </p>
      </v-card-text>
      <v-card-actions class="share-dialog-actions">
        <v-btn variant="text" @click="revokeSharesDialogOpen = false"
          >Cancel</v-btn
        >
        <v-spacer />
        <v-btn color="error" variant="tonal" @click="confirmRevokeShares">
          Remove all shares
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.sidebar-project-header {
  padding-top: 4px;
  padding-bottom: 4px;
  justify-content: center;
}

.sidebar-section-divider {
  height: 1px;
  background: rgba(var(--v-theme-border), 0.2);
  margin: 2px 0;
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
  padding: 0px 4px 2px 4px;
  position: relative;
  z-index: 1;
  margin-top: 0;
  margin-bottom: 0;
  gap: 0;
  background: transparent;
  border-bottom: none;
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
  padding: 5px 8px 4px;
  flex: 1;
  border-radius: 0;
  border: none;
  border-bottom: 2px solid rgba(var(--v-theme-sidebar-text), 0.15);
  font-size: 0.74rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: pointer;
  background: transparent;
  color: rgba(var(--v-theme-sidebar-text), 0.5);
  transition:
    color 0.15s,
    border-color 0.15s;
  white-space: nowrap;
  position: relative;
}

.sidebar-view-tab .v-icon {
  color: inherit;
}

.sidebar-view-tab.active {
  background: transparent;
  color: rgb(var(--v-theme-accent));
  border-bottom-color: rgb(var(--v-theme-accent));
}

.sidebar-view-tab:hover:not(.active) {
  background: rgba(var(--v-theme-sidebar-text), 0.05);
  color: rgba(var(--v-theme-sidebar-text), 0.9);
}

@media (hover: none) and (pointer: coarse) {
  .sidebar-view-tab {
    min-height: 44px;
    padding: 8px 8px;
  }
}

.sidebar-tab-panel {
  margin: 0;
  padding: 0;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  background: transparent;
}

.sidebar-section-block {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 0 0 auto;
}

.sidebar-section-scroll {
  flex: 0 0 auto;
  overflow-x: hidden;
  overflow-y: visible;
  scrollbar-color: rgb(var(--v-theme-accent)) rgba(var(--v-theme-shadow), 0.15);
  background: transparent;
  border-top: none;
  border-bottom: none;
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

/* Project tree (Projects tab flat tree) */
.sidebar-project-tree-add {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px 6px 8px;
  font-size: 0.8rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: rgba(var(--v-theme-sidebar-text), 0.7);
  cursor: pointer;
  border-left: 3px solid transparent;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.22);
  transition:
    color 0.12s,
    background 0.12s;
}

.sidebar-project-tree-add:hover {
  color: rgba(var(--v-theme-sidebar-text), 0.92);
  background: rgba(var(--v-theme-accent), 0.08);
}

.sidebar-project-tree-node {
  display: flex;
  flex-direction: column;
  margin-bottom: 6px;
  border-top: 1px solid rgba(var(--v-theme-border), 0.22);
}

.sidebar-project-tree-node:first-child {
  border-top: none;
}

.sidebar-project-tree-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 var(--sidebar-right-edge, 8px) 0 8px;
  min-height: 28px;
  cursor: pointer;
  border-left: 3px solid transparent;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.2);
  border-radius: 0;
  background: transparent;
  transition:
    background 0.12s,
    color 0.12s,
    border-color 0.12s;
  color: rgba(var(--v-theme-sidebar-text), 0.7);
  position: relative;
}

.sidebar-project-tree-row:hover {
  background:
    linear-gradient(
      rgba(var(--v-theme-accent), 0.08),
      rgba(var(--v-theme-accent), 0.08)
    ),
    rgba(var(--v-theme-sidebar-text), 0.05);
  color: rgba(var(--v-theme-sidebar-text), 0.92);
}

.sidebar-project-tree-row.active {
  background: rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
  border-left: 3px solid rgb(var(--v-theme-primary));
  border-radius: 0;
}

/* Drop-target highlight (drag pictures onto a project to assign them) —
   mirrors .sidebar-list-item.droppable used by character/set rows. */
.sidebar-project-tree-row.droppable {
  filter: brightness(1.2);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-project-tree-row.active:hover {
  background:
    linear-gradient(
      rgba(var(--v-theme-accent), 0.08),
      rgba(var(--v-theme-accent), 0.08)
    ),
    rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-project-tree-expand-indicator {
  flex-shrink: 0;
  opacity: 0.5;
  color: inherit;
  transform: rotate(-90deg);
  transition:
    transform 0.15s,
    opacity 0.12s;
}

.sidebar-project-tree-expand-indicator.expanded {
  transform: rotate(0deg);
}

.sidebar-project-tree-row:hover .sidebar-project-tree-expand-indicator {
  opacity: 0.9;
}

.sidebar-project-tree-icon {
  flex-shrink: 0;
  opacity: 0.6;
  color: inherit;
}

.sidebar-project-tree-name-group {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 2px;
  overflow: hidden;
}

.sidebar-project-tree-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.sidebar-project-tree-actions {
  display: none;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.sidebar-project-tree-row:hover .sidebar-project-tree-actions {
  display: flex;
}

.sidebar-project-tree-action-btn {
  opacity: 0.55;
  cursor: pointer;
  color: inherit;
  transition: opacity 0.12s;
}

.sidebar-project-tree-action-btn:hover {
  opacity: 1;
}

.sidebar-project-tree-action-btn--danger:hover {
  color: rgb(var(--v-theme-error));
}

/* Sub-sections under an expanded project */
.sidebar-project-tree-subsection {
  display: flex;
  flex-direction: column;
}

.sidebar-project-tree-subheader {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 2px 0 2px 10px;
  padding-right: var(--sidebar-header-action-right-edge) !important;
  min-height: 24px;
  cursor: pointer;
  color: rgba(var(--v-theme-sidebar-text), 0.4);
  transition: color 0.12s;
  user-select: none;
}

.sidebar-project-tree-subheader:hover {
  background: rgba(var(--v-theme-accent), 0.08);
  color: rgba(var(--v-theme-sidebar-text), 0.92);
}

.sidebar-project-tree-sub-chevron {
  flex-shrink: 0;
  color: inherit;
}

/* v-show sets display:none — override to visibility:hidden so the space is preserved */
.sidebar-project-tree-sub-chevron[style*="display: none"],
.sidebar-project-tree-sub-chevron[style*="display:none"] {
  display: inline-flex !important;
  visibility: hidden;
}

.sidebar-project-tree-subheader-icon {
  flex-shrink: 0;
  opacity: 0.6;
  color: inherit;
}

.sidebar-project-tree-subheader-label {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: inherit;
  flex: 1;
}

/* Child items indented under a project sub-section */
.sidebar-project-tree-child {
  padding-left: 16px !important;
}

.sidebar-project-tree-empty {
  padding: 2px 8px 2px 20px;
  font-size: 0.72rem;
  color: rgba(var(--v-theme-sidebar-text), 0.35);
  font-style: italic;
}

.sidebar-project-tree-files {
  padding-left: 0;
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
.sidebar-collapsed-project-menu .sidebar-project-menu-add,
.sidebar-collapsed-project-submenu .sidebar-project-menu-item,
.sidebar-collapsed-project-submenu .sidebar-project-menu-add {
  color: rgb(var(--v-theme-on-surface));
}

.sidebar-collapsed-project-menu .sidebar-project-menu-item.active,
.sidebar-collapsed-project-submenu .sidebar-project-menu-item.active {
  background: rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
  font-weight: 600;
  border-left: 3px solid rgb(var(--v-theme-primary));
  padding-left: 7px; /* compensate for the 3px border so text stays aligned */
}

.sidebar-collapsed-project-submenu {
  position: fixed;
  z-index: 301;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  border-radius: 6px;
  overflow: hidden;
  min-width: 180px;
  max-height: 60vh;
  overflow-y: auto;
  white-space: nowrap;
}

.sidebar-project-menu-separator {
  height: 1px;
  background: rgba(var(--v-theme-border), 0.3);
  margin: 2px 0;
}

.sidebar-project-menu-has-sub {
  position: relative;
}

.sidebar-project-menu-has-sub.sub-open {
  background: rgba(var(--v-theme-accent), 0.06);
}

.sidebar-project-menu-chevron {
  margin-left: auto;
  opacity: 0.45;
  flex-shrink: 0;
}

.sidebar-collapsed-item--scrapheap {
  opacity: 0.6;
  transition:
    opacity 0.15s,
    background-color 0.18s ease,
    color 0.18s ease;
}

.sidebar-collapsed-item--scrapheap:hover {
  opacity: 1;
}

.sidebar-collapsed-item--scrapheap.active {
  opacity: 1;
}

.sidebar-collapsed-flyout-menu {
  position: fixed;
  z-index: 300;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  max-height: 60vh;
  min-width: 200px;
  width: max-content;
  white-space: nowrap;
}

.sidebar-collapsed-flyout-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  padding: 6px 12px;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-sidebar-text), 0.55);
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.4);
  background: rgba(var(--v-theme-tertiary), 0.2);
}

.sidebar-collapsed-flyout-header-add {
  opacity: 0.6;
  cursor: pointer;
  flex-shrink: 0;
  transition: opacity 0.12s;
}

.sidebar-collapsed-flyout-header-add:hover {
  opacity: 1;
}

.sidebar-collapsed-flyout-scroll {
  overflow-y: auto;
  flex: 1;
}

.sidebar-collapsed-flyout-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  cursor: pointer;
  color: rgb(var(--v-theme-on-surface));
  transition: background 0.12s;
}

.sidebar-collapsed-flyout-item:hover {
  background: rgba(var(--v-theme-tertiary), 0.25);
}

.sidebar-collapsed-flyout-item.active {
  background: rgba(var(--v-theme-primary), 0.15);
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-collapsed-flyout-thumb {
  width: 32px;
  height: 32px;
  border-radius: 4px;
  object-fit: cover;
  flex-shrink: 0;
}

.sidebar-collapsed-flyout-label {
  font-size: 0.88rem;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.sidebar-collapsed-flyout-item-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  margin-left: auto;
  visibility: hidden;
}

.sidebar-collapsed-flyout-item:hover .sidebar-collapsed-flyout-item-actions {
  visibility: visible;
}

.sidebar-collapsed-flyout-item-actions .v-icon {
  opacity: 0.55;
  cursor: pointer;
  padding: 2px;
  border-radius: 3px;
  transition:
    opacity 0.1s,
    background 0.1s;
}

.sidebar-collapsed-flyout-item-actions .v-icon:hover {
  opacity: 1;
  background: rgba(var(--v-theme-tertiary), 0.35);
}

.sidebar-project-menu-section-label {
  padding: 6px 10px 2px;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-sidebar-text), 0.45);
}

.sidebar-project-menu-wrap {
  position: relative;
  padding: 0;
  border-bottom: none;
}

.sidebar-project-label {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.3);
  background: transparent;
  color: rgba(var(--v-theme-sidebar-text), 0.65);
  font-size: 0.81rem;
  font-weight: 500;
  min-height: 26px;
}

.sidebar-project-trigger {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 0;
  border: none;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.3);
  background: transparent;
  color: rgba(var(--v-theme-sidebar-text), 0.65);
  font-size: 0.81rem;
  font-weight: 500;
  min-height: 26px;
  cursor: pointer;
  transition:
    background 0.12s,
    color 0.12s;
  text-align: left;
}

.sidebar-project-trigger:hover {
  background: rgba(var(--v-theme-accent), 0.08);
  color: rgba(var(--v-theme-sidebar-text), 0.9);
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
  padding: 4px 10px;
  cursor: pointer;
  font-size: 0.81rem;
  color: rgb(var(--v-theme-on-tertiary));
  transition:
    background 0.1s,
    color 0.1s;
  gap: 6px;
  min-height: 26px;
}

.sidebar-project-menu-item:hover {
  background: rgba(var(--v-theme-accent), 0.08);
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
  padding: 4px 10px;
  font-size: 0.81rem;
  font-weight: 600;
  cursor: pointer;
  color: rgba(var(--v-theme-on-tertiary), 0.65);
  border-top: 1px solid rgba(var(--v-theme-border), 0.25);
  min-height: 26px;
  transition:
    background 0.1s,
    color 0.1s;
}

.sidebar-project-menu-add:hover {
  background: rgba(var(--v-theme-accent), 0.08);
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
  --sidebar-thumb-size: 24px;
  --sidebar-thumb-size-large: calc(var(--sidebar-thumb-size) + 4px);
  --sidebar-space-y: 2px;
  --sidebar-item-radius: 25%;
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

.sidebar.sidebar-docked {
  width: calc(var(--sidebar-thumb-size) + 20px);
  overflow: hidden;
}

.sidebar.sidebar-docked .sidebar-brand {
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 4px 0 2px;
  gap: 1px;
  position: static;
}

.sidebar.sidebar-docked .sidebar-brand-toggle {
  position: static;
  transform: none;
  width: calc(var(--sidebar-thumb-size) * 0.65) !important;
  height: calc(var(--sidebar-thumb-size) * 0.65) !important;
  min-width: calc(var(--sidebar-thumb-size) * 0.65) !important;
  min-height: calc(var(--sidebar-thumb-size) * 0.65) !important;
  padding: 0 !important;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sidebar.sidebar-docked .sidebar-brand-toggle:hover {
  background-color: rgba(var(--v-theme-accent), 0.4);
}

.sidebar.sidebar-docked .sidebar-brand-left {
  padding: 0;
  justify-content: center;
}

.sidebar.sidebar-docked .sidebar-brand-logo {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  padding-left: calc(var(--sidebar-thumb-size) * 0.1);
  object-fit: contain;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 4px 4px 2px;
  background: transparent;
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

.sidebar-update-wrapper {
  position: absolute;
  top: 100%;
  left: 0;
  display: flex;
  align-items: center;
  gap: 3px;
  white-space: nowrap;
}

.sidebar-update-available {
  font-size: 0.65rem;
  line-height: 1;
  color: rgba(var(--v-theme-accent), 0.8);
  text-decoration: none;
  white-space: nowrap;
}

.sidebar-update-available:hover {
  text-decoration: underline;
}

.sidebar-update-dismiss {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  width: 10px;
  height: 10px;
  font-size: 0.6rem;
  line-height: 1;
  background: transparent;
  border: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.4);
  opacity: 0.7;
}

.sidebar-update-dismiss:hover {
  opacity: 1;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.sidebar-update-security {
  color: #e57c00;
}

.sidebar-update-security:hover {
  color: #c96000;
}

.sidebar-update-security--high {
  color: #e53935;
}

.sidebar-update-security--high:hover {
  color: #c62828;
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

.sidebar-collapsed-row {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  flex-shrink: 0;
}

.sidebar-collapsed-row::before {
  content: "";
  position: absolute;
  left: 2px;
  top: 50%;
  transform: translateY(-50%) scaleY(0);
  width: 3px;
  height: 60%;
  border-radius: 0 2px 2px 0;
  background: rgb(var(--v-theme-primary));
  transition: transform 0.18s ease;
  pointer-events: none;
  z-index: 1;
}

.sidebar-collapsed-row.active::before {
  transform: translateY(-50%) scaleY(1);
}

.sidebar-collapsed-row--has-flyout::after {
  content: "";
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: 0;
  height: 0;
  border-top: 3px solid transparent;
  border-bottom: 3px solid transparent;
  border-left: 4px solid currentColor;
  opacity: 0.45;
  pointer-events: none;
}

.sidebar-collapsed-row--has-flyout.active::after {
  opacity: 0.8;
}

/* Prominent indicator for the main project/nav menu */
.sidebar-collapsed-row--project::after {
  right: 3px;
  border-top: 4px solid transparent;
  border-bottom: 4px solid transparent;
  border-left: 6px solid currentColor;
  opacity: 0.7;
}

.sidebar-collapsed-row--project.active::after {
  opacity: 1;
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
  position: relative;
  transition:
    background-color 0.18s ease,
    color 0.18s ease;
}

.sidebar-collapsed-item.active {
  color: rgba(var(--v-theme-sidebar-text), 0.84);
}

.sidebar-collapsed-item--add {
  background: rgba(var(--v-theme-sidebar-text), 0.07);
  border: 1px dashed rgba(var(--v-theme-sidebar-text), 0.25);
  color: rgba(var(--v-theme-sidebar-text), 0.5);
  position: relative;
  overflow: hidden;
}

.sidebar-collapsed-item--add-bg-icon {
  position: absolute !important;
  display: block !important;
  top: 50% !important;
  left: 50% !important;
  transform: translate(-50%, -50%) !important;
  font-size: calc(var(--sidebar-thumb-size) * 1) !important;
  width: calc(var(--sidebar-thumb-size) * 1) !important;
  height: calc(var(--sidebar-thumb-size) * 1) !important;
  line-height: 1 !important;
  opacity: 0.18;
  pointer-events: none;
  color: currentColor !important;
}

.sidebar-collapsed-item--add-plus {
  position: relative;
  z-index: 1;
  font-size: calc(var(--sidebar-thumb-size) * 0.42) !important;
  width: calc(var(--sidebar-thumb-size) * 0.42) !important;
  height: calc(var(--sidebar-thumb-size) * 0.42) !important;
}

.sidebar-collapsed-item--add:hover {
  background: rgba(var(--v-theme-primary), 0.18) !important;
  border-color: rgba(var(--v-theme-primary), 0.5) !important;
  color: rgb(var(--v-theme-primary)) !important;
  filter: none !important;
  box-shadow: none !important;
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
}

.sidebar-collapsed-item--has-flyout {
  position: relative;
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
  width: 70%;
  height: 1px;
  margin: 2px auto;
  background: rgba(var(--v-theme-sidebar-text), 0.15);
}

@media (max-width: 999px) {
  .sidebar {
    height: 100%;
  }
}

.sidebar-section-header {
  position: relative;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  min-height: clamp(18px, calc(var(--sidebar-thumb-size) * 0.65), 24px);
  padding: clamp(2px, calc(var(--sidebar-thumb-size) * 0.1), 8px) 10px 2px 10px;
  padding-right: var(--sidebar-header-action-right-edge) !important;
  display: flex;
  align-items: center;
  color: rgba(var(--v-theme-sidebar-text), 0.4);
}

.sidebar-section-header--collapsible {
  cursor: pointer;
  user-select: none;
}

.sidebar-section-header--collapsible:hover {
  background: rgba(var(--v-theme-accent), 0.08);
  color: rgba(var(--v-theme-sidebar-text), 0.92);
}

.sidebar-section-chevron {
  margin-right: 3px;
  flex-shrink: 0;
  color: rgba(var(--v-theme-sidebar-text), 0.4);
  transition: transform 0.15s;
}

.sidebar-section-header-icon {
  display: none;
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
  min-height: calc(var(--sidebar-thumb-size) + 6px);
  padding: 3px 8px;
  padding-right: var(--sidebar-right-edge) !important;
  cursor: pointer;
  border-radius: 0;
  margin-bottom: 0;
  font-size: 0.81rem;
  font-weight: 400;
  background: transparent;
  color: rgba(var(--v-theme-sidebar-text), 0.76);
  transition:
    background 0.12s,
    color 0.12s,
    border-color 0.12s;
  border-left: 3px solid transparent;
}

.sidebar-footer-spacer {
  flex: 1 1 auto;
}

.sidebar-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 0px 0 0;
  scrollbar-color: rgba(var(--v-theme-primary), 0.55) transparent;
  scrollbar-width: thin;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  background: transparent;
}

.sidebar-scroll::-webkit-scrollbar {
  width: 6px;
}

.sidebar-scroll::-webkit-scrollbar-thumb {
  background: rgba(var(--v-theme-primary), 0.55);
  border-radius: 6px;
}

.sidebar-scroll::-webkit-scrollbar-thumb:hover {
  background: rgb(var(--v-theme-primary));
}

.sidebar-scroll::-webkit-scrollbar-track {
  background: transparent;
}

.sidebar-readonly-notice {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 6px 10px;
  border-top: 1px solid rgba(var(--v-theme-border), 0.2);
  background: rgb(var(--v-theme-sidebar));
  flex-shrink: 0;
  font-size: 0.68rem;
  font-weight: 500;
  color: rgba(var(--v-theme-sidebar-text), 0.38);
}

.sidebar-readonly-notice-label {
  white-space: nowrap;
}

.sidebar-readonly-notice-sep {
  opacity: 0.5;
}

.sidebar-readonly-notice-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border-radius: 5px;
  background: rgba(var(--v-theme-accent), 0.15);
  color: rgb(var(--v-theme-accent));
  font-size: 0.68rem;
  font-weight: 600;
  text-decoration: none;
  transition:
    background 0.15s,
    color 0.15s;
}

.sidebar-readonly-notice-btn:hover {
  background: rgba(var(--v-theme-accent), 0.28);
}

.sidebar-readonly-notice-logo {
  width: 13px;
  height: 13px;
  object-fit: contain;
  display: block;
}

.sidebar-collapsed .sidebar-readonly-notice {
  display: none;
}

.sidebar-footer {
  padding: 4px 0 0 0;
}

.sidebar-footer-item {
  margin-bottom: 0;
}

.sidebar-list-item.active {
  background: rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
  border-left: 3px solid rgb(var(--v-theme-primary));
  position: relative;
  border-radius: 0;
}

.sidebar-list-item.active .sidebar-list-count {
  color: rgba(var(--v-theme-on-primary), 0.65);
}

.sidebar-list-item:hover {
  background: rgba(var(--v-theme-accent), 0.08);
  color: rgba(var(--v-theme-sidebar-text), 0.92);
}

.sidebar-list-item.active:hover {
  background:
    linear-gradient(
      rgba(var(--v-theme-accent), 0.08),
      rgba(var(--v-theme-accent), 0.08)
    ),
    rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
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
  gap: 1px;
  min-width: 40px;
  justify-content: flex-end;
  margin-left: auto;
  padding-right: var(--sidebar-header-action-right-edge) !important;
}

.sidebar-header-actions .v-icon {
  min-width: 22px;
  min-height: 22px;
  justify-content: center;
  text-align: center;
  color: rgba(var(--v-theme-sidebar-text), 0.5);
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
  margin-right: 6px;
  justify-content: center;
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  flex-shrink: 0;
  overflow: visible;
}

/* For items that never have a thumbnail — keep the icon slot compact */
.sidebar-list-icon--fixed {
  width: min(var(--sidebar-thumb-size), 24px) !important;
  height: min(var(--sidebar-thumb-size), 24px) !important;
}

/* Top-level nav rows (All Pictures, Scrapheap) — slightly larger than --fixed */
.sidebar-list-icon--toplevel {
  width: min(var(--sidebar-thumb-size), 26px) !important;
  height: min(var(--sidebar-thumb-size), 26px) !important;
}

.sidebar.sidebar-docked .sidebar-brand-toggle .v-icon {
  font-size: calc(var(--sidebar-thumb-size) * 0.5) !important;
  width: calc(var(--sidebar-thumb-size) * 0.5) !important;
  height: calc(var(--sidebar-thumb-size) * 0.5) !important;
}

.sidebar-list-icon .v-icon,
.sidebar-collapsed-item .v-icon,
.sidebar-brand-toggle .v-icon,
.sidebar-brand-task-btn .v-icon {
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-collapsed-item .v-icon {
  font-size: calc(var(--sidebar-thumb-size) * 0.65) !important;
  width: calc(var(--sidebar-thumb-size) * 0.65) !important;
  height: calc(var(--sidebar-thumb-size) * 0.65) !important;
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
  border: none;
  display: inline-block;
  filter: none;
  transition: transform 0.12s ease;
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
  transition: transform 0.12s ease;
}

.sidebar-set-thumb-image--collapsed {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  margin: 0;
  border: none;
  box-shadow: none;
}

.sidebar-set-thumb-image--large {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  border-radius: var(--sidebar-item-radius);
}

.sidebar-list-item:hover .sidebar-character-thumb,
.sidebar-list-item:hover .sidebar-set-thumb-image {
  transform: scale(1.04);
}

.sidebar-list-item:hover .sidebar-character-thumb {
  border-color: rgba(var(--v-theme-accent), 0.6);
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

/* ── Share link indicator icon on sidebar list items ──────────── */
.sidebar-shared-icon {
  opacity: 0.5;
  color: rgb(var(--v-theme-primary));
  flex-shrink: 0;
  pointer-events: none;
}

.sidebar-shared-icon--inline {
  opacity: 0.5;
  color: rgb(var(--v-theme-primary));
  flex-shrink: 0;
  pointer-events: none;
  margin-right: 2px;
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
  color: rgba(var(--v-theme-sidebar-text), 0.5) !important;
  font-size: 1rem;
  cursor: pointer;
  background: transparent;
  border-radius: 5px;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    background 0.15s,
    color 0.15s;
}

.add-character-inline:hover {
  background: rgba(var(--v-theme-accent), 0.85);
  color: rgb(var(--v-theme-on-primary)) !important;
}

.clear-selection-inline {
  color: rgb(var(--v-theme-primary)) !important;
  font-size: 0.9rem;
  cursor: pointer;
  background: transparent;
  border: none;
  padding: 0;
  border-radius: 5px;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 22px;
  transition: background 0.15s;
}

.clear-selection-inline:hover {
  background: rgba(var(--v-theme-primary), 0.15);
}

.edit-character-inline,
.edit-set-inline {
  color: rgba(var(--v-theme-sidebar-text), 0.5) !important;
  font-size: 1rem;
  cursor: pointer;
  background: transparent;
  border-radius: 5px;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 22px;
  transition:
    background 0.15s,
    color 0.15s;
}

.edit-character-inline:hover,
.edit-set-inline:hover {
  background: rgba(var(--v-theme-primary), 0.15);
  color: rgb(var(--v-theme-primary)) !important;
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
  color: rgba(var(--v-theme-sidebar-text), 0.5) !important;
  font-size: 1rem;
  cursor: pointer;
  background: transparent;
  border-radius: 5px;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 22px;
  transition:
    background 0.15s,
    color 0.15s;
}

.delete-character-inline:hover {
  background: rgba(var(--v-theme-error), 0.15);
  color: rgb(var(--v-theme-error)) !important;
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

/* Items within People/Sets sections get a tree indent */
.sidebar-section-scroll .sidebar-list-item {
  padding-left: 16px;
}

.sidebar-section-block .sidebar-section-header {
  padding-bottom: 1px;
}

/* All-pictures row: same compact height as regular items */
.sidebar-all-pictures-row {
  padding-top: 0;
  display: flex;
  align-items: stretch;
  width: 100%;
  transition: outline 0.12s;
}

/* Keep All Pictures / Scrapheap rows compact but slightly prominent */
.sidebar-all-pictures-row .sidebar-list-item {
  min-height: calc(min(var(--sidebar-thumb-size), 22px) + 6px) !important;
  padding-top: 3px !important;
  padding-bottom: 3px !important;
  font-size: 0.8rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: rgba(var(--v-theme-sidebar-text), 0.7);
}

.sidebar-all-pictures-row.drag-over-project {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
  background: rgba(var(--v-theme-primary), 0.1);
}

.sidebar-all-pictures-row .sidebar-list-item {
  flex: 1 1 0;
  width: 0;
  min-width: 0;
  overflow: hidden;
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

@media (hover: none) and (pointer: coarse) {
  .sidebar-list-item,
  .sidebar-list-item.active {
    min-height: 56px;
    padding: 6px 10px;
  }

  .sidebar-section-header {
    min-height: 48px;
  }

  .sidebar-all-pictures-row .sidebar-list-item,
  .sidebar-all-pictures-row .sidebar-list-item.active {
    min-height: 48px;
    padding: 6px 10px;
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
.sidebar-folders-loading {
  display: flex;
  justify-content: center;
  padding: 32px;
}

.sidebar-folders-list {
  flex: 0 0 auto;
  overflow-y: visible;
  overflow-x: hidden;
  padding: 2px 0;
  scrollbar-color: rgb(var(--v-theme-accent)) rgba(var(--v-theme-shadow), 0.15);
}

/* Add folder button in the folders tab needs no bottom divider — section headers do that job */
.sidebar-tab-panel > .sidebar-project-tree-add {
  border-bottom: none;
}

.sidebar-folder-section-header {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 var(--sidebar-right-edge, 8px) 0 8px;
  min-height: 28px;
  cursor: pointer;
  color: rgba(var(--v-theme-sidebar-text), 0.7);
  background: transparent;
  border-top: 1px solid rgba(var(--v-theme-border), 0.22);
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.2);
  border-left: 3px solid transparent;
  user-select: none;
  transition:
    background 0.12s,
    color 0.12s;
}

.sidebar-folder-section-header:first-child {
  border-top: none;
}

.sidebar-folder-section-header:hover {
  background:
    linear-gradient(
      rgba(var(--v-theme-accent), 0.08),
      rgba(var(--v-theme-accent), 0.08)
    ),
    rgba(var(--v-theme-sidebar-text), 0.05);
  color: rgba(var(--v-theme-sidebar-text), 0.92);
}

.sidebar-folder-section-chevron {
  flex-shrink: 0;
  opacity: 0.6;
  color: inherit;
}

.sidebar-folder-section-icon {
  flex-shrink: 0;
  opacity: 0.6;
  color: inherit;
}

.sidebar-folder-section-title {
  flex: 1 1 auto;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: inherit;
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
  padding: 3px 8px 3px 10px;
  cursor: pointer;
  color: rgba(var(--v-theme-sidebar-text), 0.8);
  user-select: none;
  min-height: 28px;
  border-left: 3px solid transparent;
  transition:
    background 0.12s,
    color 0.12s;
}

.sidebar-folder-root-row {
  font-size: 0.82rem;
}

.sidebar-folder-row:hover {
  background: rgba(var(--v-theme-accent), 0.08);
  color: rgba(var(--v-theme-sidebar-text), 0.92);
}

.sidebar-folder-row.active {
  background: rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
  border-left: 3px solid rgb(var(--v-theme-primary));
}

.sidebar-folder-row.active:hover {
  background:
    linear-gradient(
      rgba(var(--v-theme-accent), 0.08),
      rgba(var(--v-theme-accent), 0.08)
    ),
    rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-folder-children {
  padding-left: 4px;
  border-left: 1px dashed rgba(var(--v-theme-border), 0.35);
  margin-left: 18px;
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

/* All folder icons fixed at 16px regardless of thumbnail size */
.sidebar-folders-list :deep(.sidebar-folder-icon) {
  font-size: 16px !important;
  width: 16px !important;
  height: 16px !important;
}
.sidebar-folders-list :deep(.sidebar-folder-chevron) {
  font-size: 16px !important;
  width: 16px !important;
  height: 16px !important;
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
  color: rgba(var(--v-theme-on-primary), 0.65);
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

button.sidebar-ctx-item:disabled {
  opacity: 0.38;
  cursor: default;
  pointer-events: none;
}

button.sidebar-ctx-item:disabled:hover {
  background: transparent;
}

.sidebar-ctx-item--has-arrow {
  justify-content: flex-start;
}

.sidebar-ctx-arrow {
  margin-left: auto;
  opacity: 0.5;
  font-size: 1rem;
  line-height: 1;
}

.sidebar-ctx-color-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
}

/* Appearance panel (icon / color picker) that floats next to the context menu */
.sidebar-ctx-appearance-panel {
  position: fixed;
  z-index: 2100;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 8px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.28);
  padding: 6px 8px 8px;
  user-select: none;
  max-height: calc(100vh - 16px);
  overflow-y: auto;
}

.sidebar-ctx-appearance-label {
  font-size: 0.68rem;
  opacity: 0.55;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  margin-bottom: 7px;
}

.sidebar-ctx-icon-section-wrap {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.sidebar-ctx-icon-or-divider {
  display: flex;
  flex-direction: column;
  align-items: center;
  align-self: stretch;
  padding: 24px 2px;
  gap: 3px;
}

.sidebar-ctx-icon-or-line {
  flex: 1;
  width: 1px;
  background: rgba(var(--v-theme-on-surface), 0.12);
}

.sidebar-ctx-icon-or-text {
  font-size: 0.55rem;
  opacity: 0.35;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  line-height: 1;
}

.sidebar-ctx-icon-cards-aside {
  flex-shrink: 0;
  text-align: center;
}

.sidebar-ctx-icon-btn--cards-large {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  border: 2px solid transparent;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  overflow: hidden;
  transition: border-color 0.12s;
}

.sidebar-ctx-icon-btn--cards-large:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.sidebar-ctx-icon-btn--cards-large.selected {
  border-color: rgba(var(--v-theme-on-surface), 0.65);
  background: rgba(var(--v-theme-on-surface), 0.1);
}

.sidebar-ctx-icon-grid {
  display: grid;
  grid-template-columns: repeat(8, 28px);
  column-gap: 1px;
  row-gap: 2px;
}

.sidebar-ctx-cat-header {
  grid-column: 1 / -1;
  font-size: 0.58rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  opacity: 0.45;
  padding: 5px 0 2px;
  line-height: 1;
}

.sidebar-ctx-icon-btn {
  width: 28px;
  height: 28px;
  border-radius: 5px;
  border: 2px solid transparent;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition: border-color 0.12s;
}

.sidebar-ctx-icon-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 2px;
  display: block;
}

.sidebar-ctx-icon-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.sidebar-ctx-icon-btn.selected {
  border-color: rgba(var(--v-theme-on-surface), 0.65);
  background: rgba(var(--v-theme-on-surface), 0.1);
}

.sidebar-ctx-color-section-header {
  font-size: 0.58rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  opacity: 0.45;
  padding: 0 0 5px;
  line-height: 1;
}

.sidebar-ctx-color-grid {
  display: grid;
  grid-template-columns: repeat(6, 26px);
  grid-auto-rows: 26px;
  gap: 5px;
  align-items: center;
}

.sidebar-ctx-color-swatch {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  border: 2px solid transparent;
  cursor: pointer;
  outline: none;
  padding: 0;
  box-sizing: border-box;
  aspect-ratio: 1 / 1;
  position: relative;
  transition:
    transform 0.12s,
    border-color 0.12s;
}

.sidebar-ctx-color-swatch:hover {
  transform: scale(1.12);
  z-index: 1;
}

.sidebar-ctx-color-swatch.selected {
  border-color: #fff;
  transform: scale(1.12);
  z-index: 1;
}

.sidebar-ctx-icon {
  flex-shrink: 0;
  opacity: 0.7;
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

<style>
/* Non-scoped: webkit scrollbar pseudo-elements are suppressed by scoped data-v selectors */
.sidebar-scroll::-webkit-scrollbar {
  width: 6px !important;
}
.sidebar-scroll::-webkit-scrollbar-thumb {
  background: rgba(var(--v-theme-primary), 0.55) !important;
  border-radius: 6px !important;
}
.sidebar-scroll::-webkit-scrollbar-thumb:hover {
  background: rgb(var(--v-theme-primary)) !important;
}
.sidebar-scroll::-webkit-scrollbar-track {
  background: transparent !important;
}

/* Override ProjectFiles header inside the project tree to match subheader style */
.sidebar-project-tree-files .pf-header {
  min-height: 24px !important;
  padding: 2px 0 2px 10px !important;
  padding-right: var(--sidebar-header-action-right-edge) !important;
  font-size: 0.68rem !important;
  font-weight: 700 !important;
  letter-spacing: 0.07em !important;
  gap: 3px !important;
  color: rgba(var(--v-theme-sidebar-text), 0.4) !important;
  text-transform: uppercase !important;
  transition: color 0.12s !important;
  user-select: none !important;
}

.sidebar-project-tree-files .pf-header:hover {
  background: rgba(var(--v-theme-accent), 0.08) !important;
  color: rgba(var(--v-theme-sidebar-text), 0.92) !important;
}

.sidebar-project-tree-files .pf-header-icon {
  display: none !important;
}

.sidebar-project-tree-files .pf-title {
  font-size: 0.68rem !important;
  font-weight: 700 !important;
  letter-spacing: 0.07em !important;
  text-transform: uppercase !important;
  flex: 1 !important;
}

.sidebar-project-tree-files .pf-count {
  flex-shrink: 0;
  margin-left: auto;
  margin-right: 4px;
}

.sidebar-project-tree-files .pf-chevron {
  font-size: 14px !important;
  order: -1;
  opacity: 1 !important;
  color: inherit !important;
  flex-shrink: 0;
}

.sidebar-project-tree-files .pf-spacer {
  display: none !important;
}
</style>
