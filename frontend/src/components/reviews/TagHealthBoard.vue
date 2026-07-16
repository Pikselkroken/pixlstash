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
          <span class="rs-board-num rs-board-num--muted"
            >{{ Math.round(r.verified_pct ?? 0) }}%</span
          >
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
                v-if="acc(r).state === 'unfrozen'"
                class="rs-acc-freeze-link"
                type="button"
                :disabled="isFreezing(r.tag)"
                title="Not scored yet. Freeze your confirmed examples to start tracking this tag's accuracy."
                @click="doFreeze(r.tag)"
              >
                {{ isFreezing(r.tag) ? "Freezing…" : "Freeze to score" }}
              </button>
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
            <template v-else-if="acc(r).state === 'unfrozen'">
              <button
                class="rs-acc-freeze-link"
                type="button"
                :disabled="isFreezing(r.tag)"
                title="Not scored yet. Freeze your confirmed examples to start tracking this tag's accuracy."
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
        “Last review” = when a review for the tag was last archived ·
        “Accuracy” = how good the model is on a frozen, scored slice, once
        you’ve frozen one. “Ranking score” and “Accuracy” (the two sort
        options) are two different kinds of numbers and are never sorted
        against each other.
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
  score: "Sorted by the tags most likely to have tagging mistakes worth fixing.",
  tag: "Sorted alphabetically by tag name.",
  wrong: "Sorted by how many pictures probably have this tag by mistake.",
  missing: "Sorted by how many pictures are probably missing this tag.",
  dups: "Sorted by how many near-identical shots disagree on this tag.",
  verified:
    "Sorted by how much of each tag you’ve confirmed — least-checked first.",
  last: "Sorted by when each tag was last reviewed — longest ago first.",
  eval_ap:
    "Sorted by ranking score — only tags scored that way; everything else sinks to the bottom.",
  eval_f1:
    "Sorted by accuracy — only tags with a trustworthy cutoff; everything else sinks to the bottom.",
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
  {
    icon: "mdi-target-variant",
    center: true,
    // No `key`: this column maps to TWO non-comparable sort keys (Ranking
    // score / Accuracy — see the sort dropdown), so it isn't click-to-sort
    // like its siblings (that would silently pick one scale for the user).
    tip: "Accuracy — how good the model is at this tag, from a frozen, scored slice. (Separate from Verified, which only tracks how much you’ve checked.)",
  },
  { label: "Why it ranks here" },
  { label: "" },
];

const totalDisputes = computed(() =>
  store.healthRows.reduce((sum, r) => sum + (r.model_disputes ?? 0), 0),
);

// The board's ranking signal uses the reliability-discounted counts when the
// cache has them (est_wrong_adj/est_missing_adj — precision-weighted, so an
// unreliable tag doesn't dominate "estimated fixes"), falling back to the raw
// counts for cache rows that predate the field. The individual "Est. wrong" /
// "Est. missing" cells keep showing the raw, human-legible counts (see
// adjTitle) — only the combined ranking number is adjusted.
function corrections(r) {
  const wrong = r.est_wrong_adj ?? r.est_wrong ?? 0;
  const missing = r.est_missing_adj ?? r.est_missing ?? 0;
  return Math.round(wrong + missing + (r.mismatch ?? 0));
}

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
    case "verified":
      return r.verified_pct ?? 0;
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

const sorted = computed(() => {
  const dir = sort.value.dir === "asc" ? 1 : -1;
  const key = sort.value.key;
  const needle = filter.value.trim().toLowerCase();
  const base = store.healthRows
    .filter((r) => !anomalyOnly.value || isAnomaly(r))
    .filter((r) => !disputesOnly.value || (r.model_disputes ?? 0) > 0)
    .filter((r) => !needle || r.tag.toLowerCase().includes(needle));

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

  return base.slice().sort((a, b) => {
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

function whyText(r) {
  if (r.has_model === false)
    return "not in the tagger's vocabulary — similarity review still works";
  return r.why || "";
}

function openSessionFor(tag) {
  return store.sessions.find((s) => s.tag === tag) ?? null;
}

// --- Accuracy column ---------------------------------------------------------
//
// eval_metric_kind/eval_threshold_source are backend vocabulary and must
// never render outside a tooltip — see the state table in
// docs/reviews/tag-review-accuracy-freeze-conflicts-ux-spec.md §3.2.
function acc(r) {
  if (!r.eval_slice_frozen_at || !r.eval_metric_kind) return { state: "unfrozen" };
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
  return { state: "unfrozen" };
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
    return `Not enough confirmed examples yet — needs at least 10, this tag has ${err.nPos}. Review a few more to unlock freezing.`;
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
     verified · last · accuracy · why · action */
  grid-template-columns: 172px 116px 98px 106px 84px 44px 56px 92px 1fr 116px;
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
