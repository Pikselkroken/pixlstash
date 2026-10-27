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
          data-testid="mrm-rename"
          @click="store.requestVerb('rename')"
        >
          <Tooltip text="Edit name, type and base model…" activator="parent" />
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
          ref="setsTrigger"
          data-testid="mrm-add-to-set"
          @click="setsOpen = !setsOpen"
        >
          <Tooltip text="Add this model to one of your sets" activator="parent" />
          Add to set…
        </button>
      </template>
      <ul
        v-if="setsOpen"
        ref="setsMenu"
        class="mrm-menu"
        role="menu"
        data-testid="mrm-set-menu"
        @keydown.esc="closeSets"
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
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import {
  baseModelCell,
  fileKindLabel,
  formatModelSize,
  SHELF_TYPES,
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
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** The one model's shelf row. */
  row: { type: Object, required: true },
});

const store = useModelShelfStore();
const userPrefs = useUserPrefsStore();

/** Whether the *In sets* menu is open. */
const setsOpen = ref(false);
const setsTrigger = ref(null);
const setsMenu = ref(null);

// A press anywhere outside the menu and its button closes it, as every other
// menu here does. Focus stays where that press put it.
function onOutsidePress(event) {
  const inside = [setsMenu.value, setsTrigger.value].some((el) =>
    el?.contains(event.target),
  );
  if (!inside) setsOpen.value = false;
}

watch(setsOpen, (open) => {
  if (open) document.addEventListener("pointerdown", onOutsidePress, true);
  else document.removeEventListener("pointerdown", onOutsidePress, true);
});

onBeforeUnmount(() =>
  document.removeEventListener("pointerdown", onOutsidePress, true),
);

// The sidebar's Type word; an engine or unclassified file keeps its own.
const fileKind = computed(
  () =>
    SHELF_TYPES.find((type) => type.key === shelfTypeOf(props.row))?.one ||
    fileKindLabel(props.row.file_kind) ||
    "Other",
);
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

/**
 * Close the set menu and hand focus back to *Add to set…*: the menu's rows
 * leave the document with it, and focus would otherwise drop to <body>.
 */
function closeSets() {
  setsOpen.value = false;
  setsTrigger.value?.focus();
}

/** Add the model to a set, and close the menu: one write, its own receipt. */
async function addToSet(set) {
  closeSets();
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
