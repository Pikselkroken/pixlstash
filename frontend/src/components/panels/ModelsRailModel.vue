<template>
  <div class="mrm">
    <ModelsRailSection
      section-key="file"
      title="File"
      :count="fileKind"
    >
      <template #end>
        <button
          class="mrs-icon"
          type="button"
          aria-label="Edit name, type and base model…"
          title="Edit name, type and base model…"
          data-testid="mrm-rename"
          @click="store.requestVerb('rename')"
        >
          <v-icon size="14" aria-hidden="true">mdi-pencil-outline</v-icon>
        </button>
      </template>
      <dl class="mrs-facts">
        <div class="mrs-fact">
          <dt>Type</dt>
          <dd>{{ fileKind }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Base</dt>
          <dd>{{ base }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Size</dt>
          <dd>{{ formatModelSize(row.file_size) || "–" }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Folder</dt>
          <dd class="mrm-path" :title="folder">{{ folder || "–" }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Added</dt>
          <dd>{{ added || "–" }}</dd>
        </div>
        <div v-if="trigger" class="mrs-fact mrm-trigger">
          <dt>Trigger word</dt>
          <dd class="mrm-word">{{ trigger }}</dd>
          <button
            class="mrs-link-btn"
            type="button"
            data-testid="mrm-copy-trigger"
            @click="copyTrigger"
          >
            Copy
          </button>
        </div>
      </dl>
    </ModelsRailSection>

    <ModelsRailSection
      section-key="insets"
      title="In sets"
      :count="setNames.length"
    >
      <template #end>
        <button
          class="mrs-link-btn"
          type="button"
          aria-haspopup="menu"
          :aria-expanded="setsOpen ? 'true' : 'false'"
          title="Add this model to one of your sets"
          data-testid="mrm-add-to-set"
          @click="setsOpen = !setsOpen"
        >
          Add to set…
        </button>
      </template>
      <ul
        v-if="setsOpen"
        class="mrm-menu"
        role="menu"
        data-testid="mrm-set-menu"
        @keydown.esc="setsOpen = false"
      >
        <li v-for="set in store.shownHandMadeSets" :key="set.id" role="none">
          <button
            class="mrm-menu-item"
            type="button"
            role="menuitem"
            :disabled="!row.sha256 || Boolean(refusal(set))"
            @click="addToSet(set)"
          >
            {{ handMadeName(set) }}
            <span v-if="refusal(set)" class="mrs-hint">{{ refusal(set) }}</span>
          </button>
        </li>
        <li v-if="!store.shownHandMadeSets.length" class="mrs-hint" role="none">
          No set of your own yet.
        </li>
      </ul>
      <div v-for="name in setNames" :key="name" class="mrs-row">
        <span class="mrs-name">{{ name }}</span>
      </div>
      <p v-if="!setNames.length" class="mrs-hint">In no set yet.</p>
    </ModelsRailSection>

    <ModelsRailSection
      v-if="isTrainedLora"
      section-key="train"
      title="Training"
    >
      <ShelfRunLoss :run-id="row.training_run_id" />
    </ModelsRailSection>

    <ModelsRailSection
      section-key="pics"
      title="Latest pictures"
      :count="activity.pictures"
    >
      <ModelsRailThumbs
        v-if="activity.latest.length"
        :pictures="activity.latest"
      />
      <p v-else class="mrs-hint">Nothing made with it yet.</p>
    </ModelsRailSection>
  </div>
</template>

<script setup>
/**
 * One model file, in the Models rail's inspector (#2168): what the file is,
 * the sets it is in, its training for a trained LoRA, and the pictures made
 * with it. Its pictures are not offered as *Show all*: the shelf can filter by
 * a set or a workflow, not by one model.
 */
import { computed, ref } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import {
  baseModelCell,
  fileKindLabel,
  formatModelSize,
  shelfTypeOf,
} from "../../utils/modelShelf";
import { formatUserDate } from "../../utils/utils";
import { handMadeName, defaultSlot } from "../../utils/workflowSets";
import { activityOf, combinationsOfModel } from "../../utils/setActivity";
import { modelFolder } from "../../utils/modelsInspector";
import { triggerWord } from "../../utils/triggerWords";
import ShelfRunLoss from "../views/ShelfRunLoss.vue";
import ModelsRailSection from "./ModelsRailSection.vue";
import ModelsRailThumbs from "./ModelsRailThumbs.vue";

const props = defineProps({
  /** The one model's shelf row. */
  row: { type: Object, required: true },
});

const store = useModelShelfStore();
const userPrefs = useUserPrefsStore();

/** Whether the *In sets* menu is open. */
const setsOpen = ref(false);

const fileKind = computed(() => fileKindLabel(props.row.file_kind));
const base = computed(() => baseModelCell(props.row).text || "–");
const folder = computed(() => modelFolder(props.row));
const added = computed(() =>
  props.row.added_at ? formatUserDate(props.row.added_at, userPrefs.dateFormat) : "",
);
const trigger = computed(() => triggerWord(props.row));
const isTrainedLora = computed(
  () => shelfTypeOf(props.row) === "lora" && Boolean(props.row.training_run_id),
);

const setNames = computed(() =>
  (store.setMembership.get(props.row.id) ?? []).map((set) => set.name),
);

const activity = computed(() =>
  activityOf(
    combinationsOfModel(props.row.id, store.workflowSets.combinations ?? []),
  ),
);

/** Why a set will not take this model (already in it, another base), or "". */
function refusal(set) {
  return store.setAddRefusal(set, [props.row]);
}

/** Add the model to a set, and close the menu: one write, its own receipt. */
async function addToSet(set) {
  setsOpen.value = false;
  const members = [{ model_id: props.row.id, slot: defaultSlot(props.row.file_kind) }];
  if (!(await store.confirmSecondCheckpoint(set, members))) return;
  await store.addToHandMadeSet(set, members);
}

async function copyTrigger() {
  try {
    await navigator.clipboard.writeText(trigger.value);
  } catch (error) {
    console.warn(
      `Could not copy the trigger word for model ${props.row.id} to the clipboard`,
      error,
    );
  }
}
</script>

<style scoped>
.mrm {
  display: flex;
  flex-direction: column;
}

.mrm-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mrm-word {
  font-family: var(--font-mono);
}

.mrm-trigger {
  align-items: center;
}

.mrm-menu {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: var(--space-1);
  list-style: none;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  box-shadow: var(--elevation-3);
}

.mrm-menu-item {
  height: 32px;
  padding: 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-xs);
  text-align: left;
  cursor: pointer;
}

.mrm-menu-item:hover:not(:disabled),
.mrm-menu-item:focus-visible {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.mrm-menu-item:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}
</style>
