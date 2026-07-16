<template>
  <div class="rs-board">
    <div class="rs-board-inner">
      <div class="rs-board-heading">
        <h2 class="rs-board-title">Which tags need review?</h2>
        <span class="rs-board-subtitle">{{ subtitle }}</span>
        <span class="rs-board-heading-spacer"></span>
        <button
          class="rs-board-rebuild-persistent"
          :class="{
            'rs-board-rebuild-persistent--stale':
              store.healthStale && !store.healthBuilding,
          }"
          type="button"
          :disabled="store.healthBuilding"
          :title="rebuildTitle"
          @click="store.rebuildHealth()"
        >
          <v-icon size="14" :class="{ 'mdi-spin': store.healthBuilding }">{{
            store.healthStale && !store.healthBuilding
              ? "mdi-clock-alert-outline"
              : "mdi-refresh"
          }}</v-icon>
          {{ store.healthComputedAt ? `Updated ${relativeComputedAt}` : "Never built" }}
        </button>
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
        <select
          class="rs-board-scope"
          :class="{ 'rs-board-scope--set': scope.projectId != null }"
          :value="scope.projectId ?? ''"
          title="Only count pictures in this project"
          @change="pickScope('projectId', $event)"
        >
          <option value="">Project: Any</option>
          <option v-for="p in store.projects" :key="p.id" :value="p.id">
            {{ p.name || `Project ${p.id}` }}
          </option>
        </select>
        <select
          class="rs-board-scope"
          :class="{ 'rs-board-scope--set': scope.setId != null }"
          :value="scope.setId ?? ''"
          title="Only count pictures in this set"
          @change="pickScope('setId', $event)"
        >
          <option value="">Set: Any</option>
          <option v-for="s in store.sets" :key="s.id" :value="s.id">
            {{ s.name || `Set ${s.id}` }}
          </option>
        </select>
        <select
          class="rs-board-scope"
          :class="{ 'rs-board-scope--set': scope.characterId != null }"
          :value="scope.characterId ?? ''"
          title="Only count pictures of this character"
          @change="pickScope('characterId', $event)"
        >
          <option value="">Character: Any</option>
          <option value="UNASSIGNED">Unassigned</option>
          <option v-for="c in store.characters" :key="c.id" :value="c.id">
            {{ c.name || `Character ${c.id}` }}
          </option>
        </select>
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

      <!-- Cache (re)build in progress, OR a board-scope refetch in flight: show
           the bar, keep any stale rows below (undimmed — this is a refresh, not
           an error state). Rebuild has real processed/total progress
           (determinate fill); a scope refetch does not, so it gets an
           indeterminate sliding fill instead (ProgressOverlay's technique). -->
      <div v-if="store.healthBuilding || store.healthLoading" class="rs-board-building">
        <span class="rs-board-building-label">
          <v-icon size="15" class="mdi-spin">mdi-loading</v-icon>
          {{
            store.healthBuilding
              ? "Building tag health signals…"
              : "Updating for this scope…"
          }}
        </span>
        <span class="rs-board-building-bar">
          <span
            class="rs-board-building-fill"
            :class="{ 'rs-board-building-fill--indeterminate': !store.healthBuilding }"
            :style="
              store.healthBuilding
                ? { width: `${Math.round(store.healthProgress * 100)}%` }
                : undefined
            "
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
        <template v-else-if="store.healthScoped">
          No tags on any picture in this scope.
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
              'rs-board-hdr--divider': h.dividerBefore,
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
          :class="{ 'rs-board-row--nomodel': r.has_model === false || isRankSunk(r) }"
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
            <span
              v-if="sort.key === 'score' && wasBoosted(r)"
              class="rs-board-boost-chip"
              tabindex="0"
              :aria-label="`Ranked higher than its raw priority — weak accuracy, ${Math.round((r.eval_f1 ?? 0) * 100)} percent F1`"
              :title="`Ranked above its raw Priority score — this tag's measured accuracy is low (${Math.round((r.eval_f1 ?? 0) * 100)}% F1), so fixing it is worth more per review.`"
            >
              <v-icon size="11">mdi-arrow-up-bold</v-icon>
            </span>
          </span>
          <span
            class="rs-board-num"
            :class="numClass(r.est_wrong, 'error')"
            :title="adjTitle(r.est_wrong, r.est_wrong_adj)"
            >{{ r.est_wrong ?? 0 }}</span
          >
          <span
            class="rs-board-num"
            :class="numClass(r.est_missing, 'primary')"
            :title="adjTitle(r.est_missing, r.est_missing_adj)"
            >{{ r.est_missing ?? 0 }}</span
          >
          <span class="rs-board-num" :class="numClass(r.mismatch, 'tertiary')">{{
            r.mismatch ?? 0
          }}</span>
          <span class="rs-board-num rs-board-num--muted">{{
            lastLabel(r)
          }}</span>
          <span class="rs-board-acc">
            <template v-if="isRankSunk(r)">
              <!-- Never frozen / no model signal at all: same affordance as
                   the non-sunk state (freezing is still the actionable next
                   step, and "not scored" isn't the same claim as "scored on
                   the other scale"). -->
              <button
                v-if="acc(r).state === 'unfrozen_ready'"
                class="rs-acc-freeze-link"
                type="button"
                :disabled="isFreezing(r.tag)"
                :title="freezeReadyTip(acc(r).nPos)"
                @click="doFreeze(r.tag)"
              >
                {{ isFreezing(r.tag) ? "Freezing…" : "Freeze to score" }}
              </button>
              <span
                v-else-if="acc(r).state === 'unfrozen_pending'"
                class="rs-acc-pill rs-acc-pill--pending"
                tabindex="0"
                :aria-label="pendingAriaLabel(acc(r).nPos)"
                :title="pendingTip(acc(r).nPos)"
                >{{ acc(r).nPos }}/{{ FREEZE_MIN_N_POS }}</span
              >
              <span v-else-if="acc(r).state === 'none'" class="rs-acc-dash">–</span>
              <span
                v-else-if="acc(r).state === 'insufficient'"
                class="rs-acc-pill"
                title="Fewer than 10 confirmed examples for this tag — review a few more to unlock scoring."
                >not enough data yet</span
              >
              <span
                v-else
                class="rs-acc-sunk"
                :title="`This tag is scored on the other scale — see the '${
                  sort.key === 'eval_ap' ? 'Accuracy' : 'Ranking score'
                }' sort.`"
                >scored differently</span
              >
            </template>
            <template v-else-if="acc(r).state === 'unfrozen_ready'">
              <button
                class="rs-acc-freeze-link"
                type="button"
                :disabled="isFreezing(r.tag)"
                :title="freezeReadyTip(acc(r).nPos)"
                @click="doFreeze(r.tag)"
              >
                <v-icon
                  v-if="isFreezing(r.tag)"
                  size="12"
                  class="mdi-spin"
                  >mdi-loading</v-icon
                >
                {{ isFreezing(r.tag) ? "Freezing…" : "Freeze to score" }}
              </button>
              <span v-if="freezeError(r.tag)" class="rs-acc-freeze-fail">{{
                freezeErrorText(r.tag)
              }}</span>
            </template>
            <template v-else-if="acc(r).state === 'unfrozen_pending'">
              <span
                class="rs-acc-pill rs-acc-pill--pending"
                tabindex="0"
                :aria-label="pendingAriaLabel(acc(r).nPos)"
                :title="pendingTip(acc(r).nPos)"
                >{{ acc(r).nPos }}/{{ FREEZE_MIN_N_POS }}</span
              >
            </template>
            <template v-else>
              <span v-if="acc(r).state === 'none'" class="rs-acc-dash">–</span>
              <span
                v-else-if="acc(r).state === 'insufficient'"
                class="rs-acc-pill"
                title="Fewer than 10 confirmed examples for this tag — review a few more to unlock scoring."
                >not enough data yet</span
              >
              <span
                v-else-if="acc(r).state === 'f1'"
                class="rs-acc-f1-wrap"
              >
                <span
                  class="rs-acc-f1"
                  :class="[f1Tone(acc(r).pct), { 'rs-acc-f1--uncal': acc(r).uncalibrated }]"
                  :title="f1Tip(r, acc(r))"
                  >{{ acc(r).uncalibrated ? "~" : "" }}{{ acc(r).pct }}%</span
                >
                <v-icon
                  v-if="acc(r).stale"
                  size="11"
                  class="rs-acc-stale"
                  title="Cutoff not freshly tuned for this tag"
                  >mdi-clock-outline</v-icon
                >
              </span>
              <span
                v-else-if="acc(r).state === 'ap'"
                class="rs-acc-ap"
                :title="apTip(r, acc(r))"
              >
                <span class="rs-acc-dots">
                  <span
                    v-for="i in 5"
                    :key="i"
                    class="rs-acc-dot"
                    :class="{ 'rs-acc-dot--on': i <= Math.round(acc(r).value / 20) }"
                  ></span>
                </span>
                {{ acc(r).value }}<span v-if="!acc(r).hasCi">*</span>
              </span>
              <span
                class="rs-acc-refreeze-wrap"
                @mouseenter="loadHistory(r.tag)"
                @focusin="loadHistory(r.tag)"
              >
                <button
                  class="rs-acc-refreeze"
                  type="button"
                  :disabled="isFreezing(r.tag)"
                  :title="refreezeTip(r)"
                  @click="doFreeze(r.tag)"
                >
                  <v-icon size="12">mdi-restore</v-icon>
                </button>
                <button
                  class="rs-acc-hist-toggle"
                  type="button"
                  :aria-expanded="historyOpen === r.tag"
                  title="Freeze history"
                  @click="toggleHistory(r.tag)"
                >
                  <v-icon size="12">{{
                    historyOpen === r.tag ? "mdi-chevron-down" : "mdi-chevron-up"
                  }}</v-icon>
                </button>
                <div
                  v-if="historyOpen === r.tag"
                  class="rs-acc-hist-panel"
                  role="region"
                  aria-label="Freeze history"
                >
                  <div v-if="!store.evalHistories[r.tag]" class="rs-acc-hist-loading">
                    Loading…
                  </div>
                  <template v-else>
                    <div class="rs-acc-hist-title">
                      History ({{ store.evalHistories[r.tag].length }} freeze{{
                        store.evalHistories[r.tag].length === 1 ? "" : "s"
                      }})
                    </div>
                    <ul class="rs-acc-hist-list">
                      <li
                        v-for="h in store.evalHistories[r.tag]"
                        :key="h.id"
                        class="rs-acc-hist-row"
                      >
                        <span class="rs-acc-hist-date">{{ shortDate(h.created_at) }}</span>
                        <span class="rs-acc-hist-n">{{ h.n_pos }}/{{ h.n_total }}</span>
                        <span
                          class="rs-acc-hist-status"
                          :class="`rs-acc-hist-status--${h.status.toLowerCase()}`"
                          >{{ h.status === "ACTIVE" ? "Active" : "Superseded" }}</span
                        >
                      </li>
                    </ul>
                  </template>
                </div>
              </span>
            </template>
          </span>
          <span class="rs-board-why" :title="whyText(r)">{{ whyText(r) }}</span>
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
        “Priority” = a fast ranking estimate (est. wrong + est. missing +
        mismatches) for sorting tags — not the number of cards a review
        session will contain, which comes from a separate, slower scan (bar
        colour:
        <span class="rs-legend-error">red</span> = worst tags,
        <span class="rs-legend-warning">amber</span> = notable,
        <span class="rs-legend-tertiary">teal</span> = minor) · “Est. wrong” =
        tagged pictures the model is ≤10% sure about · “Est. missing” =
        untagged pictures it is ≥90% sure about · “Mismatch” = near-identical
        shots with different labels · “Last review” = when a review for the
        tag was last archived · “Accuracy” = how good the model is on a
        frozen, scored slice (only about 1 in 5 reviewed pictures counts
        toward it — most are reserved for training), once you’ve frozen one.
        “Ranking score” and “Accuracy” (the two sort options) are two
        different kinds of numbers and are never sorted against each other.
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
// relativeDate already solves the "naive ISO string = UTC" quirk backend
// timestamps carry (computed_at is the same shape as the snapshot timestamps
// this helper was written for) — reuse it rather than re-deriving the same
// fix here.
import { relativeDate } from "../../utils/snapshots";
// Pure ranking/explanation logic, split out for direct-import unit testing —
// see tagHealthBoardLogic.js's module doc for why.
import {
  corrections,
  whyText,
  boostedScore,
  rawCorrections,
} from "./tagHealthBoardLogic";

const emit = defineEmits(["start-review"]);
const store = useReviewSessionsStore();

const anomalyOnly = ref(false);
const disputesOnly = ref(false);
const filter = ref("");
const filterRef = ref(null);
const sort = ref({ key: "score", dir: "desc" });

// POST /tag_eval_slices requires this many verified-positive EVAL-split
// labels before a freeze can succeed — see freezeErrorText() for the
// server-side failure message this mirrors client-side.
const FREEZE_MIN_N_POS = 10;

// --- Persistent rebuild control (Spec B) ------------------------------------

const relativeComputedAt = computed(() => relativeDate(store.healthComputedAt));
const rebuildTitle = computed(() => {
  if (store.healthBuilding) return "Rebuilding…";
  if (store.healthStale)
    return "Tag health hasn't been recomputed since new activity — rebuild now, or it'll catch up automatically shortly.";
  return "Recompute tag health signals from the current data";
});

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
  { label: "Recently reviewed", key: "last", dir: "asc" },
  { label: "Ranking score", key: "eval_ap", dir: "desc" },
  { label: "Accuracy", key: "eval_f1", dir: "desc" },
];

