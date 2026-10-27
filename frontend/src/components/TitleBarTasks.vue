<template>
  <!-- The tasks pill (#2158): how much is running, and the task list as a
       popover under it. A popover and not a fourth workspace: the list is
       glanced at from wherever you are, not gone to. The same TasksPanel the
       inspectors' Tasks tabs mount, so the two cannot drift. -->
  <v-menu
    v-model="open"
    location="bottom end"
    :offset="6"
    :close-on-content-click="false"
  >
    <template #activator="{ props: menuProps }">
      <AppBarButton
        v-bind="menuProps"
        class="ttp-pill"
        chevron
        :open="open"
        :aria-label="label"
        aria-haspopup="dialog"
        data-testid="titlebar-tasks"
      >
        <span
          v-if="tasksStore.hasActiveTasks"
          class="ttp-dot"
          aria-hidden="true"
        ></span>
        {{ text }}
      </AppBarButton>
    </template>
    <div
      class="tbm ttp-panel"
      role="dialog"
      aria-label="Tasks"
      tabindex="-1"
      data-testid="titlebar-tasks-panel"
    >
      <span class="tbm-caret tbm-caret--end"></span>
      <div class="tbm-header">
        <span class="ttp-title">Tasks</span>
        <AppBarButton
          class="ttp-close"
          icon="close"
          tooltip="Close the task list"
          @click="open = false"
        />
      </div>
      <div class="ttp-body">
        <TasksPanel />
      </div>
    </div>
  </v-menu>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { VMenu } from "vuetify/components";
import { useSidebarStore } from "../stores/useSidebarStore";
import { useTasksStore } from "../stores/useTasksStore";
import TasksPanel from "./panels/TasksPanel.vue";
import AppBarButton from "./widgets/AppBarButton.vue";

const tasksStore = useTasksStore();
const sidebarStore = useSidebarStore();
const open = ref(false);
// A notice's *Show* while a picture is open: the lightbox hands it here.
watch(
  () => sidebarStore.tasksPillRequest,
  () => {
    open.value = true;
  },
);

const text = computed(() => {
  const n = tasksStore.activeCount;
  if (!n) return "Tasks";
  return `${n} task${n === 1 ? "" : "s"}`;
});

const label = computed(() => {
  const n = tasksStore.activeCount;
  if (!n) return "No active tasks. Open the task list";
  return `${n} active task${n === 1 ? "" : "s"}. Open the task list`;
});
</script>

<style scoped>
/* The attention dot: amber, because running work is what it points at. */
.ttp-dot {
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  flex-shrink: 0;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
}

.ttp-panel {
  display: flex;
  flex-direction: column;
  width: 380px;
  max-height: calc(100vh - var(--titlebar-h) - var(--space-7));
}

.ttp-title {
  flex: 1;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

/* TasksPanel has no box of its own (`display: contents`), so its rows are
   this column's children and take its gap, as they take an inspector body's. */
.ttp-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-height: 0;
  padding: var(--space-4) var(--space-5) var(--space-5);
  overflow-y: auto;
}
</style>
