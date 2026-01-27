<template>
  <div v-if="open" class="scoring-overlay" @click.self="handleClose">
    <div
      v-if="previewItem"
      class="scoring-preview-overlay"
      @click="closePreview"
    >
      <button class="scoring-preview-close" @click="closePreview">
        <v-icon size="18">mdi-close</v-icon>
        <span>Close</span>
      </button>
      <div class="scoring-preview-body">
        <video
          v-if="isVideo(previewItem)"
          class="scoring-preview-media"
          :src="getFullImageUrl(previewItem)"
          controls
          playsinline
        ></video>
        <img
          v-else
          class="scoring-preview-media"
          :src="getFullImageUrl(previewItem)"
          :alt="`Preview image ${previewItem.id}`"
        />
      </div>
    </div>
    <div class="scoring-shell">
      <header class="scoring-header">
        <button class="scoring-close" @click="handleClose" aria-label="Close">
          <v-icon size="18">mdi-close</v-icon>
          <span>Close</span>
        </button>
        <div class="scoring-title">Interactive Scoring</div>
        <div class="scoring-meta" v-if="phase === 'probe'">
          Probe {{ probesDone + 1 }} / {{ maxProbes }}
        </div>
        <div class="scoring-meta" v-else>Proposed scores ready</div>
      </header>

      <div
        class="scoring-body"
        :class="{ 'scoring-body-review': phase === 'review' }"
      >
        <div class="scoring-compare" v-if="showCompare">
          <div class="scoring-compare-card">
            <button
              class="scoring-compare-button"
              type="button"
              :aria-label="`Select ${comparePair.left.id} as best`"
              @click="chooseComparison('left')"
              :disabled="isBuildingReview"
            >
              <video
                v-if="isCompareVideo(comparePair.left)"
                class="scoring-compare-image"
                :src="getFullImageUrl(comparePair.left)"
                controls
                playsinline
              ></video>
              <img
                v-else
                class="scoring-compare-image"
                :src="getFullImageUrl(comparePair.left)"
                :alt="`Compare image ${comparePair.left.id}`"
              />
              <div class="scoring-compare-overlay">This is the best one</div>
            </button>
          </div>
          <div class="scoring-compare-card">
            <button
              class="scoring-compare-button"
              type="button"
              :aria-label="`Select ${comparePair.right.id} as best`"
              @click="chooseComparison('right')"
              :disabled="isBuildingReview"
            >
              <video
                v-if="isCompareVideo(comparePair.right)"
                class="scoring-compare-image"
                :src="getFullImageUrl(comparePair.right)"
                controls
                playsinline
              ></video>
              <img
                v-else
                class="scoring-compare-image"
                :src="getFullImageUrl(comparePair.right)"
                :alt="`Compare image ${comparePair.right.id}`"
              />
              <div class="scoring-compare-overlay">This is the best one</div>
            </button>
          </div>
        </div>

        <div class="scoring-media" v-else-if="phase === 'probe' && currentItem">
          <video
            v-if="isVideo(currentItem)"
            class="scoring-video"
            :src="getFullImageUrl(currentItem)"
            controls
            playsinline
          ></video>
          <img
            v-else
            class="scoring-image"
            :src="getFullImageUrl(currentItem)"
            :alt="`Image ${currentItem.id}`"
          />
        </div>
        <div v-else-if="phase === 'probe'" class="scoring-empty">
          No eligible images found.
        </div>

        <div class="scoring-actions" v-if="isBuildingReview">
          <v-progress-circular
            color="primary"
            indeterminate
            size="40"
            width="4"
          ></v-progress-circular>
          <div class="scoring-progress-label">Please wait…</div>
        </div>

        <div class="scoring-actions" v-else-if="showCompare">
          <div class="scoring-prompt">Click on the best image.</div>
          <div class="scoring-buttons">
            <v-btn
              class="scoring-button"
              variant="outlined"
              @click="chooseComparison('same')"
              :disabled="isBuildingReview"
            >
              They are the same
            </v-btn>
          </div>
          <div class="scoring-buttons">
            <v-btn
              variant="text"
              size="small"
              @click="openFullImage(comparePair.left)"
              :disabled="isBuildingReview"
            >
              Open left image
            </v-btn>
            <v-btn
              variant="text"
              size="small"
              @click="openFullImage(comparePair.right)"
              :disabled="isBuildingReview"
            >
              Open right image
            </v-btn>
          </div>
        </div>

        <div
          class="scoring-actions"
          v-else-if="phase === 'probe' && currentItem"
        >
          <div class="scoring-prompt">Good enough to keep?</div>
          <div class="scoring-buttons">
            <v-btn
              color="primary"
              variant="elevated"
              @click="chooseKeep"
              :disabled="isBuildingReview"
            >
              Keep
            </v-btn>
            <v-btn
              color="error"
              variant="outlined"
              @click="chooseToss"
              :disabled="isBuildingReview"
            >
              Toss
            </v-btn>
          </div>
          <div class="scoring-buttons">
            <v-btn
              variant="text"
              size="small"
              @click="openFullImage(currentItem)"
              :disabled="isBuildingReview"
            >
              Open full image
            </v-btn>
          </div>
        </div>

        <div class="scoring-review" v-else-if="phase === 'review'">
          <div class="scoring-progress" v-if="isBuildingReview">
            <v-progress-circular
              color="primary"
              indeterminate
              size="40"
              width="4"
            ></v-progress-circular>
            <div class="scoring-progress-label">Please wait…</div>
          </div>
          <div v-if="roundNumber === 1">
            <div class="scoring-round-summary">
              <div class="scoring-round-line">
                I've determined {{ roundPercent.gems }}% of the pictures are 5★
                gems and {{ roundPercent.trash }}% are 1★ trash. Do you agree?
              </div>
            </div>
            <div class="scoring-grid-wrapper">
              <div class="scoring-round-section" v-if="roundGems.length">
                <div class="scoring-round-title">5★ Gems</div>
                <div class="scoring-grid">
                  <div
                    v-for="item in roundGems"
                    :key="`gem-${item.id}`"
                    class="scoring-grid-card"
                    @click="openPreview(item)"
                  >
                    <img
                      class="scoring-grid-image"
                      :src="getPreviewUrl(item)"
                      :alt="`Image ${item.id}`"
                    />
                    <button
                      class="scoring-round-toggle"
                      type="button"
                      @click.stop="toggleRoundExclude(item.id)"
                    >
                      {{
                        roundExcludedIds.has(item.id) ? "Include" : "Exclude"
                      }}
                    </button>
                  </div>
                </div>
              </div>
              <div class="scoring-round-section" v-if="roundTrash.length">
                <div class="scoring-round-title">1★ Trash</div>
                <div class="scoring-grid">
                  <div
                    v-for="item in roundTrash"
                    :key="`trash-${item.id}`"
                    class="scoring-grid-card"
                    @click="openPreview(item)"
                  >
                    <img
                      class="scoring-grid-image"
                      :src="getPreviewUrl(item)"
                      :alt="`Image ${item.id}`"
                    />
                    <button
                      class="scoring-round-toggle"
                      type="button"
                      @click.stop="toggleRoundExclude(item.id)"
                    >
                      {{
                        roundExcludedIds.has(item.id) ? "Include" : "Exclude"
                      }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="scoring-grid-wrapper">
            <div class="scoring-grid">
              <div
                v-for="item in proposedItems"
                :key="item.id"
                class="scoring-grid-card"
                @click="openPreview(item)"
              >
                <img
                  class="scoring-grid-image"
                  :src="getPreviewUrl(item)"
                  :alt="`Image ${item.id}`"
                />
                <div class="scoring-grid-stars">
                  <button
                    v-for="n in 5"
                    :key="n"
                    :class="[
                      'scoring-grid-star',
                      { 'scoring-grid-star--unadjusted': !item.isAdjusted },
                    ]"
                    type="button"
                    :aria-label="`Set ${item.id} to ${n} stars`"
                    @click="setProvisionalStars(item.id, n)"
                  >
                    <v-icon
                      size="16"
                      :color="
                        n <= item.provisionalStars
                          ? item.isAdjusted
                            ? 'rgba(var(--v-theme-secondary), 0.95)'
                            : 'rgba(var(--v-theme-primary), 0.95)'
                          : 'rgba(var(--v-theme-on-surface), 0.35)'
                      "
                    >
                      {{
                        n <= item.provisionalStars
                          ? "mdi-star"
                          : "mdi-star-outline"
                      }}
                    </v-icon>
                  </button>
                  <span
                    :class="[
                      'scoring-grid-label',
                      { 'scoring-grid-label--adjusted': item.isAdjusted },
                    ]"
                    >{{ item.provisionalStars }}★</span
                  >
                </div>
              </div>
            </div>
          </div>
          <div class="scoring-summary-line">
            <span
              v-for="bucket in summaryBuckets"
              :key="bucket.stars"
              class="scoring-summary-pill"
            >
              {{ bucket.stars }}★ {{ bucket.count }}
            </span>
          </div>
          <div class="scoring-buttons scoring-review-actions">
            <v-btn color="primary" variant="elevated" @click="confirmScores">
              Score this round
            </v-btn>
            <v-btn
              v-if="roundNumber === 1"
              variant="outlined"
              @click="confirmScores(true)"
              :disabled="isBuildingReview"
            >
              Score and continue
            </v-btn>
            <v-btn variant="outlined" @click="discardScores">Discard</v-btn>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, watch, ref, onMounted, onUnmounted } from "vue";