// AP and F1 are two different kinds of numbers (an integral over every cutoff
// vs. a hit rate at one cutoff) — never sorted against each other. Rows of the
// other kind, plus insufficient_data/none/uncalibrated_fallback rows, sink to
// the bottom of these two sorts instead of being hidden (see `sorted` below).
const RANK_KINDS = { eval_ap: "AP", eval_f1: "F1" };

function isRankEligible(r, key) {
  const kind = RANK_KINDS[key];
  if (!kind) return true;
  if (r.eval_metric_kind !== kind) return false;
  if (kind === "F1" && r.eval_threshold_source === "uncalibrated_fallback")
    return false;
  return true;
}

const SUBTITLE = {
  score:
    "Sorted by how worth reviewing each tag looks — a fast estimate, not a review-session size.",
  tag: "Sorted alphabetically by tag name.",
  wrong: "Sorted by how many pictures probably have this tag by mistake.",
  missing: "Sorted by how many pictures are probably missing this tag.",
  dups: "Sorted by how many near-identical shots disagree on this tag.",
  last: "Sorted by when each tag was last reviewed — longest ago first.",
  eval_ap:
    "Sorted by ranking score — only tags scored that way; everything else sinks to the bottom.",
  eval_f1:
    "Sorted by accuracy — only tags with a trustworthy cutoff; everything else sinks to the bottom.",
};

