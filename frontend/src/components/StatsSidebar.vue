<script setup>
import { ref, watch, computed, onMounted } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  backendUrl: { type: String, required: true },
  // Filter props — mirrors the same params ImageGrid uses for fetching pictures
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedCharacterIds: { type: Array, default: () => [] },
  characterMode: { type: String, default: "union" },
  selectedSet: { type: [Number, String, null], default: null },
  selectedSetIds: { type: Array, default: () => [] },
  setMode: { type: String, default: "union" },
  setDifferenceBaseId: { type: Number, default: null },
  projectViewMode: { type: String, default: null },
  selectedProjectId: { type: [Number, String, null], default: null },
  tagFilter: { type: Array, default: () => [] },
  tagRejectedFilter: { type: Array, default: () => [] },
  mediaTypeFilter: { type: String, default: null },
  minScoreFilter: { type: [Number, String, null], default: null },
  maxScoreFilter: { type: [Number, String, null], default: null },
  smartScoreBucketFilter: { type: [String, null], default: null },
  resolutionBucketFilter: { type: [String, null], default: null },
  filePathPrefixFilter: { type: String, default: null },
  allPicturesId: { type: [String, Number], default: null },
  unassignedPicturesId: { type: [String, Number], default: null },
  scrapheapPicturesId: { type: [String, Number], default: null },
  penalisedTagWeights: { type: Object, default: () => ({}) },
  faceBboxFilter: { type: String, default: null },
  tagConfidenceAboveFilter: { type: Array, default: () => [] },
  tagConfidenceBelowFilter: { type: Array, default: () => [] },
  open: { type: Boolean, default: true },
  wsTagUpdate: { type: Object, default: null },
});

const emit = defineEmits([
  "filter-tag",
  "filter-tags",
  "filter-confidence-above",
  "clear-tag-filter",
  "clear-confidence-filter",
  "update:minScoreFilter",
  "update:maxScoreFilter",
  "update:smartScoreBucketFilter",
  "update:resolutionBucketFilter",
]);

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

// Section collapse state
const topTagsOpen = ref(true);
const coocOpen = ref(false);
const confHistOpen = ref(false);
const tagCountHistOpen = ref(false);

// Tab state
const activeTab = ref("tags");

// Picture stats
const picStats = ref(null);
const picStatsLoaded = ref(false);
const picStatsLoading = ref(false);

async function fetchPicStats() {
  const qs = buildQueryParams();
  picStatsLoading.value = true;
  try {
    const res = await apiClient.get(
      `/pictures/stats?${qs ? qs + "&" : ""}include=picture`,
    );
    picStats.value = res.data;
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
    const res = await apiClient.get(
      `/pictures/stats?${qs ? qs + "&" : ""}include=conf&confidence_tag=${encodeURIComponent(tag)}`,
    );
    confTagData.value = res.data;
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

  const normalizedSetIds = Array.isArray(props.selectedSetIds)
    ? props.selectedSetIds
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0)
    : [];
  const hasSetSelection = !!(
    (props.selectedSet != null && props.selectedSet !== "") ||
    normalizedSetIds.length > 0
  );
  const isSetOverlap = normalizedSetIds.length > 1;
  const primarySetId =
    !isSetOverlap && props.selectedSet != null && props.selectedSet !== ""
      ? Number(props.selectedSet)
      : null;

  if (hasSetSelection) {
    if (isSetOverlap) {
      for (const id of normalizedSetIds) params.append("set_ids", String(id));
      params.append("set_mode", props.setMode || "intersection");
      if (props.setMode === "difference" && props.setDifferenceBaseId != null) {
        params.append("base_set_id", String(props.setDifferenceBaseId));
      }
    } else if (primarySetId != null) {
      params.append("set_id", String(primarySetId));
    }
    if (props.projectViewMode === "project") {
      params.append(
        "project_id",
        props.selectedProjectId != null
          ? props.selectedProjectId
          : "UNASSIGNED",
      );
    }
  } else if (
    props.selectedCharacter != null &&
    props.selectedCharacter !== "" &&
    props.selectedCharacter !== props.allPicturesId
  ) {
    const normalizedCharIds = Array.isArray(props.selectedCharacterIds)
      ? props.selectedCharacterIds.map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0)
      : [];
    if (normalizedCharIds.length > 1) {
      for (const id of normalizedCharIds) params.append("character_ids", String(id));
      params.append("character_mode", props.characterMode || "union");
    } else {
      params.append("character_id", props.selectedCharacter);
    }
    if (props.projectViewMode === "project") {
      params.append(
        "project_id",
        props.selectedProjectId != null
          ? props.selectedProjectId
          : "UNASSIGNED",
      );
    }
  } else if (
    props.selectedCharacter === props.allPicturesId &&
    props.projectViewMode === "project"
  ) {
    params.append(
      "project_id",
      props.selectedProjectId != null ? props.selectedProjectId : "UNASSIGNED",
    );
  }

  if (props.mediaTypeFilter === "images") {
    for (const ext of PIL_IMAGE_EXTENSIONS)
      params.append("format", ext.toUpperCase());
  } else if (props.mediaTypeFilter === "videos") {
    for (const ext of VIDEO_EXTENSIONS)
      params.append("format", ext.toUpperCase());
  }

  if (props.minScoreFilter != null)
    params.append("min_score", props.minScoreFilter);
  if (props.maxScoreFilter != null)
    params.append("max_score", props.maxScoreFilter);
  if (props.smartScoreBucketFilter != null)
    params.append("smart_score_bucket", props.smartScoreBucketFilter);
  if (props.resolutionBucketFilter != null)
    params.append("resolution_bucket", props.resolutionBucketFilter);
  if (props.filePathPrefixFilter != null)
    params.append("file_path_prefix", props.filePathPrefixFilter);
  (props.tagFilter || []).forEach((t) => params.append("tag", t));
  (props.tagRejectedFilter || []).forEach((t) =>
    params.append("rejected_tag", t),
  );
  if (props.faceBboxFilter != null) {
    params.append("face_filter", props.faceBboxFilter);
  }
  (props.tagConfidenceAboveFilter || []).forEach((e) =>
    params.append("tag_confidence_above", e),
  );
  (props.tagConfidenceBelowFilter || []).forEach((e) =>
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
    const res = await apiClient.get(`/pictures/stats${qs ? `?${qs}` : ""}`);
    stats.value = { ...res.data, regular_tags: prevRegularTags };
  } catch (e) {
    error.value = "Failed to load stats";
    stats.value = null;
  } finally {
    loading.value = false;
  }
}

