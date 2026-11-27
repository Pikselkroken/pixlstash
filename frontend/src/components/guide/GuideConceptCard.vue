<template>
  <!-- One concept in the Guide's grid. A greyed card keeps its border and its
       click target and always opens: only its picture loses colour, its title
       drops to medium weight, and its foot names what it needs. -->
  <button
    type="button"
    class="gcc"
    :class="{ 'gcc--grey': state === 'grey' }"
    :data-testid="`guide-card-${concept.id}`"
    :data-state="state"
    @click="emit('open', concept)"
  >
    <GuideIllustration :concept="concept" :grey="state === 'grey'" />
    <span class="gcc-body">
      <span class="gcc-title">
        <span class="gcc-name">{{ concept.name }}</span>
        <span v-if="state === 'start'" class="gcc-badge gcc-badge--start"
          >Start here</span
        >
        <span v-if="concept.unstable" class="gcc-badge gcc-badge--unstable"
          >Unstable</span
        >
      </span>
      <span class="gcc-summary">{{ concept.summary }}</span>
      <span class="gcc-foot">
        <template v-if="need">Needs {{ needLabel }}</template>
        <template v-else-if="concept.id === 'new'"
          >Pinned first · {{ headlineCount }} headlines</template
        >
        <template v-else
          >{{ goalLabel }} · {{ concept.tasks.length }}
          {{ concept.tasks.length === 1 ? "task" : "tasks" }}</template
        >
      </span>
    </span>
  </button>
</template>

<script setup>
import { computed } from "vue";
import GuideIllustration from "./GuideIllustration.vue";
import { GOALS, HEADLINES, NEEDS } from "../../guide/catalogue";
import { useGuideStore } from "../../stores/useGuideStore";

const props = defineProps({
  concept: { type: Object, required: true },
});
const emit = defineEmits(["open"]);

const guide = useGuideStore();
const state = computed(() => guide.stateOf(props.concept));
const need = computed(() =>
  state.value === "grey" ? guide.missing(props.concept) : null,
);
const needLabel = computed(() => NEEDS[need.value]?.label ?? need.value);
const goalLabel = computed(
  () => GOALS.find((g) => g.id === props.concept.goal)?.label ?? "",
);
const headlineCount = HEADLINES.length;
</script>

<style scoped>
.gcc {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  text-align: left;
  font: inherit;
  color: inherit;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: border-color var(--dur-1) var(--ease-standard);
}

.gcc:hover {
  border-color: rgba(var(--v-theme-on-surface), 0.4);
}

.gcc:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring-inset);
}

.gcc-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
}

.gcc-title {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.gcc-name {
  flex: 1;
  min-width: 0;
}

.gcc-badge {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}

/* Teal identifies; amber is kept for the one Show me on a surface. */
.gcc-badge--start {
  background: rgb(var(--v-theme-tertiary));
  color: rgb(var(--v-theme-on-tertiary));
}

.gcc-badge--unstable {
  color: rgb(var(--v-theme-surface-warning));
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-surface-warning));
}

.gcc-summary {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gcc-foot {
  margin-top: auto;
  padding-top: var(--space-2);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gcc--grey .gcc-title {
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
