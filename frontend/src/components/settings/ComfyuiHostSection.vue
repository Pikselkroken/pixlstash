<script setup>
// The ComfyUI servers workflows run on. Workflows themselves are added by
// dropping a file on the Workflows view or into the watched workflows folder.
//
// With connections saved this is a table of them (#2194), each linked with its
// own key: `ComfyuiConnectionsTable.vue` holds the rows and their own changes,
// this section the list, the empty state and the dialogs.
//
// Invariant: an address is only ever saved after the backend has proven a
// ComfyUI answers there (`POST /comfyui/probe`). On this machine nothing has
// to be typed: with no connection, the usual ports are probed on open.
import { nextTick, ref, watch } from "vue";
import { VIcon, VProgressCircular } from "vuetify/components";
import { isReadOnly } from "../../utils/apiClient";
import { patchUserConfig } from "../../api/config";
import { listComfyuiConnections, probeComfyui } from "../../api/comfyui";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import Segmented from "../widgets/Segmented.vue";
import NewComfyuiConnectionDialog from "../panels/NewComfyuiConnectionDialog.vue";
import ComfyuiConnectionsTable from "./ComfyuiConnectionsTable.vue";
import ComfyuiLinkDialog from "./ComfyuiLinkDialog.vue";
import SettingsSection from "./SettingsSection.vue";
import { errorMessage } from "../../utils/apiError";
import { useFilterStore } from "../../stores/useFilterStore";

const props = defineProps({
  open: { type: Boolean, default: false },
  // First in its pane only where the acceleration section above it is absent.
  first: { type: Boolean, default: false },
});

const emit = defineEmits(["update:comfyui-configured", "open-tab"]);

// ComfyUI's own default port first, then ComfyUI Desktop's.
const LOCAL_CANDIDATES = ["http://127.0.0.1:8188/", "http://127.0.0.1:8000/"];

const SCHEME_OPTIONS = [
  { id: "http", label: "http" },
  { id: "https", label: "https" },
];

// idle (not loaded) | probing | found | address | table
const state = ref("idle");
const foundUrl = ref("");
const busy = ref(false);
const actionError = ref("");
let detectRun = 0;

// `GET /comfyui/connections`.
const connections = ref([]);
const current = ref(null);
const table = ref(null);

const scheme = ref("http");
const host = ref("127.0.0.1");
const port = ref("8188");
const formError = ref("");

const adding = ref(false);
const addButton = ref(null);
// The row the Link dialog is open on: `{id, name, url}`.
const linking = ref(null);

function fillForm(url) {
  let parsed;
  try {
    parsed = new URL(url.includes("://") ? url : `http://${url}`);
  } catch {
    return;
  }
  const https = parsed.protocol === "https:";
  scheme.value = https ? "https" : "http";
  // URL keeps an IPv6 host in brackets; the field takes it bare.
  host.value = parsed.hostname.replace(/^\[|\]$/g, "") || "127.0.0.1";
  port.value = parsed.port || (https ? "443" : "80");
}

// Probe the usual local addresses in order; the first that answers is offered.
async function detectLocal() {
  const run = ++detectRun;
  state.value = "probing";
  formError.value = "";
  for (const candidate of LOCAL_CANDIDATES) {
    try {
      const reply = await probeComfyui(candidate);
      if (run !== detectRun) return;
      if (reply?.reachable) {
        foundUrl.value = reply.url;
        state.value = "found";
        return;
      }
    } catch (e) {
      console.warn("[settings] ComfyUI probe failed", candidate, e);
      if (run !== detectRun) return;
    }
  }
  state.value = "address";
}

function show(body) {
  connections.value = body?.connections || [];
  current.value = body?.current ?? null;
  if (connections.value.length) {
    detectRun++;
    state.value = "table";
  } else if (state.value === "table") {
    // The last one was removed.
    emit("update:comfyui-configured", false);
    detectLocal();
  } else if (state.value === "idle") {
    detectLocal();
  }
}

async function load() {
  try {
    show(await listComfyuiConnections());
  } catch (e) {
    console.warn("[settings] could not read the ComfyUI connections", e);
    actionError.value = errorMessage(e, "Could not read the ComfyUI connections.");
  }
}