// Lazy data for heavy sections
const coocLoaded = ref(false);
const confLoaded = ref(false);

async function fetchCooc() {
  const qs = buildQueryParams();
  try {
    const res = await apiClient.get(
      `/pictures/stats?${qs ? qs + "&" : ""}include=cooc`,
    );
    if (stats.value)
      stats.value = {
        ...stats.value,
        top_cooccurrences: res.data.top_cooccurrences,
      };
    coocLoaded.value = true;
  } catch {
    // silently fail — cooc stays empty
  }
}

async function fetchConf() {
  const qs = buildQueryParams();
  try {
    const res = await apiClient.get(
      `/pictures/stats?${qs ? qs + "&" : ""}include=conf`,
    );
    if (stats.value)
      stats.value = {
        ...stats.value,
        confidence_histogram: res.data.confidence_histogram,
        regular_tags: res.data.regular_tags,
      };
    confLoaded.value = true;
  } catch {
    // silently fail — conf stays empty
  }
}

// Watch all filter props that affect the picture query
watch(
  [
    () => props.selectedCharacter,
    () => props.selectedCharacterIds,
    () => props.characterMode,
    () => props.selectedSet,
    () => props.selectedSetIds,
    () => props.setMode,
    () => props.setDifferenceBaseId,
    () => props.projectViewMode,
    () => props.selectedProjectId,
    () => props.tagFilter,
    () => props.tagRejectedFilter,
    () => props.mediaTypeFilter,
    () => props.minScoreFilter,
    () => props.maxScoreFilter,
    () => props.smartScoreBucketFilter,
    () => props.resolutionBucketFilter,
    () => props.filePathPrefixFilter,
    () => props.faceBboxFilter,
    () => props.tagConfidenceAboveFilter,
    () => props.tagConfidenceBelowFilter,
  ],
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
  { immediate: true, deep: true },
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
watch(
  () => props.wsTagUpdate,
  () => {
    fetchStats().then(() => {
      if (coocOpen.value) fetchCooc();
      if (confHistOpen.value) fetchConf();
      if (activeTab.value === "pictures") fetchPicStats();
      if (penalisedOnlyTags.value || penalisedOnlyCooc.value > 0)
        fetchStatsPenalised();
      if (penalisedOnlyCooc.value === 2) fetchStatsPenalisedBoth();
    });
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
  const taggedFraction = stats.value.tagged / stats.value.total;
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
const penalisedOnlyTags = ref(false);
// 0 = all, 1 = at least one penalised, 2 = both penalised
const penalisedOnlyCooc = ref(0);

const statsPenalised = ref(null);
const loadingPenalised = ref(false);
const statsPenalisedBoth = ref(null);
const loadingPenalisedBoth = ref(false);

function isPenalised(tag) {
  return Object.prototype.hasOwnProperty.call(
    props.penalisedTagWeights,
    String(tag).trim().toLowerCase(),
  );
}

const hasPenalisedTags = computed(
  () => Object.keys(props.penalisedTagWeights).length > 0,
);

async function fetchStatsPenalised() {
  const qs = buildQueryParams();
  loadingPenalised.value = true;
  try {
    const res = await apiClient.get(
      `/pictures/stats?${qs ? qs + "&" : ""}only_penalised=1&include=cooc`,
    );
    statsPenalised.value = res.data;
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
    const res = await apiClient.get(
      `/pictures/stats?${qs ? qs + "&" : ""}only_penalised=both&include=cooc`,
    );
    statsPenalisedBoth.value = res.data;
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
  Object.keys(props.penalisedTagWeights).sort((a, b) => a.localeCompare(b)),
);
const regularTags = computed(() => stats.value?.regular_tags ?? []);

// Active-state helpers
const tagFilterSet = computed(() => new Set(props.tagFilter || []));
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
  return (props.tagConfidenceAboveFilter || []).filter((e) => {
    if (!selectedConfTag.value) return false;
    return e.startsWith(selectedConfTag.value + ":");
  });
}
function isConfEntryActive(bucketIndex) {
  const entry = `${selectedConfTag.value}:${(bucketIndex * 0.2).toFixed(2)}`;
  return (props.tagConfidenceAboveFilter || []).includes(entry);
}

const confHistBuckets = computed(() => {
  if (selectedConfTag.value)
    return confTagData.value?.confidence_histogram ?? [];
  return stats.value?.confidence_histogram ?? [];
});
const confHistMax = computed(() =>
  Math.max(1, ...confHistBuckets.value.map((b) => b.count)),
);
const tagCountBuckets = computed(
  () => stats.value?.anomaly_tag_count_histogram ?? [],
);
const tagCountHistMax = computed(() =>
  Math.max(1, ...tagCountBuckets.value.map((b) => b.count)),
);

function histBarWidth(count, maxCount) {
  return Math.max(count > 0 ? 2 : 0, (count / maxCount) * 208);
}

function isScoreBarActive(label) {
  const n = parseInt(label);
  if (isNaN(n)) return false;
  return props.minScoreFilter === n && props.maxScoreFilter === n;
}

function handleScoreBarClick(label) {
  const n = parseInt(label);
  if (isNaN(n)) return;
  if (isScoreBarActive(label)) {
    emit("update:minScoreFilter", null);
    emit("update:maxScoreFilter", null);
  } else {
    emit("update:minScoreFilter", n);
    emit("update:maxScoreFilter", n);
  }
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
  return props.smartScoreBucketFilter === key;
}

function handleSmartScoreBarClick(label) {
  const key = SMART_SCORE_LABEL_TO_BUCKET[label];
  if (!key) return;
  if (isSmartScoreBarActive(label)) {
    emit("update:smartScoreBucketFilter", null);
  } else {
    emit("update:smartScoreBucketFilter", key);
  }
}

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
  return props.resolutionBucketFilter === key;
}

function handleResolutionBarClick(label) {
  const key = RESOLUTION_LABEL_TO_BUCKET[label];
  if (!key) return;
  if (isResolutionBarActive(label)) {
    emit("update:resolutionBucketFilter", null);
  } else {
    emit("update:resolutionBucketFilter", key);
  }
}
</script>

<template>
  <div class="stats-sidebar" :class="{ collapsed: !props.open }">
    <div v-if="props.open" class="stats-sidebar-content">
      <div class="stats-sidebar-header">
        <button
          class="stats-tab-btn"
          :class="{ active: activeTab === 'tags' }"
          type="button"
          @click="activeTab = 'tags'"
        >
          <v-icon size="12">mdi-tag-multiple-outline</v-icon>
          Tags
        </button>
        <button
          class="stats-tab-btn"
          :class="{ active: activeTab === 'pictures' }"
          type="button"
          @click="activeTab = 'pictures'"
        >
          <v-icon size="12">mdi-image-multiple-outline</v-icon>
          Pictures
        </button>
      </div>

      <div v-if="loading && !stats" class="stats-loading">
        <v-progress-circular
          indeterminate
          size="24"
          width="2"
          color="primary"
        />
      </div>

      <div v-else-if="error" class="stats-error">{{ error }}</div>

      <template v-else-if="stats && activeTab === 'tags'">
        <!-- Overview section -->
        <div class="stats-section">
          <!-- Stat tiles -->
          <div class="stats-tiles">
            <div class="stats-tile">
              <span class="stats-tile-value">{{
                stats.total_tags != null
                  ? stats.total_tags.toLocaleString()
                  : stats.total.toLocaleString()
              }}</span>
              <span class="stats-tile-label">Total tags</span>
            </div>
            <div class="stats-tile">
              <span class="stats-tile-value">{{
                stats.avg_tags_per_image.toFixed(1)
              }}</span>
              <span class="stats-tile-label">Avg tags / pic</span>
            </div>
          </div>

          <!-- Donut chart -->
          <div class="stats-section-header">
            <span class="stats-section-title">Tagged pictures</span>
          </div>
          <div class="stats-donut-wrap">
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
          class="stats-section"
        >
          <div class="stats-section-header">
            <button
              class="stats-section-toggle"
              type="button"
              @click="topTagsOpen = !topTagsOpen"
            >
              <v-icon size="13">{{
                topTagsOpen ? "mdi-chevron-down" : "mdi-chevron-right"
              }}</v-icon>
              <span class="stats-section-title" style="margin-left: 2px"
                >Top Tags</span
              >
            </button>
            <button
              v-if="hasPenalisedTags && topTagsOpen"
              class="penalised-toggle"
              :class="{ active: penalisedOnlyTags }"
              type="button"
              title="Show penalised tags only"
              @click="penalisedOnlyTags = !penalisedOnlyTags"
            >
              <v-icon size="11">mdi-alert-circle-outline</v-icon>
              penalised
            </button>
            <button
              v-if="topTagsOpen && activeTagsInTopTags().length > 0"
              class="stats-clear-btn"
              type="button"
              title="Clear top-tag filters"
              @click="emit('clear-tag-filter', activeTagsInTopTags())"
            >
              <v-icon size="11">mdi-close</v-icon>
            </button>
          </div>
          <div v-if="topTagsOpen" class="stats-bars">
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
                @click="emit('filter-tag', item.tag)"
                @keydown.enter="emit('filter-tag', item.tag)"
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
        <div v-if="stats.total > 0" class="stats-section">
          <div class="stats-section-header">
            <button
              class="stats-section-toggle"
              type="button"
              @click="coocOpen = !coocOpen"
            >
              <v-icon size="13">{{
                coocOpen ? "mdi-chevron-down" : "mdi-chevron-right"
              }}</v-icon>
              <span class="stats-section-title" style="margin-left: 2px"
                >Co-occurrences</span
              >
            </button>
            <button
              v-if="hasPenalisedTags && coocOpen"
              class="penalised-toggle"
              :class="{ active: penalisedOnlyCooc > 0 }"
              type="button"
              :title="COOC_FILTER_TITLES[penalisedOnlyCooc]"
              @click="penalisedOnlyCooc = (penalisedOnlyCooc + 1) % 3"
            >
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
              title="Clear co-occurrence filters"
              @click="emit('clear-tag-filter', activeTagsInCooc())"
            >
              <v-icon size="11">mdi-close</v-icon>
            </button>
          </div>
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
              @click="emit('filter-tags', item.tags)"
              @keydown.enter="emit('filter-tags', item.tags)"
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
        <div v-if="stats.total > 0" class="stats-section">
          <div class="stats-section-header">
            <button
              class="stats-section-toggle"
              type="button"
              @click="confHistOpen = !confHistOpen"
            >
              <v-icon size="13">{{
                confHistOpen ? "mdi-chevron-down" : "mdi-chevron-right"
              }}</v-icon>
              <span class="stats-section-title" style="margin-left: 2px"
                >Tag Confidence</span
              >
            </button>
            <div class="conf-tag-selector">
              <v-progress-circular
                v-if="confTagLoading"
                indeterminate
                size="10"
                width="1"
                color="primary"
                class="conf-tag-spinner"
              />
              <select
                v-model="selectedConfTag"
                class="conf-tag-select"
                title="Filter by tag"
              >
                <option :value="null">All tags</option>
                <optgroup v-if="anomalyTagOptions.length" label="Anomaly tags">
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
              <button
                v-if="confHistOpen && activeConfEntries().length > 0"
                class="stats-clear-btn"
                type="button"
                title="Clear confidence filters"
                @click="emit('clear-confidence-filter', activeConfEntries())"
              >
                <v-icon size="11">mdi-close</v-icon>
              </button>
            </div>
          </div>
          <div v-if="confHistOpen" class="stats-hist">
            <svg
              :width="260"
              :height="confHistBuckets.length * 18 + 4"
              class="stats-bar-chart"
              aria-label="Tag confidence distribution"
            >
              <g
                v-for="(item, i) in confHistBuckets"
                :key="item.label"
                :class="{
                  'hist-bar-row': selectedConfTag,
                  'hist-bar-row--disabled': !selectedConfTag,
                  'hist-bar-row--active':
                    selectedConfTag && isConfEntryActive(i),
                }"
                :transform="`translate(0, ${i * 18})`"
                :role="selectedConfTag ? 'button' : undefined"
                :tabindex="selectedConfTag ? 0 : undefined"
                :title="
                  selectedConfTag
                    ? `Filter: ${selectedConfTag} ≥ ${i * 20}%`
                    : undefined
                "
                @click="
                  selectedConfTag && item.count > 0
                    ? emit(
                        'filter-confidence-above',
                        `${selectedConfTag}:${(i * 0.2).toFixed(2)}`,
                      )
                    : undefined
                "
                @keydown.enter="
                  selectedConfTag && item.count > 0
                    ? emit(
                        'filter-confidence-above',
                        `${selectedConfTag}:${(i * 0.2).toFixed(2)}`,
                      )
                    : undefined
                "
              >
                <text x="46" y="9" text-anchor="end" class="hist-label">
                  {{ item.label }}
                </text>
                <rect
                  x="50"
                  y="2"
                  :width="histBarWidth(item.count, confHistMax)"
                  height="13"
                  rx="2"
                  class="hist-bar-rect hist-bar-rect--conf"
                />
                <text
                  v-if="
                    item.count > 0 &&
                    histBarWidth(item.count, confHistMax) >= 40
                  "
                  :x="50 + histBarWidth(item.count, confHistMax) - 3"
                  y="9"
                  text-anchor="end"
                  class="bar-count-inner"
                >
                  {{ item.count }}
                </text>
                <text
                  v-else-if="item.count > 0"
                  :x="50 + histBarWidth(item.count, confHistMax) + 3"
                  y="9"
                  text-anchor="start"
                  class="bar-count-outer"
                >
                  {{ item.count }}
                </text>
              </g>
            </svg>
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
          <!-- Total tile -->
          <div class="stats-section">
            <div class="stats-tiles">
              <div class="stats-tile">
                <span class="stats-tile-value">{{
                  picStats.total.toLocaleString()
                }}</span>
                <span class="stats-tile-label">Total</span>
              </div>
            </div>
          </div>

          <!-- Manual score distribution -->
          <div class="stats-section">
            <div class="stats-section-header">
              <span class="stats-section-title">Score</span>
              <button
                v-if="minScoreFilter != null || maxScoreFilter != null"
                class="stats-clear-btn"
                type="button"
                title="Clear score filter"
                @click="
                  emit('update:minScoreFilter', null);
                  emit('update:maxScoreFilter', null);
                "
              >
                <v-icon size="11">mdi-close</v-icon>
              </button>
            </div>
            <div class="stats-hist">
              <svg
                :width="260"
                :height="picStats.score_distribution.length * 18 + 4"
                class="stats-bar-chart"
                aria-label="Manual score distribution"
              >
                <g
                  v-for="item in picStats.score_distribution"
                  :key="item.label"
                  :transform="`translate(0, ${picStats.score_distribution.indexOf(item) * 18})`"
                  :class="{
                    'hist-bar-row': item.label !== 'Unscored',
                    'hist-bar-row--active': isScoreBarActive(item.label),
                  }"
                  :role="item.label !== 'Unscored' ? 'button' : undefined"
                  :tabindex="item.label !== 'Unscored' ? 0 : undefined"
                  @click="handleScoreBarClick(item.label)"
                  @keydown.enter="handleScoreBarClick(item.label)"
                >
                  <text x="46" y="9" text-anchor="end" class="hist-label">
                    {{ item.label }}
                  </text>
                  <rect
                    x="50"
                    y="2"
                    :width="
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.score_distribution.map((b) => b.count),
                        ),
                      )
                    "
                    height="13"
                    rx="2"
                    class="hist-bar-rect hist-bar-rect--score"
                  />
                  <text
                    v-if="
                      item.count > 0 &&
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.score_distribution.map((b) => b.count),
                        ),
                      ) >= 40
                    "
                    :x="
                      50 +
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.score_distribution.map((b) => b.count),
                        ),
                      ) -
                      3
                    "
                    y="9"
                    text-anchor="end"
                    class="bar-count-inner"
                  >
                    {{ item.count }}
                  </text>
                  <text
                    v-else-if="item.count > 0"
                    :x="
                      50 +
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.score_distribution.map((b) => b.count),
                        ),
                      ) +
                      3
                    "
                    y="9"
                    text-anchor="start"
                    class="bar-count-outer"
                  >
                    {{ item.count }}
                  </text>
                </g>
              </svg>
            </div>
          </div>

          <!-- Smart score distribution -->
          <div class="stats-section">
            <div class="stats-section-header">
              <span class="stats-section-title">Smart Score</span>
              <button
                v-if="smartScoreBucketFilter != null"
                class="stats-clear-btn"
                type="button"
                title="Clear smart score filter"
                @click="emit('update:smartScoreBucketFilter', null)"
              >
                <v-icon size="11">mdi-close</v-icon>
              </button>
            </div>
            <div class="stats-hist">
              <svg
                :width="260"
                :height="picStats.smart_score_distribution.length * 18 + 4"
                class="stats-bar-chart"
                aria-label="Smart score distribution"
              >
                <g
                  v-for="item in picStats.smart_score_distribution"
                  :key="item.label"
                  :transform="`translate(0, ${picStats.smart_score_distribution.indexOf(item) * 18})`"
                  class="hist-bar-row"
                  :class="{
                    'hist-bar-row--active': isSmartScoreBarActive(item.label),
                  }"
                  role="button"
                  tabindex="0"
                  @click="handleSmartScoreBarClick(item.label)"
                  @keydown.enter="handleSmartScoreBarClick(item.label)"
                >
                  <text x="46" y="9" text-anchor="end" class="hist-label">
                    {{ item.label }}
                  </text>
                  <rect
                    x="50"
                    y="2"
                    :width="
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.smart_score_distribution.map(
                            (b) => b.count,
                          ),
                        ),
                      )
                    "
                    height="13"
                    rx="2"
                    class="hist-bar-rect hist-bar-rect--smart"
                  />
                  <text
                    v-if="
                      item.count > 0 &&
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.smart_score_distribution.map(
                            (b) => b.count,
                          ),
                        ),
                      ) >= 40
                    "
                    :x="
                      50 +
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.smart_score_distribution.map(
                            (b) => b.count,
                          ),
                        ),
                      ) -
                      3
                    "
                    y="9"
                    text-anchor="end"
                    class="bar-count-inner"
                  >
                    {{ item.count }}
                  </text>
                  <text
                    v-else-if="item.count > 0"
                    :x="
                      50 +
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.smart_score_distribution.map(
                            (b) => b.count,
                          ),
                        ),
                      ) +
                      3
                    "
                    y="9"
                    text-anchor="start"
                    class="bar-count-outer"
                  >
                    {{ item.count }}
                  </text>
                </g>
              </svg>
            </div>
          </div>

          <!-- Resolution distribution -->
          <div class="stats-section">
            <div class="stats-section-header">
              <span class="stats-section-title">Resolution</span>
              <button
                v-if="resolutionBucketFilter != null"
                class="stats-clear-btn"
                type="button"
                title="Clear resolution filter"
                @click="emit('update:resolutionBucketFilter', null)"
              >
                <v-icon size="11">mdi-close</v-icon>
              </button>
            </div>
            <div class="stats-hist">
              <svg
                :width="260"
                :height="picStats.resolution_distribution.length * 18 + 4"
                class="stats-bar-chart"
                aria-label="Resolution distribution"
              >
                <g
                  v-for="item in picStats.resolution_distribution"
                  :key="item.label"
                  :transform="`translate(0, ${picStats.resolution_distribution.indexOf(item) * 18})`"
                  class="hist-bar-row"
                  :class="{
                    'hist-bar-row--active': isResolutionBarActive(item.label),
                  }"
                  role="button"
                  tabindex="0"
                  @click="handleResolutionBarClick(item.label)"
                  @keydown.enter="handleResolutionBarClick(item.label)"
                >
                  <text x="46" y="9" text-anchor="end" class="hist-label">
                    {{ item.label }}
                  </text>
                  <rect
                    x="50"
                    y="2"
                    :width="
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.resolution_distribution.map(
                            (b) => b.count,
                          ),
                        ),
                      )
                    "
                    height="13"
                    rx="2"
                    class="hist-bar-rect hist-bar-rect--res"
                  />
                  <text
                    v-if="
                      item.count > 0 &&
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.resolution_distribution.map(
                            (b) => b.count,
                          ),
                        ),
                      ) >= 40
                    "
                    :x="
                      50 +
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.resolution_distribution.map(
                            (b) => b.count,
                          ),
                        ),
                      ) -
                      3
                    "
                    y="9"
                    text-anchor="end"
                    class="bar-count-inner"
                  >
                    {{ item.count }}
                  </text>
                  <text
                    v-else-if="item.count > 0"
                    :x="
                      50 +
                      histBarWidth(
                        item.count,
                        Math.max(
                          1,
                          ...picStats.resolution_distribution.map(
                            (b) => b.count,
                          ),
                        ),
                      ) +
                      3
                    "
                    y="9"
                    text-anchor="start"
                    class="bar-count-outer"
                  >
                    {{ item.count }}
                  </text>
                </g>
              </svg>
            </div>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.stats-sidebar {
  position: relative;
  width: 288px;
  min-width: 288px;
  max-width: 288px;
  height: 100%;
  display: flex;
  flex-direction: row;
  flex-shrink: 0;
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  background: rgba(var(--v-theme-surface), 1);
  transition:
    width 0.15s,
    min-width 0.15s,
    border-color 0.15s;
  overflow: hidden;
}

