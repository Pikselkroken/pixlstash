<template>
  <AppDialog
    :open="Boolean(model)"
    :title="model?.name || 'Works with'"
    :subtitle="subtitle"
    size="md"
    @close="emit('close')"
  >
    <p v-if="store.setsLoading" class="ww__state">Reading the recipes…</p>
    <p v-else-if="store.setsError" class="ww__state" role="alert">
      {{ store.setsError }}
    </p>

    <template v-else>
      <!-- Which sets it is in, above the companions: a model in seventeen sets
           reads as a number and a short list rather than seventeen chips. -->
      <template v-if="answer.sets.length">
        <h3 class="ww__section">
          {{ setsLabel }}
        </h3>
        <ChipRow :items="setChips" class="ww__sets" />
      </template>

      <h3 class="ww__section">
        Works with
        <span class="ww__count num">{{ recipeLabel }}</span>
      </h3>

      <ul v-if="answer.companions.length" class="ww__list">
        <li
          v-for="companion in shown"
          :key="companion.id"
          class="ww__companion"
        >
          <span class="ww__names">
            <span class="ww__name">{{ companion.name }}</span>
            <span v-if="companion.kindLabel" class="ww__kind">{{
              companion.kindLabel
            }}</span>
            <!-- Same three causes, same one sentence: see `ModelComboCard`. -->
            <Tooltip v-if="companion.ambiguous" :text="UNSURE_REASON">
              <template #activator="{ props: tipProps }">
                <v-icon v-bind="tipProps" size="14" class="ww__warn"
                  >mdi-alert-outline</v-icon
                >
              </template>
            </Tooltip>
          </span>
          <!-- The bar is the ranking and the number is the evidence: sorted by
               recipe count, which is the only thing co-occurrence measures.
               `aria-hidden` on the bar because the figures beside it say the
               same thing in words. -->
          <span class="ww__evidence">
            <span class="ww__bar" aria-hidden="true">
              <span :style="{ width: `${companion.share}%` }"></span>
            </span>
            <span class="ww__figures num"
              >{{ recipeCount(companion.recipes) }} ·
              {{ companion.pictures.toLocaleString() }}
              {{ companion.pictures === 1 ? "picture" : "pictures" }}</span
            >
          </span>
        </li>
      </ul>
      <p v-else class="ww__state">
        No recipe in this library names this file beside another one. That is
        not a verdict on what it works with — only a record of what has been
        tried here.
      </p>

      <AppButton
        v-if="answer.companions.length > shown.length"
        class="ww__more"
        variant="outline"
        size="sm"
        @click="expanded = true"
        >Show all {{ answer.companions.length }} companions</AppButton
      >

      <!-- Not decoration. Without this line a short companion list reads as a
           compatibility verdict, which is exactly the claim this screen must
           not make. -->
      <p v-if="answer.companions.length" class="ww__notice">
        <v-icon size="16">mdi-information-outline</v-icon>
        <span
          >Nothing is ruled out here. A model missing from this list has simply
          never been in the same picture’s recipe — it may work perfectly.</span
        >
      </p>
    </template>
  </AppDialog>
</template>

<script setup>
// "Works with" for one model (#1438): the companions it has been seen beside,
// ranked by how many recipes back each one.
//
// **A dialog rather than an inspector section.** The approved design draws this
// inside a model inspector with `Model` and `Copies` tabs, and the shelf has no
// inspector: it is a row list with a selection bar, so there is no panel for a
// section to live in. Building one would be a new surface the design set never
// proposed and the issue never asked for. The content is the design's, verbatim
// — the sets above, the companions ranked with a bar and a number, the closing
// notice — in the container this screen actually has.
//
// Reached from the row list's own context menu and from a file line inside an
// open set in the grid, so it answers wherever a reader is looking at a model.

import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { formatModelSize } from "../../utils/modelShelf";
import { recipeCount, setName } from "../../utils/workflowSets";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import ChipRow from "../widgets/ChipRow.vue";
import Tooltip from "../widgets/Tooltip.vue";

/** Why a companion is drawn as uncertain; all three causes, one sentence. */
const UNSURE_REASON =
  "The recipe does not pin this down to one file on the shelf — a basename several rows answer to, a short digest that matches more than one, or a model still waiting for its hash. It is listed because it is the best answer the evidence gives, not hidden because it is not the only one.";

/** How many companions are drawn before *Show all* is offered. */
const FIRST_FEW = 4;

/** How many set chips are drawn; ChipRow clips the rest to "+N". */
const SET_CHIPS = 6;

const props = defineProps({
  /**
   * The model to answer for: `{id, name, kind, file_size}` is enough, so a
   * shelf row and a set member both serve. Null closes the dialog.
   */
  model: { type: Object, default: null },
});

const emit = defineEmits(["close"]);

const store = useModelShelfStore();
const expanded = ref(false);

// Asked for here rather than by the opener, so every entry point gets the same
// answer and none of them has to remember to load first. Once per session: the
// store's own guard drops the repeat.
watch(
  () => props.model?.id,
  (id) => {
    expanded.value = false;
    if (id != null) store.loadWorkflowSets();
  },
  { immediate: true },
);

const answer = computed(() =>
  props.model
    ? store.worksWithModel(props.model.id)
    : { companions: [], recipes: 0, sets: [] },
);

const shown = computed(() =>
  expanded.value
    ? answer.value.companions
    : answer.value.companions.slice(0, FIRST_FEW),
);

const subtitle = computed(() => {
  const parts = [
    props.model?.kindLabel || "",
    formatModelSize(props.model?.file_size),
  ];
  return parts.filter(Boolean).join(" · ");
});

const setsLabel = computed(() => {
  const n = answer.value.sets.length;
  return n === 1 ? "In 1 set" : `In ${n} sets`;
});

const recipeLabel = computed(() => recipeCount(answer.value.recipes));

const setChips = computed(() =>
  answer.value.sets.slice(0, SET_CHIPS).map((combination) => ({
    key: combination.key,
    label: setName(combination),
    icon: "cube-outline",
  })),
);
</script>

<style scoped>
.ww__section {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: var(--space-5) 0 var(--space-3);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.ww__section:first-child {
  margin-top: 0;
}

.ww__count {
  font-weight: var(--weight-regular);
  letter-spacing: normal;
  text-transform: none;
}

.ww__sets {
  margin-bottom: var(--space-3);
}

.ww__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ww__companion {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.ww__names {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.ww__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.ww__kind {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  flex-shrink: 0;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
  white-space: nowrap;
}

.ww__warn {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

.ww__evidence {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

/* The shipped meter trough: one filled track, not a stack of segments. The
   share is against the STRONGEST pairing rather than the total, so the top
   companion is always full and the rest are read against it. */
.ww__bar {
  flex: 1;
  min-width: 36px;
  height: 4px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--track-trough);
}

.ww__bar > span {
  display: block;
  height: 100%;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
}

.ww__figures {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.ww__more {
  margin-top: var(--space-4);
}

.ww__state {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.ww__notice {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: var(--space-5) 0 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-surface-info), 0.14);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
}

.ww__notice .v-icon {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-info));
}
</style>