// Only ever called with the url a probe just answered for.
async function save(url) {
  await patchUserConfig({ comfyui_url: url });
  useFilterStore().comfyuiUrl = url;
  emit("update:comfyui-configured", true);
  await load();
}

async function connectFound() {
  busy.value = true;
  actionError.value = "";
  try {
    await save(foundUrl.value);
  } catch (e) {
    actionError.value = errorMessage(e, "Could not save the ComfyUI address.");
  } finally {
    busy.value = false;
  }
}

function useAnotherAddress() {
  detectRun++;
  if (foundUrl.value) fillForm(foundUrl.value);
  actionError.value = "";
  state.value = "address";
}

async function connectAddress() {
  formError.value = "";
  // Typed with or without brackets; wrapped once below.
  const h = host.value.trim().replace(/^\[(.*)\]$/, "$1");
  const p = Number(String(port.value).trim());
  if (!h) {
    formError.value = "Enter the host ComfyUI runs on.";
    return;
  }
  if (!Number.isInteger(p) || p < 1 || p > 65535) {
    formError.value = "Port must be between 1 and 65535.";
    return;
  }
  const hostPart = h.includes(":") ? `[${h}]` : h;
  busy.value = true;
  try {
    const reply = await probeComfyui(`${scheme.value}://${hostPart}:${p}/`);
    if (!reply?.reachable) {
      formError.value =
        reply?.detail || `Nothing answered at ${hostPart}:${p}.`;
      return;
    }
    await save(reply.url);
  } catch (e) {
    formError.value = errorMessage(e, "Could not reach that address.");
  } finally {
    busy.value = false;
  }
}

/** A row changed: the new list, or null to read it again. */
function changed(body) {
  if (body) show(body);
  else load();
}

// Opened from a button that stays, so focus goes back to it.
async function closeAdd() {
  adding.value = false;
  await nextTick();
  addButton.value?.focus();
}

async function added(body) {
  show(body);
  await closeAdd();
}

// Linking a row removes its Link button, so focus goes to the row's ⋯.
async function closeLinkDialog() {
  const id = linking.value?.id;
  linking.value = null;
  await load();
  if (id) table.value?.focusMore(id);
}

// ── Lifecycle: fetch data when the parent dialog opens ───────────────────────
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    actionError.value = "";
    formError.value = "";
    if (isReadOnly.value) return;
    state.value = "idle";
    load();
  },
  { immediate: true },
);
</script>

