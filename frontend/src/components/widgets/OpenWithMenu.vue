<template>
  <!-- Open with ▸ : the apps the server's OS offers for this file type. The
       grid and the lightbox menus both render it, as one more `data-group`
       submenu, so the host menu's ArrowRight / ArrowLeft drive it too. -->
  <div
    class="ctx-submenu-wrap"
    data-group="open-with"
    @mouseenter="emit('update:open', true)"
    @mouseleave="emit('update:open', false)"
  >
    <button
      class="ctx-item"
      role="menuitem"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click="emit('update:open', true)"
    >
      <Tooltip
        text="Edit a copy in another app. Save over the file there and the edit comes back stacked on this picture."
        activator="parent"
      />
      <v-icon class="ctx-icon">mdi-open-in-app</v-icon>
      Open with
      <v-icon class="ctx-arrow">mdi-chevron-right</v-icon>
    </button>
    <div v-if="open" v-submenu-fit class="ctx-submenu ctx-open-with" role="menu">
      <div
        v-if="loading"
        class="ctx-item ctx-item--muted"
        role="menuitem"
        aria-disabled="true"
      >
        <v-progress-circular
          class="ctx-icon"
          indeterminate
          size="16"
          width="2"
        />
        <span class="ctx-label-text">Looking for apps…</span>
      </div>
      <template v-else>
        <template v-for="group in groups" :key="group.key">
          <div v-if="group.sep" class="ctx-sep" role="separator" />
          <div v-if="group.label" class="ctx-label section-label">
            {{ group.label }}
          </div>
          <button
            v-for="app in group.apps"
            :key="app.id"
            class="ctx-item"
            :class="{ 'ctx-item--muted': group.muted }"
            role="menuitem"
            @click="pick({ appId: app.id, appName: app.name })"
          >
            <span class="ctx-icon" aria-hidden="true" />
            <span class="ctx-label-text">{{ app.name }}</span>
            <span v-if="app.default" class="ctx-default-pill">default</span>
          </button>
        </template>
        <button
          v-if="!apps.length"
          class="ctx-item"
          role="menuitem"
          disabled
        >
          <span class="ctx-icon" aria-hidden="true" />
          {{ failed ? "Couldn't ask the system for apps" : "No apps found for this file type" }}
        </button>
        <template v-if="other">
          <div class="ctx-sep" role="separator" />
          <button
            class="ctx-item"
            role="menuitem"
            @click="pick({ other: true })"
          >
            <v-icon class="ctx-icon">mdi-dots-horizontal</v-icon>
            Other app…
          </button>
        </template>
      </template>
    </div>
  </div>
</template>

<script>
// Per file format, for the page's life: listing asks the OS, which can take a
// moment (a PowerShell compile on Windows), and what is installed rarely
// changes while the page is open.
const appsByFormat = new Map();

/** Forget every cached list; for tests. */
export function clearOpenWithCache() {
  appsByFormat.clear();
}
</script>

<script setup>
import { computed, ref, watch } from "vue";
import { listOpenWithApps } from "../../api/pictures";
import { MediaFormat } from "../../utils/media";
import { vSubmenuFit } from "../../utils/submenuFit";
import Tooltip from "./Tooltip.vue";

const props = defineProps({
  // The picture the menu was opened on: `id` and `format`.
  image: { type: Object, required: true },
  open: { type: Boolean, default: false },
});
const emit = defineEmits(["update:open", "pick"]);

const loading = ref(false);
const failed = ref(false);
const apps = ref([]);
const other = ref(false);

const formatKey = computed(() =>
  String(MediaFormat(props.image) || "").toLowerCase(),
);

// Editors first, under labels only when both kinds are there: a lone
// "Editors" heading says nothing, and a list of only viewers is the OS's own
// list, unlabelled and not muted.
const groups = computed(() => {
  const editors = apps.value.filter((app) => app.editor);
  const rest = apps.value.filter((app) => !app.editor);
  if (!editors.length || !rest.length) {
    return [{ key: "all", apps: apps.value }];
  }
  return [
    { key: "editors", label: "Editors", apps: editors },
    { key: "other", label: "Other apps", apps: rest, muted: true, sep: true },
  ];
});

async function load() {
  const key = formatKey.value;
  const cached = appsByFormat.get(key);
  if (cached) {
    apps.value = cached.apps;
    other.value = cached.other;
    failed.value = false;
    return;
  }
  loading.value = true;
  failed.value = false;
  try {
    const body = await listOpenWithApps(props.image.id);
    const entry = {
      apps: Array.isArray(body?.apps) ? body.apps : [],
      other: body?.other === true,
    };
    // Not an empty answer: the server answers empty when the OS tool failed,
    // and asks again next time, so this list must too.
    if (entry.apps.length) appsByFormat.set(key, entry);
    apps.value = entry.apps;
    other.value = entry.other;
  } catch (err) {
    console.warn(`Could not list the apps for picture ${props.image.id}`, err);
    apps.value = [];
    other.value = false;
    failed.value = true;
  } finally {
    loading.value = false;
  }
}

// On the first hover of the submenu, not when the menu opens.
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) load();
  },
  { immediate: true },
);

function pick(choice) {
  emit("pick", { pictureId: props.image.id, ...choice });
}
</script>

<style scoped>
.ctx-open-with {
  min-width: 240px;
}

/* Under "Other apps": still clickable, quieter than the editors above. */
.ctx-item--muted .ctx-label-text {
  opacity: var(--opacity-text-secondary);
}
</style>