import {
  isSupportedVideoFile,
  isSupportedImageFile,
  getOverlayFormat,
} from "../utils/media.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  items: { type: Array, default: () => [] },
  calibrationItems: { type: Array, default: () => [] },
  backendUrl: { type: String, required: true },
  session: { type: Object, default: null },
});

const emit = defineEmits(["close", "confirm", "discard", "session-update"]);

const DEFAULT_CONFIG = {
  maxProbes: 6,
  bandSizeCount: 10,
  margin: 0.03,
  pairwiseBudget: 8,
  mapping: {
    high5: 0.06,
    high4: 0.03,
    mid3: 0.03,
    low1: 0.06,
  },
};
const MIN_PROBES = 3;

const phase = ref("probe");
const probesDone = ref(0);
const keepMaxIndex = ref(-1);
const tossMinIndex = ref(0);
const sCut = ref(null);
const orderedItems = ref([]);
const provisionalMap = ref({});
const isBuildingReview = ref(false);
const reviewProgress = ref(0);
const reviewTotal = ref(0);
const manualOverrides = ref(new Set());
const roundExcludedIds = ref(new Set());
const comparisonBias = ref({});
const lastComparePair = ref(null);
const lastCompareItems = ref([]);
const usedComparePairs = ref(new Set());
const compareCursor = ref(0);
const pairwiseCount = ref(0);
const roundNumber = ref(1);
const roundOneMap = ref({});
const previewItem = ref(null);

const maxProbes = computed(() => DEFAULT_CONFIG.maxProbes);

const calibrationModel = computed(() => {
  const list = Array.isArray(props.calibrationItems)
    ? props.calibrationItems
    : [];
  const samples = list.filter(
    (item) =>
      item &&
      typeof item.smartScore === "number" &&
      typeof item.score === "number" &&
      item.score > 0,
  );
  if (samples.length < 2) return null;
  const xs = samples.map((s) => Number(s.smartScore));
  const ys = samples.map((s) => (Number(s.score) - 1) / 4);
  const meanX = xs.reduce((a, b) => a + b, 0) / xs.length;
  const meanY = ys.reduce((a, b) => a + b, 0) / ys.length;
  let num = 0;
  let den = 0;
  for (let i = 0; i < xs.length; i += 1) {
    const dx = xs[i] - meanX;
    num += dx * (ys[i] - meanY);
    den += dx * dx;
  }
  if (den === 0) return null;
  const slope = num / den;
  const intercept = meanY - slope * meanX;
  return { slope, intercept };
});

function getCalibratedScore(item) {
  const base = typeof item?.smartScore === "number" ? item.smartScore : 0;
  const model = calibrationModel.value;
  if (!model) {
    const normalized = (base - 1) / 4;
    return Math.max(0, Math.min(1, normalized));
  }
  const predicted = model.slope * base + model.intercept;
  return Math.max(0, Math.min(1, predicted));
}

