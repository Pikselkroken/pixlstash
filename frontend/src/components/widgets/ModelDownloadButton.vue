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
 * `downloadable`: a second fix beside picking a replacement.
 *
 * Says so with a passing notice whose Show opens the Tasks tab, where the
 * download's own bar and cancel are; `useTasksStore` tells how it ended.
 */
import { computed, ref } from "vue";

import { startModelDownload } from "../../api/modelDownloads";
import { useNoticeStore, DEFAULT_TIMEOUTS } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { isReadOnly } from "../../utils/apiClient";
import { errorDetail } from "../../utils/apiError";
import AppButton from "./AppButton.vue";

const props = defineProps({
  /** The missing model as the workflow's loader spells it. */
  file: { type: String, required: true },
});

const tasksStore = useTasksStore();
const starting = ref(false);

const name = computed(() => props.file.split(/[\\/]/).pop());
// By basename: a download's `file` is the name it lands under, never the
// loader's subfolder spelling.
const busy = computed(
  () => starting.value || tasksStore.runningDownloadFiles.has(name.value),
);

async function start() {
  if (busy.value) return;
  starting.value = true;
  const notices = useNoticeStore();
  try {
    const download = await startModelDownload(props.file);
    tasksStore.downloadStarted(download);
    notices.push({
      level: "info",
      text: `Downloading ${name.value}.`,
      action: {
        label: "Show",
        handler: () => useSidebarStore().showTasksTab(),
      },
      // Passing: the Tasks tab keeps the bar, so this need not stay.
      timeout: DEFAULT_TIMEOUTS.info,
      key: `model-download-started-${name.value}`,
    });
  } catch (err) {
    notices.error(errorDetail(err) || `Could not download ${name.value}.`, {
      key: `model-download-refused-${name.value}`,
    });
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