.stats-sidebar.collapsed {
  width: 0;
  min-width: 0;
  max-width: 0;
  border-left-color: transparent;
  overflow: visible;
  background: transparent;
}

.stats-sidebar-content {
  flex: 1;
  min-width: 0;
  padding: 8px 10px 12px 10px;
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stats-sidebar-header {
  display: flex;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  margin-bottom: 4px;
}

.stats-header-icon {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.stats-sidebar-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.stats-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 0;
}

.stats-error {
  font-size: 12px;
  color: rgba(var(--v-theme-error), 1);
  padding: 8px 0;
}

.stats-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.stats-section:last-child {
  border-bottom: none;
}

.stats-section-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.stats-section-toggle {
  display: flex;
  align-items: center;
  gap: 2px;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  color: inherit;
}

.stats-tiles {
  display: flex;
  gap: 8px;
}

.stats-tile {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: rgba(var(--v-theme-on-surface), 0.05);
  border-radius: 6px;
  padding: 6px 4px;
}

.stats-tile-value {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.1;
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.stats-tile-label {
  font-size: 10px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-top: 2px;
}

.stats-donut-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
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
  font-size: 14px;
  font-weight: 600;
  fill: rgba(var(--v-theme-on-surface), 0.85);
}

.donut-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
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
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.stats-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.penalised-toggle {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 10px;
  border: 1px solid rgba(var(--v-theme-warning), 0.4);
  background: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.45);
  transition:
    background 0.12s,
    color 0.12s,
    border-color 0.12s;
  white-space: nowrap;
}
.penalised-toggle:hover {
  background: rgba(var(--v-theme-warning), 0.1);
  color: rgba(var(--v-theme-warning), 1);
  border-color: rgba(var(--v-theme-warning), 0.7);
}
.penalised-toggle.active {
  background: rgba(var(--v-theme-warning), 0.18);
  color: rgba(var(--v-theme-warning), 1);
  border-color: rgba(var(--v-theme-warning), 0.7);
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
  outline: none;
}

