<script setup>
import {
  ref,
  watch,
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  provide,
} from "vue";
import AppInspector from "../widgets/AppInspector.vue";
import AppButton from "../widgets/AppButton.vue";
import SectionHeader from "../widgets/SectionHeader.vue";
import RailPicture from "./RailPicture.vue";
import RailPictures from "./RailPictures.vue";
import StatsHistogram from "../widgets/StatsHistogram.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { getPictureStats } from "../../api/pictures";
import { isReadOnly } from "../../utils/apiClient";
import { formatUserDate } from "../../utils/utils.js";
import {
  SECTION_SCOPE,
  useSectionCollapse,
} from "../../composables/useSectionCollapse";
import {
  fitWords,
  pictureTitle,
  useRailPicture,
} from "../../composables/useRailPicture";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useProjectStore } from "../../stores/useProjectStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useWsStore } from "../../stores/useWsStore";
import { useInboxStore } from "../../stores/useInboxStore";
import {
  isGroupToken,
  useTagGroupsStore,
} from "../../stores/useTagGroupsStore";
import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
} from "../../stores/useViewStore";

// Store-direct (Phase 3): the panel reads the same filter/selection state the
// grid queries from straight from the stores, and writes filter changes back
// itself. App.vue no longer mirrors any of it through props.
const filterStore = useFilterStore();
const tagGroups = useTagGroupsStore();
const selectionStore = useSelectionStore();
const projectStore = useProjectStore();
const userPrefsStore = useUserPrefsStore();
const sidebarStore = useSidebarStore();
const wsStore = useWsStore();
const inboxStore = useInboxStore();

// The two folder facets are derived from the sidebar's folder selection, same
// as App.vue used to compute them for the old props.
const filePathPrefixFilter = computed(
  () => selectionStore.selectedFolderFilter?.pathPrefix ?? null,
);
const importSourceFolderFilter = computed(
  () => filterStore.importedFromFilter ?? null,
);
// The Inbox's period, as the grid sends it (`useGridFetch.inboxPeriod`).
const inboxPeriod = computed(() =>
  selectionStore.selectedFolderFilter?.inbox ? inboxStore.period : null,
);

// ─── PIL / Video extension lists ─────────────────────────────────────────────
const PIL_IMAGE_EXTENSIONS = [
  "jpg",
  "jpeg",
  "png",
  "webp",
  "gif",
  "bmp",
  "tiff",
  "avif",
];
const VIDEO_EXTENSIONS = ["mp4", "webm", "mov", "avi", "mkv", "m4v"];

const props = defineProps({
  /** The view the grid shows: the head's title while nothing is selected. */
  viewName: { type: String, default: "" },
  /** The grid's cards for the selection, where loaded (App.vue). */
  pictures: { type: Array, default: () => [] },
  /** The rating a tile shows for a card (a guest's own, when read-only). */
  scoreOf: { type: Function, default: (card) => card?.score || 0 },
  /**
   * The lightbox lies over the rail. Its panels are the ones the rail would
   * show, so the rail's copies go rather than sit live (and focusable) under it.
   */
  covered: { type: Boolean, default: false },
});

// What the rail does to a picture goes through the grid's own paths (App.vue
// hands each to the grid), so the rail and the tile cannot disagree.
const emit = defineEmits([
  "set-score",
  "toggle-favourite",
  "update-description",
  "lightbox-change",
  "tags-applied",
]);

// ─── The selection (#2161) ────────────────────────────────────────────────────
// Nothing selected, the rail describes the view; one picture, that picture;
// several, what they share.
const selectedIds = computed(() => selectionStore.selectedImageIds);
watch(selectedIds, () => {
  // A T that no tag field took (the rail was under the lightbox) is spent:
  // it must not take the caret when some later selection mounts one.
  sidebarStore.tagFieldPending = false;
});
const railMode = computed(() => {
  const n = selectedIds.value.length;
  if (n === 0) return "none";
  return n === 1 ? "one" : "many";
});
const oneId = computed(() =>
  railMode.value === "one" ? selectedIds.value[0] : null,
);
const oneCard = computed(
  () =>
    props.pictures.find((card) => String(card.id) === String(oneId.value)) ??
    null,
);
const {
  image: oneImage,
  faces: oneFaces,
  refresh: refreshOne,
} = useRailPicture(() => oneId.value);
const isScrapheapView = computed(
  () => selectionStore.selectedCharacter === String(SCRAPHEAP_PICTURES_ID),
);

const namesStore = useEntityNamesStore();

// The head's text. A picture is named by what it shows, never by its file
// name while there is anything better: files mostly have random names.
const head = computed(() => {
  if (railMode.value === "none") {
    return { title: props.viewName, full: props.viewName };
  }
  if (railMode.value === "many") {
    const text = `${selectedIds.value.length.toLocaleString()} pictures`;
    return { title: text, full: text };
  }
  const people = [
    ...new Set(
      oneFaces.value
        .map((face) => namesStore.characterNames[face.character_id])
        .filter(Boolean),
    ),
  ];
  return pictureTitle(oneImage.value, people, (date) =>
    formatUserDate(date, userPrefsStore.dateFormat),
  );
});

// The grid's closed-rail edge tab names the selection the way this head does.
defineExpose({ head });

// The same act as the toolbar toggle, so it persists. The button goes with
// the rail, so focus moves to the grid toolbar's toggle rather than dropping
// to `body`; the lightbox mounts a toggle of its own, which is not that one.
function closeRail() {
  sidebarStore.toggleStats();
  nextTick(() =>
    [...document.querySelectorAll(".tb-stats-btn")]
      .find((el) => !el.closest(".lightbox-topbar-sidebar-toggle"))
      ?.focus(),
  );
}

// The head cuts its title after the last whole word that fits, which CSS
// cannot do: it measures the title in the head's own font.
const titleRef = ref(null);
const titleHovered = ref(false);
const railPictureRef = ref(null);
const titleWidth = ref(0);
let titleCanvas = null;
function measureTitle(text) {
  titleCanvas ??= document.createElement("canvas").getContext("2d");
  if (!titleCanvas || !titleRef.value) return 0;
  // Longhands, not `font`: not every engine serialises the shorthand.
  const style = getComputedStyle(titleRef.value);
  titleCanvas.font = [
    style.fontStyle,
    style.fontWeight,
    style.fontSize,
    style.fontFamily,
  ].join(" ");
  return titleCanvas.measureText(text).width;
}
const shownTitle = computed(() =>
  fitWords(head.value.title, titleWidth.value, measureTitle),
);
let titleObserver = null;
watch(titleRef, (el) => {
  titleObserver?.disconnect();
  if (!el || typeof ResizeObserver === "undefined") return;
  titleObserver = new ResizeObserver(() => {
    titleWidth.value = el.clientWidth;
  });
  titleObserver.observe(el);
});
onUnmounted(() => titleObserver?.disconnect());

// The sections in this rail keep their own collapsed state, apart from the
// same sections in the lightbox.
provide(SECTION_SCOPE, "rail");
const RAIL = { scope: "rail" };
const topTagsCollapsed = useSectionCollapse("topTags", RAIL);
const coocCollapsed = useSectionCollapse("cooccurrences", {
  ...RAIL,
  defaultCollapsed: true,
});
const confHistCollapsed = useSectionCollapse("tagConfidence", {
  ...RAIL,
  defaultCollapsed: true,
});
const taggedCollapsed = useSectionCollapse("taggedPictures", RAIL);
const scoreCollapsed = useSectionCollapse("score", RAIL);
const smartCollapsed = useSectionCollapse("smartScore", RAIL);
const agreementCollapsed = useSectionCollapse("agreement", RAIL);
const resolutionCollapsed = useSectionCollapse("resolution", RAIL);
const coocOpen = computed(() => !coocCollapsed.value);
const confHistOpen = computed(() => !confHistCollapsed.value);

// Tab state
const activeTab = ref("tags");

const tabs = computed(() => [
  { value: "tags", label: "Tags", icon: "mdi-tag-multiple-outline" },
  { value: "pictures", label: "Pictures", icon: "mdi-image-multiple-outline" },
]);

// Picture stats
const picStats = ref(null);
const picStatsLoaded = ref(false);
const picStatsLoading = ref(false);

async function fetchPicStats() {
  const qs = buildQueryParams();
  picStatsLoading.value = true;
  try {
    picStats.value = await getPictureStats(qs, { include: "picture" });
    picStatsLoaded.value = true;
  } catch {
    picStats.value = null;
  } finally {
    picStatsLoading.value = false;
  }
}

watch(activeTab, (tab) => {
  if (tab === "pictures" && !picStatsLoaded.value && !picStatsLoading.value) {
    fetchPicStats();
  }
});

