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
});

onUnmounted(() => {
  if (stopProgress) stopProgress();
});

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) refresh();
  },
);
</script>

<template>
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
