<template>
  <!-- The Media sidebar's header slot (#2159): which project the view is
       limited to. It replaced the Global / Projects / Folders tabs. A chosen
       project tints the slot with its colour; the list below takes a fainter
       wash of the same colour from SideBar. -->
  <div
    class="scope-slot"
    :class="{ 'scope-slot--project': color }"
    :style="color ? { '--scope-color': color } : null"
  >
    <v-menu v-model="open" location="bottom start" :offset="4">
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          type="button"
          class="scope-trigger"
          :aria-label="`Project: ${name}. Limit the view to a project`"
          aria-haspopup="menu"
          data-testid="sidebar-scope"
          @contextmenu.prevent="onTriggerContext"
        >
          <span class="scope-tile" aria-hidden="true">
            <v-icon size="12">mdi-briefcase-outline</v-icon>
          </span>
          <span class="scope-name">{{ name }}</span>
          <v-icon size="16" class="scope-chevron" aria-hidden="true"
            >mdi-unfold-more-horizontal</v-icon
          >
        </button>
      </template>
      <div
        class="ctx-menu scope-menu"
        role="menu"
        aria-label="Limit view to"
        tabindex="-1"
        data-testid="sidebar-scope-menu"
        @keydown="onMenuKeydown"
      >
        <span class="section-label scope-menu-label" role="none"
          >Limit view to</span
        >
        <button
          v-for="row in rows"
          :key="row.key"
          type="button"
          class="ctx-item"
          role="menuitemradio"
          :aria-checked="row.checked ? 'true' : 'false'"
          @click="pick(row.scope)"
          @contextmenu.prevent="row.project && openProjectMenu(row.project, $event)"
        >
          <v-icon v-if="row.checked" class="ctx-icon" aria-hidden="true"
            >mdi-check</v-icon
          >
          <span v-else class="ctx-icon" aria-hidden="true"></span>
          <span
            class="scope-dot"
            :class="{ 'scope-dot--none': !row.color }"
            :style="row.color ? { background: row.color } : null"
            aria-hidden="true"
          ></span>
          <span class="ctx-label-text">{{ row.name }}</span>
          <span class="ctx-meta">{{ row.count ?? "" }}</span>
        </button>
        <template v-if="!readOnly">
          <div class="ctx-sep" role="separator"></div>
          <button
            type="button"
            class="ctx-item"
            role="menuitem"
            @click="act('new-project')"
          >
            <span class="ctx-icon" aria-hidden="true"></span>
            <span class="ctx-label-text">New project…</span>
          </button>
          <button
            v-if="current"
            type="button"
            class="ctx-item"
            role="menuitem"
            @click="act('edit-project', current)"
          >
            <span class="ctx-icon" aria-hidden="true"></span>
            <span class="ctx-label-text">Edit “{{ current.name }}”…</span>
          </button>
        </template>
      </div>
    </v-menu>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { VIcon, VMenu } from "vuetify/components";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { projectColor } from "../../utils/setAppearance";

const props = defineProps({
  // Sorted for display by the caller.
  projects: { type: Array, default: () => [] },
  // "global" (Any project) | "project" (a project, or none when projectId is null).
  mode: { type: String, default: "global" },
  projectId: { type: [Number, String], default: null },
  // { any, none, byId: { [projectId]: n } }; a missing number shows blank.
  counts: { type: Object, default: () => ({}) },
  readOnly: { type: Boolean, default: false },
});

// `choose` carries "any", "none" or a project id.
const emit = defineEmits([
  "choose",
  "new-project",
  "edit-project",
  "project-menu",
]);

const open = ref(false);

const current = computed(() =>
  props.mode === "project" && props.projectId != null
    ? (props.projects.find((p) => p.id === props.projectId) ?? null)
    : null,
);

const scope = computed(() =>
  props.mode !== "project"
    ? "any"
    : props.projectId == null
      ? "none"
      : props.projectId,
);

const name = computed(() =>
  scope.value === "any"
    ? "Any project"
    : scope.value === "none"
      ? "Not in a project"
      : (current.value?.name ?? "Project"),
);

const color = computed(() =>
  current.value ? projectColor(current.value.id) : null,
);

const rows = computed(() => [
  {
    key: "any",
    scope: "any",
    name: "Any project",
    count: props.counts.any,
    checked: scope.value === "any",
  },
  ...props.projects.map((p) => ({
    key: `p-${p.id}`,
    scope: p.id,
    project: p,
    name: p.name,
    color: projectColor(p.id),
    count: props.counts.byId?.[p.id],
    checked: scope.value === p.id,
  })),
  {
    key: "none",
    scope: "none",
    name: "Not in a project",
    count: props.counts.none,
    checked: scope.value === "none",
  },
]);

function pick(next) {
  open.value = false;
  emit("choose", next);
}

function act(event, payload) {
  open.value = false;
  emit(event, payload);
}

// Right-click on the slot or a project row: that project's own menu (share,
// export, edit, delete), which used to hang off its row in the tree.
function openProjectMenu(project, event) {
  open.value = false;
  emit("project-menu", project, event);
}

function onTriggerContext(event) {
  if (current.value) openProjectMenu(current.value, event);
}
</script>

<style scoped>
.scope-slot {
  flex-shrink: 0;
  height: var(--sidebar-slot-h);
  box-sizing: border-box;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

/* The project's colour, worn as a tint: the slot carries the stronger one,
   the list under it (SideBar's .sidebar-media-list) a faint wash. */
.scope-slot--project {
  background: color-mix(in srgb, var(--scope-color) 22%, transparent);
  border-bottom-color: color-mix(in srgb, var(--scope-color) 45%, transparent);
}

.scope-trigger {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  height: 100%;
  padding: 0 var(--space-4) 0 var(--space-4);
  border: 0;
  background: transparent;
  color: rgb(var(--v-theme-sidebar-text));
  font-family: inherit;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  text-align: left;
  cursor: pointer;
}

.scope-trigger:hover,
.scope-trigger[aria-expanded="true"] {
  background-image: linear-gradient(var(--hover-wash), var(--hover-wash));
}

.scope-trigger:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

/* Any project / Not in a project: a neutral tile, and the name at secondary
   strength, since neither narrows to something with a colour of its own. */
.scope-tile {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-sidebar-text), 0.12);
  color: rgb(var(--v-theme-sidebar-text));
}

.scope-slot--project .scope-tile {
  background: var(--scope-color);
}

.scope-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scope-slot:not(.scope-slot--project) .scope-name {
  opacity: var(--opacity-text-secondary);
}

.scope-chevron {
  flex-shrink: 0;
  opacity: var(--opacity-text-secondary);
}

.scope-menu {
  width: 240px;
}

.scope-menu-label {
  display: block;
  padding: var(--space-2) var(--space-3) var(--space-1);
}

.scope-dot {
  flex-shrink: 0;
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  margin-right: var(--space-3);
  border-radius: var(--radius-pill);
}

.scope-dot--none {
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-on-surface), 0.35);
}
</style>