// Tag confidence filter
const selectedConfTag = ref(null);
const confTagData = ref(null);
const confTagLoading = ref(false);

async function fetchTagConfidence(tag) {
  const qs = buildQueryParams();
  confTagLoading.value = true;
  try {
    confTagData.value = await getPictureStats(qs, {
      include: "conf",
      confidence_tag: tag,
    });
  } catch {
    confTagData.value = null;
  } finally {
    confTagLoading.value = false;
  }
}

watch(selectedConfTag, (tag) => {
  confTagData.value = null;
  if (tag) fetchTagConfidence(tag);
});

// ─── Stats data ───────────────────────────────────────────────────────────────
const stats = ref(null);
const loading = ref(false);
const error = ref(null);

function buildQueryParams() {
  const params = new URLSearchParams();

  const normalizedSetIds = Array.isArray(selectionStore.selectedSetIds)
    ? selectionStore.selectedSetIds
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0)
    : [];
  const hasSetSelection = !!(
    (selectionStore.selectedSet != null && selectionStore.selectedSet !== "") ||
    normalizedSetIds.length > 0
  );
  const isSetOverlap = normalizedSetIds.length > 1;
  const primarySetId =
    !isSetOverlap &&
    selectionStore.selectedSet != null &&
    selectionStore.selectedSet !== ""
      ? Number(selectionStore.selectedSet)
      : null;
  const normalizedCharIds = Array.isArray(selectionStore.selectedCharacterIds)
    ? selectionStore.selectedCharacterIds
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0)
    : [];
  const isMultiCharacterView = normalizedCharIds.length > 1;

  if (hasSetSelection) {
    if (isSetOverlap) {
      for (const id of normalizedSetIds) params.append("set_ids", String(id));
      params.append("set_mode", selectionStore.setMultiMode || "intersection");
      if (
        selectionStore.setMultiMode === "difference" &&
        selectionStore.setDifferenceBaseId != null
      ) {
        params.append(
          "base_set_id",
          String(selectionStore.setDifferenceBaseId),
        );
      }
    } else if (primarySetId != null) {
      params.append("set_id", String(primarySetId));
    }
    if (projectStore.projectViewMode === "project") {
      params.append(
        "project_id",
        projectStore.selectedProjectId != null
          ? projectStore.selectedProjectId
          : "UNASSIGNED",
      );
    }
  } else if (isMultiCharacterView) {
    for (const id of normalizedCharIds)
      params.append("character_ids", String(id));
    params.append(
      "character_mode",
      selectionStore.characterMultiMode || "union",
    );
    if (projectStore.projectViewMode === "project") {
      params.append(
        "project_id",
        projectStore.selectedProjectId != null
          ? projectStore.selectedProjectId
          : "UNASSIGNED",
      );
    }
  } else if (
    selectionStore.selectedCharacter != null &&
    selectionStore.selectedCharacter !== "" &&
    selectionStore.selectedCharacter !== ALL_PICTURES_ID
  ) {
    if (selectionStore.selectedCharacter === String(SCRAPHEAP_PICTURES_ID)) {
      params.append("only_deleted", "true");
    } else {
      params.append("character_id", selectionStore.selectedCharacter);
      if (projectStore.projectViewMode === "project") {
        params.append(
          "project_id",
          projectStore.selectedProjectId != null
            ? projectStore.selectedProjectId
            : "UNASSIGNED",
        );
      }
    }
  } else if (
    selectionStore.selectedCharacter === ALL_PICTURES_ID &&
    projectStore.projectViewMode === "project"
  ) {
    params.append(
      "project_id",
      projectStore.selectedProjectId != null
        ? projectStore.selectedProjectId
        : "UNASSIGNED",
    );
  }

  if (filterStore.mediaTypeFilter === "images") {
    for (const ext of PIL_IMAGE_EXTENSIONS)
      params.append("format", ext.toUpperCase());
  } else if (filterStore.mediaTypeFilter === "videos") {
    for (const ext of VIDEO_EXTENSIONS)
      params.append("format", ext.toUpperCase());
  }

  if (filterStore.minScoreFilter != null)
    params.append("min_score", filterStore.minScoreFilter);
  if (filterStore.maxScoreFilter != null)
    params.append("max_score", filterStore.maxScoreFilter);
  if (filterStore.unscoredOnlyFilter) params.append("unscored", "1");
  if (filterStore.favouriteOnlyFilter) params.append("favourite", "1");
  if (filterStore.smartScoreBucketFilter != null)
    params.append("smart_score_bucket", filterStore.smartScoreBucketFilter);
  if (filterStore.resolutionBucketFilter != null)
    params.append("resolution_bucket", filterStore.resolutionBucketFilter);
  if (filePathPrefixFilter.value != null)
    params.append("file_path_prefix", filePathPrefixFilter.value);
  if (importSourceFolderFilter.value != null)
    params.append("import_source_folder", importSourceFolderFilter.value);
  if (inboxPeriod.value) params.append("inbox", inboxPeriod.value);
  (filterStore.tagFilter || []).forEach((t) => params.append("tag", t));
  (filterStore.tagRejectedFilter || []).forEach((t) =>
    params.append("rejected_tag", t),
  );
  if (filterStore.faceBboxFilter != null) {
    params.append("face_filter", filterStore.faceBboxFilter);
  }
  (filterStore.tagConfidenceAboveFilter || []).forEach((e) =>
    params.append("tag_confidence_above", e),
  );
  (filterStore.tagConfidenceBelowFilter || []).forEach((e) =>
    params.append("tag_confidence_below", e),
  );

  return params.toString();
}

async function fetchStats() {
  const qs = buildQueryParams();
  const prevRegularTags = stats.value?.regular_tags;
  loading.value = true;
  error.value = null;
  try {
    const body = await getPictureStats(qs);
    stats.value = { ...body, regular_tags: prevRegularTags };
  } catch {
    error.value = "Failed to load stats";
    stats.value = null;
  } finally {
    loading.value = false;
  }
}

// Lazy data for heavy sections
const coocLoaded = ref(false);
const confLoaded = ref(false);

// Penalised-filter state is used by immediate watchers below, so it must be
// declared before those watchers are created.
const penalisedOnlyTags = ref(false);
// 0 = all, 1 = at least one penalised, 2 = both penalised
const penalisedOnlyCooc = ref(0);
const statsPenalised = ref(null);
const loadingPenalised = ref(false);
const statsPenalisedBoth = ref(null);
const loadingPenalisedBoth = ref(false);

async function fetchCooc() {
  const qs = buildQueryParams();
  try {
    const body = await getPictureStats(qs, { include: "cooc" });
    if (stats.value)
      stats.value = {
        ...stats.value,
        top_cooccurrences: body.top_cooccurrences,
      };
    coocLoaded.value = true;
  } catch (e) {
    // Non-fatal: the co-occurrence section stays empty and the rest of the
    // panel still renders. Log it so it is not an invisible failure.
    console.warn("Failed to load tag co-occurrence stats:", e);
  }
}

async function fetchConf() {
  const qs = buildQueryParams();
  try {
    const body = await getPictureStats(qs, { include: "conf" });
    if (stats.value)
      stats.value = {
        ...stats.value,
        confidence_histogram: body.confidence_histogram,
        regular_tags: body.regular_tags,
      };
    confLoaded.value = true;
  } catch {
    // silently fail - conf stays empty
  }
}

// Refetch when the query the panel would actually send changes.
// buildQueryParams() reads every relevant piece of store state, so a computed
// over it tracks exactly the right dependencies. Unlike the old per-prop
// watch list, it also doesn't refire when a store write leaves the resulting
// query string identical.
//
// A group term (`__group:3`) reads the same while a move or its undo changes
// which pictures it matches, so such a query also carries the map's revision.
const statsQueryParams = computed(() => {
  const qs = buildQueryParams();
  const filters = [
    ...(filterStore.tagFilter || []),
    ...(filterStore.tagRejectedFilter || []),
  ];
  return filters.some(isGroupToken) ? `${qs}#groups=${tagGroups.revision}` : qs;
});
watch(
  statsQueryParams,
  () => {
    statsPenalised.value = null;
    statsPenalisedBoth.value = null;
    confTagData.value = null;
    coocLoaded.value = false;
    confLoaded.value = false;
    picStatsLoaded.value = false;
    picStats.value = null;
    fetchStats().then(() => {
      if (coocOpen.value) fetchCooc();
      if (confHistOpen.value) fetchConf();
      if (activeTab.value === "pictures") fetchPicStats();
      if (selectedConfTag.value) {
        const allAvailable = new Set([
          ...anomalyTagOptions.value,
          ...regularTags.value,
        ]);
        if (!allAvailable.has(selectedConfTag.value)) {
          selectedConfTag.value = null;
        } else {
          fetchTagConfidence(selectedConfTag.value);
        }
      }
    });
    if (penalisedOnlyTags.value || penalisedOnlyCooc.value > 0)
      fetchStatsPenalised();
    if (penalisedOnlyCooc.value === 2) fetchStatsPenalisedBoth();
  },
  { immediate: true },
);

