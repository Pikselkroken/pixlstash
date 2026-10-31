<template>
  <!-- Shared copies: what leaves with a file, per way out (#2417). Copy and
       Drag out are the desktop gestures; the Share panel and Export read and
       write the third row from their own switch. Its own pane, not a section
       of Privacy: that pane is sized to fit without scrolling and has no room
       for three more rows. -->
  <SettingsSection
    title="Shared copies"
    desc="What leaves with the files you hand to other apps: the ComfyUI workflow, the prompt and A1111 parameters. Each way out remembers its own answer."
    first
  >
    <div class="sh__ways-head" aria-hidden="true">
      <span>Way out</span><span>Keep the workflow</span>
    </div>
    <SettingsRow
      v-for="way in WAYS_OUT"
      :key="way.id"
      :icon="way.icon"
      :label="way.label"
      :sub="way.sub"
      :data-way="way.id"
    >
      <span class="sh__state">{{
        prefs.keepWorkflow[way.id] ? "Keeps" : "Strips"
      }}</span>
      <v-switch
        :model-value="prefs.keepWorkflow[way.id]"
        :aria-label="`${way.label}: keep the workflow`"
        color="primary"
        density="compact"
        hide-details
        @update:model-value="(v) => saveKeep(way.id, v)"
      />
    </SettingsRow>
    <p v-if="keepError" class="sh__error" role="alert">{{ keepError }}</p>
  </SettingsSection>
</template>

<script setup>
import { ref } from "vue";
import { VSwitch } from "vuetify/components";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import SettingsSection from "./SettingsSection.vue";
import SettingsRow from "./SettingsRow.vue";

const WAYS_OUT = [
  {
    id: "copy",
    icon: "mdi-content-copy",
    label: "Copy",
    sub: "Ctrl+C in the grid, Copy in the context menu. For pasting into a chat, a mail or a folder.",
  },
  {
    id: "drag",
    icon: "mdi-gesture-tap-hold",
    label: "Drag out",
    sub: "Dragging pictures out of the window. A drop on ComfyUI loads the workflow only when it is kept.",
  },
  {
    id: "share",
    icon: "mdi-tray-arrow-up",
    label: "Share panel and Export",
    sub: "Send to a folder, Save to…, Export. Their own Keep switch changes this row.",
  },
];

const prefs = useUserPrefsStore();
const keepError = ref("");

async function saveKeep(way, value) {
  keepError.value = "";
  if (!(await prefs.saveKeepWorkflow(way, value))) {
    keepError.value =
      "Could not save what leaves with shared files. Your previous choice was kept.";
  }
}
</script>

<style scoped>
.sh__ways-head {
  display: flex;
  justify-content: space-between;
  padding: 0 var(--space-4) var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

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