function getScoreForMapping(item) {
  const base = getCalibratedScore(item);
  const bias = comparisonBias.value[item?.id] ?? 0;
  return Math.max(0, Math.min(1, base + bias));
}

function getCalibrationSnapshot() {
  const list = Array.isArray(props.calibrationItems)
    ? props.calibrationItems
    : [];
  const samples = list.filter(
    (item) =>
      item &&
      typeof item.smartScore === "number" &&
      typeof item.score === "number" &&
      item.score > 0,
  );
  return {
    sampleCount: samples.length,
    model: calibrationModel.value,
  };
}

function logCalibrationDecision(context, details = {}) {
  const snapshot = getCalibrationSnapshot();
  console.log("[InteractiveScoring][Calibration]", context, {
    sampleCount: snapshot.sampleCount,
    calibrated: Boolean(snapshot.model),
    slope: snapshot.model?.slope ?? null,
    intercept: snapshot.model?.intercept ?? null,
    manualOverrideIds: Array.from(manualOverrides.value),
    ...details,
  });
}

function normalizeItems(items) {
  const list = Array.isArray(items) ? items : [];
  return list
    .filter((img) => img && img.id && typeof img.smartScore === "number")
    .map((img) => ({
      id: img.id,
      smartScore: Number(img.smartScore),
      created_at: img.created_at || null,
      format: img.format || null,
      pixel_sha: img.pixel_sha || null,
      thumbnail: img.thumbnail || null,
    }))
    .sort((a, b) => {
      if (b.smartScore !== a.smartScore) {
        return b.smartScore - a.smartScore;
      }
      if (a.created_at && b.created_at && a.created_at !== b.created_at) {
        return a.created_at > b.created_at ? 1 : -1;
      }
      return String(a.id).localeCompare(String(b.id));
    });
}

const anchorsByBucket = computed(() => {
  const list = Array.isArray(props.calibrationItems)
    ? props.calibrationItems
    : [];
  const buckets = { 1: [], 2: [], 3: [], 4: [], 5: [] };
  for (const item of list) {
    const stars = Number(item?.score);
    if (!Number.isFinite(stars) || stars < 1 || stars > 5) continue;
    if (typeof item.smartScore !== "number") continue;
    buckets[stars].push({
      id: item.id,
      smartScore: Number(item.smartScore),
      created_at: item.created_at || null,
      format: item.format || null,
      pixel_sha: item.pixel_sha || null,
      thumbnail: item.thumbnail || null,
    });
  }

  const anchors = {};
  for (const [stars, items] of Object.entries(buckets)) {
    if (!items.length) continue;
    const sorted = [...items].sort((a, b) => a.smartScore - b.smartScore);
    const median = sorted[Math.floor(sorted.length / 2)]?.smartScore ?? 0;
    const anchor =
      sorted.sort(
        (a, b) =>
          Math.abs(a.smartScore - median) - Math.abs(b.smartScore - median),
      )[0] || sorted[0];
    anchors[stars] = anchor;
  }
  return anchors;
});

function resetSessionState() {
  phase.value = "probe";
  probesDone.value = 0;
  keepMaxIndex.value = -1;
  tossMinIndex.value = orderedItems.value.length;
  sCut.value = null;
  provisionalMap.value = {};
  isBuildingReview.value = false;
  reviewProgress.value = 0;
  reviewTotal.value = 0;
  manualOverrides.value = new Set();
  roundExcludedIds.value = new Set();
  comparisonBias.value = {};
  lastComparePair.value = null;
  lastCompareItems.value = [];
  usedComparePairs.value = new Set();
  compareCursor.value = 0;
  pairwiseCount.value = 0;
  roundNumber.value = 1;
  roundOneMap.value = {};
}

function hydrateFromSession(session) {
  if (!session) return false;
  const hasOtherState =
    session.phase ||
    typeof session.probesDone === "number" ||
    session.provisionalMap ||
    typeof session.keepMaxIndex === "number" ||
    typeof session.tossMinIndex === "number" ||
    (session.roundOneMap && typeof session.roundOneMap === "object");
  const hasState = hasOtherState || typeof session.roundNumber === "number";
  if (!hasState) return false;
  if (!hasOtherState && typeof session.roundNumber === "number") {
    resetSessionState();
    roundNumber.value = session.roundNumber;
    if (session.roundOneMap && typeof session.roundOneMap === "object") {
      roundOneMap.value = { ...session.roundOneMap };
    }
    return true;
  }
  if (Array.isArray(session.orderedItems)) {
    orderedItems.value = session.orderedItems;
  }
  if (session.phase) phase.value = session.phase;
  if (typeof session.probesDone === "number") {
    probesDone.value = session.probesDone;
  }
  if (typeof session.keepMaxIndex === "number") {
    keepMaxIndex.value = session.keepMaxIndex;
  }
  if (typeof session.tossMinIndex === "number") {
    tossMinIndex.value = session.tossMinIndex;
  }
  sCut.value =
    typeof session.sCut === "number" ? session.sCut : (session.sCut ?? null);
  provisionalMap.value = session.provisionalMap || {};
  if (Array.isArray(session.manualOverrideIds)) {
    manualOverrides.value = new Set(session.manualOverrideIds);
  }
  if (session.comparisonBias && typeof session.comparisonBias === "object") {
    comparisonBias.value = { ...session.comparisonBias };
  }
  if (Array.isArray(session.lastCompareItems)) {
    lastCompareItems.value = session.lastCompareItems;
  }
  if (Array.isArray(session.usedComparePairs)) {
    usedComparePairs.value = new Set(session.usedComparePairs);
  }
  if (typeof session.compareCursor === "number") {
    compareCursor.value = session.compareCursor;
  }
  if (typeof session.pairwiseCount === "number") {
    pairwiseCount.value = session.pairwiseCount;
  }
  if (typeof session.roundNumber === "number") {
    roundNumber.value = session.roundNumber;
  }
  if (session.roundOneMap && typeof session.roundOneMap === "object") {
    roundOneMap.value = { ...session.roundOneMap };
  }
  return true;
}