// Belt-and-suspenders: if the immediate watch trigger fired before the component
// was fully connected (e.g. a startup race in Root.vue's auth check), the
// initial fetch may not have landed. Retry on mount when stats are still absent.
onMounted(() => {
  if (!stats.value && !loading.value) {
    fetchStats();
  }
});

// When tags change on any picture, the backend cache is already cleared via the
// CHANGED_TAGS event. Refetch stats so the sidebar reflects the updated counts.
// Debounce rapid tag-change bursts (e.g. a tagging run) so we don't hit
// /pictures/stats on every task completion while the sidebar is closed.
let _wsTagUpdateTimer = null;
watch(
  () => wsStore.wsTagUpdate,
  () => {
    if (!sidebarStore.statsOpen) return;
    clearTimeout(_wsTagUpdateTimer);
    _wsTagUpdateTimer = setTimeout(() => {
      fetchStats().then(() => {
        if (coocOpen.value) fetchCooc();
        if (confHistOpen.value) fetchConf();
        if (activeTab.value === "pictures") fetchPicStats();
        if (penalisedOnlyTags.value || penalisedOnlyCooc.value > 0)
          fetchStatsPenalised();
        if (penalisedOnlyCooc.value === 2) fetchStatsPenalisedBoth();
      });
    }, 2000);
  },
);
onUnmounted(() => {
  clearTimeout(_wsTagUpdateTimer);
});

// The one picture's tags or description changed elsewhere (a tagger run, the
// lightbox, another window): read its details again, keeping them on screen.
watch(
  () => [wsStore.wsTagUpdate, wsStore.wsDescriptionUpdate],
  (updates) => {
    if (oneId.value == null) return;
    const id = String(oneId.value);
    const touched = updates.some((u) =>
      (u?.pictureIds || []).some((p) => String(p) === id),
    );
    if (touched) refreshOne();
  },
);

// ─── Donut chart ──────────────────────────────────────────────────────────────
const DONUT_R = 40;
const DONUT_CX = 56;
const DONUT_CY = 56;
const DONUT_STROKE = 18;
const DONUT_CIRCUMFERENCE = 2 * Math.PI * DONUT_R;

const donutTaggedDash = computed(() => {
  if (!stats.value || stats.value.total === 0)
    return "0 " + DONUT_CIRCUMFERENCE;
  const fraction = stats.value.tagged / stats.value.total;
  return `${fraction * DONUT_CIRCUMFERENCE} ${DONUT_CIRCUMFERENCE}`;
});

const donutUntaggedDash = computed(() => {
  if (!stats.value || stats.value.total === 0)
    return DONUT_CIRCUMFERENCE + " " + DONUT_CIRCUMFERENCE;
  const fraction = stats.value.untagged / stats.value.total;
  return `${fraction * DONUT_CIRCUMFERENCE} ${DONUT_CIRCUMFERENCE}`;
});

const donutUntaggedOffset = computed(() => {
  if (!stats.value || stats.value.total === 0) return 0;
  const taggedFraction = stats.value.tagged / stats.value.total;
  return -(taggedFraction * DONUT_CIRCUMFERENCE);
});

// ─── Bar chart ────────────────────────────────────────────────────────────────
const maxTagCount = computed(() => {
  return displayedTags.value.length ? displayedTags.value[0].count : 1;
});

function barWidth(count) {
  return Math.max(2, (count / maxTagCount.value) * 100);
}

// ─── Penalised filter ─────────────────────────────────────────────────────────
function isPenalised(tag) {
  return Object.prototype.hasOwnProperty.call(
    userPrefsStore.penalisedTagWeights,
    String(tag).trim().toLowerCase(),
  );
}

const hasPenalisedTags = computed(
  () => Object.keys(userPrefsStore.penalisedTagWeights).length > 0,
);

async function fetchStatsPenalised() {
  const qs = buildQueryParams();
  loadingPenalised.value = true;
  try {
    statsPenalised.value = await getPictureStats(qs, {
      only_penalised: 1,
      include: "cooc",
    });
  } catch {
    statsPenalised.value = null;
  } finally {
    loadingPenalised.value = false;
  }
}

async function fetchStatsPenalisedBoth() {
  const qs = buildQueryParams();
  loadingPenalisedBoth.value = true;
  try {
    statsPenalisedBoth.value = await getPictureStats(qs, {
      only_penalised: "both",
      include: "cooc",
    });
  } catch {
    statsPenalisedBoth.value = null;
  } finally {
    loadingPenalisedBoth.value = false;
  }
}

watch([penalisedOnlyTags, penalisedOnlyCooc], ([tags, cooc]) => {
  if ((tags || cooc >= 1) && !statsPenalised.value && !loadingPenalised.value) {
    fetchStatsPenalised();
  }
  if (cooc === 2 && !statsPenalisedBoth.value && !loadingPenalisedBoth.value) {
    fetchStatsPenalisedBoth();
  }
});

watch(coocOpen, (open) => {
  if (open && !coocLoaded.value && stats.value) fetchCooc();
});
watch(confHistOpen, (open) => {
  if (open && !confLoaded.value && stats.value) fetchConf();
});

const displayedTags = computed(() => {
  if (penalisedOnlyTags.value) return statsPenalised.value?.top_tags ?? [];
  return stats.value?.top_tags ?? [];
});

const displayedCooc = computed(() => {
  const coocMode = penalisedOnlyCooc.value;
  if (coocMode === 0) return stats.value?.top_cooccurrences ?? [];
  if (coocMode === 2) return statsPenalisedBoth.value?.top_cooccurrences ?? [];
  return statsPenalised.value?.top_cooccurrences ?? [];
});

const COOC_FILTER_TITLES = [
  "Show pairs with one penalised tag",
  "Show pairs with both penalised",
  "Show all co-occurrences",
];

// ─── Histogram helpers ────────────────────────────────────────────────────────────────
const anomalyTagOptions = computed(() =>
  Object.keys(userPrefsStore.penalisedTagWeights).sort((a, b) =>
    a.localeCompare(b),
  ),
);
const regularTags = computed(() => stats.value?.regular_tags ?? []);

// Active-state helpers
const tagFilterSet = computed(() => new Set(filterStore.tagFilter || []));
function isTagActive(tag) {
  return tagFilterSet.value.has(tag);
}
function isCoocActive(tags) {
  return tagFilterSet.value.has(tags[0]) && tagFilterSet.value.has(tags[1]);
}
function activeTagsInTopTags() {
  return (displayedTags.value || []).map((t) => t.tag).filter(isTagActive);
}
function activeTagsInCooc() {
  const active = new Set();
  for (const item of displayedCooc.value || []) {
    if (isCoocActive(item.tags)) {
      active.add(item.tags[0]);
      active.add(item.tags[1]);
    }
  }
  return [...active];
}
function activeConfEntries() {
  return (filterStore.tagConfidenceAboveFilter || []).filter((e) => {
    if (!selectedConfTag.value) return false;
    return e.startsWith(selectedConfTag.value + ":");
  });
}
function isConfEntryActive(bucketIndex) {
  const entry = `${selectedConfTag.value}:${(bucketIndex * 0.2).toFixed(2)}`;
  return (filterStore.tagConfidenceAboveFilter || []).includes(entry);
}

const confHistBuckets = computed(() => {
  if (selectedConfTag.value)
    return confTagData.value?.confidence_histogram ?? [];
  return stats.value?.confidence_histogram ?? [];
});

// Tag / confidence filter writes. These used to be emits that App.vue turned
// into the same store writes; the logic lives here now (Phase 3 store-direct).
function toggleTagFilter(tag) {
  if (filterStore.tagFilter.includes(tag))
    filterStore.tagFilter = filterStore.tagFilter.filter((t) => t !== tag);
  else filterStore.tagFilter = [...filterStore.tagFilter, tag];
}

// Toggles a co-occurrence pair as one gesture: active only when every tag of
// the pair is filtered, so a click adds the missing ones or removes them all.
function toggleTagsFilter(tags) {
  const allPresent = tags.every((t) => filterStore.tagFilter.includes(t));
  if (allPresent)
    filterStore.tagFilter = filterStore.tagFilter.filter(
      (t) => !tags.includes(t),
    );
  else
    filterStore.tagFilter = [...new Set([...filterStore.tagFilter, ...tags])];
}

