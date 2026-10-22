<template>
  <AppDialog
    :open="Boolean(request)"
    title="Add to which folder?"
    size="sm"
    @close="settleModelDropChoice(undefined)"
    @accept="add"
  >
    <p class="mdf-lead">
      The models on this card are not in your default folder. Pick where
      {{ request?.fileLabel }} should go.
    </p>
    <OptionRows
      v-model="choice"
      :options="request?.options ?? []"
      aria-label="Folder"
    />
    <template #footer>
      <AppButton variant="ghost" @click="settleModelDropChoice(undefined)"
        >Cancel</AppButton
      >
      <AppButton variant="primary" :disabled="choice == null" @click="add"
        >Add here</AppButton
      >
    </template>
  </AppDialog>
</template>

<script setup>
// Asked when a model file is dropped on a workflow card whose models live
// somewhere other than the default folder (`useModelDropTarget`). Mounted once,
// in App.vue, because the drop is handled at the window.
import { ref, watch } from "vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import OptionRows from "../widgets/OptionRows.vue";
import {
  modelDropChoice as request,
  settleModelDropChoice,
} from "../../composables/useModelDropTarget";

const choice = ref(null);

// The workflow's own folder is preselected: it is listed first, and it is the
// reason the question is being asked at all.
watch(request, (value) => {
  choice.value = value?.options?.[0]?.id ?? null;
});

function add() {
  if (choice.value != null) settleModelDropChoice(choice.value);
}
</script>

<style scoped>
.mdf-lead {
  margin: 0;
  font-size: var(--text-sm);
}
</style>
