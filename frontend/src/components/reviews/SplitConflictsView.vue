<template>
  <div class="rs-conflicts">
    <div class="rs-conflicts-head">
      <h2 class="rs-conflicts-title">Needs a decision</h2>
      <span class="rs-conflicts-sub">{{ subtitle }}</span>
    </div>

    <!-- Transient, non-blocking confirmation — no backend "undo" is confirmed
         for a resolved split conflict, so this downgrades to a plain
         confirmation (UX spec §5.3's own fallback) rather than showing an
         undo control that would silently fail. -->
    <div v-if="resolvedFlash" class="rs-conflicts-flash" role="status">
      <v-icon size="15">mdi-check</v-icon>
      Resolved — kept {{ resolvedFlash.label }}.
    </div>

    <section class="rs-conflicts-body">
      <div v-if="store.conflictsLoading && !store.conflicts.length" class="rs-state">
        Loading…
      </div>

      <div v-else-if="!current" class="rs-state rs-state--done">
        <v-icon size="44" class="rs-state-check">mdi-check-decagram</v-icon>
        <p class="rs-state-big">All caught up — nothing needs a decision right now.</p>
      </div>

      <div
        v-else
        ref="cardRef"
        :key="`conf-${current.componentKey}`"
        class="rs-conf-card"
        role="group"
        tabindex="-1"
        :aria-label="questionLabel"
      >
        <SplitConflictCard
          :group="current"
          :step="step"
          :busy="resolving"
          @same="onSame"
          @choose="onChoose"
          @back="step = 1"
        />
      </div>
    </section>
  </div>
</template>

<script setup>
// Main-pane view for view.type === 'conflicts' — the split-conflict
// resolution queue. Mirrors ReviewSessionView.vue's shape: this component
// owns the decision/step state, the presentational SplitConflictCard just
// renders it and emits intent. One card shown at a time (the "current" head
// of store.conflictGroups), matching the review-session queue idiom.
import { computed, nextTick, ref, watch } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import SplitConflictCard from "./SplitConflictCard.vue";

const store = useReviewSessionsStore();

const current = computed(() => store.conflictGroups[0] ?? null);
const step = ref(1); // 1 = "same shot?" · 2 = "keep together for?"
const resolving = ref(false);
const resolvedFlash = ref(null); // { label } | null
const cardRef = ref(null);

let flashTimer = null;

const subtitle = computed(() => {
  const n = store.conflictGroupCount;
  if (!n) return "Nothing pending — new items appear here automatically.";
  return `${n} pair${n === 1 ? "" : "s"} of pictures need a quick decision.`;
});

const questionLabel = computed(() =>
  step.value === 1 ? "Are these actually the same shot?" : "Keep both together for:",
);

// New card in, or the whole queue drained: step resets and focus follows the
// card (mirrors ReviewSessionView's focus-follows-card watcher).
watch(
  () => current.value?.componentKey ?? null,
  () => {
    step.value = 1;
    nextTick(() => cardRef.value?.focus?.({ preventScroll: true }));
  },
);

const RESOLVE_LABELS = {
  TRAIN: "for teaching the tagger",
  EVAL: "for checking its work",
  NEITHER: "out for now",
};

async function resolve(split) {
  const group = current.value;
  if (!group || resolving.value) return;
  resolving.value = true;
  try {
    const result = await store.resolveConflict(group.componentKey, split);
    if (result) {
      clearTimeout(flashTimer);
      resolvedFlash.value = { label: RESOLVE_LABELS[split] || "" };
      const drained = store.conflictGroupCount === 0;
      flashTimer = setTimeout(() => {
        resolvedFlash.value = null;
        // The nav row that got here (§5.1) is gone once the count hits zero —
        // send focus somewhere sane instead of stranding the user on an empty
        // pane with no way back (mirrors abortSession/archiveSession calling
        // showBoard() when the viewed item disappears out from under them).
        if (drained) store.showBoard();
      }, 4000);
    }
  } finally {
    resolving.value = false;
    step.value = 1;
  }
}

function onSame(yes) {
  if (resolving.value) return;
  if (!yes) {
    // "No" has no separate backend action — POST .../resolve is the only
    // mutation this API exposes, and it always assigns the whole component
    // to one split. NEITHER ("set aside, unused for teaching or checking")
    // is the closest available outcome to "these aren't really the same
    // shot" and matches the system's own fail-closed default.
    resolve("NEITHER");
    return;
  }
  step.value = 2;
}

function onChoose(bucket) {
  resolve(bucket);
}

// Called by the overlay's single capture-phase keydown handler (never a
// second listener) — Y/N for step 1, T/C/O for step 2, Esc collapses step 2
// back to step 1 before the overlay's own Esc ladder takes over.
function handleKey(key) {
  if (!current.value) return false;
  if (key === "escape") {
    if (step.value === 2) {
      step.value = 1;
      return true;
    }
    return false;
  }
  if (resolving.value) return false;
  if (step.value === 1) {
    if (key === "y") return onSame(true), true;
    if (key === "n") return onSame(false), true;
    return false;
  }
  if (key === "t") return onChoose("TRAIN"), true;
  if (key === "c") return onChoose("EVAL"), true;
  if (key === "o") return onChoose("NEITHER"), true;
  return false;
}

defineExpose({ handleKey });
</script>

<style scoped>
.rs-conflicts {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: 20px 24px;
}
.rs-conflicts :is(button):focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
}
.rs-conflicts-head {
  flex-shrink: 0;
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-5);
}
.rs-conflicts-title {
  font-size: 18px;
  font-weight: var(--weight-bold);
}
.rs-conflicts-sub {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.rs-conflicts-flash {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  align-self: flex-start;
  margin-bottom: var(--space-4);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-sm);
  border: 1px solid color-mix(in srgb, rgb(var(--v-theme-success)) 55%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-success)) 12%, transparent);
  color: rgb(var(--v-theme-success));
  font-size: var(--text-sm);
}

.rs-conflicts-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.rs-conf-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.rs-conf-card:focus {
  outline: none;
}
.rs-conf-card:focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: -2px;
  border-radius: var(--radius-md);
}

.rs-state {
  margin: auto;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
}
.rs-state-check {
  color: rgb(var(--v-theme-success));
}
.rs-state-big {
  font-size: 17px;
  font-weight: var(--weight-semibold);
}
</style>
