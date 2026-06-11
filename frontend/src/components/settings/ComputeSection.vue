<script setup>
// Desktop-only: manage the compute runtime (built-in CPU/Metal vs an on-demand
// GPU overlay) via the Electron preload bridge (window.pixlstashDesktop). The
// same choice is offered on the first-run welcome screen; this is where it can
// be changed afterwards. Switching the runtime restarts the local server, which
// reloads this page — so a successful action ends with the app reloading.
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const desktop = typeof window !== "undefined" ? window.pixlstashDesktop : null;

const state = ref(null); // { bundled: {accel,label,active}, items: [...] }
const busy = ref(false);
const error = ref("");
const progress = ref(null); // { message, fraction } while installing
let stopProgress = null;

// External-server (remote access) settings. `server` mirrors the saved state
// from the backend config; `serverDraft` is what the form edits until Apply.
const server = ref(null); // { enabled, port, ssl, urls }
const serverDraft = ref({ enabled: false, port: 9537, ssl: false });

const serverDirty = computed(() => {
  if (!server.value) return false;
  const d = serverDraft.value;
  const s = server.value;
  return (
    d.enabled !== s.enabled ||
    Number(d.port) !== Number(s.port) ||
    d.ssl !== s.ssl
  );
});

const activeLabel = computed(() => {
  if (!state.value) return "";
  const active = state.value.items.find((i) => i.active);
  return active ? active.label : state.value.bundled.label;
});

async function refresh() {
  if (!desktop) return;
  try {
    state.value = await desktop.listAccelerators();
  } catch (e) {
    error.value = e?.message || String(e);
  }
}

async function refreshServer() {
  if (!desktop?.getServerSettings) return;
  try {
    const s = await desktop.getServerSettings();
    server.value = s;
    serverDraft.value = { enabled: s.enabled, port: s.port, ssl: s.ssl };
  } catch (e) {
    error.value = e?.message || String(e);
  }
}

async function guarded(fn) {
  if (busy.value || !desktop) return;
  busy.value = true;
  error.value = "";
  try {
    await fn();
  } catch (e) {
    error.value = e?.message || String(e);
  } finally {
    busy.value = false;
    progress.value = null;
    await refresh();
  }
}

// Persisting the settings restarts the backend, which reloads this page — so a
// successful Apply ends with the app reloading onto the new loopback URL.
const applyServer = () =>
  guarded(() => desktop.setServerSettings({ ...serverDraft.value }));

const install = (accel) => guarded(() => desktop.installAccelerator(accel));
const use = (accel) => guarded(() => desktop.useAccelerator(accel));
const remove = (accel) => guarded(() => desktop.removeAccelerator(accel));
const useBuiltIn = () => guarded(() => desktop.useAccelerator(null));

const openLibraryFolder = () => desktop?.openLibraryFolder();
const showLogs = () => desktop?.showLogs();

function subFor(item) {
  if (item.installed) return item.active ? "Installed · active" : "Installed";
  return "Downloads torch + GPU libraries (~2.5 GB) from PyPI / PyTorch.";
}

onMounted(() => {
  if (desktop?.onProgress) {
    stopProgress = desktop.onProgress((p) => {
      progress.value = { message: p.message || "Working…", fraction: p.fraction };
    });
  }
  refresh();
  refreshServer();
});

onUnmounted(() => {
  if (stopProgress) stopProgress();
});

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      refresh();
      refreshServer();
    }
  },
);
</script>

