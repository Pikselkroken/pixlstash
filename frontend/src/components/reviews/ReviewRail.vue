<template>
  <section
    ref="railRootRef"
    class="rs-rail"
    aria-label="Your reviews"
    tabindex="-1"
  >
    <!-- The board's footer: the archived receipts and the sticker shelf. The
         open reviews, their scope and Abort live in the sidebar under "Tags to
         review"; the way out of the view is the toolbar's back button. The
         root takes tabindex -1 so focus has a home when the row that held it
         is removed (the last archived receipt), instead of dropping to body. -->
    <div class="rs-rail-scroll">
      <template v-if="store.archived.length">
        <div class="rs-rail-label rs-rail-label--archived rs-archived-head">
          <span>Archived</span>
          <span class="rs-archived-spacer"></span>
          <!-- Clearing every receipt is permanent, so it takes two clicks
               (same arm→"Sure?"→wipe pattern as the sticker shelf clear): the
               first arms a state that auto-reverts after a few seconds, the
               second wipes the list. Only the receipts go - the decisions were
               written through during each review and are untouched. -->
          <button
            class="rs-archived-clear"
            :class="{ 'rs-archived-clear--armed': archivedClearArmed }"
            type="button"
            :aria-label="
              archivedClearArmed
                ? 'Confirm: clear every archived review'
                : 'Clear all archived reviews'
            "
            @click="onClearArchivedClick"
          >
            <Tooltip
              :text="
                archivedClearArmed
                  ? 'Click again to clear every archived review - this cannot be undone'
                  : 'Clear all archived reviews'
              "
              activator="parent"
            />
            <v-icon size="14">mdi-trash-can-outline</v-icon>
            <span v-if="archivedClearArmed">Sure?</span>
          </button>
        </div>
        <!-- Each row is a wrapper DIV so the per-item delete can be a real
             sibling <button> (never nested inside the receipt button), revealed
             on hover / focus-within - mirrors the open-session rows above. -->
        <div
          v-for="a in store.archived"
          :key="a.id"
          class="rs-rail-archived-wrap"
          :class="{ 'rs-rail-archived-wrap--active': isArchivedActive(a.id) }"
        >
          <button
            class="rs-rail-item rs-rail-archived"
            type="button"
            @click="store.openArchived(a.id)"
          >
            <Tooltip
              :text="`Show the receipt for “${a.tag}”`"
              activator="parent"
            />
            <v-icon size="14" class="rs-rail-archived-check">mdi-check</v-icon>
            <span class="rs-rail-archived-tag" :title="a.tag">{{ a.tag }}</span>
            <span class="rs-rail-archived-sum">{{ archivedSummary(a) }}</span>
          </button>
          <!-- One-click delete: a receipt is just an audit summary (decisions
               are preserved server-side), so it needs no confirm. Sibling
               button, box reserved via visibility so revealing it never
               reflows the row. -->
          <button
            class="rs-rail-archived-del"
            type="button"
            :aria-label="`Delete the archived review for ${a.tag}`"
            @click.stop="onDeleteArchived(a.id)"
          >
            <Tooltip
              :text="`Delete the archived review for “${a.tag}”`"
              activator="parent"
              :describe="false"
            />
            <v-icon size="13">mdi-close</v-icon>
          </button>
        </div>
      </template>
    </div>

    <!-- Sticker shelf: earned rewards live here. Capped (~1/3 rail height) and
         scrollable so it always yields space to the navigation above; stickers
         shrink when the collection grows. Only shown while "Pretend this is
         fun" is on - the collection is kept, not cleared, so toggling the
         setting back restores it. -->
    <div v-if="store.gamify && store.stickers.length" class="rs-shelf">
      <div class="rs-rail-label rs-shelf-label">
        <v-icon size="13">mdi-sticker-circle-outline</v-icon>
        Stickers
        <span class="rs-shelf-count">· {{ store.stickers.length }}</span>
        <span class="rs-shelf-spacer"></span>
        <!-- Clearing is permanent (the collection is not recoverable), so it
             takes two clicks: the first arms a "Sure?" state that auto-reverts
             after a few seconds, the second wipes the shelf. -->
        <button
          class="rs-shelf-clear"
          :class="{ 'rs-shelf-clear--armed': clearArmed }"
          type="button"
          :aria-label="
            clearArmed
              ? 'Click again to clear every sticker - this cannot be undone'
              : 'Clear all stickers'
          "
          @click="onClearClick"
        >
          <Tooltip
            :text="
              clearArmed
                ? 'Click again to clear every sticker - this cannot be undone'
                : 'Clear all stickers'
            "
            activator="parent"
            :describe="false"
          />
          <v-icon size="14">mdi-trash-can-outline</v-icon>
          <span v-if="clearArmed">Sure?</span>
        </button>
        <button
          class="rs-shelf-toggle"
          type="button"
          :aria-label="shelfOpen ? 'Collapse the shelf' : 'Show the shelf'"
          :aria-expanded="shelfOpen"
          @click="shelfOpen = !shelfOpen"
        >
          <Tooltip
            :text="shelfOpen ? 'Collapse the shelf' : 'Show the shelf'"
            activator="parent"
            :describe="false"
          />
          <v-icon size="15">{{
            shelfOpen ? "mdi-chevron-down" : "mdi-chevron-up"
          }}</v-icon>
        </button>
      </div>
      <div v-if="shelfOpen" class="rs-shelf-grid">
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
    </div>
  </section>
