<script setup>
// The ComfyUI server that workflows run on. Workflows themselves are added by
// dropping a file on the Workflows view or into the watched workflows folder.
//
// Invariant: an address is only ever saved after the backend has proven a
// ComfyUI answers there (`POST /comfyui/probe`). On this machine nothing has
// to be typed: the usual ports are probed on open.
import { computed, nextTick, ref, watch } from "vue";
import { VIcon, VProgressCircular } from "vuetify/components";
import { isReadOnly } from "../../utils/apiClient";
import { getUserConfig, patchUserConfig } from "../../api/config";
import {
  getComfyuiLink,
  getPixlstashNode,
  probeComfyui,
  unlinkComfyui,
} from "../../api/comfyui";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import Segmented from "../widgets/Segmented.vue";
import ComfyuiLinkDialog from "./ComfyuiLinkDialog.vue";
import SettingsSection from "./SettingsSection.vue";
import { errorMessage } from "../../utils/apiError";
import {
  PIXLSTASH_PACK_INSTALL,
  PIXLSTASH_PACK_URL,
} from "../../utils/runReasons";
import { useFilterStore } from "../../stores/useFilterStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { formatUserDay } from "../../utils/utils";

const props = defineProps({
  open: { type: Boolean, default: false },
  // First in its pane only where the acceleration section above it is absent.
  first: { type: Boolean, default: false },
});

const emit = defineEmits(["update:comfyui-configured"]);

// ComfyUI's own default port first, then ComfyUI Desktop's.
const LOCAL_CANDIDATES = ["http://127.0.0.1:8188/", "http://127.0.0.1:8000/"];

const SCHEME_OPTIONS = [
  { id: "http", label: "http" },
  { id: "https", label: "https" },
];

// idle (not loaded) | probing | found | address | connected
const state = ref("idle");
const comfyuiUrl = ref("");
const foundUrl = ref("");
const busy = ref(false);
const actionError = ref("");
// The newest probe of the saved URL (on load, after a save, Check again);
// null while one is in flight, which the status shows as "Checking".
const lastProbe = ref(null);
let savedProbe = 0;

const scheme = ref("http");
const host = ref("127.0.0.1");
const port = ref("8188");
const formError = ref("");

const unreachable = computed(() => lastProbe.value?.reachable === false);

// A ComfyUI that is not on this computer is linked over the network, and its
// own page is plain HTTP, so the key it is given can be read there. Said before
// the person links, not only when a link step fails.
// `URL.hostname` keeps an IPv6 host in its brackets.
const LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]"];
const comfyuiOnNetwork = computed(() => {
  try {
    const hostname = new URL(comfyuiUrl.value).hostname.toLowerCase();
    return Boolean(hostname) && !LOOPBACK_HOSTS.includes(hostname);
  } catch {
    return false;
  }
});

// Whether the configured ComfyUI has the ComfyUI-PixlStash node pack: `true`,
// `false`, or `null` when there is no ComfyUI to ask or it did not answer.
// Only a definite answer is shown, as the Workflows tab's Open in ComfyUI does.
// The check looks for the pack's `open_workflow.js`, so an old install reads
// as `false` too, and the sentence says so.
const packInstalled = ref(null);
let packCheck = 0;
let detectRun = 0;

// `GET /comfyui/link` for the saved ComfyUI; null until read, or when it could
// not be read (then the Access row is not shown rather than guessed).
const link = ref(null);
const linkDialogOpen = ref(false);
const linkButton = ref(null);
const checkAgainButton = ref(null);
let linkCheck = 0;
const userPrefs = useUserPrefsStore();
const linkedOn = computed(() =>
  formatUserDay(link.value?.linked_at, userPrefs.dateFormat),
);

async function refreshLink() {
  const check = ++linkCheck;
  link.value = null;
  if (!comfyuiUrl.value) return;
  try {
    const reply = await getComfyuiLink();
    if (check === linkCheck) link.value = reply;
  } catch (e) {
    console.warn("[settings] could not read the ComfyUI link", e);
  }
}

// Focus goes back to the Link button that opened the dialog, or, once linked
// and that button is gone, to Check again rather than to the page.
async function closeLinkDialog() {
  linkDialogOpen.value = false;
  await refreshLink();
  await nextTick();
  (linkButton.value || checkAgainButton.value)?.focus();
}

