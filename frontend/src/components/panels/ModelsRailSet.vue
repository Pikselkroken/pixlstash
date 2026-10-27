<template>
  <div class="mrs-set">
    <ModelsRailSection
      section-key="members"
      title="Models"
      :count="info.members.length"
    >
      <template #end>
        <button
          class="mrs-icon"
          type="button"
          aria-label="Edit set…"
          title="Edit set…"
          data-testid="mrs-edit-set"
          @click="editSet"
        >
          <v-icon size="14" aria-hidden="true">mdi-pencil-outline</v-icon>
        </button>
      </template>
      <div
        v-for="member in info.members"
        :key="member.key"
        class="mrs-row"
        :data-testid="member.onShelf ? undefined : 'mrs-missing-member'"
      >
        <ModelMark
          class="mrs-mark"
          :row="rowOf(member)"
          aria-hidden="true"
        />
        <span
          class="mrs-name"
          :class="{ 'mrs-warn': !member.onShelf }"
          >{{ member.name }}<template v-if="!member.onShelf">
            (not on shelf)</template
          ></span
        >
        <span class="mrs-num num">{{
          member.onShelf ? formatModelSize(member.size) || "–" : "–"
        }}</span>
      </div>
      <p class="mrs-hint">{{ verdict }}</p>
    </ModelsRailSection>

    <ModelsRailSection
      section-key="flows"
      title="Works in"
      :count="info.activity.workflows.length"
    >
      <div
        v-for="flow in info.activity.workflows"
        :key="flow.id"
        class="mrs-row"
      >
        <router-link
          class="mrs-link mrs-name"
          :to="{ name: 'workflows', query: { workflow: flow.id } }"
          >{{ flow.name }}</router-link
        >
        <span class="mrs-num num">{{ pictureWord(flow.picture_count) }}</span>
      </div>
      <p v-if="!info.activity.workflows.length" class="mrs-hint">
        Not run in any workflow yet.
      </p>
    </ModelsRailSection>

    <ModelsRailSection
      section-key="pics"
      title="Latest pictures"
      :count="info.activity.pictures"
    >
      <template v-if="info.activity.workflows.length" #end>
        <button
          class="mrs-link-btn"
          type="button"
          data-testid="mrs-show-all"
          @click="showAll"
        >
          Show all
        </button>
      </template>
      <ModelsRailThumbs
        v-if="info.activity.latest.length"
        :pictures="info.activity.latest"
      />
      <p v-else class="mrs-hint">Nothing made with it yet.</p>
    </ModelsRailSection>

    <ModelsRailSection section-key="about" title="About">
      <dl class="mrs-facts">
        <div class="mrs-fact">
          <dt>Base</dt>
          <dd>{{ info.base }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>On disk</dt>
          <dd>{{ formatModelSize(info.size) || "–" }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Made by</dt>
          <dd>{{ info.madeBy }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Last used</dt>
          <dd>{{ info.lastUsed }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Recipes</dt>
          <dd>{{ info.recipes.toLocaleString() }}</dd>
        </div>
        <div v-if="info.runs" class="mrs-fact">
          <dt>ComfyUI runs</dt>
          <dd>{{ info.runs.toLocaleString() }}</dd>
        </div>
      </dl>
    </ModelsRailSection>
  </div>
</template>

<script setup>
/**
 * One set, in the Models rail's inspector (#2168): what it holds and whether
 * it runs, the workflows that load it, its newest pictures and what it is.
 *
 * The one set the rail names is the set the grid's card, its tray and the
 * *Edit set…* pencil all point at, so it is read from the same store groups.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { useWorkflowPictures } from "../../composables/useWorkflowPictures";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { formatModelSize } from "../../utils/modelShelf";
import { describeSet, setVerdict } from "../../utils/modelsInspector";
import ModelMark from "../widgets/ModelMark.vue";
import ModelsRailSection from "./ModelsRailSection.vue";
import ModelsRailThumbs from "./ModelsRailThumbs.vue";

const props = defineProps({
  /** A `setGroups` entry, or a `handMadeGroups` entry (`set` set). */
  group: { type: Object, required: true },
});

const store = useModelShelfStore();
const { showWorkflowsPictures } = useWorkflowPictures();

const rowsById = computed(() => new Map(store.rows.map((row) => [row.id, row])));

const info = computed(() =>
  describeSet(props.group, {
    rowsById: rowsById.value,
    combinations: store.workflowSets.combinations ?? [],
  }),
);

const verdict = computed(() => setVerdict(info.value.missing));

function rowOf(member) {
  return rowsById.value.get(member.id) ?? { filename: member.name };
}

function pictureWord(count) {
  return `${Number(count).toLocaleString()} ${count === 1 ? "picture" : "pictures"}`;
}

/** Open the set's tray in the grid, the way its card does. */
function editSet() {
  if (store.openSetKey !== info.value.key) store.toggleSet(info.value.key);
}

function showAll() {
  showWorkflowsPictures(info.value.activity.workflows);
}
</script>

<style scoped>
.mrs-set {
  display: flex;
  flex-direction: column;
}

.mrs-mark {
  flex-shrink: 0;
}
</style>
