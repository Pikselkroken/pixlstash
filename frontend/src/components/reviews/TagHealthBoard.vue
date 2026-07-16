<template>
  <div class="rs-board">
    <div class="rs-board-inner">
      <div class="rs-board-heading">
        <h2 class="rs-board-title">Which tags need review?</h2>
        <span class="rs-board-subtitle">{{ subtitle }}</span>
      </div>

      <div class="rs-board-controls">
        <button
          v-if="totalDisputes > 0"
          class="rs-board-disputes"
          :class="{ 'rs-board-disputes--on': disputesOnly }"
          type="button"
          :aria-pressed="disputesOnly"
          title="Show only tags where the current model disputes your earlier calls"
          @click="disputesOnly = !disputesOnly"
        >
          <v-icon size="14">mdi-account-alert-outline</v-icon>
          The current model disputes {{ totalDisputes }} of your earlier calls
        </button>
        <span class="rs-board-controls-spacer"></span>
        <div class="rs-board-filter">
          <v-icon size="15" class="rs-board-filter-icon">mdi-magnify</v-icon>
          <input
            ref="filterRef"
            v-model="filter"
            class="rs-board-filter-input"
            type="text"
            placeholder="Filter tags… ( / )"
            @keydown.escape.stop.prevent="clearFilter"
          />
        </div>
        <button
          class="rs-board-anomaly-toggle"
          :class="{ 'rs-board-anomaly-toggle--on': anomalyOnly }"
          type="button"
          :aria-pressed="anomalyOnly"
          title="Only show smart-score penalised tags"
          @click="anomalyOnly = !anomalyOnly"
        >
          <v-icon size="15">mdi-alert-octagon-outline</v-icon>
          Anomalies only
        </button>
        <select
          class="rs-board-sort"
          :value="sort.key"
          title="Sort the board"
          @change="pickSort($event.target.value, $event)"
        >
          <option v-for="o in SORT_OPTS" :key="o.key" :value="o.key">
            Sort: {{ o.label }}
          </option>
        </select>
      </div>

      <!-- Cache (re)build in progress: show the bar, keep any stale rows below. -->
      <div v-if="store.healthBuilding" class="rs-board-building">
        <span class="rs-board-building-label">
          <v-icon size="15" class="mdi-spin">mdi-loading</v-icon>
          Building tag health signals…
        </span>
        <span class="rs-board-building-bar">
          <span
            class="rs-board-building-fill"
            :style="{ width: `${Math.round(store.healthProgress * 100)}%` }"
          ></span>
        </span>
      </div>

      <div
        v-if="!store.healthBuilding && !sorted.length && !store.healthLoading"
        class="rs-board-empty"
      >
        <template v-if="store.healthRows.length">
          No tags match the current filters.
        </template>
        <template v-else>
          No tag health data yet.
          <button
            class="rs-board-rebuild"
            type="button"
            @click="store.rebuildHealth()"
          >
            Build now
          </button>
        </template>
      </div>

      <div v-if="sorted.length" class="rs-board-table">
        <div class="rs-board-row rs-board-row--head">
          <component
            :is="h.key ? 'button' : 'span'"
            v-for="h in headers"
            :key="h.label || h.icon"
            class="rs-board-hdr"
            :class="{
              'rs-board-hdr--center': h.center,
              'rs-board-hdr--active': h.key && sort.key === h.key,
            }"
            :type="h.key ? 'button' : undefined"
            :title="h.tip || undefined"
            @click="h.key && toggleSort(h.key)"
          >
            <v-icon v-if="h.icon" size="16">{{ h.icon }}</v-icon>
            <template v-else>{{ h.label }}</template>
            <v-icon v-if="h.key" size="13" class="rs-board-hdr-arrow">
              {{
                sort.key === h.key
                  ? sort.dir === "asc"
                    ? "mdi-arrow-up"
                    : "mdi-arrow-down"
                  : "mdi-unfold-more-horizontal"
              }}
            </v-icon>
          </component>
        </div>

        <div
          v-for="r in sorted"
          :key="r.tag"
          class="rs-board-row"
          :class="{ 'rs-board-row--nomodel': r.has_model === false }"
        >
          <span
            class="rs-board-tag"
            :class="{ 'rs-board-tag--anomaly': isAnomaly(r) }"
          >
            <span class="rs-board-tag-name">{{ r.tag }}</span>
            <v-icon
              v-if="isAnomaly(r)"
              size="14"
              class="rs-board-tag-flag"
              title="smart-score penalised"
              >mdi-alert-octagon-outline</v-icon
            >
            <span
              v-if="r.has_model === false"
              class="rs-board-nomodel-chip"
              title="This tag is not in the tagger's vocabulary; the board only sees neighbour-scan signals"
              >no model signal</span
            >
          </span>
          <span class="rs-board-health">
            <span class="rs-board-health-track">
              <span
                class="rs-board-health-fill"
                :style="healthBarStyle(r)"
              ></span>
            </span>
            <span class="rs-board-health-num">{{ corrections(r) }}</span>
          </span>
          <span class="rs-board-num" :class="numClass(r.est_wrong, 'error')">{{
            r.est_wrong ?? 0
          }}</span>
          <span
            class="rs-board-num"
            :class="numClass(r.est_missing, 'primary')"
            >{{ r.est_missing ?? 0 }}</span
          >
          <span class="rs-board-num" :class="numClass(r.mismatch, 'tertiary')">{{
            r.mismatch ?? 0
          }}</span>
          <span class="rs-board-num rs-board-num--muted"
            >{{ Math.round(r.verified_pct ?? 0) }}%</span
          >
          <span class="rs-board-num rs-board-num--muted">{{
            lastLabel(r)
          }}</span>
          <span class="rs-board-why">{{ whyText(r) }}</span>
          <span class="rs-board-action">
            <button
              v-if="openSessionFor(r.tag)"
              class="rs-board-btn rs-board-btn--open"
              type="button"
              @click="store.openSession(openSessionFor(r.tag).id)"
            >
              Open <v-icon size="14">mdi-arrow-right</v-icon>
            </button>
            <button
              v-else
              class="rs-board-btn"
              type="button"
              @click="emit('start-review', r.tag)"
            >
              Start review
            </button>
          </span>
        </div>
      </div>

      <p v-if="sorted.length" class="rs-board-legend">
        “Est. fixes” = est. wrong + est. missing + mismatches (bar colour:
        <span class="rs-legend-error">red</span> = worst tags,
        <span class="rs-legend-warning">amber</span> = notable,
        <span class="rs-legend-tertiary">teal</span> = minor) · “Est. wrong” =
        tagged pictures the model is ≤10% sure about · “Est. missing” =
        untagged pictures it is ≥90% sure about · “Mismatch” = near-identical
        shots with different labels · “Verified” = share ever human-reviewed ·
        “Last review” = when a review for the tag was last archived.
      </p>
    </div>
  </div>