function toggleConfidenceAboveFilter(entry) {
  if (filterStore.tagConfidenceAboveFilter.includes(entry))
    filterStore.tagConfidenceAboveFilter =
      filterStore.tagConfidenceAboveFilter.filter((e) => e !== entry);
  else
    filterStore.tagConfidenceAboveFilter = [
      ...filterStore.tagConfidenceAboveFilter,
      entry,
    ];
}

// An empty confidence bucket stays focusable (the row is still a filter you
// could reach), but pressing it would toggle a filter that can only ever match
// nothing, so it does nothing.
function onConfBucketSelect(item, i) {
  if (item.count > 0)
    toggleConfidenceAboveFilter(
      `${selectedConfTag.value}:${(i * 0.2).toFixed(2)}`,
    );
}

function clearTagFilters(tags) {
  filterStore.tagFilter = filterStore.tagFilter.filter(
    (t) => !tags.includes(t),
  );
}

function clearConfidenceFilters(entries) {
  filterStore.tagConfidenceAboveFilter =
    filterStore.tagConfidenceAboveFilter.filter((e) => !entries.includes(e));
}

// "Unscored" is not a star, so it maps to its own filter rather than to a score
// range - the same shape as the smart-score chart's Unscored bucket below. The
// store lets a range and unscored combine (the filter menu's "include
// unscored"), but each bar here is one pick, so a bar clears the other kind.
function isScoreBarActive(label) {
  if (label === "Unscored")
    return (
      filterStore.unscoredOnlyFilter &&
      filterStore.minScoreFilter == null &&
      filterStore.maxScoreFilter == null
    );
  const n = parseInt(label);
  if (isNaN(n)) return false;
  return (
    filterStore.minScoreFilter === n &&
    filterStore.maxScoreFilter === n &&
    !filterStore.unscoredOnlyFilter
  );
}

function handleScoreBarClick(label) {
  const active = isScoreBarActive(label);
  if (label === "Unscored") {
    filterStore.minScoreFilter = null;
    filterStore.maxScoreFilter = null;
    filterStore.unscoredOnlyFilter = !active;
    return;
  }
  const n = parseInt(label);
  if (isNaN(n)) return;
  filterStore.unscoredOnlyFilter = false;
  filterStore.minScoreFilter = active ? null : n;
  filterStore.maxScoreFilter = active ? null : n;
}

const SMART_SCORE_LABEL_TO_BUCKET = {
  Unscored: "unscored",
  "1\u20132": "1-2",
  "2\u20133": "2-3",
  "3\u20134": "3-4",
  "4\u20135": "4-5",
};

function isSmartScoreBarActive(label) {
  const key = SMART_SCORE_LABEL_TO_BUCKET[label];
  if (!key) return false;
  return filterStore.smartScoreBucketFilter === key;
}

function handleSmartScoreBarClick(label) {
  const key = SMART_SCORE_LABEL_TO_BUCKET[label];
  if (!key) return;
  if (isSmartScoreBarActive(label)) {
    filterStore.smartScoreBucketFilter = null;
  } else {
    filterStore.smartScoreBucketFilter = key;
  }
}

// ─── Agreement matrix (your rating x smart score) ────────────────────────────
// Rows run 1..5 top to bottom and columns 1-2..4-5 left to right, matching the
// two histograms above so this reads as their cross-product and agreement runs
// as a band down the diagonal.
const AGREEMENT_STARS = [1, 2, 3, 4, 5];
const AGREEMENT_BUCKETS = ["1-2", "2-3", "3-4", "4-5"];
// Both axes are already the same 1-5 scale, so the buckets are compared to the
// ratings in smart-score units rather than by grid position.
const AGREEMENT_BUCKET_RANGES = {
  "1-2": [1, 2],
  "2-3": [2, 3],
  "3-4": [3, 4],
  "4-5": [4, 5],
};
// A star is a rounded smart score: rating 4 stands for anything from 3.5 to 4.5,
// so any bucket within half a point of the rating is a match.
const AGREEMENT_MATCH_RADIUS = 0.5;
// The gutter is wider than the sibling charts' 50px to make room for the
// rotated y-axis title.
const AGREEMENT_X0 = 62;
const AGREEMENT_COL_W = 49;
const AGREEMENT_CELL_W = 47; // 2px surface gap between cells
const AGREEMENT_ROW_H = 22;
const AGREEMENT_CELL_H = 20;
const AGREEMENT_HEADER_H = 14;
const AGREEMENT_AXIS_H = 15; // x-axis title strip below the grid
// Above this shade the fill is dark enough that the count needs the hue's own
// on-colour ink, the same inside/outside-the-bar switch the sibling charts make
// spatially.
const AGREEMENT_ON_FILL_SHADE = 0.55;
const AGREEMENT_CAVEAT =
  "Green cells are pictures you and the smart score agree about, within half a point, and red ones are where you disagree by more than a point and a half. The stronger the colour, the more pictures are in that cell. Pictures you rate 1 or 5 also train the smart score, so agreement at the extremes is partly built in. The interesting part is the middle rows and the cells outside the green band.";

// Traffic-light hue by how far apart the two scores are, opacity by count.
//
// The gap is measured in smart-score points, not in grid steps: a star rating is
// a rounded smart score, so rating 4 covers 3.5 to 4.5 and therefore matches BOTH
// the 3-4 and the 4-5 bucket. Comparing normalised grid positions instead made
// rating 4 a near-miss against 4-5, which is wrong: the two axes are the same
// 1-5 scale and should be compared on it.
//
// Distance is from the rating to the nearest point of the bucket's interval, so
// a rating inside the bucket is 0 apart. Ratings and bucket edges are whole
// numbers, so this only ever yields 0, 1, 2 or 3.
function agreementDisagreement(star, bucket) {
  const range = AGREEMENT_BUCKET_RANGES[bucket];
  if (!range) return 0;
  const [low, high] = range;
  return Math.max(low - star, star - high, 0);
}

function agreementTone(star, bucket) {
  const distance = agreementDisagreement(star, bucket);
  if (distance <= AGREEMENT_MATCH_RADIUS) return "good";
  if (distance <= 1 + AGREEMENT_MATCH_RADIUS) return "mixed";
  return "bad";
}

const agreement = computed(() => {
  const raw = picStats.value?.score_agreement;
  if (!raw || !Array.isArray(raw.cells) || !raw.cells.length) return null;
  return raw;
});

const agreementCounts = computed(() => {
  const map = new Map();
  for (const cell of agreement.value?.cells || []) {
    map.set(`${cell.score}|${cell.bucket}`, Number(cell.count) || 0);
  }
  return map;
});

function agreementCount(star, bucket) {
  return agreementCounts.value.get(`${star}|${bucket}`) || 0;
}

const agreementMax = computed(() => {
  let max = 0;
  for (const count of agreementCounts.value.values()) {
    if (count > max) max = count;
  }
  return max;
});

// sqrt so a single dominant cell doesn't flatten every other populated cell to
// the same near-invisible wash. Empty cells get no fill at all.
function agreementShade(star, bucket) {
  const count = agreementCount(star, bucket);
  if (!count || !agreementMax.value) return 0;
  const ratio = Math.sqrt(count / agreementMax.value);
  return 0.12 + ratio * 0.73;
}

function agreementCountOnFill(star, bucket) {
  return agreementShade(star, bucket) >= AGREEMENT_ON_FILL_SHADE;
}

function agreementCellLabel(star, bucket) {
  const count = agreementCount(star, bucket);
  const pictures = count === 1 ? "1 picture" : `${count} pictures`;
  return `${star} star, smart score ${bucket}: ${pictures}`;
}

function isAgreementCellActive(star, bucket) {
  return (
    filterStore.minScoreFilter === star &&
    filterStore.maxScoreFilter === star &&
    !filterStore.unscoredOnlyFilter &&
    filterStore.smartScoreBucketFilter === bucket
  );
}

const agreementCellSelected = computed(() =>
  AGREEMENT_STARS.some((star) =>
    AGREEMENT_BUCKETS.some((bucket) => isAgreementCellActive(star, bucket)),
  ),
);

function clearAgreementFilter() {
  filterStore.minScoreFilter = null;
  filterStore.maxScoreFilter = null;
  filterStore.smartScoreBucketFilter = null;
}

// A cell click is a compound filter: the row sets the score range, the column
// sets the smart-score bucket. Clicking the active cell clears both, matching
// the toggle behaviour of the sibling bars.
function onAgreementCellClick(star, bucket, row, col) {
  if (agreementCount(star, bucket) <= 0) return; // an empty cell filters to nothing
  agreementFocus.value = { row, col };
  if (isAgreementCellActive(star, bucket)) {
    clearAgreementFilter();
    return;
  }
  filterStore.unscoredOnlyFilter = false;
  filterStore.minScoreFilter = star;
  filterStore.maxScoreFilter = star;
  filterStore.smartScoreBucketFilter = bucket;
}