const subtitle = computed(() => SUBTITLE[sort.value.key] || SUBTITLE.score);

// headers is a computed (not a plain const) solely so the Accuracy column's
// tip can append vaultWideNote() reactively when the board's scope changes —
// every other header's tip is static.
const headers = computed(() => [
  { label: "Tag", key: "tag" },
  {
    label: "Priority",
    key: "score",
    tip: "A fast ranking estimate (est. wrong + est. missing + mismatches), used to sort tags by how worth reviewing they look. Not a forecast of how many cards a review session will contain — Start review runs a separate, slower scan (nearest-neighbour comparison) that usually finds a smaller, different set of pictures.",
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
    icon: "mdi-clock-outline",
    key: "last",
    center: true,
    tip: "Last reviewed",
  },
  {
    label: "Accuracy",
    center: true,
    dividerBefore: true,
    // No `key`: this column maps to TWO non-comparable sort keys (Ranking
    // score / Accuracy — see the sort dropdown), so it isn't click-to-sort
    // like its siblings (that would silently pick one scale for the user).
    tip: `Accuracy — how good the model is at this tag, measured on a separate frozen, scored slice. Not a count of pictures needing review — that’s Est. wrong / Est. missing / Ranking score to the left, which update live. This number only changes when the tag is (re)frozen.${vaultWideNote()}`,
  },
  { label: "Why it ranks here" },
  { label: "" },
]);

