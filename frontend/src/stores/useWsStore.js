import { ref } from "vue";
import { defineStore } from "pinia";

export const useWsStore = defineStore("ws", () => {
  const wsTagUpdate = ref({ key: 0, pictureIds: [] });
  const wsDescriptionUpdate = ref({ key: 0, pictureIds: [] });
  const wsPluginProgress = ref({ key: 0, payload: null });
  const pendingExternalImportCount = ref(0);
  const isUploadInProgress = ref(false);
  // Checkpoint / restore events — updated from App.vue's WS handler.
  const wsCheckpointEvent = ref({ key: 0, payload: null });
  const wsRestoreEvent = ref({ key: 0, payload: null });

  return {
    wsTagUpdate,
    wsDescriptionUpdate,
    wsPluginProgress,
    pendingExternalImportCount,
    isUploadInProgress,
    wsCheckpointEvent,
    wsRestoreEvent,
  };
});