</template>

<script setup>
// The tag health board: the answer to "what should I review?". Design locked
// per the 2026-07-15 decisions: compact density, icon anomaly marker, heat
// health bar, "Why it ranks here" shown, wide table. Tags outside the tagger
// vocabulary keep an ENABLED "Start review" (kNN review still works) plus a
// "no model signal" chip.
import { computed, ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";

const emit = defineEmits(["start-review"]);
const store = useReviewSessionsStore();

const anomalyOnly = ref(false);
const disputesOnly = ref(false);
const filter = ref("");
const filterRef = ref(null);
const sort = ref({ key: "score", dir: "desc" });

// The overlay routes the `/` shortcut here.
function focusFilter() {
  filterRef.value?.focus();
}
defineExpose({ focusFilter });

function clearFilter() {
  filter.value = "";
  filterRef.value?.blur();
}

const SORT_OPTS = [
  { label: "Suggested (health)", key: "score", dir: "desc" },
  { label: "Tag name (A–Z)", key: "tag", dir: "asc" },
  { label: "Most wrong", key: "wrong", dir: "desc" },
  { label: "Most missing", key: "missing", dir: "desc" },
  { label: "Most conflicts", key: "dups", dir: "desc" },
  { label: "Least verified", key: "verified", dir: "asc" },
  { label: "Recently reviewed", key: "last", dir: "asc" },
];

const SUBTITLE = {
  score: "Sorted by the tags most likely to have tagging mistakes worth fixing.",
  tag: "Sorted alphabetically by tag name.",
  wrong: "Sorted by how many pictures probably have this tag by mistake.",
  missing: "Sorted by how many pictures are probably missing this tag.",
  dups: "Sorted by how many near-identical shots disagree on this tag.",
  verified:
    "Sorted by how much of each tag you’ve confirmed — least-checked first.",
  last: "Sorted by when each tag was last reviewed — longest ago first.",
};

const subtitle = computed(() => SUBTITLE[sort.value.key] || SUBTITLE.score);

const headers = [
  { label: "Tag", key: "tag" },
  {
    label: "Est. fixes",
    key: "score",
    tip: "Estimated fixable labels — est. wrong + est. missing + mismatches",
  },
  {
    label: "Est. wrong",
    key: "wrong",
    center: true,
    tip: "Images that carry this tag but the model thinks shouldn’t",
  },
  {
    label: "Est. missing",
    key: "missing",
    center: true,
    tip: "Images the model thinks should carry this tag but don’t",
  },
  {
    label: "Mismatch",
    key: "dups",
    center: true,
    tip: "Near-identical images (duplicates or burst shots) that disagree on this tag — one has it, the other doesn’t",
  },
  {
    icon: "mdi-check-decagram-outline",
    key: "verified",
    center: true,
    tip: "Verified — share of this tag’s images you’ve already confirmed",
  },
  {
    icon: "mdi-clock-outline",
    key: "last",
    center: true,
    tip: "Last reviewed",
  },
  { label: "Why it ranks here" },
  { label: "" },
];

const totalDisputes = computed(() =>
  store.healthRows.reduce((sum, r) => sum + (r.model_disputes ?? 0), 0),
);

function corrections(r) {
  return (r.est_wrong ?? 0) + (r.est_missing ?? 0) + (r.mismatch ?? 0);
}

function isAnomaly(r) {
  return store.isAnomalyTag(r.tag);
}

// `last_reviewed_at` is not in the /tag_health contract yet — treat a missing
// value as "never" (sorts oldest).
function lastValue(r) {
  const t = r.last_reviewed_at ? new Date(r.last_reviewed_at).getTime() : NaN;
  return Number.isNaN(t) ? 0 : t;
}

function lastLabel(r) {
  if (!r.last_reviewed_at) return "never";
  const d = new Date(r.last_reviewed_at);
  if (Number.isNaN(d.getTime())) return "never";
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return "today";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function keyval(r, key) {
  switch (key) {
    case "tag":
      return r.tag;
    case "score":
      return corrections(r);
    case "wrong":
      return r.est_wrong ?? 0;
    case "missing":
      return r.est_missing ?? 0;
    case "dups":
      return r.mismatch ?? 0;
    case "verified":
      return r.verified_pct ?? 0;
    case "last":
      return lastValue(r);
    default:
      return 0;
  }
}

const sorted = computed(() => {
  const dir = sort.value.dir === "asc" ? 1 : -1;
  const key = sort.value.key;
  const needle = filter.value.trim().toLowerCase();
  return store.healthRows
    .filter((r) => !anomalyOnly.value || isAnomaly(r))
    .filter((r) => !disputesOnly.value || (r.model_disputes ?? 0) > 0)
    .filter((r) => !needle || r.tag.toLowerCase().includes(needle))
    .slice()
    .sort((a, b) => {
      const av = keyval(a, key);
      const bv = keyval(b, key);
      if (typeof av === "string")
        return av.localeCompare(bv) * dir || a.tag.localeCompare(b.tag);
      if (av === bv) return a.tag.localeCompare(b.tag);
      return (av - bv) * dir;
    });
});

function defaultDir(key) {
  return key === "tag" || key === "verified" || key === "last" ? "asc" : "desc";
}

function toggleSort(key) {
  sort.value =
    sort.value.key === key
      ? { key, dir: sort.value.dir === "asc" ? "desc" : "asc" }
      : { key, dir: defaultDir(key) };
}

// The dropdown always applies the option's canonical direction; blur so the
// native <select> doesn't swallow later keystrokes (same fix as the old
// overlay's scope selects).
function pickSort(key, event) {
  const opt = SORT_OPTS.find((o) => o.key === key);
  if (opt) sort.value = { key: opt.key, dir: opt.dir };
  event?.target?.blur();
}

// Heat-coloured "Needs review" bar. The absolute count is printed beside the
// bar; the bar length is scaled absolutely (a fixed "50 corrections = full
// bar" scale), not normalised to the worst tag, so two vaults read alike.
const ABS_FULL_BAR = 50;

function healthBarStyle(r) {
  const score = corrections(r);
  const pct = Math.min(100, Math.round((score / ABS_FULL_BAR) * 100));
  const heat =
    pct > 55
      ? "rgb(var(--v-theme-error))"
      : pct > 25
        ? "rgb(var(--v-theme-warning))"
        : "rgb(var(--v-theme-tertiary))";
  return { width: `${pct}%`, background: heat };
}

function numClass(v, tone) {
  return (v ?? 0) > 0 ? `rs-board-num--${tone}` : "rs-board-num--zero";
}

function whyText(r) {
  if (r.has_model === false)
    return "not in the tagger's vocabulary — similarity review still works";
  return r.why || "";
}

function openSessionFor(tag) {
  return store.sessions.find((s) => s.tag === tag) ?? null;
}
</script>

<style scoped>
.rs-board {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 20px 24px;
}
.rs-board :is(button, input, select):focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
}
.rs-board-inner {
  max-width: 1248px; /* "wide" table width — locked */
  margin: 0 auto;
}
.rs-board-heading {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.rs-board-title {
  font-size: 18px;
  font-weight: var(--weight-bold);
}
.rs-board-subtitle {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.rs-board-controls {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin: 6px 0 14px;
}
.rs-board-controls-spacer {
  flex: 1;
}
.rs-board-disputes {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 11px;
  border-radius: 999px;
  cursor: pointer;
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-tertiary));
  background: color-mix(in srgb, rgb(var(--v-theme-tertiary)) 12%, transparent);
  border: 1px solid
    color-mix(in srgb, rgb(var(--v-theme-tertiary)) 35%, transparent);
}
.rs-board-disputes--on {
  background: color-mix(in srgb, rgb(var(--v-theme-tertiary)) 28%, transparent);
  border-color: rgb(var(--v-theme-tertiary));
}
.rs-board-filter {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.rs-board-filter-icon {
  position: absolute;
  left: 8px;
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  pointer-events: none;
}
.rs-board-filter-input {
  height: 30px;
  width: 190px;
  padding: 0 var(--space-2) 0 28px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
}
.rs-board-filter-input::placeholder {
  color: rgba(var(--v-theme-on-dark-surface), 0.45);
}
.rs-board-anomaly-toggle {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 30px;
  padding: 0 11px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-board-anomaly-toggle--on {
  border-color: rgb(var(--v-theme-error));
  background: color-mix(in srgb, rgb(var(--v-theme-error)) 15%, transparent);
  color: rgb(var(--v-theme-error));
}
.rs-board-sort {
  height: 30px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  cursor: pointer;
  color-scheme: dark;
}
.rs-board-sort option {
  background-color: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
}

.rs-board-building {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  margin-bottom: var(--space-3);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-dark-surface), 0.05);
}
.rs-board-building-label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  white-space: nowrap;
}
.rs-board-building-bar {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
  overflow: hidden;
}
.rs-board-building-fill {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-accent));
  transition: width 0.4s;
}