// Roving tabindex: the grid is one tab stop and arrow keys move within it, so 20
// cells don't cost 20 tab presses to skip past.
const agreementFocus = ref({ row: 0, col: 0 });

function agreementTabIndex(row, col) {
  return agreementFocus.value.row === row && agreementFocus.value.col === col
    ? 0
    : -1;
}

function focusAgreementCell(row, col) {
  agreementFocus.value = { row, col };
  nextTick(() => {
    const cells = document.querySelectorAll(
      ".agreement-grid [role='gridcell']",
    );
    cells[row * AGREEMENT_BUCKETS.length + col]?.focus?.();
  });
}

function onAgreementKeydown(event) {
  const { row, col } = agreementFocus.value;
  const lastRow = AGREEMENT_STARS.length - 1;
  const lastCol = AGREEMENT_BUCKETS.length - 1;
  let next = null;
  if (event.key === "ArrowRight")
    next = { row, col: Math.min(lastCol, col + 1) };
  else if (event.key === "ArrowLeft") next = { row, col: Math.max(0, col - 1) };
  else if (event.key === "ArrowDown")
    next = { row: Math.min(lastRow, row + 1), col };
  else if (event.key === "ArrowUp") next = { row: Math.max(0, row - 1), col };
  else if (event.key === "Home") next = { row, col: 0 };
  else if (event.key === "End") next = { row, col: lastCol };
  else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    onAgreementCellClick(
      AGREEMENT_STARS[row],
      AGREEMENT_BUCKETS[col],
      row,
      col,
    );
    return;
  }
  if (!next) return;
  event.preventDefault();
  focusAgreementCell(next.row, next.col);
}

// Signed to two decimals, so a negative coefficient reads as "the smart score
// disagrees with you" rather than as a typo.
function formatCoefficient(value) {
  if (value == null || Number.isNaN(Number(value))) return null;
  const rounded = Number(value).toFixed(2);
  return Number(rounded) > 0 ? `+${rounded}` : rounded;
}

// Both coefficients are shown with their names spelled out: they answer
// different questions (Pearson assumes the star scale is evenly spaced,
// Spearman only assumes the order), and a bare Greek letter tells the reader
// neither which is which nor what it measures.
const agreementCoefficients = computed(() => {
  const data = agreement.value;
  if (!data) return [];
  return [
    {
      key: "pearson",
      name: "Pearson r",
      value: formatCoefficient(data.pearson),
      title:
        "Pearson's r: straight-line correlation. Treats the gap between each star as equal.",
    },
    {
      key: "spearman",
      name: "Spearman ρ",
      value: formatCoefficient(data.spearman),
      title:
        "Spearman's rho: rank correlation. Only assumes more stars means better, not that the steps are even.",
    },
  ].filter((entry) => entry.value !== null);
});

const agreementNoCoefficientLabel = computed(() =>
  (agreement.value?.pairs || 0) > 0
    ? "Rate a few more pictures to see the correlation"
    : "Rate some pictures to compare them with the smart score",
);

const agreementCoverage = computed(() => {
  const data = agreement.value;
  if (!data) return "";
  const rated = Number(data.rated) || 0;
  const total = Number(data.total) || 0;
  if (!total) return "No pictures in view";
  const pct = Math.round((rated / total) * 100);
  return `${rated.toLocaleString()} of ${total.toLocaleString()} rated (${pct}%)`;
});

const RESOLUTION_LABEL_TO_BUCKET = {
  Unknown: "unknown",
  "<1 MP": "lt1mp",
  "1\u20134 MP": "1-4mp",
  "4\u20138 MP": "4-8mp",
  "8\u201316 MP": "8-16mp",
  "16+ MP": "16plus",
};

function isResolutionBarActive(label) {
  const key = RESOLUTION_LABEL_TO_BUCKET[label];
  if (!key) return false;
  return filterStore.resolutionBucketFilter === key;
}

function handleResolutionBarClick(label) {
  const key = RESOLUTION_LABEL_TO_BUCKET[label];
  if (!key) return;
  if (isResolutionBarActive(label)) {
    filterStore.resolutionBucketFilter = null;
  } else {
    filterStore.resolutionBucketFilter = key;
  }
}
</script>