function emitSessionUpdate() {
  emit("session-update", {
    orderedItems: orderedItems.value,
    phase: phase.value,
    probesDone: probesDone.value,
    keepMaxIndex: keepMaxIndex.value,
    tossMinIndex: tossMinIndex.value,
    sCut: sCut.value,
    provisionalMap: provisionalMap.value,
    manualOverrideIds: Array.from(manualOverrides.value),
    comparisonBias: comparisonBias.value,
    lastCompareItems: lastCompareItems.value,
    usedComparePairs: Array.from(usedComparePairs.value),
    compareCursor: compareCursor.value,
    pairwiseCount: pairwiseCount.value,
    roundNumber: roundNumber.value,
    roundOneMap: roundOneMap.value,
  });
}

function applyRoundOneMap(baseMap) {
  const map = { ...baseMap };
  const roundMap = roundOneMap.value || {};
  const ids = new Set(orderedItems.value.map((item) => String(item?.id ?? "")));
  for (const [id, stars] of Object.entries(roundMap)) {
    const key = String(id);
    if (!ids.has(key)) continue;
    map[key] = stars;
  }
  return map;
}

async function ensureReviewMode() {
  const list = orderedItems.value;
  if (!list.length) {
    /* ... */ return;
  }

  const n = list.length;
  const dynamicBand = Math.max(5, Math.ceil(n * 0.1)); // same as in checkProbeStop

  // Determine indices with robust fallbacks
  let keepIdx = keepMaxIndex.value;
  let tossIdx = tossMinIndex.value;

  if (keepIdx < 0 && tossIdx >= n) {
    // no feedback at all: pick median +/- 1 as a neutral band
    keepIdx = Math.max(0, Math.floor(n / 2) - 1);
    tossIdx = Math.min(n - 1, keepIdx + dynamicBand);
  } else if (tossIdx >= n) {
    // only Keeps: synthesize a lower bound some distance below keepIdx
    tossIdx = Math.min(n - 1, keepIdx + dynamicBand);
  } else if (keepIdx < 0) {
    // only Tosses: synthesize an upper bound some distance above tossIdx
    keepIdx = Math.max(0, tossIdx - dynamicBand);
  }

  const keepScore = getScoreForMapping(list[keepIdx]) ?? 0.5;
  const tossScore = getScoreForMapping(list[tossIdx]) ?? 0.5;

  const scoreValues = list
    .map((item) => getScoreForMapping(item))
    .filter((value) => Number.isFinite(value));
  const minScore = scoreValues.length ? Math.min(...scoreValues) : 0;
  const maxScore = scoreValues.length ? Math.max(...scoreValues) : 1;
  const globalRange = Math.max(0.01, maxScore - minScore);

  // Use local spread, but enforce a minimum based on overall range
  const spread = Math.max(
    0.12,
    Math.abs(keepScore - tossScore),
    globalRange * 0.35,
  );
  const hi5 = Math.max(0.18, spread * 0.9);
  const hi4 = Math.max(0.1, spread * 0.5);
  const mid3 = Math.max(0.07, spread * 0.3);
  const lo1 = Math.max(0.18, spread * 0.9);

  const useQuantiles = globalRange < 0.35 || spread < 0.22;

  sCut.value = 0.5 * (keepScore + tossScore);
  isBuildingReview.value = true;
  reviewProgress.value = 0;
  await tick();
  if (roundNumber.value === 1) {
    provisionalMap.value = buildRoundOneMapByRank(list);
    reviewProgress.value = list.length;
  } else {
    provisionalMap.value = useQuantiles
      ? await buildProvisionalMapWithQuantilesAsync()
      : await buildProvisionalMapWithLocalAsync(sCut.value, {
          hi5,
          hi4,
          mid3,
          lo1,
        });
    provisionalMap.value = applyRoundOneMap(provisionalMap.value);
  }
  isBuildingReview.value = false;
  logCalibrationDecision("probe-stop", {
    keepIdx,
    tossIdx,
    keepScore,
    tossScore,
    cutoff: sCut.value,
    spread,
    globalRange,
    strategy: useQuantiles ? "quantiles" : "local-spread",
  });
  phase.value = "review";
  emitSessionUpdate();
}

function getQuantileCutoffs(values, points) {
  if (!values.length) return points.map(() => 0);
  const sorted = [...values].sort((a, b) => a - b);
  const lastIndex = sorted.length - 1;
  return points.map((p) => {
    const idx = Math.min(lastIndex, Math.max(0, Math.floor(p * lastIndex)));
    return sorted[idx];
  });
}

function getQuantileTargets() {
  if (roundNumber.value === 1) {
    return [0.97, 0.85, 0.45, 0.2];
  }
  return [0.85, 0.65, 0.35, 0.15];
}

function buildRoundOneMapByRank(items) {
  const total = items.length;
  if (!total) return {};
  const gemRate = 0.05;
  const trashRate = 0.1;
  const gemCount =
    total >= 8
      ? Math.max(1, Math.round(total * gemRate))
      : Math.round(total * gemRate);
  const trashCount = Math.max(1, Math.round(total * trashRate));

  const scored = items
    .map((item) => ({
      id: item.id,
      score: getScoreForMapping(item),
    }))
    .filter((entry) => Number.isFinite(entry.score))
    .sort((a, b) => b.score - a.score);

  const map = {};
  const topIds = new Set(scored.slice(0, gemCount).map((entry) => entry.id));
  const bottomIds = new Set(
    scored
      .slice(Math.max(0, scored.length - trashCount))
      .map((entry) => entry.id),
  );

  for (const item of items) {
    if (topIds.has(item.id)) map[item.id] = 5;
    else if (bottomIds.has(item.id)) map[item.id] = 1;
    else map[item.id] = 3;
  }
  return map;
}

