import { ref } from "vue";
import { defineStore } from "pinia";

export const useProjectStore = defineStore("project", () => {
  const projectViewMode = ref("global"); // 'global' | 'project'
  const selectedProjectId = ref(null);
  const characterProjectIds = ref({});
  const setProjectIds = ref({});

  return {
    projectViewMode,
    selectedProjectId,
    characterProjectIds,
    setProjectIds,
  };
});