</template>

<script setup>
import { nextTick, onUnmounted, ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import ReviewSticker from "./ReviewSticker.vue";
import Tooltip from "../widgets/Tooltip.vue";

const store = useReviewSessionsStore();

const shelfOpen = ref(true);
const railRootRef = ref(null);

// Two-step sticker clear: first click arms, second (within the window) wipes.
const clearArmed = ref(false);
let clearArmTimer = null;
const CLEAR_ARM_MS = 3000;

function onClearClick() {
  clearTimeout(clearArmTimer);
  if (!clearArmed.value) {
    clearArmed.value = true;
    clearArmTimer = setTimeout(() => {
      clearArmed.value = false;
    }, CLEAR_ARM_MS);
    return;
  }
  clearArmed.value = false;
  store.clearStickers();
}

// Two-step clear-all for the Archived list - same arm→confirm→wipe pattern as
// the sticker shelf clear above, with its own ref + timer (never shared).
const archivedClearArmed = ref(false);
let archivedClearTimer = null;

// The whole Archived section unmounts when the list empties, so the button the
// user just pressed disappears and focus would fall to <body>. Capture the
// fallback target BEFORE the store call (it's still mounted), then move focus
// once the removal has rendered. The rail root is the fallback: it is the only
// thing left in the footer once the Archived section is gone.
function railFallbackEl() {
  return railRootRef.value ?? null;
}

async function onClearArchivedClick() {
  clearTimeout(archivedClearTimer);
  if (!archivedClearArmed.value) {
    archivedClearArmed.value = true;
    archivedClearTimer = setTimeout(() => {
      archivedClearArmed.value = false;
    }, CLEAR_ARM_MS);
    return;
  }
  archivedClearArmed.value = false;
  const fallback = railFallbackEl();
  await store.clearArchived();
  await nextTick();
  fallback?.focus();
}

// Per-item delete: focus the next archived row's delete button, or the rail
// root when that was the last archived item.
async function onDeleteArchived(id) {
  const index = store.archived.findIndex((a) => a.id === id);
  const wasLast = store.archived.length <= 1;
  const fallback = railFallbackEl();
  await store.deleteArchived(id);
  await nextTick();
  if (wasLast) {
    fallback?.focus();
    return;
  }
  const dels =
    railRootRef.value?.querySelectorAll(".rs-rail-archived-del") ?? [];
  const next = dels[Math.min(Math.max(index, 0), dels.length - 1)];
  (next ?? fallback)?.focus();
}

onUnmounted(() => {
  clearTimeout(clearArmTimer);
  clearTimeout(archivedClearTimer);
});

function isArchivedActive(id) {
  return store.view.type === "archived" && store.view.id === id;
}

function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function archivedSummary(a) {
  const reviewed = a.stats?.found ?? 0;
  const when = shortDate(a.refreshed_at || a.created_at);
  return when ? `${reviewed} reviewed · ${when}` : `${reviewed} reviewed`;
}
</script>

<style scoped>
.rs-rail {
  display: flex;
  flex-direction: column;
  max-width: 480px;
  color: rgb(var(--v-theme-on-dark-surface));
}

.rs-rail-scroll {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.rs-rail-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 100%;
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-dark-surface));
}
/* The rail is dark in both themes: the dark-surface hover wash. */
.rs-rail-item:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}
.rs-rail-label {
  padding: var(--space-3) var(--space-3) var(--space-1);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.rs-rail-label--archived {
  padding-top: var(--space-4);
}
/* Archived header carries the "Clear all" control on its trailing edge. */
.rs-archived-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.rs-archived-spacer {
  flex: 1;
}
/* Clear-all: mirrors the sticker-shelf clear (.rs-shelf-clear) so both
   destructive clears read identically. */
.rs-archived-clear {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-archived-clear:hover {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-archived-clear--armed {
  color: rgb(var(--v-theme-dark-surface-error));
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
}

/* The wrap carries the card surface (hover / active) so it extends under the
   delete button beside the receipt button - same structure as the session
   rows above. */
.rs-rail-archived-wrap {
  display: flex;
  align-items: center;
  border-radius: var(--radius-sm);
}
.rs-rail-archived-wrap:hover,
.rs-rail-archived-wrap:focus-within {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}
.rs-rail-archived-wrap--active {
  background: rgba(var(--v-theme-dark-surface-primary), 0.2);
}
.rs-rail-archived-wrap--active:hover,
.rs-rail-archived-wrap--active:focus-within {
  background: linear-gradient(rgba(var(--v-theme-on-dark-surface), 0.16), rgba(var(--v-theme-on-dark-surface), 0.16))
    rgba(var(--v-theme-dark-surface-primary), 0.2);
}
.rs-rail-archived-wrap:hover .rs-rail-archived,
.rs-rail-archived-wrap:focus-within .rs-rail-archived,
.rs-rail-archived-wrap--active .rs-rail-archived {
  background: transparent;
}
.rs-rail-archived {
  flex: 1;
  min-width: 0;
  flex-direction: row;
  align-items: center;
  gap: var(--space-2);
  padding: 7px var(--space-3);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-rail-archived-check {
  color: rgb(var(--v-theme-dark-surface-success));
}
.rs-rail-archived-tag {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rs-rail-archived-sum {
  font-size: var(--text-2xs);
  white-space: nowrap;
}

/* Per-item delete: a real sibling button, invisible until the row is hovered or
   anything in it has focus (focus-within keeps it keyboard-reachable).
   `visibility` (not `display`) so its box stays reserved and revealing it never
   reflows the row - same technique as the session abort control. */
.rs-rail-archived-del {
  visibility: hidden;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-right: var(--space-1);
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-rail-archived-wrap:hover .rs-rail-archived-del,
.rs-rail-archived-wrap:focus-within .rs-rail-archived-del {
  visibility: visible;
}
.rs-rail-archived-del:hover {
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
}

/* Sticker shelf: hard-capped height + own scroll so navigation always wins
   the space fight. */
.rs-shelf {
  display: flex;
  flex-direction: column;
  padding-top: var(--space-2);
  margin-top: var(--space-2);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rs-shelf-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 var(--space-3) 5px;
  flex-shrink: 0;
}
.rs-shelf-count {
  font-weight: var(--weight-medium);
}
.rs-shelf-spacer {
  flex: 1;
}
.rs-shelf-toggle {
  display: inline-flex;
  padding: 1px;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-shelf-clear {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 3px;
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-shelf-clear:hover {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-shelf-clear--armed {
  color: rgb(var(--v-theme-dark-surface-error));
  background: color-mix(in srgb, rgb(var(--v-theme-dark-surface-error)) 12%, transparent);
}
.rs-shelf-grid {
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: var(--space-1) 9px var(--space-2);
  align-content: flex-start;
}
</style>
