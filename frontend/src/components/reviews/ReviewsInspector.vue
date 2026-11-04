<template>
  <AppInspector
    v-model="tab"
    class="rvi"
    label="Reviews"
    :open="sidebarStore.reviewsRailOpen"
    :tabs="tabs"
  >
    <!-- The Tags view's right rail: every review, one list per tab. It is about
         the screen, not a selection, so it opens with the view and stays open
         until the reader closes it. -->
    <template #head>
      <h2 ref="titleRef" class="rvi-title" tabindex="-1">Reviews</h2>
      <AppButton
        variant="ghost"
        size="sm"
        icon-left="close"
        icon-only
        tooltip="Close this panel"
        data-testid="rvi-close"
        @click="sidebarStore.toggleReviewsRail()"
      />
    </template>

    <!-- ── Open: each open review with its scope, and the way to end it ── -->
    <template v-if="tab === 'open'">
      <div
        v-for="s in store.sessions"
        :key="s.id"
        class="rvi-row-wrap rvi-open"
        :class="{ 'rvi-row-wrap--on': isSessionShown(s.id) }"
      >
        <button
          class="rvi-row"
          type="button"
          @click="store.openSession(s.id)"
        >
          <v-icon size="15" class="rvi-glyph">mdi-tag-outline</v-icon>
          <span class="rvi-two">
            <span class="rvi-tag">{{ s.tag }}</span>
            <small>{{ openSummary(s) }}</small>
          </span>
          <span class="rvi-prog" aria-hidden="true"
            ><b :style="{ width: `${progressPct(s)}%` }"></b
          ></span>
        </button>
        <v-menu location="bottom end" :offset="4">
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="rvi-more"
              type="button"
              aria-haspopup="menu"
              :aria-label="`Actions for the review of “${s.tag}”`"
            >
              <v-icon size="16">mdi-dots-vertical</v-icon>
            </button>
          </template>
          <div class="ctx-menu" role="menu">
            <button
              class="ctx-item"
              type="button"
              role="menuitem"
              @click="store.openSession(s.id)"
            >
              <v-icon class="ctx-icon">mdi-play-circle-outline</v-icon>
              Continue review
            </button>
            <button
              class="ctx-item"
              type="button"
              role="menuitem"
              :disabled="isReadOnly"
              @click="endReview(s.id)"
            >
              <v-icon class="ctx-icon">mdi-stop-circle-outline</v-icon>
              End review…
            </button>
          </div>
        </v-menu>
      </div>
      <p class="rvi-note">
        <template v-if="store.sessions.length"
          >The sidebar takes you to an open review. This tab is where you see
          its scope and end it.</template
        >
        <template v-else
          >No open reviews. Start one from a tag on the board.</template
        >
      </p>
    </template>

    <!-- ── Archived: the receipts of reviews ended and kept ── -->
    <template v-else-if="tab === 'archived'">
      <template v-for="group in archivedGroups" :key="group.label">
        <div class="section-label rvi-sec">{{ group.label }}</div>
        <!-- A wrapper DIV so the per-item delete is a real sibling <button>,
             never nested inside the receipt button. -->
        <div
          v-for="a in group.rows"
          :key="a.id"
          class="rvi-row-wrap rvi-archived"
          :class="{ 'rvi-row-wrap--on': isArchivedShown(a.id) }"
        >
          <button
            class="rvi-row"
            type="button"
            @click="store.openArchived(a.id)"
          >
            <Tooltip
              :text="`Show the receipt for “${a.tag}”`"
              activator="parent"
            />
            <v-icon size="15" class="rvi-glyph rvi-glyph--ok">mdi-check</v-icon>
            <span class="rvi-tag" :title="a.tag">{{ a.tag }}</span>
            <span class="rvi-meta">{{ archivedSummary(a) }}</span>
          </button>
          <!-- One click: a receipt is only a summary, deleting it never
               reverses a change. -->
          <button
            v-if="!isReadOnly"
            class="rvi-del"
            type="button"
            :aria-label="`Delete the archived review for ${a.tag}`"
            @click.stop="onDeleteArchived(a.id)"
          >
            <Tooltip
              :text="`Delete the archived review for “${a.tag}”`"
              activator="parent"
              :describe="false"
            />
            <v-icon size="14">mdi-close</v-icon>
          </button>
        </div>
      </template>
      <p class="rvi-note">
        A receipt is the summary of a review you ended and kept. Removing one
        never reverses a change.
      </p>
    </template>

    <!-- ── Stickers: only while "Pretend this is fun" is on ── -->
    <template v-else-if="tab === 'stickers'">
      <div v-if="store.stickers.length" class="rvi-stickers">
        <ReviewSticker
          v-for="(s, i) in store.stickers"
          :key="s.id"
          :icon="s.icon"
          :color="s.color"
          :size="store.stickers.length > 12 ? 27 : 34"
          :tilt="((i % 5) - 2) * 4"
          :fresh="i === store.stickers.length - 1"
          :label="s.tag ? `${s.label} - earned reviewing “${s.tag}”` : s.label"
        />
      </div>
      <p v-else class="rvi-note">
        No stickers yet. Finish a review to earn one.
      </p>
    </template>

    <!-- The tab's only destructive control, away from the list. -->
    <template v-if="clearLabel" #footer>
      <div class="rvi-foot">
        <AppButton
          variant="ghost"
          size="sm"
          icon-left="trash-can-outline"
          class="rvi-clear"
          @click="onClear"
        >
          {{ clearLabel }}
        </AppButton>
      </div>
    </template>
  </AppInspector>

  <ReviewEndDialog ref="endDialogRef" @closed="onEndClosed" />
