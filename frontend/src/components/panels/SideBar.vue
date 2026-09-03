<script setup>
import {
  computed,
  ref,
  onBeforeUnmount,
  onMounted,
  watch,
  nextTick,
} from "vue";
import { MODEL_SHELF_ROUTES } from "../../router/routeNames";
import ImageImporter from "../io/ImageImporter.vue";
import CharacterEditor from "../editors/CharacterEditor.vue";
import PictureSetEditor from "../editors/PictureSetEditor.vue";
import ProjectEditor from "../editors/ProjectEditor.vue";
import ProjectFiles from "./ProjectFiles.vue";
import UserSettingsDialog from "../settings/UserSettingsDialog.vue";
import FolderTreeNode from "../editors/FolderTreeNode.vue";
import FolderEditor from "../editors/FolderEditor.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import FolderMappingWizard from "../folders/FolderMappingWizard.vue";
import { useFolderMappingStore } from "../../stores/useFolderMappingStore";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import ShareDialog from "../io/ShareDialog.vue";
import WordmarkLogo from "../WordmarkLogo.vue";
import unknownPerson from "../../assets/unknown-person.png"; // Fallback avatar for characters without thumbnails
import {
  API_BASE_URL,
  appendShareToken,
  isReadOnly,
  sessionContext,
} from "../../utils/apiClient";
import {
  patchCharacter,
  getCharacterSummary,
  getCharacterThumbnail,
  addCharacterFaces,
  addCharacterFacesByFaceId,
  deleteCharacter as apiDeleteCharacter,
} from "../../api/characters";
import {
  patchPictureSet,
  deletePictureSet,
  addPictureToSet,
} from "../../api/pictureSets";
import { inspectLibraryPath } from "../../api/libraries";
import { deleteProject } from "../../api/projects";
import {
  listReferenceFolders,
  listImportFolders,
  deleteFolder,
  browseFilesystem,
  // Aliased: this component's own click handler is also called
  // relocateReferenceFolder.
  relocateReferenceFolder as requestFolderRelocate,
  movePicturesToReferenceFolder,
} from "../../api/folders";
import { setPicturesProject } from "../../api/pictures";
import { getSharedResourceIds, revokeTokensByResource } from "../../api/users";
import { listSortMechanisms } from "../../api/session";
import {
  extractSupportedImportFilesFromDataTransfer,
  isFaceDrag,
  isFileDrag,
  isInternalImageDrag,
  isPictureDrag,
} from "../../utils/media.js";
import {
  characterCountUpdates,
  projectCountUpdates,
} from "../../utils/sidebarCounts.js";
import {
  entityBelongsToProject,
  getEntityProjectIds,
  toggleEntityProjectPatch,
  withEntityProjectIds,
} from "../../utils/projectMembership.js";
import {
  SET_COLORS,
  SET_ICON_CATEGORIES,
  ICON_CARDS,
  nextSetAppearance,
} from "../../utils/setAppearance.js";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useVersionCheck } from "../../composables/useVersionCheck";
import { useSidebarExpansion } from "../../composables/useSidebarExpansion";
import { useRoute } from "vue-router";
import { useDedupStore, scopeKey } from "../../stores/useDedupStore";
import { useMovesStore } from "../../stores/useMovesStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useSortStore } from "../../stores/useSortStore";
import { useProjectStore } from "../../stores/useProjectStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useGridStore } from "../../stores/useGridStore";
import { useFilterStore } from "../../stores/useFilterStore";
import { errorDetail } from "../../utils/apiError";
import { activateOnEnterOrSpace } from "../../utils/keyboardActivation.js";
import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
  useViewStore,
} from "../../stores/useViewStore";

// Publishes id → name maps for the ImageGrid breadcrumb. The sidebar is the
// authoritative name source (it fetches these lists); see useEntityNamesStore.
const entityNames = useEntityNamesStore();
// The character / set / project lists themselves are shared with the image
// context menu and the review scope pickers, so they live in one store (§4).
const entityLists = useEntityListsStore();
const sidebarStore = useSidebarStore();
const lockedSetsStore = useLockedSetsStore();
// Failures and outcomes report through the notice surface rather than a
// blocking native alert() (docs/design/notice-surface.md §1).
const noticeStore = useNoticeStore();

/**
 * Human-readable reason from an axios/HTTP error, for a one-sentence notice.
 * @param {unknown} e
 */
function noticeDetail(e) {
  return errorDetail(e) || e?.message || "Please try again.";
}

// The desktop shell hosts the brand (logo + "new version" alert) in the title
// bar, so the sidebar copies below are gated on !isDesktop.
const isDesktop = typeof window !== "undefined" && !!window.pixlstashDesktop;

const dedupStore = useDedupStore();
const movesStore = useMovesStore();

// Store-direct (Phase 3): the sidebar reads the selection, sort, project and
// preference state it displays straight from the stores and writes changes
// back itself, instead of mirroring all of it through App.vue.
const selectionStore = useSelectionStore();
const sortStore = useSortStore();
const projectStore = useProjectStore();
const userPrefsStore = useUserPrefsStore();

const telemetryIndicatorTitle = computed(() => {
  const active = [];
  if (userPrefsStore.checkForUpdates) active.push("update checks");
  if (userPrefsStore.telemetrySendInstallId)
    active.push("an anonymous install ID");
  if (userPrefsStore.telemetrySendFeatureUsage) active.push("feature usage");
  if (userPrefsStore.telemetrySendErrorReports) active.push("error reports");
  if (userPrefsStore.telemetrySendHardwareProfile)
    active.push("a hardware profile");
  if (!active.length) return "Telemetry is off";
  if (active.length === 1) return `Sending ${active[0]}`;
  const last = active.pop();
  return `Sending ${active.join(", ")} and ${last}`;
});
const gridStore = useGridStore();
const filterStore = useFilterStore();
const viewStore = useViewStore();
const route = useRoute();

// A folder filter and the Duplicates destination each suppress parts of the
// entity lists, so both are derived once here.
const hasFolderFilter = computed(
  () => selectionStore.selectedFolderFilter != null,
);
// Duplicates is addressed by route name, not by a selection sentinel: it shows
// no pictures, so there is no selection to express.
const isDuplicatesView = computed(() => route.name === "duplicates");
// Both of the shelf's routes, from the one list, so the runs tab cannot fall
// out of this the way it did when the two predicates were separate literals.
const isModelsView = computed(() => MODEL_SHELF_ROUTES.includes(route.name));

// A shared library keeps the duplicate affordances VISIBLE and inert rather
// than hiding them: a read-only visitor should still see that the feature
// exists. Every /dedup/* route is owner-only (a duplicate group is defined by
// content identity, so it straddles any token's scope), so nothing here can be
// primed with a count or a badge - the row states the reason instead.
const READ_ONLY_DEDUP_HINT =
  "Duplicate review is only available in your own library";

// The shelf is the same shape of refusal: every /models, /adapters,
// /checkpoints and /model-* route is owner-only, because the shelf lists files
// on the machine PixlStash runs on. Same show-but-disable rule as Duplicates -
// the destination stays visible and says why (issue #1014).
const READ_ONLY_SHELF_HINT =
  "The model shelf is only available in your own library";

// "About your library" is a destination too: it reads the library rather than
// showing it, so it has no selection to express.
const isInsightsView = computed(() => route.name === "insights");

// Same show-but-disable rule again. GET /insights is owner-only because its
// numbers ARE the vault-wide aggregate - narrowing them to a share token's
// scope would leak that out-of-scope pictures exist - so the row is visible,
// inert, and says why rather than quietly vanishing for a demo visitor.
const READ_ONLY_INSIGHTS_HINT =
  "Findings about a library are only available in your own";

// Moves is not a permanent destination the way the three above are: it is a
// to-do queue of moves made outside PixlStash, most libraries never populate
// it (no reference folder has opted into a layout), and unlike the others its
// data is never useful to a share visitor even as a curiosity. So it follows
// the opposite rule - hidden rather than shown-and-disabled - gated on
// movesStore.hasAnyPending, which itself never becomes true for a read-only
// session because nothing here ever calls GET /moves/pending for one.
const isMovesView = computed(() => route.name === "moves");

const props = defineProps({
  backendUrl: { type: String, default: () => API_BASE_URL },
  installType: { type: String, default: "pip" },
  dockerVariant: { type: String, default: "gpu" },
});

const emit = defineEmits([
  "select-duplicates",
  "select-models",
  "select-insights",
  "select-moves",
  "select-character",
  "select-set",
  "import-finished",
  "set-error",
  "set-loading",
  "images-assigned-to-character",
  "faces-assigned-to-character",
  "images-moved",
  "empty-scrapheap",
  "suggest-pictures-for-character",
  "view-project",
  "update:check-for-updates",
  "select-folder",
]);

// "New version available" alert. Disabled on the desktop shell, where the title
// bar owns the check, so it never runs twice.
const {
  latestVersion,
  latestVersionUrl,
  latestSecurityLevel,
  updateAvailable,
  updateDismissed,
  isHighSecurity,
  securityUpdateTitle,
  dismissUpdateAlert,
} = useVersionCheck(
  () => props.installType,
  () => userPrefsStore.checkForUpdates,
  !isDesktop,
);

const securityUpdateClass = computed(() => {
  if (!latestSecurityLevel.value) return "sidebar-update-available";
  return isHighSecurity.value
    ? "sidebar-update-available sidebar-update-security sidebar-update-security--high"
    : "sidebar-update-available sidebar-update-security";
});

const imageImporterRef = ref(null);
const sidebarRootRef = ref(null);
const labelOverflow = ref({});
const labelRefs = new Map();
const labelObservers = new Map();

const dragOverSet = ref(null);

// Which sections, projects and folders are open - hydrated from and written
// back to localStorage by the composable, so the shape of the sidebar survives
// a reload. Section state (People/Sets, the Folders-tab headers) defaults to
// expanded; the folder tree defaults to collapsed.
const {
  peopleSectionCollapsed,
  setsSectionCollapsed,
  referenceFoldersCollapsed,
  importFoldersCollapsed,
  expandedProjectIds,
  projectTreePeopleCollapsed, // project IDs where People is collapsed
  projectTreeSetsCollapsed, // project IDs where Sets is collapsed
  expandedFolderIds, // reference-folder id (number) or subfolder path (string)
  togglePeopleSection,
  toggleSetsSection,
  toggleReferenceFoldersSection,
  toggleImportFoldersSection,
  toggleProjectExpanded,
  toggleProjectTreePeople,
  toggleProjectTreeSets,
  toggleFolderExpanded,
  syncProjectExpansion,
} = useSidebarExpansion();

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
const characters = computed(() => entityLists.characters);
const categoryCounts = ref({
  [ALL_PICTURES_ID]: 0,
  [UNASSIGNED_PICTURES_ID]: 0,
  [SCRAPHEAP_PICTURES_ID]: 0,
});
// Per-project picture counts, keyed by project id. Populated from the shared
// project list's `image_count` (see `utils/sidebarCounts.js`) and read by the
// project tree's count badge only; there is no "unassigned" bucket key, since
// no row renders one.
const projectCounts = ref({});

const flashCountsNextFetch = ref(false);
const countNewTags = ref({});
const knownCountIds = new Set();

const characterThumbnails = ref({});
const setThumbnails = ref({});
const setThumbnailRetryCounts = ref({});
const setThumbnailRetryTimers = new Map();
const SET_THUMBNAIL_MAX_RETRIES = 2;

const dragOverCharacter = ref(null);
const nextCharacterNumber = ref(1);

// --- Picture Sets State ---
const pictureSets = computed(() => entityLists.pictureSets);

// --- Project State ---
const projects = computed(() => entityLists.projects);
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
const collapsedProjectsMenuTriggerRef = ref(null);
const collapsedFoldersMenuTriggerRef = ref(null);
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
const characterMenuPos = ref({ top: "0px", left: "0px" });
const setMoveMenuOpen = ref(false);
const setMoveMenuBtnRef = ref(null);
const setMenuPos = ref({ top: "0px", left: "0px" });

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
const sidebarCtxFolderScopePath = ref(null); // null means the reference folder root
const sidebarCtxImportFolder = ref(null); // import folder object or null
const sidebarCtxProject = ref(null); // { id, name } or null
const sidebarCtxAllPictures = ref(false); // true when ctx opened from All Pictures row
const sidebarCtxScrapheap = ref(false); // true when ctx opened from the Scrapheap row
const sidebarCtxDeleteIds = ref([]); // character IDs to delete via context menu
// Right-click on a section HEADER (not an item): 'people' | 'sets' |
// 'reference-folders' | 'import-folders', or null. Offers the "create/add" action.
const sidebarCtxHeader = ref(null);
// Right-click on empty/uncovered sidebar space: offers the view toggles
// (auto-hide, dock mode).
const sidebarCtxEmpty = ref(false);

// Computed style for the main context menu - opens upward when near the bottom.
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
// Shared resource IDs - drives the share-link icon overlay on sidebar items
const sharedCharacterIds = ref(new Set());
const sharedSetIds = ref(new Set());
const sharedProjectIds = ref(new Set());

// Confirm-revoke-all dialog state
const revokeSharesDialogOpen = ref(false);
const revokeSharesPending = ref(null); // { resourceType, resourceId, label }

// Share dialog state
const shareDialogOpen = ref(false);
const shareDialogPending = ref(null); // { resourceType, resourceId, label }

// Worst-case height of a move/create flyout. Keep in sync with the
// .sidebar-move-menu max-height CSS rule below.
const MOVE_MENU_MAX_H = 420;

// Position a move/create flyout relative to its trigger button, returning a
// ready-to-bind style object. Opens downward (anchored at the trigger's
// bottom) when there is room; otherwise flips upward and anchors the menu's
// bottom just above the trigger so it stays attached, matching the flip
// behaviour of sidebarCtxMenuStyle. An over-tall menu still scrolls inside
// its max-height in either direction.
function _moveMenuPos(rect) {
  const margin = 8;
  const left = rect.left + "px";
  // Flip up only when opening downward would push the menu past the viewport
  // bottom and there is more room above the trigger than below it.
  const spaceBelow = window.innerHeight - rect.bottom;
  const spaceAbove = rect.top;
  if (spaceBelow < MOVE_MENU_MAX_H + margin && spaceAbove > spaceBelow) {
    // Anchor the menu's bottom just above the trigger's top edge.
    return { bottom: window.innerHeight - rect.top + 4 + "px", left };
  }
  return { top: rect.bottom + 4 + "px", left };
}

function openCharacterMoveMenu(event) {
  const el = event?.currentTarget ?? event?.target;
  if (el) {
    characterMenuPos.value = _moveMenuPos(el.getBoundingClientRect());
  }
  characterMoveMenuOpen.value = !characterMoveMenuOpen.value;
}

function openSetMoveMenu(event) {
  const el = event?.currentTarget ?? event?.target;
  if (el) {
    setMenuPos.value = _moveMenuPos(el.getBoundingClientRect());
  }
  setMoveMenuOpen.value = !setMoveMenuOpen.value;
}

// --- Character Editor State ---
const characterEditorOpen = ref(false);
const characterEditorCharacter = ref(null);

const setEditorOpen = ref(false);
const setEditorSet = ref(null);
const settingsDialogOpen = ref(false);
// Nav entry the settings dialog should land on. Set by `openSettingsDialog(tab)`
// so a caller can deep-link (e.g. the scrapheap header's "change" link).
const settingsDialogInitialTab = ref("");
// --- Reference Folders (Folders tab) ---
const sidebarPrimaryTab = ref("library"); // 'library' | 'folders'
const referenceFolders = ref([]);
const referenceFoldersLoading = ref(false);
const importFolders = ref([]);
const importFoldersLoading = ref(false);
const inDocker = ref(false);
const referenceFoldersImageRoot = ref(null);
// expandedFolderIds / referenceFoldersCollapsed / importFoldersCollapsed are
// persisted - see useSidebarExpansion() at the top of this component.
const folderBrowseCache = ref({}); // keyed by path → { entries, loading, image_count }
const selectedFolderKey = ref(null); // 'rf-{id}' | 'path-{path}' | 'if-{id}' | null
const selectedFolderReferenceId = ref(null); // numeric reference-folder id or null
const dragOverReferenceTargetKey = ref(null);

// Reference folder editor state
const referenceFolderEditorOpen = ref(false);
const referenceFolderEditorFolder = ref(null); // null = create, object = edit

// v1.11 Phase 3: adding a new reference folder goes through the mapping
// wizard instead of the plain editor - see `openReferenceFolderEditor` below.
// The editor is unchanged for *editing* an already-registered one (sync
// toggles, sidecar suffixes), which the wizard has no opinion about.
const mappingStore = useFolderMappingStore();
const librariesStore = useLibrariesStore();

function _samePath(a, b) {
  const norm = (p) => String(p || "").replace(/[\\/]+$/, "");
  return norm(a) !== "" && norm(a) === norm(b);
}

// The pending mapping this library can act on. A `local_import` entry names
// the library root it was saved for; shown or auto-opened against any OTHER
// library it would offer to "set up" a library that is already set up - which
// is exactly what happened when a stale entry met a re-attached vault. The
// listing may not carry paths (a remote session), and then there is no way
// to be sure, so nothing is offered.
const pendingForThisLibrary = computed(() => {
  const entry = mappingStore.pending;
  if (!entry) return null;
  if (entry.mode !== "local_import") return entry;
  return _samePath(entry.path, librariesStore.activeLibrary?.path)
    ? entry
    : null;
});
const importFolderEditorOpen = ref(false);
const importFolderEditorFolder = ref(null); // null = create, object = edit
const addFolderTypeDialogOpen = ref(false);
const referenceFolderRelocateOpen = ref(false);
const referenceFolderRelocateFolder = ref(null);
const referenceFolderRelocateDestination = ref("");
const referenceFolderRelocateBrowseOpen = ref(false);
const referenceFolderRelocateLoading = ref(false);
const referenceFolderRelocateError = ref("");
const referenceFolderRelocateResult = ref("");

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
  if (rf === null) {
    // Adding a folder is "Add a library" now: a folder indexed in place as
    // the library's own storage. This used to open the mapping wizard in
    // reference mode, which, pointed at the library root, registered the
    // whole library as one reference folder.
    openFolderMappingWizard();
    return;
  }
  referenceFolderEditorFolder.value = rf;
  referenceFolderEditorOpen.value = true;
}

function closeReferenceFolderEditor() {
  referenceFolderEditorOpen.value = false;
  referenceFolderEditorFolder.value = null;
}

