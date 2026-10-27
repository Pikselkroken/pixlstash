<template>
  <!-- The Workflows sidebar's header slot (#2166), where Media keeps the
       project scope: which ComfyUI the workspace runs on. With none set up it
       is an amber row into the ComfyUI settings, so a first-time owner meets
       it without opening Settings. -->
  <!-- Nothing until the config is read: a saved address must not flash the
       Set up row on every load. -->
  <div v-if="!filterStore.comfyuiConfigRead" class="wcs-slot" aria-hidden="true"></div>
  <button
    v-else-if="!filterStore.comfyuiConfigured"
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
        ref="trigger"
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
      <button
        v-for="row in rows"
        :key="row.id"
        type="button"
        class="ctx-item wcs-row"
        role="menuitemradio"
        :aria-checked="String(row.current)"
        :data-testid="`workflows-comfyui-row-${row.id}`"
        @click="pick(row)"
      >
        <v-icon class="ctx-icon" aria-hidden="true">{{
          row.current ? "mdi-check" : ""
        }}</v-icon>
        <span class="ctx-label-text wcs-row-id">
          <span class="wcs-row-name">{{ row.name }}</span>
          <span class="wcs-row-sub">{{
            [row.address, row.gpu].filter(Boolean).join(" · ")
          }}</span>
        </span>
        <span class="ctx-meta wcs-row-status">
          <span class="wcs-dot wcs-dot--inline" :data-state="row.state"></span>
          {{ STATUS[row.state] }}
        </span>
      </button>
      <div class="ctx-sep" role="separator"></div>
      <button
        type="button"
        class="ctx-item"
        role="menuitem"
        @click="newConnection"
      >
        <span class="ctx-icon" aria-hidden="true"></span>
        <span class="ctx-label-text">New ComfyUI connection…</span>
      </button>
      <button type="button" class="ctx-item" role="menuitem" @click="settings">
        <span class="ctx-icon" aria-hidden="true"></span>
        <span class="ctx-label-text">ComfyUI settings…</span>
      </button>
    </div>
  </v-menu>
  <!-- Mounted only while open, so it starts empty each time. -->
  <NewComfyuiConnectionDialog
    v-if="adding"
    open
    @close="closeDialog"
    @added="added"
  />
</template>

<script setup>
import { computed, nextTick, reactive, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import NewComfyuiConnectionDialog from "./NewComfyuiConnectionDialog.vue";
import { listComfyuiConnections, probeComfyui } from "../../api/comfyui";
import { patchUserConfig } from "../../api/config";
import { useFilterStore } from "../../stores/useFilterStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";

const emit = defineEmits(["open-settings"]);

const filterStore = useFilterStore();
const open = ref(false);
const adding = ref(false);
const trigger = ref(null);
// `GET /comfyui/connections`; empty until read, when the slot names the
// saved address the way the server would.
const connections = ref([]);
// This session's probe answers by `urlKey`; `failed` when the probe itself did
// not come back. An address with none reads its stored status meanwhile.
const answers = reactive({});

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);
const STATUS = {
  checking: "Checking…",
  connected: "Connected",
  offline: "Offline",
  unknown: "Not checked",
};

// As the server matches them: case and a trailing slash aside.
const urlKey = (url) => (url || "").replace(/\/+$/, "").toLowerCase();
const sameUrl = (a, b) => urlKey(a) === urlKey(b);

function parts(url) {
  try {
    const parsed = new URL(url);
    return {
      name: LOOPBACK.has(parsed.hostname) ? "This PC" : parsed.hostname,
      address: parsed.host,
    };
  } catch (err) {
    console.warn(`[workflows] the ComfyUI address ${url} does not parse`, err);
    return { name: "ComfyUI", address: url };
  }
}

function gpuLabel(gpu, vramMb) {
  if (!gpu) return "";
  const name = gpu.replace(/^NVIDIA (GeForce )?/, "");
  return vramMb ? `${name} · ${Math.round(vramMb / 1024)} GB` : name;
}

/** A connection as the menu draws it, its status the newest known. */
function describe(entry) {
  const answer = answers[urlKey(entry.url)];
  let state = entry.status || "checking";
  let gpu = gpuLabel(entry.gpu, entry.vram_mb);
  if (answer === "failed") state = "unknown";
  else if (answer) {
    state = answer.reachable ? "connected" : "offline";
    gpu = answer.reachable ? gpuLabel(answer.gpu, answer.vram_mb) : "";
  }
  return {
    id: entry.id,
    url: entry.url,
    name: entry.name || parts(entry.url).name,
    address: parts(entry.url).address,
    current: sameUrl(entry.url, filterStore.comfyuiUrl),
    state,
    gpu,
  };
}

const rows = computed(() => {
  const listed = connections.value.map(describe);
  // The list not read yet, or read before a change of address elsewhere.
  if (!listed.some((row) => row.current) && filterStore.comfyuiUrl) {
    listed.unshift(describe({ id: "saved", url: filterStore.comfyuiUrl }));
  }
  return listed;
});

const instance = computed(() => {
  const row = rows.value.find((r) => r.current) || { state: "checking" };
  return { ...row, status: STATUS[row.state] };
});
const state = computed(() => instance.value.state);

async function load() {
  try {
    connections.value = (await listComfyuiConnections()).connections || [];
  } catch (err) {
    console.warn("[workflows] could not read the ComfyUI connections", err);
  }
}

/** Ask the server whether ComfyUI answers at *url*. */
async function check(url) {
  try {
    answers[urlKey(url)] = await probeComfyui(url);
  } catch (err) {
    console.warn(`[workflows] could not ask whether ComfyUI answers at ${url}`, err);
    answers[urlKey(url)] = "failed";
  }
}

// ponytail: checked on mount, on an address change and on each menu open, not
// polled; a ComfyUI that stops reads Connected until the menu is next opened.
watch(
  () => filterStore.comfyuiUrl,
  (url) => {
    if (!filterStore.comfyuiConfigured || !url) return;
    delete answers[urlKey(url)];
    check(url);
    load();
  },
  { immediate: true },
);

watch(open, async (on) => {
  if (!on) return;
  await load();
  rows.value.forEach((row) => check(row.url));
});

/** Run on *row* from now on: what the config PATCH of `comfyui_url` does. */
async function pick(row) {
  open.value = false;
  if (row.current) return;
  try {
    const body = await patchUserConfig({ comfyui_url: row.url });
    filterStore.comfyuiUrl = body?.config?.comfyui_url || row.url;
  } catch (err) {
    console.warn(`[workflows] could not switch ComfyUI to ${row.url}`, err);
  }
}

function newConnection() {
  open.value = false;
  adding.value = true;
}

// Opened from a menu item that is gone by the time it closes, so focus goes
// back to the slot rather than to the page.
async function closeDialog() {
  adding.value = false;
  await nextTick();
  trigger.value?.focus();
}

async function added(list) {
  connections.value = list?.connections || connections.value;
  await closeDialog();
  open.value = true;
}

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