.bar-row:hover .bar-rect {
  opacity: 0.85;
}

.bar-rect {
  fill: rgba(var(--v-theme-primary), 0.65);
  transition: opacity 0.12s;
}

.bar-row--active .bar-rect {
  stroke: rgba(var(--v-theme-primary), 1);
  stroke-width: 1.5;
  opacity: 1;
}

.bar-penalised .bar-rect {
  fill: rgba(var(--v-theme-warning), 0.65);
}

.bar-label-fo {
  font-size: 10px;
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
  font-size: 9px;
  font-weight: 600;
  fill: rgba(255, 255, 255, 0.85);
  dominant-baseline: central;
  pointer-events: none;
}

.bar-count-outer {
  font-size: 9px;
  font-weight: 600;
  fill: rgba(var(--v-theme-on-surface), 0.65);
  dominant-baseline: central;
  pointer-events: none;
}

.stats-cooc-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 2px;
}

.cooc-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  cursor: pointer;
  border-radius: 3px;
  padding: 1px 2px;
  outline: none;
}
.cooc-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.9);
}
.cooc-item:focus-visible {
  box-shadow: 0 0 0 1px rgba(var(--v-theme-primary), 0.5);
}

.cooc-item--active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.tag-penalised {
  color: rgba(var(--v-theme-warning), 1);
}

