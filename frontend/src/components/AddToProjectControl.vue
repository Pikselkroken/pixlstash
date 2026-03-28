<template>
  <div
    ref="rootRef"
    class="add-to-project"
    :class="{ open: menuOpen, disabled }"
  >
    <button
      class="add-to-project-btn"
      type="button"
      :disabled="disabled"
      :aria-expanded="menuOpen"
      aria-haspopup="true"
      aria-label="Set project"
      title="Set project"
      @click.stop="toggleMenu"
    >
      <v-icon size="18">mdi-briefcase-edit-outline</v-icon>
      <span class="add-to-project-label">{{ label }}</span>
      <v-icon size="16">mdi-chevron-down</v-icon>
    </button>

    <div class="add-to-project-menu" role="menu">
      <div class="add-to-project-search">
        <v-icon size="14">mdi-magnify</v-icon>
        <input
          ref="searchInputRef"
          v-model="searchQuery"
          type="text"
          placeholder="Search projects..."
          @keydown.escape.stop.prevent="closeMenu"
        />
      </div>

      <div v-if="isLoading" class="add-to-project-empty">
        Loading projects...
      </div>
      <div
        v-else-if="filteredProjects.length === 0"
        class="add-to-project-empty"
      >
        No projects found
      </div>
      <button
        v-for="project in filteredProjects"
        :key="project.key"
        class="add-to-project-item"
        type="button"
        role="menuitem"
        @click.stop="selectProject(project)"
      >
        <span class="add-to-project-item-name">{{ project.name }}</span>
      </button>

      <div v-if="statusMessage" class="add-to-project-status">
        {{ statusMessage }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  backendUrl: { type: String, required: true },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: "Project" },
});

const emit = defineEmits(["selected"]);

const rootRef = ref(null);
const searchInputRef = ref(null);
const menuOpen = ref(false);
const searchQuery = ref("");
const isLoading = ref(false);
const projects = ref([]);
const statusMessage = ref("");
let statusTimer = null;

const baseUrl = computed(() =>
  props.backendUrl ? String(props.backendUrl).replace(/\/$/, "") : "",
);

function resolveUrl(path) {
  return baseUrl.value ? `${baseUrl.value}${path}` : path;
}

const filteredProjects = computed(() => {
  const all = [
    { id: null, key: "unassigned", name: "Unassigned" },
    ...projects.value.map((project) => ({
      id: project.id,
      key: `project-${project.id}`,
      name: project.name,
    })),
  ];
  const needle = searchQuery.value.trim().toLowerCase();
  if (!needle) return all;
  return all.filter((project) =>
    String(project.name || "")
      .toLowerCase()
      .includes(needle),
  );
});

function toggleMenu() {
  if (props.disabled) return;
  menuOpen.value = !menuOpen.value;
  if (menuOpen.value) {
    openMenu();
  } else {
    closeMenu();
  }
}

function openMenu() {
  menuOpen.value = true;
  fetchProjects();
  nextTick(() => searchInputRef.value?.focus());
  document.addEventListener("pointerdown", handleOutsideClick, true);
}

function closeMenu() {
  menuOpen.value = false;
  searchQuery.value = "";
  document.removeEventListener("pointerdown", handleOutsideClick, true);
}

function handleOutsideClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) return;
  if (!rootRef.value || rootRef.value.contains(target)) return;
  closeMenu();
}

async function fetchProjects() {
  if (!props.backendUrl || isLoading.value) return;
  isLoading.value = true;
  try {
    const res = await apiClient.get(resolveUrl("/projects"));
    const data = Array.isArray(res.data) ? res.data : [];
    projects.value = data
      .map((row) => ({
        id: Number(row?.id),
        name: String(row?.name || "").trim() || `Project ${row?.id}`,
      }))
      .filter((row) => Number.isFinite(row.id) && row.id > 0)
      .sort((a, b) => a.name.localeCompare(b.name));
  } catch (_e) {
    projects.value = [];
  } finally {
    isLoading.value = false;
  }
}

function selectProject(project) {
  emit("selected", {
    projectId: project?.id ?? null,
    projectName: project?.name,
  });
  statusMessage.value =
    project?.id == null
      ? "Project cleared"
      : `Project set to ${project?.name || project?.id}`;
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => {
    statusMessage.value = "";
  }, 1200);
  closeMenu();
}

onBeforeUnmount(() => {
  if (statusTimer) clearTimeout(statusTimer);
  document.removeEventListener("pointerdown", handleOutsideClick, true);
});
</script>

<style scoped>
.add-to-project {
  position: relative;
  display: inline-flex;
}

.add-to-project-btn {
  border: none;
  background-color: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgba(var(--v-theme-on-dark-surface), 1);
  padding: 4px 8px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.85rem;
  cursor: pointer;
}

.add-to-project-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.add-to-project-btn:hover {
  filter: brightness(1.75);
  border: none;
}

.add-to-project-label {
  white-space: nowrap;
}

.add-to-project-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  min-width: 220px;
  padding: 10px;
  border-radius: 10px;
  background-color: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgba(var(--v-theme-on-dark-surface), 1);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.35);
  opacity: 0;
  transform: translateY(-6px);
  pointer-events: none;
  transition:
    opacity 0.15s ease,
    transform 0.15s ease;
  z-index: 6;
}

.add-to-project.open .add-to-project-menu {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.add-to-project-search {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.72rem;
  color: rgba(var(--v-theme-on-background), 0.7);
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  margin-bottom: 8px;
}

.add-to-project-search input {
  background: transparent;
  border: none;
  color: #fff;
  width: 100%;
  font-size: 0.78rem;
  outline: none;
}

.add-to-project-item {
  width: 100%;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-dark-surface), 1);
  background: transparent;
  border: none;
  text-align: left;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
}

.add-to-project-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.add-to-project-empty,
.add-to-project-status {
  font-size: 0.75rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  padding: 6px 2px;
}
</style>
