<template>
  <!-- The Workflows sidebar's header slot (#2166), where Media keeps the
       project scope: which ComfyUI the workspace runs on. With none set up it
       is an amber row into the ComfyUI settings, so a first-time owner meets
       it without opening Settings. -->
  <button
    v-if="!filterStore.comfyuiConfigured"
    type="button"
    class="wcs-slot wcs-slot--setup"
    data-testid="workflows-comfyui-setup"
    @click="emit('open-settings', 'comfyui')"
  >
    <span class="wcs-mark wcs-mark--setup" aria-hidden="true"
      ><v-icon size="12">mdi-plus</v-icon></span
    >
    <span class="wcs-name">Set up ComfyUI</span>
    <v-icon size="16" class="wcs-chev" aria-hidden="true"
      >mdi-chevron-right</v-icon
    >
  </button>

  <v-menu v-else v-model="open" location="bottom start" :offset="4">
    <template #activator="{ props: menuProps }">
      <button
        v-bind="menuProps"
        type="button"
        class="wcs-slot"
        :aria-label="`ComfyUI: ${instance.name}, ${instance.status}`"
        aria-haspopup="menu"
        data-testid="workflows-comfyui-slot"
      >
        <span class="wcs-mark" aria-hidden="true">
          <ComfyuiIcon :size="12" />
          <span class="wcs-dot" :data-state="state"></span>
        </span>
        <span class="wcs-id">
          <span class="wcs-name">{{ instance.name }}</span>
          <span class="wcs-sub">{{
            [instance.status, instance.gpu].filter(Boolean).join(" · ")
          }}</span>
        </span>
        <v-icon size="16" class="wcs-chev" aria-hidden="true"
          >mdi-unfold-more-horizontal</v-icon
        >
      </button>
    </template>
    <div
      class="ctx-menu wcs-menu"
      role="menu"
      aria-label="Run on"
      tabindex="-1"
      data-testid="workflows-comfyui-menu"
      @keydown="onMenuKeydown"
    >
      <span class="section-label wcs-menu-label" role="none">Run on</span>
      <!-- One row while the backend keeps a single comfyui_url; the list is
           the multi-instance issue's. -->
      <button
        type="button"
        class="ctx-item wcs-row"
        role="menuitemradio"
        aria-checked="true"
        @click="open = false"
      >
        <v-icon class="ctx-icon" aria-hidden="true">mdi-check</v-icon>
        <span class="ctx-label-text wcs-row-id">
          <span class="wcs-row-name">{{ instance.name }}</span>
          <span class="wcs-row-sub">{{
            [instance.address, instance.gpu].filter(Boolean).join(" · ")
          }}</span>
        </span>
        <span class="ctx-meta wcs-row-status">
          <span class="wcs-dot wcs-dot--inline" :data-state="state"></span>
          {{ instance.status }}
        </span>
      </button>
      <div class="ctx-sep" role="separator"></div>
      <!-- Settings holds the one address there is; a second connection is
           the multi-instance issue's, so New opens the same place. -->
      <button type="button" class="ctx-item" role="menuitem" @click="settings">
        <span class="ctx-icon" aria-hidden="true"></span>
        <span class="ctx-label-text">New ComfyUI connection…</span>
      </button>
      <button type="button" class="ctx-item" role="menuitem" @click="settings">
        <span class="ctx-icon" aria-hidden="true"></span>
        <span class="ctx-label-text">ComfyUI settings…</span>
      </button>
    </div>
  </v-menu>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import { probeComfyui } from "../../api/comfyui";
import { useFilterStore } from "../../stores/useFilterStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";

const emit = defineEmits(["open-settings"]);

const filterStore = useFilterStore();
const open = ref(false);
// The probe's answer for the saved address; null while it is out.
const probe = ref(null);
const failed = ref(false);

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);
const STATUS = {
  checking: "Checking…",
  connected: "Connected",
  offline: "Offline",
  unknown: "Not checked",
};

const state = computed(() => {
  if (failed.value) return "unknown";
  if (!probe.value) return "checking";
  return probe.value.reachable ? "connected" : "offline";
});