</template>

<script setup>
// The Reviews inspector (Tags view): Open | Archived | Stickers. It replaced
// the board's footer (archived receipts, their clear, the sticker shelf) and
// is where an open review's scope is read and the review ended, through the
// same "End review…" dialog as the sidebar row.
import { computed, nextTick, ref, watch } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useConfirm } from "../../composables/useConfirm";
import { isReadOnly } from "../../utils/apiClient";
import AppInspector from "../widgets/AppInspector.vue";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import ReviewSticker from "./ReviewSticker.vue";
import ReviewEndDialog from "./ReviewEndDialog.vue";

const store = useReviewSessionsStore();
const sidebarStore = useSidebarStore();

const tab = ref("open");
const titleRef = ref(null);
const endDialogRef = ref(null);

const tabs = computed(() => {
  const out = [
    { value: "open", label: "Open", count: store.sessions.length },
    { value: "archived", label: "Archived", count: store.archived.length },
  ];
  if (store.gamify) {
    out.push({ value: "stickers", label: "Stickers", count: store.stickers.length });
  }
  return out;
});

// Turning "Pretend this is fun" off takes the Stickers tab away (the
// collection is kept); a reader left on it lands on Open.
watch(
  () => store.gamify,
  (on) => {
    if (!on && tab.value === "stickers") tab.value = "open";
  },
);

// Opening a receipt shows the list it came from.
watch(
  () => store.view.type,
  (type) => {
    if (type === "archived") tab.value = "archived";
  },
);

function isSessionShown(id) {
  return store.view.type === "session" && store.view.id === id;
}

function isArchivedShown(id) {
  return store.view.type === "archived" && store.view.id === id;
}

// --- Open ---------------------------------------------------------------------

function progressPct(s) {
  const found = s.stats?.found ?? 0;
  return found ? Math.round(((s.progress?.done ?? 0) / found) * 100) : 0;
}

// Resolve the frozen scope JSON to a short label using the option lists the
// store loaded for the creation dialog. Unknown ids degrade to the raw id.
function scopeLabel(s) {
  const scope = s.scope || {};
  const parts = [];
  if (scope.project_id != null) {
    const p = store.projects.find((x) => x.id === scope.project_id);
    parts.push(`Project: ${p?.name ?? scope.project_id}`);
  }
  if (scope.set_id != null) {
    const set = store.sets.find((x) => x.id === scope.set_id);
    parts.push(`Set: ${set?.name ?? scope.set_id}`);
  }
  if (scope.character_id != null && scope.character_id !== "") {
    if (String(scope.character_id) === "UNASSIGNED") {
      parts.push("Character: Unassigned");
    } else {
      const c = store.characters.find(
        (x) => String(x.id) === String(scope.character_id),
      );
      parts.push(`Character: ${c?.name ?? scope.character_id}`);
    }
  }
  return parts.length ? parts.join(" · ") : "Whole library";
}

function openSummary(s) {
  return `${s.progress?.done ?? 0} of ${s.stats?.found ?? 0} · ${scopeLabel(s)}`;
}

function endReview(id) {
  endDialogRef.value?.open(id);
}

// Ending a review removes its row (and the menu that held focus); focus goes
// to the pane's title rather than dropping to <body>.
function onEndClosed() {
  nextTick(() => {
    const el = document.activeElement;
    if (!el || el === document.body || !el.isConnected) titleRef.value?.focus();
  });
}

// --- Archived -----------------------------------------------------------------

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

function whenOf(a) {
  const t = new Date(a.refreshed_at || a.created_at).getTime();
  return Number.isNaN(t) ? null : t;
}