const totalDisputes = computed(() =>
  store.healthRows.reduce((sum, r) => sum + (r.model_disputes ?? 0), 0),
);

// Tooltip for the raw Est. wrong/missing cells: surface the reliability
// discount without replacing the number a user already understands.
function adjTitle(raw, adj) {
  if (adj == null || Math.round(adj) === (raw ?? 0)) return undefined;
  return `~${Math.round(adj)} after discounting for this tag's measured reliability`;
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
    case "last":
      return lastValue(r);
    case "eval_ap":
      return r.eval_ap ?? 0;
    case "eval_f1":
      return r.eval_f1 ?? 0;
    default:
      return 0;
  }
}

const isRankSort = computed(() => sort.value.key in RANK_KINDS);

// A row sorting to the bottom under the active "Ranking score"/"Accuracy"
// sort — not hidden (per the acceptance criteria: someone might be looking
// for exactly one of them), just excluded from the ranked order.
function isRankSunk(r) {
  return isRankSort.value && !isRankEligible(r, sort.value.key);
}

// The filtered-but-unsorted row set — shared by `sorted` and the boost badge
// (`boostInfo`) below so the two never drift out of sync with each other.
const filteredRows = computed(() => {
  const needle = filter.value.trim().toLowerCase();
  return store.healthRows
    .filter((r) => !anomalyOnly.value || isAnomaly(r))
    .filter((r) => !disputesOnly.value || (r.model_disputes ?? 0) > 0)
    .filter((r) => !needle || r.tag.toLowerCase().includes(needle));
});

const sorted = computed(() => {
  const dir = sort.value.dir === "asc" ? 1 : -1;
  const key = sort.value.key;
  const base = filteredRows.value;

  if (key in RANK_KINDS) {
    const eligible = [];
    const sunk = [];
    for (const r of base) (isRankEligible(r, key) ? eligible : sunk).push(r);
    eligible.sort((a, b) => {
      const av = keyval(a, key) ?? 0;
      const bv = keyval(b, key) ?? 0;
      if (av === bv) return a.tag.localeCompare(b.tag);
      return (av - bv) * dir;
    });
    sunk.sort((a, b) => a.tag.localeCompare(b.tag));
    return [...eligible, ...sunk];
  }

  // The default "Suggested (health)" sort (key === "score") orders by the
  // accuracy-boosted score (Spec F) instead of the raw one — every other
  // sort keeps ranking strictly by keyval(), unaffected by the boost.
  //
  // Two tags routinely round to the same boostedScore in a lightly-reviewed
  // vault (corrections() rounds, and un-boosted rows share boostFactor 1), so
  // a tag-name fallback here would decide the PRIMARY ranking, not just a
  // rare genuine tie — that's the bug this tie-break closes. rawCorrections()
  // (the un-rounded, un-discounted est_wrong + est_missing + mismatch) breaks
  // the tie with the same underlying signal at full precision before falling
  // back to tag name for a genuine, full tie.
  if (key === "score") {
    return base.slice().sort((a, b) => {
      const av = boostedScore(a);
      const bv = boostedScore(b);
      if (av !== bv) return (av - bv) * dir;
      const ar = rawCorrections(a);
      const br = rawCorrections(b);
      if (ar !== br) return (ar - br) * dir;
      return a.tag.localeCompare(b.tag);
    });
  }

  return base.slice().sort((a, b) => {
    const av = keyval(a, key);
    const bv = keyval(b, key);
    if (typeof av === "string")
      return av.localeCompare(bv) * dir || a.tag.localeCompare(b.tag);
    if (av === bv) return a.tag.localeCompare(b.tag);
    return (av - bv) * dir;
  });
});

// --- Accuracy tie-breaker (Spec F) -------------------------------------------
//
// isBoostEligible/boostFactor/boostedScore live in tagHealthBoardLogic.js
// (imported above) — continuous, capped multiplier on the "Suggested
// (health)" sort's key ONLY, never on "Most wrong"/"Most missing"/"Ranking
// score"/"Accuracy" (those keep their own single-number or partitioned-scale
// contracts intact, see RANK_KINDS above). Never changes the DISPLAYED
// Priority number (`corrections(r)`), only where a row lands in this one
// sort — surfaced via the `wasBoosted` badge so the reorder is never silent.

