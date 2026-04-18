<script setup>
import { ref, watch, computed } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  backendUrl: { type: String, required: true },
  // Filter props — mirrors the same params ImageGrid uses for fetching pictures
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedSet: { type: [Number, String, null], default: null },
  selectedSetIds: { type: Array, default: () => [] },
  setMode: { type: String, default: "union" },
  projectViewMode: { type: String, default: null },
  selectedProjectId: { type: [Number, String, null], default: null },
  tagFilter: { type: Array, default: () => [] },
  tagRejectedFilter: { type: Array, default: () => [] },
  mediaTypeFilter: { type: String, default: null },
  minScoreFilter: { type: [Number, String, null], default: null },
  filePathPrefixFilter: { type: String, default: null },
  allPicturesId: { type: [String, Number], default: null },
  unassignedPicturesId: { type: [String, Number], default: null },
  scrapheapPicturesId: { type: [String, Number], default: null },
  penalisedTagWeights: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["filter-tag"]);

// ─── Sidebar open/closed state ────────────────────────────────────────────────
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

function loadOpen() {
  try {
    return (
      window.localStorage?.getItem("pixlstash:statsSidebarOpen") !== "false"
    );
  } catch {
    return true;
  }
}
function saveOpen(val) {
  try {
    window.localStorage?.setItem(
      "pixlstash:statsSidebarOpen",
      val ? "true" : "false",
    );
  } catch {
    // ignore
  }
}

const sidebarOpen = ref(loadOpen());
function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value;
  saveOpen(sidebarOpen.value);
}

// Co-occurrence section collapsed
const coocOpen = ref(false);

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
      params.append("set_mode", "intersection");
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
    params.append("character_id", props.selectedCharacter);
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
  if (props.filePathPrefixFilter != null)
    params.append("file_path_prefix", props.filePathPrefixFilter);
  (props.tagFilter || []).forEach((t) => params.append("tag", t));
  (props.tagRejectedFilter || []).forEach((t) =>
    params.append("rejected_tag", t),
  );

  return params.toString();
}

async function fetchStats() {
  const qs = buildQueryParams();
  loading.value = true;
  error.value = null;
  try {
    const res = await apiClient.get(`/pictures/stats${qs ? `?${qs}` : ""}`);
    stats.value = res.data;
  } catch (e) {
    error.value = "Failed to load stats";
    stats.value = null;
  } finally {
    loading.value = false;
  }
}

// Watch all filter props that affect the picture query
watch(
  [
    () => props.selectedCharacter,
    () => props.selectedSet,
    () => props.selectedSetIds,
    () => props.projectViewMode,
    () => props.selectedProjectId,
    () => props.tagFilter,
    () => props.tagRejectedFilter,
    () => props.mediaTypeFilter,
    () => props.minScoreFilter,
    () => props.filePathPrefixFilter,
  ],
  () => {
    statsPenalised.value = null;
    statsPenalisedBoth.value = null;
    fetchStats();
    if (penalisedOnlyTags.value || penalisedOnlyCooc.value > 0)
      fetchStatsPenalised();
    if (penalisedOnlyCooc.value === 2) fetchStatsPenalisedBoth();
  },
  { immediate: true, deep: true },
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
      `/pictures/stats?${qs ? qs + "&" : ""}only_penalised=1`,
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
      `/pictures/stats?${qs ? qs + "&" : ""}only_penalised=both`,
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
</script>

<template>
  <div class="stats-sidebar" :class="{ collapsed: !sidebarOpen }">
    <button
      v-if="!sidebarOpen"
      class="stats-sidebar-toggle"
      type="button"
      title="Show stats sidebar"
      @click="toggleSidebar"
    >
      <v-icon size="14">mdi-chevron-left</v-icon>
    </button>

    <div v-if="sidebarOpen" class="stats-sidebar-content">
      <div class="stats-sidebar-header">
        <v-icon size="14" class="stats-header-icon">mdi-chart-bar</v-icon>
        <span class="stats-sidebar-title">Tag Stats</span>
        <button
          class="stats-sidebar-toggle"
          type="button"
          title="Hide stats sidebar"
          @click="toggleSidebar"
        >
          <v-icon size="14">mdi-chevron-right</v-icon>
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

      <template v-else-if="stats">
        <!-- Overview section -->
        <div class="stats-section">
          <!-- Stat tiles -->
          <div class="stats-tiles">
            <div class="stats-tile">
              <span class="stats-tile-value">{{
                stats.total.toLocaleString()
              }}</span>
              <span class="stats-tile-label">Total</span>
            </div>
            <div class="stats-tile">
              <span class="stats-tile-value">{{
                stats.avg_tags_per_image.toFixed(1)
              }}</span>
              <span class="stats-tile-label">Avg tags</span>
            </div>
          </div>

          <!-- Donut chart -->
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
            <span class="stats-section-title">Top Tags</span>
            <button
              v-if="hasPenalisedTags"
              class="penalised-toggle"
              :class="{ active: penalisedOnlyTags }"
              type="button"
              title="Show penalised tags only"
              @click="penalisedOnlyTags = !penalisedOnlyTags"
            >
              <v-icon size="11">mdi-alert-circle-outline</v-icon>
              penalised
            </button>
          </div>
          <div class="stats-bars">
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
                :class="{ 'bar-penalised': isPenalised(item.tag) }"
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
                  :x="Math.max(4, (barWidth(item.count) / 100) * 140 - 3)"
                  y="9"
                  text-anchor="end"
                  class="bar-count-inner"
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
        <div
          v-if="stats.top_cooccurrences.length || penalisedOnlyCooc > 0"
          class="stats-section"
        >
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
          </div>
          <div v-if="coocOpen" class="stats-cooc-list">
            <div
              v-for="(item, i) in displayedCooc"
              :key="i"
              class="cooc-item"
              :class="{
                'cooc-penalised':
                  isPenalised(item.tags[0]) || isPenalised(item.tags[1]),
              }"
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

.stats-sidebar-toggle {
  position: relative;
  z-index: 100;
  width: 22px;
  height: 22px;
  background: none;
  border: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-radius: 4px;
  flex-shrink: 0;
  transition:
    color 0.15s,
    background 0.15s,
    box-shadow 0.15s;
}
.stats-sidebar.collapsed .stats-sidebar-toggle {
  position: absolute;
  top: 6px;
  left: -26px;
  background: rgba(var(--v-theme-surface), 0.96);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 4px 0 0 4px;
  box-shadow: -2px 1px 5px rgba(0, 0, 0, 0.12);
  color: rgba(var(--v-theme-on-surface), 0.5);
}
.stats-sidebar-toggle:hover {
  color: rgba(var(--v-theme-on-surface), 0.9);
  background: rgba(var(--v-theme-on-surface), 0.08);
}
.stats-sidebar.collapsed .stats-sidebar-toggle:hover {
  background: rgba(var(--v-theme-surface), 1);
  color: rgba(var(--v-theme-on-surface), 0.9);
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
  align-items: center;
  gap: 5px;
  margin-bottom: 4px;
  justify-content: space-between;
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
  stroke: rgba(var(--v-theme-primary), 0.85);
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
  background: rgba(var(--v-theme-primary), 0.85);
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
</style>
