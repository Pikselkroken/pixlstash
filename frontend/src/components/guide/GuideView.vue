<template>
  <!-- The Guide (#2825): a place in the shell, like the library itself. Its
       toolbar holds the search; the body is either the A–Z grid, with Start
       here pinned above it, or one concept opened in place. -->
  <div class="gv" data-testid="guide-view">
    <div class="gv-toolbar">
      <nav class="gv-crumb" aria-label="Guide">
        <button
          v-if="concept"
          type="button"
          class="gv-crumb-link"
          @click="router.push({ path: '/guide', query: route.query })"
        >
          Guide
        </button>
        <span v-else class="gv-title">Guide</span>
        <template v-if="concept">
          <v-icon size="16" class="gv-crumb-sep" aria-hidden="true"
            >mdi-chevron-right</v-icon
          >
          <span class="gv-title" aria-current="page">{{ concept.name }}</span>
        </template>
      </nav>
      <span class="gv-sub"
        >{{ GUIDE_CONCEPTS.length }} concepts · {{ TASK_COUNT }} tasks</span
      >
      <span class="gv-spacer"></span>
      <NameFilterField
        ref="searchRef"
        v-model="guide.query"
        class="gv-search"
        placeholder="Search the guide"
        label="Search the guide"
        data-testid="guide-search"
        @keydown.enter="onSearchEnter"
      />
    </div>

    <GuideConcept v-if="concept" :concept="concept" />

    <div v-else class="gv-scroll">
      <template v-if="!searching && browse === 'all'">
        <section class="gv-section" aria-labelledby="gv-start">
          <h2 id="gv-start" class="gv-heading">
            Start here
            <span v-if="startNote" class="gv-heading-note">· {{ startNote }}</span>
          </h2>
          <div class="gv-grid">
            <GuideConceptCard
              v-for="c in pinned"
              :key="c.id"
              :concept="c"
              @open="open"
            />
          </div>
        </section>
        <h2 class="gv-heading">All concepts · A–Z</h2>
      </template>
      <template v-else-if="browse === 'new' && !searching">
        <section class="gv-section" aria-labelledby="gv-new">
          <h2 id="gv-new" class="gv-heading">New in 2.x</h2>
          <p class="gv-lede">
            What arrived in this release. Each headline opens the concept that
            owns it.
          </p>
          <ul class="gv-headlines">
            <li v-for="h in HEADLINES" :key="h.id">
              <button
                type="button"
                class="gv-headline"
                :data-testid="`guide-headline-${h.id}`"
                @click="openHeadline(h)"
              >
                <span class="gv-headline-title">{{ h.title }}</span>
                <span v-if="h.unstable" class="gv-unstable">Unstable</span>
                <span class="gv-headline-where"
                  >{{ conceptName(h.concept) }} ·
                  {{ h.release === "next" ? "coming" : h.release }}</span
                >
              </button>
            </li>
          </ul>
        </section>
      </template>
      <div v-if="shown.length" class="gv-grid">
        <GuideConceptCard
          v-for="c in shown"
          :key="c.id"
          :concept="c"
          @open="open"
        />
      </div>
      <p
        v-else-if="browse !== 'new' || searching"
        class="gv-empty"
        data-testid="guide-empty"
      >
        Nothing in the Guide matches “{{ guide.query }}”.
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon } from "vuetify/components";
import GuideConcept from "./GuideConcept.vue";
import GuideConceptCard from "./GuideConceptCard.vue";
import NameFilterField from "../widgets/NameFilterField.vue";
import { HEADLINES } from "../../guide/catalogue";
import {
  browseOf,
  conceptsIn,
  GUIDE_CONCEPTS,
  matches,
  TASK_COUNT,
} from "../../guide/browse";
import { CONCEPT_BY_ID, useGuideStore } from "../../stores/useGuideStore";

const route = useRoute();
const router = useRouter();
const guide = useGuideStore();
const searchRef = ref(null);

const concept = computed(() => CONCEPT_BY_ID[route.params.concept] ?? null);
const browse = computed(() => browseOf(route));
const searching = computed(() => guide.query.trim() !== "");

/** New in 2.x always, then the library's Start-here concepts. */
const pinned = computed(() => [
  CONCEPT_BY_ID.new,
  ...guide.startHere.map((id) => CONCEPT_BY_ID[id]).filter(Boolean),
]);

const START_NOTES = {
  "import-pictures": "picked for an empty library",
  people: "picked for a library with pictures and no people yet",
  "consistent-characters": "picked for a library with people in it",
};
const startNote = computed(() => START_NOTES[guide.startHere[0]] ?? "");

const shown = computed(() => {
  // A search covers every concept, whatever row the sidebar is on.
  if (searching.value)
    return [CONCEPT_BY_ID.new, ...GUIDE_CONCEPTS].filter((c) =>
      matches(c, guide.query),
    );
  if (browse.value === "new") return [];
  return conceptsIn(browse.value, guide.startHere);
});

const conceptName = (id) => CONCEPT_BY_ID[id]?.name ?? id;

function open(c) {
  if (c.id === "new") {
    router.push({ path: "/guide", query: { in: "new" } });
    return;
  }
  router.push({ path: `/guide/${c.id}`, query: route.query });
}

function openHeadline(h) {
  router.push({
    path: `/guide/${h.concept}`,
    query: { ...route.query, task: h.task },
  });
}

/** Enter in the field opens the one concept a search leaves. */
function onSearchEnter() {
  if (!concept.value && shown.value.length === 1) open(shown.value[0]);
}

// Re-read the library each time the Guide is opened: a picture imported or a
// person made since changes what is greyed.
onMounted(() => guide.refresh());
watch(
  () => route.params.concept,
  () => document.querySelector(".gv-scroll, .gc-scroll")?.scrollTo?.(0, 0),
);

defineExpose({ focusSearch: () => searchRef.value?.focus() });
</script>

<style scoped>
.gv {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: rgb(var(--v-theme-background));
}

.gv-toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--toolbar-height);
  padding: 0 var(--space-3) 0 var(--space-5);
  background: rgb(var(--v-theme-toolbar));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  position: relative;
  z-index: var(--z-sticky);
}

.gv-crumb {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
}

.gv-title,
.gv-crumb-link {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gv-crumb-link {
  padding: 0 var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-family: inherit;
  cursor: pointer;
}

.gv-crumb-link:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.gv-crumb-link:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring-inset);
}

.gv-crumb-sep {
  opacity: var(--opacity-text-secondary);
}

.gv-sub {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.gv-spacer {
  flex: 1;
}

.gv-search {
  width: 280px;
}

.gv-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-5) var(--space-6) var(--space-8);
}

.gv-section {
  margin-bottom: var(--space-6);
}

.gv-heading {
  margin: 0 0 var(--space-4);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gv-heading-note {
  text-transform: none;
  letter-spacing: 0;
  font-weight: var(--weight-regular);
}

.gv-lede {
  margin: 0 0 var(--space-4);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gv-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-5);
  align-content: start;
}

.gv-headlines {
  display: grid;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  max-width: 720px;
}

.gv-headline {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.gv-headline:hover {
  border-color: rgba(var(--v-theme-on-surface), 0.4);
}

.gv-headline:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring-inset);
}

.gv-headline-title {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.gv-headline-where {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  white-space: nowrap;
}

.gv-unstable {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-surface-warning));
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-surface-warning));
}

.gv-empty {
  margin: var(--space-6) 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