<template>
  <AppInspector
    v-model="activeTab"
    class="stats-sidebar"
    label="Inspector"
    :open="sidebarStore.statsOpen"
    :tabs="railMode === 'none' ? tabs : []"
  >
    <!-- One head row on the toolbar's line (#2161): what the rail is about -
         the view, the one picture, or how many - and, while anything is
         selected, Esc's keycap and the selection-off icon that clears it.
         The close button always ends the row. -->
    <template #head>
      <h2 ref="titleRef" class="rail-title" data-testid="rail-title">
        <Tooltip
          v-if="head.full && head.full !== shownTitle"
          :text="head.full"
          activator="parent"
        />
        <!-- A description's sentence: pointing at it marks it there, and a
             click selects it. -->
        <button
          v-if="head.fromDescription"
          class="rail-title__quote"
          type="button"
          @mouseenter="titleHovered = true"
          @mouseleave="titleHovered = false"
          @focus="titleHovered = true"
          @blur="titleHovered = false"
          @vue:unmounted="titleHovered = false"
          @click="railPictureRef?.selectInDescription(head.title)"
        >
          {{ shownTitle }}
        </button>
        <template v-else>{{ shownTitle }}</template>
      </h2>
      <template v-if="selectedIds.length">
        <kbd class="fm-kbd rail-esc" aria-hidden="true">Esc</kbd>
        <AppButton
          class="rail-clear"
          variant="ghost"
          size="sm"
          icon-left="selection-off"
          icon-only
          tooltip="Clear the selection · Esc"
          aria-keyshortcuts="Escape"
          @click="selectionStore.selectedImageIds = []"
        />
      </template>
      <AppButton
        class="rail-close"
        variant="ghost"
        size="sm"
        icon-left="close"
        icon-only
        tooltip="Close this panel"
        @click="closeRail"
      />
    </template>
    <template v-if="railMode === 'none' && stats" #lead>
      <div class="stats-tiles">
        <div class="stats-tile">
          <span class="stats-tile-value">{{
            stats.total.toLocaleString()
          }}</span>
          <span class="stats-tile-label">Pictures</span>
        </div>
        <div class="stats-tile">
          <span class="stats-tile-value">{{
            (stats.total_tags ?? 0).toLocaleString()
          }}</span>
          <span class="stats-tile-label">Tags</span>
        </div>
        <div class="stats-tile">
          <span class="stats-tile-value">{{
            stats.avg_tags_per_image.toFixed(1)
          }}</span>
          <span class="stats-tile-label">Tags / picture</span>
        </div>
      </div>
    </template>

    <RailPicture
      v-if="railMode === 'one' && !covered"
      ref="railPictureRef"
      v-model:image="oneImage"
      v-model:faces="oneFaces"
      :picture-id="oneId"
      :card="oneCard"
      :score="oneCard ? scoreOf(oneCard) : 0"
      :favouritable="!isReadOnly && !isScrapheapView"
      :description-highlight="titleHovered && head.fromDescription ? head.title : ''"
      @set-score="(n) => oneCard && emit('set-score', oneCard, n)"
      @toggle-favourite="emit('toggle-favourite', [oneId])"
      @update-description="(id, text) => emit('update-description', id, text)"
      @lightbox-change="emit('lightbox-change', $event)"
      @tags-applied="emit('tags-applied', $event)"
      @refresh="refreshOne"
    />
    <RailPictures
      v-else-if="railMode === 'many' && !covered"
      :ids="selectedIds"
      :cards="pictures"
      @tags-applied="emit('tags-applied', $event)"
    />
    <template v-else-if="railMode === 'none'">
    <div v-if="loading && !stats" class="stats-loading">
      <v-progress-circular indeterminate size="24" width="2" color="primary" />
    </div>

    <div v-else-if="error" class="stats-error">{{ error }}</div>

    <template v-else-if="stats && activeTab === 'tags'">
      <!-- Overview section -->
      <div class="inspector-section">
        <!-- Donut chart -->
        <SectionHeader
          v-model:collapsed="taggedCollapsed"
          label="Tagged pictures"
        />
        <div v-if="!taggedCollapsed" class="stats-donut-wrap">
          <svg
            :width="DONUT_CX * 2"
            :height="DONUT_CY * 2"
            class="stats-donut"
            aria-label="Tagged vs untagged"
          >
            <!-- background track -->
            <circle
              :cx="DONUT_CX"
              :cy="DONUT_CY"
              :r="DONUT_R"
              fill="none"
              :stroke-width="DONUT_STROKE"
              class="donut-track"
            />
            <!-- untagged segment -->
            <circle
              v-if="stats.total > 0"
              :cx="DONUT_CX"
              :cy="DONUT_CY"
              :r="DONUT_R"
              fill="none"
              :stroke-width="DONUT_STROKE"
              :stroke-dasharray="donutUntaggedDash"
              :stroke-dashoffset="donutUntaggedOffset"
              class="donut-untagged"
              transform="rotate(-90, 56, 56)"
            />
            <!-- tagged segment -->
            <circle
              v-if="stats.tagged > 0"
              :cx="DONUT_CX"
              :cy="DONUT_CY"
              :r="DONUT_R"
              fill="none"
              :stroke-width="DONUT_STROKE"
              :stroke-dasharray="donutTaggedDash"
              class="donut-tagged"
              transform="rotate(-90, 56, 56)"
            />
            <text
              :x="DONUT_CX"
              :y="DONUT_CY + 5"
              text-anchor="middle"
              class="donut-label"
            >
              {{
                stats.total > 0
                  ? Math.round((stats.tagged / stats.total) * 100) + "%"
                  : "—"
              }}
            </text>
          </svg>
          <div class="donut-legend">
            <span class="legend-dot tagged-dot" />
            <span class="legend-text"
              >Tagged {{ stats.tagged.toLocaleString() }}</span
            >
            <span class="legend-dot untagged-dot" />
            <span class="legend-text"
              >Untagged {{ stats.untagged.toLocaleString() }}</span
            >
          </div>
        </div>
      </div>

      <!-- Top tags section -->
      <div
        v-if="stats.top_tags.length || penalisedOnlyTags"
        class="inspector-section"
      >
        <SectionHeader v-model:collapsed="topTagsCollapsed" label="Top tags">
          <template #actions>
          <button
            v-if="hasPenalisedTags && !topTagsCollapsed"
            class="penalised-toggle"
            :class="{ active: penalisedOnlyTags }"
            type="button"
            @click="penalisedOnlyTags = !penalisedOnlyTags"
          >
            <Tooltip text="Show penalised tags only" activator="parent" />
            <v-icon size="11">mdi-alert-circle-outline</v-icon>
            penalised
          </button>
          <button
            v-if="!topTagsCollapsed && activeTagsInTopTags().length > 0"
            class="stats-clear-btn"
            type="button"
            aria-label="Clear top-tag filters"
            @click="clearTagFilters(activeTagsInTopTags())"
          >
            <Tooltip
              text="Clear top-tag filters"
              activator="parent"
              :describe="false"
            />
            <v-icon size="11">mdi-close</v-icon>
          </button>
          </template>
        </SectionHeader>
        <div v-if="!topTagsCollapsed" class="stats-bars">
          <svg
            :width="260"
            :height="displayedTags.length * 18 + 4"
            class="stats-bar-chart"
            aria-label="Top tags bar chart"
          >
            <g
              v-for="(item, i) in displayedTags"
              :key="item.tag"
              class="bar-row"
              :class="{
                'bar-penalised': isPenalised(item.tag),
                'bar-row--active': isTagActive(item.tag),
              }"
              :transform="`translate(0, ${i * 18})`"
              role="button"
              tabindex="0"
              @click="toggleTagFilter(item.tag)"
              @keydown.enter="toggleTagFilter(item.tag)"
            >
              <title>{{ item.tag }}</title>
              <rect
                x="0"
                y="2"
                :width="(barWidth(item.count) / 100) * 140"
                height="13"
                rx="2"
                class="bar-rect"
              />
              <text
                v-if="(barWidth(item.count) / 100) * 140 >= 40"
                :x="(barWidth(item.count) / 100) * 140 - 3"
                y="9"
                text-anchor="end"
                class="bar-count-inner"
              >
                {{ item.count }}
              </text>
              <text
                v-else-if="item.count > 0"
                :x="(barWidth(item.count) / 100) * 140 + 3"
                y="9"
                text-anchor="start"
                class="bar-count-outer"
              >
                {{ item.count }}
              </text>
              <foreignObject x="148" y="1" width="110" height="16">
                <div class="bar-label-fo">{{ item.tag }}</div>
              </foreignObject>
            </g>
          </svg>
        </div>
      </div>

      <!-- Co-occurrence section -->
      <div v-if="stats.total > 0" class="inspector-section">
        <SectionHeader
          v-model:collapsed="coocCollapsed"
          label="Co-occurrences"
        >
          <template #actions>
          <button
            v-if="hasPenalisedTags && coocOpen"
            class="penalised-toggle"
            :class="{ active: penalisedOnlyCooc > 0 }"
            type="button"
            @click="penalisedOnlyCooc = (penalisedOnlyCooc + 1) % 3"
          >
            <Tooltip
              :text="COOC_FILTER_TITLES[penalisedOnlyCooc]"
              activator="parent"
            />
            <v-icon size="11">mdi-alert-circle-outline</v-icon>
            {{
              penalisedOnlyCooc === 0
                ? "penalised"
                : penalisedOnlyCooc === 1
                  ? "one penalised"
                  : "both penalised"
            }}
          </button>
          <button
            v-if="coocOpen && activeTagsInCooc().length > 0"
            class="stats-clear-btn"
            type="button"
            aria-label="Clear co-occurrence filters"
            @click="clearTagFilters(activeTagsInCooc())"
          >
            <Tooltip
              text="Clear co-occurrence filters"
              activator="parent"
              :describe="false"
            />
            <v-icon size="11">mdi-close</v-icon>
          </button>
          </template>
        </SectionHeader>
        <div v-if="coocOpen" class="stats-cooc-list">
          <div
            v-for="(item, i) in displayedCooc"
            :key="i"
            class="cooc-item"
            :class="{
              'cooc-penalised':
                isPenalised(item.tags[0]) || isPenalised(item.tags[1]),
              'cooc-item--active': isCoocActive(item.tags),
            }"
            role="button"
            tabindex="0"
            @click="toggleTagsFilter(item.tags)"
            @keydown.enter="toggleTagsFilter(item.tags)"
          >
            <span class="cooc-tags">
              <span :class="{ 'tag-penalised': isPenalised(item.tags[0]) }">{{
                item.tags[0]
              }}</span>
              <span class="cooc-sep"> + </span>
              <span :class="{ 'tag-penalised': isPenalised(item.tags[1]) }">{{
                item.tags[1]
              }}</span>
            </span>
            <span class="cooc-count">{{ item.count }}</span>
          </div>
          <div v-if="displayedCooc.length === 0" class="cooc-empty">
            No penalised pairs
          </div>
        </div>
      </div>

      <!-- Confidence distribution section -->
      <div v-if="stats.total > 0" class="inspector-section">
        <SectionHeader
          v-model:collapsed="confHistCollapsed"
          label="Tag confidence"
        >
          <template #actions>
          <div class="conf-tag-selector">
            <v-progress-circular
              v-if="confTagLoading"
              indeterminate
              size="10"
              width="1"
              color="primary"
              class="conf-tag-spinner"
            />
            <Tooltip text="Filter by tag" :describe="false">
              <template #activator="{ props: tipProps }">
                <select
                  v-model="selectedConfTag"
                  class="conf-tag-select"
                  v-bind="tipProps"
                  aria-label="Filter by tag"
                >
                  <option :value="null">All tags</option>
                  <optgroup
                    v-if="anomalyTagOptions.length"
                    label="Anomaly tags"
                  >
                    <option
                      v-for="tag in anomalyTagOptions"
                      :key="'a:' + tag"
                      :value="tag"
                    >
                      {{ tag }}
                    </option>
                  </optgroup>
                  <optgroup v-if="regularTags.length" label="Regular tags">
                    <option
                      v-for="tag in regularTags"
                      :key="'r:' + tag"
                      :value="tag"
                    >
                      {{ tag }}
                    </option>
                  </optgroup>
                </select>
              </template>
            </Tooltip>
            <button
              v-if="confHistOpen && activeConfEntries().length > 0"
              class="stats-clear-btn"
              type="button"
              aria-label="Clear confidence filters"
              @click="clearConfidenceFilters(activeConfEntries())"
            >
              <Tooltip
                text="Clear confidence filters"
                activator="parent"
                :describe="false"
              />
              <v-icon size="11">mdi-close</v-icon>
            </button>
          </div>
          </template>
        </SectionHeader>
        <div v-if="confHistOpen" class="stats-hist">
          <StatsHistogram
            :buckets="confHistBuckets"
            aria-label="Tag confidence distribution"
            fill="tertiary"
            :interactive="() => !!selectedConfTag"
            :active="(item, i) => isConfEntryActive(i)"
            :row-label="
              (item, i) => `Filter: ${selectedConfTag} \u2265 ${i * 20}%`
            "
            @select="onConfBucketSelect"
          />
        </div>
      </div>
    </template>

    <!-- ── Pictures tab ──────────────────────────────────────────────── -->
    <template v-if="activeTab === 'pictures'">
      <div v-if="picStatsLoading && !picStats" class="stats-loading">
        <v-progress-circular
          indeterminate
          size="24"
          width="2"
          color="primary"
        />
      </div>
      <template v-else-if="picStats">
        <!-- Manual score distribution -->
        <div class="inspector-section">
          <SectionHeader v-model:collapsed="scoreCollapsed" label="Score">
            <template #actions>
            <button
              v-if="
                filterStore.minScoreFilter != null ||
                filterStore.maxScoreFilter != null ||
                filterStore.unscoredOnlyFilter
              "
              class="stats-clear-btn"
              type="button"
              aria-label="Clear score filter"
              @click="
                filterStore.minScoreFilter = null;
                filterStore.maxScoreFilter = null;
                filterStore.unscoredOnlyFilter = false;
              "
            >
              <Tooltip
                text="Clear score filter"
                activator="parent"
                :describe="false"
              />
              <v-icon size="11">mdi-close</v-icon>
            </button>
            </template>
          </SectionHeader>
          <div v-if="!scoreCollapsed" class="stats-hist">
            <StatsHistogram
              :buckets="picStats.score_distribution"
              aria-label="Manual score distribution"
              fill="secondary"
              :active="(item) => isScoreBarActive(item.label)"
              @select="(item) => handleScoreBarClick(item.label)"
            />
          </div>
        </div>

        <!-- Smart score distribution -->
        <div class="inspector-section">
          <SectionHeader v-model:collapsed="smartCollapsed" label="Smart score">
            <template #actions>
            <button
              v-if="filterStore.smartScoreBucketFilter != null"
              class="stats-clear-btn"
              type="button"
              aria-label="Clear smart score filter"
              @click="filterStore.smartScoreBucketFilter = null"
            >
              <Tooltip
                text="Clear smart score filter"
                activator="parent"
                :describe="false"
              />
              <v-icon size="11">mdi-close</v-icon>
            </button>
            </template>
          </SectionHeader>
          <div v-if="!smartCollapsed" class="stats-hist">
            <StatsHistogram
              :buckets="picStats.smart_score_distribution"
              aria-label="Smart score distribution"
              fill="primary"
              :active="(item) => isSmartScoreBarActive(item.label)"
              @select="(item) => handleSmartScoreBarClick(item.label)"
            />
          </div>
        </div>

        <!-- Smart score vs your rating (agreement matrix) -->
        <div v-if="agreement" class="inspector-section">
          <SectionHeader
            v-model:collapsed="agreementCollapsed"
            label="Agreement"
          >
            <template #actions>
            <span
              class="stats-info-dot"
              tabindex="0"
              role="note"
              :aria-label="AGREEMENT_CAVEAT"
            >
              <Tooltip
                :text="AGREEMENT_CAVEAT"
                activator="parent"
                :describe="false"
              />
              <v-icon size="11">mdi-information-outline</v-icon>
            </span>
            <button
              v-if="agreementCellSelected"
              class="stats-clear-btn"
              type="button"
              aria-label="Clear agreement filter"
              @click="clearAgreementFilter"
            >
              <Tooltip
                text="Clear agreement filter"
                activator="parent"
                :describe="false"
              />
              <v-icon size="11">mdi-close</v-icon>
            </button>
            </template>
          </SectionHeader>
          <template v-if="!agreementCollapsed">
          <div v-if="agreement.pairs > 0" class="stats-hist">
            <svg
              :width="260"
              :height="
                AGREEMENT_ROW_H * 5 + AGREEMENT_HEADER_H + AGREEMENT_AXIS_H + 2
              "
              class="stats-bar-chart agreement-grid"
              role="grid"
              aria-label="Your rating against smart score"
              @keydown="onAgreementKeydown"
            >
              <!-- Axis titles. The y title is rotated up the left edge; the x
                     title sits under the grid, both in the recessive label ink. -->
              <text
                :x="-(AGREEMENT_HEADER_H + (AGREEMENT_ROW_H * 5) / 2)"
                y="9"
                transform="rotate(-90)"
                text-anchor="middle"
                class="hist-axis-title"
              >
                Your rating
              </text>
              <text
                :x="AGREEMENT_X0 + (AGREEMENT_COL_W * 4) / 2"
                :y="AGREEMENT_HEADER_H + AGREEMENT_ROW_H * 5 + 11"
                text-anchor="middle"
                class="hist-axis-title"
              >
                Smart score
              </text>
              <text
                v-for="(bucket, col) in AGREEMENT_BUCKETS"
                :key="`col-${bucket}`"
                :x="AGREEMENT_X0 + col * AGREEMENT_COL_W + AGREEMENT_CELL_W / 2"
                y="9"
                text-anchor="middle"
                class="hist-label"
              >
                {{ bucket }}
              </text>
              <g
                v-for="(star, row) in AGREEMENT_STARS"
                :key="`row-${star}`"
                role="row"
                :transform="`translate(0, ${AGREEMENT_HEADER_H + row * AGREEMENT_ROW_H})`"
              >
                <text x="46" y="14" text-anchor="end" class="hist-label">
                  {{ star }}
                </text>
                <g
                  v-for="(bucket, col) in AGREEMENT_BUCKETS"
                  :key="`cell-${star}-${bucket}`"
                  role="gridcell"
                  :class="[
                    'agreement-cell',
                    `agreement-cell--${agreementTone(star, bucket)}`,
                    {
                      'agreement-cell--interactive':
                        agreementCount(star, bucket) > 0,
                      'agreement-cell--selected': isAgreementCellActive(
                        star,
                        bucket,
                      ),
                    },
                  ]"
                  :tabindex="agreementTabIndex(row, col)"
                  :aria-selected="isAgreementCellActive(star, bucket)"
                  :aria-label="agreementCellLabel(star, bucket)"
                  @click="onAgreementCellClick(star, bucket, row, col)"
                  @focus="agreementFocus = { row, col }"
                >
                  <rect
                    :x="AGREEMENT_X0 + col * AGREEMENT_COL_W"
                    y="2"
                    :width="AGREEMENT_CELL_W"
                    :height="AGREEMENT_CELL_H"
                    rx="2"
                    class="agreement-cell-rect"
                    :style="{ opacity: agreementShade(star, bucket) }"
                  />
                  <rect
                    :x="AGREEMENT_X0 + col * AGREEMENT_COL_W"
                    y="2"
                    :width="AGREEMENT_CELL_W"
                    :height="AGREEMENT_CELL_H"
                    rx="2"
                    class="agreement-cell-outline"
                  />
                  <text
                    v-if="agreementCount(star, bucket) > 0"
                    :x="
                      AGREEMENT_X0 +
                      col * AGREEMENT_COL_W +
                      AGREEMENT_CELL_W / 2
                    "
                    :y="2 + AGREEMENT_CELL_H / 2 + 4"
                    text-anchor="middle"
                    :class="[
                      'agreement-count',
                      {
                        'agreement-count--on-fill': agreementCountOnFill(
                          star,
                          bucket,
                        ),
                      },
                    ]"
                  >
                    {{ agreementCount(star, bucket) }}
                  </text>
                </g>
              </g>
            </svg>
          </div>
          <div class="agreement-summary">
            <dl v-if="agreementCoefficients.length" class="agreement-stats">
              <template v-for="stat in agreementCoefficients" :key="stat.key">
                <dt class="agreement-stat-name">
                  <Tooltip :text="stat.title" activator="parent" />
                  {{ stat.name }}
                </dt>
                <dd class="agreement-stat-value">{{ stat.value }}</dd>
              </template>
            </dl>
            <span v-else class="agreement-stat-empty">
              {{ agreementNoCoefficientLabel }}
            </span>
            <span class="agreement-coverage">{{ agreementCoverage }}</span>
          </div>
          </template>
        </div>

        <!-- Resolution distribution -->
        <div class="inspector-section">
          <SectionHeader v-model:collapsed="resolutionCollapsed" label="Resolution">
            <template #actions>
            <button
              v-if="filterStore.resolutionBucketFilter != null"
              class="stats-clear-btn"
              type="button"
              aria-label="Clear resolution filter"
              @click="filterStore.resolutionBucketFilter = null"
            >
              <Tooltip
                text="Clear resolution filter"
                activator="parent"
                :describe="false"
              />
              <v-icon size="11">mdi-close</v-icon>
            </button>
            </template>
          </SectionHeader>
          <div v-if="!resolutionCollapsed" class="stats-hist">
            <StatsHistogram
              :buckets="picStats.resolution_distribution"
              aria-label="Resolution distribution"
              fill="tertiary"
              :active="(item) => isResolutionBarActive(item.label)"
              @select="(item) => handleResolutionBarClick(item.label)"
            />
          </div>
        </div>
      </template>
    </template>
    </template>
  </AppInspector>
