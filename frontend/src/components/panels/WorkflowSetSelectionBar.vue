<template>
  <!-- The pill while hand-made SETS are selected (#1520). The same floating
       object as the file pill (`ShelfSelectionBar`), with the set vocabulary:
       Rename and Delete set. No file verb is here, and none of these touches a
       file - which is why Delete set is not in the error colour the file
       Delete wears. -->
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
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";

const emit = defineEmits(["rename", "delete"]);

const store = useModelShelfStore();

const contextOpen = ref(false);
const contextAt = ref([0, 0]);

const countLabel = computed(() => {
  const n = store.selectedSets.length;
  return n === 1 ? "1 set" : `${n} sets`;
});

function clear() {
  store.clearSetSelection();
}

function verb(name) {
  contextOpen.value = false;
  emit(name);
}

/**
 * Open the set menu at a point, for the grid's right-click and Menu key.
 *
 * Opened from code at a point, so v-menu has no activator to hand focus back
 * to and drops it on <body>. The card that asked is remembered and refocused
 * on close, unless a verb moved focus somewhere on purpose (the rename dialog).
 */
let menuTrigger = null;
function openContextMenu(x, y, trigger = document.activeElement) {
  menuTrigger = trigger;
  contextAt.value = [x, y];
  contextOpen.value = true;
}

watch(contextOpen, (open) => {
  if (open || !menuTrigger) return;
  const trigger = menuTrigger;
  menuTrigger = null;
  setTimeout(() => {
    const active = document.activeElement;
    if (!active || active === document.body) trigger.focus?.();
  }, 0);
});

defineExpose({ openContextMenu });
</script>