// Grouped by week: the list only grows, since no receipt expires on its own.
const archivedGroups = computed(() => {
  const cutoff = Date.now() - WEEK_MS;
  const recent = [];
  const earlier = [];
  for (const a of store.archived) {
    const t = whenOf(a);
    (t != null && t >= cutoff ? recent : earlier).push(a);
  }
  return [
    { label: "This week", rows: recent },
    { label: "Earlier", rows: earlier },
  ].filter((g) => g.rows.length);
});

// A receipt now only exists for a kept ending, so the number is what is still
// true in the library: the decided rows (`progress.done`, skips excluded).
function archivedSummary(a) {
  const kept = a.progress?.done ?? 0;
  const t = whenOf(a);
  const when =
    t == null
      ? ""
      : new Date(t).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        });
  return when ? `${kept} kept · ${when}` : `${kept} kept`;
}

// Per-item delete: focus the next row's delete button, or the pane's title when
// that was the last receipt.
async function onDeleteArchived(id) {
  const index = store.archived.findIndex((a) => a.id === id);
  await store.deleteArchived(id);
  await nextTick();
  const pane = titleRef.value?.closest(".inspector");
  const dels = pane?.querySelectorAll(".rvi-archived .rvi-del") ?? [];
  const next = dels[Math.min(Math.max(index, 0), dels.length - 1)];
  (next ?? titleRef.value)?.focus();
}

// --- Footer -------------------------------------------------------------------

const clearLabel = computed(() => {
  if (isReadOnly.value) return "";
  if (tab.value === "archived" && store.archived.length) return "Clear archived…";
  if (tab.value === "stickers" && store.stickers.length) return "Clear stickers…";
  return "";
});

async function onClear() {
  const archived = tab.value === "archived";
  const ok = await useConfirm().confirm(
    archived
      ? {
          title: "Clear every archived review?",
          message: `${store.archived.length} receipt${store.archived.length === 1 ? "" : "s"} will be deleted. The changes the reviews made stay as they are.`,
          confirmLabel: "Clear archived",
          danger: true,
        }
      : {
          title: "Clear every sticker?",
          message: "The collection cannot be brought back.",
          confirmLabel: "Clear stickers",
          danger: true,
        },
  );
  if (!ok) return;
  if (archived) await store.clearArchived();
  else store.clearStickers();
  await nextTick();
  titleRef.value?.focus();
}
</script>

<style scoped>
.rvi-title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The body's gap separates sections; rows sit flush against each other. */
.rvi :deep(.inspector-body) {
  gap: 0;
  padding-top: var(--space-2);
}

.rvi-sec {
  padding: var(--space-4) var(--space-3) var(--space-2);
}

/* A row: the wrap carries the hover / current surface so it runs under the
   sibling button on the trailing edge. */
.rvi-row-wrap {
  display: flex;
  align-items: center;
  border-radius: var(--radius-sm);
  border-left: 3px solid transparent;
}
.rvi-row-wrap:hover,
.rvi-row-wrap:focus-within {
  background: var(--hover-wash);
}
.rvi-row-wrap--on {
  background: var(--active-wash);
  border-left-color: var(--active-bar);
}
.rvi-row-wrap--on:hover,
.rvi-row-wrap--on:focus-within {
  background: linear-gradient(var(--hover-wash), var(--hover-wash)), var(--active-wash);
}

.rvi-row {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h-bar);
  padding: var(--space-2) var(--space-3);
  text-align: left;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.rvi-glyph {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.rvi-glyph--ok {
  color: rgb(var(--v-theme-surface-success));
}

.rvi-tag {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rvi-two {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: var(--leading-snug);
}
.rvi-two small,
.rvi-meta {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-variant-numeric: tabular-nums;
}
.rvi-meta {
  flex-shrink: 0;
}

/* The sidebar row's progress bar, same tertiary fill. */
.rvi-prog {
  flex-shrink: 0;
  position: relative;
  width: 52px;
  height: 4px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
}
.rvi-prog b {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-tertiary));
}

.rvi-more,
.rvi-del {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  margin-right: var(--space-2);
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
.rvi-more:hover,
.rvi-del:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

/* The per-item delete is hidden until the row is hovered or holds focus;
   `visibility` keeps its box, so revealing it never reflows the row. */
.rvi-del {
  visibility: hidden;
}
.rvi-row-wrap:hover .rvi-del,
.rvi-row-wrap:focus-within .rvi-del {
  visibility: visible;
}

.rvi-note {
  margin: var(--space-3) 0 0;
  padding: 0 var(--space-3);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rvi-stickers {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
}

.rvi-foot {
  flex-shrink: 0;
  display: flex;
  justify-content: flex-end;
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}
.rvi-clear {
  color: rgb(var(--v-theme-surface-error));
}
</style>