<template>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Remote access</div>
    <div class="settings-section-desc">
      Let other devices on your network open this library in a browser. The app
      window always uses a private local connection — this only controls the
      separate connection from the outside. Changing it restarts the local
      server.
    </div>

    <template v-if="server">
      <div class="compute-row">
        <div class="compute-meta">
          <div class="compute-label">Enable server</div>
          <div class="compute-sub">
            Serve the library to other devices on your network.
          </div>
        </div>
        <v-switch
          v-model="serverDraft.enabled"
          color="primary"
          density="compact"
          hide-details
          :disabled="busy"
        />
      </div>

      <div
        class="compute-row"
        :class="{ 'server-row--disabled': !serverDraft.enabled }"
      >
        <div class="compute-meta">
          <div class="compute-label">Port</div>
          <div class="compute-sub">The port other devices connect to.</div>
        </div>
        <input
          v-model.number="serverDraft.port"
          type="number"
          min="1"
          max="65535"
          class="server-port-input"
          :disabled="!serverDraft.enabled || busy"
        />
      </div>

      <div
        class="compute-row"
        :class="{ 'server-row--disabled': !serverDraft.enabled }"
      >
        <div class="compute-meta">
          <div class="compute-label">Use HTTPS (SSL)</div>
          <div class="compute-sub">
            Encrypts the outside connection with a self-signed certificate —
            browsers will warn on first connect.
          </div>
        </div>
        <v-switch
          v-model="serverDraft.ssl"
          color="primary"
          density="compact"
          hide-details
          :disabled="!serverDraft.enabled || busy"
        />
      </div>

      <div v-if="server.enabled" class="compute-sub server-urls">
        <template v-if="server.urls.length">
          Reachable at:
          <span v-for="url in server.urls" :key="url" class="server-url">
            {{ url }}
          </span>
        </template>
        <template v-else>
          Reachable on this machine's network address, port {{ server.port }}.
        </template>
      </div>

      <v-btn
        size="small"
        class="settings-action-btn server-apply-btn"
        :disabled="busy || !serverDirty"
        @click="applyServer"
      >
        Apply &amp; restart
      </v-btn>
    </template>
    <div v-else class="settings-token-empty">Loading…</div>
  </div>

  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Compute acceleration</div>
    <div class="settings-section-desc">
      PixlStash includes a built-in runtime that works out of the box. If this
      machine has a discrete GPU, add acceleration for faster tagging and search.
      Switching the runtime restarts the local server.
    </div>

    <template v-if="state">
      <div class="compute-row">
        <div class="compute-meta">
          <div class="compute-label">{{ activeLabel }}</div>
          <div class="compute-sub">
            {{
              state.bundled.active
                ? "Built-in runtime · active"
                : "GPU acceleration · active"
            }}
          </div>
        </div>
        <v-btn
          v-if="!state.bundled.active"
          variant="text"
          size="small"
          :disabled="busy"
          @click="useBuiltIn"
        >
          Use built-in
        </v-btn>
      </div>

      <div v-for="item in state.items" :key="item.accel" class="compute-row">
        <div class="compute-meta">
          <div class="compute-label">{{ item.label }}</div>
          <div class="compute-sub">{{ subFor(item) }}</div>
        </div>
        <div class="compute-actions">
          <v-btn
            v-if="!item.installed"
            size="small"
            class="settings-action-btn"
            :disabled="busy"
            @click="install(item.accel)"
          >
            {{ item.recommended ? "Install (recommended)" : "Install" }}
          </v-btn>
          <template v-else>
            <v-btn
              v-if="!item.active"
              size="small"
              class="settings-action-btn"
              :disabled="busy"
              @click="use(item.accel)"
            >
              Use
            </v-btn>
            <v-btn variant="text" size="small" :disabled="busy" @click="remove(item.accel)">
              Remove
            </v-btn>
          </template>
        </div>
      </div>

      <div v-if="!state.items.length" class="settings-token-empty">
        No discrete GPU detected — the built-in runtime is the best fit for this
        machine.
      </div>

      <div v-if="progress" class="compute-progress">
        <v-progress-linear
          :indeterminate="!(progress.fraction >= 0)"
          :model-value="progress.fraction >= 0 ? progress.fraction * 100 : 0"
          color="primary"
          height="6"
          rounded
        />
        <div class="compute-sub">{{ progress.message }}</div>
      </div>

      <div v-if="error" class="settings-error">{{ error }}</div>
    </template>
    <div v-else class="settings-token-empty">Loading…</div>
  </div>

  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Desktop</div>
    <div class="compute-links">
      <v-btn
        variant="text"
        size="small"
        prepend-icon="mdi-folder-image"
        @click="openLibraryFolder"
      >
        Open library folder
      </v-btn>
      <v-btn
        variant="text"
        size="small"
        prepend-icon="mdi-text-box-outline"
        @click="showLogs"
      >
        Show server logs
      </v-btn>
    </div>
  </div>
  <v-divider class="settings-section-divider" />
</template>

<style scoped>
.settings-section-divider {
  margin: 4px 0 8px;
}

.settings-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.settings-section-title {
  font-weight: 600;
}

.settings-section-desc {
  font-size: 0.92em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.compute-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 8px;
}

.compute-meta {
  min-width: 0;
}

.compute-label {
  font-weight: 600;
}

.compute-sub {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: 2px;
}

.compute-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.compute-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* Reserve two lines so the layout doesn't jump as the message length changes. */
.compute-progress .compute-sub {
  min-height: 2.4em;
}

.compute-links {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.server-row--disabled {
  opacity: 0.5;
}

.server-port-input {
  width: 96px;
  flex-shrink: 0;
  text-align: right;
  font-variant-numeric: tabular-nums;
  padding: 4px 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.28);
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: inherit;
  outline: none;
  appearance: textfield;
  -moz-appearance: textfield;
}

.server-port-input::-webkit-inner-spin-button,
.server-port-input::-webkit-outer-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.server-port-input:disabled {
  cursor: default;
}

.server-urls {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.server-url {
  font-family: monospace;
  color: rgb(var(--v-theme-on-surface));
}

.server-apply-btn {
  align-self: flex-start;
}

.settings-token-empty {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.9em;
  white-space: pre-wrap;
}

.settings-action-btn {
  background-color: rgb(var(--v-theme-primary)) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
}

.settings-action-btn:hover {
  background-color: rgb(var(--v-theme-accent)) !important;
}
</style>
