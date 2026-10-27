<template>
  <!-- The library picker (#2158): the open library's name over a 3px bar of
       how full its drive is, and a menu of every library with its free space.
       It used to be a label that deep-linked to Settings › Libraries; it now
       switches itself, through the same confirmation that pane uses. -->
  <v-menu v-model="open" location="bottom start" :offset="4">
    <template #activator="{ props: menuProps }">
      <AppBarButton
        v-bind="withRef(menuProps, (el) => (trigger = el))"
        class="tlp-trigger"
        icon="bookshelf"
        chevron
        :open="open"
        :aria-label="triggerLabel"
        aria-haspopup="menu"
        data-testid="titlebar-library"
      >
        <span class="tlp-id">
          <span class="tlp-name">{{ activeLibraryName }}</span>
          <span
            v-if="activeDrive"
            class="tlp-bar"
            :data-state="activeDrive.state"
            aria-hidden="true"
            ><span :style="{ width: `${activeDrive.used}%` }"></span
          ></span>
        </span>
      </AppBarButton>
    </template>
    <div
      class="ctx-menu tlp-menu"
      role="menu"
      aria-label="Libraries"
      aria-describedby="tlp-shared-note"
      tabindex="-1"
      data-testid="titlebar-library-menu"
      @keydown="onMenuKeydown"
    >
      <button
        v-for="row in rows"
        :key="row.library.uuid"
        type="button"
        class="ctx-item"
        :class="{ 'ctx-item--disabled': row.unavailable }"
        role="menuitemradio"
        :aria-checked="row.library.is_active ? 'true' : 'false'"
        :aria-disabled="row.unavailable ? 'true' : undefined"
        :aria-label="row.label"
        :data-uuid="row.library.uuid"
        @click="pick(row)"
      >
        <Tooltip v-if="row.reason" :text="row.reason" activator="parent" />
        <v-icon v-if="row.library.is_active" class="ctx-icon" aria-hidden="true"
          >mdi-check</v-icon
        >
        <span v-else class="ctx-icon" aria-hidden="true"></span>
        <span class="ctx-label-text">{{ row.library.name }}</span>
        <span v-if="!row.library.is_reachable" class="ctx-meta">Not found</span>
        <span v-else-if="row.drive" class="ctx-meta tlp-meta">
          <span class="tlp-free" :data-state="row.drive.state"
            >{{ formatModelSize(row.drive.free) }} free</span
          >
          <span class="tlp-bar tlp-bar--mini" :data-state="row.drive.state"
            ><span :style="{ width: `${row.drive.used}%` }"></span
          ></span>
        </span>
      </button>
      <div class="ctx-sep" role="separator"></div>
      <p id="tlp-shared-note" class="tlp-note" role="none">
        Models and workflows are shared by all libraries.
      </p>
      <button
        v-if="librariesStore.canManage"
        type="button"
        class="ctx-item"
        role="menuitem"
        @click="addLibrary"
      >
        <span class="ctx-icon" aria-hidden="true"></span>
        <span class="ctx-label-text">Add a library…</span>
      </button>
      <button
        type="button"
        class="ctx-item"
        role="menuitem"
        @click="manageLibraries"
      >
        <span class="ctx-icon" aria-hidden="true"></span>
        <span class="ctx-label-text">Manage libraries…</span>
      </button>
    </div>
  </v-menu>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";
import { useFolderMappingStore } from "../stores/useFolderMappingStore";
import {
  useLibrariesStore,
  useLibrarySwitchStore,
} from "../stores/useLibrariesStore";
import { driveFullness } from "../utils/libraryChrome";
import { onMenuKeydown } from "../utils/menuKeyboard.js";
import { withRef } from "../utils/withRef.js";
import { formatModelSize } from "../utils/modelShelf";
import AppBarButton from "./widgets/AppBarButton.vue";
import Tooltip from "./widgets/Tooltip.vue";

defineProps({
  // From App.vue rather than the store: it also names a library still on its
  // first import, which the listing leaves out.
  activeLibraryName: { type: String, required: true },
});

const emit = defineEmits(["open-settings"]);

const librariesStore = useLibrariesStore();
const switchStore = useLibrarySwitchStore();
const mappingStore = useFolderMappingStore();

const open = ref(false);
// The trigger outlives the menu, so a failed or cancelled switch can give
// focus back to it. Not reactive: only `pick` reads it.
let trigger = null;

// Free space moves while the app is open, so the menu reads it again each time
// it opens rather than showing what boot saw.
watch(open, (isOpen) => {
  if (isOpen) librariesStore.refresh();
});

const activeDrive = computed(() => driveFullness(librariesStore.activeLibrary));

const triggerLabel = computed(() => {
  const name = librariesStore.activeLibrary?.name;
  const drive = activeDrive.value;
  const parts = [`Library: ${name ?? ""}`.trim()];
  if (drive) {
    parts.push(
      `Its drive is ${drive.used}% full, ${drive.words}: ${formatModelSize(drive.free)} free of ${formatModelSize(drive.total)}`,
    );
  }
  parts.push("Choose a library");
  return parts.join(". ");
});

// A library cannot be switched to when its drive is unplugged, or when this
// caller may not switch at all (a remote owner: Settings › Libraries says why,
// and Manage libraries… is the way there).
const rows = computed(() =>
  librariesStore.libraries.map((library) => {
    const drive = driveFullness(library);
    const unavailable =
      !library.is_active &&
      (!library.is_reachable || !librariesStore.canManage);
    const state = !library.is_reachable
      ? "not found"
      : drive
        ? `${formatModelSize(drive.free)} free, drive ${drive.used}% full`
        : "";
    return {
      library,
      drive,
      unavailable,
      // Why an inert row is inert, said where the row is rather than only in
      // Settings.
      reason: !unavailable
        ? ""
        : !library.is_reachable
          ? "Its drive is not connected"
          : "Switching library is only available on the machine running PixlStash, or over your local network or Tailscale",
      label: [library.name, state].filter(Boolean).join(", "),
    };
  }),
);

async function pick(row) {
  if (row.unavailable) return;
  open.value = false;
  if (row.library.is_active) return;
  const asked = await switchStore.confirmAndBegin(
    row.library,
    librariesStore.activeLibrary,
    trigger?.el ?? null,
  );
  // Declined: the menu row that held focus left with the menu, so give the
  // keyboard back to the picker rather than to <body>.
  if (!asked) trigger?.focus?.();
}

function addLibrary() {
  open.value = false;
  mappingStore.openWizard();
}

function manageLibraries() {
  open.value = false;
  emit("open-settings", "libraries");
}
</script>

<style scoped>
.tlp-trigger {
  min-width: 0;
  max-width: 240px;
  font-weight: var(--weight-semibold);
}

.tlp-id {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-1);
  min-width: 76px;
  overflow: hidden;
  text-align: left;
}

.tlp-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}

/* The drive meter: a 3px track with the used part painted in its state. */
.tlp-bar {
  display: flex;
  height: 3px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--drive-meter-track);
}

.tlp-bar > span {
  display: block;
}

.tlp-bar--mini {
  width: 28px;
  flex-shrink: 0;
}

.tlp-bar[data-state="success"] > span {
  background: rgb(var(--v-theme-surface-success));
}

.tlp-bar[data-state="warning"] > span {
  background: rgb(var(--v-theme-surface-warning));
}

.tlp-bar[data-state="error"] > span {
  background: rgb(var(--v-theme-surface-error));
}

.tlp-menu {
  width: 320px;
}

.tlp-meta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

/* A drive past 75 % says so in its words' colour too; the row's own words
   stay ink otherwise. */
.tlp-free[data-state="warning"] {
  color: rgb(var(--v-theme-surface-warning));
}

.tlp-free[data-state="error"] {
  color: rgb(var(--v-theme-surface-error));
}

.tlp-note {
  margin: 0;
  padding: var(--space-1) var(--space-4) var(--space-2);
  font-size: var(--text-2xs);
  line-height: 1.4;
  opacity: var(--opacity-text-secondary);
  white-space: normal;
}
</style>