</template>

<style scoped>
.stats-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-6) 0;
}

.stats-error {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-surface-error), 1);
  padding: var(--space-3) 0;
}

/* Three tiles across the rail (#2161): pictures, tags, tags per picture. */
.stats-tiles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
}

.stats-tile {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: rgba(var(--v-theme-on-surface), 0.05);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-2);
}

.stats-tile-value {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  line-height: 1.1;
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.stats-tile-label {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  margin-top: var(--space-1);
  text-align: center;
}

/* The head's title: one line, ellipsised; the whole of it is in the tip. */
.rail-title {
  flex: 1;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.rail-title__quote {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  color: inherit;
  text-align: left;
}

.rail-esc {
  flex: none;
  margin-left: var(--space-2);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-clear,
.rail-close {
  flex: none;
}

.stats-donut-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}

.stats-donut {
  display: block;
  overflow: visible;
}

.donut-track {
  stroke: rgba(var(--v-theme-on-surface), 0.1);
}

.donut-tagged {
  stroke: rgba(var(--v-theme-secondary), 0.85);
  stroke-linecap: butt;
}

.donut-untagged {
  stroke: rgba(var(--v-theme-on-surface), 0.22);
  stroke-linecap: butt;
}

.donut-label {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  fill: rgba(var(--v-theme-on-surface), 0.85);
}

.donut-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
  justify-content: center;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}

.tagged-dot {
  background: rgba(var(--v-theme-secondary), 0.85);
}

.untagged-dot {
  background: rgba(var(--v-theme-on-surface), 0.22);
}

.legend-text {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.penalised-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-pill);
  border: 1px solid rgba(var(--v-theme-surface-warning), 0.4);
  color: rgba(var(--v-theme-on-surface), 0.45);
  transition:
    background 0.12s,
    color 0.12s,
    border-color 0.12s;
  white-space: nowrap;
}
.penalised-toggle:hover {
  background: rgba(var(--v-theme-warning), 0.1);
  color: rgba(var(--v-theme-surface-warning), 1);
  border-color: rgba(var(--v-theme-surface-warning), 0.7);
}
.penalised-toggle.active {
  background: rgba(var(--v-theme-warning), 0.18);
  color: rgba(var(--v-theme-surface-warning), 1);
  border-color: rgba(var(--v-theme-surface-warning), 0.7);
}