// The wizard's open state is the store's, so Settings › Libraries and the
// empty library's "Choose a folder…" open this same mounted instance.
function openFolderMappingWizard(resume = null) {
  mappingStore.openWizard(resume);
}

async function folderMappingWizardCommitted() {
  mappingStore.closeWizard();
  await fetchReferenceFolders();
  // A newly added folder may be active-but-unscanned, same as a plain add.
  _startFolderStatusPoll();
}

// "Add a library"'s "Yes, build this library" saves a `local_import` entry
// with `autoCommit` and switches the active library before this component
// even exists - reopening this same wizard is therefore this session's ONLY
// chance to run that commit, unlike the ordinary "Finish organising…" row
// below, which only ever needs a click because its read is already kept
// server-side either way. `mappingStore.pending` is left untouched by this
// auto-open, so if the owner cancels, the row still offers it again.
// Watched rather than checked once on mount: the library list that says
// which root is active loads after this component does.
let autoOpenedPendingMapping = false;
watch(
  () => pendingForThisLibrary.value,
  (entry) => {
    if (autoOpenedPendingMapping || isReadOnly.value) return;
    if (entry?.mode !== "local_import") return;
    autoOpenedPendingMapping = true;
    openFolderMappingWizard(entry);
  },
  { immediate: true },
);

// The empty library, when its own folder is not empty. The desktop's first
// run creates the vault in whatever folder was chosen, and the web flow's
// "Add a library" only saves a pending entry for folders IT read, so a vault
// made over loose pictures has nothing to bring the wizard up. The grid says
// the library is empty; this asks the server what is on disk and opens the
// same wizard the sidebar's row would, against the library root, as a
// `local_import` with no read yet. Once per page load, like the auto-open
// above: a cancelled read is saved as pending and the row offers it again.
// The grid asks twice in one tick (`library-empty`, then `library-loaded`),
// and App.vue holds the telemetry question on the second answer, so every
// caller gets the one in-flight offer rather than an instant "already
// asked": settling before the wizard had opened is what let the question
// through on a fresh desktop library.
let loosePicturesOffer = null;
function offerLoosePictures() {
  if (isReadOnly.value || mappingStore.pending) return Promise.resolve();
  loosePicturesOffer ??= _offerLoosePictures();
  return loosePicturesOffer;
}

/**
 * A folder read the desktop startup screen finished while the GPU runtime
 * downloaded, carrying the read's own RESULT. The task id would not do: the
 * task lives in the server's memory and the backend restarts onto the GPU
 * runtime before the app loads, so asking for it answered "Task not found".
 * Null in a browser, and on a desktop launch that had nothing to read.
 */
async function takeParkedFolderRead() {
  const desktop =
    typeof window !== "undefined" ? window.pixlstashDesktop : null;
  if (!desktop?.takePendingMapping) return null;
  try {
    return (await desktop.takePendingMapping()) || null;
  } catch (error) {
    console.warn("Could not read the startup screen's folder read", { error });
    return null;
  }
}

async function _offerLoosePictures() {
  if (!librariesStore.hasLoadedSuccessfully) await librariesStore.refresh();
  const path = librariesStore.activeLibrary?.path;
  if (!path || !librariesStore.canManage) return;
  // On desktop the startup screen may have read this very folder already,
  // alongside the runtime download. Resuming that read is the whole point of
  // doing it there: the wizard opens on its questions instead of on a second
  // progress bar over an empty grid.
  const parked = await takeParkedFolderRead();
  if (parked?.result && parked.path === path) {
    autoOpenedPendingMapping = true;
    openFolderMappingWizard({
      path,
      result: parked.result,
      mode: "local_import",
    });
    return;
  }
  try {
    const verdict = await inspectLibraryPath(path);
    if (verdict?.picture_count > 0) {
      // The read the wizard starts saves a pending entry, which the watch
      // above would otherwise take as its cue to open the wizard again.
      autoOpenedPendingMapping = true;
      openFolderMappingWizard({ path, mode: "local_import" });
    }
  } catch (error) {
    console.warn("Could not check the library folder for pictures", {
      path,
      error,
    });
  }
}

function pathParent(path) {
  const raw = String(path || "");
  if (!raw || raw === "/") return "/";
  const trimmed = raw.replace(/[\\/]+$/, "");
  if (/^[A-Za-z]:$/.test(trimmed)) return `${trimmed}\\`;
  const match = trimmed.match(/^(.*)[\\/][^\\/]+$/);
  if (!match) return "/";
  const parent = match[1];
  if (/^[A-Za-z]:$/.test(parent)) return `${parent}\\`;
  return parent || "/";
}

function openReferenceFolderRelocateDialog(folder) {
  if (!folder || inDocker.value) return;
  referenceFolderRelocateFolder.value = folder;
  referenceFolderRelocateDestination.value = "";
  referenceFolderRelocateError.value = "";
  referenceFolderRelocateResult.value = "";
  referenceFolderRelocateOpen.value = true;
}

function closeReferenceFolderRelocateDialog() {
  if (referenceFolderRelocateLoading.value) return;
  referenceFolderRelocateOpen.value = false;
  referenceFolderRelocateFolder.value = null;
  referenceFolderRelocateDestination.value = "";
  referenceFolderRelocateError.value = "";
  referenceFolderRelocateResult.value = "";
}

const referenceFolderRelocateInitialPath = computed(() =>
  pathParent(referenceFolderRelocateFolder.value?.folder || ""),
);

const relocationRegisteredPaths = computed(() => {
  const currentId = Number(referenceFolderRelocateFolder.value?.id);
  return referenceFolders.value
    .filter((rf) => Number(rf.id) !== currentId)
    .map((rf) => rf.folder.replace(/[\\/]+$/, ""));
});

async function relocateReferenceFolder() {
  const folder = referenceFolderRelocateFolder.value;
  const destination = referenceFolderRelocateDestination.value.trim();
  if (!folder?.id || !destination) return;
  referenceFolderRelocateLoading.value = true;
  referenceFolderRelocateError.value = "";
  referenceFolderRelocateResult.value = "";
  try {
    const data = await requestFolderRelocate(folder.id, destination);
    await fetchReferenceFolders();
    folderBrowseCache.value = {};
    browseExpandedFolders();
    emit("images-moved", {
      imageIds: data?.moved_picture_ids || [],
      kind: "reference-folder",
      refresh: true,
    });
    referenceFolderRelocateResult.value = `Moved ${data?.moved_entry_count ?? 0} item${data?.moved_entry_count === 1 ? "" : "s"} and updated ${data?.rewritten_count ?? 0} picture path${data?.rewritten_count === 1 ? "" : "s"}.`;
  } catch (error) {
    referenceFolderRelocateError.value =
      errorDetail(error) || error?.message || "Relocation failed.";
  } finally {
    referenceFolderRelocateLoading.value = false;
  }
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
  // Informational outcome, not a decision - a notice, not a blocking dialog.
  // Sticky (no auto-dismiss) because it asks the user to go and do something.
  noticeStore.push({
    level: "info",
    text: "Docker: restart the PixlStash container with the new folder mount, then reopen PixlStash.",
    timeout: 0,
    key: "docker-restart-prompt",
  });
}

async function referenceFolderSaved(savedFolder = null) {
  const createdNewFolder = !referenceFolderEditorFolder.value?.id;
  closeReferenceFolderEditor();
  await fetchReferenceFolders();
  if (savedFolder?.relocation) {
    const relocation = savedFolder.relocation;
    const issues =
      Number(relocation.missing_count || 0) +
      Number(relocation.unmatched_count || 0);
    const issueText = issues
      ? ` ${issues} item${issues === 1 ? "" : "s"} need attention.`
      : "";
    // A partial outcome (some items may need attention) reads as `warning`;
    // a clean relocation is a plain success.
    const relocationText = `Reference folder relocated - rewrote ${relocation.rewritten_count || 0} image path${relocation.rewritten_count === 1 ? "" : "s"}.${issueText}`;
    if (issues) {
      noticeStore.warning(relocationText, { key: "reference-relocated" });
    } else {
      noticeStore.success(relocationText, { key: "reference-relocated" });
    }
  }
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
  sidebarStore.folderScanning = false;
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
      sidebarStore.folderScanning = Boolean(newFolder.last_checked == null);
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
    sidebarStore.folderScanning = false;
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
    sidebarStore.folderScanning = false;
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
  sidebarStore.folderScanning = val;
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
    const body = await listReferenceFolders();
    referenceFolders.value = body?.folders ?? [];
    entityNames.mergeRefFolderLabels(referenceFolders.value);
    inDocker.value = Boolean(body?.in_docker);
    referenceFoldersImageRoot.value = body?.image_root ?? null;
    // In non-Docker mode we eagerly browse roots so we know which have
    // subdirectories (controls whether the expand chevron is shown).
    if (!inDocker.value) {
      referenceFolders.value.forEach((rf) => browseFolderPath(rf.folder, true));
      // Subfolders expanded in an earlier session start out with no listing of
      // their own, so the restored tree would render them empty.
      browseExpandedFolderPaths();
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
    const body = await listImportFolders();
    importFolders.value = body?.folders ?? [];
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

/**
 * Fetch a listing for every subfolder the user has expanded. Their entries live
 * in `folderBrowseCache`, which is per-session: a tree restored from a previous
 * session (or re-rendered after the cache was dropped) has the nodes but not
 * their children until they are browsed again.
 */
function browseExpandedFolderPaths() {
  for (const key of expandedFolderIds.value) {
    if (typeof key === "string") void browseFolderPath(key, true);
  }
}

/** Re-browse everything currently expanded, after the browse cache is cleared. */
function browseExpandedFolders() {
  for (const rf of referenceFolders.value) {
    if (expandedFolderIds.value.has(rf.id)) {
      void browseFolderPath(rf.folder, true);
    }
  }
  browseExpandedFolderPaths();
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
    const listing = await browseFilesystem(path);
    const entries = listing?.entries ?? [];
    const imageCount = Number(listing?.image_count);
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
  } catch {
    folderBrowseCache.value = {
      ...folderBrowseCache.value,
      [path]: { entries: [], loading: false, image_count: null, error: true },
    };
  }
}

/* Whether a project's People / Sets caption has anything under it.
   One helper per caption rather than the test inlined twice, because the
   chevron's visibility and the row's inert state must never disagree: a live
   caption with no chevron, or a dimmed caption you can still collapse, are both
   worse than either state on its own. */
function projectHasPeople(projectId) {
  return sortedCharacters.value.some((c) =>
    entityBelongsToProject(c, projectId),
  );
}

function projectHasSets(projectId) {
  return nonReferenceSets.value.some((s) =>
    entityBelongsToProject(s, projectId),
  );
}

/* Whether a reference-folder row can disclose children, i.e. whether its
   chevron is drawn. The slot is reserved either way (`.sidebar-row-glyph`), so
   this only decides visibility and never the row's left edge. Not browsable in
   Docker, and not-yet-browsed counts as "can", so the affordance is there
   before the first browse returns. */
function referenceFolderCanDisclose(rf) {
  if (inDocker.value) return false;
  const cached = folderBrowseCache.value[rf.folder];
  if (!cached || cached.loading) return true;
  return (cached.entries?.length ?? 0) > 0;
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
  sidebarStore.folderScanning = selectedFolderScanning.value;
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
    const [referenceBody, importBody] = await Promise.all([
      listReferenceFolders(),
      listImportFolders(),
    ]);
    const folders = referenceBody?.folders ?? [];
    const updatedImportFolders = importBody?.folders ?? [];
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
    // Ignore transient errors - just try again next tick.
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
  // emit select-* / navigate / clear the grid's selection - switching a tab
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

  // Rotate on from the newest set, skipping what the siblings already use.
  const siblingScope =
    defaultProjectId !== null
      ? nonReferenceSets.value.filter((s) =>
          entityBelongsToProject(s, defaultProjectId),
        )
      : nonReferenceSets.value;

  setEditorSet.value = {
    ...(defaultProjectId !== null ? { project_id: defaultProjectId } : {}),
    ...nextSetAppearance(nonReferenceSets.value, siblingScope),
  };
  setEditorOpen.value = true;
}

function projectMenuItems(container) {
  return Array.from(container?.children ?? []).filter(
    (item) => item.matches?.('button[role="menuitem"]') && !item.disabled,
  );
}

function focusProjectMenuItem(container, index) {
  const items = projectMenuItems(container);
  if (!items.length) return;
  const wrappedIndex = ((index % items.length) + items.length) % items.length;
  items[wrappedIndex].focus();
}

function closeProjectMenu({ restoreFocus = false } = {}) {
  clearTimeout(_projectSubCloseTimer);
  projectMenuSection.value = null;
  projectMenuOpen.value = false;
  if (restoreFocus) {
    nextTick(() => collapsedProjectBtnRef.value?.focus());
  }
}

function closeProjectMenuForTab() {
  // The menus are teleported to <body>, so their DOM order is unrelated to the
  // opener. Put focus back on the opener before the browser performs Tab's
  // default move; forward and reverse Tab then continue from the logical place.
  collapsedProjectBtnRef.value?.focus();
  closeProjectMenu();
}

function openProjectMenu({ focusFirst = false } = {}) {
  if (sidebarStore.effectiveDocked && collapsedProjectBtnRef.value) {
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
  projectMenuOpen.value = true;
  if (focusFirst) {
    nextTick(() => focusProjectMenuItem(collapsedProjectMenuRef.value, 0));
  }
}

function toggleProjectMenu(event) {
  if (projectMenuOpen.value) {
    closeProjectMenu();
    return;
  }
  openProjectMenu({ focusFirst: event?.detail === 0 });
}

function onCollapsedProjectTriggerKeydown(event) {
  if (event.key === "ArrowDown" || event.key === "ArrowRight") {
    event.preventDefault();
    openProjectMenu({ focusFirst: true });
    return;
  }
  if (event.key === "Escape" && projectMenuOpen.value) {
    event.preventDefault();
    closeProjectMenu({ restoreFocus: true });
  }
}

let _projectSubCloseTimer = null;

function openProjectSubMenu(section, event, focusFirst = false) {
  clearTimeout(_projectSubCloseTimer);
  const rect = event.currentTarget.getBoundingClientRect();
  projectMenuSubPos.value = { top: rect.top - 4, left: rect.right + 4 };
  projectMenuSection.value = section;
  if (focusFirst) {
    nextTick(() => focusProjectMenuItem(collapsedProjectSubMenuRef.value, 0));
  }
}

function focusProjectSubMenuTrigger(section) {
  const trigger =
    section === "projects"
      ? collapsedProjectsMenuTriggerRef.value
      : collapsedFoldersMenuTriggerRef.value;
  nextTick(() => trigger?.focus());
}

function closeProjectSubMenu({ restoreFocus = false } = {}) {
  const section = projectMenuSection.value;
  projectMenuSection.value = null;
  if (restoreFocus && section) focusProjectSubMenuTrigger(section);
}

function moveProjectMenuFocus(event, container) {
  const items = projectMenuItems(container);
  if (!items.length) return false;
  const currentIndex = items.indexOf(document.activeElement);
  let nextIndex = null;
  if (event.key === "ArrowDown") nextIndex = currentIndex + 1;
  if (event.key === "ArrowUp")
    nextIndex = currentIndex < 0 ? items.length - 1 : currentIndex - 1;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = items.length - 1;
  if (nextIndex === null) return false;
  event.preventDefault();
  focusProjectMenuItem(container, nextIndex);
  return true;
}

function onProjectMenuKeydown(event) {
  if (moveProjectMenuFocus(event, collapsedProjectMenuRef.value)) return;
  if (event.key === "ArrowRight") {
    const section = document.activeElement?.dataset?.projectSubmenu;
    if (section) {
      event.preventDefault();
      openProjectSubMenu(
        section,
        { currentTarget: document.activeElement },
        true,
      );
    }
    return;
  }
  if (event.key === "Escape" || event.key === "ArrowLeft") {
    event.preventDefault();
    closeProjectMenu({ restoreFocus: true });
    return;
  }
  if (event.key === "Tab") closeProjectMenuForTab();
}

function onProjectSubMenuKeydown(event) {
  if (moveProjectMenuFocus(event, collapsedProjectSubMenuRef.value)) return;
  if (event.key === "Escape" || event.key === "ArrowLeft") {
    event.preventDefault();
    closeProjectSubMenu({ restoreFocus: true });
    return;
  }
  if (event.key === "Tab") closeProjectMenuForTab();
}

function selectGlobalFromProjectMenu() {
  selectLibraryTab("global");
  selectCharacter(ALL_PICTURES_ID, "All Pictures");
  closeProjectMenu({ restoreFocus: true });
}

function selectProjectFromProjectMenu(project) {
  selectLibraryTab("project");
  selectProjectNode(project);
  closeProjectMenu({ restoreFocus: true });
}

function openAddFolderFromProjectMenu() {
  closeProjectMenu();
  openAddFolderTypeDialog();
}

function selectFolderFromProjectMenu(folder, kind) {
  selectFoldersTab();
  handleFolderNodeSelect(`${kind === "reference" ? "rf" : "if"}-${folder.id}`, {
    ...(kind === "reference"
      ? { referenceFolderId: folder.id, pathPrefix: folder.folder }
      : { importSourceFolder: folder.folder, importFolderId: folder.id }),
    label: folder.label || folder.folder,
  });
  closeProjectMenu({ restoreFocus: true });
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
  closeProjectMenu();
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
    await deleteProject(project.id);
    await projectDeleted(project.id);
  } catch (e) {
    console.error("Failed to delete project", e);
    noticeStore.error(`Couldn't delete that project. ${noticeDetail(e)}`, {
      key: "project-delete",
    });
  }
}

const sortedProjects = computed(() =>
  [...projects.value].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
  ),
);

// Auto-expand projects the first time they appear in the tree, except the ones
// the user collapsed in an earlier session. This keeps projects open by default
// without preventing a manual collapse - or undoing a remembered one - and
// forgets projects that no longer exist.
watch(
  () => sortedProjects.value.map((p) => p.id),
  (ids) => syncProjectExpansion(ids),
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
    selectionStore.selectedCharacter &&
    selectionStore.selectedCharacter !== ALL_PICTURES_ID &&
    selectionStore.selectedCharacter !== UNASSIGNED_PICTURES_ID &&
    selectionStore.selectedCharacter !== SCRAPHEAP_PICTURES_ID
  ) {
    const char =
      characters.value.find((c) => c.id === selectionStore.selectedCharacter) ||
      null;
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
    Array.isArray(selectionStore.selectedSetIds) &&
    selectionStore.selectedSetIds.length
      ? selectionStore.selectedSetIds[0]
      : selectionStore.selectedSet;
  if (!primarySetId) return null;
  return pictureSets.value.find((pset) => pset.id === primarySetId) || null;
});