// Rows the boost actually moved up, relative to the unboosted "score" order —
// only meaningful (and only computed) while that sort is active.
const boostInfo = computed(() => {
  if (sort.value.key !== "score") return null;
  const base = filteredRows.value;
  const rawOrder = base
    .slice()
    .sort((a, b) => keyval(b, "score") - keyval(a, "score"));
  const boostedOrder = base.slice().sort((a, b) => boostedScore(b) - boostedScore(a));
  const rawIndex = new Map(rawOrder.map((r, i) => [r.tag, i]));
  const boostedIndex = new Map(boostedOrder.map((r, i) => [r.tag, i]));
  return { rawIndex, boostedIndex };
});

function wasBoosted(r) {
  const info = boostInfo.value;
  if (!info) return false;
  return (info.boostedIndex.get(r.tag) ?? 0) < (info.rawIndex.get(r.tag) ?? 0);
}

function defaultDir(key) {
  return key === "tag" || key === "last" ? "asc" : "desc";
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

// Board scope (project/set/character): server-side — every signal column is
// recomputed for the chosen pictures, and out-of-scope-only tags drop off.
const scope = computed(() => store.healthScope);

function pickScope(dim, event) {
  const raw = event.target.value;
  let value = raw === "" ? null : raw;
  // Project/set ids are numeric; character stays a string ("UNASSIGNED" or id).
  if (value !== null && dim !== "characterId") value = Number(value);
  store.setHealthScope({ ...scope.value, [dim]: value });
  event.target.blur();
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

// whyText() lives in tagHealthBoardLogic.js (imported above) so it's
// unit-testable by direct import.

function openSessionFor(tag) {
  return store.sessions.find((s) => s.tag === tag) ?? null;
}

// --- Accuracy column ---------------------------------------------------------
//
// eval_metric_kind/eval_threshold_source are backend vocabulary and must
// never render outside a tooltip — see the state table in
// docs/reviews/tag-review-accuracy-freeze-conflicts-ux-spec.md §3.2.
function acc(r) {
  if (!r.eval_slice_frozen_at || !r.eval_metric_kind) {
    // Freeze eligibility is otherwise invisible until a failed click (the
    // user only learns the real count via the 5s freezeError tooltip). Split
    // "never frozen" into a clickable button when there's already enough to
    // succeed vs. a non-interactive progress pill when there isn't — a click
    // below the floor is a deterministic, client-computable failure.
    const nPos = r.eval_candidate_n_pos ?? 0;
    return nPos >= FREEZE_MIN_N_POS
      ? { state: "unfrozen_ready", nPos }
      : { state: "unfrozen_pending", nPos };
  }
  if (r.eval_metric_kind === "none") return { state: "none" };
  if (r.eval_metric_kind === "insufficient_data") {
    return { state: "insufficient", nPos: r.eval_n_pos ?? 0 };
  }
  if (r.eval_metric_kind === "F1") {
    const source = r.eval_threshold_source;
    return {
      state: "f1",
      pct: Math.round((r.eval_f1 ?? 0) * 100),
      n: r.eval_n ?? 0,
      nPos: r.eval_n_pos ?? 0,
      uncalibrated: source === "uncalibrated_fallback",
      stale: source === "carried_forward" || source === "rederived_disjoint_val",
    };
  }
  if (r.eval_metric_kind === "AP") {
    const hasCi = r.eval_ap_ci_low != null && r.eval_ap_ci_high != null;
    return {
      state: "ap",
      value: Math.round((r.eval_ap ?? 0) * 100),
      nPos: r.eval_n_pos ?? 0,
      hasCi,
      ciLow: hasCi ? Math.round(r.eval_ap_ci_low * 100) : null,
      ciHigh: hasCi ? Math.round(r.eval_ap_ci_high * 100) : null,
    };
  }
  // Defensive fallback: eval_metric_kind was truthy but not a recognised
  // value (frozen but no matching branch above) — never reachable with the
  // current backend contract, but rendering "–" beats rendering nothing.
  return { state: "none" };
}

// Freeze eligibility (eval_candidate_n_pos) and every eval_* metric field are
// always vault-wide (TagHealthResponse.eval_vault_wide — see
// pixlstash/routes/tag_health.py) even when the board itself is scoped to a
// project/set/character, because freezing a TagEvalSlice has no scope concept
// (POST /tag_eval_slices takes no scope parameter either). Appended to the
// freeze pill/button and Accuracy header tooltips so their numbers don't
// silently imply they respect the current scope the way every other column
// does. Empty string when unscoped, or in the (currently unreachable, kept
// for forward-compatibility) case the backend ever scopes eval fields.
function vaultWideNote() {
  if (!store.healthScoped || !store.healthEvalVaultWide) return "";
  return " (reflects every eligible picture vault-wide, not just your current scope)";
}

// Never-frozen, eligible-to-freeze tooltip: names the confirmed count so the
// user knows the freeze click is expected to succeed (the pending pill below
// is what shows when it wouldn't).
function freezeReadyTip(nPos) {
  return `Not scored yet — ${nPos} confirmed examples ready. Freeze to start tracking this tag's accuracy.${vaultWideNote()}`;
}

// Never-frozen, below-the-floor tooltip/aria-label for the non-interactive
// `{n}/10` pill — the primary answer to "why can't I freeze this," so it must
// be keyboard-discoverable (tabindex + aria-label), not hover-only.
//
// The "roughly N*5 reviews" figure is a copy-level approximation of
// 1 / (1 - TRAIN_RATIO), tied to TRAIN_RATIO = 0.8 in
// pixlstash/services/picture_split_service.py by convention, not a computed
// guarantee — only ~1 in 5 reviewed pictures lands on the EVAL side that
// counts toward eval_candidate_n_pos. If TRAIN_RATIO ever changes, update the
// "5" here (a fully robust version would expose the ratio via the API
// instead — noted as an open item in the redesign spec, not required yet).
function pendingTip(nPos) {
  const remaining = FREEZE_MIN_N_POS - nPos;
  return `${nPos}/${FREEZE_MIN_N_POS} confirmed EVAL-side examples — freezing needs at least ${FREEZE_MIN_N_POS}. PixlStash reserves most reviewed pictures for training and only keeps a fifth for scoring, so this climbs slower than your review count — reviewing more of this tag is still the way to unlock it, just not 1-for-1. Review ${remaining} more EVAL-side examples (roughly ${remaining * 5} reviews of this tag) to unlock scoring.${vaultWideNote()}`;
}

function pendingAriaLabel(nPos) {
  return `${nPos} of ${FREEZE_MIN_N_POS} EVAL-side confirmed examples needed to freeze this tag's accuracy score. Only about one in five reviewed pictures counts toward this number.${vaultWideNote()}`;
}

function f1Tone(pct) {
  if (pct >= 85) return "rs-acc-f1--good";
  if (pct >= 60) return "rs-acc-f1--warn";
  return "rs-acc-f1--bad";
}

function f1Tip(r, a) {
  if (a.uncalibrated) {
    return "Rough estimate only — this tag has no tuned cutoff yet, so this uses a generic 50/50 guess boundary. Not included when sorting by accuracy.";
  }
  const source = r.eval_threshold_source;
  if (source === "carried_forward") {
    return `${a.pct}% accurate, using the cutoff from an earlier version of the tagger — this tag hasn’t been retuned since the model last changed.`;
  }
  if (source === "rederived_disjoint_val") {
    return `${a.pct}% accurate, using a cutoff estimated from your training examples (no tuned cutoff exists for this tag yet).`;
  }
  return `${a.pct}% accurate, based on ${a.n} pictures you’ve confirmed. This tag has a cutoff tuned specifically for it.`;
}

function apTip(_r, a) {
  const base = a.hasCi
    ? `Ranking score: ${a.value} out of 100, from ${a.nPos} confirmed examples (likely range ${a.ciLow}–${a.ciHigh}). Measures how well the model sorts probably-correct pictures ahead of probably-wrong ones — this tag doesn’t have a tuned yes/no cutoff yet, so there’s no single accuracy percentage for it.`
    : `Ranking score: ${a.value} out of 100. Measures how well the model sorts probably-correct pictures ahead of probably-wrong ones — this tag doesn’t have a tuned yes/no cutoff yet, so there’s no single accuracy percentage for it.`;
  return a.hasCi ? base : `${base} Only ${a.nPos} confirmed examples — too few yet to show a confidence range.`;
}

// --- Freeze ------------------------------------------------------------------

function isFreezing(tag) {
  return store.freezingTags.has(tag);
}

function freezeError(tag) {
  return store.freezeErrors[tag] || null;
}

function freezeErrorText(tag) {
  const err = freezeError(tag);
  if (!err) return "";
  if (err.reason === "insufficient_positives" && err.nPos != null) {
    return `Not enough confirmed examples yet — needs at least ${FREEZE_MIN_N_POS}, this tag has ${err.nPos}. Review a few more to unlock freezing.`;
  }
  return "Not enough confirmed examples yet — review a few more of this tag to unlock freezing.";
}

const freezeErrorTimers = {};

function doFreeze(tag) {
  store.freezeEvalSlice(tag).then(() => {
    if (!store.freezeErrors[tag]) return;
    clearTimeout(freezeErrorTimers[tag]);
    freezeErrorTimers[tag] = setTimeout(() => {
      store.clearFreezeError(tag);
    }, 5000);
  });
}

// --- Freeze history disclosure ------------------------------------------------

const historyOpen = ref(null); // tag currently expanded, or null

function loadHistory(tag) {
  if (store.evalHistories[tag] !== undefined) return;
  store.fetchEvalHistory(tag);
}

function toggleHistory(tag) {
  historyOpen.value = historyOpen.value === tag ? null : tag;
  if (historyOpen.value) loadHistory(tag);
}

// The refreeze tooltip must show the last-frozen date/count BEFORE any click
// (and before the async history fetch resolves) — the board row already
// carries eval_slice_frozen_at/eval_n_pos, so this reads synchronously.
function refreezeTip(r) {
  if (!r.eval_slice_frozen_at) return "Refreeze this tag’s accuracy snapshot.";
  const date = shortDate(r.eval_slice_frozen_at);
  return `Last frozen ${date}, ${r.eval_n_pos ?? 0} confirmed examples. Refreezing replaces this with today’s confirmed set — the old snapshot is kept in history, not deleted.`;
}

function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
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
  /* Fills the frame (sidebar excluded — that's `.rs-board`'s sibling), with
     the surrounding margin coming from `.rs-board`'s own padding. */
  width: 100%;
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
.rs-board-heading-spacer {
  flex: 1;
  min-width: var(--space-3);
}
/* Always rendered (row count 0, 1, or many) — the escape hatch stays visible
   even after the board has been built once, unlike the empty-state's
   one-time-only .rs-board-rebuild button below. Quieter, ambient copy since
   it's on screen at all times. */
.rs-board-rebuild-persistent {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  background: rgba(var(--v-theme-on-dark-surface), 0.05);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  font-size: var(--text-2xs);
  cursor: pointer;
  white-space: nowrap;
}
.rs-board-rebuild-persistent:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
}
.rs-board-rebuild-persistent:disabled {
  cursor: default;
  opacity: 0.7;
}
/* stale = new activity landed since the cache's computed_at — same icon as
   the review-session staleness chip (mdi-clock-alert-outline) for visual
   consistency across the two features. */
.rs-board-rebuild-persistent--stale {
  border-color: color-mix(in srgb, rgb(var(--v-theme-warning)) 55%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-warning)) 12%, transparent);
  color: rgb(var(--v-theme-warning));
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
.rs-board-sort,
.rs-board-scope {
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
.rs-board-sort option,
.rs-board-scope option {
  background-color: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
}
/* A scope dimension that is actively narrowing the board reads as "on". */
.rs-board-scope--set {
  border-color: color-mix(in srgb, rgb(var(--v-theme-accent)) 60%, transparent);
  color: rgb(var(--v-theme-accent));
}
.rs-board-scope {
  max-width: 150px;
  text-overflow: ellipsis;
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
/* A board-scope refetch has no processed/total to report, so it gets an
   indeterminate sliding fill instead of the rebuild bar's determinate width —
   same technique as ProgressOverlay.vue's `.progress-overlay__fill--indeterminate`. */
.rs-board-building-fill--indeterminate {
  width: 38% !important;
  animation: rs-board-building-indeterminate 1.2s ease-in-out infinite;
  transition: none;
}
@keyframes rs-board-building-indeterminate {
  0% {
    transform: translateX(-120%);
  }
  50% {
    transform: translateX(90%);
  }
  100% {
    transform: translateX(220%);
  }
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
     last · accuracy · why · action (Verified column cut — see Spec E) */
  grid-template-columns: 172px 116px 98px 106px 84px 56px 92px 1fr 116px;
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
   action fully interactive (a kNN review still works for them). Sunk rank
   rows (a row that isn't scored on the currently active Ranking/Accuracy
   sort) reuse the same treatment. */
.rs-board-row--nomodel .rs-board-tag,
.rs-board-row--nomodel .rs-board-health,
.rs-board-row--nomodel .rs-board-num,
.rs-board-row--nomodel .rs-board-acc,
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
/* Accuracy reads from a frozen, point-in-time slice — everything to its left
   is a live count over current unscored data. A quiet vertical rule states
   that boundary without implying severity (this app's existing tint
   vocabulary means "flagged/problem", so a background tint here would
   misread as an issue). */
.rs-board-hdr--divider {
  border-left: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  padding-left: var(--space-3);
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
  /* Flex items default to min-width: auto (their content's natural width),
     which blocks shrinking and defeats the ellipsis above — without this,
     a long tag name pushes past the column and crowds out sibling badges
     like the "no model signal" chip instead of truncating. */
  min-width: 0;
}
.rs-board-tag-flag {
  flex-shrink: 0;
}
.rs-board-nomodel-chip {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 10px;
  line-height: 1.4;
  white-space: nowrap;
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
/* Spec F boost badge: reuses the existing warning-tone vocabulary already
   defined for low-F1 cells (.rs-acc-f1--warn/--bad) rather than inventing a
   new color. tabindex="0" + aria-label for the same reason the
   unfrozen_pending pill has them — a non-<button> element carrying a
   decision-relevant explanation must be keyboard-discoverable. */
.rs-board-boost-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 3px;
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-warning));
  background: color-mix(in srgb, rgb(var(--v-theme-warning)) 16%, transparent);
}
.rs-board-boost-chip:focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
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

/* --- Accuracy column ------------------------------------------------------ */

.rs-board-acc {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  /* Same divider as the header above it — carries the "different kind of
     number" boundary into every data row, including --nomodel/opacity-faded
     ones (opacity applies to the whole cell, border included). */
  border-left: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  padding-left: var(--space-3);
}
.rs-acc-dash {
  color: rgba(var(--v-theme-on-dark-surface), 0.4);
}
.rs-acc-sunk {
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
  font-style: italic;
  white-space: nowrap;
}
.rs-acc-pill {
  padding: 1px var(--space-3);
  border-radius: var(--radius-pill);
  font-size: 10px;
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.25);
}
/* The pre-freeze pending pill (tabindex="0") isn't a <button>/<input>/<select>,
   so it falls outside .rs-board's blanket focus-visible selector — it needs
   its own, since it's now the primary, keyboard-discoverable answer to "why
   can't I freeze this yet." */