.cooc-sep {
  color: rgba(var(--v-theme-on-surface), 0.35);
}

.cooc-empty {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.35);
  font-style: italic;
}

.cooc-tags {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: 6px;
}

.cooc-count {
  color: rgba(var(--v-theme-on-surface), 0.4);
  flex-shrink: 0;
  font-size: 10px;
}

.stats-hist {
  overflow-x: hidden;
}

.hist-label {
  font-size: 10px;
  fill: rgba(var(--v-theme-on-surface), 0.6);
  dominant-baseline: central;
}

.hist-bar-rect {
  fill: rgba(var(--v-theme-primary), 0.5);
}

.hist-bar-row {
  cursor: pointer;
  outline: none;
}
.hist-bar-row:hover .hist-bar-rect {
  fill: rgba(var(--v-theme-primary), 0.75);
}
.hist-bar-row--active .hist-bar-rect {
  fill: rgba(var(--v-theme-primary), 0.85);
  stroke: rgba(var(--v-theme-primary), 1);
  stroke-width: 1;
}

.hist-bar-row--disabled {
  cursor: default;
}

.stats-clear-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  background: none;
  border: none;
  cursor: pointer;
  border-radius: 3px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  flex-shrink: 0;
}
.stats-clear-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.conf-tag-selector {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.conf-tag-spinner {
  flex-shrink: 0;
}

.conf-tag-select {
  font-size: 10px;
  color: rgba(var(--v-theme-on-surface), 0.65);
  background: rgba(var(--v-theme-on-surface), 0.06);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  border-radius: 4px;
  padding: 1px 4px;
  cursor: pointer;
  max-width: 108px;
  outline: none;
  appearance: none;
}
.conf-tag-select:hover {
  border-color: rgba(var(--v-theme-on-surface), 0.3);
}
.conf-tag-select:focus {
  border-color: rgba(var(--v-theme-primary), 0.6);
}

.stats-tab-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 500;
  padding: 5px 6px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.45);
  transition:
    color 0.12s,
    border-color 0.12s;
  border-radius: 0;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.stats-tab-btn:hover {
  color: rgba(var(--v-theme-on-surface), 0.75);
}
.stats-tab-btn.active {
  color: rgba(var(--v-theme-primary), 1);
  border-bottom-color: rgba(var(--v-theme-primary), 0.85);
}

