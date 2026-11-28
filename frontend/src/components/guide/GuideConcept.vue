<template>
  <!-- One concept, opened in place (#2825). Doing comes before reading: it
       opens on its tasks, and the written page is the second segment. A task
       whose content is missing still lists where it starts, and names what
       comes first instead of offering Show me. Nothing here is disabled. -->
  <div class="gc-scroll" data-testid="guide-concept">
    <header class="gc-head">
      <GuideIllustration
        class="gc-shot"
        :concept="concept"
        :grey="state === 'grey'"
      />
      <div class="gc-intro">
        <h1 class="gc-name">
          {{ concept.name }}
          <span v-if="state === 'start'" class="gc-badge gc-badge--start"
            >Start here</span
          >
          <span v-if="concept.unstable" class="gc-badge gc-badge--unstable"
            >Unstable</span
          >
        </h1>
        <p class="gc-summary">{{ concept.summary }}</p>
        <p class="gc-meta">
          {{ goalLabel }} · {{ workspaceLabels }}
          <template v-if="conceptNeed"> · Needs {{ needLabel(conceptNeed) }}</template>
        </p>
      </div>
    </header>

    <div class="gc-bar">
      <Segmented
        v-model="view"
        :options="viewOptions"
        aria-label="Show the concept as"
      />
      <span class="gc-count"
        >{{ concept.tasks.length }}
        {{ concept.tasks.length === 1 ? "task" : "tasks" }} ·
        {{ readyCount }} ready now</span
      >
    </div>

    <ol v-if="view === 'tasks'" class="gc-tasks">
      <li
        v-for="(task, i) in concept.tasks"
        :key="task.id"
        class="gc-task"
        :class="{
          'gc-task--grey': !!missing(task),
          'gc-task--focus': task.id === focusTask,
        }"
        :data-testid="`guide-task-${task.id}`"
      >
        <span class="gc-num" aria-hidden="true">{{ i + 1 }}</span>
        <span class="gc-task-text">
          <span class="gc-task-name">
            {{ task.name }}
            <span v-if="task.unstable" class="gc-badge gc-badge--unstable"
              >Unstable</span
            >
          </span>
          <span class="gc-task-entry">{{ task.entry }}</span>
        </span>
        <button
          v-if="missing(task)"
          type="button"
          class="gc-first"
          :data-testid="`guide-first-${task.id}`"
          @click="openFirst(missing(task))"
        >
          First: {{ firstName(missing(task)) }}
        </button>
        <AppButton
          v-else
          :variant="task.id === firstReady ? 'primary' : 'secondary'"
          size="sm"
          :data-testid="`guide-show-${task.id}`"
          @click="show(task)"
          >Show me</AppButton
        >
      </li>
    </ol>

    <article v-else-if="view === 'read'" class="gc-read" data-testid="guide-read">
      <section
        v-for="task in concept.tasks"
        :id="`guide-read-${task.id}`"
        :key="task.id"
        class="gc-read-task"
      >
        <h2 class="gc-read-name">{{ task.name }}</h2>
        <p class="gc-read-sentence">{{ task.sentence }}</p>
        <p class="gc-read-line">
          <span class="gc-read-label">Where</span> {{ task.entry }}
        </p>
        <p v-if="missing(task)" class="gc-read-line">
          <span class="gc-read-label">First</span>
          {{ needLabel(missing(task)) }}:
          <button
            type="button"
            class="gc-first gc-first--inline"
            @click="openFirst(missing(task))"
          >
            {{ firstName(missing(task)) }}
          </button>
        </p>
        <ol class="gc-read-steps">
          <li v-for="(step, n) in task.steps" :key="n">{{ step.say }}</li>
        </ol>
      </section>
    </article>

    <!-- The server's own reference, live, not a copy that can drift. -->
    <iframe
      v-else
      class="gc-ref"
      :src="referenceUrl"
      :title="`${concept.name} reference`"
      data-testid="guide-reference"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppButton from "../widgets/AppButton.vue";
import Segmented from "../widgets/Segmented.vue";
import GuideIllustration from "./GuideIllustration.vue";
import { GOALS, NEEDS, WORKSPACES } from "../../guide/catalogue";
import { firstConceptFor, useGuideStore } from "../../stores/useGuideStore";
import { BACKEND_URL } from "../../utils/apiClient";

const props = defineProps({
  concept: { type: Object, required: true },
});

const route = useRoute();
const router = useRouter();
const guide = useGuideStore();

const state = computed(() => guide.stateOf(props.concept));
const conceptNeed = computed(() => guide.missing(props.concept));
const missing = (task) => guide.missing(task);
const readyCount = computed(
  () => props.concept.tasks.filter((t) => !missing(t)).length,
);
/** The one amber Show me on the page: the first task that can be done. */
const firstReady = computed(
  () => props.concept.tasks.find((t) => !missing(t))?.id ?? null,
);
/** A headline opens its concept on its task. */
const focusTask = computed(() =>
  typeof route.query.task === "string" ? route.query.task : null,
);