async function checkPixlstashPack() {
  const check = ++packCheck;
  packInstalled.value = null;
  if (!comfyuiUrl.value) return;
  try {
    const { can_open_workflows: canOpen } = await getPixlstashNode();
    if (check === packCheck) packInstalled.value = canOpen ?? null;
  } catch (e) {
    console.warn("[settings] could not ask ComfyUI about ComfyUI-PixlStash", e);
  }
}

function parseComfyuiUrl(value) {
  try {
    const parsed = new URL(value.includes("://") ? value : `http://${value}`);
    return {
      scheme: parsed.protocol === "https:" ? "https" : "http",
      // URL keeps an IPv6 host in brackets; the field takes it bare.
      host: parsed.hostname.replace(/^\[|\]$/g, "") || "127.0.0.1",
      port: parsed.port || (parsed.protocol === "https:" ? "443" : "80"),
    };
  } catch {
    return null;
  }
}

function fillForm(url) {
  const parsed = parseComfyuiUrl(url);
  if (!parsed) return;
  scheme.value = parsed.scheme;
  host.value = parsed.host;
  port.value = parsed.port;
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

// "Connected" claims ComfyUI answers, so the saved address is asked before
// the pip says so. An answer for an address no longer saved is dropped.
async function probeSaved() {
  const check = ++savedProbe;
  lastProbe.value = null;
  let answer;
  try {
    answer = await probeComfyui(comfyuiUrl.value);
  } catch (e) {
    console.warn("[settings] could not check the saved ComfyUI", e);
    answer = {
      reachable: false,
      detail: errorMessage(e, "Could not check ComfyUI."),
    };
  }
  if (check === savedProbe) lastProbe.value = answer;
}

async function fetchComfyuiUrl() {
  let cfg;
  try {
    cfg = await getUserConfig();
  } catch (e) {
    console.warn("[settings] could not read the ComfyUI address", e);
    actionError.value = errorMessage(e, "Could not read the ComfyUI address.");
    return;
  }
  comfyuiUrl.value = String(cfg?.comfyui_url || "").trim();
  if (comfyuiUrl.value) {
    detectRun++;
    state.value = "connected";
    fillForm(comfyuiUrl.value);
    probeSaved();
    checkPixlstashPack();
    refreshLink();
  } else {
    checkPixlstashPack();
    detectLocal();
  }
}

// Only ever called with the url a probe just answered for.
async function save(url) {
  await patchUserConfig({ comfyui_url: url });
  comfyuiUrl.value = url;
  lastProbe.value = { reachable: true, url };
  state.value = "connected";
  useFilterStore().comfyuiUrl = url;
  emit("update:comfyui-configured", true);
  checkPixlstashPack();
  refreshLink();
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

async function checkAgain() {
  busy.value = true;
  actionError.value = "";
  try {
    await probeSaved();
  } finally {
    busy.value = false;
  }
  checkPixlstashPack();
}

async function disconnect() {
  busy.value = true;
  actionError.value = "";
  // Revoke the key before forgetting the address it was written to. A refusal
  // does not keep the address: the key stays listed under API Tokens, where it
  // can still be deleted, and the person is told so. Skipped only when the
  // link is known to be absent: a status that could not be read may hide a
  // live key, and the DELETE is a no-op when there is none.
  let keyLeft = "";
  if (link.value?.linked !== false) {
    try {
      await unlinkComfyui();
    } catch (e) {
      console.warn("[settings] could not revoke the ComfyUI key", e);
      keyLeft =
        `${errorMessage(e, "The ComfyUI key could not be revoked.")} ` +
        'The key "ComfyUI (linked)" still works: delete it under Account › API Tokens.';
    }
  }
  try {
    await patchUserConfig({ comfyui_url: null });
    link.value = null;
    comfyuiUrl.value = "";
    savedProbe++;
    lastProbe.value = null;
    useFilterStore().comfyuiUrl = "";
    emit("update:comfyui-configured", false);
    checkPixlstashPack();
    detectLocal();
  } catch (e) {
    actionError.value = errorMessage(e, "Failed to clear ComfyUI URL.");
  } finally {
    busy.value = false;
  }
  if (keyLeft) actionError.value = [keyLeft, actionError.value].join(" ").trim();
}

// ── Lifecycle: fetch data when the parent dialog opens ───────────────────────
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    actionError.value = "";
    formError.value = "";
    lastProbe.value = null;
    if (isReadOnly.value) return;
    fetchComfyuiUrl();
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
    <div
      v-if="state !== 'idle' || actionError"
      class="cf-card"
      :aria-busy="state === 'probing' ? 'true' : 'false'">
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

      <template v-else-if="state === 'connected'">
        <div class="cf-row">
          <span v-if="unreachable" class="cf-pip cf-pip--bad">
            <v-icon size="16">mdi-alert-rhombus-outline</v-icon>
            Not answering
          </span>
          <span v-else-if="!lastProbe" class="cf-pip">
            <v-progress-circular indeterminate size="16" width="2" />
            Checking
          </span>
          <span v-else class="cf-pip cf-pip--ok">
            <v-icon size="16">mdi-check-circle</v-icon>
            Connected
          </span>
          <span class="cf-mono cf-muted" data-testid="comfyui-connected-url">{{
            comfyuiUrl
          }}</span>
        </div>
        <p v-if="unreachable && lastProbe.detail" class="cf-small cf-muted">
          {{ lastProbe.detail }}
        </p>
        <div role="status">
          <p
            v-if="packInstalled !== null"
            class="cf-pack-status"
            data-testid="comfyui-pack-status"
          >
            <a
              class="cf-pack-link"
              :href="PIXLSTASH_PACK_URL"
              target="_blank"
              rel="noopener noreferrer"
              >ComfyUI-PixlStash</a
            >
            node pack:
            <template v-if="packInstalled">installed.</template>
            <template v-else>
              not found, or too old. Opening workflows in ComfyUI and some
              recipes need it. {{ PIXLSTASH_PACK_INSTALL }}
            </template>
          </p>
        </div>
        <div v-if="link" class="cf-access" data-testid="comfyui-access">
          <p class="cf-small">
            <span class="cf-muted">Access</span>
            <span v-if="link.linked" data-testid="comfyui-access-value"
              >Full access · linked {{ linkedOn }}</span
            >
            <span v-else data-testid="comfyui-access-value">Not linked</span>
          </p>
          <template v-if="!link.linked">
            <div class="cf-row">
              <AppButton
                ref="linkButton"
                variant="primary"
                :disabled="busy"
                data-testid="comfyui-link"
                @click="linkDialogOpen = true"
              >
                Link ComfyUI
              </AppButton>
            </div>
            <p class="cf-small cf-muted">
              Linking gives ComfyUI full access to this library so its
              PixlStash nodes can load and save pictures.
            </p>
            <p
              v-if="comfyuiOnNetwork"
              class="cf-small cf-muted"
              data-testid="comfyui-network-warning"
            >
              ComfyUI's own page stays plain HTTP, so anyone on your network
              who opens it can see the key.
            </p>
          </template>
        </div>
        <div class="cf-row">
          <AppButton
            ref="checkAgainButton"
            variant="secondary"
            :loading="busy"
            data-testid="comfyui-check-again"
            @click="checkAgain"
          >
            Check again
          </AppButton>
          <span class="cf-spacer" />
          <AppButton
            variant="danger"
            :disabled="busy"
            data-testid="comfyui-disconnect"
            @click="disconnect"
          >
            Disconnect
          </AppButton>
        </div>
        <p
          v-if="link?.linked"
          class="cf-small cf-muted"
          data-testid="comfyui-disconnect-note"
        >
          Disconnect removes PixlStash's key from ComfyUI and revokes it here.
        </p>
      </template>

      <p v-if="actionError" class="cf-error" role="alert">
        <v-icon size="16" class="cf-error__icon">mdi-alert-rhombus-outline</v-icon>
        <span>{{ actionError }}</span>
      </p>
    </div>
    <ComfyuiLinkDialog :open="linkDialogOpen" @close="closeLinkDialog" />
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

.cf-spacer {
  flex: 1 1 auto;
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

.cf-pip--ok {
  color: rgb(var(--v-theme-surface-success));
}

.cf-pip--bad {
  color: rgb(var(--v-theme-surface-error));
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

/* Access: label then value on one line, the fix underneath. */
.cf-access {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.cf-access .cf-small {
  display: flex;
  gap: var(--space-4);
}

/* ── Node pack status line ────────────────────────────────────────────────── */
.cf-pack-status {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The plugin catalogue link's shape (BehaviourSection.vue). */
.cf-pack-link {
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.cf-pack-link:hover {
  text-decoration-thickness: 2px;
}
</style>