.rs-acc-pill--pending:focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
}

/* First-time freeze: an always-visible text-link affordance, NOT hover-gated
   (a first-time action must not be hover-only — see the UX spec §7). Lighter
   weight than .rs-board-btn since it's secondary to "Start review" in the
   same row. */
.rs-acc-freeze-link {
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-accent));
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, rgb(var(--v-theme-accent)) 45%, transparent);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
}
.rs-acc-freeze-link:hover {
  text-decoration-color: rgb(var(--v-theme-accent));
}
.rs-acc-freeze-link:disabled {
  cursor: default;
  opacity: 0.7;
  text-decoration: none;
}
.rs-acc-freeze-fail {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  z-index: 3;
  width: 200px;
  margin-top: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid color-mix(in srgb, rgb(var(--v-theme-warning)) 55%, transparent);
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-warning));
  font-size: var(--text-2xs);
  line-height: 1.4;
  box-shadow: var(--elevation-3);
}

.rs-acc-f1-wrap {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.rs-acc-f1 {
  padding: 1px 7px;
  border-radius: var(--radius-pill);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.rs-acc-f1--good {
  color: rgb(var(--v-theme-tertiary));
  background: color-mix(in srgb, rgb(var(--v-theme-tertiary)) 16%, transparent);
}
.rs-acc-f1--warn {
  color: rgb(var(--v-theme-warning));
  background: color-mix(in srgb, rgb(var(--v-theme-warning)) 16%, transparent);
}
.rs-acc-f1--bad {
  color: rgb(var(--v-theme-error));
  background: color-mix(in srgb, rgb(var(--v-theme-error)) 16%, transparent);
}
/* Uncalibrated (fixed 0.5 threshold): faded + dashed underline + "~" prefix —
   visually distinct from a trusted percentage, never just a tooltip. */
.rs-acc-f1--uncal {
  opacity: 0.62;
  background: none;
  border-bottom: 1px dashed currentColor;
  border-radius: 0;
  padding: 1px 2px;
}
.rs-acc-stale {
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
}

/* AP: a discrete dot-meter, deliberately NOT a percentage bar — see the UX
   spec §3.3 for why a continuous percent would misread as a hit rate. */
.rs-acc-ap {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-variant-numeric: tabular-nums;
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
}
/* Dot glyph size (5px) and gap (1px) are a deliberate new design decision, not
   an inlined one-off: there is no spacing-scale token for a mark this small
   (the 4px grid floor would space five dots wide enough to read as separate
   ticks rather than one meter, undoing the "not a percentage" signal this
   element exists for). Treat it like the v-icon `size` props elsewhere in
   this app — a per-widget glyph metric, not a layout gap. */
.rs-acc-dots {
  display: inline-flex;
  gap: 1px;
}
.rs-acc-dot {
  width: 5px;
  height: 5px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-dark-surface), 0.22);
}
.rs-acc-dot--on {
  background: rgb(var(--v-theme-tertiary));
}

