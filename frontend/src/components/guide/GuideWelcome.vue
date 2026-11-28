<template>
  <!-- The Guide's welcome (#2825): a few concepts, one picture and one
       sentence each. Esc, × and Not now all close it for this release; Try it
       closes it and starts that concept's wizard. A slide whose content the
       library lacks offers what comes first instead, as a greyed task does. -->
  <AppDialog
    :open="!!guide.welcome"
    size="lg"
    :title="title"
    :subtitle="`${index + 1} of ${slides.length}`"
    @close="guide.closeWelcome()"
    @accept="isLast ? primary() : next()"
  >
    <div v-if="slide" class="gwl" data-testid="guide-welcome">
      <GuideIllustration
        class="gwl-shot"
        :concept="slide.concept"
        :grey="!!need"
      />
      <h3 class="gwl-name">{{ slide.title }}</h3>
      <p class="gwl-text">{{ slide.text }}</p>
      <p v-if="need" class="gwl-need">
        Needs {{ needLabel }} first.
      </p>
      <div class="gwl-pips" aria-hidden="true">
        <i v-for="(s, i) in slides" :key="s.key" :class="{ on: i === index }"></i>
      </div>
    </div>
    <template #footer>
      <AppButton variant="ghost" data-testid="guide-welcome-not-now" @click="guide.closeWelcome()"
        >Not now</AppButton
      >
      <span class="gwl-sp"></span>
      <AppButton v-if="index > 0" variant="ghost" icon-left="arrow-left" @click="index -= 1"
        >Back</AppButton
      >
      <AppButton v-if="!isLast" variant="secondary" data-testid="guide-welcome-next" @click="next"
        >Next</AppButton
      >
      <AppButton variant="primary" data-testid="guide-welcome-try" @click="primary">{{
        primaryLabel
      }}</AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import GuideIllustration from "./GuideIllustration.vue";
import { NEEDS } from "../../guide/catalogue";
import {
  CONCEPT_BY_ID,
  firstConceptFor,
  useGuideStore,
} from "../../stores/useGuideStore";

const guide = useGuideStore();
const router = useRouter();
const index = ref(0);

/** The task Try it starts: the slide's own, or the first on an empty library. */
function slideTask(concept, empty) {
  const own = concept.tasks.find((t) => t.id === concept.startupTask);
  return (!empty && own) || concept.tasks[0] || null;
}

const slides = computed(() => {
  const w = guide.welcome;
  if (!w) return [];
  const empty = guide.met.picture === false;
  const out = [];
  if (w.withNew)
    out.push({
      key: "new",
      concept: CONCEPT_BY_ID.new,
      title: "New in this release",
      text: CONCEPT_BY_ID.new.summary,
      task: null,
    });
  for (const id of w.concepts) {
    const concept = CONCEPT_BY_ID[id];
    if (!concept) continue;
    const task = slideTask(concept, empty);
    out.push({
      key: id,
      concept,
      title: task && task.id === concept.startupTask ? task.name : concept.name,
      text: task && task.id === concept.startupTask ? task.sentence : concept.summary,
      task,
    });
  }
  return out;
});

const title = computed(() =>
  guide.met.picture === false
    ? "Two ways to start"
    : `${slides.value.length} things to try in PixlStash`,
);

watch(
  () => guide.welcome,
  () => (index.value = 0),
);

const slide = computed(() => slides.value[index.value] ?? null);
const isLast = computed(() => index.value + 1 >= slides.value.length);

/** What the slide still needs: its task's first, else its concept's. */
const need = computed(() => {
  const s = slide.value;
  if (!s?.task) return null;
  return guide.missing(s.task) ?? guide.missing(s.concept);
});
const needLabel = computed(() => NEEDS[need.value]?.label ?? need.value);

const primaryLabel = computed(() => {
  if (!slide.value?.task) return "See what's new";
  if (need.value) return `First: ${firstConceptFor(need.value)?.name ?? needLabel.value}`;
  return "Try it";
});

function next() {
  if (!isLast.value) index.value += 1;
}

async function primary() {
  const s = slide.value;
  if (!s) return;
  const firstKey = need.value;
  await guide.closeWelcome();
  if (!s.task) {
    router.push({ path: "/guide", query: { in: "new" } });
    return;
  }
  if (firstKey) {
    const first = firstConceptFor(firstKey);
    if (first) router.push(`/guide/${first.id}`);
    return;
  }
  guide.startTask(s.concept.id, s.task.id);
}
</script>

<style scoped>
.gwl {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.gwl-shot {
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.gwl-name {
  margin: var(--space-2) 0 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}

.gwl-text {
  margin: 0;
  font-size: var(--text-base);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.gwl-need {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.gwl-pips {
  display: flex;
  justify-content: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.gwl-pips i {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.3);
}

.gwl-pips i.on {
  background: rgb(var(--v-theme-on-surface));
}

.gwl-sp {
  flex: 1;
}
</style>