<template>
  <SettingsSection
    title="ComfyUI"
    desc="Run workflows from PixlStash and save ComfyUI's pictures straight into your library. Automatic setup works for a ComfyUI on this computer or your local network."
    :first="first"
  >
    <template v-if="state === 'table'">
      <div class="cf-head">
        <span class="section-label">Connections</span>
        <AppButton
          ref="addButton"
          variant="outline"
          size="sm"
          icon-left="plus"
          data-testid="comfyui-add-connection"
          @click="adding = true"
        >
          Add connection
        </AppButton>
      </div>
      <ComfyuiConnectionsTable
        ref="table"
        :connections="connections"
        :current="current"
        :linking="linking?.id || null"
        @changed="changed"
        @link="linking = $event"
      />
      <span class="section-label cf-keys-label">Link keys</span>
      <div class="cf-keys" data-testid="comfyui-link-keys-note">
        <v-icon size="18" class="cf-keys__icon" aria-hidden="true"
          >mdi-information-outline</v-icon
        >
        <p class="cf-small">
          Each linked ComfyUI has its own key, which works only from its
          address and cannot delete, change settings or make tokens. Keys are
          listed under
          <button
            type="button"
            class="cf-link"
            data-testid="comfyui-open-tokens"
            @click="emit('open-tab', 'account')"
          >
            Account › API Tokens</button
          >.
        </p>
      </div>
    </template>

    <div
      v-else-if="state !== 'idle' || actionError"
      class="cf-card"
      :aria-busy="state === 'probing' ? 'true' : 'false'"
    >
      <!-- The live region is always there, so a line that arrives in it is
           announced; one inserted already filled often is not. -->
      <div role="status" class="cf-status">
        <p v-if="state === 'probing'" class="cf-pending" data-testid="comfyui-probing">
          <v-progress-circular indeterminate size="16" width="2" />
          Looking for ComfyUI on this computer…
        </p>
      </div>

      <template v-if="state === 'found'">
        <span class="cf-pip">
          <v-icon size="16">mdi-circle-outline</v-icon>
          Not connected
        </span>
        <div class="cf-stack">
          <p class="cf-title">Found ComfyUI on this computer</p>
          <p class="cf-mono cf-muted" data-testid="comfyui-found-url">
            {{ foundUrl }}
          </p>
        </div>
        <div class="cf-row">
          <AppButton
            variant="primary"
            :loading="busy"
            data-testid="comfyui-connect-found"
            @click="connectFound"
          >
            Connect
          </AppButton>
          <AppButton
            variant="ghost"
            :disabled="busy"
            data-testid="comfyui-use-another"
            @click="useAnotherAddress"
          >
            Use another address
          </AppButton>
        </div>
      </template>

      <template v-else-if="state === 'address'">
        <div class="cf-stack">
          <p class="cf-title">Where is ComfyUI running?</p>
          <p class="cf-small cf-muted">
            Nothing answered on this computer at the usual addresses (ports 8188
            and 8000).
          </p>
        </div>
        <div class="cf-fields">
          <div class="cf-field">
            <FieldLabel>Scheme</FieldLabel>
            <Segmented
              v-model="scheme"
              :options="SCHEME_OPTIONS"
              aria-label="Scheme"
            />
          </div>
          <AppInput
            v-model="host"
            label="Host"
            placeholder="e.g. 127.0.0.1"
            mono
            :error="!!formError"
            :disabled="busy"
            data-testid="comfyui-host"
            @enter="connectAddress"
          />
          <AppInput
            v-model="port"
            label="Port"
            placeholder="8188"
            mono
            :error="!!formError"
            :disabled="busy"
            data-testid="comfyui-port"
            @enter="connectAddress"
          />
        </div>
        <p
          v-if="formError"
          class="cf-error"
          role="alert"
          data-testid="comfyui-address-error"
        >
          <v-icon size="16" class="cf-error__icon">mdi-alert-rhombus-outline</v-icon>
          <span>{{ formError }}</span>
        </p>
        <div class="cf-row">
          <AppButton
            variant="primary"
            :loading="busy"
            data-testid="comfyui-connect-address"
            @click="connectAddress"
          >
            Connect
          </AppButton>
          <AppButton
            variant="ghost"
            :disabled="busy"
            data-testid="comfyui-search-again"
            @click="detectLocal"
          >
            Search again
          </AppButton>
        </div>
      </template>

      <p v-if="actionError" class="cf-error" role="alert">
        <v-icon size="16" class="cf-error__icon">mdi-alert-rhombus-outline</v-icon>
        <span>{{ actionError }}</span>
      </p>
    </div>
    <ComfyuiLinkDialog
      v-if="linking"
      open
      :connection="linking"
      @close="closeLinkDialog"
    />
    <!-- Mounted only while open, so it starts empty each time. -->
    <NewComfyuiConnectionDialog
      v-if="adding"
      open
      @close="closeAdd"
      @added="added"
    />
  </SettingsSection>
</template>

<style scoped>
.cf-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

/* An empty live region must not add a gap to the card. */
.cf-status:empty {
  display: none;
}

.cf-pending {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cf-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.cf-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.cf-stack p {
  margin: 0;
}

.cf-title {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.cf-small {
  margin: 0;
  font-size: var(--text-sm);
}

.cf-muted {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cf-mono {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
}

.cf-pip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* Scheme | host | port, bottoms aligned so the fields share a baseline. */
.cf-fields {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) 96px;
  gap: var(--space-4);
  align-items: end;
}

.cf-field {
  display: flex;
  flex-direction: column;
}

.cf-error {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-error));
}

.cf-error__icon {
  flex-shrink: 0;
  margin-top: var(--space-1);
}

/* The section label with Add connection at its right, as the design's
   Connections header. */
.cf-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.cf-keys-label {
  margin-top: var(--space-4);
}

/* The DS info notice: a 3px info rule down its left edge. */
.cf-keys {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-left: 3px solid rgb(var(--v-theme-surface-info));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-surface));
}

.cf-keys__icon {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-info));
}

/* A link that switches the Settings pane: text-shaped, as the pack link was. */
.cf-link {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.cf-link:hover {
  text-decoration-thickness: 2px;
}
</style>