/* Refreeze + history: hover/focus-reveal, same technique as .rs-rail-abort
   (visibility not display, so the row never reflows when it appears). */
.rs-acc-refreeze-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 1px;
}
.rs-acc-refreeze,
.rs-acc-hist-toggle {
  visibility: hidden;
  display: inline-flex;
  align-items: center;
  padding: 1px 2px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  cursor: pointer;
}
.rs-board-acc:hover .rs-acc-refreeze,
.rs-board-acc:hover .rs-acc-hist-toggle,
.rs-acc-refreeze-wrap:focus-within .rs-acc-refreeze,
.rs-acc-refreeze-wrap:focus-within .rs-acc-hist-toggle,
.rs-acc-hist-toggle[aria-expanded="true"] {
  visibility: visible;
}
.rs-acc-refreeze:hover,
.rs-acc-hist-toggle:hover {
  color: rgb(var(--v-theme-accent));
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 12%, transparent);
}
.rs-acc-refreeze:disabled {
  cursor: default;
  opacity: 0.6;
}

.rs-acc-hist-panel {
  position: absolute;
  top: 100%;
  right: 0;
  z-index: 4;
  width: 220px;
  margin-top: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgb(var(--v-theme-dark-surface));
  box-shadow: var(--elevation-3);
  text-align: left;
}
.rs-acc-hist-loading {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-acc-hist-title {
  font-size: 10px;
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  margin-bottom: var(--space-2);
}
.rs-acc-hist-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.rs-acc-hist-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
}
.rs-acc-hist-date {
  flex: 1;
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-acc-hist-n {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  font-variant-numeric: tabular-nums;
}
.rs-acc-hist-status {
  padding: 0 6px;
  border-radius: var(--radius-pill);
  font-size: 10px;
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}
.rs-acc-hist-status--active {
  color: rgb(var(--v-theme-tertiary));
  background: color-mix(in srgb, rgb(var(--v-theme-tertiary)) 16%, transparent);
}
.rs-acc-hist-status--superseded {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
}
</style>