.hist-bar-rect--conf {
  fill: rgba(var(--v-theme-tertiary), 0.5);
}
.hist-bar-row:hover .hist-bar-rect--conf {
  fill: rgba(var(--v-theme-tertiary), 0.75);
}
.hist-bar-row--active .hist-bar-rect--conf {
  fill: rgba(var(--v-theme-tertiary), 0.85);
  stroke: rgba(var(--v-theme-tertiary), 1);
  stroke-width: 1;
}
.hist-bar-rect--score {
  fill: rgba(var(--v-theme-secondary), 0.5);
}
.hist-bar-row:hover .hist-bar-rect--score {
  fill: rgba(var(--v-theme-secondary), 0.75);
}
.hist-bar-row--active .hist-bar-rect--score {
  fill: rgba(var(--v-theme-secondary), 0.85);
  stroke: rgba(var(--v-theme-secondary), 1);
  stroke-width: 1;
}
.hist-bar-rect--smart {
  fill: rgba(var(--v-theme-primary), 0.5);
}
.hist-bar-row:hover .hist-bar-rect--smart {
  fill: rgba(var(--v-theme-primary), 0.75);
}
.hist-bar-row--active .hist-bar-rect--smart {
  fill: rgba(var(--v-theme-primary), 0.85);
  stroke: rgba(var(--v-theme-primary), 1);
  stroke-width: 1;
}
.hist-bar-rect--res {
  fill: rgba(var(--v-theme-tertiary), 0.5);
}
.hist-bar-row:hover .hist-bar-rect--res {
  fill: rgba(var(--v-theme-tertiary), 0.75);
}
.hist-bar-row--active .hist-bar-rect--res {
  fill: rgba(var(--v-theme-tertiary), 0.85);
  stroke: rgba(var(--v-theme-tertiary), 1);
  stroke-width: 1;
}
</style>
