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
      aria-label="Replace missing models"
      tooltip="Replace the missing models in every workflow that loads them"
      @click="emit('replace')"
    />
  </div>
</template>

<script setup>
/**
 * The verb surface for selected missing-base cards. Emits only, like
 * `WorkflowSetSelectionBar`: the view owns the Replace dialog.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";

const emit = defineEmits(["replace"]);

const store = useModelShelfStore();

const countLabel = computed(() => {
  // Files, not cards: one set can be missing two.
  const n = new Set(store.selectedMissing.flatMap((head) => head.names)).size;
  return n === 1 ? "1 missing model" : `${n} missing models`;
});
</script>