function buildProvisionalMapWithQuantiles() {
  const map = {};
  if (roundNumber.value === 1) {
    return buildRoundOneMapByRank(orderedItems.value);
  }
  const scores = orderedItems.value
    .map((item) => getScoreForMapping(item))
    .filter((value) => Number.isFinite(value));
  const [q85, q65, q35, q15] = getQuantileCutoffs(scores, getQuantileTargets());

  for (const item of orderedItems.value) {
    const score = getScoreForMapping(item);
    let stars = 3;
    if (score >= q85) stars = 5;
    else if (score >= q65) stars = 4;
    else if (score >= q35) stars = 3;
    else if (score >= q15) stars = 2;
    else stars = 1;
    map[item.id] = stars;
  }
  return map;
}

function buildProvisionalMapWithLocal(cutoff, L) {
  const map = {};
  for (const item of orderedItems.value) {
    const score = getScoreForMapping(item);
    let stars = 2;
    if (score >= cutoff + L.hi5) stars = 5;
    else if (score >= cutoff + L.hi4) stars = 4;
    else if (Math.abs(score - cutoff) < L.mid3) stars = 3;
    else if (score <= cutoff - L.lo1) stars = 1;
    else stars = 2;
    map[item.id] = stars;
  }
  return map;
}

function tick() {
  return new Promise((resolve) => requestAnimationFrame(resolve));
}

async function buildProvisionalMapWithLocalAsync(cutoff, L) {
  const map = {};
  const items = orderedItems.value;
  const total = items.length;
  reviewTotal.value = total;
  for (let i = 0; i < total; i += 1) {
    const item = items[i];
    const score = getScoreForMapping(item);
    let stars = 2;
    if (score >= cutoff + L.hi5) stars = 5;
    else if (score >= cutoff + L.hi4) stars = 4;
    else if (Math.abs(score - cutoff) < L.mid3) stars = 3;
    else if (score <= cutoff - L.lo1) stars = 1;
    else stars = 2;
    map[item.id] = stars;

    if (i % 200 === 0) {
      reviewProgress.value = i;
      await tick();
    }
  }
  reviewProgress.value = total;
  return map;
}

async function buildProvisionalMapWithQuantilesAsync() {
  const map = {};
  const items = orderedItems.value;
  const total = items.length;
  if (roundNumber.value === 1) {
    const roundMap = buildRoundOneMapByRank(items);
    reviewTotal.value = total;
    reviewProgress.value = total;
    return roundMap;
  }
  const scores = items
    .map((item) => getScoreForMapping(item))
    .filter((value) => Number.isFinite(value));
  const [q85, q65, q35, q15] = getQuantileCutoffs(scores, getQuantileTargets());

  reviewTotal.value = total;
  for (let i = 0; i < total; i += 1) {
    const item = items[i];
    const score = getScoreForMapping(item);
    let stars = 3;
    if (score >= q85) stars = 5;
    else if (score >= q65) stars = 4;
    else if (score >= q35) stars = 3;
    else if (score >= q15) stars = 2;
    else stars = 1;
    map[item.id] = stars;

    if (i % 200 === 0) {
      reviewProgress.value = i;
      await tick();
    }
  }
  reviewProgress.value = total;
  return map;
}

function buildProvisionalMap(cutoff) {
  const map = {};
  const mapping = DEFAULT_CONFIG.mapping;
  const margin = DEFAULT_CONFIG.margin;
  for (const item of orderedItems.value) {
    const score = getScoreForMapping(item);
    let stars = 2;
    if (score >= cutoff + mapping.high5) stars = 5;
    else if (score >= cutoff + mapping.high4) stars = 4;
    else if (Math.abs(score - cutoff) < margin) stars = 3;
    else if (score <= cutoff - mapping.low1) stars = 1;
    else stars = 2;
    map[item.id] = stars;
  }
  return map;
}

async function buildProvisionalMapAsync(cutoff) {
  const map = {};
  const mapping = DEFAULT_CONFIG.mapping;
  const margin = DEFAULT_CONFIG.margin;
  const items = orderedItems.value;
  const total = items.length;
  reviewTotal.value = total;
  for (let i = 0; i < total; i += 1) {
    const item = items[i];
    const score = getScoreForMapping(item);
    let stars = 2;
    if (score >= cutoff + mapping.high5) stars = 5;
    else if (score >= cutoff + mapping.high4) stars = 4;
    else if (Math.abs(score - cutoff) < margin) stars = 3;
    else if (score <= cutoff - mapping.low1) stars = 1;
    else stars = 2;
    map[item.id] = stars;

    if (i % 200 === 0) {
      reviewProgress.value = i;
      await tick();
    }
  }
  reviewProgress.value = total;
  return map;
}

function setProvisionalStars(itemId, stars) {
  const next = Math.max(1, Math.min(5, Number(stars) || 1));
  manualOverrides.value = new Set([...manualOverrides.value, itemId]);
  provisionalMap.value = {
    ...provisionalMap.value,
    [itemId]: next,
  };
  emitSessionUpdate();
}

function applyComparisonBias(betterItem, worseItem) {
  if (!betterItem || !worseItem) return;
  const epsilon = 0.06;
  const betterScore = getScoreForMapping(betterItem);
  const worseScore = getScoreForMapping(worseItem);
  const delta = Math.max(0, worseScore - betterScore + epsilon);
  if (!delta) return;
  const next = { ...comparisonBias.value };
  const betterId = betterItem.id;
  const worseId = worseItem.id;
  next[betterId] = Math.min(0.5, (next[betterId] ?? 0) + delta);
  next[worseId] = Math.max(-0.5, (next[worseId] ?? 0) - delta);
  comparisonBias.value = next;
}

function chooseComparison(result) {
  const pair = comparePair.value;
  if (!pair) return;
  const a = Math.min(pair.leftIndex, pair.rightIndex);
  const b = Math.max(pair.leftIndex, pair.rightIndex);
  lastComparePair.value = { a, b };
  lastCompareItems.value = [pair.left?.id, pair.right?.id].filter(
    (id) => id != null,
  );
  usedComparePairs.value = new Set([...usedComparePairs.value, pair.key]);
  compareCursor.value += 1;
  pairwiseCount.value += 1;
  if (result === "left" || result === "right") {
    const better = result === "left" ? pair.left : pair.right;
    const worse = result === "left" ? pair.right : pair.left;
    const betterIndex = result === "left" ? pair.leftIndex : pair.rightIndex;
    const worseIndex = result === "left" ? pair.rightIndex : pair.leftIndex;
    applyComparisonBias(better, worse);
    if (Number.isInteger(betterIndex)) {
      keepMaxIndex.value = Math.max(keepMaxIndex.value, betterIndex);
    }
    if (Number.isInteger(worseIndex)) {
      tossMinIndex.value = Math.min(tossMinIndex.value, worseIndex);
    }
  }
  probesDone.value += 1;
  checkProbeStop();
}