.rs-board-empty {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}
.rs-board-rebuild {
  height: 28px;
  padding: 0 11px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  cursor: pointer;
}

.rs-board-table {
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: rgba(var(--v-theme-on-dark-surface), 0.03);
}
.rs-board-row {
  display: grid;
  /* Compact density (locked): tag · needs-review · wrong · missing · mismatch ·
     verified · last · why · action */
  grid-template-columns: 172px 116px 98px 106px 84px 44px 56px 1fr 116px;
  gap: 10px;
  padding: 7px 14px;
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.08);
  align-items: center;
}
.rs-board-row--head {
  padding: 9px 14px;
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
}
/* Tags outside the tagger vocabulary: mute the SIGNAL cells but keep the
   action fully interactive (a kNN review still works for them). */
.rs-board-row--nomodel .rs-board-tag,
.rs-board-row--nomodel .rs-board-health,
.rs-board-row--nomodel .rs-board-num,
.rs-board-row--nomodel .rs-board-why {
  opacity: 0.55;
}

.rs-board-hdr {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: none;
  background: none;
  font-size: 11px;
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  white-space: nowrap;
  text-align: left;
}
button.rs-board-hdr {
  cursor: pointer;
}
.rs-board-hdr--center {
  justify-content: center;
}
.rs-board-hdr--active {
  color: rgb(var(--v-theme-accent));
}
.rs-board-hdr-arrow {
  opacity: 0.45;
}
.rs-board-hdr--active .rs-board-hdr-arrow {
  opacity: 1;
}

