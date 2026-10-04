<template>
  <!-- The pill while cards named after a MISSING base model are selected. The
       same floating object as the other two pills; its one verb replaces the
       missing file in the workflows that load it. No file on the shelf is
       touched, so nothing here wears the error colour. -->
  <div
    v-if="store.selectedMissing.length"
    class="selbar"
    role="toolbar"
    aria-label="Selected sets with a missing model"
  >
    <button class="selbar-count" type="button" @click="store.clearMissingSelection()">
      <Tooltip text="Clear the selection (Esc)" activator="parent" />
      <v-icon size="16">mdi-file-alert-outline</v-icon>
      <span>{{ countLabel }}</span>
      <v-icon size="16" class="selbar-chevron">mdi-close</v-icon>
    </button>
    <span class="selbar-sep"></span>
    <AppBarButton
      shape="round"
      icon="file-replace-outline"
      data-verb="replace-missing"
      aria-label="Replace"
      tooltip="Replace in every workflow that loads it"
      @click="emit('replace')"
    />
  </div>

  <!-- The card's context menu: right-click, the Menu key or Shift+F10 on a
       missing-base card. The full inventory, which the pill is a shortcut into. -->
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
        class="ctx-item"
        type="button"
        role="menuitem"
        data-verb="replace-missing"
        @click="verb('replace')"
      >
        <v-icon class="ctx-icon">mdi-file-replace-outline</v-icon>
        <span class="ctx-label-text">Replace…</span>
      </button>
    </div>
  </v-menu>
</template>

<script setup>
/**
 * The verb surface for selected missing-base cards. Emits only, like
 * `WorkflowSetSelectionBar`: the view owns the Replace dialog.
 */
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";

const emit = defineEmits(["replace"]);

const store = useModelShelfStore();

const contextOpen = ref(false);
const contextAt = ref([0, 0]);

const countLabel = computed(() => {
  // Files, not cards: one set can be missing two.
  const n = new Set(store.selectedMissing.flatMap((head) => head.names)).size;
  return n === 1 ? "1 missing model" : `${n} missing models`;
});

function verb(name) {
  contextOpen.value = false;
  emit(name);
}

/**
 * Open the card menu at a point, for the grid's right-click and Menu key.
 *
 * Opened from code, so v-menu has no activator to hand focus back to and
 * drops it on <body>. The card that asked is refocused on close, unless a
 * verb moved focus on purpose (the Replace dialog).
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