.stats-bars {
  overflow-x: hidden;
}

.stats-bar-chart {
  display: block;
  overflow: visible;
}

.bar-row {
  cursor: pointer;
}

.bar-row:hover .bar-rect {
  opacity: 0.85;
}

.bar-rect {
  fill: rgba(var(--v-theme-primary), 0.65);
  transition: opacity 0.12s;
}

.bar-row--active .bar-rect {
  stroke: var(--active-bar);
  stroke-width: 1.5;
  opacity: 1;
}

.bar-penalised .bar-rect {
  fill: rgba(var(--v-theme-warning), 0.65);
}

.bar-label-fo {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.75);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 16px;
  height: 16px;
  pointer-events: none;
  user-select: none;
}

.bar-count-inner {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  fill: rgb(var(--v-theme-on-surface));
  dominant-baseline: central;
  pointer-events: none;
}

.bar-count-outer {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  fill: rgba(var(--v-theme-on-surface), 0.65);
  dominant-baseline: central;
  pointer-events: none;
}

.stats-cooc-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-1);
}

.cooc-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  cursor: pointer;
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-1);
}
.cooc-item:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.cooc-item--active {
  background: var(--active-wash);
  color: var(--active-text);
}

.tag-penalised {
  color: rgba(var(--v-theme-surface-warning), 1);
}

.cooc-sep {
  color: rgba(var(--v-theme-on-surface), 0.35);
}

.cooc-empty {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.35);
  font-style: italic;
}

.cooc-tags {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: var(--space-3);
}

.cooc-count {
  color: rgba(var(--v-theme-on-surface), 0.4);
  flex-shrink: 0;
  font-size: var(--text-2xs);
}

.stats-hist {
  overflow-x: hidden;
}

.hist-label {
  font-size: var(--text-2xs);
  fill: rgba(var(--v-theme-on-surface), 0.6);
  dominant-baseline: central;
}

.stats-clear-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), 0.45);
  flex-shrink: 0;
}
.stats-clear-btn:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.conf-tag-selector {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.conf-tag-spinner {
  flex-shrink: 0;
}

.conf-tag-select {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  background: rgba(var(--v-theme-on-surface), 0.06);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
  cursor: pointer;
  max-width: 108px;
  appearance: none;
}
/* The popup list is native chrome: without an explicit fill it paints the
   OS default (white) under the select's on-surface text in dark mode. */
.conf-tag-select option,
.conf-tag-select optgroup {
  background-color: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}
.conf-tag-select:hover {
  border-color: rgba(var(--v-theme-on-surface), 0.3);
}

/* ── Agreement matrix ──────────────────────────────────────────────────────
   Composite encoding: HUE is the traffic light (how far the cell sits from
   agreement, fixed by its position in the grid), OPACITY is how many pictures
   are in it. The hue is redundant with position and every populated cell prints
   its count, so nothing here is carried by colour alone - which is what makes a
   red/green pair acceptable for colour-blind readers. Status hues are the
   success/warning/error FILLS, one value in both themes, never the lifted
   `surface-*` text family: the counts below sit on them. The fill IS the value, so hover
   must not change it: hover and focus ring the cell instead. */
.agreement-cell-rect {
  fill: rgb(var(--v-theme-primary));
}
.agreement-cell--good .agreement-cell-rect {
  fill: rgb(var(--v-theme-success));
}
.agreement-cell--mixed .agreement-cell-rect {
  fill: rgb(var(--v-theme-warning));
}
.agreement-cell--bad .agreement-cell-rect {
  fill: rgb(var(--v-theme-error));
}
/* Once the fill is strong enough to carry it, the count switches to that hue's
   own label ink (4.57:1+ on its fill, the same in both themes). */
.agreement-cell--good .agreement-count--on-fill {
  fill: rgb(var(--v-theme-on-success));
}
.agreement-cell--mixed .agreement-count--on-fill {
  fill: rgb(var(--v-theme-on-warning));
}
.agreement-cell--bad .agreement-count--on-fill {
  fill: rgb(var(--v-theme-on-error));
}
.agreement-cell-outline {
  fill: none;
  stroke: rgba(var(--v-theme-on-surface), 0.12);
  stroke-width: 1;
}
.agreement-cell--interactive {
  cursor: pointer;
}
.agreement-cell--interactive:hover .agreement-cell-outline {
  stroke: rgba(var(--v-theme-on-surface), 0.45);
}
.agreement-cell--selected .agreement-cell-outline {
  stroke: var(--active-bar);
  stroke-width: 2;
}
/* The indicator is the SVG stroke below, not a ring: opt out of both halves of
   the app-wide `:focus-visible` rule in style.css. */
.agreement-cell:focus-visible {
  outline: none;
  box-shadow: none;
}
.agreement-cell:focus-visible .agreement-cell-outline {
  stroke: var(--focus-stroke);
  stroke-width: 2;
}
.agreement-count {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  fill: rgba(var(--v-theme-on-surface), 0.75);
  pointer-events: none;
}
.agreement-count--on-fill {
  fill: rgba(var(--v-theme-on-primary), 0.9);
}
.agreement-summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding-top: var(--space-2);
}
/* Name / value pairs, so both coefficients line up on the value column instead
   of running together as one sentence. */
.agreement-stats {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--space-1) var(--space-3);
  margin: 0;
}
.agreement-stat-name {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  cursor: help;
}
.agreement-stat-value {
  margin: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.85);
  text-align: right;
}
.agreement-stat-empty {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.hist-axis-title {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  fill: rgba(var(--v-theme-on-surface), 0.45);
  letter-spacing: 0.04em;
}
.agreement-coverage {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.stats-info-dot {
  display: inline-flex;
  align-items: center;
  margin-left: var(--space-1);
  color: rgba(var(--v-theme-on-surface), 0.45);
  border-radius: var(--radius-sm);
  cursor: help;
}
</style>
