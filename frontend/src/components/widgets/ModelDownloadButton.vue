<template>
  <AppButton
    v-if="!isReadOnly"
    class="mdl-btn"
    icon-only
    size="sm"
    variant="ghost"
    icon-left="download"
    :loading="busy"
    :tooltip="busy ? `Downloading ${name}…` : `Download ${name}`"
    :aria-label="busy ? `Downloading ${name}` : `Download ${name}`"
    data-testid="model-download"
    @click="start"
  />
</template>

<script setup>
/**
 * Download one missing model onto the shelf (`POST /model-downloads`).
 *
 * Shown beside a missing model's name only where the server marked it
 * `downloadable`: a second fix beside picking a replacement. What it says is
 * `downloadModels`'.
 */
import { computed, ref } from "vue";

import {
  downloadModels,
  modelBaseName,
} from "../../composables/useModelDownloads";
import { useTasksStore } from "../../stores/useTasksStore";
import { isReadOnly } from "../../utils/apiClient";
import AppButton from "./AppButton.vue";

const props = defineProps({
  /** The missing model as the workflow's loader spells it. */
  file: { type: String, required: true },
});

const tasksStore = useTasksStore();
const starting = ref(false);

const name = computed(() => modelBaseName(props.file));
const busy = computed(
  () => starting.value || tasksStore.runningDownloadFiles.has(name.value),
);

async function start() {
  if (busy.value) return;
  starting.value = true;
  try {
    await downloadModels([props.file]);
  } finally {
    starting.value = false;
  }
}
</script>

<style scoped>
/* It sits in a line of text, after the file's name. */
.mdl-btn {
  vertical-align: middle;
  margin-left: var(--space-2);
}
</style>
