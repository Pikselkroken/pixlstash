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
          {{
            store.healthComputedAt
              ? `Updated ${relativeComputedAt}`
              : "Never built"
          }}
        </button>
      </div>

      <div class="rs-board-controls">
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
          <!-- A native <option> can carry neither an icon nor a title, so the
               lock state rides in the label text. Locked sets keep their natural
               order - burying them makes them harder to find exactly when the
               user is asking why one is unavailable. -->
          <option v-for="s in store.sets" :key="s.id" :value="s.id">
            {{ `${s.name || `Set ${s.id}`}${s.locked ? " (locked)" : ""}` }}
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

      <!-- Terminal state: the board is scoped to a LOCKED set. A locked set's
           pictures are read-only, so no tag on them can be reviewed - showing
           rows with a live "Start review" would be an offer we can't honour
           (the backend 423s it). Replace the whole body with the explanation
           instead; the controls above stay mounted so the user can scope back
           out. Follows ReviewSessionView.vue's `.rs-state` terminal-state
           pattern (centred column, display icon, headline, sub-line). -->
      <div
        v-if="scopedSetLocked"
        ref="lockedRef"
        class="rs-board-locked"
        role="status"
        tabindex="-1"
      >
        <v-icon size="48" class="rs-board-locked-icon">mdi-lock-outline</v-icon>
        <h3 class="rs-board-locked-title">{{ LOCKED_SET_HEADLINE }}</h3>
        <p class="rs-board-locked-sub">{{ lockedSetTitle(scopedSetName) }}</p>
        <p class="rs-board-locked-hint">
          Pick another set - or “Set: Any” - to get the board back.
        </p>
      </div>

      <template v-else>
        <!-- Cache (re)build in progress, OR a board-scope refetch in flight: show
           the bar, keep any stale rows below (undimmed - this is a refresh, not
           an error state). Rebuild has real processed/total progress
           (determinate fill); a scope refetch does not, so it gets an
           indeterminate sliding fill instead (ProgressOverlay's technique). -->
        <div
          v-if="store.healthBuilding || store.healthLoading"
          class="rs-board-building"
        >
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
              :class="{
                'rs-board-building-fill--indeterminate': !store.healthBuilding,
              }"
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

        <div v-if="sorted.length" :id="TAIL_ID" class="rs-board-table">
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
            v-for="r in visibleRows"
            :key="r.tag"
            class="rs-board-row"
            :class="{ 'rs-board-row--nomodel': r.has_model === false }"
          >
            <span
              class="rs-board-tag"
              :class="{ 'rs-board-tag--anomaly': isAnomaly(r) }"
            >
              <span class="rs-board-tag-name" :title="r.tag">{{ r.tag }}</span>
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
              :class="
                numClass(estDisplay(r.est_wrong, r.est_wrong_adj), 'error')
              "
              :title="estRawTitle(r.est_wrong, r.est_wrong_adj)"
              >{{ estDisplay(r.est_wrong, r.est_wrong_adj) }}</span
            >
            <span
              class="rs-board-num"
              :class="
                numClass(
                  estDisplay(r.est_missing, r.est_missing_adj),
                  'primary',
                )
              "
              :title="estRawTitle(r.est_missing, r.est_missing_adj)"
              >{{ estDisplay(r.est_missing, r.est_missing_adj) }}</span
            >
            <span
              class="rs-board-num"
              :class="numClass(r.mismatch, 'tertiary')"
              >{{ r.mismatch ?? 0 }}</span
            >
            <span class="rs-board-num rs-board-num--muted">{{
              lastLabel(r)
            }}</span>
            <span class="rs-board-why" :title="whyText(r)">{{
              whyText(r)
            }}</span>
            <!-- The blocked reason rides on this WRAPPER, not on the button:
                 a `disabled` button dispatches no pointer events in Chromium,
                 so a `title` on it would never surface a tooltip. The wrapper
                 is not disabled, so hovering the control still explains it. -->
            <span
              class="rs-board-action"
              :title="startBlockedReason(r) || undefined"
            >
              <button
                v-if="openSessionFor(r.tag)"
                class="rs-board-btn rs-board-btn--open"
                type="button"
                @click="store.openSession(openSessionFor(r.tag).id)"
              >
                Open <v-icon size="14">mdi-arrow-right</v-icon>
              </button>
              <!-- Disabled ONLY when this row's review is provably empty AND
                   the board scope is the review's scope - see
                   startBlockedReason(). The reason names the cause and the
                   remedy, so the tooltip is the whole explanation. -->
              <button
                v-else
                class="rs-board-btn"
                :class="{ 'rs-board-btn--blocked': startBlockedReason(r) }"
                type="button"
                :disabled="!!startBlockedReason(r)"
                :title="startBlockedReason(r) || undefined"
                @click="emit('start-review', r.tag)"
              >
                Start review
              </button>
            </span>
          </div>
        </div>

        <!-- Zero-Priority tail disclosure. The rows are never dropped, only
             collapsed; the count is the real number of hidden rows. -->
        <button
          v-if="hiddenCount > 0"
          class="rs-board-more"
          type="button"
          :aria-expanded="tailExpanded"
          :aria-controls="TAIL_ID"
          :title="TAIL_TOGGLE_TITLE"
          @click="tailExpanded = !tailExpanded"
        >
          <v-icon size="14">{{
            tailExpanded ? "mdi-chevron-up" : "mdi-chevron-down"
          }}</v-icon>
          {{ tailToggleLabel }}
        </button>

        <p v-if="sorted.length" class="rs-board-legend">
          “Priority” = a fast ranking estimate (est. wrong + est. missing +
          mismatches) for sorting tags - not the number of cards a review
          session will contain, which comes from a separate, slower scan (bar
          colour:
          <span class="rs-legend-error">red</span> = worst tags,
          <span class="rs-legend-warning">amber</span> = notable,
          <span class="rs-legend-tertiary">teal</span> = minor) · “Est. wrong” =
          tagged pictures the model is ≤10% sure about · “Est. missing” =
          untagged pictures it is ≥90% sure about - both discounted by the tag’s
          measured reliability, so the estimate reflects likely genuine fixes,
          not raw model flags (hover for the raw count) · “Mismatch” =
          near-identical shots with different labels · “Last review” = when a
          review for the tag was last archived.
        </p>
      </template>
    </div>
  </div>