/** A concept with a server page (API › /scalar) gets a third view of it. */
const viewOptions = computed(() => [
  { id: "tasks", label: "Tasks", testid: "guide-view-tasks" },
  { id: "read", label: "Read", testid: "guide-view-read" },
  ...(props.concept.reference
    ? [{ id: "reference", label: "Reference", testid: "guide-view-reference" }]
    : []),
]);
const referenceUrl = computed(() => `${BACKEND_URL}${props.concept.reference}`);

const view = computed({
  get: () =>
    viewOptions.value.some((o) => o.id === route.query.view)
      ? route.query.view
      : "tasks",
  set: (v) =>
    router.replace({
      query: { ...route.query, view: v === "tasks" ? undefined : v },
    }),
});

const goalLabel = computed(
  () => GOALS.find((g) => g.id === props.concept.goal)?.label ?? "",
);
const workspaceLabels = computed(() =>
  (props.concept.workspaces ?? [])
    .map((id) => WORKSPACES.find((w) => w.id === id)?.label ?? id)
    .join(", "),
);

const needLabel = (key) => NEEDS[key]?.label ?? key;
const firstName = (key) => firstConceptFor(key)?.name ?? needLabel(key);

async function openFirst(key) {
  const target = firstConceptFor(key);
  if (!target) return;
  // What comes first is on this page (People › Create a person): ring it.
  if (target.id === props.concept.id) {
    const task = firstReady.value;
    // From Read the task list renders only after the route has changed.
    await router.replace({ query: { ...route.query, view: undefined, task: task ?? undefined } });
    await nextTick();
    if (task)
      document
        .querySelector(`[data-testid="guide-task-${task}"]`)
        ?.scrollIntoView?.({ block: "center" });
    return;
  }
  router.push({ path: `/guide/${target.id}`, query: { in: route.query.in } });
}

/** Leave the Guide for the real screen; the wizard takes it from there. */
function show(task) {
  guide.startTask(props.concept.id, task.id);
}

onMounted(async () => {
  if (!focusTask.value) return;
  await nextTick();
  document
    .querySelector(`[data-testid="guide-task-${focusTask.value}"]`)
    ?.scrollIntoView?.({ block: "center" });
});
</script>

<style scoped>
.gc-scroll {
  container-type: inline-size;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-6) var(--space-6) var(--space-8);
}

.gc-head {
  display: grid;
  grid-template-columns: minmax(0, 360px) minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
  max-width: 960px;
}

.gc-shot {
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.gc-name {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin: 0 0 var(--space-3);
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}

.gc-summary {
  margin: 0 0 var(--space-3);
  font-size: var(--text-md);
  line-height: var(--leading-body);
}

.gc-meta {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gc-badge {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}

.gc-badge--start {
  background: rgb(var(--v-theme-tertiary));
  color: rgb(var(--v-theme-on-tertiary));
}

.gc-badge--unstable {
  color: rgb(var(--v-theme-surface-warning));
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-surface-warning));
}

.gc-bar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin: var(--space-6) 0 var(--space-4);
  max-width: 960px;
}

.gc-count {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gc-tasks {
  display: grid;
  gap: var(--space-2);
  max-width: 960px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.gc-task {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.gc-task--focus {
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-tertiary));
  border-color: rgb(var(--v-theme-tertiary));
}

.gc-num {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.08);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.gc-task-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.gc-task-name {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.gc-task--grey .gc-task-name {
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gc-task-entry {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* "First: <concept>" is a link to what comes first, not a dead button. */
.gc-first {
  flex-shrink: 0;
  padding: 0 var(--space-2);
  height: var(--control-h-sm);
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-sm);
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.gc-first:hover {
  background: var(--hover-wash);
}

.gc-first:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring-inset);
}

.gc-first--inline {
  height: auto;
  padding: 0;
}

.gc-read {
  max-width: 68ch;
}

.gc-read-task {
  margin-bottom: var(--space-6);
}

.gc-read-name {
  margin: 0 0 var(--space-2);
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.gc-read-sentence,
.gc-read-line {
  margin: 0 0 var(--space-2);
  font-size: var(--text-base);
  line-height: var(--leading-body);
}

.gc-read-label {
  display: inline-block;
  min-width: 48px;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gc-read-steps {
  margin: var(--space-2) 0 0;
  padding-left: var(--space-6);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.gc-ref {
  display: block;
  width: 100%;
  height: max(480px, 75vh);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
}

@container (max-width: 720px) {
  .gc-head {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
