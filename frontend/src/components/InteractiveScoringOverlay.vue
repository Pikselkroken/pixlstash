<template>
  <div v-if="open" class="scoring-overlay" @click.self="handleClose">
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
            >
              <img
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
            >
              <img
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

        <div class="scoring-actions" v-if="showCompare">
          <div class="scoring-prompt">Click on the best image.</div>
          <div class="scoring-buttons">
            <v-btn variant="outlined" @click="chooseComparison('same')">
              They are the same
            </v-btn>
          </div>
        </div>

        <div
          class="scoring-actions"
          v-else-if="phase === 'probe' && currentItem"
        >
          <div class="scoring-prompt">Good enough to keep?</div>
          <div class="scoring-buttons">
            <v-btn color="primary" variant="elevated" @click="chooseKeep">
              Keep
            </v-btn>
            <v-btn color="error" variant="outlined" @click="chooseToss">
              Toss
            </v-btn>
          </div>
        </div>

        <div class="scoring-review" v-else-if="phase === 'review'">
          <div class="scoring-grid-wrapper">
            <div class="scoring-grid">
              <div
                v-for="item in proposedItems"
                :key="item.id"
                class="scoring-grid-card"
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
          <div class="scoring-buttons">
            <v-btn color="primary" variant="elevated" @click="confirmScores">
              Confirm Scores
            </v-btn>
            <v-btn variant="outlined" @click="discardScores">Discard</v-btn>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, watch, ref } from "vue";
import { isSupportedVideoFile, getOverlayFormat } from "../utils/media.js";

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
const manualOverrides = ref(new Set());
const comparisonBias = ref({});
const lastComparePair = ref(null);
const usedComparePairs = ref(new Set());

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
  if (!model) return base;
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

function resetSessionState() {
  phase.value = "probe";
  probesDone.value = 0;
  keepMaxIndex.value = -1;
  tossMinIndex.value = orderedItems.value.length;
  sCut.value = null;
  provisionalMap.value = {};
  manualOverrides.value = new Set();
  comparisonBias.value = {};
  lastComparePair.value = null;
  usedComparePairs.value = new Set();
}

function hydrateFromSession(session) {
  if (!session) return false;
  const hasState =
    session.phase ||
    typeof session.probesDone === "number" ||
    session.provisionalMap ||
    typeof session.keepMaxIndex === "number" ||
    typeof session.tossMinIndex === "number";
  if (!hasState) return false;
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
  if (Array.isArray(session.usedComparePairs)) {
    usedComparePairs.value = new Set(session.usedComparePairs);
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
    usedComparePairs: Array.from(usedComparePairs.value),
  });
}

function ensureReviewMode() {
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

  // Use local spread to derive thresholds
  const spread = Math.max(0.01, Math.abs(keepScore - tossScore)); // guard against 0
  const hi4 = 0.5 * spread; // e.g., 50% of local gap
  const hi5 = 1.0 * spread; // e.g., 100% of local gap
  const mid3 = 0.5 * spread; // inside ±mid3 => 3★
  const lo1 = 1.0 * spread;

  sCut.value = 0.5 * (keepScore + tossScore);
  provisionalMap.value = buildProvisionalMapWithLocal(sCut.value, {
    hi5,
    hi4,
    mid3,
    lo1,
  });
  logCalibrationDecision("probe-stop", {
    keepIdx,
    tossIdx,
    keepScore,
    tossScore,
    cutoff: sCut.value,
    spread,
  });
  phase.value = "review";
  emitSessionUpdate();
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
  usedComparePairs.value = new Set([...usedComparePairs.value, `${a}-${b}`]);
  if (result === "left" || result === "right") {
    const better = result === "left" ? pair.left : pair.right;
    const worse = result === "left" ? pair.right : pair.left;
    const betterIndex = result === "left" ? pair.leftIndex : pair.rightIndex;
    const worseIndex = result === "left" ? pair.rightIndex : pair.leftIndex;
    applyComparisonBias(better, worse);
    keepMaxIndex.value = Math.max(keepMaxIndex.value, betterIndex);
    tossMinIndex.value = Math.min(tossMinIndex.value, worseIndex);
  }
  probesDone.value += 1;
  checkProbeStop();
}

const currentIndex = computed(() => {
  if (!orderedItems.value.length) return -1;
  if (
    keepMaxIndex.value < 0 &&
    tossMinIndex.value === orderedItems.value.length
  ) {
    return Math.floor((orderedItems.value.length - 1) / 2);
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

const comparePair = computed(() => {
  if (phase.value !== "probe") return null;
  const list = orderedItems.value;
  if (!list.length) return null;
  const center = currentIndex.value;
  if (center < 0 || center >= list.length) return null;
  const candidates = [
    [center, center + 1],
    [center - 1, center],
    [center, center - 1],
    [center + 1, center + 2],
    [center - 2, center - 1],
  ]
    .map(([a, b]) => [a, b])
    .filter(([a, b]) => a >= 0 && b >= 0 && a < list.length && b < list.length)
    .filter(([a, b]) => a !== b);

  const used = usedComparePairs.value;
  let selected =
    candidates.find(([a, b]) => {
      const min = Math.min(a, b);
      const max = Math.max(a, b);
      return !used.has(`${min}-${max}`);
    }) || null;

  if (!selected && candidates.length) {
    selected = candidates[0];
  }

  if (!selected) return null;
  const [leftIndex, rightIndex] = selected;
  return {
    left: list[leftIndex],
    right: list[rightIndex],
    leftIndex,
    rightIndex,
  };
});

const showCompare = computed(() => {
  if (phase.value !== "probe") return false;
  const pair = comparePair.value;
  if (!pair) return false;
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
  const remainingBand = tossMinIndex.value - keepMaxIndex.value;
  const haveKeep = keepMaxIndex.value >= 0;
  const haveToss = tossMinIndex.value < n;
  const dynamicBand = Math.max(5, Math.ceil(n * 0.1));

  const canStopByBand =
    haveKeep &&
    haveToss &&
    remainingBand <= dynamicBand &&
    probesDone.value >= MIN_PROBES;

  const canStopByProbeCap = probesDone.value >= DEFAULT_CONFIG.maxProbes;

  if (canStopByBand || canStopByProbeCap) {
    logProbeState("stop");
    ensureReviewMode();
  } else {
    emitSessionUpdate();
  }
}

function confirmScores() {
  emit("confirm", {
    provisionalMap: provisionalMap.value,
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
  comparisonBias.value = {};
  lastComparePair.value = null;
  usedComparePairs.value = new Set();
  emit("discard");
}

function handleClose() {
  emit("close");
}

function isVideo(item) {
  const format = getOverlayFormat(item) || "";
  return isSupportedVideoFile(format);
}

function getFullImageUrl(item) {
  if (!item || !item.id) return "";
  const format = item.format ? String(item.format).toLowerCase() : "";
  const ext = format ? `.${format}` : "";
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
        provisionalMap.value = buildProvisionalMap(sCut.value);
        logCalibrationDecision("auto-review", {
          cutoff: sCut.value,
          itemCount: orderedItems.value.length,
        });
        phase.value = "review";
      }
    }
    emitSessionUpdate();
  },
  { immediate: true },
);
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
}

.scoring-buttons {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
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
  padding: 6px;
  border-radius: 12px;
  background: rgba(var(--v-theme-surface), 0.2);
  overflow-y: auto;
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