const selectedSetIdSet = computed(
  () =>
    new Set(
      (Array.isArray(selectionStore.selectedSetIds)
        ? selectionStore.selectedSetIds
        : []
      )
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0),
    ),
);

const hasSingleSelectedSet = computed(() => selectedSetIdSet.value.size === 1);

const selectedCharacterIdSet = computed(
  () =>
    new Set(
      (Array.isArray(selectionStore.selectedCharacterIds)
        ? selectionStore.selectedCharacterIds
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
  return sortedCharacters.value.filter((c) =>
    entityBelongsToProject(c, selectedProjectId.value),
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
  const globalItems = all.filter((c) => getEntityProjectIds(c).length === 0);
  const projectsSorted = [...projects.value].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  const groups = [];
  if (globalItems.length > 0) {
    groups.push({ label: "Global", projectId: null, items: globalItems });
  }
  for (const proj of projectsSorted) {
    const items = all.filter((c) => getEntityProjectIds(c)[0] === proj.id);
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
  const globalItems = all.filter((s) => getEntityProjectIds(s).length === 0);
  const projectsSorted = [...projects.value].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  const groups = [];
  if (globalItems.length > 0) {
    groups.push({ label: "Global", projectId: null, items: globalItems });
  }
  for (const proj of projectsSorted) {
    const items = all.filter((s) => getEntityProjectIds(s)[0] === proj.id);
    if (items.length > 0) {
      groups.push({ label: proj.name, projectId: proj.id, items });
    }
  }
  return groups;
});

const visibleSets = computed(() => {
  if (projectViewMode.value === "global") return nonReferenceSets.value;
  return nonReferenceSets.value.filter((s) =>
    entityBelongsToProject(s, selectedProjectId.value),
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
    sortStore.setSimilarityCharacterOptions(options);
  },
  { immediate: true },
);

const similarityCharacterModel = computed({
  get: () => sortStore.selectedSimilarityCharacter,
  // Changing the reference person changes what the similarity sort means, so
  // the grid has to repaint; and on a narrow window the choice is made, so the
  // auto-hidden sidebar gets out of the way.
  set: (value) => {
    sortStore.selectedSimilarityCharacter = value ?? null;
    gridStore.refreshGridVersion();
    if (sidebarStore.sidebarForcedHidden) sidebarStore.hideAutoSidebar();
  },
});

const sidebarThumbnailSizeModel = computed({
  get: () => userPrefsStore.sidebarThumbnailSize ?? 48,
  set: (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(64, Math.max(16, parsed));
    const snapped = Math.round(clamped / 4) * 4;
    userPrefsStore.setSidebarThumbnailSize(snapped);
  },
});

// Dock layout: how many rows of chars/sets can fit in the available scroll height.
// setsCollapsed: collapse sets to a single flyout button first.
// charsCollapsed: also collapse chars if sets-as-menu still doesn't free enough space.
const _dockRowH = computed(() => sidebarThumbnailSizeModel.value + 4);
const _DOCK_DIV = 3; // divider height (1px + 2px margins)
const _addBtn = computed(() => (isReadOnly.value ? 0 : 1)); // extra [+] row when editable
const setsCollapsed = computed(() => {
  if (!sidebarStore.effectiveDocked || sidebarPrimaryTab.value === "folders")
    return false;
  const h = _dockRowH.value;
  const charCount = visibleCharacters.value.length;
  const setCount = visibleSets.value.length;
  if (!setCount || dockedScrollHeight.value === 0) return false;
  const fixedH = 3 * h + _DOCK_DIV; // allPictures + duplicates + scrapheap + divider after allPictures
  const charH = (charCount + _addBtn.value) * h; // always include [+] row when editable
  const setDividerH = _DOCK_DIV;
  const allSetsH = (setCount + _addBtn.value) * h;
  return fixedH + charH + setDividerH + allSetsH > dockedScrollHeight.value;
});
const charsCollapsed = computed(() => {
  if (!sidebarStore.effectiveDocked || sidebarPrimaryTab.value === "folders")
    return false;
  if (!setsCollapsed.value) return false;
  const h = _dockRowH.value;
  const charCount = visibleCharacters.value.length;
  const setCount = visibleSets.value.length;
  if (!charCount || dockedScrollHeight.value === 0) return false;
  const fixedH = 3 * h + _DOCK_DIV; // allPictures + duplicates + scrapheap + divider
  const charH = (charCount + _addBtn.value) * h;
  const setDividerH = setCount > 0 ? _DOCK_DIV : 0;
  const collapsedSetH = setCount > 0 ? h : 0; // sets become 1 button
  return (
    fixedH + charH + setDividerH + collapsedSetH > dockedScrollHeight.value
  );
});

const sidebarFolderChildIconSize = computed(() =>
  Math.round(sidebarThumbnailSizeModel.value * 0.5),
);

const dateFormatModel = computed({
  get: () => userPrefsStore.dateFormat ?? "locale",
  set: (value) => userPrefsStore.setDateFormat(value ?? "locale"),
});

const themeModeModel = computed({
  get: () => userPrefsStore.themeMode ?? "dark",
  set: (value) => userPrefsStore.setThemeMode(value ?? "dark"),
});

const showKeyboardHintModel = computed({
  get: () => userPrefsStore.showKeyboardHint ?? true,
  set: (value) => (userPrefsStore.showKeyboardHint = value ?? true),
});

const sidebarThumbnailSizeLarge = computed(
  () => sidebarThumbnailSizeModel.value + 8,
);

// Scale sidebar text with the thumbnail size: full size at 40px-and-up thumbnails,
// smaller below (≈0.8% per px under 40, floored at 0.85) so dense small-thumb
// layouts stay balanced. Feeds --sidebar-font-scale, which rescales the type-ramp
// tokens on the .sidebar scope (see the stylesheet).
const sidebarFontScale = computed(() => {
  const t = sidebarThumbnailSizeModel.value;
  if (t >= 40) return 1;
  return Math.max(0.85, Math.round((1 - (40 - t) * 0.008) * 1000) / 1000);
});

// Expanded-sidebar width (drag-resizable). Clamp ≈50%–125% of the 240px default.
const SIDEBAR_WIDTH_MIN = 140;
const SIDEBAR_WIDTH_MAX = 300;
const clampSidebarWidth = (v) =>
  Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, Math.round(v)));

// Persisted width; the setter emits up to the store/backend.
const sidebarWidthModel = computed({
  get: () => userPrefsStore.sidebarWidth ?? 240,
  set: (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    userPrefsStore.setSidebarWidth(clampSidebarWidth(parsed));
  },
});

// Live width while dragging (null when not dragging). Kept local so a drag does
// not emit/persist on every frame - we commit once on pointer-up.
const sidebarDragWidth = ref(null);

// Below 150px the expanded sidebar is too tight for the per-entry image counts;
// drop them. Uses the live drag width while resizing, the saved width otherwise.
const sidebarIsNarrow = computed(
  () =>
    !sidebarStore.effectiveDocked &&
    (sidebarDragWidth.value ?? sidebarWidthModel.value ?? 240) < 150,
);

const sidebarThumbStyle = computed(() => {
  const style = {
    "--sidebar-thumb-size": `${sidebarThumbnailSizeModel.value}px`,
    "--sidebar-font-scale": sidebarFontScale.value,
  };
  // Apply the resized width only when expanded; docked width is driven by the
  // thumbnail size in CSS, so leave it to the stylesheet there.
  if (!sidebarStore.effectiveDocked) {
    const w = sidebarDragWidth.value ?? sidebarWidthModel.value;
    style.width = `${w}px`;
  }
  return style;
});

// --- Sidebar resize drag (expanded mode only) ---
let _resizeStartX = 0;
let _resizeStartWidth = 0;

function onSidebarResizeMove(e) {
  sidebarDragWidth.value = clampSidebarWidth(
    _resizeStartWidth + (e.clientX - _resizeStartX),
  );
}

function onSidebarResizeEnd() {
  window.removeEventListener("pointermove", onSidebarResizeMove);
  window.removeEventListener("pointerup", onSidebarResizeEnd);
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  // Commit the final width once (emits → store → persist).
  if (sidebarDragWidth.value != null) {
    sidebarWidthModel.value = sidebarDragWidth.value;
    sidebarDragWidth.value = null;
  }
}

function onSidebarResizeStart(e) {
  if (sidebarStore.effectiveDocked) return;
  e.preventDefault();
  const rect = sidebarRootRef.value?.getBoundingClientRect();
  _resizeStartWidth = rect ? rect.width : sidebarWidthModel.value;
  _resizeStartX = e.clientX;
  sidebarDragWidth.value = clampSidebarWidth(_resizeStartWidth);
  window.addEventListener("pointermove", onSidebarResizeMove);
  window.addEventListener("pointerup", onSidebarResizeEnd);
  document.body.style.cursor = "ew-resize";
  document.body.style.userSelect = "none";
}

function onSidebarResizeKey(e) {
  if (sidebarStore.effectiveDocked) return;
  const step = e.shiftKey ? 24 : 8;
  if (e.key === "ArrowLeft") {
    sidebarWidthModel.value = sidebarWidthModel.value - step;
    e.preventDefault();
  } else if (e.key === "ArrowRight") {
    sidebarWidthModel.value = sidebarWidthModel.value + step;
    e.preventDefault();
  }
}

const reactiveSelectedDescending = ref(sortStore.selectedDescending);

watch(
  () => sortStore.selectedDescending,
  (newValue) => {
    reactiveSelectedDescending.value = newValue;
  },
);

// Changing the sort from the sidebar also closes an auto-hidden sidebar: on a
// narrow window the user has just made their choice and wants to see the grid.
function applySort(sort, descending) {
  sortStore.selectedSort = sort;
  sortStore.selectedDescending = descending;
  if (sidebarStore.sidebarForcedHidden) sidebarStore.hideAutoSidebar();
}

const descendingModel = computed({
  get: () => {
    return reactiveSelectedDescending.value;
  },
  set: (value) => {
    reactiveSelectedDescending.value = value;
    applySort(sortModel.value, value);
  },
});

const sortModel = computed({
  get: () => sortStore.selectedSort,
  set: (value) =>
    applySort(value != null ? String(value) : "", descendingModel.value),
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

/**
 * Open the settings dialog, optionally on a specific nav entry.
 * @param {string} [tab] - nav entry id (e.g. "scrapheap"); Appearance if omitted
 *   or not visible in this session.
 */
function openSettingsDialog(tab = "") {
  settingsDialogInitialTab.value = typeof tab === "string" ? tab : "";
  settingsDialogOpen.value = true;
}

function selectCharacter(id, label = null, event = null) {
  clearCountNew(id);
  const isSpecial =
    id === ALL_PICTURES_ID ||
    id === UNASSIGNED_PICTURES_ID ||
    id === SCRAPHEAP_PICTURES_ID;
  const isMultiToggle = !isSpecial && Boolean(event?.ctrlKey || event?.metaKey);

  if (!isMultiToggle) {
    if (id === ALL_PICTURES_ID) {
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
    if (id === ALL_PICTURES_ID) {
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
    // Nothing selected yet - treat as plain click
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
      id: ALL_PICTURES_ID,
      label: null,
      ids: [],
      projectIds: {},
    });
    return;
  }

  // Keep the primary view unchanged on ctrl-click
  const primaryId = selectionStore.selectedCharacter ?? nextIds[0];
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
  if (!selectionStore.selectedCharacter) return;
  if (!window.confirm("Delete this character?")) return;
  try {
    await apiDeleteCharacter(selectionStore.selectedCharacter);
    // The list is shared state now: the server is asked again rather than the
    // row being spliced out locally.
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
    await Promise.all(ids.map((id) => apiDeleteCharacter(id)));
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
    await deletePictureSet(id);
    emit("select-set", null);
    await fetchPictureSets();
    await fetchSidebarData();
  } catch (e) {
    console.error("Failed to delete set", e);
    noticeStore.error(`Couldn't delete that set. ${noticeDetail(e)}`, {
      key: "set-delete",
    });
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
    await Promise.all(normalizedIds.map((id) => deletePictureSet(id)));
    emit("select-set", null);
    await fetchPictureSets();
    await fetchSidebarData();
  } catch (e) {
    console.error("Failed to delete picture sets", e);
    noticeStore.error(`Couldn't delete those sets. ${noticeDetail(e)}`, {
      key: "set-delete-many",
    });
  }
}

async function deleteReferenceFolderById(id) {
  const folder = referenceFolders.value.find((rf) => rf.id === id);
  if (!folder) return;
  const folderLabel = folder.label || folder.folder;
  if (!window.confirm(`Remove reference folder "${folderLabel}"?`)) return;
  try {
    await deleteFolder("reference", id);
    if (selectedFolderKey.value === `rf-${id}`) {
      selectedFolderKey.value = null;
      emit("select-folder", null);
    }
    await fetchReferenceFolders();
  } catch (e) {
    console.error("Failed to remove reference folder", e);
    noticeStore.error(
      `Couldn't remove that reference folder. ${noticeDetail(e)}`,
      { key: "reference-folder-remove" },
    );
  }
}

async function deleteImportFolderById(id) {
  const folder = importFolders.value.find((entry) => entry.id === id);
  if (!folder) return;
  const folderLabel = folder.label || folder.folder;
  if (!window.confirm(`Remove import folder "${folderLabel}"?`)) return;
  try {
    await deleteFolder("import", id);
    if (selectedFolderKey.value === `if-${id}`) {
      selectedFolderKey.value = null;
      selectedFolderReferenceId.value = null;
      emit("select-folder", null);
      sidebarStore.folderScanning = false;
    }
    await fetchImportFolders();
  } catch (e) {
    console.error("Failed to remove import folder", e);
    noticeStore.error(
      `Couldn't remove that import folder. ${noticeDetail(e)}`,
      {
        key: "import-folder-remove",
      },
    );
  }
}

/** Context-menu target types that can be scoped for a duplicate scan. */
const DEDUP_SCOPE_TYPES = {
  character: {
    scope: "character",
    icon: "mdi-account-box-outline",
    noun: "for",
  },
  set: { scope: "set", icon: "mdi-folder-multiple-image", noun: "in this set" },
  project: {
    scope: "project",
    icon: "mdi-briefcase-outline",
    noun: "in this project",
  },
  folder: {
    scope: "folder",
    icon: "mdi-folder-outline",
    noun: "in this folder",
  },
};

/**
 * Fetch the duplicate count for the object a context menu just opened on.
 *
 * Fire and forget: the row renders a placeholder until the number lands, which
 * is the honest state, and a failed read leaves the row reading "Find
 * duplicates" without a count rather than a wrong zero.
 *
 * @param {string} type - the context-menu target type.
 * @param {Object} item - the target object.
 */
function primeDuplicateCount(type, item) {
  if (isReadOnly.value) return;
  const spec = DEDUP_SCOPE_TYPES[type];
  if (!spec || !item?.id) return;
  dedupStore.fetchScopeCount(spec.scope, item.id);
}

/**
 * The known duplicate count for one scope, or null while it is still unknown.
 * @param {string} type
 * @param {Object} item
 * @returns {number|null}
 */
function duplicateCountFor(type, item) {
  const spec = DEDUP_SCOPE_TYPES[type];
  if (!spec || !item?.id) return null;
  const value = dedupStore.scopeCounts[scopeKey(spec.scope, item.id)];
  return value === undefined ? null : value;
}

/**
 * Open the duplicate queue scoped to the object the menu was opened on.
 *
 * Closes the menu first and acts afterwards, so the menu's own teardown cannot
 * race the navigation for focus.
 *
 * @param {string} type
 * @param {Object} item
 */
function findDuplicatesIn(type, item) {
  const spec = DEDUP_SCOPE_TYPES[type];
  if (!spec || !item?.id) return;
  closeSidebarCtxMenu();
  nextTick(() => {
    emit("select-duplicates", {
      type: spec.scope,
      id: item.id,
      label: item.name || item.label || "",
      icon: spec.icon,
    });
  });
}

function openSidebarCtxMenu(type, item, event) {
  if (isReadOnly.value && (type === "folder" || type === "import-folder"))
    return;
  // Warm the duplicate count for the object under the cursor, so the menu's
  // "Find duplicates in..." row can carry a number rather than open a queue
  // that turns out to be empty. A scoped count reuses cached hashes and is
  // cheap; the store also de-duplicates repeat opens on the same object.
  primeDuplicateCount(type, item);
  // Reset here rather than in every branch: only the scrapheap branch turns it
  // on, so a single top-level reset keeps the per-type blocks below untouched.
  sidebarCtxScrapheap.value = false;
  // Same treatment for the header/empty targets - reset up front so the legacy
  // per-item branches below never have to clear them.
  sidebarCtxHeader.value = null;
  sidebarCtxEmpty.value = false;
  // A right-click on a section header ('people' | 'sets' | 'reference-folders' |
  // 'import-folders') or on empty sidebar space is not tied to a specific item,
  // so clear every item target and short-circuit.
  if (type === "header" || type === "empty") {
    sidebarCtxCharacter.value = null;
    sidebarCtxSet.value = null;
    sidebarCtxFolder.value = null;
    sidebarCtxFolderScopePath.value = null;
    sidebarCtxImportFolder.value = null;
    sidebarCtxProject.value = null;
    sidebarCtxAllPictures.value = false;
    sidebarCtxDeleteIds.value = [];
    if (type === "header") sidebarCtxHeader.value = item;
    else sidebarCtxEmpty.value = true;
    sidebarCtxX.value = event.clientX;
    sidebarCtxY.value = event.clientY;
    sidebarCtxVisible.value = true;
    return;
  }
  if (type === "scrapheap") {
    sidebarCtxCharacter.value = null;
    sidebarCtxSet.value = null;
    sidebarCtxFolder.value = null;
    sidebarCtxFolderScopePath.value = null;
    sidebarCtxImportFolder.value = null;
    sidebarCtxProject.value = null;
    sidebarCtxAllPictures.value = false;
    sidebarCtxScrapheap.value = true;
    sidebarCtxDeleteIds.value = [];
    sidebarCtxX.value = event.clientX;
    sidebarCtxY.value = event.clientY;
    sidebarCtxVisible.value = true;
    return;
  }
  if (type === "character") {
    sidebarCtxCharacter.value = item;
    sidebarCtxSet.value = null;
    sidebarCtxFolder.value = null;
    sidebarCtxFolderScopePath.value = null;
    sidebarCtxImportFolder.value = null;
    sidebarCtxProject.value = null;
    sidebarCtxAllPictures.value = false;
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
      sidebarCtxFolderScopePath.value = null;
      sidebarCtxImportFolder.value = null;
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = false;
    } else if (type === "folder") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = item;
      sidebarCtxFolderScopePath.value = null;
      sidebarCtxImportFolder.value = null;
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = false;
    } else if (type === "folder-path") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = item?.folder || null;
      sidebarCtxFolderScopePath.value = item?.path || null;
      sidebarCtxImportFolder.value = null;
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = false;
    } else if (type === "import-folder") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = null;
      sidebarCtxFolderScopePath.value = null;
      sidebarCtxImportFolder.value = item;
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = false;
    } else if (type === "project") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = null;
      sidebarCtxFolderScopePath.value = null;
      sidebarCtxImportFolder.value = null;
      sidebarCtxProject.value = item;
      sidebarCtxAllPictures.value = false;
    } else if (type === "all-pictures") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxFolder.value = null;
      sidebarCtxFolderScopePath.value = null;
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

// True when the Scrapheap holds nothing to empty - drives the disabled state of
// the context-menu item (a confirm on an empty heap is a dead-end affordance).
// The sidebar row already owns this count, so we read it here rather than reach
// into the grid's `scrapheapEmptyDisabled`.
const scrapheapIsEmpty = computed(
  () => !categoryCounts.value[SCRAPHEAP_PICTURES_ID],
);

// Empty Scrapheap from the sidebar context menu. Navigate into the scrapheap
// first so the grid's post-confirm refetch reconciles the right view, then hand
// off to the grid's existing consent-gated delete-forever-all flow via App.vue.
function emptyScrapheapFromCtx() {
  if (scrapheapIsEmpty.value) return;
  closeSidebarCtxMenu();
  selectCharacter(SCRAPHEAP_PICTURES_ID, "Scrapheap");
  emit("empty-scrapheap");
}

// "Suggest more pictures of <person>" (#636): ranks the whole library against
// this person's reference faces so their un-tagged pictures can be assigned in
// one action. Deliberately does NOT select the person first - the search spans
// the library, and narrowing the view to what is already assigned would hide
// every result it is meant to find.
function suggestPicturesForCharacterFromCtx(character) {
  if (!character?.id) return;
  closeSidebarCtxMenu();
  emit("suggest-pictures-for-character", {
    id: character.id,
    name: character.name,
  });
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
    await patchPictureSet(setId, payload);
    refreshSidebar();
  } catch (e) {
    console.error("Failed to update set appearance", e);
  }
  closeSidebarCtxMenu();
}

