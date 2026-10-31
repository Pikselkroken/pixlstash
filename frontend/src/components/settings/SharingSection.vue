<template>
  <!-- Whether an export keeps generation data (#2417). Copy and drag-out
       always keep it: stripping during a drag costs too much, so stripping
       belongs to an explicit export. Its own pane, not a section of Privacy:
       that pane is sized to fit without scrolling and has no room left. -->
  <SettingsSection
    title="Shared copies"
    desc="What leaves with the pictures you export: the ComfyUI workflow, the prompt and A1111 parameters. Copy and drag-out always keep them."
    first
  >
    <SettingsRow
      icon="mdi-tray-arrow-up"
      label="Keep the workflow when exporting"
      sub="Export, Export to Folder and the Share panel. The Export panel's own switch changes this too."
    >
      <span class="sh__state">{{
        prefs.keepWorkflowOnExport ? "Keeps" : "Strips"
      }}</span>
      <v-switch
        :model-value="prefs.keepWorkflowOnExport"
        aria-label="Keep the workflow when exporting"
        color="primary"
        density="compact"
        hide-details
        @update:model-value="save"
      />
    </SettingsRow>
    <p v-if="error" class="sh__error" role="alert">{{ error }}</p>
  </SettingsSection>
</template>

<script setup>
import { ref } from "vue";
import { VSwitch } from "vuetify/components";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import SettingsSection from "./SettingsSection.vue";
import SettingsRow from "./SettingsRow.vue";

const prefs = useUserPrefsStore();
const error = ref("");

async function save(value) {
  error.value = "";
  if (!(await prefs.saveKeepWorkflowOnExport(value))) {
    error.value =
      "Could not save what leaves with exported files. Your previous choice was kept.";
  }
}
</script>

<style scoped>
.sh__state {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-right: var(--space-1);
  white-space: nowrap;
}

.sh__error {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
}
</style>