function getInitialPivotIndex(list) {
  if (!list.length) return -1;
  const target = 0.5;
  const mid = Math.floor((list.length - 1) / 2);
  let bestIndex = mid;
  let bestDiff = Infinity;
  for (let i = 0; i < list.length; i += 1) {
    const score = getCalibratedScore(list[i]);
    const diff = Math.abs(score - target);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestIndex = i;
    } else if (diff === bestDiff) {
      if (Math.abs(i - mid) < Math.abs(bestIndex - mid)) {
        bestIndex = i;
      }
    }
  }
  return Math.round((bestIndex + mid) / 2);
}

const currentIndex = computed(() => {
  if (!orderedItems.value.length) return -1;
  if (
    keepMaxIndex.value < 0 &&
    tossMinIndex.value === orderedItems.value.length
  ) {
    return getInitialPivotIndex(orderedItems.value);
  }
  return Math.floor((keepMaxIndex.value + tossMinIndex.value) / 2);
});

function logProbeState(action) {
  console.log("[InteractiveScoring]", action, {
    probeIndex: currentIndex.value,
    keepMaxIndex: keepMaxIndex.value,
    tossMinIndex: tossMinIndex.value,
    probesDone: probesDone.value,
    total: orderedItems.value.length,
  });
}

const currentItem = computed(() => {
  if (phase.value !== "probe") return orderedItems.value[0] || null;
  if (
    currentIndex.value < 0 ||
    currentIndex.value >= orderedItems.value.length
  ) {
    return (
      orderedItems.value[Math.floor((orderedItems.value.length - 1) / 2)] ||
      null
    );
  }
  return orderedItems.value[currentIndex.value];
});

function getPairKey(leftItem, rightItem) {
  const a = String(leftItem?.id ?? "");
  const b = String(rightItem?.id ?? "");
  return a < b ? `${a}-${b}` : `${b}-${a}`;
}

function buildCompareCandidates(center, listLength) {
  const offsets = [0, 1, -1, 2, -2, 3, -3];
  const pairs = [];
  const seen = new Set();
  for (const offset of offsets) {
    const left = center + offset;
    if (left < 0 || left >= listLength) continue;
    const right = left + 1 < listLength ? left + 1 : left - 1;
    if (right < 0 || right >= listLength || right === left) continue;
    const min = Math.min(left, right);
    const max = Math.max(left, right);
    const key = `${min}-${max}`;
    if (seen.has(key)) continue;
    seen.add(key);
    pairs.push([left, right]);
  }
  return pairs;
}

function pickAnchorForItem(item) {
  const anchors = anchorsByBucket.value;
  if (!item || !anchors) return null;
  const recent = new Set(lastCompareItems.value || []);
  const anchor3 = anchors[3] || null;
  const anchor4 = anchors[4] || null;
  const usable3 = anchor3 && !recent.has(anchor3.id);
  const usable4 = anchor4 && !recent.has(anchor4.id);
  if (!usable3 && !usable4) return null;
  if (usable3 && !usable4) return anchor3;
  if (usable4 && !usable3) return anchor4;
  const itemScore = getCalibratedScore(item);
  const d3 = Math.abs(itemScore - getCalibratedScore(anchor3));
  const d4 = Math.abs(itemScore - getCalibratedScore(anchor4));
  return d3 <= d4 ? anchor3 : anchor4;
}

const comparePair = computed(() => {
  if (phase.value !== "probe") return null;
  const list = orderedItems.value;
  if (!list.length) return null;
  const center = currentIndex.value;
  if (center < 0 || center >= list.length) return null;
  const current = list[center];
  const anchor =
    pairwiseCount.value < DEFAULT_CONFIG.pairwiseBudget
      ? pickAnchorForItem(current)
      : null;
  if (anchor) {
    const key = getPairKey(current, anchor);
    if (!usedComparePairs.value.has(key)) {
      return {
        left: current,
        right: anchor,
        leftIndex: center,
        rightIndex: null,
        key,
        kind: "anchor",
      };
    }
  }
  const candidates = buildCompareCandidates(center, list.length);
  if (!candidates.length) return null;

  const used = usedComparePairs.value;
  const recent = new Set(lastCompareItems.value || []);
  let selected = null;
  for (let i = 0; i < candidates.length; i += 1) {
    const idx = (compareCursor.value + i) % candidates.length;
    const [a, b] = candidates[idx];
    const key = getPairKey(list[a], list[b]);
    if (
      !used.has(key) &&
      !recent.has(list[a]?.id) &&
      !recent.has(list[b]?.id)
    ) {
      selected = [a, b];
      break;
    }
  }

  if (!selected) {
    for (let i = 0; i < candidates.length; i += 1) {
      const idx = (compareCursor.value + i) % candidates.length;
      const [a, b] = candidates[idx];
      const key = getPairKey(list[a], list[b]);
      if (!used.has(key)) {
        selected = [a, b];
        break;
      }
    }
  }

  if (!selected) {
    selected = candidates[0];
  }

  if (!selected) return null;
  const [leftIndex, rightIndex] = selected;
  return {
    left: list[leftIndex],
    right: list[rightIndex],
    leftIndex,
    rightIndex,
    key: getPairKey(list[leftIndex], list[rightIndex]),
    kind: "unscored",
  };
});

const showCompare = computed(() => {
  if (phase.value !== "probe") return false;
  if (isBuildingReview.value) return false;
  const haveKeep = keepMaxIndex.value >= 0;
  const haveToss = tossMinIndex.value < orderedItems.value.length;
  if (!haveKeep || !haveToss) return false;
  if (probesDone.value < 2) return false;
  const pair = comparePair.value;
  if (!pair) return false;
  if (pair.kind === "anchor") return true;
  const leftScore = getCalibratedScore(pair.left);
  const rightScore = getCalibratedScore(pair.right);
  const diff = Math.abs(leftScore - rightScore);
  return diff <= 0.04;
});