// Lock/unlock a set. Locking freezes the set and every picture in it; the only
// PATCH a locked set accepts is this toggle back to unlocked. After it commits
// we refresh the sidebar (row icon + set objects) and the locked-sets store
// (grid/overlay badges); the backend also fires CHANGED_PICTURES for members.
async function toggleSetLock(set) {
  closeSidebarCtxMenu();
  if (!set?.id) return;
  try {
    await patchPictureSet(set.id, { locked: !set.locked });
    refreshSidebar();
    lockedSetsStore.fetch();
  } catch (e) {
    console.error("Failed to toggle set lock", e);
  }
}

// Tooltip on the Delete entry when a set is locked (set-scoped; the store's
// lockReason is picture-scoped for the grid/overlay).
const SET_LOCK_REASON =
  "This set is locked. Unlock it first (Unlock set) to delete it.";

// Tooltip on the lock indicator shown on a locked set row (expanded row icon and
// the collapsed-dock/flyout variants). Single-sourced so every set-row surface
// reads identically.
const SET_LOCKED_ROW_TITLE =
  "Locked - this set is read-only. Right-click and choose Unlock set to edit it.";

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

// Drop-to-set / drop-to-character association is now done server-side: the drop
// target (set_id / character_id) is threaded into the import staging session
// (openStagingSession), and PictureImportTask associates every imported picture
// on commit. The async streaming-staging contract returns no per-file
// results[], so there is nothing to associate client-side here - just re-emit.
function handleImportFinished(payload) {
  emit("import-finished", payload);
}