</template>

<script setup>
// The tag health board: the answer to "what should I review?". Design locked
// per the 2026-07-15 decisions: compact density, icon anomaly marker, heat
// health bar, "Why it ranks here" shown, wide table. Tags outside the tagger
// vocabulary keep an ENABLED "Start review" (kNN review still works) plus a
// "no model signal" chip.
import { computed, nextTick, ref, watch } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
// Same words as NewReviewDialog's locked set option/trigger tooltip - one
// source so the two surfaces can't drift apart.
import { LOCKED_SET_HEADLINE, lockedSetTitle } from "./lockedSetCopy";
// relativeDate already solves the "naive ISO string = UTC" quirk backend
// timestamps carry (computed_at is the same shape as the snapshot timestamps
// this helper was written for) - reuse it rather than re-deriving the same
// fix here.
import { relativeDate } from "../../utils/snapshots";
// Pure ranking/explanation logic, split out for direct-import unit testing -
// see tagHealthBoardLogic.js's module doc for why.
import {
  corrections,
  whyText,
  rawCorrections,
  estDisplay,
  estRawTitle,
  zeroYieldReason,
  zeroTailStart,
} from "./tagHealthBoardLogic";

const emit = defineEmits(["start-review"]);
const store = useReviewSessionsStore();

const anomalyOnly = ref(false);
const filter = ref("");
const filterRef = ref(null);
const sort = ref({ key: "score", dir: "desc" });

// --- Persistent rebuild control (Spec B) ------------------------------------