const summaryBuckets = computed(() => {
  const counts = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  for (const stars of Object.values(provisionalMap.value)) {
    if (counts[stars] != null) counts[stars] += 1;
  }
  return Object.keys(counts)
    .map((stars) => ({ stars, count: counts[stars] }))
    .reverse();
});

const proposedItems = computed(() => {
  return orderedItems.value
    .filter((item) => provisionalMap.value[item.id] != null)
    .map((item) => ({
      ...item,
      provisionalStars: provisionalMap.value[item.id],
      isAdjusted: manualOverrides.value.has(item.id),
    }));
});

const roundGems = computed(() =>
  proposedItems.value.filter((item) => item.provisionalStars === 5),
);
const roundTrash = computed(() =>
  proposedItems.value.filter((item) => item.provisionalStars === 1),
);
const roundIncludedMap = computed(() => {
  const exclude = roundExcludedIds.value;
  const map = {};
  for (const item of proposedItems.value) {
    if (exclude.has(item.id)) continue;
    if (item.provisionalStars === 5 || item.provisionalStars === 1) {
      map[item.id] = item.provisionalStars;
    }
  }
  return map;
});
const roundPercent = computed(() => {
  const total = orderedItems.value.length || 1;
  const gems = roundGems.value.length;
  const trash = roundTrash.value.length;
  return {
    gems: Math.round((gems / total) * 100),
    trash: Math.round((trash / total) * 100),
  };
});

function chooseKeep() {
  if (!orderedItems.value.length) return;
  logProbeState("keep-before");
  keepMaxIndex.value = currentIndex.value;
  probesDone.value += 1;
  logProbeState("keep-after");
  checkProbeStop();
}

function chooseToss() {
  if (!orderedItems.value.length) return;
  logProbeState("toss-before");
  tossMinIndex.value = currentIndex.value;
  probesDone.value += 1;
  logProbeState("toss-after");
  checkProbeStop();
}

function checkProbeStop() {
  logProbeState("check");
  const n = orderedItems.value.length;
  const haveKeep = keepMaxIndex.value >= 0;
  const haveToss = tossMinIndex.value < n;
  const canStopByProbeCap = probesDone.value >= DEFAULT_CONFIG.maxProbes;

  if (haveKeep && haveToss && canStopByProbeCap) {
    logProbeState("stop");
    isBuildingReview.value = true;
    void ensureReviewMode();
  } else {
    emitSessionUpdate();
  }
}

function confirmScores(continueScoring = false) {
  const mapToScore =
    roundNumber.value === 1 ? roundIncludedMap.value : provisionalMap.value;
  emit("confirm", {
    provisionalMap: mapToScore,
    continueScoring,
    roundNumber: roundNumber.value,
    roundOneMap:
      roundNumber.value === 1 ? roundIncludedMap.value : roundOneMap.value,
    session: {
      cutoff: sCut.value,
      margin: DEFAULT_CONFIG.margin,
      probes: probesDone.value,
    },
  });
}

function discardScores() {
  provisionalMap.value = {};
  manualOverrides.value = new Set();
  roundExcludedIds.value = new Set();
  comparisonBias.value = {};
  lastComparePair.value = null;
  lastCompareItems.value = [];
  usedComparePairs.value = new Set();
  compareCursor.value = 0;
  pairwiseCount.value = 0;
  emit("discard");
}

function handleClose() {
  emit("close");
}

function openPreview(item) {
  if (!item || !item.id) return;
  previewItem.value = item;
}

function closePreview() {
  previewItem.value = null;
}

function handleKeyDown(event) {
  if (event.key !== "Escape") return;
  if (previewItem.value) {
    closePreview();
  } else {
    handleClose();
  }
}

function openFullImage(item) {
  if (!item || !item.id) return;
  const url = getFullImageUrl(item);
  if (!url) return;
  window.open(url, "_blank", "noopener");
}

function toggleRoundExclude(id) {
  if (id == null) return;
  const next = new Set(roundExcludedIds.value);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  roundExcludedIds.value = next;
}

function isVideo(item) {
  const format = getOverlayFormat(item) || "";
  return isSupportedVideoFile(format);
}

function isCompareVideo(item) {
  const format = getOverlayFormat(item) || "";
  if (format) return isSupportedVideoFile(format);
  const url = getFullImageUrl(item);
  const clean = url.split("?")[0];
  return isSupportedVideoFile(clean);
}

function getFullImageUrl(item) {
  if (!item || !item.id) return "";
  const rawFormat = item.format
    ? String(item.format).toLowerCase()
    : (getOverlayFormat(item) || "").toLowerCase();
  const format =
    rawFormat &&
    (isSupportedImageFile(rawFormat) || isSupportedVideoFile(rawFormat))
      ? rawFormat
      : "";
  const idString = String(item.id);
  const ext = format && !idString.includes(".") ? `.${format}` : "";
  const cacheBuster = item.pixel_sha ? `?v=${item.pixel_sha}` : "";
  return `${props.backendUrl}/pictures/${item.id}${ext}${cacheBuster}`;
}

function getPreviewUrl(item) {
  if (item?.thumbnail) return item.thumbnail;
  return getFullImageUrl(item);
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    const normalized = normalizeItems(props.items);
    if (!normalized.length) {
      orderedItems.value = [];
      resetSessionState();
      phase.value = "review";
      provisionalMap.value = {};
      emitSessionUpdate();
      return;
    }

    orderedItems.value = normalized;
    if (!hydrateFromSession(props.session)) {
      resetSessionState();
      if (orderedItems.value.length < DEFAULT_CONFIG.bandSizeCount) {
        sCut.value =
          orderedItems.value[Math.floor(orderedItems.value.length / 2)]
            ?.smartScore ?? 0.5;
        isBuildingReview.value = true;
        reviewProgress.value = 0;
        tick()
          .then(() => buildProvisionalMapAsync(sCut.value))
          .then((map) => {
            provisionalMap.value = map;
            isBuildingReview.value = false;
            logCalibrationDecision("auto-review", {
              cutoff: sCut.value,
              itemCount: orderedItems.value.length,
            });
            phase.value = "review";
            emitSessionUpdate();
          });
      }
    }
    emitSessionUpdate();
  },
  { immediate: true },
);