function startLocalImport(files, projectId = null, target = null) {
  const list = Array.isArray(files) ? files : [];
  if (!list.length) return;
  // `target` carries the set/character the caller was looking at. Only the
  // keys `startImport` actually reads are forwarded -- it takes `setId` and
  // `characterId` and ignores anything else, which is how the grid's
  // `selectedCharacterId` used to go nowhere.
  const options = {
    ...(projectId != null ? { projectId } : {}),
    ...(target?.setId != null ? { setId: target.setId } : {}),
    ...(target?.characterId != null ? { characterId: target.characterId } : {}),
  };
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

function dragOverSetItem(setId, event) {
  // Suppress the image-drop highlight while an entity is being dragged between
  // projects - that drag is not an image assignment.
  if (draggingEntityKind.value) return;
  const verdict = acceptDrop(event, ["pictures", "files"]);
  if (verdict === "ignore") return;
  dropRejected.value = verdict === "reject";
  dragOverSet.value = setId;
}

function dragLeaveSetItem(event) {
  if (event && !leftDropRow(event)) return;
  dragOverSet.value = null;
}

function isCountSelected(id) {
  if (!id) return false;
  return selectionStore.selectedCharacter === id;
}

/**
 * Whether a selection-driven row may render as active at all. The Duplicates
 * view, the model shelf and "About your library" are addressed by ROUTE, not
 * by the selection system, so while any of them is open
 * the underlying selection (kept so back-navigation restores it) must yield
 * the highlight - otherwise the sidebar shows two active destinations. A live
 * folder filter suppresses the same rows for the same reason, so the guards
 * travel together.
 */
const selectionOwnsHighlight = computed(
  () =>
    !hasFolderFilter.value &&
    !isDuplicatesView.value &&
    !isModelsView.value &&
    !isInsightsView.value,
);

const isAllPicturesRowActive = computed(() => {
  if (!selectionOwnsHighlight.value) return false;
  if (selectionStore.selectedCharacter !== ALL_PICTURES_ID) return false;
  if (selectedSetIdSet.value.size > 0) return false;
  return true;
});

// One source for the Scrapheap highlight. The expanded row and the docked rail
// each need it twice (the `active` class and `aria-current`), and four copies of
// the same expression is four chances for the styling and the screen-reader
// state to drift apart.
const isScrapheapRowActive = computed(
  () =>
    selectionStore.selectedCharacter === SCRAPHEAP_PICTURES_ID &&
    selectionOwnsHighlight.value,
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
let sidebarCountEpoch = 0;

function sidebarCountRequestKey() {
  return JSON.stringify({
    mode: projectViewMode.value,
    projectId: selectedProjectId.value,
    characters: characters.value.map((char) => [char.id, char.project_id]),
    projects: projects.value.map((project) => project.id),
  });
}

async function fetchSidebarData() {
  const requestEpoch = (sidebarCountEpoch += 1);
  // The per-character and per-project counts arrive ON the shared lists
  // (`include_counts`), so this pass has to hold those lists before it can read
  // a count off them, and before it can compute a request key that describes
  // what it is about to write. `refreshSidebar` fires `fetchCharacters()` /
  // `fetchProjects()` unawaited a tick earlier, and `useEntityListsStore.refresh`
  // de-duplicates on the kind while a request is in flight, so awaiting here
  // JOINS those reads rather than issuing a second pair. (Awaiting them after
  // computing the key instead would be a bug: a cold list landing mid-flight
  // changes the key and every count write would be discarded as stale.)
  const [characterRows, projectRows] = await Promise.all([
    entityLists.refresh("characters"),
    entityLists.refresh("projects"),
  ]);
  const requestKey = sidebarCountRequestKey();
  const isCurrentRequest = () =>
    requestEpoch === sidebarCountEpoch &&
    requestKey === sidebarCountRequestKey();
  const shouldFlash = flashCountsNextFetch.value;
  // Per-character counts come off the list rows fetched above: one read for
  // the whole tree instead of a `/characters/{id}/summary` per row (#651). Both
  // scopes ship on every row, so a mode switch is a re-read of the same shape;
  // which of the two a mode reads (and which rows have no answer yet) is pinned
  // in `utils/sidebarCounts.js`. Written before the category summaries below so
  // the tree's numbers do not wait on those round-trips.
  if (isCurrentRequest()) {
    for (const { id, count } of characterCountUpdates(
      characterRows,
      projectViewMode.value,
    )) {
      setCategoryCount(id, count, shouldFlash);
    }
  }
  // Same for the projects. The unassigned bucket is not a row in that list, so
  // it stays a single summary request (below).
  if (isCurrentRequest()) {
    for (const { id, count } of projectCountUpdates(projectRows)) {
      projectCounts.value[id] = count;
    }
  }
  // Fetch total image count for END key logic
  try {
    // All images summary
    const data = await getCharacterSummary(ALL_PICTURES_ID, undefined);
    if (isCurrentRequest())
      setCategoryCount(ALL_PICTURES_ID, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching all images summary:", e);
  }
  try {
    // Unassigned images summary
    const unassignedParams =
      projectViewMode.value === "project"
        ? {
            project_id:
              selectedProjectId.value != null
                ? selectedProjectId.value
                : "UNASSIGNED",
          }
        : undefined;
    const data = await getCharacterSummary(
      UNASSIGNED_PICTURES_ID,
      unassignedParams,
    );
    if (isCurrentRequest())
      setCategoryCount(UNASSIGNED_PICTURES_ID, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching unassigned images summary:", e);
  }
  try {
    const data = await getCharacterSummary(SCRAPHEAP_PICTURES_ID, undefined);
    if (isCurrentRequest())
      setCategoryCount(SCRAPHEAP_PICTURES_ID, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching scrapheap images summary:", e);
  }
  // There is deliberately no "pictures in no project" count fetched here.
  // `GET /projects/UNASSIGNED/summary` used to run on every sidebar refresh and
  // write `projectCounts[UNASSIGNED]`, which no template has ever rendered.
  // The tree's only count binding is `projectCounts[p.id]` over real project
  // rows. It cost the owner a round-trip per refresh for nothing, and for a
  // token scoped to a character / picture / set it is a project route that
  // 403s, so it logged a warning on every refresh for a number nobody could
  // have seen. Reinstating it means adding the row that would display it.
  if (isCurrentRequest()) flashCountsNextFetch.value = false;
}

async function fetchCharacters() {
  setLoading(true);
  setError(null);
  try {
    const nextCharacters = await entityLists.refresh("characters");
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
    // No cache-buster: a fresh `?cb=` per call re-downloaded every character
    // thumbnail on every sidebar refresh, against an already-expensive route
    // (#651). Freshness is the *response's* job instead - the route sends
    // `Cache-Control: private, no-cache` with an ETag and answers a conditional
    // request with a 304, so the browser revalidates every time but transfers
    // bytes only when the thumbnail actually changed. Re-adding a buster here
    // would defeat that (integration_architecture.md §9).
    const blob = await getCharacterThumbnail(characterId);

    // Create an object URL for the blob
    const blobUrl = URL.createObjectURL(blob);
    characterThumbnails.value[characterId] = blobUrl;
  } catch (e) {
    console.error(`Failed to fetch thumbnail for character ${characterId}:`, e);
    characterThumbnails.value[characterId] = null;
  }
}

// --- Sorting & Pagination ---
async function fetchSortOptions() {
  try {
    const payload = await listSortMechanisms({ baseUrl: props.backendUrl });

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
    sortStore.setSortOptions(sortOptions.value);
  } catch (e) {
    console.error("Error fetching sort options:", e);
    sortOptions.value = [];
    sortStore.setSortOptions([]);
  }
}

// --- Picture Sets ---
async function fetchProjects() {
  // The store declines the call outright for a token scoped to a non-project
  // resource, which cannot read the projects list.
  const rows = await entityLists.refresh("projects");
  entityNames.mergeProjectNames(rows);
}

async function fetchPictureSets() {
  // Always fetch all sets - in the flat project tree each project filters
  // its own sets client-side, so we must not scope this call to a single project.
  const sets = await entityLists.refresh("sets");
  entityNames.mergeSetNames(sets);
  await updateSetThumbnails(sets);
}

async function fetchSharedIds() {
  if (isReadOnly.value) return; // only owner can query tokens
  try {
    const [charBody, setBody, projBody] = await Promise.all([
      getSharedResourceIds("character"),
      getSharedResourceIds("picture_set"),
      getSharedResourceIds("project"),
    ]);
    sharedCharacterIds.value = new Set(charBody?.ids ?? []);
    sharedSetIds.value = new Set(setBody?.ids ?? []);
    sharedProjectIds.value = new Set(projBody?.ids ?? []);
  } catch (e) {
    console.warn("[SideBar] fetchSharedIds error:", e);
  }
}

async function revokeAllShares(resourceType, resourceId) {
  try {
    await revokeTokensByResource(resourceType, resourceId);
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
  // An entity (set/character) is being moved between projects - that drop is
  // handled by the project header / sub-section zones, not by this image-drop
  // handler. Bail out so we don't try to parse it as image-drag data.
  if (draggingEntityKind.value) return;
  // If this is an internal grid drag (has application/json payload), skip the
  // file-import path - browsers also populate dataTransfer.files for <img> drags.
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
    // Thread the drop target into the staging session so the backend associates
    // every imported picture with this set on commit (no client-side results[]).
    const targetSet = pictureSets.value.find((s) => s.id === setId);
    const options = { setId };
    if (targetSet?.project_id != null) options.projectId = targetSet.project_id;
    imageImporterRef.value?.startImport(files, options);
    return;
  }
  // Get the dragged image IDs from the drag event. A face drag is not a
  // picture drag, and readDraggedImageIds() returns nothing for one.
  const draggedIds = readDraggedImageIds(event);
  if (draggedIds.length === 0) {
    return;
  }

  const targetSet = pictureSets.value.find((s) => s.id === setId);
  if (!targetSet) return;

  try {
    // Add each image to the set
    const addPromises = draggedIds.map(async (picId) => {
      await addPictureToSet(setId, picId);
    });

    await Promise.all(addPromises);

    // Refresh the picture sets to update counts
    await fetchPictureSets();

    // Emit event to parent to remove images from grid
    emit("images-moved", { imageIds: draggedIds });
  } catch (e) {
    const detail = errorDetail(e) || e?.message || String(e);
    if (typeof detail === "string" && detail.includes("already in set")) {
      showNotice("Picture already in set", setId);
      return;
    }
    setError("Failed to add images to set: " + detail, setId, "set");
  }
}

function handleDragOverCharacter(id, event) {
  // Suppress the image-drop highlight while an entity is being dragged between
  // projects - that drag is not an image assignment.
  if (draggingEntityKind.value) return;
  const verdict = acceptDrop(event, ["pictures", "faces", "files"]);
  if (verdict === "ignore") return;
  dropRejected.value = verdict === "reject";
  dragOverCharacter.value = id;
}

function handleDragLeaveCharacter(event) {
  if (event && !leftDropRow(event)) return;
  dragOverCharacter.value = null;
}

// --- Project drop target (assign dragged pictures to a specific project) ---
const dragOverProjectId = ref(null);

function handleDragOverProject(id, event) {
  // Suppress the picture-drop highlight while an entity (character/set) is
  // being dragged between projects - that drag is handled by the project
  // header's entity-move zone (onProjectHeaderDrop), not by a picture assign.
  if (draggingEntityKind.value) return;
  const verdict = acceptDrop(event, ["pictures"]);
  if (verdict === "ignore") return;
  dropRejected.value = verdict === "reject";
  dragOverProjectId.value = id;
}

function handleDragLeaveProject(event) {
  if (event && !leftDropRow(event)) return;
  dragOverProjectId.value = null;
}

function readDraggedImageIds(event) {
  try {
    const data = JSON.parse(
      event?.dataTransfer?.getData("application/json") || "{}",
    );
    // A face drag carries imageIds too (the pictures the faces were found in),
    // so the payload kind, not the presence of imageIds, decides.
    if (data.type !== "image-ids") return [];
    return Array.isArray(data.imageIds) ? data.imageIds.filter(Boolean) : [];
  } catch (e) {
    console.error("Could not parse drag data:", e);
    return [];
  }
}

// Marks the currently hovered row as one that will not take this payload, so
// the row can say no during the drag instead of after it (see .not-droppable).
const dropRejected = ref(false);

/**
 * Decide whether a row takes this drag, and accept it if so.
 *
 * `@dragover.prevent` in the template cannot do this: the modifier calls
 * preventDefault() before the handler runs, which accepts every payload on the
 * page. preventDefault() therefore belongs here, only for the kinds listed.
 *
 * @param {DragEvent} event
 * @param {string[]} kinds - any of "pictures", "faces", "files".
 * @returns {"accept"|"reject"|"ignore"} - "ignore" means the drag is not ours
 *   to judge (an external file drag the window-level importer still handles),
 *   so the row stays unpainted rather than claiming it will refuse the drop.
 */
function acceptDrop(event, kinds) {
  const dt = event?.dataTransfer;
  const accepted =
    (kinds.includes("pictures") && isPictureDrag(dt)) ||
    (kinds.includes("faces") && isFaceDrag(dt)) ||
    (kinds.includes("files") && isFileDrag(dt));
  if (accepted) {
    event.preventDefault();
    if (dt) dt.dropEffect = "move";
    return "accept";
  }
  return isInternalImageDrag(dt) ? "reject" : "ignore";
}

// dragleave also fires when the pointer crosses from a row into one of its own
// children, which flickers the highlight off; only a leave that lands outside
// the row is a real leave.
function leftDropRow(event) {
  const row = event?.currentTarget;
  if (!row?.contains) return true;
  return !row.contains(event.relatedTarget);
}

function handleReferenceFolderDragOver(folderId, scopePath, event) {
  if (draggingEntityKind.value) return;
  const verdict = acceptDrop(event, ["pictures"]);
  if (verdict === "ignore") return;
  dropRejected.value = verdict === "reject";
  dragOverReferenceTargetKey.value = scopePath
    ? `path-${scopePath}`
    : `rf-${folderId}`;
}

function handleReferenceFolderDragLeave(folderId, scopePath, event) {
  if (event && !leftDropRow(event)) return;
  const key = scopePath ? `path-${scopePath}` : `rf-${folderId}`;
  if (dragOverReferenceTargetKey.value === key) {
    dragOverReferenceTargetKey.value = null;
  }
}

async function handleReferenceFolderDrop(folderId, scopePath, event) {
  dragOverReferenceTargetKey.value = null;
  if (draggingEntityKind.value) return;
  const imageIds = readDraggedImageIds(event);
  if (!imageIds.length) return;
  try {
    const data = await movePicturesToReferenceFolder(folderId, imageIds, {
      destinationSubpath: scopePath,
    });
    await fetchReferenceFolders();
    folderBrowseCache.value = {};
    browseExpandedFolders();
    emit("images-moved", {
      imageIds: data?.moved_picture_ids || imageIds,
      kind: "reference-folder",
      refresh: true,
    });
  } catch (e) {
    const detail = e?.response?.data?.detail;
    const failures = detail?.failures || [];
    if (failures.length) {
      const first = failures[0];
      console.error("Failed to move images", e);
      noticeStore.error(
        `Couldn't move ${failures.length} image${failures.length === 1 ? "" : "s"}. ${first.reason || "Please try again."}`,
        { key: "images-move" },
      );
      return;
    }
    console.error("Failed to move images", e);
    noticeStore.error(
      `Couldn't move those images. ${detail?.message || detail || e?.message || "Please try again."}`,
      { key: "images-move" },
    );
  }
}

function handleReferenceFolderNodeContext({ rfId, path, label, event }) {
  const folder = referenceFolders.value.find((rf) => rf.id === rfId);
  if (!folder) return;
  openSidebarCtxMenu("folder-path", { folder, path, label }, event);
}

async function onProjectDrop(projectId, event) {
  dragOverProjectId.value = null;
  // An entity (character/set) is being moved between projects - that drop is
  // handled by onProjectHeaderDrop, not this picture-assign handler. Bail out
  // so we don't try to parse it as image-drag data (and log a spurious error).
  if (draggingEntityKind.value) return;
  if (projectId == null) return;
  // Internal grid drag carries the selected image ids as application/json.
  const imageIds = readDraggedImageIds(event);
  if (!imageIds.length) return;
  try {
    // Picture↔Project is many-to-many (PictureProjectMember); membership is
    // created via the batch /pictures/project endpoint with mode "add".
    // (Patching a picture's direct project_id column does NOT create the
    // membership the project view queries - that returns 200 but shows nothing.)
    await setPicturesProject(imageIds, projectId, {
      mode: "add",
    });
    emit("images-moved", { imageIds });
  } catch (e) {
    console.error("Failed to assign pictures to project:", e);
    if (Number(e?.response?.status) >= 500) {
      noticeStore.error(
        "The project assignment outcome is uncertain. Counts and the current view are reloading before you retry.",
        { key: "images-project-uncertain" },
      );
      await fetchSidebarData();
      emit("images-moved", { imageIds, uncertain: true });
      return;
    }
    noticeStore.error(
      `Couldn't assign those pictures to the project. ${noticeDetail(e)}`,
      { key: "images-project-assign" },
    );
  }
}

async function onCharacterDrop(characterId, event) {
  dragOverCharacter.value = null;
  // An entity (character/set) is being moved between projects - handled by the
  // project header / sub-section drop zones, not by this image-drop handler.
  if (draggingEntityKind.value) return;
  // If this is an internal grid drag (has application/json payload), skip the
  // file-import path - browsers also populate dataTransfer.files for <img> drags.
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
    // Thread the drop target into the staging session so the backend associates
    // every imported picture with this character on commit (no client results[]).
    const options = { characterId };
    if (selectedProjectId.value != null)
      options.projectId = selectedProjectId.value;
    imageImporterRef.value?.startImport(files, options);
    return;
  }
  // Accept faceIds or imageIds from drag event
  let faceIds = [];
  let imageIds = [];
  let dragType;
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
    // The actual "images-assigned-to-character" event is emitted after the
    // assignment is committed (below); emitting it here as well would refresh
    // the grid against not-yet-committed data and also fire wrongly for
    // face-only drags.
  } catch (e) {
    const detail = errorDetail(e) || e?.message || String(e);
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
      await addCharacterFacesByFaceId(characterId, faceIds);
      await fetchSidebarData();
      await fetchCharacterThumbnail(characterId);
      emit("faces-assigned-to-character", { characterId, faceIds });
    } catch (e) {
      console.error("Failed to assign faces to character", e);
      noticeStore.error(
        `Couldn't assign those faces to the person. ${noticeDetail(e)}`,
        { key: "faces-assign-character" },
      );
    }
    return;
  }

  if (imageIds.length === 0) {
    return;
  }

  try {
    // Fallback: assign images to character
    await addCharacterFaces(characterId, imageIds);
    await fetchSidebarData();
    await fetchCharacterThumbnail(characterId);
    emit("images-assigned-to-character", { characterId, imageIds });
  } catch (e) {
    const detail = errorDetail(e) || e?.message || String(e);
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
    // New character was created, increment nextCharacterNumber. The row itself
    // comes from the refetch below - the shared list is never written locally.
    nextCharacterNumber.value++;
  }
  await fetchCharacters(); // Refresh characters
  await fetchSortOptions(); // Ensure sort options include similarity when characters exist
  await fetchPictureSets(); // Refresh picture sets to include reference sets
  closeCharacterEditor();
}

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
      projectStore.projectViewMode != null ||
      projectStore.selectedProjectId != null
    ) {
      _initializing = true;
      if (projectStore.projectViewMode != null)
        projectViewMode.value = projectStore.projectViewMode;
      if (projectStore.selectedProjectId != null) {
        lastUsedProjectId.value = projectStore.selectedProjectId;
        selectedProjectId.value = projectStore.selectedProjectId;
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

// The "screen on next start" half of Phase 5's review (release plan §4): one
// GET on mount, so a backlog left over from while PixlStash was closed shows
// up the moment the sidebar renders rather than waiting for the next scan's
// WebSocket nudge. Read-only sessions never call this - GET /moves/pending is
// owner-only and the row it would feed is hidden for them regardless.
onMounted(() => {
  if (!isReadOnly.value) movesStore.fetchPending();
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
  document.removeEventListener("mousedown", onSidebarCtxOutside);
  document.removeEventListener("keydown", onSidebarCtxKeydown, true);
  // Drop any in-flight sidebar-resize drag listeners.
  onSidebarResizeEnd();
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
  [() => sortedCharacters.value, () => sortStore.selectedSort],
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
  () => selectionStore.selectedCharacter,
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
  // must NOT touch the grid - the grid view follows the route (the single
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
  () => projectStore.selectedProjectId,
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
  () => viewStore.activeFolderKey,
  async (newKey, oldKey) => {
    if (!newKey) {
      // Route left a folder view - clear the sidebar's folder highlight.
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
    if (viewStore.activeFolderKey !== newKey) return;

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
  if (!char || selectedProjectId.value == null) return;
  const membershipPatch = toggleEntityProjectPatch(
    char,
    selectedProjectId.value,
  );
  try {
    await patchCharacter(charId, membershipPatch);
    const idx = characters.value.findIndex((c) => c.id === charId);
    if (idx !== -1) {
      characters.value[idx] = withEntityProjectIds(
        characters.value[idx],
        membershipPatch.project_ids,
      );
    }
    // Reassignment changes per-project and per-character image counts.
    fetchSidebarData();
  } catch (e) {
    console.error("Failed to update character project membership:", e);
  }
}

async function toggleSetProjectMembership(setId) {
  const set = pictureSets.value.find((s) => s.id === setId);
  if (!set || selectedProjectId.value == null) return;
  const membershipPatch = toggleEntityProjectPatch(
    set,
    selectedProjectId.value,
  );
  try {
    await patchPictureSet(setId, membershipPatch);
    const idx = pictureSets.value.findIndex((s) => s.id === setId);
    if (idx !== -1) {
      pictureSets.value[idx] = withEntityProjectIds(
        pictureSets.value[idx],
        membershipPatch.project_ids,
      );
    }
    // Reassignment changes per-project and per-set image counts.
    fetchSidebarData();
  } catch (e) {
    console.error("Failed to update set project membership:", e);
  }
}

// --- Drag-and-drop: move a character / picture set between projects ---
// The dragged entity's kind+id are stashed in module refs (this is a
// same-document drag, and dataTransfer is not readable during dragover in most
// browsers), so the drop zones can react without reading dataTransfer.
const draggingEntityKind = ref(null); // 'character' | 'set' | null
const draggingEntityId = ref(null);
const moveDragOverProjectId = ref(null); // project header highlight
const moveDragOverPeopleId = ref(null); // People area highlight
const moveDragOverSetsId = ref(null); // Sets area highlight

function onEntityDragStart(kind, id, event) {
  if (isReadOnly.value) return;
  draggingEntityKind.value = kind;
  draggingEntityId.value = id;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", `${kind}:${id}`);
  }
}

function onEntityDragEnd() {
  draggingEntityKind.value = null;
  draggingEntityId.value = null;
  moveDragOverProjectId.value = null;
  moveDragOverPeopleId.value = null;
  moveDragOverSetsId.value = null;
}

async function moveCharacterToProject(charId, projectId) {
  const char = characters.value.find((c) => c.id === charId);
  const currentProjectIds = getEntityProjectIds(char);
  if (
    !char ||
    (currentProjectIds.length === 1 && currentProjectIds[0] === projectId)
  )
    return;
  try {
    await patchCharacter(charId, { project_id: projectId });
    const idx = characters.value.findIndex((c) => c.id === charId);
    if (idx !== -1) {
      characters.value[idx] = withEntityProjectIds(characters.value[idx], [
        projectId,
      ]);
    }
    // Reassignment changes per-project and per-character image counts.
    fetchSidebarData();
  } catch (e) {
    console.error(
      `Failed to move character ${charId} to project ${projectId}:`,
      e,
    );
  }
}

async function moveSetToProject(setId, projectId) {
  const set = pictureSets.value.find((s) => s.id === setId);
  const currentProjectIds = getEntityProjectIds(set);
  if (
    !set ||
    (currentProjectIds.length === 1 && currentProjectIds[0] === projectId)
  )
    return;
  try {
    await patchPictureSet(setId, { project_id: projectId });
    const idx = pictureSets.value.findIndex((s) => s.id === setId);
    if (idx !== -1) {
      pictureSets.value[idx] = withEntityProjectIds(pictureSets.value[idx], [
        projectId,
      ]);
    }
    // Reassignment changes per-project and per-set image counts.
    fetchSidebarData();
  } catch (e) {
    console.error(`Failed to move set ${setId} to project ${projectId}:`, e);
  }
}

// Project header - accepts both characters and sets.
function onProjectHeaderDragOver(projectId, event) {
  if (!draggingEntityKind.value) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  moveDragOverProjectId.value = projectId;
}

function onProjectHeaderDragLeave() {
  moveDragOverProjectId.value = null;
}

function onProjectHeaderDrop(projectId) {
  const kind = draggingEntityKind.value;
  const id = draggingEntityId.value;
  moveDragOverProjectId.value = null;
  if (id == null) return;
  if (kind === "character") moveCharacterToProject(id, projectId);
  else if (kind === "set") moveSetToProject(id, projectId);
}

// People area - accepts only characters.
function onProjectPeopleDragOver(projectId, event) {
  if (draggingEntityKind.value !== "character") return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  moveDragOverPeopleId.value = projectId;
}

function onProjectPeopleDragLeave() {
  moveDragOverPeopleId.value = null;
}

function onProjectPeopleDrop(projectId) {
  const id = draggingEntityId.value;
  moveDragOverPeopleId.value = null;
  if (draggingEntityKind.value === "character" && id != null) {
    moveCharacterToProject(id, projectId);
  }
}

// Sets area - accepts only picture sets.
function onProjectSetsDragOver(projectId, event) {
  if (draggingEntityKind.value !== "set") return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  moveDragOverSetsId.value = projectId;
}

function onProjectSetsDragLeave() {
  moveDragOverSetsId.value = null;
}

function onProjectSetsDrop(projectId) {
  const id = draggingEntityId.value;
  moveDragOverSetsId.value = null;
  if (draggingEntityKind.value === "set" && id != null) {
    moveSetToProject(id, projectId);
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
  // Reached from the empty library's "Choose a folder…", which is the first
  // thing pointing anyone at reference folders - they have always worked and
  // were a sidebar accessory nobody was sent to.
  //
  // The reference editor DIRECTLY, not `openAddFolderTypeDialog`. That chooser
  // offers "Import folder - watch for new files and import them
  // automatically", which copies files in; the button that gets here promises
  // "Nothing is moved" one screen earlier, so routing through the chooser would
  // have the release's headline claim falsified by the next click.
  openReferenceFolderEditor,
  offerLoosePictures,
  startLocalImport,
  currentProjectId,
  openCurrentSelectionEditor,
});
</script>

<template>
  <ImageImporter
    ref="imageImporterRef"
    @import-finished="handleImportFinished"
  />
  <CharacterEditor
    :open="characterEditorOpen"
    :character="characterEditorCharacter"
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
    :projects="projects"
    @close="closeSetEditor"
    @refresh-sidebar="refreshSidebar"
  />
  <ProjectEditor
    :open="projectEditorOpen"
    :project="projectEditorProject"
    @close="closeProjectEditor"
    @saved="projectSaved"
    @deleted="projectDeleted"
  />
  <UserSettingsDialog
    v-model:open="settingsDialogOpen"
    v-model:sidebar-thumbnail-size="sidebarThumbnailSizeModel"
    v-model:date-format="dateFormatModel"
    v-model:theme-mode="themeModeModel"
    :checkForUpdates="userPrefsStore.checkForUpdates"
    v-model:show-keyboard-hint="showKeyboardHintModel"
    :thumbnail-mode="gridStore.thumbnailMode"
    @update:thumbnail-mode="(value) => gridStore.setThumbnailMode(value)"
    :initial-tab="settingsDialogInitialTab"
    @update:hidden-tags="(value) => userPrefsStore.setHiddenTags(value)"
    @update:apply-tag-filter="
      (value) => userPrefsStore.setApplyTagFilter(value)
    "
    @update:comfyui-configured="
      (value) => (filterStore.comfyuiConfigured = value)
    "
    @update:public-url="(value) => (userPrefsStore.publicUrl = value)"
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
    @relocate="openReferenceFolderRelocateDialog"
  />
  <FolderMappingWizard
    :open="mappingStore.wizardOpen"
    :resume="mappingStore.wizardResume"
    @close="mappingStore.closeWizard()"
    @committed="folderMappingWizardCommitted"
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

  <v-dialog v-model="referenceFolderRelocateOpen" max-width="560">
    <v-card class="relocate-card">
      <v-card-title class="relocate-title"
        >Relocate Reference Folder</v-card-title
      >
      <v-card-text class="relocate-body">
        <div class="relocate-path-block">
          <div class="relocate-path-label">Current folder</div>
          <div
            class="relocate-path-value"
            :title="referenceFolderRelocateFolder?.folder"
          >
            {{ referenceFolderRelocateFolder?.folder }}
          </div>
        </div>
        <div class="relocate-path-block">
          <div class="relocate-path-label">Destination folder</div>
          <div class="relocate-destination-row">
            <v-text-field
              v-model="referenceFolderRelocateDestination"
              density="comfortable"
              variant="filled"
              hide-details
              readonly
              placeholder="Choose an empty folder"
            />
            <v-btn
              variant="outlined"
              size="small"
              icon
              title="Choose destination folder"
              @click="referenceFolderRelocateBrowseOpen = true"
            >
              <v-icon size="18">mdi-folder-open-outline</v-icon>
            </v-btn>
          </div>
        </div>
        <div class="relocate-warning">
          This moves every file and subfolder from the current reference folder
          into the destination folder, then updates PixlStash to use the new
          location. The destination must be empty.
        </div>
        <div v-if="referenceFolderRelocateError" class="relocate-error">
          {{ referenceFolderRelocateError }}
        </div>
        <div v-if="referenceFolderRelocateResult" class="relocate-result">
          {{ referenceFolderRelocateResult }}
        </div>
      </v-card-text>
      <v-card-actions class="relocate-actions">
        <v-spacer />
        <v-btn variant="text" @click="closeReferenceFolderRelocateDialog">
          {{ referenceFolderRelocateResult ? "Close" : "Cancel" }}
        </v-btn>
        <v-btn
          v-if="!referenceFolderRelocateResult"
          color="primary"
          variant="flat"
          :loading="referenceFolderRelocateLoading"
          :disabled="!referenceFolderRelocateDestination"
          @click="relocateReferenceFolder"
        >
          Move Files and Relocate
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <FolderBrowser
    :open="referenceFolderRelocateBrowseOpen"
    :initial-path="referenceFolderRelocateInitialPath"
    :registered-paths="relocationRegisteredPaths"
    :image-root="referenceFoldersImageRoot"
    already-registered-label="Already a reference folder"
    allow-create-folder
    @select="(path) => (referenceFolderRelocateDestination = path)"
    @close="referenceFolderRelocateBrowseOpen = false"
  />

  <aside
    ref="sidebarRootRef"
    class="sidebar"
    :class="{
      'sidebar-docked': sidebarStore.effectiveDocked,
      'sidebar--narrow': sidebarIsNarrow,
    }"
    :style="sidebarThumbStyle"
  >
    <!-- Drag the right edge to resize the expanded sidebar (hidden when docked). -->
    <div
      v-if="!sidebarStore.effectiveDocked"
      class="sidebar-resize-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      tabindex="0"
      title="Drag to resize sidebar"
      @pointerdown="onSidebarResizeStart"
      @keydown="onSidebarResizeKey"
    ></div>
    <!-- On the desktop shell the brand (logo + name + update alert) lives in
         the title bar, so this row collapses to just the dock toggle. -->
    <div
      v-if="!isDesktop"
      class="sidebar-brand"
      @contextmenu.prevent="openSidebarCtxMenu('empty', null, $event)"
    >
      <div class="sidebar-brand-left">
        <!-- The logo is a real outbound link - let its native right-click menu
             (copy/open link) through and don't open the sidebar's view menu. -->
        <a
          href="https://pikselkroken.github.io/pixlstash/"
          target="_blank"
          rel="noopener noreferrer"
          class="sidebar-brand-logo-link"
          @contextmenu.stop
        >
          <img
            src="/Logo.png"
            alt="PixlStash logo"
            class="sidebar-brand-logo"
          />
        </a>
        <div v-if="!sidebarStore.effectiveDocked" class="sidebar-brand-text">
          <div class="sidebar-brand-title-row">
            <WordmarkLogo class="sidebar-brand-title" />
            <button
              v-if="userPrefsStore.telemetryActive"
              type="button"
              class="sidebar-telemetry-dot"
              :title="telemetryIndicatorTitle"
              :aria-label="`${telemetryIndicatorTitle}. Open privacy settings.`"
              @click="openSettingsDialog('privacy')"
            ></button>
          </div>
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
              @contextmenu.stop
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
    </div>
    <!-- The docked counterpart of the Global / Projects / Folders tab strip
         below, and gated on `scopedResourceType` for the same reason it is:
         a share token scoped to a character, a picture or a set can read no
         project list at all, so its Projects flyout is an empty box and its
         Folders flyout is owner-only. Without this the two sidebar widths
         disagreed: the expanded one omitted the switcher, the docked one
         still offered it. -->
    <div
      v-if="sidebarStore.effectiveDocked && !scopedResourceType"
      class="sidebar-collapsed-project-wrap"
      ref="projectMenuRef"
      @contextmenu.prevent="openSidebarCtxMenu('empty', null, $event)"
    >
      <div
        class="sidebar-collapsed-row sidebar-collapsed-row--has-flyout sidebar-collapsed-row--project"
      >
        <button
          type="button"
          class="sidebar-collapsed-item sidebar-collapsed-item--has-flyout"
          style="margin: 0 auto"
          :title="collapsedProjectBtnTitle"
          ref="collapsedProjectBtnRef"
          aria-haspopup="menu"
          :aria-expanded="projectMenuOpen"
          aria-controls="sidebar-project-menu"
          @click.stop="toggleProjectMenu"
          @keydown="onCollapsedProjectTriggerKeydown"
        >
          <v-icon size="20">{{
            sidebarPrimaryTab === "folders"
              ? "mdi-folder-outline"
              : projectViewMode === "global"
                ? "mdi-earth"
                : "mdi-briefcase-outline"
          }}</v-icon>
        </button>
      </div>
      <Teleport to="body">
        <div
          v-if="projectMenuOpen && sidebarStore.effectiveDocked"
          id="sidebar-project-menu"
          ref="collapsedProjectMenuRef"
          class="sidebar-collapsed-project-menu"
          role="menu"
          aria-label="Library navigation"
          :style="{
            top: collapsedProjectMenuPos.top + 'px',
            left: collapsedProjectMenuPos.left + 'px',
          }"
          @mouseleave="scheduleCloseProjectSubMenu"
          @keydown="onProjectMenuKeydown"
        >
          <!-- Global -->
          <button
            type="button"
            role="menuitem"
            tabindex="-1"
            class="sidebar-project-menu-item"
            :class="{
              active:
                projectViewMode === 'global' && sidebarPrimaryTab !== 'folders',
            }"
            :aria-current="
              projectViewMode === 'global' && sidebarPrimaryTab !== 'folders'
                ? 'page'
                : undefined
            "
            @mouseenter="scheduleCloseProjectSubMenu"
            @click="selectGlobalFromProjectMenu"
          >
            <v-icon size="14">mdi-earth</v-icon>
            <span class="sidebar-project-menu-item-label">Global</span>
          </button>

          <div class="sidebar-project-menu-separator" role="separator"></div>

          <!-- Projects row → flyout submenu on hover or click -->
          <button
            ref="collapsedProjectsMenuTriggerRef"
            type="button"
            role="menuitem"
            tabindex="-1"
            class="sidebar-project-menu-item sidebar-project-menu-has-sub"
            :class="{
              active:
                projectViewMode === 'project' &&
                sidebarPrimaryTab !== 'folders',
              'sub-open': projectMenuSection === 'projects',
            }"
            data-project-submenu="projects"
            aria-haspopup="menu"
            :aria-expanded="projectMenuSection === 'projects'"
            aria-controls="sidebar-project-submenu"
            @mouseenter="openProjectSubMenu('projects', $event)"
            @click.stop="
              openProjectSubMenu('projects', $event, $event.detail === 0)
            "
          >
            <v-icon size="14">mdi-briefcase-outline</v-icon>
            <span class="sidebar-project-menu-item-label">Projects</span>
            <v-icon size="12" class="sidebar-project-menu-chevron"
              >mdi-chevron-right</v-icon
            >
          </button>

          <!-- Folders row → flyout submenu on hover or click -->
          <button
            ref="collapsedFoldersMenuTriggerRef"
            type="button"
            role="menuitem"
            tabindex="-1"
            class="sidebar-project-menu-item sidebar-project-menu-has-sub"
            :class="{
              active: sidebarPrimaryTab === 'folders',
              'sub-open': projectMenuSection === 'folders',
            }"
            data-project-submenu="folders"
            aria-haspopup="menu"
            :aria-expanded="projectMenuSection === 'folders'"
            aria-controls="sidebar-project-submenu"
            @mouseenter="openProjectSubMenu('folders', $event)"
            @click.stop="
              openProjectSubMenu('folders', $event, $event.detail === 0)
            "
          >
            <v-icon size="14">mdi-folder-outline</v-icon>
            <span class="sidebar-project-menu-item-label">Folders</span>
            <v-icon size="12" class="sidebar-project-menu-chevron"
              >mdi-chevron-right</v-icon
            >
          </button>
        </div>
      </Teleport>

      <!-- Flyout submenu -->
      <Teleport to="body">
        <div
          v-if="
            projectMenuSection &&
            projectMenuOpen &&
            sidebarStore.effectiveDocked
          "
          id="sidebar-project-submenu"
          ref="collapsedProjectSubMenuRef"
          class="sidebar-collapsed-project-submenu"
          role="menu"
          :aria-label="
            projectMenuSection === 'projects' ? 'Projects' : 'Library folders'
          "
          :style="{
            top: projectMenuSubPos.top + 'px',
            left: projectMenuSubPos.left + 'px',
          }"
          @mouseenter="cancelCloseProjectSubMenu"
          @mouseleave="scheduleCloseProjectSubMenu"
          @keydown="onProjectSubMenuKeydown"
        >
          <!-- Projects submenu -->
          <template v-if="projectMenuSection === 'projects'">
            <button
              v-if="!isReadOnly"
              type="button"
              role="menuitem"
              tabindex="-1"
              class="sidebar-project-menu-item sidebar-project-menu-add"
              @click="createProject"
            >
              <v-icon size="14">mdi-plus</v-icon>
              <span class="sidebar-project-menu-item-label"
                >Add new project</span
              >
            </button>
            <button
              v-for="p in sortedProjects"
              :key="p.id"
              type="button"
              role="menuitem"
              tabindex="-1"
              class="sidebar-project-menu-item"
              :class="{
                active:
                  projectStore.projectViewMode === 'project' &&
                  projectStore.selectedProjectId === p.id,
              }"
              :aria-current="
                projectStore.projectViewMode === 'project' &&
                projectStore.selectedProjectId === p.id
                  ? 'page'
                  : undefined
              "
              @click="selectProjectFromProjectMenu(p)"
            >
              <v-icon size="14">mdi-folder</v-icon>
              <span class="sidebar-project-menu-item-label">{{ p.name }}</span>
            </button>
          </template>

          <!-- Folders submenu -->
          <template v-if="projectMenuSection === 'folders'">
            <button
              v-if="!isReadOnly"
              type="button"
              role="menuitem"
              tabindex="-1"
              class="sidebar-project-menu-item sidebar-project-menu-add"
              @click="openAddFolderFromProjectMenu"
            >
              <v-icon size="14">mdi-plus</v-icon>
              <span class="sidebar-project-menu-item-label">Add folder</span>
            </button>
            <div
              v-if="referenceFolders.length"
              class="sidebar-project-menu-section-label"
              role="presentation"
            >
              Reference Folders
            </div>
            <button
              v-for="rf in referenceFolders"
              :key="'rf-' + rf.id"
              type="button"
              role="menuitem"
              tabindex="-1"
              class="sidebar-project-menu-item"
              :class="{
                active:
                  sidebarPrimaryTab === 'folders' &&
                  selectedFolderKey === 'rf-' + rf.id &&
                  !isDuplicatesView,
              }"
              :aria-current="
                sidebarPrimaryTab === 'folders' &&
                selectedFolderKey === 'rf-' + rf.id &&
                !isDuplicatesView
                  ? 'page'
                  : undefined
              "
              @click="selectFolderFromProjectMenu(rf, 'reference')"
            >
              <v-icon size="14">mdi-folder-network-outline</v-icon>
              <span class="sidebar-project-menu-item-label">{{
                rf.label || rf.folder
              }}</span>
            </button>
            <div
              v-if="importFolders.length"
              class="sidebar-project-menu-section-label"
              role="presentation"
            >
              Import Folders
            </div>
            <button
              v-for="imf in importFolders"
              :key="'if-' + imf.id"
              type="button"
              role="menuitem"
              tabindex="-1"
              class="sidebar-project-menu-item"
              :class="{
                active:
                  sidebarPrimaryTab === 'folders' &&
                  selectedFolderKey === 'if-' + imf.id &&
                  !isDuplicatesView,
              }"
              :aria-current="
                sidebarPrimaryTab === 'folders' &&
                selectedFolderKey === 'if-' + imf.id &&
                !isDuplicatesView
                  ? 'page'
                  : undefined
              "
              @click="selectFolderFromProjectMenu(imf, 'import')"
            >
              <v-icon size="14">mdi-folder-open-outline</v-icon>
              <span class="sidebar-project-menu-item-label">{{
                imf.label || imf.folder
              }}</span>
            </button>
          </template>
        </div>
      </Teleport>
    </div>
    <div
      v-else-if="!scopedResourceType"
      class="sidebar-view-header"
      @contextmenu.prevent="openSidebarCtxMenu('empty', null, $event)"
    >
      <div class="sidebar-view-tabs-row">
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
            <span class="sidebar-view-tab-label">Global</span>
          </button>
          <button
            class="sidebar-view-tab"
            :class="{
              active:
                sidebarPrimaryTab === 'library' &&
                projectViewMode === 'project',
            }"
            @click="selectLibraryTab('project')"
          >
            <v-icon size="14">mdi-briefcase-outline</v-icon>
            <span class="sidebar-view-tab-label">Projects</span>
          </button>
          <button
            v-if="!isReadOnly"
            class="sidebar-view-tab"
            :class="{ active: sidebarPrimaryTab === 'folders' }"
            @click="selectFoldersTab()"
          >
            <v-icon size="14">mdi-folder-outline</v-icon>
            <span class="sidebar-view-tab-label">Folders</span>
          </button>
        </div>
      </div>
    </div>
    <div
      class="sidebar-scroll"
      ref="dockedScrollRef"
      @contextmenu.self.prevent="openSidebarCtxMenu('empty', null, $event)"
    >
      <template v-if="sidebarStore.effectiveDocked">
        <!-- Catch-all: any right-click that reaches the list (blank gaps, the
             margins beside the centered rows, the spacer) opens the view menu.
             Item rows below use `.stop` on their own context menus so they never
             fall through to this. -->
        <div
          class="sidebar-collapsed-list"
          @contextmenu.prevent="openSidebarCtxMenu('empty', null, $event)"
        >
          <div
            v-if="sidebarPrimaryTab !== 'folders'"
            :class="[
              'sidebar-collapsed-row',
              { active: isAllPicturesRowActive },
            ]"
          >
            <button
              type="button"
              :class="[
                'sidebar-collapsed-item',
                { active: isAllPicturesRowActive },
              ]"
              :aria-current="isAllPicturesRowActive ? 'page' : undefined"
              aria-label="All Pictures"
              title="All Pictures"
              @click="selectCharacter(ALL_PICTURES_ID, 'All Pictures')"
              @contextmenu.prevent.stop="
                openSidebarCtxMenu('all-pictures', null, $event)
              "
            >
              <v-icon>mdi-image-multiple</v-icon>
            </button>
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
                    selectionStore.selectedCharacter === char.id &&
                    selectionOwnsHighlight,
                },
              ]"
            >
              <div
                :class="[
                  'sidebar-collapsed-item',
                  {
                    active:
                      selectionStore.selectedCharacter === char.id &&
                      selectionOwnsHighlight,
                  },
                ]"
                role="button"
                tabindex="0"
                :aria-pressed="
                  selectionStore.selectedCharacter === char.id &&
                  selectionOwnsHighlight
                    ? 'true'
                    : 'false'
                "
                :title="`${char.name || 'Character'} (Ctrl/Cmd + click to multi-select)`"
                @click="
                  selectCharacter(char.id, char.name || 'Character', $event)
                "
                @keydown="activateOnEnterOrSpace"
                @contextmenu.prevent.stop="
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
                role="button"
                tabindex="0"
                @click="createCharacter()"
                @keydown="activateOnEnterOrSpace"
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
              role="button"
              tabindex="0"
              @click="createCharacter()"
              @keydown="activateOnEnterOrSpace"
              @contextmenu.prevent.stop="
                openSidebarCtxMenu('header', 'people', $event)
              "
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
                  selectedCharacterIdSet.size > 0 && selectionOwnsHighlight,
              },
            ]"
          >
            <div
              :class="[
                'sidebar-collapsed-item',
                'sidebar-collapsed-item--has-flyout',
                {
                  active:
                    selectedCharacterIdSet.size > 0 && selectionOwnsHighlight,
                },
              ]"
              :title="
                selectedCharacterObj ? selectedCharacterObj.name : 'People'
              "
              ref="collapsedCharBtnRef"
              role="button"
              tabindex="0"
              aria-haspopup="menu"
              :aria-expanded="collapsedCharMenuOpen"
              @click.stop="toggleCollapsedCharMenu"
              @keydown="activateOnEnterOrSpace"
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
              <div
                class="sidebar-collapsed-flyout-header"
                @contextmenu.prevent.stop="
                  openSidebarCtxMenu('header', 'people', $event)
                "
              >
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
                        selectionStore.selectedCharacter === char.id &&
                        selectionOwnsHighlight,
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
            v-if="
              (visibleSets.length || !isReadOnly) &&
              sidebarPrimaryTab !== 'folders'
            "
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
                    selectedSetIdSet.has(pset.id) && selectionOwnsHighlight,
                },
              ]"
            >
              <div
                :class="[
                  'sidebar-collapsed-item',
                  {
                    active:
                      selectedSetIdSet.has(pset.id) && selectionOwnsHighlight,
                  },
                ]"
                :title="pset.name || 'Picture Set'"
                role="button"
                tabindex="0"
                :aria-pressed="
                  selectedSetIdSet.has(pset.id) && selectionOwnsHighlight
                    ? 'true'
                    : 'false'
                "
                @click="selectSet(pset.id, pset.name || 'Picture Set', $event)"
                @keydown="activateOnEnterOrSpace"
                @contextmenu.prevent.stop="
                  openSidebarCtxMenu('set', pset, $event)
                "
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
                <v-icon
                  v-if="pset.locked"
                  class="sidebar-collapsed-lock"
                  size="10"
                  :title="SET_LOCKED_ROW_TITLE"
                  >mdi-lock-outline</v-icon
                >
              </div>
            </div>
            <div v-if="!isReadOnly" class="sidebar-collapsed-row">
              <div
                class="sidebar-collapsed-item sidebar-collapsed-item--add sidebar-collapsed-item--add-set"
                title="Add picture set"
                role="button"
                tabindex="0"
                @click="createSet()"
                @keydown="activateOnEnterOrSpace"
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
            v-else-if="
              !visibleSets.length &&
              !isReadOnly &&
              sidebarPrimaryTab !== 'folders'
            "
            class="sidebar-collapsed-row"
          >
            <div
              class="sidebar-collapsed-item sidebar-collapsed-item--add sidebar-collapsed-item--add-set"
              title="Add picture set"
              role="button"
              tabindex="0"
              @click="createSet()"
              @keydown="activateOnEnterOrSpace"
              @contextmenu.prevent.stop="
                openSidebarCtxMenu('header', 'sets', $event)
              "
            >
              <i
                class="mdi mdi-image-album sidebar-collapsed-item--add-bg-icon"
                aria-hidden="true"
              ></i>
              <v-icon class="sidebar-collapsed-item--add-plus">mdi-plus</v-icon>
            </div>
          </div>
          <div
            v-else-if="visibleSets.length && sidebarPrimaryTab !== 'folders'"
            :class="[
              'sidebar-collapsed-row',
              'sidebar-collapsed-row--has-flyout',
              { active: selectedSetIdSet.size > 0 && selectionOwnsHighlight },
            ]"
          >
            <div
              :class="[
                'sidebar-collapsed-item',
                'sidebar-collapsed-item--has-flyout',
                {
                  active: selectedSetIdSet.size > 0 && selectionOwnsHighlight,
                },
              ]"
              :title="selectedSetObj ? selectedSetObj.name : 'Picture Sets'"
              ref="collapsedSetBtnRef"
              role="button"
              tabindex="0"
              aria-haspopup="menu"
              :aria-expanded="collapsedSetMenuOpen"
              @click.stop="toggleCollapsedSetMenu"
              @keydown="activateOnEnterOrSpace"
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
              <v-icon
                v-if="selectedSetObj && selectedSetObj.locked"
                class="sidebar-collapsed-lock"
                size="10"
                :title="SET_LOCKED_ROW_TITLE"
                >mdi-lock-outline</v-icon
              >
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
              <div
                class="sidebar-collapsed-flyout-header"
                @contextmenu.prevent.stop="
                  openSidebarCtxMenu('header', 'sets', $event)
                "
              >
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
                        selectedSetIdSet.has(pset.id) && selectionOwnsHighlight,
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
                  <v-icon
                    v-if="pset.locked"
                    class="sidebar-lock-icon"
                    size="12"
                    :title="SET_LOCKED_ROW_TITLE"
                    >mdi-lock-outline</v-icon
                  >
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

          <!-- Duplicates keeps its dock row so the count stays reachable when
               the sidebar is narrow; the badge is the only thing here that
               reports pending work. -->
          <div :class="['sidebar-collapsed-row', { active: isDuplicatesView }]">
            <button
              type="button"
              :class="[
                'sidebar-collapsed-item',
                {
                  active: isDuplicatesView,
                  'sidebar-collapsed-item--unavailable': isReadOnly,
                },
              ]"
              :aria-current="isDuplicatesView ? 'page' : undefined"
              :aria-disabled="isReadOnly || undefined"
              aria-label="Duplicates"
              :title="isReadOnly ? READ_ONLY_DEDUP_HINT : 'Duplicates'"
              @click="isReadOnly || emit('select-duplicates', {})"
            >
              <v-icon>mdi-content-duplicate</v-icon>
              <span
                v-if="dedupStore.hasDuplicates"
                class="sidebar-collapsed-dedup-badge"
                title="There are duplicates to review"
              ></span>
            </button>
          </div>

          <!-- The shelf's dock mirror. Same <button> reasoning as the
               expanded row, and the same read-only treatment as Duplicates
               above it. -->
          <div :class="['sidebar-collapsed-row', { active: isModelsView }]">
            <button
              type="button"
              class="sidebar-collapsed-item sidebar-destination-btn"
              :class="{
                active: isModelsView,
                'sidebar-collapsed-item--unavailable': isReadOnly,
              }"
              :aria-current="isModelsView ? 'page' : undefined"
              :aria-disabled="isReadOnly || undefined"
              :title="isReadOnly ? READ_ONLY_SHELF_HINT : 'Models'"
              @click="isReadOnly || emit('select-models')"
            >
              <v-icon>mdi-layers-outline</v-icon>
            </button>
          </div>

          <!-- Moves in the dock: reachable whenever the queue holds anything
               at all (movesStore.hasAnyPending), never shown-and-disabled the
               way the three permanent destinations above are - see the
               comment on isMovesView. The attention dot is narrower than the
               row: an off_layout-only queue has nothing to decide, so it
               earns the row (or its retention window would expire it unseen)
               but not the dot. -->
          <div
            v-if="movesStore.hasAnyPending"
            :class="['sidebar-collapsed-row', { active: isMovesView }]"
          >
            <button
              type="button"
              class="sidebar-collapsed-item sidebar-destination-btn"
              :class="{ active: isMovesView }"
              :aria-current="isMovesView ? 'page' : undefined"
              aria-label="Moves made outside PixlStash"
              :title="
                movesStore.hasPending
                  ? `${movesStore.pendingCount} move(s) to review`
                  : 'Moves already followed, nothing to decide'
              "
              @click="emit('select-moves')"
            >
              <v-icon>mdi-folder-move-outline</v-icon>
              <span
                v-if="movesStore.hasPending"
                class="sidebar-collapsed-dedup-badge"
                title="There are moves to review"
              ></span>
            </button>
          </div>

          <!-- Scrap Heap at bottom of dock. The flex spacer above it fills most
               of the dock's blank space; its right-clicks bubble to the list's
               catch-all handler, so it needs no handler of its own. -->
          <div v-if="!isReadOnly" class="sidebar-collapsed-spacer"></div>
          <div
            v-if="!isReadOnly"
            :class="[
              'sidebar-collapsed-row',
              {
                active:
                  selectionStore.selectedCharacter === SCRAPHEAP_PICTURES_ID &&
                  selectionOwnsHighlight,
              },
            ]"
          >
            <button
              type="button"
              :class="[
                'sidebar-collapsed-item',
                'sidebar-collapsed-item--scrapheap',
                { active: isScrapheapRowActive },
              ]"
              :aria-current="isScrapheapRowActive ? 'page' : undefined"
              aria-label="Scrapheap"
              title="Scrapheap"
              @click="selectCharacter(SCRAPHEAP_PICTURES_ID, 'Scrapheap')"
              @contextmenu.prevent.stop="
                openSidebarCtxMenu('scrapheap', null, $event)
              "
            >
              <v-icon>mdi-trash-can-outline</v-icon>
            </button>
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
              @click="toggleReferenceFoldersSection()"
              @contextmenu.prevent.stop="
                openSidebarCtxMenu('header', 'reference-folders', $event)
              "
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
              v-if="pendingForThisLibrary"
              class="sidebar-folder-row sidebar-mapping-resume-row"
              :title="
                pendingForThisLibrary.taskId
                  ? 'The scan is kept - reopening this does not re-scan'
                  : 'Reopening this starts scanning that folder'
              "
              @click="openFolderMappingWizard(pendingForThisLibrary)"
            >
              <v-icon size="15" class="sidebar-mapping-resume-icon"
                >mdi-map-marker-path</v-icon
              >
              <span class="sidebar-mapping-resume-label">
                Finish organising
                {{ pendingForThisLibrary.label || pendingForThisLibrary.path }}…
              </span>
            </div>
            <div
              v-for="rf in referenceFolders"
              v-show="!referenceFoldersCollapsed"
              :key="rf.id"
              class="sidebar-folder-root"
            >
              <div
                class="sidebar-folder-row sidebar-folder-root-row"
                :class="{
                  active: selectedFolderKey === 'rf-' + rf.id,
                  droppable:
                    dragOverReferenceTargetKey === 'rf-' + rf.id &&
                    !dropRejected,
                  'not-droppable':
                    dragOverReferenceTargetKey === 'rf-' + rf.id &&
                    dropRejected,
                }"
                :title="
                  inDocker
                    ? rf.folder
                    : `${rf.folder} - drop dragged reference images here to move them`
                "
                @contextmenu.prevent="openSidebarCtxMenu('folder', rf, $event)"
                @dragover="
                  !inDocker &&
                  handleReferenceFolderDragOver(rf.id, null, $event)
                "
                @dragleave="
                  !inDocker &&
                  handleReferenceFolderDragLeave(rf.id, null, $event)
                "
                @drop.prevent="
                  !inDocker && handleReferenceFolderDrop(rf.id, null, $event)
                "
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
                  size="12"
                  class="sidebar-row-glyph sidebar-folder-chevron"
                  :class="{
                    'sidebar-row-glyph--empty': !referenceFolderCanDisclose(rf),
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
                <v-icon size="16" class="sidebar-row-glyph sidebar-folder-icon"
                  >mdi-folder-network-outline</v-icon
                >
                <span class="sidebar-folder-label">{{
                  rf.label || rf.folder
                }}</span>
                <span
                  v-if="!inDocker"
                  class="sidebar-folder-actions"
                  @click.stop
                >
                  <button
                    type="button"
                    class="sidebar-folder-action-btn"
                    title="Relocate folder and move files"
                    @click="openReferenceFolderRelocateDialog(rf)"
                  >
                    <v-icon size="13">mdi-folder-move-outline</v-icon>
                  </button>
                  <button
                    type="button"
                    class="sidebar-folder-action-btn"
                    title="Edit folder settings"
                    @click="openReferenceFolderEditor(rf)"
                  >
                    <v-icon size="13">mdi-pencil-outline</v-icon>
                  </button>
                </span>
                <span
                  v-if="rf.status === 'mount_error'"
                  class="sidebar-folder-status-badge sidebar-folder-status--mount_error"
                  :title="
                    inDocker
                      ? 'Mount error - check Docker volume'
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
                      ? 'Pending restart - restart server to mount'
                      : 'Scan pending - will start automatically'
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
                      :drop-target-key="dragOverReferenceTargetKey"
                      :drop-rejected="dropRejected"
                      @select="handleFolderNodeSelect"
                      @toggle="handleFolderNodeToggle"
                      @drag-over="
                        ({ rfId, path, event }) =>
                          handleReferenceFolderDragOver(rfId, path, event)
                      "
                      @drag-leave="
                        ({ rfId, path, event }) =>
                          handleReferenceFolderDragLeave(rfId, path, event)
                      "
                      @drop="
                        ({ rfId, path, event }) =>
                          handleReferenceFolderDrop(rfId, path, event)
                      "
                      @context="handleReferenceFolderNodeContext"
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
              @click="toggleImportFoldersSection()"
              @contextmenu.prevent.stop="
                openSidebarCtxMenu('header', 'import-folders', $event)
              "
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
                  size="12"
                  class="sidebar-row-glyph sidebar-row-glyph--empty sidebar-folder-chevron"
                >
                  mdi-chevron-right
                </v-icon>
                <v-icon size="16" class="sidebar-row-glyph sidebar-folder-icon"
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
              <!-- A destination is a real <button>: keyboard reach and
                   Enter/Space activation come for free, and `aria-current`
                   is what tells a screen reader which one you are in. -->
              <button
                type="button"
                :class="[
                  'sidebar-list-item',
                  { active: isAllPicturesRowActive },
                ]"
                :aria-current="isAllPicturesRowActive ? 'page' : undefined"
                @click="selectCharacter(ALL_PICTURES_ID, allPicturesRowLabel)"
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
                  categoryCounts[ALL_PICTURES_ID] ?? ""
                }}</span>
              </button>
            </div>

            <!-- Duplicates earns a sidebar row because it is the one thing
                 here with a to-do count: the number goes down as the user
                 works, which is what a destination is for. Stacked and
                 unstacked stay a filter. -->
            <div class="sidebar-all-pictures-row">
              <!-- `aria-disabled`, not `disabled`: a read-only session must
                   still be able to focus the row and read the title that
                   explains why it is inert. The click guard stays. -->
              <button
                type="button"
                :class="[
                  'sidebar-list-item',
                  {
                    active: isDuplicatesView,
                    'sidebar-list-item--unavailable': isReadOnly,
                  },
                ]"
                :aria-current="isDuplicatesView ? 'page' : undefined"
                :aria-disabled="isReadOnly || undefined"
                :title="isReadOnly ? READ_ONLY_DEDUP_HINT : undefined"
                @click="isReadOnly || emit('select-duplicates', {})"
              >
                <span class="sidebar-list-icon sidebar-list-icon--toplevel"
                  ><v-icon size="18">mdi-content-duplicate</v-icon></span
                >
                <span class="sidebar-list-label">Duplicates</span>
                <span
                  v-if="dedupStore.isScanning"
                  class="sidebar-dedup-scanning"
                  title="Still looking for duplicates. Groups appear as they are found."
                >
                  <v-progress-circular indeterminate size="10" width="1.5" />
                </span>
                <!-- A presence DOT, not a count (owner call, 2026-07-29): the
                     group count moves with the tier gate and the threshold,
                     so it read as churn. The dot only says "there are
                     duplicates to review"; the queue's own header carries the
                     numbers. -->
                <span
                  v-if="dedupStore.hasDuplicates"
                  class="sidebar-dedup-dot"
                  title="There are duplicates to review"
                ></span>
              </button>
            </div>

            <!-- The shelf is a destination, not a filter: it lists the LoRAs
                 and checkpoints on this machine, which no picture view can
                 express. A <button>, unlike the rows above it, because a new
                 destination must not be born unreachable by keyboard; the
                 other three are filed to follow.

                 Inert rather than hidden for a READ session, exactly like
                 Duplicates above and for the same reason: the demo site is a
                 READ session, and hiding a feature there advertises a smaller
                 product than PixlStash is (`e2e/specs/read-only-features.spec.js`).
                 Every /models route is owner-only, so the row must be quiet as
                 well as inert - it can carry no count and start no fetch
                 (issue #1014). -->
            <div class="sidebar-all-pictures-row">
              <button
                type="button"
                class="sidebar-list-item sidebar-destination-btn"
                :class="{
                  active: isModelsView,
                  'sidebar-list-item--unavailable': isReadOnly,
                }"
                :aria-current="isModelsView ? 'page' : undefined"
                :aria-disabled="isReadOnly || undefined"
                :title="isReadOnly ? READ_ONLY_SHELF_HINT : undefined"
                @click="isReadOnly || emit('select-models')"
              >
                <span class="sidebar-list-icon sidebar-list-icon--toplevel"
                  ><v-icon size="18">mdi-layers-outline</v-icon></span
                >
                <span class="sidebar-list-label">Models</span>
              </button>
            </div>

            <!-- Moves: a to-do queue, not a permanent destination, so it earns
                 its row while the queue holds anything at all - including an
                 off_layout-only backlog, which has nothing to decide but
                 still has to be reachable before its retention window expires
                 it unseen (see hasAnyPending's own comment). The count only
                 ever names what needs a DECISION; an off_layout-only queue
                 shows the row with no number, not a "0" that reads as empty. -->
            <div
              v-if="movesStore.hasAnyPending"
              class="sidebar-all-pictures-row"
            >
              <button
                type="button"
                :class="['sidebar-list-item', { active: isMovesView }]"
                :aria-current="isMovesView ? 'page' : undefined"
                @click="emit('select-moves')"
              >
                <span class="sidebar-list-icon sidebar-list-icon--toplevel"
                  ><v-icon size="18">mdi-folder-move-outline</v-icon></span
                >
                <span class="sidebar-list-label">Moves</span>
                <span v-if="movesStore.hasPending" class="sidebar-list-count">{{
                  movesStore.pendingCount
                }}</span>
              </button>
            </div>

            <div v-if="!isReadOnly" class="sidebar-all-pictures-row">
              <button
                type="button"
                :class="['sidebar-list-item', { active: isScrapheapRowActive }]"
                :aria-current="isScrapheapRowActive ? 'page' : undefined"
                @click="selectCharacter(SCRAPHEAP_PICTURES_ID, 'Scrapheap')"
                @contextmenu.prevent="
                  openSidebarCtxMenu('scrapheap', null, $event)
                "
              >
                <span class="sidebar-list-icon sidebar-list-icon--toplevel"
                  ><v-icon size="18">mdi-trash-can-outline</v-icon></span
                >
                <span class="sidebar-list-label">Scrapheap</span>
                <!-- `?? ""`, not `|| ""`: an empty Scrapheap has a count of 0,
                     and 0 is an answer, so it renders. Only a count that has
                     not arrived yet is blank. This is the same distinction
                     `utils/sidebarCounts.js` is a module to protect, and every
                     sibling badge here already reads this way. -->
                <span class="sidebar-list-count">{{
                  categoryCounts[SCRAPHEAP_PICTURES_ID] ?? ""
                }}</span>
              </button>
            </div>

            <div class="sidebar-section-divider" />

            <div
              v-if="scopedResourceType !== 'picture_set'"
              class="sidebar-section-block"
            >
              <div
                class="sidebar-section-header sidebar-section-header--collapsible"
                @contextmenu.prevent.stop="
                  openSidebarCtxMenu('header', 'people', $event)
                "
              >
                <button
                  type="button"
                  class="sidebar-section-toggle"
                  :aria-expanded="!peopleSectionCollapsed"
                  @click.stop="togglePeopleSection()"
                >
                  <v-icon class="sidebar-section-chevron" size="16">{{
                    peopleSectionCollapsed
                      ? "mdi-chevron-right"
                      : "mdi-chevron-down"
                  }}</v-icon>
                  <span>People</span>
                </button>
                <span class="sidebar-header-spacer"></span>
                <div class="sidebar-header-actions" @click.stop>
                  <button
                    v-if="selectedCharacterIdSet.size > 1"
                    type="button"
                    class="clear-selection-inline"
                    aria-label="Clear character selection"
                    @click.stop="
                      selectCharacter(ALL_PICTURES_ID, 'All Pictures')
                    "
                    title="Clear character selection"
                  >
                    <v-icon size="16">mdi-selection-off</v-icon>
                  </button>
                  <button
                    v-if="
                      selectedCharacterObj &&
                      hasSingleSelectedCharacter &&
                      !isReadOnly
                    "
                    type="button"
                    class="edit-character-inline"
                    aria-label="Edit selected character"
                    @click.stop="openCharacterEditor(selectedCharacterObj)"
                    title="Edit selected character"
                  >
                    <v-icon size="16">mdi-pencil</v-icon>
                  </button>
                  <button
                    v-if="
                      !isReadOnly &&
                      selectionStore.selectedCharacter &&
                      selectionStore.selectedCharacter !== ALL_PICTURES_ID &&
                      selectionStore.selectedCharacter !==
                        UNASSIGNED_PICTURES_ID &&
                      selectionStore.selectedCharacter !== SCRAPHEAP_PICTURES_ID
                    "
                    type="button"
                    class="delete-character-inline"
                    aria-label="Delete selected character"
                    @click.stop="deleteCharacter"
                    title="Delete selected character"
                  >
                    <v-icon size="16">mdi-trash-can-outline</v-icon>
                  </button>
                  <button
                    v-if="!isReadOnly"
                    type="button"
                    class="add-character-inline"
                    aria-label="Add character"
                    @click.stop="createCharacter"
                    title="Add character"
                  >
                    <v-icon size="16">mdi-plus</v-icon>
                  </button>
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
                            : selectionStore.selectedCharacter === char.id) &&
                          selectionOwnsHighlight,
                        droppable:
                          dragOverCharacter === char.id && !dropRejected,
                        'not-droppable':
                          dragOverCharacter === char.id && dropRejected,
                      },
                    ]"
                    :ref="(el) => registerCharacterRef(char.id, el)"
                    role="button"
                    tabindex="0"
                    :aria-pressed="
                      (selectedCharacterIdSet.size > 0
                        ? selectedCharacterIdSet.has(char.id)
                        : selectionStore.selectedCharacter === char.id) &&
                      selectionOwnsHighlight
                        ? 'true'
                        : 'false'
                    "
                    :title="`${char.name || 'Character'} (Ctrl/Cmd + click to multi-select)`"
                    @click="
                      selectCharacter(char.id, char.name || 'Character', $event)
                    "
                    @keydown="activateOnEnterOrSpace"
                    @contextmenu.prevent="
                      openSidebarCtxMenu('character', char, $event)
                    "
                    @dragover="handleDragOverCharacter(char.id, $event)"
                    @dragleave="handleDragLeaveCharacter($event)"
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
                @contextmenu.prevent.stop="
                  openSidebarCtxMenu('header', 'sets', $event)
                "
              >
                <button
                  type="button"
                  class="sidebar-section-toggle"
                  :aria-expanded="!setsSectionCollapsed"
                  @click.stop="toggleSetsSection()"
                >
                  <v-icon class="sidebar-section-chevron" size="16">{{
                    setsSectionCollapsed
                      ? "mdi-chevron-right"
                      : "mdi-chevron-down"
                  }}</v-icon>
                  <span>Sets</span>
                </button>
                <span class="sidebar-header-spacer"></span>
                <div class="sidebar-header-actions">
                  <button
                    v-if="selectedSetIdSet.size > 1"
                    type="button"
                    class="clear-selection-inline"
                    aria-label="Clear set selection"
                    @click.stop="emit('select-set', null)"
                    title="Clear set selection"
                  >
                    <v-icon size="16">mdi-selection-off</v-icon>
                  </button>
                  <button
                    v-if="selectedSetObj && hasSingleSelectedSet && !isReadOnly"
                    type="button"
                    class="edit-set-inline"
                    aria-label="Edit selected set"
                    @click.stop="openSetEditor(selectedSetObj)"
                    title="Edit selected set"
                  >
                    <v-icon size="16">mdi-pencil</v-icon>
                  </button>
                  <button
                    v-if="!isReadOnly && selectedSetIdSet.size > 0"
                    type="button"
                    class="delete-character-inline"
                    aria-label="Delete selected sets"
                    @click.stop="handleDeleteSet"
                    :title="
                      selectedSetIdSet.size > 1
                        ? `Delete ${selectedSetIdSet.size} selected sets`
                        : 'Delete selected set'
                    "
                  >
                    <v-icon size="16">mdi-trash-can-outline</v-icon>
                  </button>
                  <button
                    v-if="!isReadOnly"
                    type="button"
                    class="add-character-inline"
                    aria-label="Create new set"
                    @click.stop="createSet"
                    title="Create new set"
                  >
                    <v-icon size="16">mdi-plus</v-icon>
                  </button>
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
                <template v-for="pset in visibleSets" :key="pset.id">
                  <div
                    :class="[
                      'sidebar-list-item',
                      'sidebar-set-item',
                      {
                        active:
                          selectedSetIdSet.has(pset.id) &&
                          selectionOwnsHighlight,
                        droppable: dragOverSet === pset.id && !dropRejected,
                        'not-droppable':
                          dragOverSet === pset.id && dropRejected,
                      },
                    ]"
                    :ref="(el) => registerSetRef(pset.id, el)"
                    role="button"
                    tabindex="0"
                    :aria-pressed="
                      selectedSetIdSet.has(pset.id) && selectionOwnsHighlight
                        ? 'true'
                        : 'false'
                    "
                    :title="`${pset.name || 'Picture Set'} (Ctrl/Cmd + click to multi-select)`"
                    @click="
                      selectSet(pset.id, pset.name || 'Picture Set', $event)
                    "
                    @keydown="activateOnEnterOrSpace"
                    @contextmenu.prevent="
                      openSidebarCtxMenu('set', pset, $event)
                    "
                    @dragover="dragOverSetItem(pset.id, $event)"
                    @dragleave="dragLeaveSetItem($event)"
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
                      v-if="pset.locked"
                      class="sidebar-lock-icon"
                      size="11"
                      :title="SET_LOCKED_ROW_TITLE"
                      >mdi-lock-outline</v-icon
                    >
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

          <!-- ══ PROJECTS tab content - flat tree ══ -->
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
                        projectStore.projectViewMode === 'project' &&
                        projectStore.selectedProjectId === p.id &&
                        selectionStore.selectedCharacter === ALL_PICTURES_ID &&
                        selectedSetIdSet.size === 0 &&
                        selectionOwnsHighlight,
                      droppable: dragOverProjectId === p.id && !dropRejected,
                      'not-droppable':
                        dragOverProjectId === p.id && dropRejected,
                      'project-move-target': moveDragOverProjectId === p.id,
                    },
                  ]"
                  :title="`${p.name} (drop pictures here to add them to this project)`"
                  @click="selectProjectNode(p)"
                  @contextmenu.prevent="
                    openSidebarCtxMenu('project', p, $event)
                  "
                  @dragover="
                    handleDragOverProject(p.id, $event);
                    onProjectHeaderDragOver(p.id, $event);
                  "
                  @dragleave="
                    handleDragLeaveProject($event);
                    onProjectHeaderDragLeave();
                  "
                  @drop.prevent="
                    onProjectHeaderDrop(p.id);
                    onProjectDrop(p.id, $event);
                  "
                >
                  <v-icon
                    class="sidebar-row-glyph sidebar-project-tree-chevron"
                    size="14"
                    @click.stop="toggleProjectExpanded(p.id)"
                    >{{
                      expandedProjectIds.has(p.id)
                        ? "mdi-chevron-down"
                        : "mdi-chevron-right"
                    }}</v-icon
                  >
                  <span class="sidebar-project-tree-name-group">
                    <span class="sidebar-project-tree-label">{{ p.name }}</span>
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
                  <div
                    :class="[
                      'sidebar-project-tree-subsection',
                      { 'project-move-target': moveDragOverPeopleId === p.id },
                    ]"
                    @dragover="onProjectPeopleDragOver(p.id, $event)"
                    @dragleave="onProjectPeopleDragLeave"
                    @drop.prevent="onProjectPeopleDrop(p.id)"
                  >
                    <div
                      class="sidebar-project-tree-subheader"
                      :class="{
                        'sidebar-project-tree-subheader--empty':
                          !projectHasPeople(p.id),
                      }"
                      :aria-disabled="!projectHasPeople(p.id) || undefined"
                      @click.stop="
                        projectHasPeople(p.id) && toggleProjectTreePeople(p.id)
                      "
                    >
                      <v-icon
                        size="14"
                        class="sidebar-row-glyph sidebar-project-tree-sub-chevron"
                        :class="{
                          'sidebar-row-glyph--empty': !projectHasPeople(p.id),
                        }"
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
                              :style="characterMenuPos"
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
                                      entityBelongsToProject(
                                        char,
                                        selectedProjectId,
                                      ),
                                  }"
                                  @click.stop="
                                    toggleCharacterProjectMembership(char.id)
                                  "
                                >
                                  <v-icon
                                    size="16"
                                    class="sidebar-move-menu-check"
                                    >{{
                                      entityBelongsToProject(
                                        char,
                                        selectedProjectId,
                                      )
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
                        v-for="char in sortedCharacters.filter((c) =>
                          entityBelongsToProject(c, p.id),
                        )"
                        :key="char.id"
                        :class="[
                          'sidebar-list-item',
                          'sidebar-project-tree-child',
                          {
                            active:
                              (selectedCharacterIdSet.size > 0
                                ? selectedCharacterIdSet.has(char.id)
                                : selectionStore.selectedCharacter ===
                                  char.id) && selectionOwnsHighlight,
                            droppable:
                              dragOverCharacter === char.id && !dropRejected,
                            'not-droppable':
                              dragOverCharacter === char.id && dropRejected,
                          },
                        ]"
                        :draggable="!isReadOnly"
                        @dragstart="
                          onEntityDragStart('character', char.id, $event)
                        "
                        @dragend="onEntityDragEnd"
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
                        @dragover="handleDragOverCharacter(char.id, $event)"
                        @dragleave="handleDragLeaveCharacter($event)"
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
                  <div
                    :class="[
                      'sidebar-project-tree-subsection',
                      { 'project-move-target': moveDragOverSetsId === p.id },
                    ]"
                    @dragover="onProjectSetsDragOver(p.id, $event)"
                    @dragleave="onProjectSetsDragLeave"
                    @drop.prevent="onProjectSetsDrop(p.id)"
                  >
                    <div
                      class="sidebar-project-tree-subheader"
                      :class="{
                        'sidebar-project-tree-subheader--empty':
                          !projectHasSets(p.id),
                      }"
                      :aria-disabled="!projectHasSets(p.id) || undefined"
                      @click.stop="
                        projectHasSets(p.id) && toggleProjectTreeSets(p.id)
                      "
                    >
                      <v-icon
                        size="14"
                        class="sidebar-row-glyph sidebar-project-tree-sub-chevron"
                        :class="{
                          'sidebar-row-glyph--empty': !projectHasSets(p.id),
                        }"
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
                              :style="setMenuPos"
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
                                      entityBelongsToProject(
                                        pset,
                                        selectedProjectId,
                                      ),
                                  }"
                                  @click.stop="
                                    toggleSetProjectMembership(pset.id)
                                  "
                                >
                                  <v-icon
                                    size="16"
                                    class="sidebar-move-menu-check"
                                    >{{
                                      entityBelongsToProject(
                                        pset,
                                        selectedProjectId,
                                      )
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
                        v-for="pset in nonReferenceSets.filter((s) =>
                          entityBelongsToProject(s, p.id),
                        )"
                        :key="pset.id"
                        :class="[
                          'sidebar-list-item',
                          'sidebar-set-item',
                          'sidebar-project-tree-child',
                          {
                            active:
                              selectedSetIdSet.has(pset.id) &&
                              selectionOwnsHighlight,
                            droppable: dragOverSet === pset.id && !dropRejected,
                            'not-droppable':
                              dragOverSet === pset.id && dropRejected,
                          },
                        ]"
                        :draggable="!isReadOnly"
                        @dragstart="onEntityDragStart('set', pset.id, $event)"
                        @dragend="onEntityDragEnd"
                        :title="`${pset.name || 'Picture Set'} (Ctrl/Cmd + click to multi-select)`"
                        @click="
                          selectSet(pset.id, pset.name || 'Picture Set', $event)
                        "
                        @contextmenu.prevent="
                          openSidebarCtxMenu('set', pset, $event)
                        "
                        @dragover="dragOverSetItem(pset.id, $event)"
                        @dragleave="dragLeaveSetItem($event)"
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
                          v-if="pset.locked"
                          class="sidebar-lock-icon"
                          size="11"
                          :title="SET_LOCKED_ROW_TITLE"
                          >mdi-lock-outline</v-icon
                        >
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
                    <ProjectFiles :projectId="p.id" compact />
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
      <!-- Suppressed for the empty-space menu: its view toggles (auto-hide,
           dock) are not content edits and stay enabled in read-only. -->
      <div v-if="isReadOnly && !sidebarCtxEmpty" class="ctx-readonly-header">
        <span class="ctx-readonly-pill">
          <v-icon size="10">mdi-lock-outline</v-icon>
          Read only
        </span>
      </div>
      <template v-if="sidebarCtxAllPictures">
        <!-- "About your library" moved here from its own permanent sidebar
             destination: it reads the whole library rather than acting on
             All Pictures specifically, but All Pictures is the one row that
             already means "the whole library" everywhere else in this
             sidebar, so its context menu is where owners now find it. -->
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          :title="isReadOnly ? READ_ONLY_INSIGHTS_HINT : undefined"
          @click="
            emit('select-insights');
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-lightbulb-on-outline</v-icon
          >
          About your library
        </button>
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
      <template v-if="sidebarCtxScrapheap">
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          :disabled="isReadOnly || scrapheapIsEmpty"
          :title="scrapheapIsEmpty ? 'Scrapheap is already empty' : undefined"
          :aria-disabled="isReadOnly || scrapheapIsEmpty"
          @click="emptyScrapheapFromCtx()"
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-trash-can-outline</v-icon
          >
          Empty Scrapheap
        </button>
      </template>
      <template v-if="sidebarCtxCharacter">
        <button
          v-if="!isReadOnly"
          class="sidebar-ctx-item"
          :title="`Rank the library against ${sidebarCtxCharacter.name}'s reference faces to find their un-tagged pictures`"
          @click="suggestPicturesForCharacterFromCtx(sidebarCtxCharacter)"
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-account-search</v-icon>
          <!-- The name is deliberately NOT in the label. The menu is anchored to
               that person's row and every other item in it is already about
               them, so repeating the name only bought an ellipsis: the menu is
               260px wide and "Suggest more pictures of <a real name>" does not
               fit. The title below still names them in full. -->
          <span class="sidebar-ctx-label">Suggest more pictures</span>
        </button>
        <button
          class="sidebar-ctx-item"
          :disabled="
            isReadOnly ||
            duplicateCountFor('character', sidebarCtxCharacter) === 0
          "
          :title="isReadOnly ? READ_ONLY_DEDUP_HINT : undefined"
          @click="findDuplicatesIn('character', sidebarCtxCharacter)"
        >
          <v-icon size="15" class="sidebar-ctx-icon">{{
            duplicateCountFor("character", sidebarCtxCharacter) === 0
              ? "mdi-check"
              : "mdi-content-duplicate"
          }}</v-icon>
          <span class="sidebar-ctx-label">{{
            duplicateCountFor("character", sidebarCtxCharacter) === 0
              ? "No duplicates for this person"
              : "Find duplicates for this person"
          }}</span>
          <span
            v-if="duplicateCountFor('character', sidebarCtxCharacter)"
            class="sidebar-ctx-count"
            >{{ duplicateCountFor("character", sidebarCtxCharacter) }}</span
          >
        </button>
        <div class="sidebar-ctx-divider"></div>
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
          <span class="sidebar-ctx-label"
            >Share "{{ sidebarCtxCharacter.name }}"</span
          >
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
          :disabled="
            isReadOnly || duplicateCountFor('set', sidebarCtxSet) === 0
          "
          :title="isReadOnly ? READ_ONLY_DEDUP_HINT : undefined"
          @click="findDuplicatesIn('set', sidebarCtxSet)"
        >
          <v-icon size="15" class="sidebar-ctx-icon">{{
            duplicateCountFor("set", sidebarCtxSet) === 0
              ? "mdi-check"
              : "mdi-content-duplicate"
          }}</v-icon>
          <span class="sidebar-ctx-label">{{
            duplicateCountFor("set", sidebarCtxSet) === 0
              ? "No duplicates in this set"
              : "Find duplicates in this set"
          }}</span>
          <span
            v-if="duplicateCountFor('set', sidebarCtxSet)"
            class="sidebar-ctx-count"
            >{{ duplicateCountFor("set", sidebarCtxSet) }}</span
          >
        </button>
        <div class="sidebar-ctx-divider"></div>
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
          <span class="sidebar-ctx-label"
            >Share "{{ sidebarCtxSet.name }}"</span
          >
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
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="toggleSetLock(sidebarCtxSet)"
        >
          <v-icon size="15" class="sidebar-ctx-icon">{{
            sidebarCtxSet.locked
              ? "mdi-lock-open-variant-outline"
              : "mdi-lock-outline"
          }}</v-icon>
          {{ sidebarCtxSet.locked ? "Unlock set" : "Lock set" }}
        </button>
        <button
          class="sidebar-ctx-item sidebar-ctx-item--danger"
          :disabled="isReadOnly || sidebarCtxSet.locked"
          :title="sidebarCtxSet.locked ? SET_LOCK_REASON : undefined"
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
          :disabled="
            isReadOnly || duplicateCountFor('project', sidebarCtxProject) === 0
          "
          :title="isReadOnly ? READ_ONLY_DEDUP_HINT : undefined"
          @click="findDuplicatesIn('project', sidebarCtxProject)"
        >
          <v-icon size="15" class="sidebar-ctx-icon">{{
            duplicateCountFor("project", sidebarCtxProject) === 0
              ? "mdi-check"
              : "mdi-content-duplicate"
          }}</v-icon>
          <span class="sidebar-ctx-label">{{
            duplicateCountFor("project", sidebarCtxProject) === 0
              ? "No duplicates in this project"
              : "Find duplicates in this project"
          }}</span>
          <span
            v-if="duplicateCountFor('project', sidebarCtxProject)"
            class="sidebar-ctx-count"
            >{{ duplicateCountFor("project", sidebarCtxProject) }}</span
          >
        </button>
        <div class="sidebar-ctx-divider"></div>
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
          v-if="!isReadOnly && !sidebarCtxFolderScopePath"
          class="sidebar-ctx-item"
          :disabled="duplicateCountFor('folder', sidebarCtxFolder) === 0"
          @click="findDuplicatesIn('folder', sidebarCtxFolder)"
        >
          <v-icon size="15" class="sidebar-ctx-icon">{{
            duplicateCountFor("folder", sidebarCtxFolder) === 0
              ? "mdi-check"
              : "mdi-content-duplicate"
          }}</v-icon>
          <span class="sidebar-ctx-label">{{
            duplicateCountFor("folder", sidebarCtxFolder) === 0
              ? "No duplicates in this folder"
              : "Find duplicates in this folder"
          }}</span>
          <span
            v-if="duplicateCountFor('folder', sidebarCtxFolder)"
            class="sidebar-ctx-count"
            >{{ duplicateCountFor("folder", sidebarCtxFolder) }}</span
          >
        </button>
        <div
          v-if="!isReadOnly && !sidebarCtxFolderScopePath"
          class="sidebar-ctx-divider"
        ></div>
        <button
          v-if="!inDocker && !sidebarCtxFolderScopePath"
          class="sidebar-ctx-item"
          @click="
            openReferenceFolderRelocateDialog(sidebarCtxFolder);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-folder-move-outline</v-icon
          >
          Relocate
        </button>
        <button
          v-if="!sidebarCtxFolderScopePath"
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
          v-if="!sidebarCtxFolderScopePath"
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
      <!-- ── Section-header menus (right-click a section's title) ── -->
      <template v-if="sidebarCtxHeader === 'people'">
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            createCharacter();
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-account-plus-outline</v-icon
          >
          Create person
        </button>
      </template>
      <template v-if="sidebarCtxHeader === 'sets'">
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            createSet();
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">mdi-image-album</v-icon>
          Create set
        </button>
      </template>
      <template v-if="sidebarCtxHeader === 'reference-folders'">
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            openReferenceFolderEditor();
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-folder-plus-outline</v-icon
          >
          Add folder
        </button>
      </template>
      <template v-if="sidebarCtxHeader === 'import-folders'">
        <button
          class="sidebar-ctx-item"
          :disabled="isReadOnly"
          @click="
            openImportFolderEditor();
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon"
            >mdi-folder-plus-outline</v-icon
          >
          Add folder
        </button>
      </template>
      <!-- ── Empty-space menu (right-click uncovered sidebar area) ── -->
      <template v-if="sidebarCtxEmpty">
        <button
          class="sidebar-ctx-item"
          @click="
            sidebarStore.setSidebarPinned(!sidebarStore.sidebarPinned);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">{{
            sidebarStore.sidebarPinned
              ? "mdi-checkbox-blank-outline"
              : "mdi-checkbox-marked-outline"
          }}</v-icon>
          Auto hide sidebar
        </button>
        <button
          class="sidebar-ctx-item"
          @click="
            sidebarStore.setSidebarDocked(!sidebarStore.sidebarDocked);
            closeSidebarCtxMenu();
          "
        >
          <v-icon size="15" class="sidebar-ctx-icon">{{
            sidebarStore.sidebarDocked
              ? "mdi-checkbox-marked-outline"
              : "mdi-checkbox-blank-outline"
          }}</v-icon>
          Dock mode
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
    :embed-watermark="userPrefsStore.embedWatermark"
    :public-url="userPrefsStore.publicUrl"
    @update:embed-watermark="userPrefsStore.embedWatermark = $event"
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
<style scoped src="./SideBar.css"></style>
<style src="./SideBar.global.css"></style>