.rs-board-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: var(--weight-semibold);
  min-width: 0;
}
.rs-board-tag--anomaly {
  color: rgb(var(--v-theme-error));
}
.rs-board-tag-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rs-board-tag-flag {
  flex-shrink: 0;
}
.rs-board-nomodel-chip {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: var(--weight-semibold);
  letter-spacing: 0.03em;
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.25);
}

.rs-board-health {
  display: flex;
  align-items: center;
  gap: 7px;
}
.rs-board-health-track {
  display: inline-block;
  width: 56px;
  height: 6px;
  border-radius: 3px;
  background: rgba(var(--v-theme-on-dark-surface), 0.18);
  overflow: hidden;
}
.rs-board-health-fill {
  display: block;
  height: 100%;
}
.rs-board-health-num {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
}

.rs-board-num {
  font-size: 13px;
  text-align: center;
  font-variant-numeric: tabular-nums;
  font-weight: var(--weight-semibold);
}
.rs-board-num--zero,
.rs-board-num--muted {
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  font-weight: var(--weight-regular);
}
.rs-board-num--error {
  color: rgb(var(--v-theme-error));
}
.rs-board-num--primary {
  color: rgb(var(--v-theme-primary));
}
.rs-board-num--tertiary {
  color: rgb(var(--v-theme-tertiary));
}

.rs-board-why {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rs-board-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 11px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-board-btn:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rs-board-btn--open {
  border-color: color-mix(in srgb, rgb(var(--v-theme-accent)) 60%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 16%, transparent);
  color: rgb(var(--v-theme-accent));
}

.rs-board-legend {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  margin-top: 10px;
  line-height: 1.5;
}
.rs-legend-error {
  color: rgb(var(--v-theme-error));
}
.rs-legend-warning {
  color: rgb(var(--v-theme-warning));
}
.rs-legend-tertiary {
  color: rgb(var(--v-theme-tertiary));
}
</style>