onMounted(() => {
  window.addEventListener("keydown", handleKeyDown);
});

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeyDown);
});
</script>

<style scoped>
.scoring-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.9);
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
}

.scoring-shell {
  width: min(1100px, 92vw);
  height: 92vh;
  max-height: 92vh;
  background: rgba(var(--v-theme-surface), 0.95);
  color: rgb(var(--v-theme-on-surface));
  border-radius: 16px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.scoring-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.scoring-close {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: none;
  color: inherit;
  cursor: pointer;
}

.scoring-title {
  font-weight: 700;
  font-size: 1.1rem;
}

.scoring-meta {
  margin-left: auto;
  font-size: 0.9rem;
  opacity: 0.8;
}

.scoring-body {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.scoring-body-review {
  flex: 1;
  min-height: 0;
}

.scoring-media {
  display: flex;
  justify-content: center;
}

.scoring-compare {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.scoring-compare-card {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
  background: rgba(var(--v-theme-surface), 0.2);
}

.scoring-compare-button {
  width: 100%;
  display: block;
  border: 2px solid transparent;
  border-radius: 12px;
  padding: 0;
  background: transparent;
  cursor: pointer;
  position: relative;
}

.scoring-compare-button:disabled {
  cursor: wait;
  opacity: 0.6;
}

.scoring-compare-button:focus-visible {
  outline: 2px solid rgba(var(--v-theme-primary), 0.9);
  outline-offset: 2px;
}

.scoring-compare-image {
  width: 100%;
  object-fit: contain;
  background: rgba(0, 0, 0, 0.2);
  display: block;
}

.scoring-compare-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  color: #fff;
  background: rgba(var(--v-theme-primary), 0.25);
  opacity: 0;
  transition: opacity 0.15s ease;
}

.scoring-compare-button:hover {
  border-color: rgba(var(--v-theme-primary), 0.9);
}

.scoring-compare-button:hover .scoring-compare-overlay {
  opacity: 1;
}

.scoring-image,
.scoring-video {
  max-width: 100%;
  max-height: 60vh;
  border-radius: 12px;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.35);
}

.scoring-actions,
.scoring-review {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.scoring-review {
  flex: 1;
  width: 100%;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.scoring-progress {
  width: min(520px, 100%);
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  padding: 12px 0;
}

.scoring-progress-label {
  font-size: 0.85rem;
  opacity: 0.8;
}

.scoring-buttons {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
}

.scoring-button {
  padding: 8px 16px;
  border-radius: 8px;
  border: none;
  cursor: pointer;
  font-weight: 600;
  background: rgba(var(--v-theme-primary), 0.9);
  color: rgb(var(--v-theme-on-primary));
  transition: background 0.15s ease;
}

.scoring-button:hover {
  background-color: rgb(var(--v-theme-accent));
  transition: background 0.15s ease;
}

.scoring-prompt {
  font-size: 1rem;
  font-weight: 600;
}

.scoring-summary {
  background: rgba(var(--v-theme-surface), 0.35);
  padding: 12px 16px;
  border-radius: 12px;
  width: min(420px, 100%);
}

.scoring-grid-wrapper {
  flex: 1;
  min-height: 0;
  width: 100%;
  padding: 4px;
  border-radius: 12px;
  background: rgba(var(--v-theme-surface), 0.2);
  overflow-y: auto;
  max-height: 70vh;
}

.scoring-review-actions {
  margin-top: auto;
  padding-top: 4px;
}

.scoring-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
}

.scoring-grid-card {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  background: rgba(var(--v-theme-surface), 0.3);
  cursor: zoom-in;
}

.scoring-grid-image {
  width: 100%;
  aspect-ratio: 1 / 1;
  height: auto;
  object-fit: cover;
  display: block;
}

.scoring-grid-stars {
  position: absolute;
  inset: auto 6px 6px 6px;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 6px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 0.85rem;
}

.scoring-grid-star {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}

.scoring-grid-star--unadjusted:hover .v-icon {
  color: rgba(var(--v-theme-secondary), 0.95) !important;
}

.scoring-grid-star:focus-visible {
  outline: 2px solid rgba(var(--v-theme-primary), 0.8);
  outline-offset: 2px;
  border-radius: 6px;
}

.scoring-grid-label {
  margin-left: 4px;
  font-weight: 600;
}

.scoring-grid-label--adjusted {
  color: rgba(var(--v-theme-secondary), 0.95);
}

.scoring-summary-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  font-size: 0.9rem;
}

.scoring-round-summary {
  width: 100%;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(var(--v-theme-surface), 0.25);
  text-align: center;
  font-weight: 600;
}

.scoring-round-line {
  font-size: 0.95rem;
}

.scoring-round-section {
  margin-bottom: 16px;
}

.scoring-round-title {
  font-weight: 700;
  margin: 8px 0;
}

.scoring-round-toggle {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 4px 8px;
  border-radius: 8px;
  border: none;
  font-size: 0.75rem;
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
  cursor: pointer;
}

.scoring-preview-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  z-index: 10050;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.scoring-preview-body {
  max-width: 92vw;
  max-height: 88vh;
  display: flex;
  align-items: center;
  justify-content: center;
}

.scoring-preview-media {
  max-width: 92vw;
  max-height: 88vh;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  background: rgba(0, 0, 0, 0.2);
}

.scoring-preview-close {
  align-self: flex-end;
  margin-bottom: 12px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 6px 10px;
  cursor: pointer;
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

.scoring-summary-pill {
  background: rgba(var(--v-theme-surface), 0.25);
  padding: 4px 10px;
  border-radius: 999px;
}

.summary-title {
  font-weight: 600;
  margin-bottom: 8px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 12px;
  font-size: 0.95rem;
}

.scoring-empty {
  text-align: center;
  opacity: 0.7;
}
</style>
