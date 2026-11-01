<script setup>
import { withRef } from "../../utils/withRef.js";
import {
  computed,
  ref,
  onBeforeUnmount,
  onMounted,
  onUpdated,
  watch,
  nextTick,
} from "vue";
import { MODEL_SHELF_ROUTES, WORKFLOW_ROUTES } from "../../router/routeNames";
import ImageImporter from "../io/ImageImporter.vue";
import CharacterEditor from "../editors/CharacterEditor.vue";
import PictureSetEditor from "../editors/PictureSetEditor.vue";
import ProjectEditor from "../editors/ProjectEditor.vue";
import ProjectFiles from "./ProjectFiles.vue";
import SidebarScopeSlot from "./SidebarScopeSlot.vue";
import SidebarResizeHandle from "./SidebarResizeHandle.vue";
import UserSettingsDialog from "../settings/UserSettingsDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import Segmented from "../widgets/Segmented.vue";
import Tooltip from "../widgets/Tooltip.vue";
import AppDialog from "../widgets/AppDialog.vue";
import FolderMappingWizard from "../folders/FolderMappingWizard.vue";
import { useFolderMappingStore } from "../../stores/useFolderMappingStore";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import ShareDialog from "../io/ShareDialog.vue";
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
import {
  inspectLibraryPath,
  listLibraryFolders,
  openLibraryFolder,
} from "../../api/libraries";
import { getFolderStructureCommitStatus } from "../../api/folderStructure";
import { deleteProject, getProjectSummary } from "../../api/projects";
import {
  listReferenceFolders,
} from "../../api/folders";
import { getPictureCount, setPicturesProject } from "../../api/pictures";
import { getSharedResourceIds, revokeTokensByResource } from "../../api/users";
import { listSortMechanisms } from "../../api/session";
import { listAdapters } from "../../api/modelShelf";
import {
  dropEffectFor,
  extractSupportedImportFilesFromDataTransfer,
  isFaceDrag,
  isFileDrag,
  isInternalImageDrag,
  isPictureDrag,
  readInternalDragPayload,
} from "../../utils/media.js";
import {
  PROJECT_VIEW_MODE,
  characterCountUpdates,
  projectCountUpdates,
} from "../../utils/sidebarCounts.js";
import {
  entityBelongsToProject,
  getEntityProjectIds,
  toggleEntityProjectPatch,
  withEntityProjectIds,
} from "../../utils/projectMembership.js";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import {
  ancestorsOf,
  buildSetTree,
  flattenBranch,
  treeGuideX,
  treeSpacerWidth,
} from "../../utils/setTree.js";
import {
  findMatch,
  folderTreeRows,
  inProjectBranch,
} from "../../utils/libraryTree.js";
import {
  SET_COLORS,
  SET_ICON_CATEGORIES,
  ICON_CARDS,
  nextSetAppearance,
  projectColor,
} from "../../utils/setAppearance.js";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSetTreeExpansion } from "../../composables/useSetTreeExpansion";
import { useSectionCollapse } from "../../composables/useSectionCollapse";
import { useSidebarWidth } from "../../composables/useSidebarWidth";
import { useRoute } from "vue-router";
import { useDedupStore, scopeKey } from "../../stores/useDedupStore";
import { useMovesStore } from "../../stores/useMovesStore";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useInboxStore } from "../../stores/useInboxStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useSortStore } from "../../stores/useSortStore";
import { useProjectStore } from "../../stores/useProjectStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useGridStore } from "../../stores/useGridStore";
import { useFilterStore } from "../../stores/useFilterStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
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

const dedupStore = useDedupStore();
const movesStore = useMovesStore();
const reviewSessionsStore = useReviewSessionsStore();
const inboxStore = useInboxStore();
const modelShelfStore = useModelShelfStore();

// Store-direct (Phase 3): the sidebar reads the selection, sort, project and
// preference state it displays straight from the stores and writes changes
// back itself, instead of mirroring all of it through App.vue.
const selectionStore = useSelectionStore();
const sortStore = useSortStore();
const projectStore = useProjectStore();
const userPrefsStore = useUserPrefsStore();

const gridStore = useGridStore();
const filterStore = useFilterStore();
const runDialogStore = useRunDialogStore();
const viewStore = useViewStore();
const route = useRoute();

// A folder filter and the Duplicates destination each suppress parts of the
// entity lists, so both are derived once here.
const hasFolderFilter = computed(
  () => selectionStore.selectedFolderFilter != null,
);
// Tags to review is a view drawn over whichever route it was opened from
// (`?review=` on that route, useReviewRoute.js), so while it is up it owns the
// highlight and the route's own destination row must not light beside it.
const isTagReviewView = computed(() => reviewSessionsStore.overlayOpen);
// Duplicates is addressed by route name, not by a selection sentinel: it shows
// no pictures, so there is no selection to express.
const isDuplicatesView = computed(
  () => route.name === "duplicates" && !isTagReviewView.value,
);
// Both of the shelf's routes, from the one list, so the runs tab cannot fall
// out of this the way it did when the two predicates were separate literals.
const isModelsView = computed(() => MODEL_SHELF_ROUTES.includes(route.name));
const isWorkflowsView = computed(() => WORKFLOW_ROUTES.includes(route.name));
// The title bar's Models dot means "arrived while you were not looking", so the
// shelf being open answers it, including for an arrival while it is open.
watch(
  () => isModelsView.value && modelShelfStore.unseenArrivals,
  (seen) => {
    if (seen) modelShelfStore.unseenArrivals = false;
  },
  { immediate: true },
);

// A shared library keeps the duplicate affordances VISIBLE and inert rather
// than hiding them: a read-only visitor should still see that the feature
// exists. Every /dedup/* route is owner-only (a duplicate group is defined by
// content identity, so it straddles any token's scope), so nothing here can be
// primed with a count or a badge - the row states the reason instead.
const READ_ONLY_DEDUP_HINT =
  "Duplicate review is only available in your own library";

// "About your library" is a destination too: it reads the library rather than
// showing it, so it has no selection to express.
const isInsightsView = computed(
  () => route.name === "insights" && !isTagReviewView.value,
);

// Same show-but-disable rule again. GET /insights is owner-only because its
// numbers ARE the vault-wide aggregate - narrowing them to a share token's
// scope would leak that out-of-scope pictures exist - so the row is visible,
// inert, and says why rather than quietly vanishing for a demo visitor.
const READ_ONLY_INSIGHTS_HINT =
  "Findings about a library are only available in your own";

// Moves is not a permanent destination the way Duplicates and Insights are: it is a
// to-do queue of moves made outside PixlStash, most libraries never populate
// it (no reference folder has opted into a layout), and unlike the others its
// data is never useful to a share visitor even as a curiosity. So it follows
// the opposite rule - hidden rather than shown-and-disabled - gated on
// movesStore.hasAnyPending, which itself never becomes true for a read-only
// session because nothing here ever calls GET /moves/pending for one.
const isMovesView = computed(
  () => route.name === "moves" && !isTagReviewView.value,
);
// The Inbox row: what was added within the chosen period and has no person
// and no set.
const isInboxView = computed(
  () => route.name === "inbox" && !isTagReviewView.value,
);

// The board is the "Tags to review" row's page; a review is its own child's.
const reviewBoardShown = computed(
  () => reviewSessionsStore.view.type === "board",
);
function isReviewSessionShown(id) {
  return (
    isTagReviewView.value &&
    reviewSessionsStore.view.type === "session" &&
    reviewSessionsStore.view.id === id
  );
}
function openTagReviewBoard() {
  if (isTagReviewView.value) reviewSessionsStore.showBoard();
  else emit("open-tag-review");
}
function reviewProgressPct(review) {
  const found = review.stats?.found ?? 0;
  return found ? Math.round(((review.progress?.done ?? 0) / found) * 100) : 0;
}
function reviewProgressText(review) {
  return `${review.progress?.done ?? 0}/${review.stats?.found ?? 0}`;
}

const props = defineProps({
  backendUrl: { type: String, default: () => API_BASE_URL },
  installType: { type: String, default: "pip" },
  dockerVariant: { type: String, default: "gpu" },
});

const emit = defineEmits([
  "select-duplicates",
  "select-insights",
  "select-moves",
  "select-inbox",
  "select-character",
  "select-set",
  "train-lora",
  "import-finished",
  "set-error",
  "set-loading",
  "images-assigned-to-character",
  "faces-assigned-to-character",
  "images-moved",
  "empty-scrapheap",
  "suggest-pictures-for-character",
  "suggest-pictures-for-set",
  "view-project",
  "select-folder",
  "open-import",
  "open-tag-review",
]);

const imageImporterRef = ref(null);
const sidebarRootRef = ref(null);
const labelOverflow = ref({});
const labelRefs = new Map();
const labelObservers = new Map();

const dragOverSet = ref(null);

// Which parents in the People and Sets trees are open (#2124), kept in
// localStorage so the tree's shape survives a reload.
const { expandedTreeKeys, toggleTreeKey, expandTreeKeys } =
  useSetTreeExpansion();

// The People and Sets sections fold away under their heads, remembered across
// reloads like an inspector section.
const peopleCollapsed = useSectionCollapse("people", { scope: "sidebar" });
const setsCollapsed = useSectionCollapse("sets", { scope: "sidebar" });

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
const projectEditorOpen = ref(false);
const projectEditorProject = ref(null);

// --- Move-to-project menus ---
const characterMoveMenuOpen = ref(false);
const characterMenuPos = ref({ top: "0px", left: "0px" });
const setMoveMenuOpen = ref(false);
const setMenuPos = ref({ top: "0px", left: "0px" });

// --- Sidebar Context Menu ---
const sidebarCtxVisible = ref(false);
const sidebarCtxX = ref(0);
const sidebarCtxY = ref(0);
const sidebarCtxCharacter = ref(null); // { id, name } or null
const sidebarCtxSet = ref(null); // { id, name, set_icon, set_color } or null
// Whether the open menu's person or set has a LoRA for "Create with LoRA…":
// 'checking' | 'attached' | 'none' | 'failed'. Only 'attached' enables the row.
const sidebarCtxLoraState = ref("checking");
let loraCheckEpoch = 0;
const setCtxIconMenuOpen = ref(false);
const setCtxColorMenuOpen = ref(false);
const setCtxAppearanceMenuPos = ref({ top: 0, left: 0, openUp: false });
const sidebarCtxProject = ref(null); // { id, name } or null
const sidebarCtxAllPictures = ref(false); // true when ctx opened from All Pictures row
const sidebarCtxScrapheap = ref(false); // true when ctx opened from the Scrapheap row
const sidebarCtxDeleteIds = ref([]); // character IDs to delete via context menu
// Right-click on a section HEADER (not an item): 'people' | 'sets', or null. Offers the "create/add" action.
const sidebarCtxHeader = ref(null);
// Right-click on empty/uncovered sidebar space: offers the view toggles
// (auto-hide, dock mode).
const sidebarCtxEmpty = ref(false);

// The rendered menu's height. It depends on the target (a set's menu is
// several rows taller than a folder's), so a fixed estimate flipped the taller
// menus one row too late and clipped their last entry. Measured after every
// render; the correcting re-render lands before paint.
const sidebarCtxMenuEl = ref(null);
const sidebarCtxMenuH = ref(0);
onUpdated(() => {
  if (sidebarCtxMenuEl.value) {
    sidebarCtxMenuH.value = sidebarCtxMenuEl.value.offsetHeight;
  }
});