const relativeComputedAt = computed(() => relativeDate(store.healthComputedAt));
const rebuildTitle = computed(() => {
  if (store.healthBuilding) return "Rebuilding…";
  if (store.healthStale)
    return "Tag health hasn't been recomputed since new activity - rebuild now, or it'll catch up automatically shortly.";
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
];

const SUBTITLE = {
  score:
    "Sorted by how worth reviewing each tag looks - a fast estimate, not a review-session size.",
  tag: "Sorted alphabetically by tag name.",
  wrong: "Sorted by how many pictures probably have this tag by mistake.",
  missing: "Sorted by how many pictures are probably missing this tag.",
  dups: "Sorted by how many near-identical shots disagree on this tag.",
  last: "Sorted by when each tag was last reviewed - longest ago first.",
};

const subtitle = computed(() => SUBTITLE[sort.value.key] || SUBTITLE.score);

const headers = [
  { label: "Tag", key: "tag" },
  {
    label: "Priority",
    key: "score",
    tip: "A fast ranking estimate (est. wrong + est. missing + mismatches), used to sort tags by how worth reviewing they look. Not a forecast of how many cards a review session will contain - Start review runs a separate, slower scan (nearest-neighbour comparison) that usually finds a smaller, different set of pictures.",
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
    tip: "Near-identical images (duplicates or burst shots) that disagree on this tag - one has it, the other doesn’t",
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

function isAnomaly(r) {
  return store.isAnomalyTag(r.tag);
}

// `last_reviewed_at` is not in the /tag_health contract yet - treat a missing
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
    // Sort on the displayed (precision-adjusted) estimate so the "Most
    // wrong"/"Most missing" orderings match the numbers on screen.
    case "wrong":
      return estDisplay(r.est_wrong, r.est_wrong_adj);
    case "missing":
      return estDisplay(r.est_missing, r.est_missing_adj);
    case "dups":
      return r.mismatch ?? 0;
    case "last":
      return lastValue(r);
    default:
      return 0;
  }
}

const filteredRows = computed(() => {
  const needle = filter.value.trim().toLowerCase();
  return store.healthRows
    .filter((r) => !anomalyOnly.value || isAnomaly(r))
    .filter((r) => !needle || r.tag.toLowerCase().includes(needle));
});

const sorted = computed(() => {
  const dir = sort.value.dir === "asc" ? 1 : -1;
  const key = sort.value.key;
  const base = filteredRows.value;

  // The default "Suggested (health)" sort (key === "score") ranks by the
  // plain Priority score, with a raw-disagreement tie-break.
  //
  // Two tags routinely round to the same corrections() value in a
  // lightly-reviewed vault, so a tag-name fallback here would decide the
  // PRIMARY ranking, not just a rare genuine tie - that's the bug this
  // tie-break closes. rawCorrections() (the un-rounded, un-discounted
  // est_wrong + est_missing + mismatch) breaks the tie with the same
  // underlying signal at full precision before falling back to tag name for
  // a genuine, full tie.
  if (key === "score") {
    return base.slice().sort((a, b) => {
      const av = corrections(a);
      const bv = corrections(b);
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

// Board scope (project/set/character): server-side - every signal column is
// recomputed for the chosen pictures, and out-of-scope-only tags drop off.
const scope = computed(() => store.healthScope);

// The set the board is currently scoped to (if any), and whether it is locked.
// `store.sets` carries `locked` straight from /picture_sets, the same source
// NewReviewDialog's listbox reads. An unknown id (sets not fetched yet) reads
// as "not locked" - the board then behaves exactly as it did before, and
// creation is still refused by the backend's 423.
const scopedSet = computed(() =>
  scope.value.setId == null
    ? null
    : (store.sets.find((s) => s.id === scope.value.setId) ?? null),
);
const scopedSetLocked = computed(() => !!scopedSet.value?.locked);
const scopedSetName = computed(
  () => scopedSet.value?.name || `Set ${scope.value.setId}`,
);

// The rows (and any focused "Start review"/sort button inside them) are removed
// the moment the scope becomes locked, which would otherwise strand focus on
// <body>. Move it to the explanation panel: it is the thing that replaced them,
// it is announced (role="status"), and Shift+Tab from it lands back on the Set
// filter the user just used.
const lockedRef = ref(null);
watch(scopedSetLocked, (locked) => {
  if (locked) nextTick(() => lockedRef.value?.focus());
});

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
      ? "rgb(var(--v-theme-dark-surface-error))"
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

// --- Provably-empty "Start review" gate --------------------------------------

// The gate is only sound when the row's signals describe the review that the
// button would actually create - i.e. when the board's scope IS the review's
// scope. ReviewSessionsOverlay.vue's `openNewReview()` (~:205-213) inherits the
// board scope into the dialog ONLY when `store.healthScoped` is true; on an
// unscoped board it prefills from the app selection instead
// (`initialScopeFromSelection()`), which can be an entirely different set of
// pictures from the vault-wide numbers on this row. Disabling a button on
// signals that don't describe the resulting review would be a false negative,
// so outside the scope-matching case the button stays enabled and the click
// falls through to the dialog exactly as before.
//
// (This condition is deliberately a strict SUBSET of "the scopes match": an
// unscoped board with an empty app selection also matches, but the board can't
// see the app selection from here, and erring toward "enabled" is the safe
// direction.)
const gateApplies = computed(() => store.healthScoped);

// Keyed by tag so the template resolves each row's reason with one lookup
// instead of recomputing it for :disabled, :title, and :class separately.
const blockedStarts = computed(() => {
  const blocked = new Map();
  if (!gateApplies.value) return blocked;
  for (const r of store.healthRows) {
    const reason = zeroYieldReason(r);
    if (reason) blocked.set(r.tag, reason);
  }
  return blocked;
});

function startBlockedReason(r) {
  // A row with an open session renders "Open", not "Start review" - that
  // session already exists and its cards are already there, so the
  // would-be-empty reason does not apply to it (and must not end up on the
  // wrapper's tooltip).
  if (openSessionFor(r.tag)) return null;
  return blockedStarts.value.get(r.tag) ?? null;
}

// --- Zero-Priority tail disclosure -------------------------------------------

// The board renders EVERY tag (no threshold, no filter - a Priority-0 tag
// routinely still has reviewable work, see zeroTailStart's note), which makes
// a mature vault a very long list. The Priority-0 rows collapse behind a
// disclosure instead, defaulting closed.
//
// Only the default Priority-descending ordering actually groups those rows into
// a contiguous tail. Under any other sort they are interleaved with scored
// rows, and hiding them would be a filter dressed up as a disclosure - so the
// control only appears when the ordering guarantees a genuine tail. Everything
// below derives from `sorted`, so re-sorting can never strand the disclosure in
// a state that disagrees with what is on screen.
const TAIL_ID = "rs-board-table";
const tailExpanded = ref(false);

const tailStart = computed(() => {
  if (sort.value.key !== "score" || sort.value.dir !== "desc")
    return sorted.value.length;
  const start = zeroTailStart(sorted.value, corrections);
  // Every row is Priority 0 (a fresh or fully-reviewed vault): collapsing them
  // all would leave a header row over an empty table, which reads as "no tags"
  // - strictly worse than the density it would save. Keep them visible.
  return start === 0 ? sorted.value.length : start;
});

const hiddenCount = computed(() => sorted.value.length - tailStart.value);

const visibleRows = computed(() =>
  tailExpanded.value ? sorted.value : sorted.value.slice(0, tailStart.value),
);

const tailToggleLabel = computed(() => {
  const n = hiddenCount.value;
  const tags = `${n} tag${n === 1 ? "" : "s"}`;
  return tailExpanded.value
    ? `Hide ${tags} with nothing flagged`
    : `Show ${tags} with nothing flagged`;
});

const TAIL_TOGGLE_TITLE =
  "These tags scored 0 on the fast Priority estimate - nothing flagged. A review can still find work on them: it runs a separate nearest-neighbour scan that looks at different evidence.";
</script>

<style scoped>
.rs-board {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 20px 24px;
}
.rs-board-inner {
  /* Fills the frame (sidebar excluded - that's `.rs-board`'s sibling), with
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
/* Always rendered (row count 0, 1, or many) - the escape hatch stays visible
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
  white-space: nowrap;
}
.rs-board-rebuild-persistent:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
}
.rs-board-rebuild-persistent:disabled {
  cursor: default;
  opacity: 0.7;
}
/* stale = new activity landed since the cache's computed_at - same icon as
   the review-session staleness chip (mdi-clock-alert-outline) for visual
   consistency across the two features. */
.rs-board-rebuild-persistent--stale {
  border-color: color-mix(
    in srgb,
    rgb(var(--v-theme-warning)) 55%,
    transparent
  );
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
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-board-anomaly-toggle--on {
  border-color: rgb(var(--v-theme-dark-surface-error));
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 15%, transparent);
  color: rgb(var(--v-theme-dark-surface-error));
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
   indeterminate sliding fill instead of the rebuild bar's determinate width -
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

/* Locked-scope terminal state. Mirrors ReviewSessionView.vue's `.rs-state`
   (centred column, display-size icon, headline, sub-line) - this board sits on
   the overlay's `.rs-shell`, which is dark-surface/on-dark-surface, so it uses
   the same token pair as the rest of this file. */
.rs-board-locked {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-4);
  padding: var(--space-9) var(--space-5);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
}
.rs-board-locked:focus {
  /* Focused programmatically, purely to carry focus across the rows' removal.
     It is not a control, so it takes no focus ring. */
  outline: none;
}
/* The disabled/unavailable treatment, identical to
   `.rs-listbox-option--locked` in NewReviewDialog - this is a blocked scope,
   not an informational note. */
.rs-board-locked-icon {
  color: rgba(var(--v-theme-on-dark-surface), 0.38);
}
.rs-board-locked-title {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}
.rs-board-locked-sub {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
  max-width: 420px;
}
.rs-board-locked-hint {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
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
     last · why · action (Verified column cut - see Spec E) */
  grid-template-columns: 172px 116px 98px 106px 84px 56px 1fr 116px;
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
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-board-tag-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* Flex items default to min-width: auto (their content's natural width),
     which blocks shrinking and defeats the ellipsis above - without this,
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
  color: rgb(var(--v-theme-dark-surface-error));
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
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-board-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
/* Blocked "Start review": the SAME treatment as `.rs-listbox-option--locked` in
   NewReviewDialog (38% on-dark-surface + not-allowed) - this board sits on the
   overlay's `.rs-shell`, which is the dark-surface/on-dark-surface pair, so the
   idiom transfers verbatim. It is a blocked action, not an informational note,
   and 38% is the system's disabled step (visual-language.md §11). */
.rs-board-btn--blocked {
  color: rgba(var(--v-theme-on-dark-surface), 0.38);
  cursor: not-allowed;
}
.rs-board-btn--open {
  border-color: color-mix(in srgb, rgb(var(--v-theme-accent)) 60%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 16%, transparent);
  color: rgb(var(--v-theme-accent));
}

/* Zero-Priority tail disclosure. Quiet, full-width and directly under the
   table so it reads as the continuation of the list rather than a new
   control - it is an extension of the rows above, not an action on them. */
.rs-board-more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: 100%;
  height: 28px;
  margin-top: var(--space-3);
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  background: rgba(var(--v-theme-on-dark-surface), 0.05);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  cursor: pointer;
}
.rs-board-more:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  color: rgb(var(--v-theme-on-dark-surface));
}

.rs-board-legend {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  margin-top: 10px;
  line-height: 1.5;
}
.rs-legend-error {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-legend-warning {
  color: rgb(var(--v-theme-warning));
}
.rs-legend-tertiary {
  color: rgb(var(--v-theme-tertiary));
}
</style>
