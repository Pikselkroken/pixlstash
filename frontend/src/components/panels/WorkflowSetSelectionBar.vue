<template>
  <!-- The pill while hand-made SETS are selected (#1520). The same floating
       object as the file pill (`ShelfSelectionBar`), with the set vocabulary:
       Rename, Duplicate set, Merge, Hide and Delete set. Hide sits before
       Delete so the one destructive verb stays last. No file verb is here, and none of
       these touches a file - which is why Delete set is not in the error
       colour the file Delete wears. -->
  <div
    v-if="store.selectedSets.length"
    class="selbar"
    role="toolbar"
    aria-label="Selected workflow sets"
  >
    <button class="selbar-count" type="button" @click="clear">
      <Tooltip text="Clear the selection (Esc)" activator="parent" />
      <v-icon size="16">mdi-layers-outline</v-icon>
      <span>{{ countLabel }}</span>
      <v-icon size="16" class="selbar-chevron">mdi-close</v-icon>
    </button>
    <span class="selbar-sep"></span>
    <AppBarButton
      shape="round"
      icon="pencil-outline"
      data-verb="rename-set"
      aria-label="Rename"
      :disabled="store.selectedSets.length !== 1"
      :tooltip="
        store.selectedSets.length === 1
          ? 'Rename (F2)'
          : 'Rename one set at a time.'
      "
      @click="emit('rename')"
    />
    <AppBarButton
      shape="round"
      icon="content-duplicate"
      data-verb="duplicate-set"
      aria-label="Duplicate set"
      :disabled="store.selectedSets.length !== 1"
      :tooltip="
        store.selectedSets.length === 1
          ? 'Duplicate set: a copy with the same models. No file is touched.'
          : 'Duplicate one set at a time.'
      "
      @click="emit('duplicate')"
    />
    <!-- Merge (#1972): live only when every selected set is on the same
         checkpoint, and otherwise in its place, disabled, saying why. -->
    <AppBarButton
      v-if="merge"
      shape="round"
      icon="call-merge"
      data-verb="merge-sets"
      :aria-label="merge.label"
      :disabled="!merge.enabled"
      :tooltip="`${merge.label}. ${merge.reason}`"
      @click="emit('merge', merge)"
    />
    <AppBarButton
      v-if="hide"
      shape="round"
      :icon="hide.icon.replace('mdi-', '')"
      data-verb="hide-sets"
      :aria-label="hide.label"
      :tooltip="hide.label"
      @click="emit('hide-sets')"
    />
    <AppBarButton
      shape="round"
      icon="layers-remove"
      data-verb="delete-set"
      aria-label="Delete set"
      tooltip="Delete set (Del). No file is touched, and it can be undone."
      @click="emit('delete')"
    />
  </div>

  <!-- The set's context menu: right-click, the Menu key or Shift+F10 on a
       hand-made card. -->
  <v-menu
    v-model="contextOpen"
    :target="contextAt"
    location="bottom end"
    origin="top start"
    :offset="2"
  >
    <div
      class="ctx-menu shelf-menu"
      role="menu"
      tabindex="-1"
      @keydown="onMenuKeydown"
    >
      <button
        v-if="store.selectedSets.length === 1"
        class="ctx-item"
        type="button"
        role="menuitem"
        @click="verb('rename')"
      >
        <v-icon class="ctx-icon">mdi-pencil-outline</v-icon>
        <span class="ctx-label-text">Rename</span>
        <span class="ctx-shortcut">F2</span>
      </button>
      <button
        v-if="store.selectedSets.length === 1"
        class="ctx-item"
        type="button"
        role="menuitem"
        @click="verb('duplicate')"
      >
        <v-icon class="ctx-icon">mdi-content-duplicate</v-icon>
        <span class="ctx-label-text">Duplicate set</span>
      </button>
      <button
        v-if="hide"
        class="ctx-item"
        type="button"
        role="menuitem"
        data-verb="hide-sets"
        @click="verb('hide-sets')"
      >
        <v-icon class="ctx-icon">{{ hide.icon }}</v-icon>
        <span class="ctx-label-text">{{ hide.label }}</span>
      </button>
      <!-- Merge (#1972): several selected sets, or one set and each other
           set on its checkpoint. Disabled with the reason when it cannot. -->
      <button
        v-if="merge"
        class="ctx-item"
        :class="{ 'ctx-item--disabled': !merge.enabled }"
        type="button"
        role="menuitem"
        data-verb="merge-sets"
        :disabled="!merge.enabled"
        @click="verb('merge', merge)"
      >
        <Tooltip :text="merge.reason" activator="parent" />
        <v-icon class="ctx-icon">mdi-call-merge</v-icon>
        <span class="ctx-label-text">{{ merge.label }}</span>
      </button>
      <template v-else-if="one">
        <button
          v-for="partner in partners"
          :key="partner.id"
          class="ctx-item"
          type="button"
          role="menuitem"
          data-verb="merge-sets"
          @click="
            verb('merge', { into: one, others: [partner] })
          "
        >
          <Tooltip
            :text="`Merges “${handMadeName(partner)}” into this set. No file is touched.`"
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-call-merge</v-icon>
          <span class="ctx-label-text"
            >Merge with “{{ handMadeName(partner) }}”</span
          >
        </button>
        <button
          v-if="!partners.length"
          class="ctx-item ctx-item--disabled"
          type="button"
          role="menuitem"
          disabled
        >
          <v-icon class="ctx-icon">mdi-call-merge</v-icon>
          <span class="ctx-label-text">{{ noPartner }}</span>
        </button>
      </template>
      <!-- The template offer (#1999), for the one set the menu is about. -->
      <template v-if="offered">
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          @click="verb('merge-offer')"
        >
          <v-icon class="ctx-icon">mdi-plus</v-icon>
          <span class="ctx-label-text">Review models to add…</span>
        </button>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          @click="verb('keep-separate')"
        >
          <v-icon class="ctx-icon">mdi-call-split</v-icon>
          <span class="ctx-label-text">Keep separate</span>
        </button>
      </template>
      <button
        v-if="one && changed"
        class="ctx-item"
        type="button"
        role="menuitem"
        data-verb="reset-set"
        @click="verb('reset')"
      >
        <v-icon class="ctx-icon">mdi-backup-restore</v-icon>
        <span class="ctx-label-text">Reset to automatic</span>
      </button>
      <button
        class="ctx-item"
        type="button"
        role="menuitem"
        @click="verb('delete')"
      >
        <v-icon class="ctx-icon">mdi-layers-remove</v-icon>
        <span class="ctx-label-text">{{
          store.selectedSets.length === 1 ? "Delete set" : "Delete sets"
        }}</span>
        <span class="ctx-shortcut">Del</span>
      </button>
    </div>
  </v-menu>
</template>

<script setup>
/**
 * The verb surface for selected hand-made workflow sets (#1520).
 *
 * Emits and runs nothing itself, like `ShelfSelectionBar`: the view owns the
 * rename dialog and the store owns the writes and their receipts.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { useContextMenuAt } from "../../composables/useContextMenuAt";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import {
  handMadeName,
  hideSetsVerb,
  mergePartners,
  mergeVerb,
  canReset,
  setCheckpoint,
} from "../../utils/workflowSets";
import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** Every selected card from pictures and hand-made sets: what Hide takes. */
  sets: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "rename",
  "duplicate",
  "delete",
  "hide-sets",
  // `{into, others}`: the set that stays and the ones merged into it.
  "merge",
  "reset",
  // The template offer (#1999).
  "merge-offer",
  "keep-separate",
]);

const store = useModelShelfStore();

/** Hide over the selected cards, as the pill's button and the menu's row. */
const hide = computed(() => hideSetsVerb(props.sets));

const { contextOpen, contextAt, openContextMenu } = useContextMenuAt();

/**
 * "2 sets · Lumen XL v4" when every selected set is on one checkpoint, so
 * the pill says Merge is live before it is pressed; "2 sets · 2 checkpoints"
 * otherwise.
 */
const countLabel = computed(() => {
  const sets = store.selectedSets;
  const n = sets.length;
  const count = n === 1 ? "1 set" : `${n} sets`;
  if (n < 2) return count;
  const names = new Set(
    sets.map((set) => setCheckpoint(set)?.sha256 ?? "").filter(Boolean),
  );
  if (merge.value?.enabled) {
    return `${count} · ${setCheckpoint(store.mergeTarget)?.name ?? ""}`;
  }
  return names.size > 1 ? `${count} · ${names.size} checkpoints` : count;
});

/** The one set the menu is about, or null with several selected. */
const one = computed(() =>
  store.selectedSets.length === 1 ? store.selectedSets[0] : null,
);

/** Merge over several selected sets, into the one last clicked. */
const merge = computed(() =>
  mergeVerb(store.selectedSets, store.mergeTarget),
);

/** The other sets on the one selected set's checkpoint. */
const partners = computed(() =>
  one.value ? mergePartners(one.value, store.shownHandMadeSets) : [],
);

const noPartner = computed(() => {
  const checkpoint = setCheckpoint(one.value);
  return checkpoint
    ? `No other set uses ${checkpoint.name}`
    : "Merge needs a checkpoint";
});

/** The one selected set carries a template offer (#1999). */
const offered = computed(() => Boolean(one.value?.offer));

/** The one selected set carries changes Reset to automatic would undo. */
const changed = computed(() => Boolean(one.value) && canReset(one.value));

function clear() {
  store.clearSetSelection();
}

function verb(name, payload) {
  contextOpen.value = false;
  emit(name, payload);
}

defineExpose({ openContextMenu });
</script>