function gpuLabel(answer) {
  if (!answer?.reachable || !answer.gpu) return "";
  const name = answer.gpu.replace(/^NVIDIA (GeForce )?/, "");
  return answer.vram_mb
    ? `${name} · ${Math.round(answer.vram_mb / 1024)} GB`
    : name;
}

const instance = computed(() => {
  let host = "";
  let address = filterStore.comfyuiUrl;
  try {
    const url = new URL(filterStore.comfyuiUrl);
    host = url.hostname;
    address = url.host;
  } catch (err) {
    console.warn(
      `[workflows] the saved ComfyUI address ${filterStore.comfyuiUrl} does not parse`,
      err,
    );
  }
  return {
    name: LOOPBACK.has(host) ? "This PC" : host || "ComfyUI",
    address,
    status: STATUS[state.value],
    gpu: gpuLabel(probe.value),
  };
});

/** Ask the server whether ComfyUI answers at the saved address. */
async function check() {
  const url = filterStore.comfyuiUrl;
  if (!filterStore.comfyuiConfigured || !url) return;
  try {
    const answer = await probeComfyui(url);
    if (url !== filterStore.comfyuiUrl) return;
    probe.value = answer;
    failed.value = false;
  } catch (err) {
    console.warn(`[workflows] could not ask whether ComfyUI answers at ${url}`, err);
    if (url === filterStore.comfyuiUrl) failed.value = true;
  }
}

// ponytail: checked on mount, on an address change and on each menu open, not
// polled; a ComfyUI that stops reads Connected until the menu is next opened.
watch(
  () => filterStore.comfyuiUrl,
  () => {
    probe.value = null;
    failed.value = false;
    check();
  },
  { immediate: true },
);

watch(open, (on) => {
  if (on) check();
});

function settings() {
  open.value = false;
  emit("open-settings", "comfyui");
}
</script>

<style scoped>
/* The header slot, the height every workspace's slot takes, so a switch
   moves neither edge (ModelsSideBar's .msb-slot). */
.wcs-slot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  height: var(--sidebar-slot-h);
  box-sizing: border-box;
  padding: 0 var(--space-4);
  border: 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  background: transparent;
  color: rgb(var(--v-theme-sidebar-text));
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.wcs-slot:hover,
.wcs-slot[aria-expanded="true"] {
  background: var(--hover-wash);
}

.wcs-slot:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

/* Not set up: the one thing to do here, so it is amber. */
.wcs-slot--setup {
  background: rgba(var(--v-theme-accent), 0.12);
  border-bottom-color: rgba(var(--v-theme-accent), 0.5);
}

.wcs-slot--setup:hover {
  background:
    linear-gradient(var(--hover-wash), var(--hover-wash)),
    rgba(var(--v-theme-accent), 0.12);
}

.wcs-mark {
  position: relative;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-sidebar-text), 0.12);
}

.wcs-mark--setup {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}

/* The status dot, ringed in the sidebar's ground where it sits on the mark. */
.wcs-dot {
  position: absolute;
  right: -2px;
  bottom: -2px;
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-sidebar-text), 0.45);
  box-shadow: 0 0 0 2px rgb(var(--v-theme-sidebar));
}

.wcs-dot[data-state="connected"] {
  background: rgb(var(--v-theme-success));
}

.wcs-dot--inline {
  position: static;
  box-shadow: none;
  background: rgba(var(--v-theme-on-surface), 0.45);
}

.wcs-id {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.wcs-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  line-height: 1.2;
}

.wcs-sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-2xs);
  line-height: 1.2;
  opacity: var(--opacity-text-secondary);
}

.wcs-chev {
  flex-shrink: 0;
  margin-left: auto;
  opacity: var(--opacity-text-secondary);
}

/* ModelsSideBar's folder menu width: room for the address and the card. */
.wcs-menu {
  width: 300px;
}

.wcs-menu-label {
  display: block;
  padding: var(--space-2) var(--space-3) var(--space-1);
}

.wcs-row {
  min-height: calc(var(--control-h-bar) + var(--space-4));
}

.wcs-row-id {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.wcs-row-name,
.wcs-row-sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wcs-row-sub {
  font-size: var(--text-2xs);
  opacity: var(--opacity-text-secondary);
}

.wcs-row-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
}
</style>