// Computed style for the main context menu - opens upward when near the bottom.
const sidebarCtxMenuStyle = computed(() => {
  const MENU_W = 165;
  const x = Math.min(sidebarCtxX.value, window.innerWidth - MENU_W - 8);
  if (sidebarCtxY.value + sidebarCtxMenuH.value > window.innerHeight - 8) {
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

// These menus are teleported divs, not a `v-menu`, so nothing moves focus into
// them the way VMenu's activator does. Without this the menu declares
// `role="menu"` and then answers no key at all - worse for a screen reader than
// the unrolled divs it used to be, which at least read in browse mode.
async function _focusMoveMenu() {
  await nextTick();
  document.querySelector(".sidebar-move-menu")?.focus();
}

// The "+" that opened a move menu, so Escape can hand the keyboard back to it.
let moveMenuTrigger = null;

function openCharacterMoveMenu(event) {
  const el = event?.currentTarget ?? event?.target;
  moveMenuTrigger = el ?? null;
  if (el) {
    characterMenuPos.value = _moveMenuPos(el.getBoundingClientRect());
  }
  // One move menu at a time: both are `.sidebar-move-menu`, and the outside
  // click handler spares the other trigger, so both could stay open and focus
  // would land in whichever teleported first.
  setMoveMenuOpen.value = false;
  characterMoveMenuOpen.value = !characterMoveMenuOpen.value;
  if (characterMoveMenuOpen.value) _focusMoveMenu();
}

function openSetMoveMenu(event) {
  const el = event?.currentTarget ?? event?.target;
  moveMenuTrigger = el ?? null;
  if (el) {
    setMenuPos.value = _moveMenuPos(el.getBoundingClientRect());
  }
  characterMoveMenuOpen.value = false;
  setMoveMenuOpen.value = !setMoveMenuOpen.value;
  if (setMoveMenuOpen.value) _focusMoveMenu();
}

// Arrow keys move through the rows; Escape closes the menu and returns the
// keyboard to its "+", which a programmatic overlay never does on its own.
function onMoveMenuKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    characterMoveMenuOpen.value = false;
    setMoveMenuOpen.value = false;
    moveMenuTrigger?.focus?.();
    return;
  }
  onMenuKeydown(event);
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
// --- Reference and import folders ---
// The sidebar no longer lists them, but a folder view is still reached by URL
// (an old link, Back, a library insight), and it needs the folder's label and
// first-scan state. Owner only.
const foldersWanted = computed(() => !isReadOnly.value);
const referenceFolders = ref([]);
const selectedFolderKey = ref(null); // 'rf-{id}' | 'path-{path}' | null
const selectedFolderReferenceId = ref(null); // numeric reference-folder id or null
/** The absolute folder path the selection is filtering on, or null. The row key
    above cannot carry it: a folder ROOT row's key is `rf-{id}`. */
const selectedFolderPath = ref(null);
/** The same folder said RELATIVE to its reference folder's root - the sidebar's
    copy of what it last handed `?path=`, and `null` for the root itself. The
    URL carries this rather than the absolute path so the owner's folder tree is
    not in the address bar or the browser history (#1206 item 9). */
const selectedFolderSubPath = ref(null);

// Adding a folder goes through the mapping wizard.
const mappingStore = useFolderMappingStore();
const librariesStore = useLibrariesStore();
const searchStore = useSearchStore();

// ── Folder keys: five spellings, and which comparisons may cross them ────────
//
// Three separate rounds of review found bugs here, all of the same shape: two
// of these compared with `===` as though they were one thing. They are not, and
// none of them is redundant - they carry different information. The map:
//
//   1. Row key      `selectedFolderKey`: `rf-<id>` | `path-<path>`.
//      Identifies a ROW, which is why a subfolder needs its own path in it.
//      Written only by `handleFolderNodeSelect`.
//   2. Route key    `viewStore.activeFolderKey`: `rf-<id>`.
//      Identifies a FOLDER. Never `path-`: a route has no row.
//   3. Route path   `?path=`. A URL - any separator spelling, and editable.
//      On an `rf-` route it is now RELATIVE to the folder the id names
//      (#1206 item 9: the id already says where the folder is, so repeating the
//      owner's absolute path only put their folder tree in the address bar and
//      the browser history). An ABSOLUTE one is still read, because links and
//      history entries written before that carry one. On `/` - the only route
//      with no folder id - it stays absolute, since nothing else names it.
//      `selectedFolderSubPath` is the sidebar's copy of what it last wrote.
//   4. Grid filter  `selectedFolderFilter.pathPrefix`, which reaches the
//      listing as `file_path_prefix`: a literal LIKE against the stored
//      `file_path`, so it MUST be in the server's spelling.
//   5. Tree path    `entry.path` from the browse listing. Server spelling.
//
// Five crossings are legitimate, and each has one owner:
//   1 -> 2         `selectedFolderRouteKey`
//   4 -> 3         `_subPathUnder`, via `handleFolderNodeSelect` (the only
//                  place a server path is allowed to become a URL path)
//   2 + 3 -> 1,4   `routeSubfolderUnder`  (the only place a URL path is
//                  allowed to become a server path)
//   4 vs 3         the watcher's "already in sync" test, through `_urlPath`.
//                  This is the one an earlier pass missed: `routeSubfolderUnder`
//                  RE-SPELLS the path, so the sidebar's copy and the `?path=`
//                  that produced it are in different spaces and `_samePath`
//                  could never match them.
//   5 -> 1,4       direct; both are already the server's spelling.
//
// **The guardrail, not this comment, is the mechanism.** This list was written
// with three crossings and a claim that anything else was a bug; a fourth was
// found the same day, and the id rule it stated was already violated five
// times in this file. So: `SideBarFolderRouteRestore.test.js` asserts a route
// arriving twice is inert, parameterised over the separator-changing inputs -
// that is what fails when a new crossing is compared in the wrong space. Treat
// the list above as a map to read before editing, and add to it when you add a
// crossing; it is documentation, and the test is the enforcement.
//
// Ids: read every one through `_folderId`. That IS enforced, in the sense that
// there is now no other coercion in this file to copy from.

/**
 * A reference-folder id, or null. The ONE rule for reading one.
 *
 * `Number(null)` is `0` and `Number.isFinite(0)` is true, so coercing before
 * testing turns "no folder selected" into "folder zero" - which then reads as
 * a real selection everywhere downstream. `Number("")` is `0` too, so a
 * malformed `rf-` key would do the same. Both are rejected before coercion.
 */
function _folderId(value) {
  if (value == null || value === "") return null;
  const id = Number(value);
  return Number.isFinite(id) ? id : null;
}

/** A path made comparable: trailing separators dropped. */
function _normPath(p) {
  return String(p || "").replace(/[\\/]+$/, "");
}

/** Which separator a path is spelled with. A POSIX path always contains `/`,
 *  including one whose folder is legally NAMED `a\b`; a Windows path never
 *  does. Testing for `/` rather than for `\` is what keeps that name working. */
function _pathSeparator(p) {
  return String(p || "").includes("/") ? "/" : "\\";
}

function _samePath(a, b) {
  return _normPath(a) !== "" && _normPath(a) === _normPath(b);
}

/**
 * The same, and `\` folded to `/` as well.
 *
 * ONLY for comparing a path against one that arrived in a URL, where the two
 * halves can genuinely disagree about separators. Deliberately not folded into
 * `_samePath`: a POSIX folder may legally be named `a\b`, and making every
 * caller treat that as `a/b` would let the parked-read and pending-mapping
 * matches fire on the wrong folder - a wrong action, which is worse than the
 * missed one this guards against. Comparison only; the original spelling is
 * what goes on to the server.
 */
function _urlPath(p) {
  return _normPath(p).replace(/\\/g, "/");
}

/**
 * `path` said relative to `root`, or `null` when it is not inside it (the root
 * ITSELF included - that is "no subfolder", not "an empty one").
 *
 * The one crossing from the server's spelling into the URL's. The tail keeps
 * the path's OWN separators: `routeSubfolderUnder` re-spells on the way back,
 * and re-spelling here as well would only make the two disagree.
 *
 * Folding `\` into `/` is for a WINDOWS root only, on the same rule and for the
 * same reason as `routeSubfolderUnder`: on POSIX a `\` is an ordinary character
 * in a folder's NAME, and folding would slice the tail at a character that does
 * not separate anything.
 */
function _subPathUnder(root, path) {
  const rootPath = _normPath(root);
  const here = _normPath(path);
  if (!rootPath || !here) return null;
  const windows = _pathSeparator(rootPath) === "\\";
  const base = windows ? _urlPath(rootPath) : rootPath;
  const under = windows ? _urlPath(here) : here;
  // One character for one character, so the tail can be sliced out of the
  // ORIGINAL spelling at the offset the folded comparison found.
  return under.startsWith(`${base}/`) ? here.slice(rootPath.length + 1) : null;
}

/** Whether a `?path=` names a folder outright rather than one inside another.
    A tail sliced by `_subPathUnder` never starts with a separator and never
    carries a drive letter, so those are what tell the two apart. */
function _isAbsolutePath(p) {
  return /^([a-zA-Z]:)?[\\/]/.test(String(p || ""));
}

/**
 * The `?path=` on the current folder route, when it names a folder INSIDE
 * `root` rather than `root` itself.
 *
 * A subfolder click pushes `/ref-folder/:id?path=<tail under the folder>`, and
 * this is what reads it back so a reload or a shared link lands on the
 * subfolder the URL names instead of the folder root. `null` means "the route
 * names the folder itself", which is the ordinary case.
 *
 * **Both shapes of `?path=` are read.** A tail is what the sidebar writes now
 * (#1206 item 9); an ABSOLUTE path is what it wrote before, so links already
 * shared and history entries already made keep working - and a `/` route,
 * which has no id to be relative to, has no other shape available. An absolute
 * one that is not inside this folder is still `null`, the same refusal as ever.
 *
 * Matched through `_urlPath`, so the two sides may disagree about separators
 * and still match. They come from the same server and normally agree, but the
 * `?path=` half is a URL: it survives being shared, edited and pasted, and a
 * `/` where the folder listing said `\` would otherwise drop the reader back
 * to the folder root.
 *
 * **The path handed back is re-spelled in `root`'s own separator, not the
 * URL's.** It becomes `selectedFolderFilter.pathPrefix` and travels to the
 * listing API as `file_path_prefix`, which is a literal `LIKE` against the
 * stored `file_path` (`utils/query/predicate_filter.py`) - it does not
 * normalise anything. So on a Windows library a slash-spelled link would
 * select the right row and return an EMPTY grid, and the tree row's own key
 * (built from the browse listing's spelling) would not match the highlight
 * either. Rebuilding the tail off the root fixes both, and is a no-op on a
 * POSIX library, where the two spellings already agree.
 *
 * @param {string} root the reference folder's own path
 * @returns {{pathPrefix: string, label: string}|null}
 */
function routeSubfolderUnder(root) {
  const filter = viewStore.view?.folderFilter;
  if (!filter?.pathPrefix || !root) return null;
  const raw = _normPath(filter.pathPrefix);
  const rootPath = _normPath(root);
  const sep = _pathSeparator(rootPath);
  // Containment first, and for BOTH shapes. `?path=` is URL text - it survives
  // being shared, edited and pasted - and a `..` segment in it builds a
  // pathPrefix pointing outside the folder. The relative branch below took its
  // tail verbatim, so `?path=../../..` went straight through; the absolute
  // branch sliced against the root but `_subPathUnder` compares prefixes and
  // does not collapse `..` either, so `/root/../etc` passed it as the tail
  // `../etc`. Nothing sliced out of a real path carries a `.` or `..` segment,
  // so refusing one costs no legitimate link and restores the containment this
  // function exists to enforce. Split on what actually separates under this
  // root: on POSIX a `\` is an ordinary character in a folder's NAME, the same
  // rule `_subPathUnder` and the re-spelling below already follow.
  if (raw.split(sep === "\\" ? /[\\/]/ : /\//).some((s) => s === "." || s === ".."))
    return null;
  // An absolute `?path=` is sliced against the root (and refused when it names
  // somewhere else); a relative one already IS the tail. `_subPathUnder` folds
  // `\` into `/` for a WINDOWS root only, where both characters separate and a
  // URL may legitimately spell either. A POSIX root is compared raw, because
  // there `\` is an ordinary character in a folder's name and folding would
  // make `?path=/x/a/b/2024` - a different folder, or none - read as a
  // subfolder of `/x/a\b` and then be rewritten into it.
  const rawTail = _isAbsolutePath(raw) ? _subPathUnder(rootPath, raw) : raw;
  if (!rawTail) return null;
  const windows = sep === "\\";
  // Re-spelt only on Windows: on POSIX the tail's own backslashes are part of
  // a name, so this is genuinely a no-op there.
  const tail = windows ? rawTail.split(/[\\/]/).join(sep) : rawTail;
  return {
    pathPrefix: `${rootPath}${sep}${tail}`,
    label: tail.split(sep).pop() || filter.label,
  };
}

// The folder this session is on. A library on its first import is left out
// of the listing, so `activeLibrary` is undefined until the import finishes -
// and the import is exactly what the wizard reopened against this path runs.
const activeRoot = computed(
  () => librariesStore.activeLibrary?.path || librariesStore.importingPath,
);

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
  return _samePath(entry.path, activeRoot.value)
    ? entry
    : null;
});

/**
 * The empty library's "Choose a folder…" / "Import them…".
 *
 * When the library's own folder holds pictures the way in is the mapping
 * wizard over that folder, the same one the offer opens, never "Add a
 * library", which refuses the folder because the library already is it. So a
 * dismissed offer has a way back. Otherwise the ordinary add.
 */
async function chooseLibraryFolder() {
  if (!librariesStore.hasLoadedSuccessfully) await librariesStore.refresh();
  // Read after the refresh: `pendingForThisLibrary` matches the entry against
  // the active library's path, and with no list yet a good entry looks absent.
  const entry = pendingForThisLibrary.value;
  if (entry?.mode === "local_import" && (await pendingEntryIsLive(entry))) {
    openFolderMappingWizard(entry);
    return;
  }
  const path = activeRoot.value;
  if (path && librariesStore.canManage) {
    // Re-inspected on every click: a root that was empty when the count was
    // cached may hold pictures now. A failed inspect leaves the count alone.
    try {
      const verdict = await inspectLibraryPath(path);
      mappingStore.setRootPictureCount(
        verdict?.picture_count ?? 0,
        verdict?.picture_count_capped ?? false,
      );
    } catch (error) {
      console.warn("Could not check the library folder for pictures", {
        path,
        error,
      });
    }
    if (mappingStore.rootMayHoldPictures) {
      openFolderMappingWizard({ path, mode: "local_import" });
      return;
    }
  }
  // Adding a folder is "Add a library": a folder indexed in place as the
  // library's own storage.
  openFolderMappingWizard();
}

// The wizard's open state is the store's, so Settings › Libraries & imports and the
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
// commit task id -> in-flight Promise<completed?>; see `pendingEntryIsLive`.
// Above the watch, which runs immediately.
const commitChecks = new Map();
watch(
  () => pendingForThisLibrary.value,
  async (entry) => {
    if (!entry || isReadOnly.value) return;
    // Every entry is settled on sight, not only one this would open: the
    // "Finish organising…" row renders from the same entry.
    if (!(await pendingEntryIsLive(entry))) return;
    if (autoOpenedPendingMapping || entry.mode !== "local_import") return;
    autoOpenedPendingMapping = true;
    openFolderMappingWizard(entry);
  },
  { immediate: true },
);

/** The "Finish organising…" row: the same check as every other way in. */
async function openPendingMapping() {
  const entry = pendingForThisLibrary.value;
  if (entry && (await pendingEntryIsLive(entry))) openFolderMappingWizard(entry);
}

/**
 * Whether a saved entry still has anything to finish, clearing it when not.
 *
 * Promoting a first import closes the library's websockets, and the client
 * reloads on that close - usually before the wizard's poll sees `completed`
 * and clears the entry. Opened from it, the wizard would offer to set up the
 * library the import just finished. Every way of opening a saved entry asks
 * here; anything short of a definite `completed` (running, failed, forgotten)
 * is still live, as before. Concurrent askers share one request.
 */
async function pendingEntryIsLive(entry) {
  const id = entry.commitTaskId;
  if (!id) return true;
  if (!commitChecks.has(id)) {
    commitChecks.set(
      id,
      getFolderStructureCommitStatus(id)
        .then((body) => body?.status === "completed")
        .catch((error) => {
          console.warn("Could not check whether the saved import finished", {
            commitTaskId: id,
            error,
          });
          return false;
        }),
    );
  }
  const completed = await commitChecks.get(id);
  // Only a finished commit is final. One still running (or unreachable) may
  // finish later in this session, so the next opener asks again.
  if (!completed) {
    commitChecks.delete(id);
    return true;
  }
  if (mappingStore.pending?.commitTaskId === id) mappingStore.clear();
  return false;
}

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
  // Suppressed only by an entry that will ACTUALLY bring a wizard up, which is
  // the auto-open's own condition above: a `local_import` for this library.
  // `mappingStore.pending` is localStorage-backed and unbounded, and
  // `pendingForThisLibrary` is not the answer either - it returns a
  // `reference` entry unchanged, and the auto-open refuses those. Gating on
  // either meant an empty library whose owner held a stale entry got no wizard
  // AND no offer, for the life of the install; there was no way into the
  // library at all. This has to stay the same test as the auto-open, so state
  // it the same way.
  if (isReadOnly.value) return Promise.resolve();
  const entry = pendingForThisLibrary.value;
  if (entry?.mode === "local_import") {
    // Nothing to offer, but the empty state's wording reads the count, and on
    // a reload onto a persisted entry nobody has filled it yet.
    if (mappingStore.rootPictureCount === null && entry.pictureCount != null) {
      mappingStore.setRootPictureCount(entry.pictureCount);
    }
    return Promise.resolve();
  }
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
  const path = activeRoot.value;
  if (!path || !librariesStore.canManage) return;
  // On desktop the startup screen may have read this very folder already,
  // alongside the runtime download. Resuming that read is the whole point of
  // doing it there: the wizard opens on its questions instead of on a second
  // progress bar over an empty grid.
  const parked = await takeParkedFolderRead();
  if (parked?.result && _samePath(parked.path, path)) {
    mappingStore.setRootPictureCount(parked.result.picture_count ?? 0);
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
    mappingStore.setRootPictureCount(
      verdict?.picture_count ?? 0,
      verdict?.picture_count_capped ?? false,
    );
    // The store's reading, not `picture_count > 0`: a count capped at zero
    // stopped before it reached a picture and proves nothing.
    if (mappingStore.rootMayHoldPictures) {
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

// Whether the currently selected reference folder is actively being scanned
// for the first time (active but never completed a pass).
const selectedFolderScanning = computed(() => {
  const id = _folderId(selectedFolderReferenceId.value);
  if (id == null) return false;
  const rf = referenceFolders.value.find((f) => f.id === id);
  return Boolean(rf && rf.status === "active" && rf.last_scanned == null);
});

watch(selectedFolderScanning, (val) => {
  sidebarStore.folderScanning = val;
});

async function fetchReferenceFolders() {
  try {
    const body = await listReferenceFolders();
    referenceFolders.value = body?.folders ?? [];
    entityNames.mergeRefFolderLabels(referenceFolders.value);
    // If any folder is still pending, start polling for status updates.
    if (foldersWanted.value) {
      _startFolderStatusPoll();
    }
  } catch (e) {
    console.error("Failed to fetch reference folders:", e);
  }
}

/**
 * The sidebar's current selection, said the way a ROUTE says it.
 *
 * The sidebar's own key identifies a row, so a subfolder's is
 * `path-<absolute path>`; the route only ever names the
 * folder the row sits in. These two are what let the watcher below compare the
 * two spellings without either of them having to change: this is the folder,
 * `selectedFolderPath` is which folder inside it. An equality test on the row
 * key alone could neither tell "already showing this" from "showing its
 * sibling", nor recognise its own selection on the way out.
 */
const selectedFolderRouteKey = computed(() => {
  const id = _folderId(selectedFolderReferenceId.value);
  return id != null ? `rf-${id}` : null;
});

function handleFolderNodeSelect(key, payload) {
  selectedFolderKey.value = key;
  const payloadId = _folderId(payload?.referenceFolderId);
  if (payloadId != null) {
    selectedFolderReferenceId.value = payloadId;
  } else if (key?.startsWith("rf-")) {
    selectedFolderReferenceId.value = _folderId(key.slice(3));
  } else {
    selectedFolderReferenceId.value = null;
  }
  selectedFolderPath.value = payload?.pathPrefix ?? null;
  // What the URL will say. The id already names the folder, so `?path=` only
  // has to say which folder INSIDE it - and saying it absolutely put the
  // owner's whole folder tree in the address bar and the browser history
  // (#1206 item 9). `null` for the folder root, which needs no query at all.
  const root =
    referenceFolders.value.find((rf) => rf.id === selectedFolderReferenceId.value)
      ?.folder ?? null;
  const subPath = _subPathUnder(root, payload?.pathPrefix);
  selectedFolderSubPath.value = subPath;
  // Added only when there IS one, so a folder root's and an import folder's
  // payload stay exactly the object they were.
  emit("select-folder", subPath ? { ...payload, subPath } : payload);
  // Emit immediately on selection so ImageGrid updates before next poll tick.
  sidebarStore.folderScanning = selectedFolderScanning.value;
}

// Poll for folder status updates for the owner.
// Keeps polling while any reference folder is transitioning/retrying
// (pending, mount_error, or first active scan).
let _folderStatusPollTimer = null;

function _anyFolderNeedsPolling() {
  return referenceFolders.value.some(
    (rf) =>
      rf.status === "pending_mount" ||
      rf.status === "mount_error" ||
      (rf.status === "active" && rf.last_scanned == null),
  );
}

async function _pollFolderStatus() {
  try {
    const referenceBody = await listReferenceFolders();
    const folders = referenceBody?.folders ?? [];
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
      }
    }

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

// After the poll's timer is declared: `immediate` starts it during setup.
watch(
  foldersWanted,
  (shown) => {
    if (shown) {
      fetchReferenceFolders();
      _startFolderStatusPoll();
    } else {
      _stopFolderStatusPoll();
    }
  },
  { immediate: true },
);

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

/** The project a new person or set lands in: the scope's, unless a branch's
 * "+" names one (`null` = none). */
function newEntityProjectId(projectId) {
  if (projectId !== undefined) return projectId;
  return projectViewMode.value === "project" ? selectedProjectId.value : null;
}

function createSet(projectId = undefined) {
  const defaultProjectId = newEntityProjectId(projectId);

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

function createProject() {
  projectEditorProject.value = null;
  projectEditorOpen.value = true;
}

function exportProject(project) {
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
  // Through chooseScope, which navigates: setting the scope alone leaves the
  // route on the old view.
  if (newProjectId != null) chooseScope(newProjectId);
}

async function projectDeleted(deletedId) {
  closeProjectEditor();
  if (Number(selectedProjectId.value) === Number(deletedId)) chooseScope("any");
  await fetchProjects();
  await fetchCharacters();
  await fetchSidebarData();
}

async function deleteProjectById(project) {
  if (
    !window.confirm(
      `Delete project "${project.name}"? Its people, sets and pictures stay in the library; its attachments are deleted.`,
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

const nonReferenceSets = computed(() =>
  pictureSets.value.filter((pset) => !pset.reference_character),
);

const selectedProjectObj = computed(() =>
  projectViewMode.value === "project" && selectedProjectId.value !== null
    ? projects.value.find((p) => p.id === selectedProjectId.value) || null
    : null,
);

// When the session is scoped to a specific resource type via a share token,
// this reflects that type ('character', 'picture_set', 'project') or null.
const scopedResourceType = computed(() =>
  sessionContext.value?.scope === "READ"
    ? (sessionContext.value?.resource_type ?? null)
    : null,
);

const projectMenuCharacterGroups = computed(() => {
  if (menuProjectId.value == null) return [];
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
  if (menuProjectId.value == null) return [];
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

// --- Child sets (#2124): People and Sets as trees ---
// The whole library's tree, for opening a parent that gains a set; each
// branch of the Library tree builds its own (makeBranch).
const setTree = computed(() =>
  buildSetTree(
    nonReferenceSets.value,
    sortedCharacters.value.map((c) => c.id),
  ),
);

function isTreeOpen(key) {
  return expandedTreeKeys.value.has(key);
}

function childSetsLabel(n) {
  return n === 1 ? "1 set" : `${n} sets`;
}

/** Open the branch that holds the selected set, so the selection is never hidden. */
watch(
  () => [...selectedSetIdSet.value, nonReferenceSets.value.length > 0],
  () => {
    const ids = [...selectedSetIdSet.value];
    const keys = [];
    for (const id of ids) {
      const { setIds, characterId } = ancestorsOf(id, nonReferenceSets.value);
      keys.push(...setIds.map((sid) => `s:${sid}`));
      if (characterId != null) keys.push(`c:${characterId}`);
    }
    if (keys.length) expandTreeKeys(keys);
  },
  { immediate: true },
);

/**
 * Open a parent the moment it gains a set that did not exist before, so a set
 * just made inside a person or set (from the picker or the menu) is on screen,
 * not behind a closed chevron. The first list is the baseline, not news.
 *
 * "Did not exist" is judged against every set, not the scoped tree: a project
 * switch brings existing sets into view, and judged against the tree those
 * read as new and opened every parent.
 */
let knownSetIds = null;
watch(setTree, (tree) => {
  const now = new Map();
  for (const [id, kids] of tree.byCharacter) now.set(`c:${id}`, kids);
  for (const [id, kids] of tree.bySet) now.set(`s:${id}`, kids);
  if (knownSetIds) {
    const keys = [];
    for (const [key, kids] of now) {
      if (kids.some((kid) => !knownSetIds.has(Number(kid.id)))) {
        keys.push(key);
        if (key.startsWith("s:")) {
          const { setIds, characterId } = ancestorsOf(
            Number(key.slice(2)),
            nonReferenceSets.value,
          );
          keys.push(...setIds.map((sid) => `s:${sid}`));
          if (characterId != null) keys.push(`c:${characterId}`);
        }
      }
    }
    if (keys.length) expandTreeKeys(keys);
  }
  // The baseline is taken once both lists have arrived: people landing after
  // the sets would otherwise move every person's sets under them at once and
  // read as news, opening them all.
  if (entityLists.has("sets") && entityLists.has("characters")) {
    knownSetIds = new Set(pictureSets.value.map((s) => Number(s.id)));
  }
});

/** ArrowRight opens a parent row, ArrowLeft closes it; the rest is the row's. */
function onTreeRowKeydown(key, childCount, event) {
  if (childCount > 0 && event.key === "ArrowRight" && !isTreeOpen(key)) {
    event.preventDefault();
    toggleTreeKey(key);
    return;
  }
  if (childCount > 0 && event.key === "ArrowLeft" && isTreeOpen(key)) {
    event.preventDefault();
    toggleTreeKey(key);
    return;
  }
  activateOnEnterOrSpace(event);
}

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

// Scale People and Sets text with the thumbnail size: full size at 40px-and-up thumbnails,
// smaller below (≈0.8% per px under 40, floored at 0.85) so dense small-thumb
// layouts stay balanced. Feeds --sidebar-font-scale, which rescales the type-ramp
// tokens inside the People and Sets lists only (see the stylesheet).
const sidebarFontScale = computed(() => {
  const t = sidebarThumbnailSizeModel.value;
  if (t >= 40) return 1;
  return Math.max(0.85, Math.round((1 - (40 - t) * 0.008) * 1000) / 1000);
});

// The expanded width is the one every workspace's sidebar shares; the handle
// in the template drags it.
const { width: sidebarWidth, narrow: sidebarWidthNarrow } = useSidebarWidth();

// Too tight for the per-entry image counts; drop them (expanded only).
const sidebarIsNarrow = computed(
  () => !sidebarStore.effectiveDocked && sidebarWidthNarrow.value,
);

/** The mark of a tree row: the setting at the top level, the child size below. */
function treeMarkPx(level) {
  return level > 0 ? treeKidThumbPx.value : sidebarThumbnailSizeModel.value;
}

/**
 * A child set's mark: a fixed 0.6 of the setting, so every size in the tree
 * (marks, row heights, guide positions) grows in step with the one slider.
 * 16px is the floor a set glyph stays legible at.
 */
const treeKidThumbPx = computed(() =>
  Math.max(16, Math.round(sidebarThumbnailSizeModel.value * 0.6)),
);

const sidebarThumbStyle = computed(() => {
  const style = {
    "--sidebar-thumb-size": `${sidebarThumbnailSizeModel.value}px`,
    // The media list pins its places rows at 20px; People and Sets take the
    // thumbnail-size setting for their top-level rows (#2124).
    "--sidebar-entity-thumb": `${sidebarThumbnailSizeModel.value}px`,
    "--tree-kid-thumb": `${treeKidThumbPx.value}px`,
    "--sidebar-font-scale": sidebarFontScale.value,
  };
  // Apply the resized width only when expanded; docked width is driven by the
  // thumbnail size in CSS, so leave it to the stylesheet there.
  if (!sidebarStore.effectiveDocked) {
    style.width = `${sidebarWidth.value}px`;
  }
  return style;
});

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

// A new set opens, as a new project does. After the list has it: selectSet
// reads the set's project off the list, and inside a project a set read as in
// none filters the grid to UNASSIGNED.
async function openCreatedSet(created) {
  await fetchPictureSets();
  selectSet(created.id, created.name);
}

// The control focused when Settings opened and its ancestors, nearest first:
// DOM nodes, held raw. The ancestors are taken now because a node that has
// since been removed no longer leads back up to the ones still in the page.
let settingsReturnPath = [];

/**
 * Open the settings dialog, optionally on a specific nav entry.
 * @param {string} [tab] - nav entry id (e.g. "scrapheap"); Appearance if omitted
 *   or not visible in this session.
 */
function openSettingsDialog(tab = "") {
  settingsDialogInitialTab.value = typeof tab === "string" ? tab : "";
  // Settings opens from a prop, so Vuetify has no activator to hand focus back
  // to on close; the control that was focused when it opened takes it instead.
  settingsReturnPath = [];
  for (let el = document.activeElement; el && el !== document.body; ) {
    settingsReturnPath.push(el);
    el = el.parentElement;
  }
  settingsDialogOpen.value = true;
}

watch(settingsDialogOpen, (isOpen) => {
  if (isOpen) return;
  const path = settingsReturnPath;
  settingsReturnPath = [];
  // The opener may be gone (the shelf's empty state unmounts once a folder is
  // scanned in) or folded away under a narrow toolbar's ⋯; the nearest
  // ancestor that still takes focus (the shelf root) beats <body>.
  nextTick(() => {
    for (const el of path) {
      if (!el.isConnected) continue;
      el.focus();
      if (document.activeElement === el) return;
    }
  });
});

// A row inside a project's branch opens in THAT project, whatever the scope
// was: `inScope` lends its project to the one navigation it wraps (#2448).
let scopeOverride = null;

/** `inScope`'s "the whole library": Library's Not in a project branch. */
const WHOLE_LIBRARY = "whole-library";

/**
 * Run `fn` with `projectId` as the navigation's scope. `undefined` lends
 * nothing (a share token's flat list); `null` is in no project (Folders'
 * Global); `WHOLE_LIBRARY` is no project scope at all.
 */
function inScope(projectId, fn) {
  scopeOverride =
    projectId === undefined
      ? null
      : projectId === WHOLE_LIBRARY
        ? { mode: "global", projectId: null }
        : { mode: "project", projectId };
  try {
    fn();
  } finally {
    scopeOverride = null;
  }
}

/** The scope the grid is in, as a navigation's `projectContext`. */
function currentScopeContext() {
  if (scopeOverride) return scopeOverride;
  return projectViewMode.value === "project"
    ? { mode: "project", projectId: selectedProjectId.value ?? null }
    : { mode: "global", projectId: null };
}

function selectCharacter(id, label = null, event = null) {
  clearCountNew(id);
  const isSpecial =
    id === ALL_PICTURES_ID ||
    id === UNASSIGNED_PICTURES_ID ||
    id === SCRAPHEAP_PICTURES_ID;
  const isMultiToggle = !isSpecial && Boolean(event?.ctrlKey || event?.metaKey);

  if (!isMultiToggle) {
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
      // Scrapheap is outside every project; Unassigned is too, except as the
      // Folders tree's Unassigned, which lends it Not in a project.
      projectContext = scopeOverride ?? { mode: "global", projectId: null };
    } else {
      // A person opens inside the scope the header slot names, not the
      // person's own project: someone in two projects, seen from the second,
      // must not jump the view to the first (#2159).
      projectContext = currentScopeContext();
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
      projectContext: currentScopeContext(),
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
    // Opens inside the current scope, as a person does (see selectCharacter).
    emit("select-set", {
      id: numericSetId,
      label,
      ids: [numericSetId],
      names: { [numericSetId]: label || String(numericSetId) },
      projectIds: { [numericSetId]: setProjectId },
      projectContext: currentScopeContext(),
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
  const viewed = new Set(selectedCharacterIdSet.value);
  viewed.add(Number(selectionStore.selectedCharacter));
  const { deleted, failure } = await settleDeletes(ids, apiDeleteCharacter);
  if (deleted.some((id) => viewed.has(Number(id)))) leaveDeletedView();
  if (deleted.length) await fetchCharacters();
  if (failure) {
    console.error("Failed to delete characters", { ids, error: failure });
    setError(failure.message);
  }
}

/** Delete each id, and say which went and the first refusal. Settled rather
 * than all-or-nothing: the ones that did go must still leave the view and
 * drop out of the list when another is refused (a locked set, say). */
async function settleDeletes(ids, deleteOne) {
  const results = await Promise.allSettled(ids.map((id) => deleteOne(id)));
  return {
    deleted: ids.filter((_, i) => results[i].status === "fulfilled"),
    failure: results.find((r) => r.status === "rejected")?.reason ?? null,
  };
}

/** Deleting what is on screen must not leave the route on a dead id: go to
 * All Pictures, in the scope the header slot names. */
function leaveDeletedView() {
  emit("select-character", {
    id: ALL_PICTURES_ID,
    label: null,
    ids: [],
    projectIds: {},
    projectContext: currentScopeContext(),
  });
}

// The sets a set row's Delete acts on: the whole multi-selection when the
// row is part of one (the Sets head's delete button used to offer that), else
// just the row.
const ctxSetDeleteIds = computed(() => {
  const id = sidebarCtxSet.value?.id;
  if (id == null) return [];
  return selectedSetIdSet.value.size > 1 && selectedSetIdSet.value.has(id)
    ? Array.from(selectedSetIdSet.value)
    : [id];
});

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

  const { deleted, failure } = await settleDeletes(
    normalizedIds,
    deletePictureSet,
  );
  if (deleted.some((id) => selectedSetIdSet.value.has(id))) leaveDeletedView();
  if (deleted.length) {
    await fetchPictureSets();
    await fetchSidebarData();
  }
  if (failure) {
    console.error("Failed to delete picture sets", {
      ids: normalizedIds,
      error: failure,
    });
    noticeStore.error(`Couldn't delete those sets. ${noticeDetail(failure)}`, {
      key: "set-delete-many",
    });
  }
}

/** Forget the sidebar's folder selection - all four pieces of it.
 *
 * They are written together by `handleFolderNodeSelect` and must be cleared
 * together. Clearing a subset leaves state the UI cannot show but the route
 * watcher still reads: after a delete, `selectedFolderRouteKey` has already
 * changed, so the watcher's `=== oldKey` guard never matches and a leftover
 * `selectedFolderPath` / `selectedFolderSubPath` is never collected. Both
 * delete branches used to clear two of the four (#1206 item 12).
 */
function clearFolderSelection() {
  selectedFolderKey.value = null;
  selectedFolderReferenceId.value = null;
  selectedFolderPath.value = null;
  selectedFolderSubPath.value = null;
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
 * Find out whether the person or set a context menu just opened on has a LoRA
 * attached, so "Create with LoRA…" is only live when there is one to run.
 *
 * The row stays disabled until the answer lands and when it cannot be read:
 * the popup makes the same read, so it would fail the same way. Both
 * attachable file kinds, as `RunDialog.loadLoraSource` reads them.
 *
 * @param {string} type - the context-menu target type.
 * @param {Object} item - the target object.
 */
async function primeLoraCheck(type, item) {
  const mine = ++loraCheckEpoch;
  // Reset before any return: the last menu's answer is not this one's.
  sidebarCtxLoraState.value = "checking";
  const filter = { character: "characterId", set: "setId" }[type];
  if (!filter || !item?.id) return;
  // The row is not rendered for these, and the read is the owner's alone.
  if (isReadOnly.value || !filterStore.comfyuiConfigured) return;
  let state;
  try {
    const kinds = await Promise.all(
      ["adapter", "unknown"].map((fileKind) =>
        listAdapters({ fileKind, [filter]: Number(item.id) }),
      ),
    );
    // The same rows the Run popup then offers: a hidden LoRA is not one.
    state = kinds.flat().some((row) => row?.sha256 && !row.hidden)
      ? "attached"
      : "none";
  } catch (err) {
    console.warn(
      `Could not read the LoRAs attached to ${type} ${item.id}:`,
      err,
    );
    state = "failed";
  }
  // A menu opened on another row while this read was out has its own answer.
  if (mine === loraCheckEpoch) sidebarCtxLoraState.value = state;
}

// ComfyUI's configuration lands after start-up, possibly under an open menu.
// The row it reveals was skipped by the check above, so ask again.
watch([() => filterStore.comfyuiConfigured, isReadOnly], () => {
  if (!sidebarCtxVisible.value) return;
  if (sidebarCtxCharacter.value) {
    primeLoraCheck("character", sidebarCtxCharacter.value);
  } else if (sidebarCtxSet.value) {
    primeLoraCheck("set", sidebarCtxSet.value);
  }
});

/**
 * Why "Create with LoRA…" is disabled on the open menu, or "" when it is live.
 * @param {string} name - the person or set the menu was opened on.
 * @returns {string}
 */
function loraRowReason(name) {
  return {
    checking: "Checking for an attached LoRA…",
    none: `No LoRA is attached to ${name}. Assign one from the Models shelf.`,
    failed: "Could not read the Models shelf.",
  }[sidebarCtxLoraState.value] || "";
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
  // Warm the duplicate count for the object under the cursor, so the menu's
  // "Find duplicates in..." row can carry a number rather than open a queue
  // that turns out to be empty. A scoped count reuses cached hashes and is
  // cheap; the store also de-duplicates repeat opens on the same object.
  primeDuplicateCount(type, item);
  primeLoraCheck(type, item);
  // Reset here rather than in every branch: only the scrapheap branch turns it
  // on, so a single top-level reset keeps the per-type blocks below untouched.
  sidebarCtxScrapheap.value = false;
  // Same treatment for the header/empty targets - reset up front so the legacy
  // per-item branches below never have to clear them.
  sidebarCtxHeader.value = null;
  sidebarCtxEmpty.value = false;
  sidebarCtxFolderRow.value = null;
  // A right-click on a section header ('people' | 'sets'), on empty sidebar
  // space or on a Folders row with no entity (Global, Unassigned) is not tied
  // to a specific item, so clear every item target and short-circuit.
  if (type === "header" || type === "empty" || type === "folder") {
    sidebarCtxCharacter.value = null;
    sidebarCtxSet.value = null;
    sidebarCtxProject.value = null;
    sidebarCtxAllPictures.value = false;
    sidebarCtxDeleteIds.value = [];
    if (type === "header") sidebarCtxHeader.value = item;
    else if (type === "empty") sidebarCtxEmpty.value = true;
    sidebarCtxX.value = event.clientX;
    sidebarCtxY.value = event.clientY;
    sidebarCtxVisible.value = true;
    return;
  }
  if (type === "scrapheap") {
    sidebarCtxCharacter.value = null;
    sidebarCtxSet.value = null;
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
      sidebarCtxProject.value = null;
      sidebarCtxAllPictures.value = false;
    } else if (type === "project") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
      sidebarCtxProject.value = item;
      sidebarCtxAllPictures.value = false;
    } else if (type === "all-pictures") {
      sidebarCtxCharacter.value = null;
      sidebarCtxSet.value = null;
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

// "Suggest more pictures" for a set (#1489): ranks the library against the
// set's members so the pictures that belong in it can be added in one action.
// Like the person twin above, it does not select the set first.
function suggestPicturesForSetFromCtx(pictureSet) {
  if (!pictureSet?.id) return;
  closeSidebarCtxMenu();
  emit("suggest-pictures-for-set", {
    id: pictureSet.id,
    name: pictureSet.name,
  });
}

// "Create with LoRA…" (person or set): the Run popup, with its workflow
// picker narrowed to what fits the LoRA attached to it, that LoRA added to the
// run and the results filed back to it. The popup is App.vue's.
function openLoraRunFromCtx(entityType, item) {
  if (!item?.id) return;
  closeSidebarCtxMenu();
  runDialogStore.openRun({
    kind: "card",
    pickWorkflow: true,
    name: item.name,
    lora: { entityType, entityId: item.id, name: item.name },
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

// --- Child sets (#2124): New set inside…, Move out of… ---

// Sets nest three levels at most, counting the person or top-level set; the
// server refuses deeper (picture_set_tree.MAX_DEPTH).
const MAX_SET_DEPTH = 2;
const SET_DEPTH_REASON =
  "Sets nest three levels deep at most, counting the person or top-level set.";

/**
 * How far below its person or top-level set a set sits (0 at the top). A
 * chain that stops at a parent outside the list (another scope) counts as
 * full, so the menu never offers a create the server would refuse.
 */
function setDepth(set) {
  const { setIds, characterId, resolved } = ancestorsOf(
    set?.id,
    pictureSets.value,
  );
  if (!resolved) return MAX_SET_DEPTH;
  return setIds.length + (characterId != null ? 1 : 0);
}

/** Whether a set sits under anything, listed here or not. */
function setHasParent(set) {
  return set?.parent_set_id != null || set?.parent_character_id != null;
}

/** The display name of the set or person a set sits under, or "". */
function setParentName(set) {
  if (set?.parent_set_id != null) {
    return (
      pictureSets.value.find((s) => s.id === set.parent_set_id)?.name ?? ""
    );
  }
  if (set?.parent_character_id != null) {
    return (
      sortedCharacters.value.find((c) => c.id === set.parent_character_id)
        ?.name ?? ""
    );
  }
  return "";
}

/**
 * Open the set editor for a new set under a set or a person. The child joins
 * its parent's projects, so it shows where the parent does.
 */
function createChildSet({ setId = null, characterId = null, name = "" }) {
  closeSidebarCtxMenu();
  const parent =
    setId != null
      ? pictureSets.value.find((s) => s.id === setId)
      : sortedCharacters.value.find((c) => c.id === characterId);
  const projectIds = parent ? getEntityProjectIds(parent) : [];
  setEditorSet.value = {
    ...(projectIds.length ? { project_ids: projectIds } : {}),
    ...nextSetAppearance(nonReferenceSets.value),
    parent_set_id: setId,
    parent_character_id: characterId,
    parent_name: name || parent?.name || "",
  };
  setEditorOpen.value = true;
}

async function moveSetToTop(set) {
  closeSidebarCtxMenu();
  if (!set?.id) return;
  try {
    await patchPictureSet(set.id, {
      parent_set_id: null,
      parent_character_id: null,
    });
    refreshSidebar();
  } catch (e) {
    console.error("Failed to move the set out of its parent", e);
    noticeStore.error(
      `Couldn't move ${set.name} out. ${errorDetail(e) || "Please try again."}`,
      { key: "set-move-out" },
    );
  }
}

// Tooltip on the Delete entry when a set is locked (set-scoped; the store's
// lockReason is picture-scoped for the grid/lightbox).
const SET_LOCK_REASON =
  "This set is locked. Unlock it first (Unlock set) to delete it.";
const SET_LOCK_MOVE_REASON =
  "This set is locked. Unlock it first (Unlock set) to move it.";

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

function createCharacter(projectId = undefined) {
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
    project_id: newEntityProjectId(projectId),
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
    !isTagReviewView.value &&
    !isDuplicatesView.value &&
    !isModelsView.value &&
    !isInsightsView.value &&
    !isWorkflowsView.value,
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

// --- The places, the Library / Folders switch and Find (#2159, #2448) ---

// The project the view is limited to, or null for Any project and for Not in
// a project.
const scopedProjectObj = selectedProjectObj;

// The places above the switch are global (#2448): All media and Favourites
// always mean the whole library, and "all of a project" is the project's own
// row. Favourites is All media with the `favourite=1` filter on, so the two
// rows split that highlight on the one switch.
const isGlobalAllPictures = computed(
  () => isAllPicturesRowActive.value && projectViewMode.value !== "project",
);
const isAllMediaRowActive = computed(
  () => isGlobalAllPictures.value && !filterStore.favouriteOnlyFilter,
);
const isFavouritesRowActive = computed(
  () => isGlobalAllPictures.value && filterStore.favouriteOnlyFilter,
);

function selectAllMedia(favourites) {
  filterStore.favouriteOnlyFilter = favourites;
  chooseScope("any");
}

// Pictures in no project: the Not in a project and Global rows. Read only
// where a tree can render: a share-token session has no project list.
const notInProjectCount = ref(null);
// The Favourites row's number, library-wide like the row.
const favouriteCount = ref(null);

/**
 * Limit the view to a project: the grid and the counts follow. It lands on
 * All media in the new scope, keeping the Favourites filter, so Favourites in
 * one project becomes Favourites in the next.
 *
 * @param {"any"|"none"|number} scope
 */
function chooseScope(scope) {
  if (scope === "any") {
    projectViewMode.value = "global";
    selectedProjectId.value = null;
  } else {
    projectViewMode.value = "project";
    selectedProjectId.value = scope === "none" ? null : scope;
  }
  selectCharacter(ALL_PICTURES_ID, allPicturesRowLabel.value);
}

/** A project row, or Not in a project / Global (`null`): all of it. */
function openProjectBranch(projectId) {
  filterStore.favouriteOnlyFilter = false;
  chooseScope(projectId == null ? "none" : projectId);
}

// The All of <project> row under the dropdown: the scope itself as a row, lit
// while the grid shows all of it (not its favourites, as All media splits).
const isAllOfScopeRowActive = computed(
  () =>
    isAllPicturesRowActive.value &&
    projectViewMode.value === "project" &&
    !filterStore.favouriteOnlyFilter,
);
const allOfScopeLabel = computed(() =>
  libraryScope.value === null
    ? "All not in a project"
    : `All of ${scopedProjectObj.value?.name ?? "this project"}`,
);
const dragOverScopeRow = ref(false);
// The dropdown heads the box it scopes; a project token has no dropdown.
const showsScopeSlot = computed(
  () => showsTree.value && scopedResourceType.value !== "project",
);

// Only a project takes a drop: Not in a project names nowhere to add to.
function dragOverScopeItem(event) {
  if (libraryScope.value == null || isReadOnly.value) return;
  const verdict = acceptDrop(event, ["pictures", "files"]);
  if (verdict === "ignore") return;
  dropRejected.value = verdict === "reject";
  dragOverScopeRow.value = true;
}

function dragLeaveScopeItem(event) {
  if (event && !leftDropRow(event)) return;
  dragOverScopeRow.value = false;
}

/** Pictures dropped on the row join the project. Files are left to the
 * window's importer (useWindowFileImport), which already files them in the
 * project in view, this row's own; a second import here would race it. */
async function handleDropOnScope(event) {
  dragOverScopeRow.value = false;
  const projectId = libraryScope.value;
  if (projectId == null || isReadOnly.value) return;
  const imageIds = readDraggedImageIds(event);
  if (imageIds.length === 0) return;
  try {
    await setPicturesProject(imageIds, projectId, { mode: "add" });
    emit("images-moved", { imageIds, refresh: true });
  } catch (e) {
    console.error(
      `Failed to add ${imageIds.length} picture(s) to project ${projectId}`,
      e,
    );
    noticeStore.error(
      `Couldn't add those pictures to the project. ${noticeDetail(e)}`,
      { key: "project-drop" },
    );
  }
}

// How the tree below the switch is drawn: a user preference, remembered per
// library and never in the URL, so flipping it never moves the grid.
const TREE_MODE_KEY = "pixlstash:sidebar:treeMode";
const treeMode = ref("library");
const treeModeKey = computed(
  () => `${TREE_MODE_KEY}:${librariesStore.activeLibrary?.path ?? ""}`,
);
watch(
  treeModeKey,
  (key) => {
    let stored = null;
    try {
      stored = window.localStorage?.getItem(key);
    } catch (e) {
      console.warn(
        `Sidebar tree mode could not be read from localStorage (${key}); showing Library.`,
        e,
      );
    }
    treeMode.value = stored === "folders" ? "folders" : "library";
  },
  { immediate: true },
);

function setTreeMode(mode) {
  treeMode.value = mode;
  try {
    window.localStorage?.setItem(treeModeKey.value, mode);
  } catch (e) {
    console.warn(
      `Sidebar tree mode could not be saved (${treeModeKey.value}); it won't survive a reload.`,
      e,
    );
  }
}

const TREE_MODE_OPTIONS = [
  { id: "library", label: "Library", icon: "bookshelf", testid: "tree-mode-library" },
  { id: "folders", label: "Folders", icon: "folder-outline", testid: "tree-mode-folders" },
];

// A token for a person, a set or a picture gets no switch and no tree, only
// its own flat list; a project token keeps both, rooted at its project.
const showsTree = computed(
  () => !scopedResourceType.value || scopedResourceType.value === "project",
);

// Folders draws the owner's disk (#2458), which no share token reads: a
// read-only session keeps its Library list and gets no switch.
const showsFolders = computed(() => !isReadOnly.value);
const foldersMode = computed(
  () => showsFolders.value && treeMode.value === "folders",
);

// Find in library: names in the tree below the switch, never picture content.
const findQuery = ref("");
const findInputRef = ref(null);
const isFinding = computed(() => findQuery.value.trim() !== "");

// A find shows folded People and Sets, so the matches it counts are on screen,
// without writing over the fold: it comes back when the find is cleared.
const peopleOpen = computed(() => !peopleCollapsed.value || isFinding.value);
const setsOpen = computed(() => !setsCollapsed.value || isFinding.value);

/**
 * Does a Folders row stand for what the grid shows? In a project's scope, only
 * the rows under that project. With no scope (a deep link to a person, say),
 * a person or set lights under its own project, `entity` being the row's
 * character or set. `undefined` is the Library list, which is the scope.
 */
function branchIsScope(projectId, entity = null) {
  if (projectId === undefined) return true;
  if (projectViewMode.value === "project") {
    return (selectedProjectId.value ?? null) === projectId;
  }
  return entity != null && (entity.project_id ?? null) === projectId;
}

/** The Folders branch the selection lives in, by the rule above. */
const selectionBranchId = computed(() => {
  if (projectViewMode.value === "project") return selectedProjectId.value ?? null;
  const setId = selectedSetIdSet.value.values().next().value;
  const entity =
    setId != null
      ? pictureSets.value.find((s) => s.id === setId)
      : characters.value.find(
          (c) => c.id === Number(selectionStore.selectedCharacter),
        );
  return entity ? (entity.project_id ?? null) : undefined;
});

/**
 * A person's count in a scope (read off the list rows, which carry both): the
 * whole library with no project scope, and inside the project otherwise. The
 * list only carries the project-scoped number for the person's own project
 * (or for "in no project"), so in another project it is blank, not wrong.
 */
function personCountIn(scope, char) {
  if (scope === undefined) return char.image_count ?? "";
  if ((char.project_id ?? null) !== scope) return "";
  return char.project_image_count ?? "";
}

/** The dropdown's scope as a filter: undefined for Any project, null for
 * Not in a project, else the project's id. */
const libraryScope = computed(() =>
  projectViewMode.value !== "project" ? undefined : (selectedProjectId.value ?? null),
);

/**
 * Library mode's People and Sets: everyone in the dropdown's scope, narrowed
 * by Find. One list, as it shipped; a share token for a person or set gets the
 * same list without the switch or the dropdown.
 */
function makeLibraryList(scope) {
  const inScope = (e) => scope === undefined || inProjectBranch(e, scope);
  const allChars = sortedCharacters.value.filter(inScope);
  const allSets = nonReferenceSets.value.filter(inScope);
  const query = findQuery.value;
  const finding = isFinding.value;
  const chars = finding
    ? allChars.filter((c) => findMatch(c.name, query))
    : allChars;
  // While finding, a matched set is drawn flat in Sets, not under a parent
  // the search did not open.
  const sets = finding
    ? allSets
        .filter((s) => findMatch(s.name, query))
        .map((s) => ({ ...s, parent_set_id: null, parent_character_id: null }))
    : allSets;
  const tree = buildSetTree(
    sets,
    chars.map((c) => c.id),
  );
  const branchRows = (kids) =>
    flattenBranch(kids, tree.bySet, (id) => isTreeOpen(`s:${id}`));
  const rowsByCharacter = new Map();
  for (const [charId, kids] of tree.byCharacter) {
    rowsByCharacter.set(charId, isTreeOpen(`c:${charId}`) ? branchRows(kids) : []);
  }
  const setSections = tree.roots.map((root) => {
    const kids = tree.bySet.get(Number(root.id)) ?? [];
    const open = kids.length > 0 && isTreeOpen(`s:${root.id}`);
    return {
      root,
      open,
      rows: [
        { set: root, level: 0, last: true, rails: [], childCount: kids.length, expanded: open },
        ...(open ? branchRows(kids) : []),
      ],
    };
  });
  return {
    key: "library",
    // Rows light by the scope itself, and open in it.
    projectId: undefined,
    countScope: scope,
    // The "+" acts on the scope's project: undefined and null create.
    addProjectId: scope,
    characters: chars,
    sets,
    rowsByCharacter,
    setSections,
    childCount: (charId) => tree.byCharacter.get(Number(charId))?.length ?? 0,
  };
}

const libraryBranches = computed(() => [makeLibraryList(libraryScope.value)]);

// The Library list wears a faint wash of the scoped project's colour; the
// dropdown carries the stronger tint.
const scopeWashStyle = computed(() => washStyle(scopedProjectObj.value?.id));

// What the dropdown shows: Any project's and Not in a project's numbers, and
// each project's.
const scopeCounts = computed(() => ({
  any: categoryCounts.value[ALL_PICTURES_ID],
  none: notInProjectCount.value,
  byId: projectCounts.value,
}));

// Folders mode (#2458): the folders pictures are filed in, from the server.
const libraryFolders = ref([]);
const libraryFoldersRead = ref(false);
// The last read failed: said in the tree, so a blank tree is never mistaken
// for an empty library. The next refresh or mode switch reads again.
const libraryFoldersFailed = ref(false);
let libraryFoldersEpoch = 0;

async function fetchLibraryFolders() {
  if (!foldersMode.value) return;
  const epoch = (libraryFoldersEpoch += 1);
  try {
    const { folders } = await listLibraryFolders();
    if (epoch !== libraryFoldersEpoch) return;
    libraryFolders.value = folders ?? [];
    libraryFoldersRead.value = true;
    libraryFoldersFailed.value = false;
  } catch (err) {
    if (epoch !== libraryFoldersEpoch) return;
    libraryFoldersFailed.value = true;
    console.warn("Failed to read the library's folders for the Folders tree", err);
  }
}

watch(foldersMode, (on) => on && fetchLibraryFolders(), { immediate: true });

const folderRows = computed(() =>
  folderTreeRows({
    folders: libraryFolders.value,
    projects: sortedProjects.value,
    characters: sortedCharacters.value,
    sets: nonReferenceSets.value,
    isOpen: isTreeOpen,
    query: findQuery.value,
  }),
);

function folderRowCount(row) {
  return row.count ?? "";
}

function folderRowActive(row) {
  if (row.kind === "folder" || !selectionOwnsHighlight.value) return false;
  const placed = row.kind === "project" || row.kind === "global" ? null : row.entity;
  if (!branchIsScope(row.projectId, placed)) return false;
  if (row.kind === "project" || row.kind === "global")
    return isAllPicturesRowActive.value;
  if (row.kind === "unassigned")
    return selectionStore.selectedCharacter === UNASSIGNED_PICTURES_ID;
  if (row.kind === "character")
    return selectionStore.selectedCharacter === row.entity.id;
  return selectedSetIdSet.value.has(row.entity.id);
}

// A row opens what the folder names, not the folder: a project or Global its
// branch, a person inside the nearest project, a set whole. The owner's own
// folder names nothing, so it only opens and closes.
function openFolderRow(row, event = null) {
  if (row.kind === "project" || row.kind === "global") {
    openProjectBranch(row.projectId);
  } else if (row.kind === "unassigned") {
    inScope(null, () =>
      selectCharacter(UNASSIGNED_PICTURES_ID, "Unassigned Pictures"),
    );
  } else if (row.kind === "character") {
    inScope(row.projectId, () =>
      selectCharacter(row.entity.id, row.entity.name, event),
    );
  } else if (row.kind === "set") {
    openSetRow(row.entity, event);
  } else if (row.childCount) {
    toggleTreeKey(row.key);
  }
}

// A Folders row's menu: its entity's own (a project, person or set), headed
// by Open in file manager; Global and Unassigned have only that.
const sidebarCtxFolderRow = ref(null);

function openFolderRowMenu(row, event) {
  if (row.kind === "project") openSidebarCtxMenu("project", row.entity, event);
  else if (row.kind === "character")
    openSidebarCtxMenu("character", row.entity, event);
  else if (row.kind === "set") openSidebarCtxMenu("set", row.entity, event);
  // A share token cannot open a folder, so the rest have no menu.
  else if (isReadOnly.value) return;
  else openSidebarCtxMenu("folder", null, event);
  sidebarCtxFolderRow.value = row;
}

// The folder opens on the machine running PixlStash, so a press that did
// nothing says why: 403 is the app opened from another machine (the route is
// loopback-only), 404 a folder emptied or moved since the tree was read, which
// re-reads the tree.
async function openFolderInFileManager(row) {
  try {
    await openLibraryFolder(row.path);
  } catch (err) {
    const status = err?.response?.status;
    if (status === 404) fetchLibraryFolders();
    console.warn(`Failed to open the ${row.kind} folder ${row.key}`, err);
    noticeStore.warning(
      status === 403
        ? "A file manager opens on the machine running PixlStash, so this only works when you are sitting at it."
        : status === 404
          ? `Nothing is filed in “${row.name}” any more.`
          : "Couldn't open that folder - the machine running PixlStash has no desktop file manager.",
      { key: "sidebar-open-folder" },
    );
  }
}

/** A name split round Find's match, for an underline; all `before` when
 * nothing matches. */
function findParts(name) {
  return (
    findMatch(name, findQuery.value) ?? { before: name, match: "", after: "" }
  );
}

// A project's rows carry a faint wash of its colour.
function washStyle(projectId) {
  return projectId != null
    ? { "--wash": `color-mix(in srgb, ${projectColor(projectId)} 6%, transparent)` }
    : null;
}

function folderWashStyle(row) {
  if (row.kind === "project") return row.open ? washStyle(row.projectId) : null;
  return row.depth > 0 ? washStyle(row.projectId) : null;
}

// Every match, in the order drawn, as the navigation it opens.
const findMatches = computed(() => {
  if (!isFinding.value) return [];
  if (foldersMode.value) {
    return folderRows.value
      .filter((row) => row.match)
      .map((row) => () => openFolderRow(row));
  }
  // Library: the people and sets of the dropdown's scope, opened in it.
  const [list] = libraryBranches.value;
  return [
    ...list.characters.map((c) => () => selectCharacter(c.id, c.name)),
    ...list.sets.map((s) => () => selectSet(s.id, s.name)),
  ];
});

function matchCountLabel(n) {
  return n === 1 ? "1 match" : `${n} matches`;
}

function onFindKeydown(event) {
  if (event.key === "Escape") {
    if (!findQuery.value) return;
    event.preventDefault();
    event.stopPropagation();
    findQuery.value = "";
  } else if (event.key === "Enter") {
    event.preventDefault();
    findMatches.value[0]?.();
  }
}

/** Leave Find; true when there was a search to leave. Esc anywhere outside a
 * text field reaches here through the app's global keydown. */
function clearFind() {
  if (!findQuery.value) return false;
  findQuery.value = "";
  return true;
}

/** No name matched: hand the words to the toolbar's picture search. */
function searchPicturesForFind() {
  searchStore.searchInput = findQuery.value.trim();
  searchStore.commitSearch();
  findQuery.value = "";
}

/**
 * A set row, in either tree: the whole set. A set is a collection made by
 * hand and nothing counts it inside one project, so a set is not narrowed to
 * a branch's project. In Library its row counts all of it (`picture_count`);
 * in Folders the row counts what is filed in that folder (#2458), so the
 * grid can hold more than the number. A person row stays in its branch's
 * project.
 */
function openSetRow(pset, event = null) {
  inScope(showsTree.value ? WHOLE_LIBRARY : undefined, () =>
    selectSet(pset.id, pset.name || "Picture Set", event),
  );
}

// The Folders branch holding the selection is forced open: a project chosen
// from anywhere (the dropdown, Back, a deep link) opens in place there, with
// every folder above it. Re-run when the folders first arrive, not on every
// re-read, so a branch the owner closed stays closed.
watch(
  [selectionBranchId, () => libraryFolders.value.length > 0],
  ([id]) => {
    if (id === undefined) return;
    const branch = libraryFolders.value.find((f) =>
      id == null
        ? f.kind === "global" && !f.path.includes("/")
        : f.kind === "project" && f.entity_id === id,
    );
    if (!branch) return;
    const parts = branch.path.split("/");
    expandTreeKeys(parts.map((_, i) => `f:${parts.slice(0, i + 1).join("/")}`));
  },
  { immediate: true },
);

// --- The docked rail's tree (#2448): one button per top-level node ---

const railModeLabel = computed(() =>
  treeMode.value === "library"
    ? "Showing Library. Switch to Folders"
    : "Showing Folders. Switch to Library",
);

function projectInitial(project) {
  return (project?.name || "?").trim().charAt(0).toUpperCase() || "?";
}

/** A Folders node's flyout: all of it (the owner's own folder, which names
 * nothing, opens in the file manager instead), then the people and sets
 * filed in it, each labelled by its path below the node. */
const RAIL_ICONS = {
  global: "mdi-earth",
  unassigned: "mdi-folder-question-outline",
  folder: "mdi-folder-outline",
  character: "mdi-account-outline",
  set: "mdi-image-album",
};

function folderNodeItems(node) {
  const below = `${node.row.path}/`;
  const kids = folderTreeRows({
    folders: libraryFolders.value,
    projects: sortedProjects.value,
    characters: sortedCharacters.value,
    sets: nonReferenceSets.value,
    isOpen: () => true,
  }).filter(
    (row) =>
      row.path.startsWith(below) && (row.kind === "character" || row.kind === "set"),
  );
  return [
    node.row.kind !== "folder"
      ? {
          key: "all",
          label: `All of ${node.label}`,
          icon: "mdi-image-multiple-outline",
          run: () => openFolderRow(node.row),
        }
      : {
          key: "open",
          label: "Open in file manager",
          icon: "mdi-folder-open-outline",
          run: () => openFolderInFileManager(node.row),
        },
    ...(kids.length ? [{ key: "sep", sep: true }] : []),
    ...kids.map((row) => ({
      key: row.key,
      label: row.path.slice(below.length).split("/").join(" › "),
      icon: RAIL_ICONS[row.kind],
      run: () => openFolderRow(row),
    })),
  ];
}

/** Library's one rail button: All of the project in view, Limit view to as
 * the expanded dropdown offers it, then the People and Sets it narrows. A
 * token for one project has no dropdown: it keeps All of its own project,
 * its only way back to the whole of it from a person or set, and the lists. */
function libraryNodeItems() {
  const scope = libraryScope.value;
  const [list] = libraryBranches.value;
  const people = list.characters.map((c) => ({
    key: `c-${c.id}`,
    label: c.name,
    icon: RAIL_ICONS.character,
    run: () => selectCharacter(c.id, c.name),
  }));
  const sets = list.sets.map((pset) => ({
    key: `s-${pset.id}`,
    label: pset.name,
    icon: RAIL_ICONS.set,
    run: () => selectSet(pset.id, pset.name),
  }));
  const project = scopedProjectObj.value;
  return [
    // As the design opens it: all of the project in view comes first.
    ...(project
      ? [
          {
            key: "all",
            label: `All of ${project.name}`,
            icon: "mdi-image-multiple-outline",
            run: () => chooseScope(project.id),
          },
        ]
      : []),
    ...(scopedResourceType.value === "project" ? [] : scopeItems(scope)),
    ...(people.length ? [{ key: "hp", section: "People" }, ...people] : []),
    ...(sets.length ? [{ key: "hs", section: "Sets" }, ...sets] : []),
  ];
}

function scopeItems(scope) {
  return [
    { key: "h", section: "Limit view to" },
    {
      key: "any",
      label: "Any project",
      icon: "mdi-folder-multiple-outline",
      checked: scope === undefined,
      run: () => chooseScope("any"),
    },
    ...sortedProjects.value.map((p) => ({
      key: `p-${p.id}`,
      label: p.name,
      icon: RAIL_ICONS.folder,
      checked: scope === p.id,
      run: () => chooseScope(p.id),
    })),
    {
      key: "none",
      label: "Not in a project",
      icon: "mdi-folder-off-outline",
      checked: scope === null,
      run: () => chooseScope("none"),
    },
  ];
}

const railNodes = computed(() => {
  if (!foldersMode.value) {
    // One button, as the design draws it: the project in view, its flyout
    // the dropdown with the People and Sets below it.
    const project =
      scopedResourceType.value === "project" ? null : scopedProjectObj.value;
    return [
      {
        key: "r:library",
        kind: "library",
        label:
          scopedResourceType.value === "project"
            ? "People and sets"
            : project?.name ??
              (libraryScope.value === null ? "Not in a project" : "Any project"),
        project,
        icon:
          libraryScope.value === null
            ? "mdi-folder-off-outline"
            : "mdi-folder-multiple-outline",
        items: libraryNodeItems,
      },
    ];
  }
  // Folders: one button per top-level folder.
  return folderTreeRows({
    folders: libraryFolders.value,
    projects: sortedProjects.value,
    characters: sortedCharacters.value,
    sets: nonReferenceSets.value,
    isOpen: () => false,
  }).map((row) => {
    const node = {
      key: `r:${row.key}`,
      kind: row.kind === "unassigned" ? "unassigned" : "folder",
      projectId: row.projectId,
      project: row.kind === "project" ? row.entity : undefined,
      label: row.name,
      icon: RAIL_ICONS[row.kind],
      leaf: row.kind === "unassigned",
      row,
    };
    if (!node.leaf) node.items = () => folderNodeItems(node);
    return node;
  });
});

function railNodeActive(node) {
  if (!selectionOwnsHighlight.value) return false;
  const character = selectionStore.selectedCharacter;
  // Library's button lights for all of a project, a listed person or a set.
  // A person is selected when the selection names a listed person: the
  // places' ids are strings, and no selection at all (null) is not one.
  if (node.kind === "library")
    return (
      (isAllPicturesRowActive.value && projectViewMode.value === "project") ||
      selectedSetIdSet.value.size > 0 ||
      characters.value.some((c) => c.id === Number(character) && character != null)
    );
  if (node.row && !["project", "global", "unassigned"].includes(node.row.kind))
    return folderRowActive(node.row);
  if (!branchIsScope(node.projectId)) return false;
  const onUnassigned = character === UNASSIGNED_PICTURES_ID;
  return node.leaf ? onUnassigned : !onUnassigned;
}

const railFlyout = ref(null);
const railFlyoutRef = ref(null);
let railFlyoutTrigger = null;

function onRailOutsidePointer(event) {
  if (railFlyoutRef.value?.contains(event.target)) return;
  if (railFlyoutTrigger?.contains(event.target)) return;
  closeRailFlyout();
}

function closeRailFlyout({ restoreFocus = false } = {}) {
  document.removeEventListener("pointerdown", onRailOutsidePointer, true);
  railFlyout.value = null;
  if (restoreFocus) railFlyoutTrigger?.focus();
}

function openRailNode(node, event) {
  if (node.leaf) {
    closeRailFlyout();
    inScope(null, () =>
      selectCharacter(UNASSIGNED_PICTURES_ID, "Unassigned Pictures"),
    );
    return;
  }
  if (railFlyout.value?.key === node.key) {
    closeRailFlyout();
    return;
  }
  railFlyoutTrigger = event.currentTarget;
  const rect = railFlyoutTrigger.getBoundingClientRect();
  railFlyout.value = {
    key: node.key,
    node,
    // Kept on screen: the flyout scrolls past 70% of the window's height.
    top: Math.max(8, Math.min(rect.top, window.innerHeight * 0.3 - 8)),
    left: rect.right + 4,
    items: node.items(),
  };
  document.addEventListener("pointerdown", onRailOutsidePointer, true);
  nextTick(() => railFlyoutRef.value?.querySelector(".ctx-item")?.focus());
}

function pickRailEntry(open) {
  open();
  closeRailFlyout();
}

function onRailFlyoutKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeRailFlyout({ restoreFocus: true });
    return;
  }
  onMenuKeydown(event);
}

// Expanding the sidebar takes the flyout's anchor away.
watch(
  () => sidebarStore.effectiveDocked,
  (docked) => {
    if (!docked) closeRailFlyout();
  },
);
onBeforeUnmount(() => closeRailFlyout());

// The project a branch's "+" acts on, read by the add-or-remove menus it
// opens. `undefined` outside a branch (a share token's flat list).
const menuProjectId = ref(undefined);

function sectionAddLabel(kind, projectId) {
  if (projectId != null) {
    return kind === "people"
      ? "Add people to this project"
      : "Add sets to this project";
  }
  return kind === "people" ? "Add person" : "Create new set";
}

// A branch's "+". Inside a project it opens the menu that makes a new one in
// the project or ticks existing ones in and out; elsewhere it makes a new one.
function addFromSectionHead(kind, projectId, event) {
  menuProjectId.value = projectId;
  if (projectId != null) {
    if (kind === "people") openCharacterMoveMenu(event);
    else openSetMoveMenu(event);
    return;
  }
  if (kind === "people") createCharacter(projectId);
  else createSet(projectId);
}

// Where the footer's Import puts pictures: the open library, and the project
// in scope, which App passes on as the import dialog's default project.
const importTooltip = computed(() => {
  const place = [
    librariesStore.activeLibrary?.name,
    scopedProjectObj.value?.name,
  ].filter(Boolean);
  return place.length ? `Import into ${place.join(" › ")}` : "Import pictures";
});
const importLabel = computed(() =>
  scopedProjectObj.value ? `Import to ${scopedProjectObj.value.name}` : "Import",
);

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
    // The tree files every person under a project (or none), so a row reads
    // the count inside its own project; a person token's flat list has no
    // project to read it in.
    for (const { id, count } of characterCountUpdates(
      characterRows,
      showsTree.value ? PROJECT_VIEW_MODE : "global",
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
    // The Folders tree's Unassigned: in no project, and no person or set
    // of the ones in no project either, so every picture in Global is under
    // one of its folders or here.
    const data = await getCharacterSummary(UNASSIGNED_PICTURES_ID, {
      project_id: "UNASSIGNED",
    });
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
  // Pictures in no project: the Not in a project and Global rows. Only where
  // a project tree renders, since for
  // a token scoped to a character / picture / set this is a project route that
  // 403s, and it once logged a warning on every refresh for a number no row
  // showed.
  if (!scopedResourceType.value) {
    try {
      const data = await getProjectSummary("UNASSIGNED");
      if (isCurrentRequest()) notInProjectCount.value = data.image_count;
    } catch (e) {
      console.warn("Error fetching the not-in-a-project count:", e);
    }
  }
  // The Inbox row's number, inside the sidebar's project like the grid it
  // opens. Owner only, as the row is.
  if (!isReadOnly.value) void inboxStore.refreshCount();
  // Favourites in the whole library, as the row means (#2448): the same
  // `/pictures/count` query the grid's filter makes. Owner only, as the row is.
  if (!isReadOnly.value) {
    try {
      const data = await getPictureCount("favourite=1");
      if (isCurrentRequest()) favouriteCount.value = data.count;
    } catch (e) {
      console.warn("Error fetching the favourites count:", e);
    }
  }
  if (isCurrentRequest()) flashCountsNextFetch.value = false;
}

async function fetchCharacters() {
  setLoading(true);
  setError(null);
  try {
    const [nextCharacters] = await entityLists.invalidate(["characters"]);
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
  fetchLibraryFolders();
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
  const [rows] = await entityLists.invalidate(["projects"]);
  entityNames.mergeProjectNames(rows);
}

async function fetchPictureSets() {
  // Always fetch all sets - in the flat project tree each project filters
  // its own sets client-side, so we must not scope this call to a single project.
  // `invalidate`, not `refresh`, in all three fetchers: they run after a change
  // (a delete, a save, a WS event), and `refresh` would join a read already in
  // flight that started before it, putting a deleted row back on screen.
  const [sets] = await entityLists.invalidate(["sets"]);
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


async function handleDropOnSet(setId, event) {
  dragOverSet.value = null;
  // An internal grid drag skips the file-import path: browsers also populate
  // dataTransfer.files for <img> drags, and a desktop drag-out IS a file drag.
  const isInternalDrag = isInternalImageDrag(event?.dataTransfer);
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
  const verdict = acceptDrop(event, ["pictures", "faces", "files"]);
  if (verdict === "ignore") return;
  dropRejected.value = verdict === "reject";
  dragOverCharacter.value = id;
}

function handleDragLeaveCharacter(event) {
  if (event && !leftDropRow(event)) return;
  dragOverCharacter.value = null;
}



function readDraggedImageIds(event) {
  const data = readInternalDragPayload(event?.dataTransfer);
  // A face drag carries imageIds too (the pictures the faces were found in),
  // so the payload kind, not the presence of imageIds, decides.
  if (data?.type !== "image-ids") return [];
  return Array.isArray(data.imageIds) ? data.imageIds.filter(Boolean) : [];
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
    // A file drag asks to copy where the drag lets it: the import copies.
    if (dt) dt.dropEffect = dropEffectFor(dt, isFileDrag(dt) ? "copy" : "move");
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


async function onCharacterDrop(characterId, event) {
  dragOverCharacter.value = null;
  // An internal grid drag skips the file-import path: browsers also populate
  // dataTransfer.files for <img> drags, and a desktop drag-out IS a file drag.
  const isInternalDrag = isInternalImageDrag(event?.dataTransfer);
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
    const data = readInternalDragPayload(event.dataTransfer);
    if (!data) throw new Error("The drag carried nothing to assign.");
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
async function characterSaved(saved) {
  const created =
    characterEditorCharacter.value && !characterEditorCharacter.value.id;
  if (created) {
    // New character was created, increment nextCharacterNumber. The row itself
    // comes from the refetch below - the shared list is never written locally.
    nextCharacterNumber.value++;
  }
  await fetchCharacters(); // Refresh characters
  await fetchSortOptions(); // Ensure sort options include similarity when characters exist
  await fetchPictureSets(); // Refresh picture sets to include reference sets
  closeCharacterEditor();
  // A new person opens, as a new project does.
  if (created && saved?.id) selectCharacter(saved.id, saved.name);
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
        selectedProjectId.value = projectStore.selectedProjectId;
      }
      nextTick(() => {
        _initializing = false;
      });
    }
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
onBeforeUnmount(() => {
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
  // Re-fetch sets for the newly selected project (sidebar list scope).
  void fetchPictureSets();
  void fetchSidebarData();
});

// Keep the sidebar's scope in sync with the route (the single source of
// truth). Navigating via the breadcrumb, a deep link or back/forward moves the
// project store; without mirroring it here the header slot would keep naming
// the scope last chosen *in the sidebar* over a grid showing another. Both
// halves follow, since the slot shows the scope the grid is in (#2159): a
// route that is not project-scoped (the Scrapheap, Duplicates) reads as Any
// project. The init block seeds this once on mount; this handles every later
// route change.
watch(
  () => [projectStore.projectViewMode, projectStore.selectedProjectId],
  ([mode, id]) => {
    if (_initializing) return;
    const nextMode = mode === "project" ? "project" : "global";
    const nextId = nextMode === "project" ? (id ?? null) : null;
    if (nextMode !== projectViewMode.value) projectViewMode.value = nextMode;
    if (nextId !== selectedProjectId.value) selectedProjectId.value = nextId;
  },
);

// Sync the sidebar's folder highlight with the active route.
// When App.vue navigates to /ref-folder/:id it passes
// the matching key via the activeFolderKey prop so we can switch to the
// folders tab and emit the correct filter payload.
//
// `?path=` is watched alongside the key, and it has to be: a subfolder click
// pushes a route that differs from its sibling's ONLY in the query, so the key
// alone cannot see the difference. Watching just the key made every one of
// those a history entry whose Back went nowhere - the address bar moved and the
// grid did not - which is worse than the previous behaviour, where the pushes
// were identical and vue-router created no Back target at all.
watch(
  () => [
    viewStore.activeFolderKey,
    viewStore.view?.folderFilter?.pathPrefix ?? null,
  ],
  async ([newKey, newPath], [oldKey] = []) => {
    if (!newKey) {
      // Route left a folder view - clear the sidebar's folder highlight.
      if (oldKey && selectedFolderRouteKey.value === oldKey) {
        clearFolderSelection();
      }
      return;
    }
    // Already showing exactly what the route names, folder AND subfolder. A
    // click gets here right after setting both, so without this every one of
    // them would re-fetch the listings and re-emit the payload it just sent.
    //
    // `_urlPath`, NOT `_samePath`: this compares the sidebar's path, which
    // `routeSubfolderUnder` has re-spelled into the folder's separators,
    // against `?path=`, which `parseFolderPath` keeps verbatim from the URL.
    //
    // This one folds unconditionally, where `routeSubfolderUnder` folds only
    // for a Windows root, and the asymmetry is deliberate. That function
    // decides whether a URL belongs to a folder AT ALL, so a false match
    // selects the wrong folder. This one only asks "did I just do this?", so a
    // false match can at worst skip re-restoring something already restored -
    // while a false MISS is the loop. Cheap direction, expensive direction.
    // With `_samePath` the two could never match once the re-spelling changed
    // anything, so the sidebar could not recognise its own work: it re-emitted,
    // the emit pushed the re-spelled path as a NEW url, and Back returned to
    // the original spelling and started the round again. A shared Windows link
    // could not be navigated away from.
    //
    // Both spellings of the sidebar's own path are offered, because both
    // spellings of `?path=` arrive (see `routeSubfolderUnder`):
    // `selectedFolderSubPath` is what the sidebar wrote into the URL itself,
    // `selectedFolderPath` is what an absolute link from before that carries.
    // Comparing only the absolute one would leave the sidebar unable to
    // recognise the very URL it had just pushed - the loop above, again.
    const showingSubfolder = selectedFolderKey.value?.startsWith("path-");
    const routePath = _urlPath(newPath);
    const mine = [selectedFolderSubPath.value, selectedFolderPath.value];
    if (
      selectedFolderRouteKey.value === newKey &&
      (newPath
        ? routePath !== "" && mine.some((p) => p && _urlPath(p) === routePath)
        : !showingSubfolder)
    ) {
      return;
    }

    await fetchReferenceFolders();

    // Guard: user may have navigated away - or to a sibling subfolder, which
    // moves only the query - while the fetches were in flight.
    if (viewStore.activeFolderKey !== newKey) return;
    if ((viewStore.view?.folderFilter?.pathPrefix ?? null) !== newPath) return;

    if (newKey.startsWith("rf-")) {
      const id = parseInt(newKey.slice(3), 10);
      const folder = referenceFolders.value.find((f) => f.id === id);
      if (folder) {
        // A subfolder in the URL wins over the folder root, on every way in:
        // a reload, a deep link, Back out of another view, and Back between
        // two subfolders of this same folder.
        const sub = routeSubfolderUnder(folder.folder);
        handleFolderNodeSelect(sub ? `path-${sub.pathPrefix}` : newKey, {
          referenceFolderId: folder.id,
          pathPrefix: sub ? sub.pathPrefix : folder.folder,
          label: sub ? sub.label : folder.label || folder.folder,
        });
      }
    }
  },
  { immediate: true },
);

async function toggleCharacterProjectMembership(charId) {
  const char = characters.value.find((c) => c.id === charId);
  if (!char || menuProjectId.value == null) return;
  const membershipPatch = toggleEntityProjectPatch(char, menuProjectId.value);
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
  if (!set || menuProjectId.value == null) return;
  const membershipPatch = toggleEntityProjectPatch(set, menuProjectId.value);
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
  chooseLibraryFolder,
  offerLoosePictures,
  startLocalImport,
  currentProjectId,
  openCurrentSelectionEditor,
  clearFind,
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
    @saved="openCreatedSet"
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
  />
  <FolderMappingWizard
    :open="mappingStore.wizardOpen"
    :resume="mappingStore.wizardResume"
    @close="mappingStore.closeWizard()"
    @committed="folderMappingWizardCommitted"
  />

  <aside
    ref="sidebarRootRef"
    class="sidebar sidebar-panel"
    :class="{
      'sidebar-docked': sidebarStore.effectiveDocked,
      'sidebar--narrow': sidebarIsNarrow,
    }"
    :style="sidebarThumbStyle"
  >
    <!-- Drag the right edge to resize the expanded sidebar (hidden when docked). -->
    <SidebarResizeHandle v-if="!sidebarStore.effectiveDocked" />
    <!-- The header slot (#2448): Find in library with the dock button beside
         it, expanded; docked, the rail's top cell holds Expand in the dock
         button's place, so the toggle never moves. -->
    <div v-if="sidebarStore.effectiveDocked" class="sidebar-rail-slot">
      <button
        type="button"
        class="sidebar-collapsed-item sidebar-rail-toggle"
        aria-label="Expand the sidebar"
        @click="sidebarStore.setSidebarDocked(false)"
      >
        <Tooltip text="Expand the sidebar" activator="parent" :describe="false" />
        <!-- A panel and a chevron: MDI has no such glyph. -->
        <svg class="sidebar-dock-glyph" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="2" /><path d="M9 4.5v15M13.2 9.5l2.5 2.5-2.5 2.5" /></svg>
      </button>
    </div>
    <div v-else class="sidebar-find-slot">
      <label class="sidebar-find-field" :class="{ typed: isFinding }">
        <v-icon size="16" aria-hidden="true">mdi-magnify</v-icon>
        <input
          ref="findInputRef"
          v-model="findQuery"
          type="search"
          class="sidebar-find-input"
          placeholder="Find in library"
          aria-label="Find in library: projects, people and sets by name"
          data-testid="sidebar-find"
          @keydown="onFindKeydown"
        />
        <button
          v-if="findQuery"
          type="button"
          class="sidebar-find-clear"
          aria-label="Clear Find"
          @click="findQuery = ''"
        >
          <v-icon size="14">mdi-close</v-icon>
        </button>
      </label>
      <button
        type="button"
        class="sidebar-slot-btn"
        aria-label="Dock the sidebar"
        @click="sidebarStore.setSidebarDocked(true)"
      >
        <Tooltip text="Dock the sidebar" activator="parent" :describe="false" />
        <!-- A panel and a chevron: MDI has no such glyph. -->
        <svg class="sidebar-dock-glyph" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="2" /><path d="M9 4.5v15M15.7 9.5L13.2 12l2.5 2.5" /></svg>
      </button>
    </div>
    <div
      class="sidebar-scroll"
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
          <!-- The places, then Review: every row the expanded list has above
               the switch, under the same conditions. -->
          <div
            v-if="!scopedResourceType"
            :class="['sidebar-collapsed-row', { active: isAllMediaRowActive }]"
          >
            <button
              type="button"
              :class="['sidebar-collapsed-item', { active: isAllMediaRowActive }]"
              :aria-current="isAllMediaRowActive ? 'page' : undefined"
              aria-label="All media"
              @click="selectAllMedia(false)"
              @contextmenu.prevent.stop="
                openSidebarCtxMenu('all-pictures', null, $event)
              "
            >
              <Tooltip text="All media" activator="parent" :describe="false" />
              <v-icon>mdi-image-multiple-outline</v-icon>
            </button>
          </div>
          <div
            v-if="!scopedResourceType && !isReadOnly"
            :class="['sidebar-collapsed-row', { active: isFavouritesRowActive }]"
          >
            <button
              type="button"
              :class="['sidebar-collapsed-item', { active: isFavouritesRowActive }]"
              :aria-current="isFavouritesRowActive ? 'page' : undefined"
              aria-label="Favourites"
              @click="selectAllMedia(true)"
            >
              <Tooltip text="Favourites" activator="parent" :describe="false" />
              <v-icon>mdi-heart-outline</v-icon>
            </button>
          </div>
          <div
            v-if="!isReadOnly"
            :class="['sidebar-collapsed-row', { active: isScrapheapRowActive }]"
          >
            <button
              type="button"
              :class="['sidebar-collapsed-item', { active: isScrapheapRowActive }]"
              :aria-current="isScrapheapRowActive ? 'page' : undefined"
              aria-label="Scrapheap"
              @click="selectCharacter(SCRAPHEAP_PICTURES_ID, 'Scrapheap')"
              @contextmenu.prevent.stop="
                openSidebarCtxMenu('scrapheap', null, $event)
              "
            >
              <Tooltip text="Scrapheap" activator="parent" :describe="false" />
              <v-icon>mdi-trash-can-outline</v-icon>
            </button>
          </div>
          <div class="sidebar-collapsed-divider"></div>

          <div
            v-if="!isReadOnly"
            :class="['sidebar-collapsed-row', { active: isInboxView }]"
          >
            <button
              type="button"
              :class="['sidebar-collapsed-item', { active: isInboxView }]"
              :aria-current="isInboxView ? 'page' : undefined"
              aria-label="Inbox"
              @click="emit('select-inbox')"
            >
              <Tooltip text="Inbox" activator="parent" :describe="false" />
              <v-icon>mdi-inbox-arrow-down-outline</v-icon>
            </button>
          </div>

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
              @click="isReadOnly || emit('select-duplicates', {})"
            >
              <Tooltip
                :text="isReadOnly ? READ_ONLY_DEDUP_HINT : 'Duplicates'"
                activator="parent"
              />
              <v-icon>mdi-content-duplicate</v-icon>
              <span
                v-if="dedupStore.hasDuplicates"
                class="sidebar-collapsed-dedup-badge"
              >
                <Tooltip
                  text="There are duplicates to review"
                  activator="parent"
                />
              </span>
            </button>
          </div>

          <div
            v-if="!isReadOnly"
            :class="['sidebar-collapsed-row', { active: isTagReviewView }]"
          >
            <button
              type="button"
              :class="['sidebar-collapsed-item', { active: isTagReviewView }]"
              :aria-current="
                isTagReviewView && reviewBoardShown ? 'page' : undefined
              "
              aria-label="Tags to review"
              @click="openTagReviewBoard"
            >
              <Tooltip text="Tags to review" activator="parent" :describe="false" />
              <v-icon>mdi-tag-check-outline</v-icon>
            </button>
          </div>

          <!-- Moves in the dock: reachable whenever the queue holds anything
               at all (movesStore.hasAnyPending), never shown-and-disabled the
               way Duplicates above is - see the
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
              class="sidebar-collapsed-item"
              :class="{ active: isMovesView }"
              :aria-current="isMovesView ? 'page' : undefined"
              aria-label="Moves made outside PixlStash"
              @click="emit('select-moves')"
            >
              <Tooltip
                :text="
                  movesStore.hasPending
                    ? `${movesStore.pendingCount} move(s) to review`
                    : 'Moves already followed, nothing to decide'
                "
                activator="parent"
              />
              <v-icon>mdi-folder-move-outline</v-icon>
              <span
                v-if="movesStore.hasPending"
                class="sidebar-collapsed-dedup-badge"
              >
                <Tooltip text="There are moves to review" activator="parent" />
              </span>
            </button>
          </div>


          <template v-if="showsTree">
            <div class="sidebar-collapsed-divider"></div>
            <!-- One boxed button flips Library / Folders. -->
            <div v-if="showsFolders" class="sidebar-collapsed-row">
              <button
                type="button"
                class="sidebar-collapsed-item sidebar-rail-mode"
                :aria-label="railModeLabel"
                @click="setTreeMode(treeMode === 'library' ? 'folders' : 'library')"
              >
                <Tooltip :text="railModeLabel" activator="parent" :describe="false" />
                <v-icon>{{
                  treeMode === "library" ? "mdi-bookshelf" : "mdi-folder-outline"
                }}</v-icon>
              </button>
            </div>
            <!-- One button per top-level node, each with a one-level flyout. -->
            <div
              v-for="node in railNodes"
              :key="node.key"
              :class="['sidebar-collapsed-row', { active: railNodeActive(node) }]"
            >
              <button
                type="button"
                :class="[
                  'sidebar-collapsed-item',
                  {
                    active: railNodeActive(node),
                    'sidebar-rail-node--open': railFlyout?.key === node.key,
                  },
                ]"
                :aria-label="node.label"
                :aria-haspopup="node.leaf ? undefined : 'menu'"
                :aria-expanded="node.leaf ? undefined : railFlyout?.key === node.key"
                @click.stop="openRailNode(node, $event)"
                @contextmenu.prevent.stop="
                  node.project
                    ? openSidebarCtxMenu('project', node.project, $event)
                    : openSidebarCtxMenu('empty', null, $event)
                "
              >
                <Tooltip :text="node.label" activator="parent" :describe="false" />
                <span
                  v-if="node.project"
                  class="sidebar-project-tile"
                  :style="{ '--tile-color': projectColor(node.project.id) }"
                  aria-hidden="true"
                  >{{ projectInitial(node.project) }}</span
                >
                <v-icon v-else>{{ node.icon }}</v-icon>
              </button>
            </div>
          </template>
          <Teleport to="body">
            <div
              v-if="railFlyout"
              ref="railFlyoutRef"
              class="ctx-menu sidebar-rail-flyout"
              role="menu"
              :aria-label="railFlyout.node.label"
              :style="{ top: railFlyout.top + 'px', left: railFlyout.left + 'px' }"
              @keydown="onRailFlyoutKeydown"
            >
              <!-- A rule above every group and a glyph on every row, so the
                   rows sit in under their heading and the groups read apart. -->
              <template v-for="(item, i) in railFlyout.items" :key="item.key">
                <div
                  v-if="item.sep || (item.section && i > 0)"
                  class="ctx-sep"
                  role="separator"
                ></div>
                <div
                  v-if="item.section"
                  class="section-label ctx-label"
                  role="presentation"
                >
                  {{ item.section }}
                </div>
                <button
                  v-else-if="!item.sep"
                  type="button"
                  :role="item.checked === undefined ? 'menuitem' : 'menuitemradio'"
                  :aria-checked="
                    item.checked === undefined ? undefined : String(item.checked)
                  "
                  tabindex="-1"
                  class="ctx-item"
                  @click="pickRailEntry(item.run)"
                >
                  <v-icon class="ctx-icon" aria-hidden="true">{{ item.icon }}</v-icon>
                  <span class="ctx-label-text">{{ item.label }}</span>
                  <v-icon v-if="item.checked" class="ctx-check">mdi-check</v-icon>
                </button>
              </template>
              <div
                v-if="!railFlyout.items.length"
                class="section-label ctx-label"
                role="presentation"
              >
                Nothing here yet
              </div>
            </div>
          </Teleport>
        </div>
      </template>
      <template v-else>
        <!-- One list in every scope (#2159): places, then Review, then the
             People / Sets sections, all on 32px rows. The project
             scope in the header slot narrows the grid, the counts and the
             People and Sets lists; it no longer swaps this list for a tree. -->
        <div
          class="sidebar-tab-panel sidebar-media-list sidebar-nav-list"
        >
          <template v-if="!scopedResourceType">
            <!-- A destination is a real <button>: keyboard reach and
                 Enter/Space activation come for free, and `aria-current`
                 is what tells a screen reader which one you are in. -->
            <button
              type="button"
              :class="['sidebar-list-item', { active: isAllMediaRowActive }]"
              :aria-current="isAllMediaRowActive ? 'page' : undefined"
              @click="selectAllMedia(false)"
              @contextmenu.prevent="
                openSidebarCtxMenu('all-pictures', null, $event)
              "
            >
              <span class="sidebar-list-icon"
                ><v-icon size="16">mdi-image-multiple-outline</v-icon></span
              >
              <span class="sidebar-list-label">All media</span>
              <span class="sidebar-list-count">{{
                categoryCounts[ALL_PICTURES_ID] ?? ""
              }}</span>
            </button>
            <!-- Favourites is the `favourite=1` filter (#2155) on All media,
                 not a container: the filter chip still says so, and the
                 filter menu's box is the same switch. Owner only, as that box
                 is: a share token's view of the hearts is not its own. -->
            <button
              v-if="!isReadOnly"
              type="button"
              :class="['sidebar-list-item', { active: isFavouritesRowActive }]"
              :aria-current="isFavouritesRowActive ? 'page' : undefined"
              @click="selectAllMedia(true)"
            >
              <span class="sidebar-list-icon"
                ><v-icon size="16">mdi-heart-outline</v-icon></span
              >
              <span class="sidebar-list-label">Favourites</span>
              <span class="sidebar-list-count">{{
                favouriteCount ?? ""
              }}</span>
            </button>
          </template>
          <button
            v-if="!isReadOnly"
            type="button"
            :class="['sidebar-list-item', { active: isScrapheapRowActive }]"
            :aria-current="isScrapheapRowActive ? 'page' : undefined"
            @click="selectCharacter(SCRAPHEAP_PICTURES_ID, 'Scrapheap')"
            @contextmenu.prevent="openSidebarCtxMenu('scrapheap', null, $event)"
          >
            <span class="sidebar-list-icon"
              ><v-icon size="16">mdi-trash-can-outline</v-icon></span
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

          <!-- Review: the queues whose number goes down as you work. -->
          <div class="sidebar-section-header" role="presentation">
            <span class="sidebar-section-title">Review</span>
          </div>
          <!-- The Inbox: added within the chosen period, with no person and
               no set. The count follows the period the toolbar picks; caught
               up, it stays with a 0 as the other Review rows do. -->
          <button
            v-if="!isReadOnly"
            type="button"
            :class="['sidebar-list-item', { active: isInboxView }]"
            :aria-current="isInboxView ? 'page' : undefined"
            @click="emit('select-inbox')"
          >
            <span class="sidebar-list-icon"
              ><v-icon size="16">mdi-inbox-arrow-down-outline</v-icon></span
            >
            <span class="sidebar-list-label">Inbox</span>
            <span class="sidebar-list-count" data-testid="inbox-count">{{
              inboxStore.count?.toLocaleString() ?? ""
            }}</span>
          </button>
          <!-- `aria-disabled`, not `disabled`: a read-only session must still
               be able to focus the row and read the title that explains why
               it is inert. The click guard stays. -->
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
            @click="isReadOnly || emit('select-duplicates', {})"
          >
            <Tooltip
              :text="isReadOnly ? READ_ONLY_DEDUP_HINT : ''"
              activator="parent"
            />
            <span class="sidebar-list-icon"
              ><v-icon size="16">mdi-content-duplicate</v-icon></span
            >
            <span class="sidebar-list-label">Duplicates</span>
            <span v-if="dedupStore.isScanning" class="sidebar-dedup-scanning">
              <Tooltip
                text="Still looking for duplicates. Groups appear as they are found."
                activator="parent"
              />
              <v-progress-circular indeterminate size="10" width="1.5" />
            </span>
            <!-- A presence DOT, not a count (owner call, 2026-07-29): the
                 group count moves with the tier gate and the threshold, so it
                 read as churn. The dot only says "there are duplicates to
                 review"; the queue's own header carries the numbers. -->
            <span v-if="dedupStore.hasDuplicates" class="sidebar-dedup-dot">
              <Tooltip text="There are duplicates to review" activator="parent" />
            </span>
          </button>
          <!-- Tags to review: a view like Duplicates, so it lights while it is
               up. Its open reviews nest under it, each lit while it is the one
               on screen, then "New review…". The list is the view's own data,
               so the children show while the view is open. -->
          <button
            v-if="!isReadOnly"
            type="button"
            :class="['sidebar-list-item', { active: isTagReviewView }]"
            :aria-current="
              isTagReviewView && reviewBoardShown ? 'page' : undefined
            "
            @click="openTagReviewBoard"
          >
            <span class="sidebar-list-icon"
              ><v-icon size="16">mdi-tag-check-outline</v-icon></span
            >
            <span class="sidebar-list-label">Tags to review</span>
          </button>
          <template v-if="!isReadOnly && isTagReviewView">
            <button
              v-for="review in reviewSessionsStore.sessions"
              :key="review.id"
              type="button"
              :class="[
                'sidebar-list-item',
                'sidebar-review-session',
                { active: isReviewSessionShown(review.id) },
              ]"
              :aria-current="
                isReviewSessionShown(review.id) ? 'page' : undefined
              "
              :aria-label="`Review “${review.tag}”, ${reviewProgressText(review)} decided`"
              @click="reviewSessionsStore.openSession(review.id)"
            >
              <span class="sidebar-list-icon"
                ><v-icon size="14">mdi-tag-outline</v-icon></span
              >
              <span class="sidebar-list-label">
                <span class="sidebar-list-label-text">{{ review.tag }}</span>
              </span>
              <span class="sidebar-review-progress" aria-hidden="true"
                ><span
                  class="sidebar-review-progress-fill"
                  :style="{ width: `${reviewProgressPct(review)}%` }"
                ></span
              ></span>
              <span class="sidebar-list-count sidebar-review-session-count">{{
                reviewProgressText(review)
              }}</span>
            </button>
            <button
              type="button"
              class="sidebar-list-item sidebar-review-session sidebar-review-new"
              @click="reviewSessionsStore.newReviewTick++"
            >
              <span class="sidebar-list-icon"
                ><v-icon size="14">mdi-plus</v-icon></span
              >
              <span class="sidebar-list-label">New review…</span>
            </button>
          </template>
          <!-- Moves: a to-do queue, not a permanent destination, so it earns
               its row while the queue holds anything at all - including an
               off_layout-only backlog, which has nothing to decide but still
               has to be reachable before its retention window expires it
               unseen (see hasAnyPending's own comment). The count only ever
               names what needs a DECISION; an off_layout-only queue shows the
               row with no number, not a "0" that reads as empty. -->
          <button
            v-if="movesStore.hasAnyPending"
            type="button"
            :class="['sidebar-list-item', { active: isMovesView }]"
            :aria-current="isMovesView ? 'page' : undefined"
            @click="emit('select-moves')"
          >
            <span class="sidebar-list-icon"
              ><v-icon size="16">mdi-folder-move-outline</v-icon></span
            >
            <span class="sidebar-list-label">Moves</span>
            <span v-if="movesStore.hasPending" class="sidebar-list-count">{{
              movesStore.pendingCount
            }}</span>
          </button>

          <!-- Below Review, one switch chooses how the rest is drawn (#2448):
               Library is PixlStash's own structure, Folders the tree as the
               default layout files it on disk. The design system's Segmented:
               a radiogroup, since it sets a value rather than switching
               panels. -->
          <div v-if="showsFolders" class="sidebar-tree-switch">
            <Segmented
              :options="TREE_MODE_OPTIONS"
              :model-value="treeMode"
              variant="icon-label"
              full
              aria-label="Show the library as"
              @update:model-value="setTreeMode"
            />
          </div>
          <div
            v-if="isFinding && findMatches.length"
            class="sidebar-find-line"
            aria-live="polite"
          >
            {{ matchCountLabel(findMatches.length) }} ·
            <kbd class="sidebar-kbd">Enter</kbd> opens the first ·
            <kbd class="sidebar-kbd">Esc</kbd> clears
          </div>
          <div v-else-if="isFinding" class="sidebar-find-none" aria-live="polite">
            <p>No project, person or set is called “{{ findQuery.trim() }}”.</p>
            <AppButton icon-left="magnify" @click="searchPicturesForFind"
              >Search pictures for “{{ findQuery.trim() }}”</AppButton
            >
          </div>
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

          <!-- Library (#2448): the project dropdown that shipped in the header
               slot (#2159), moved under the switch, over the People and Sets
               it narrows. A project token has one project to see, so no
               dropdown; a person or set token has no switch either. -->
          <template v-if="!foldersMode">
            <div
              v-for="branch in libraryBranches"
              :key="branch.key"
              :class="['sidebar-branch', { 'sidebar-branch--headed': showsScopeSlot }]"
              :style="scopeWashStyle"
            >
              <!-- The dropdown is the box's top band, so it plainly owns the
                   rows under it. -->
              <SidebarScopeSlot
                v-if="showsScopeSlot"
                :projects="sortedProjects"
                :mode="projectViewMode"
                :project-id="selectedProjectId"
                :counts="scopeCounts"
                :read-only="isReadOnly"
                @choose="chooseScope"
                @new-project="createProject"
                @edit-project="openProjectEditor"
                @project-menu="(p, e) => openSidebarCtxMenu('project', p, e)"
              />
              <!-- The whole scope as a row: the view choosing a project lands
                   on, lit while it is up, and the way back to it from a person
                   or set. Any project has none; All media is the library. -->
              <button
                v-if="libraryScope !== undefined && !isFinding"
                type="button"
                :class="[
                  'sidebar-list-item',
                  'sidebar-all-of-scope',
                  {
                    active: isAllOfScopeRowActive,
                    droppable: dragOverScopeRow && !dropRejected,
                    'not-droppable': dragOverScopeRow && dropRejected,
                  },
                ]"
                :aria-current="isAllOfScopeRowActive ? 'page' : undefined"
                data-testid="sidebar-all-of-scope"
                @click="openProjectBranch(libraryScope)"
                @contextmenu.prevent="
                  scopedProjectObj
                    ? openSidebarCtxMenu('project', scopedProjectObj, $event)
                    : openSidebarCtxMenu('empty', null, $event)
                "
                @dragover="dragOverScopeItem($event)"
                @dragleave="dragLeaveScopeItem($event)"
                @drop.prevent="handleDropOnScope($event)"
              >
                <span class="sidebar-list-icon">
                  <span
                    v-if="libraryScope !== null"
                    class="sidebar-project-tile sidebar-project-tile--mark"
                    :style="{ '--tile-color': projectColor(libraryScope) }"
                    aria-hidden="true"
                  />
                  <v-icon v-else size="16">mdi-folder-outline</v-icon>
                </span>
                <span class="sidebar-list-label">
                  <span class="sidebar-list-label-text">{{ allOfScopeLabel }}</span>
                </span>
                <span class="sidebar-list-count">{{
                  (libraryScope === null
                    ? scopeCounts.none
                    : scopeCounts.byId?.[libraryScope]) ?? ""
                }}</span>
              </button>
              <div>
              <div
                v-if="
                  scopedResourceType !== 'picture_set' &&
                  (!isFinding || branch.characters.length)
                "
                class="sidebar-section-block"
              >
                <div
                  class="sidebar-section-header sidebar-section-header--collapsible"
                  @click="isFinding || (peopleCollapsed = peopleOpen)"
                  @contextmenu.prevent.stop="
                    openSidebarCtxMenu('header', 'people', $event)
                  "
                >
                  <button
                    type="button"
                    class="sidebar-tree-toggle sidebar-section-toggle"
                    :aria-expanded="String(peopleOpen)"
                    :disabled="isFinding"
                    aria-controls="sidebar-section-people"
                    :aria-label="`${peopleOpen ? 'Collapse' : 'Expand'} People`"
                    @click.stop="isFinding || (peopleCollapsed = peopleOpen)"
                  >
                    <v-icon size="16">{{
                      peopleOpen ? "mdi-chevron-down" : "mdi-chevron-right"
                    }}</v-icon>
                  </button>
                  <span class="sidebar-section-title">People</span>
                  <span
                    v-if="!isReadOnly"
                    class="sidebar-header-actions sidebar-move-to-project-wrap"
                  >
                    <button
                      type="button"
                      class="sidebar-section-add"
                      :aria-label="sectionAddLabel('people', branch.addProjectId)"
                      :aria-haspopup="branch.addProjectId != null ? 'menu' : undefined"
                      :aria-expanded="
                        branch.addProjectId != null ? characterMoveMenuOpen : undefined
                      "
                      @click.stop="
                        addFromSectionHead('people', branch.addProjectId, $event)
                      "
                    >
                      <Tooltip
                        :text="sectionAddLabel('people', branch.addProjectId)"
                        activator="parent"
                        :describe="false"
                      />
                      <v-icon size="16">mdi-plus</v-icon>
                    </button>
                  </span>
                </div>
                <div
                  v-show="peopleOpen"
                  id="sidebar-section-people"
                  class="sidebar-section-scroll"
                >
                  <div
                    v-if="!isFinding && branch.characters.length === 0"
                    class="sidebar-collections-help-row"
                  >
                    <span class="sidebar-collections-help"
                      >Click the + button to add one.</span
                    >
                  </div>
                  <div
                    v-for="char in branch.characters"
                    :key="char.id"
                    :class="[
                      'sidebar-character-group',
                      {
                        'sidebar-tree-group':
                          branch.childCount(char.id) &&
                          isTreeOpen(`c:${char.id}`),
                      },
                    ]"
                  >
                    <div
                      :class="[
                        'sidebar-list-item',
                        {
                          active:
                            (selectedCharacterIdSet.size > 0
                              ? selectedCharacterIdSet.has(char.id)
                              : selectionStore.selectedCharacter === char.id) &&
                            selectionOwnsHighlight &&
                            branchIsScope(branch.projectId, char),
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
                        selectionOwnsHighlight &&
                        branchIsScope(branch.projectId, char)
                          ? 'true'
                          : 'false'
                      "
                      @click="
                        selectCharacter(char.id, char.name || 'Character', $event)
                      "
                      :aria-expanded="
                        branch.childCount(char.id)
                          ? String(isTreeOpen(`c:${char.id}`))
                          : undefined
                      "
                      @keydown="
                        onTreeRowKeydown(
                          `c:${char.id}`,
                          branch.childCount(char.id),
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
                      <Tooltip
                        :text="`${char.name || 'Character'} (Ctrl/Cmd + click to multi-select)`"
                        activator="parent"
                      />
                      <button
                        v-if="branch.childCount(char.id)"
                        type="button"
                        class="sidebar-tree-toggle"
                        tabindex="-1"
                        :aria-label="`${isTreeOpen(`c:${char.id}`) ? 'Hide' : 'Show'} the sets in ${char.name}`"
                        @click.stop="toggleTreeKey(`c:${char.id}`)"
                      >
                        <v-icon size="16">{{
                          isTreeOpen(`c:${char.id}`)
                            ? "mdi-chevron-down"
                            : "mdi-chevron-right"
                        }}</v-icon>
                      </button>
                      <span
                        v-if="
                          branch.childCount(char.id) && isTreeOpen(`c:${char.id}`)
                        "
                        class="sidebar-tree-stub"
                        :style="{ '--tree-stub-x': treeGuideX(1) }"
                        aria-hidden="true"
                      />
                      <span class="sidebar-list-icon">
                        <img
                          :src="
                            characterThumbnails[char.id]
                              ? characterThumbnails[char.id]
                              : unknownPerson
                          "
                          alt=""
                          width="20"
                          height="20"
                          class="sidebar-character-thumb"
                        />
                      </span>
                      <span class="sidebar-list-label">
                        <Tooltip
                          location="top"
                          :disabled="!labelNeedsTooltip(`char-${char.id}`)"
                          :describe="false"
                        >
                          <template #activator="{ props }">
                            <span
                              v-bind="
                                withRef(props, (el) =>
                                  registerLabelRef(`char-${char.id}`, el),
                                )
                              "
                              class="sidebar-list-label-text"
                              ><template v-for="part in [findParts(char.name.charAt(0).toUpperCase() + char.name.slice(1))]" :key="0"
                            >{{ part.before
                            }}<mark v-if="part.match" class="sidebar-find-mark">{{
                              part.match
                            }}</mark>{{ part.after }}</template
                          ></span>
                          </template>
                          <span>{{ char.name }}</span>
                        </Tooltip>
                        <span
                          v-if="branch.childCount(char.id)"
                          class="sidebar-tree-sub"
                          >{{ childSetsLabel(branch.childCount(char.id)) }}</span
                        >
                      </span>
                      <span class="sidebar-character-actions">
                        <Tooltip
                          v-if="sharedCharacterIds.has(char.id)"
                          text="Has active share links"
                        >
                          <template #activator="{ props: tipProps }">
                            <v-icon
                              v-bind="tipProps"
                              class="sidebar-shared-icon"
                              size="11"
                              >mdi-link-variant</v-icon
                            >
                          </template>
                        </Tooltip>
                        <span class="sidebar-list-count">
                          <span v-if="isCountNew(char.id)" class="sidebar-new-tag"
                            >new</span
                          >
                          <span>{{ personCountIn(branch.countScope, char) }}</span>
                        </span>
                      </span>
                    </div>
                    <div
                      v-for="row in branch.rowsByCharacter.get(Number(char.id)) ?? []"
                      :key="`tree-${row.set.id}`"
                      :class="[
                        'sidebar-list-item',
                        'sidebar-set-item',
                        {
                          'sidebar-tree-kid': row.level > 0,
                          'sidebar-tree-kid--last': row.level > 0 && row.last,
                          'sidebar-tree-kid--parent':
                            row.level > 0 && row.childCount > 0,
                          active:
                            selectedSetIdSet.has(row.set.id) &&
                            selectionOwnsHighlight &&
                            branchIsScope(branch.projectId, row.set),
                          droppable: dragOverSet === row.set.id && !dropRejected,
                          'not-droppable':
                            dragOverSet === row.set.id && dropRejected,
                        },
                      ]"
                      :style="{
                        '--tree-x': treeGuideX(row.level),
                        '--tree-spacer': treeSpacerWidth(row.level),
                      }"
                      :ref="(el) => registerSetRef(row.set.id, el)"
                      role="button"
                      tabindex="0"
                      :aria-pressed="
                        selectedSetIdSet.has(row.set.id) &&
                        selectionOwnsHighlight &&
                        branchIsScope(branch.projectId, row.set)
                          ? 'true'
                          : 'false'
                      "
                      :aria-expanded="row.childCount ? String(row.expanded) : undefined"
                      @click="
                        selectSet(row.set.id, row.set.name || 'Picture Set', $event)
                      "
                      @keydown="
                        onTreeRowKeydown(`s:${row.set.id}`, row.childCount, $event)
                      "
                      @contextmenu.prevent="
                        openSidebarCtxMenu('set', row.set, $event)
                      "
                      @dragover="dragOverSetItem(row.set.id, $event)"
                      @dragleave="dragLeaveSetItem($event)"
                      @drop.prevent="handleDropOnSet(row.set.id, $event)"
                    >
                      <Tooltip
                        :text="`${row.set.name || 'Picture Set'} (Ctrl/Cmd + click to multi-select)`"
                        activator="parent"
                      />
                      <button
                        v-if="row.childCount"
                        type="button"
                        class="sidebar-tree-toggle"
                        tabindex="-1"
                        :aria-label="`${row.expanded ? 'Hide' : 'Show'} the sets in ${row.set.name}`"
                        @click.stop="toggleTreeKey(`s:${row.set.id}`)"
                      >
                        <v-icon size="16">{{
                          row.expanded ? "mdi-chevron-down" : "mdi-chevron-right"
                        }}</v-icon>
                      </button>
                      <span
                        v-if="row.level > 0"
                        class="sidebar-tree-spacer"
                        aria-hidden="true"
                      >
                        <span
                          v-for="level in row.rails"
                          :key="level"
                          class="sidebar-tree-rail"
                          :style="{ '--tree-x': treeGuideX(level) }"
                        />
                      </span>
                      <span
                        v-if="row.expanded"
                        :class="[
                          'sidebar-tree-stub',
                          { 'sidebar-tree-stub--kid': row.level > 0 },
                        ]"
                        :style="{ '--tree-stub-x': treeGuideX(row.level + 1) }"
                        aria-hidden="true"
                      />
                      <span
                        :class="[
                          'sidebar-list-icon',
                          { 'sidebar-tree-mark': row.level > 0 },
                        ]"
                      >
                        <v-icon
                          v-if="row.set.set_icon && row.set.set_icon !== ICON_CARDS"
                          :size="treeMarkPx(row.level) - 4"
                          :color="row.set.set_color || undefined"
                          >{{ row.set.set_icon }}</v-icon
                        >
                        <img
                          v-else-if="hasSetThumbnail(row.set)"
                          :src="getSetThumbnail(row.set.id)"
                          alt=""
                          class="sidebar-set-thumb-image"
                          :width="treeMarkPx(row.level)"
                          :height="treeMarkPx(row.level)"
                          :style="
                            row.set.set_color
                              ? {
                                  filter: row.level
                                    ? `drop-shadow(0 0 2px ${row.set.set_color}) drop-shadow(0 0 1px ${row.set.set_color})`
                                    : `drop-shadow(0 0 3px ${row.set.set_color}) drop-shadow(0 0 8px ${row.set.set_color})`,
                                }
                              : {}
                          "
                          @load="handleSetThumbnailLoad(row.set.id)"
                          @error="handleSetThumbnailError(row.set.id)"
                        />
                        <v-icon v-else :size="treeMarkPx(row.level) - 4"
                          >mdi-image-album</v-icon
                        >
                      </span>
                      <span class="sidebar-list-label">
                        <Tooltip
                          location="top"
                          :disabled="!labelNeedsTooltip(`set-${row.set.id}`)"
                          :describe="false"
                        >
                          <template #activator="{ props }">
                            <span
                              v-bind="
                                withRef(props, (el) =>
                                  registerLabelRef(`set-${row.set.id}`, el),
                                )
                              "
                              class="sidebar-list-label-text"
                              ><template v-for="part in [findParts(row.set.name)]" :key="0"
                            >{{ part.before
                            }}<mark v-if="part.match" class="sidebar-find-mark">{{
                              part.match
                            }}</mark>{{ part.after }}</template
                          ></span>
                          </template>
                          <span>{{ row.set.name }}</span>
                        </Tooltip>
                        <span v-if="row.childCount" class="sidebar-tree-sub">{{
                          childSetsLabel(row.childCount)
                        }}</span>
                      </span>
                      <Tooltip v-if="row.set.locked" :text="SET_LOCKED_ROW_TITLE">
                        <template #activator="{ props: tipProps }">
                          <v-icon
                            v-bind="tipProps"
                            class="sidebar-lock-icon"
                            size="11"
                            >mdi-lock-outline</v-icon
                          >
                        </template>
                      </Tooltip>
                      <Tooltip
                        v-if="sharedSetIds.has(row.set.id)"
                        text="Has active share links"
                      >
                        <template #activator="{ props: tipProps }">
                          <v-icon
                            v-bind="tipProps"
                            class="sidebar-shared-icon"
                            size="11"
                            >mdi-link-variant</v-icon
                          >
                        </template>
                      </Tooltip>
                      <span class="sidebar-list-count">{{
                        row.set.picture_count ?? 0
                      }}</span>
                    </div>
                  </div>
                </div>
              </div>

              <div
                v-if="
                  scopedResourceType !== 'character' &&
                  (!isFinding || branch.sets.length)
                "
                class="sidebar-section-block"
              >
                <div
                  class="sidebar-section-header sidebar-section-header--collapsible"
                  @click="isFinding || (setsCollapsed = setsOpen)"
                  @contextmenu.prevent.stop="
                    openSidebarCtxMenu('header', 'sets', $event)
                  "
                >
                  <button
                    type="button"
                    class="sidebar-tree-toggle sidebar-section-toggle"
                    :aria-expanded="String(setsOpen)"
                    :disabled="isFinding"
                    aria-controls="sidebar-section-sets"
                    :aria-label="`${setsOpen ? 'Collapse' : 'Expand'} Sets`"
                    @click.stop="isFinding || (setsCollapsed = setsOpen)"
                  >
                    <v-icon size="16">{{
                      setsOpen ? "mdi-chevron-down" : "mdi-chevron-right"
                    }}</v-icon>
                  </button>
                  <span class="sidebar-section-title">Sets</span>
                  <span
                    v-if="!isReadOnly"
                    class="sidebar-header-actions sidebar-move-to-project-wrap"
                  >
                    <button
                      type="button"
                      class="sidebar-section-add"
                      :aria-label="sectionAddLabel('sets', branch.addProjectId)"
                      :aria-haspopup="branch.addProjectId != null ? 'menu' : undefined"
                      :aria-expanded="
                        branch.addProjectId != null ? setMoveMenuOpen : undefined
                      "
                      @click.stop="addFromSectionHead('sets', branch.addProjectId, $event)"
                    >
                      <Tooltip
                        :text="sectionAddLabel('sets', branch.addProjectId)"
                        activator="parent"
                        :describe="false"
                      />
                      <v-icon size="16">mdi-plus</v-icon>
                    </button>
                  </span>
                </div>
                <div
                  v-show="setsOpen"
                  id="sidebar-section-sets"
                  class="sidebar-section-scroll"
                >
                  <div
                    v-if="!isFinding && branch.sets.length === 0"
                    class="sidebar-collections-help-row"
                  >
                    <span class="sidebar-collections-help"
                      >Click the + button to add one.</span
                    >
                  </div>
                  <div
                    v-for="group in branch.setSections"
                    :key="group.root.id"
                    :class="[
                      { 'sidebar-tree-group': group.open },
                    ]"
                  >
                    <div
                      v-for="row in group.rows"
                      :key="`tree-${row.set.id}`"
                      :class="[
                        'sidebar-list-item',
                        'sidebar-set-item',
                        {
                          'sidebar-tree-kid': row.level > 0,
                          'sidebar-tree-kid--last': row.level > 0 && row.last,
                          'sidebar-tree-kid--parent':
                            row.level > 0 && row.childCount > 0,
                          active:
                            selectedSetIdSet.has(row.set.id) &&
                            selectionOwnsHighlight &&
                            branchIsScope(branch.projectId, row.set),
                          droppable: dragOverSet === row.set.id && !dropRejected,
                          'not-droppable':
                            dragOverSet === row.set.id && dropRejected,
                        },
                      ]"
                      :style="{
                        '--tree-x': treeGuideX(row.level),
                        '--tree-spacer': treeSpacerWidth(row.level),
                      }"
                      :ref="(el) => registerSetRef(row.set.id, el)"
                      role="button"
                      tabindex="0"
                      :aria-pressed="
                        selectedSetIdSet.has(row.set.id) &&
                        selectionOwnsHighlight &&
                        branchIsScope(branch.projectId, row.set)
                          ? 'true'
                          : 'false'
                      "
                      :aria-expanded="row.childCount ? String(row.expanded) : undefined"
                      @click="
                        selectSet(row.set.id, row.set.name || 'Picture Set', $event)
                      "
                      @keydown="
                        onTreeRowKeydown(`s:${row.set.id}`, row.childCount, $event)
                      "
                      @contextmenu.prevent="
                        openSidebarCtxMenu('set', row.set, $event)
                      "
                      @dragover="dragOverSetItem(row.set.id, $event)"
                      @dragleave="dragLeaveSetItem($event)"
                      @drop.prevent="handleDropOnSet(row.set.id, $event)"
                    >
                      <Tooltip
                        :text="`${row.set.name || 'Picture Set'} (Ctrl/Cmd + click to multi-select)`"
                        activator="parent"
                      />
                      <button
                        v-if="row.childCount"
                        type="button"
                        class="sidebar-tree-toggle"
                        tabindex="-1"
                        :aria-label="`${row.expanded ? 'Hide' : 'Show'} the sets in ${row.set.name}`"
                        @click.stop="toggleTreeKey(`s:${row.set.id}`)"
                      >
                        <v-icon size="16">{{
                          row.expanded ? "mdi-chevron-down" : "mdi-chevron-right"
                        }}</v-icon>
                      </button>
                      <span
                        v-if="row.level > 0"
                        class="sidebar-tree-spacer"
                        aria-hidden="true"
                      >
                        <span
                          v-for="level in row.rails"
                          :key="level"
                          class="sidebar-tree-rail"
                          :style="{ '--tree-x': treeGuideX(level) }"
                        />
                      </span>
                      <span
                        v-if="row.expanded"
                        :class="[
                          'sidebar-tree-stub',
                          { 'sidebar-tree-stub--kid': row.level > 0 },
                        ]"
                        :style="{ '--tree-stub-x': treeGuideX(row.level + 1) }"
                        aria-hidden="true"
                      />
                      <span
                        :class="[
                          'sidebar-list-icon',
                          { 'sidebar-tree-mark': row.level > 0 },
                        ]"
                      >
                        <v-icon
                          v-if="row.set.set_icon && row.set.set_icon !== ICON_CARDS"
                          :size="treeMarkPx(row.level) - 4"
                          :color="row.set.set_color || undefined"
                          >{{ row.set.set_icon }}</v-icon
                        >
                        <img
                          v-else-if="hasSetThumbnail(row.set)"
                          :src="getSetThumbnail(row.set.id)"
                          alt=""
                          class="sidebar-set-thumb-image"
                          :width="treeMarkPx(row.level)"
                          :height="treeMarkPx(row.level)"
                          :style="
                            row.set.set_color
                              ? {
                                  filter: row.level
                                    ? `drop-shadow(0 0 2px ${row.set.set_color}) drop-shadow(0 0 1px ${row.set.set_color})`
                                    : `drop-shadow(0 0 3px ${row.set.set_color}) drop-shadow(0 0 8px ${row.set.set_color})`,
                                }
                              : {}
                          "
                          @load="handleSetThumbnailLoad(row.set.id)"
                          @error="handleSetThumbnailError(row.set.id)"
                        />
                        <v-icon v-else :size="treeMarkPx(row.level) - 4"
                          >mdi-image-album</v-icon
                        >
                      </span>
                      <span class="sidebar-list-label">
                        <Tooltip
                          location="top"
                          :disabled="!labelNeedsTooltip(`set-${row.set.id}`)"
                          :describe="false"
                        >
                          <template #activator="{ props }">
                            <span
                              v-bind="
                                withRef(props, (el) =>
                                  registerLabelRef(`set-${row.set.id}`, el),
                                )
                              "
                              class="sidebar-list-label-text"
                              ><template v-for="part in [findParts(row.set.name)]" :key="0"
                            >{{ part.before
                            }}<mark v-if="part.match" class="sidebar-find-mark">{{
                              part.match
                            }}</mark>{{ part.after }}</template
                          ></span>
                          </template>
                          <span>{{ row.set.name }}</span>
                        </Tooltip>
                        <span v-if="row.childCount" class="sidebar-tree-sub">{{
                          childSetsLabel(row.childCount)
                        }}</span>
                      </span>
                      <Tooltip v-if="row.set.locked" :text="SET_LOCKED_ROW_TITLE">
                        <template #activator="{ props: tipProps }">
                          <v-icon
                            v-bind="tipProps"
                            class="sidebar-lock-icon"
                            size="11"
                            >mdi-lock-outline</v-icon
                          >
                        </template>
                      </Tooltip>
                      <Tooltip
                        v-if="sharedSetIds.has(row.set.id)"
                        text="Has active share links"
                      >
                        <template #activator="{ props: tipProps }">
                          <v-icon
                            v-bind="tipProps"
                            class="sidebar-shared-icon"
                            size="11"
                            >mdi-link-variant</v-icon
                          >
                        </template>
                      </Tooltip>
                      <span class="sidebar-list-count">{{
                        row.set.picture_count ?? 0
                      }}</span>
                    </div>
                  </div>
                </div>
              </div>
                <!-- A project's attached files, in the branch of the project
                     the grid is in. -->
                <div
                  v-if="
                    scopedProjectObj &&
                    (!isReadOnly || sessionContext?.include_attachments)
                  "
                  class="sidebar-project-tree-files"
                >
                  <ProjectFiles :projectId="scopedProjectObj.id" compact />
                </div>
              </div>
            </div>
          </template>

          <!-- Folders (#2458): the folders pictures are filed in, nested as
               on disk. Read-only about folders; a level the layout reads as
               a person or set keeps its menu and takes drops. -->
          <template v-else>
            <div
              v-for="row in folderRows"
              :key="row.key"
              :class="[
                'sidebar-list-item',
                'sidebar-folder-row',
                {
                  'sidebar-folder-kid': row.depth > 0,
                  'sidebar-folder-kid--last': row.depth > 0 && row.last,
                  active: folderRowActive(row),
                  'sidebar-row--dim': row.dim,
                  droppable:
                    !dropRejected &&
                    ((row.kind === 'character' && dragOverCharacter === row.entity.id) ||
                      (row.kind === 'set' && dragOverSet === row.entity.id)),
                  'not-droppable':
                    dropRejected &&
                    ((row.kind === 'character' && dragOverCharacter === row.entity.id) ||
                      (row.kind === 'set' && dragOverSet === row.entity.id)),
                },
              ]"
              :style="{ ...folderWashStyle(row), '--depth': row.depth }"
              role="button"
              tabindex="0"
              :data-testid="`sidebar-folder-${row.kind}`"
              :aria-expanded="row.childCount ? String(row.open) : undefined"
              :aria-current="folderRowActive(row) ? 'page' : undefined"
              @click="openFolderRow(row, $event)"
              @keydown="onTreeRowKeydown(row.key, row.childCount, $event)"
              @contextmenu.prevent="openFolderRowMenu(row, $event)"
              @dragover="
                row.kind === 'character'
                  ? handleDragOverCharacter(row.entity.id, $event)
                  : row.kind === 'set'
                    ? dragOverSetItem(row.entity.id, $event)
                    : undefined
              "
              @dragleave="
                row.kind === 'character'
                  ? handleDragLeaveCharacter($event)
                  : row.kind === 'set'
                    ? dragLeaveSetItem($event)
                    : undefined
              "
              @drop.prevent="
                row.kind === 'character'
                  ? handleDropOnCharacter({ characterId: row.entity.id, event: $event })
                  : row.kind === 'set'
                    ? handleDropOnSet(row.entity.id, $event)
                    : undefined
              "
            >
              <span
                v-for="level in row.rails"
                :key="level"
                class="sidebar-folder-rail"
                :style="{ '--rail-depth': level }"
                aria-hidden="true"
              />
              <span
                :class="[
                  'ps-row-glyph',
                  { 'ps-row-glyph--empty': !row.childCount },
                ]"
              >
                <button
                  v-if="row.childCount"
                  type="button"
                  class="sidebar-branch-toggle"
                  tabindex="-1"
                  :aria-label="`${row.open ? 'Close' : 'Open'} ${row.name}`"
                  @click.stop="toggleTreeKey(row.key)"
                >
                  <v-icon size="16">{{
                    row.open ? "mdi-chevron-down" : "mdi-chevron-right"
                  }}</v-icon>
                </button>
              </span>
              <span class="sidebar-list-icon">
                <v-icon
                  v-if="row.kind === 'project'"
                  size="16"
                  :color="projectColor(row.projectId)"
                  >{{ row.open ? "mdi-folder-open" : "mdi-folder" }}</v-icon
                >
                <v-icon v-else-if="row.kind === 'global'" size="16">{{
                  row.open ? "mdi-folder-open-outline" : "mdi-folder-outline"
                }}</v-icon>
                <v-icon v-else-if="row.kind === 'unassigned'" size="16"
                  >mdi-folder-question-outline</v-icon
                >
                <v-icon v-else-if="row.kind === 'folder'" size="16">{{
                  row.open ? "mdi-folder-open-outline" : "mdi-folder-outline"
                }}</v-icon>
                <img
                  v-else-if="row.kind === 'character'"
                  :src="characterThumbnails[row.entity.id] || unknownPerson"
                  alt=""
                  width="20"
                  height="20"
                  class="sidebar-character-thumb"
                />
                <v-icon
                  v-else-if="row.entity.set_icon && row.entity.set_icon !== ICON_CARDS"
                  size="16"
                  :color="row.entity.set_color || undefined"
                  >{{ row.entity.set_icon }}</v-icon
                >
                <v-icon v-else size="16">mdi-image-album</v-icon>
              </span>
              <span class="sidebar-list-label">
                <span class="sidebar-list-label-text"
                  ><template v-for="part in [findParts(row.name)]" :key="0"
                            >{{ part.before
                            }}<mark v-if="part.match" class="sidebar-find-mark">{{
                              part.match
                            }}</mark>{{ part.after }}</template
                          ></span>
              </span>
              <span class="sidebar-list-count">{{ folderRowCount(row) }}</span>
            </div>
            <p
              v-if="libraryFoldersFailed"
              class="sidebar-find-none"
              data-testid="sidebar-folders-failed"
            >
              Couldn't read the library's folders. They are read again on the
              next refresh.
            </p>
            <p
              v-else-if="libraryFoldersRead && !libraryFolders.length"
              class="sidebar-find-none"
              data-testid="sidebar-folders-empty"
            >
              No picture is in a folder yet: they all sit at the top of the
              library folder.
            </p>
          </template>

          <!-- A pending folder read for this library: the wizard's way back
               in. -->
          <button
            v-if="pendingForThisLibrary && !isReadOnly"
            type="button"
            class="sidebar-list-item sidebar-mapping-resume-row"
            @click="openPendingMapping"
          >
            <Tooltip
              :text="
                pendingForThisLibrary.taskId
                  ? 'The scan is kept - reopening this does not re-scan'
                  : 'Reopening this starts scanning that folder'
              "
              activator="parent"
            />
            <span class="sidebar-list-icon"
              ><v-icon size="16">mdi-map-marker-path</v-icon></span
            >
            <span class="sidebar-list-label"
              ><span class="sidebar-list-label-text"
                >Finish organising
                {{ pendingForThisLibrary.label || pendingForThisLibrary.path }}…</span
              ></span
            >
          </button>
          <!-- In a project's scope the People and Sets "+" open these: make
               a new one in the project, or tick existing ones in or out. -->
          <Teleport to="body">
            <div
              v-if="characterMoveMenuOpen"
              class="ctx-menu sidebar-move-menu"
              role="menu"
              tabindex="-1"
              :style="characterMenuPos"
              @keydown="onMoveMenuKeydown"
            >
              <button
                type="button"
                class="ctx-item"
                role="menuitem"
                @click.stop="
                  createCharacter(menuProjectId);
                  characterMoveMenuOpen = false;
                "
              >
                <v-icon class="ctx-icon"
                  >mdi-plus-circle-outline</v-icon
                >Create new
              </button>
              <div class="ctx-sep" role="separator"></div>
              <template
                v-for="group in projectMenuCharacterGroups"
                :key="group.label"
              >
                <div
                  class="section-label ctx-label"
                  :class="{
                    'ctx-label--current':
                      group.projectId === menuProjectId,
                  }"
                  role="presentation"
                >
                  {{ group.label }}
                </div>
                <button
                  v-for="char in group.items"
                  :key="char.id"
                  type="button"
                  class="ctx-item"
                  role="menuitemcheckbox"
                  :aria-checked="
                    entityBelongsToProject(
                      char,
                      menuProjectId,
                    )
                      ? 'true'
                      : 'false'
                  "
                  @click.stop="
                    toggleCharacterProjectMembership(char.id)
                  "
                >
                  <span class="ctx-label-text">{{
                    char.name
                  }}</span>
                  <v-icon
                    v-if="
                      entityBelongsToProject(
                        char,
                        menuProjectId,
                      )
                    "
                    class="ctx-check"
                    >mdi-check</v-icon
                  >
                </button>
              </template>
            </div>
          </Teleport>
          <Teleport to="body">
            <div
              v-if="setMoveMenuOpen"
              class="ctx-menu sidebar-move-menu"
              role="menu"
              tabindex="-1"
              :style="setMenuPos"
              @keydown="onMoveMenuKeydown"
            >
              <button
                type="button"
                class="ctx-item"
                role="menuitem"
                @click.stop="
                  createSet(menuProjectId);
                  setMoveMenuOpen = false;
                "
              >
                <v-icon class="ctx-icon"
                  >mdi-plus-circle-outline</v-icon
                >Create new
              </button>
              <div class="ctx-sep" role="separator"></div>
              <template
                v-for="group in projectMenuSetGroups"
                :key="group.label"
              >
                <div
                  class="section-label ctx-label"
                  :class="{
                    'ctx-label--current':
                      group.projectId === menuProjectId,
                  }"
                  role="presentation"
                >
                  {{ group.label }}
                </div>
                <button
                  v-for="pset in group.items"
                  :key="pset.id"
                  type="button"
                  class="ctx-item"
                  role="menuitemcheckbox"
                  :aria-checked="
                    entityBelongsToProject(
                      pset,
                      menuProjectId,
                    )
                      ? 'true'
                      : 'false'
                  "
                  @click.stop="
                    toggleSetProjectMembership(pset.id)
                  "
                >
                  <span class="ctx-label-text">{{
                    pset.name
                  }}</span>
                  <v-icon
                    v-if="
                      entityBelongsToProject(
                        pset,
                        menuProjectId,
                      )
                    "
                    class="ctx-check"
                    >mdi-check</v-icon
                  >
                </button>
              </template>
            </div>
          </Teleport>
        </div>
      </template>
    </div>
    <!-- end sidebar-scroll -->
    <!-- The footer band (#2159): one amber Import that names where the
         pictures will land. Owner only, and not on the docked rail. -->
    <div
      v-if="!sidebarStore.effectiveDocked && !isReadOnly"
      class="sidebar-footer-band"
    >
      <AppButton
        variant="primary"
        block
        icon-left="plus"
        class="sidebar-import-btn"
        :tooltip="importTooltip"
        @click="emit('open-import')"
        >{{ importLabel }}</AppButton
      >
    </div>
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
      ref="sidebarCtxMenuEl"
      class="ctx-menu sidebar-ctx-menu"
      :style="sidebarCtxMenuStyle"
      @contextmenu.prevent
      @mousedown.stop
    >
      <!-- ── Read-only indicator ───────────────────────────────── -->
      <!-- Suppressed for the empty-space menu: its view toggles (auto-hide,
           dock) are not content edits and stay enabled in read-only. -->
      <template v-if="sidebarCtxFolderRow && !isReadOnly">
        <button
          class="ctx-item"
          @click="
            openFolderInFileManager(sidebarCtxFolderRow);
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-folder-open-outline</v-icon>
          Open in file manager
        </button>
        <div v-if="sidebarCtxFolderRow.depth > 0 || sidebarCtxFolderRow.kind === 'project'" class="ctx-sep"></div>
      </template>
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
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            emit('select-insights');
            closeSidebarCtxMenu();
          "
        >
          <Tooltip
            :text="isReadOnly ? READ_ONLY_INSIGHTS_HINT : ''"
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-lightbulb-on-outline</v-icon>
          About your library
        </button>
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            shareResource(null, null, 'All Pictures');
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-share-variant-outline</v-icon>
          Share
        </button>
      </template>
      <template v-if="sidebarCtxScrapheap">
        <button
          class="ctx-item ctx-item--danger"
          :disabled="isReadOnly || scrapheapIsEmpty"
          :aria-disabled="isReadOnly || scrapheapIsEmpty"
          @click="emptyScrapheapFromCtx()"
        >
          <Tooltip
            :text="scrapheapIsEmpty ? 'Scrapheap is already empty' : ''"
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-trash-can-outline</v-icon>
          Empty Scrapheap
        </button>
      </template>
      <template v-if="sidebarCtxCharacter">
        <button
          v-if="!isReadOnly"
          class="ctx-item"
          @click="suggestPicturesForCharacterFromCtx(sidebarCtxCharacter)"
        >
          <Tooltip
            :text="`Rank the library against ${sidebarCtxCharacter.name}'s reference faces to find their un-tagged pictures`"
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-account-search</v-icon>
          <!-- The name is deliberately NOT in the label. The menu is anchored to
               that person's row and every other item in it is already about
               them, so repeating the name only bought an ellipsis: the menu is
               260px wide and "Suggest more pictures of <a real name>" does not
               fit. The title below still names them in full. -->
          <span class="ctx-label-text">Suggest more pictures</span>
        </button>
        <button
          v-if="!isReadOnly && filterStore.comfyuiConfigured"
          class="ctx-item"
          :disabled="sidebarCtxLoraState !== 'attached'"
          @click="openLoraRunFromCtx('character', sidebarCtxCharacter)"
        >
          <Tooltip
            :text="
              loraRowReason(sidebarCtxCharacter.name) ||
              `Run a workflow with ${sidebarCtxCharacter.name}'s LoRA`
            "
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-sitemap-outline</v-icon>
          <!-- The reason is in the label as well as the tooltip, as on the
               duplicates row below: a disabled row cannot be focused. -->
          <span class="ctx-label-text">{{
            sidebarCtxLoraState === "none"
              ? "No LoRA attached"
              : "Create with LoRA…"
          }}</span>
        </button>
        <button
          class="ctx-item"
          :disabled="
            isReadOnly ||
            duplicateCountFor('character', sidebarCtxCharacter) === 0
          "
          @click="findDuplicatesIn('character', sidebarCtxCharacter)"
        >
          <Tooltip
            :text="isReadOnly ? READ_ONLY_DEDUP_HINT : ''"
            activator="parent"
          />
          <v-icon class="ctx-icon">{{
            duplicateCountFor("character", sidebarCtxCharacter) === 0
              ? "mdi-check"
              : "mdi-content-duplicate"
          }}</v-icon>
          <span class="ctx-label-text">{{
            duplicateCountFor("character", sidebarCtxCharacter) === 0
              ? "No duplicates for this person"
              : "Find duplicates for this person"
          }}</span>
          <span
            v-if="duplicateCountFor('character', sidebarCtxCharacter)"
            class="ctx-meta"
            >{{ duplicateCountFor("character", sidebarCtxCharacter) }}</span
          >
        </button>
        <div class="ctx-sep"></div>
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            shareResource(
              'character',
              sidebarCtxCharacter.id,
              sidebarCtxCharacter.name,
            )
          "
        >
          <v-icon class="ctx-icon">mdi-share-variant-outline</v-icon>
          <span class="ctx-label-text"
            >Share "{{ sidebarCtxCharacter.name }}"</span
          >
        </button>
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            emit('train-lora', {
              type: 'character',
              id: sidebarCtxCharacter.id,
              name: sidebarCtxCharacter.name,
            });
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-school-outline</v-icon>
          <span class="ctx-label-text">Train LoRA…</span>
        </button>
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            createChildSet({
              characterId: sidebarCtxCharacter.id,
              name: sidebarCtxCharacter.name,
            })
          "
        >
          <v-icon class="ctx-icon">mdi-folder-plus-outline</v-icon>
          <span class="ctx-label-text"
            >New set in "{{ sidebarCtxCharacter.name }}"…</span
          >
        </button>
        <button
          class="ctx-item"
          :disabled="sidebarCtxDeleteIds.length > 1 || isReadOnly"
          :class="{
            'ctx-item--disabled': sidebarCtxDeleteIds.length > 1 || isReadOnly,
          }"
          @click="
            sidebarCtxDeleteIds.length === 1 &&
            !isReadOnly &&
            (openCharacterEditor(sidebarCtxCharacter), closeSidebarCtxMenu())
          "
        >
          <v-icon class="ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <button
          v-if="sharedCharacterIds.has(sidebarCtxCharacter.id)"
          class="ctx-item ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            openRevokeSharesDialog(
              'character',
              sidebarCtxCharacter.id,
              sidebarCtxCharacter.name,
            )
          "
        >
          <v-icon class="ctx-icon">mdi-link-variant-off</v-icon>
          Remove all shares
        </button>
        <button
          class="ctx-item ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            deleteCharactersByIds(sidebarCtxDeleteIds);
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-trash-can-outline</v-icon>
          {{
            sidebarCtxDeleteIds.length > 1
              ? `Delete ${sidebarCtxDeleteIds.length} characters`
              : "Delete"
          }}
        </button>
      </template>
      <template v-if="sidebarCtxSet">
        <!-- A LoRA dataset is a set (#2124): training it and filing a new set
             under it lead the menu. -->
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            emit('train-lora', {
              type: 'set',
              id: sidebarCtxSet.id,
              name: sidebarCtxSet.name,
            });
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-school-outline</v-icon>
          <span class="ctx-label-text">Train LoRA…</span>
        </button>
        <button
          class="ctx-item"
          :disabled="isReadOnly || setDepth(sidebarCtxSet) >= MAX_SET_DEPTH"
          :class="{
            'ctx-item--disabled': setDepth(sidebarCtxSet) >= MAX_SET_DEPTH,
          }"
          @click="
            createChildSet({
              setId: sidebarCtxSet.id,
              name: sidebarCtxSet.name,
            })
          "
        >
          <Tooltip
            :text="
              setDepth(sidebarCtxSet) >= MAX_SET_DEPTH ? SET_DEPTH_REASON : ''
            "
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-folder-plus-outline</v-icon>
          <span class="ctx-label-text"
            >New set inside "{{ sidebarCtxSet.name }}"…</span
          >
        </button>
        <!-- Hidden on a locked set: adding to it is refused, and an action
             that cannot complete is worse than one that is not offered. -->
        <button
          v-if="!isReadOnly && !sidebarCtxSet.locked"
          class="ctx-item"
          @click="suggestPicturesForSetFromCtx(sidebarCtxSet)"
        >
          <Tooltip
            :text="`Rank the library against ${sidebarCtxSet.name} to find pictures that belong in it`"
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-image-search</v-icon>
          <span class="ctx-label-text">Suggest more pictures</span>
        </button>
        <!-- Hidden on a locked set for the same reason: the run files its
             pictures into the set, and a locked set refuses them. -->
        <button
          v-if="
            !isReadOnly && !sidebarCtxSet.locked && filterStore.comfyuiConfigured
          "
          class="ctx-item"
          :disabled="sidebarCtxLoraState !== 'attached'"
          @click="openLoraRunFromCtx('set', sidebarCtxSet)"
        >
          <Tooltip
            :text="
              loraRowReason(sidebarCtxSet.name) ||
              `Run a workflow with the LoRA attached to ${sidebarCtxSet.name}`
            "
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-sitemap-outline</v-icon>
          <span class="ctx-label-text">{{
            sidebarCtxLoraState === "none"
              ? "No LoRA attached"
              : "Create with LoRA…"
          }}</span>
        </button>
        <button
          class="ctx-item"
          :disabled="
            isReadOnly || duplicateCountFor('set', sidebarCtxSet) === 0
          "
          @click="findDuplicatesIn('set', sidebarCtxSet)"
        >
          <Tooltip
            :text="isReadOnly ? READ_ONLY_DEDUP_HINT : ''"
            activator="parent"
          />
          <v-icon class="ctx-icon">{{
            duplicateCountFor("set", sidebarCtxSet) === 0
              ? "mdi-check"
              : "mdi-content-duplicate"
          }}</v-icon>
          <span class="ctx-label-text">{{
            duplicateCountFor("set", sidebarCtxSet) === 0
              ? "No duplicates in this set"
              : "Find duplicates in this set"
          }}</span>
          <span
            v-if="duplicateCountFor('set', sidebarCtxSet)"
            class="ctx-meta"
            >{{ duplicateCountFor("set", sidebarCtxSet) }}</span
          >
        </button>
        <div class="ctx-sep"></div>
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            shareResource('picture_set', sidebarCtxSet.id, sidebarCtxSet.name)
          "
        >
          <v-icon class="ctx-icon">mdi-share-variant-outline</v-icon>
          <span class="ctx-label-text">Share "{{ sidebarCtxSet.name }}"</span>
        </button>
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            openSetEditor(sidebarCtxSet);
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <!-- Icon sub-menu -->
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click.stop="openSetCtxIconMenu($event)"
        >
          <v-icon
            class="ctx-icon"
            :color="sidebarCtxSet.set_color || undefined"
          >
            {{
              sidebarCtxSet.set_icon && sidebarCtxSet.set_icon !== ICON_CARDS
                ? sidebarCtxSet.set_icon
                : "mdi-layers-triple"
            }}
          </v-icon>
          Icon
          <v-icon class="ctx-arrow">mdi-chevron-right</v-icon>
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
                      :aria-label="ic.label"
                      @click="
                        applySetAppearance(sidebarCtxSet.id, ic.value, null)
                      "
                    >
                      <Tooltip
                        :text="ic.label"
                        activator="parent"
                        :describe="false"
                      />
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
                  aria-label="Thumbnail Stack"
                  @click="
                    applySetAppearance(sidebarCtxSet.id, ICON_CARDS, null)
                  "
                >
                  <Tooltip
                    text="Thumbnail Stack"
                    activator="parent"
                    :describe="false"
                  />
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
          class="ctx-item"
          :disabled="isReadOnly"
          @click.stop="openSetCtxColorMenu($event)"
        >
          <span
            class="sidebar-ctx-color-dot"
            :style="{ background: sidebarCtxSet.set_color || '#888' }"
          />
          Color
          <v-icon class="ctx-arrow">mdi-chevron-right</v-icon>
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
                :aria-label="col.label"
                @click="applySetAppearance(sidebarCtxSet.id, null, col.value)"
              >
                <Tooltip
                  :text="col.label"
                  activator="parent"
                  :describe="false"
                />
              </button>
            </div>
          </div>
        </Teleport>
        <button
          v-if="sharedSetIds.has(sidebarCtxSet.id)"
          class="ctx-item ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            openRevokeSharesDialog(
              'picture_set',
              sidebarCtxSet.id,
              sidebarCtxSet.name,
            )
          "
        >
          <v-icon class="ctx-icon">mdi-link-variant-off</v-icon>
          Remove all shares
        </button>
        <!-- A locked set refuses the move, so it is greyed with the reason
             rather than offered and then refused. -->
        <button
          v-if="setHasParent(sidebarCtxSet)"
          class="ctx-item"
          :disabled="isReadOnly || sidebarCtxSet.locked"
          :class="{ 'ctx-item--disabled': sidebarCtxSet.locked }"
          @click="moveSetToTop(sidebarCtxSet)"
        >
          <Tooltip
            :text="sidebarCtxSet.locked ? SET_LOCK_MOVE_REASON : ''"
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-arrow-top-left</v-icon>
          <!-- A parent outside the current scope is not listed, so it has no
               name to give; the set still moves. -->
          <span class="ctx-label-text">{{
            setParentName(sidebarCtxSet)
              ? `Move out of "${setParentName(sidebarCtxSet)}"`
              : "Move to the top level"
          }}</span>
        </button>
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="toggleSetLock(sidebarCtxSet)"
        >
          <v-icon class="ctx-icon">{{
            sidebarCtxSet.locked
              ? "mdi-lock-open-variant-outline"
              : "mdi-lock-outline"
          }}</v-icon>
          {{ sidebarCtxSet.locked ? "Unlock set" : "Lock set" }}
        </button>
        <button
          class="ctx-item ctx-item--danger"
          :disabled="isReadOnly || sidebarCtxSet.locked"
          @click="
            deleteSetsByIds(ctxSetDeleteIds);
            closeSidebarCtxMenu();
          "
        >
          <Tooltip
            :text="sidebarCtxSet.locked ? SET_LOCK_REASON : ''"
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-trash-can-outline</v-icon>
          {{
            ctxSetDeleteIds.length > 1
              ? `Delete ${ctxSetDeleteIds.length} sets`
              : "Delete"
          }}
        </button>
      </template>
      <template v-if="sidebarCtxProject">
        <button
          class="ctx-item"
          :disabled="
            isReadOnly || duplicateCountFor('project', sidebarCtxProject) === 0
          "
          @click="findDuplicatesIn('project', sidebarCtxProject)"
        >
          <Tooltip
            :text="isReadOnly ? READ_ONLY_DEDUP_HINT : ''"
            activator="parent"
          />
          <v-icon class="ctx-icon">{{
            duplicateCountFor("project", sidebarCtxProject) === 0
              ? "mdi-check"
              : "mdi-content-duplicate"
          }}</v-icon>
          <span class="ctx-label-text">{{
            duplicateCountFor("project", sidebarCtxProject) === 0
              ? "No duplicates in this project"
              : "Find duplicates in this project"
          }}</span>
          <span
            v-if="duplicateCountFor('project', sidebarCtxProject)"
            class="ctx-meta"
            >{{ duplicateCountFor("project", sidebarCtxProject) }}</span
          >
        </button>
        <div class="ctx-sep"></div>
        <button
          class="ctx-item"
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
          <v-icon class="ctx-icon">mdi-share-variant-outline</v-icon>
          Share
        </button>
        <button
          class="ctx-item"
          @click="
            exportProject(sidebarCtxProject);
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-download-outline</v-icon>
          Export as ZIP
        </button>
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            openProjectEditor(sidebarCtxProject);
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-pencil</v-icon>
          Edit
        </button>
        <button
          v-if="!isReadOnly"
          class="ctx-item"
          @click="
            createProject();
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-folder-plus-outline</v-icon>
          New project…
        </button>
        <button
          v-if="sharedProjectIds.has(sidebarCtxProject.id)"
          class="ctx-item ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            openRevokeSharesDialog(
              'project',
              sidebarCtxProject.id,
              sidebarCtxProject.name,
            )
          "
        >
          <v-icon class="ctx-icon">mdi-link-variant-off</v-icon>
          Remove all shares
        </button>
        <button
          class="ctx-item ctx-item--danger"
          :disabled="isReadOnly"
          @click="
            deleteProjectById(sidebarCtxProject);
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-trash-can-outline</v-icon>
          Delete
        </button>
      </template>
      <!-- ── Section-header menus (right-click a section's title) ── -->
      <template v-if="sidebarCtxHeader === 'people'">
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            createCharacter();
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-account-plus-outline</v-icon>
          Create person
        </button>
      </template>
      <template v-if="sidebarCtxHeader === 'sets'">
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="
            createSet();
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-image-album</v-icon>
          Create set
        </button>
      </template>
      <!-- ── Empty-space menu (right-click uncovered sidebar area) ── -->
      <template v-if="sidebarCtxEmpty">
        <button
          v-if="!isReadOnly"
          class="ctx-item"
          @click="
            createProject();
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">mdi-folder-plus-outline</v-icon>
          New project…
        </button>
        <button
          class="ctx-item"
          @click="
            sidebarStore.setSidebarPinned(!sidebarStore.sidebarPinned);
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">{{
            sidebarStore.sidebarPinned
              ? "mdi-checkbox-blank-outline"
              : "mdi-checkbox-marked-outline"
          }}</v-icon>
          Auto hide sidebar
        </button>
        <button
          class="ctx-item"
          @click="
            sidebarStore.setSidebarDocked(!sidebarStore.sidebarDocked);
            closeSidebarCtxMenu();
          "
        >
          <v-icon class="ctx-icon">{{
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
  <AppDialog
    :open="revokeSharesDialogOpen"
    title="Remove all shares"
    size="sm"
    @close="revokeSharesDialogOpen = false"
  >
    <p class="share-dialog-hint">
      This will revoke all active share links for
      <strong>{{ revokeSharesPending?.label }}</strong
      >. Anyone with an existing link will lose access immediately.
    </p>
    <template #footer>
      <AppButton variant="secondary" @click="revokeSharesDialogOpen = false">
        Cancel
      </AppButton>
      <AppButton variant="danger" @click="confirmRevokeShares">
        Remove all shares
      </AppButton>
    </template>
  </AppDialog>
</template>
<style scoped src="./SidebarNav.css"></style>
<style scoped src="./SideBar.css"></style>
<style src="./SideBar.global.css"></style>
