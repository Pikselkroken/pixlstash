<script setup>
// Desktop-only: manage the compute runtime (built-in CPU/Metal vs an on-demand
// GPU overlay) via the Electron preload bridge (window.pixlstashDesktop). The
// same choice is offered on the first-run welcome screen; this is where it can
// be changed afterwards. Switching the runtime restarts the local server, which
// reloads this page — so a successful action ends with the app reloading.
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { apiClient, login } from "../../utils/apiClient";
import AppButton from "../widgets/AppButton.vue";
import AppStepper from "../widgets/AppStepper.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsRow from "./SettingsRow.vue";
import SettingsTwoCol from "./SettingsTwoCol.vue";
import { VSwitch, VIcon, VTooltip, VProgressLinear } from "vuetify/components";

const props = defineProps({
  open: { type: Boolean, default: false },
  // The proposal splits this into two tabs sharing one component: 'compute'
  // (acceleration) and 'backend' (remote access + desktop + updates). Each
  // instance loads and renders only its own half.
  view: { type: String, default: "compute" },
  checkForUpdates: { type: Boolean, default: null },
});

const emit = defineEmits(["update:check-for-updates"]);

const isBackend = computed(() => props.view === "backend");

// "Check for updates automatically" — lives on the Backend tab's Desktop block.
const checkForUpdatesModel = computed({
  get: () => props.checkForUpdates ?? false,
  set: (value) => emit("update:check-for-updates", value),
});

const desktop = typeof window !== "undefined" ? window.pixlstashDesktop : null;

const state = ref(null); // { bundled: {accel,label,active}, items: [...] }
const busy = ref(false);
const error = ref("");
const progress = ref(null); // { message, fraction } while installing
let stopProgress = null;

// Where on-demand GPU overlays are stored. Lets the user keep the multi-GB
// download off the system drive; changing it relocates an installed overlay.
const backendLocation = ref("");

// External-server (remote access) settings. `server` mirrors the saved state
// from the backend config; `serverDraft` is what the form edits until Apply.
const server = ref(null); // { enabled, port, ssl, urls }
const serverDraft = ref({ enabled: false, port: 9537, ssl: false });

// Owner-account state. A fresh desktop owner is auto-logged-in over loopback
// with NO username/password, but remote devices must sign in — so the backend
// refuses to bind the network listener until the owner is claimed. We collect a
// username + password here and claim the account (POST /login, loopback-only)
// before enabling remote access, so the server actually comes up reachable.
const accountUsername = ref("");
const accountHasPassword = ref(false);
const ownerUsername = ref("");
const ownerPassword = ref("");
const ownerShowPassword = ref(false);
const ownerError = ref("");

// Claimed = has BOTH a username and a password. Either alone leaves remote login
// broken (login matches on username; the listener gate checks the password).
const accountClaimed = computed(
  () => accountHasPassword.value && !!accountUsername.value,
);
// Show the owner-setup form whenever remote access is requested on an unclaimed
// account (newly toggled on, or previously enabled but never actually bound).
const needsOwnerSetup = computed(
  () => serverDraft.value.enabled && !accountClaimed.value,
);
const ownerFormValid = computed(
  () =>
    ownerUsername.value.trim().length > 0 &&
    ownerPassword.value.trim().length >= 8,
);

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

// Whether the chosen external port can actually be bound. We probe it in the
// main process (a throwaway bind on 0.0.0.0) so the user is warned before
// enabling the server on a port that's already taken or privileged. `null` =
// not checked yet.
const portCheck = ref(null); // { available, code } | null
let portCheckTimer = null;
let portCheckSeq = 0;

// The running external listener owns its own port, so probing that exact port
// reports it "in use" by us — not a real conflict. Ignore that self-case.
const portIsOwnRunning = computed(
  () =>
    !!server.value?.enabled &&
    Number(server.value.port) === Number(serverDraft.value.port),
);

const portConflict = computed(
  () =>
    serverDraft.value.enabled &&
    !!portCheck.value &&
    !portCheck.value.available &&
    !portIsOwnRunning.value,
);

const portWarning = computed(() => {
  if (!portConflict.value) return "";
  const port = serverDraft.value.port;
  switch (portCheck.value.code) {
    case "EADDRINUSE":
      return `Port ${port} is already in use by another app. Pick a different port.`;
    case "EACCES":
      return `Port ${port} is reserved by the system. Pick a port above 1023.`;
    case "EINVAL":
      return `${port} is not a valid port number (use 1–65535).`;
    default:
      return `Port ${port} can't be used for the server.`;
  }
});

async function checkPort() {
  if (!desktop?.checkServerPort) return;
  const port = Number(serverDraft.value.port);
  if (!serverDraft.value.enabled || !Number.isInteger(port)) {
    portCheck.value = null;
    return;
  }
  const seq = ++portCheckSeq;
  try {
    const result = await desktop.checkServerPort(port);
    if (seq === portCheckSeq) portCheck.value = result;
  } catch (e) {
    if (seq === portCheckSeq) portCheck.value = null;
    error.value = e?.message || String(e);
  }
}

// Debounce while the user is typing a port; re-check when the toggle flips.
watch(
  () => [serverDraft.value.port, serverDraft.value.enabled],
  () => {
    if (portCheckTimer) clearTimeout(portCheckTimer);
    portCheckTimer = setTimeout(checkPort, 300);
  },
);

// Desktop-shell preference: keep running in the tray when the window is closed.
const hideToTrayOnClose = ref(true);

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

async function refreshLocation() {
  if (!desktop?.getBackendLocation) return;
  try {
    const res = await desktop.getBackendLocation();
    backendLocation.value = res?.dir || "";
  } catch (e) {
    error.value = e?.message || String(e);
  }
}

// Pick a new folder and move any installed overlay there. setBackendLocation
// restarts the backend when an overlay is active, which reloads this page; when
// none is active it just returns and we update the shown path.
async function changeLocation() {
  if (busy.value || !desktop?.pickBackendLocation) return;
  const dir = await desktop.pickBackendLocation(backendLocation.value);
  if (!dir) return;
  await guarded(async () => {
    progress.value = {
      message: "Moving GPU files to the new location…",
      fraction: -1,
    };
    const res = await desktop.setBackendLocation(dir);
    backendLocation.value = res?.dir || dir;
  });
}

async function refreshDesktopPrefs() {
  if (!desktop?.getDesktopPrefs) return;
  try {
    const prefs = await desktop.getDesktopPrefs();
    hideToTrayOnClose.value = !!prefs.hideToTrayOnClose;
  } catch (e) {
    error.value = e?.message || String(e);
  }
}

async function setHideToTray(value) {
  if (!desktop?.setDesktopPrefs) return;
  hideToTrayOnClose.value = value;
  try {
    await desktop.setDesktopPrefs({ hideToTrayOnClose: value });
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

async function refreshAuthState() {
  try {
    const res = await apiClient.get("/users/me/auth");
    accountUsername.value = res.data?.username || "";
    accountHasPassword.value = Boolean(res.data?.has_password);
    // Prefill the form with any existing username so the user only fills gaps.
    if (accountUsername.value && !ownerUsername.value) {
      ownerUsername.value = accountUsername.value;
    }
  } catch (e) {
    // Non-fatal: the form still works, the claim just won't be pre-filled.
    console.warn("Failed to load owner account state:", e);
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
// successful Apply ends with the app reloading onto the new loopback URL. When
// turning remote access ON for an unclaimed account, we first claim the owner
// (set username + password via POST /login) so the network listener can bind;
// without it the backend would refuse to expose the server and the host would
// be silently unreachable.
async function applyServer() {
  if (busy.value || !desktop) return;
  // Capture before any mutation — accountClaimed flips once the claim lands.
  const claiming = serverDraft.value.enabled && !accountClaimed.value;
  ownerError.value = "";
  if (claiming && !ownerFormValid.value) {
    ownerError.value =
      "Choose a username and a password of at least 8 characters.";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    if (claiming) {
      // Loopback-only first-owner claim; sets username AND password.
      await login(ownerUsername.value.trim(), ownerPassword.value);
      accountUsername.value = ownerUsername.value.trim();
      accountHasPassword.value = true;
      ownerPassword.value = "";
    }
    await desktop.setServerSettings({ ...serverDraft.value });
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    if (claiming) ownerError.value = detail;
    else error.value = detail;
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

function loadForView() {
  if (isBackend.value) {
    refreshServer();
    refreshDesktopPrefs();
    refreshAuthState();
  } else {
    refresh();
    refreshLocation();
  }
}

onMounted(() => {
  // Only the compute view installs accelerators, so only it needs progress.
  if (!isBackend.value && desktop?.onProgress) {
    stopProgress = desktop.onProgress((p) => {
      progress.value = {
        message: p.message || "Working…",
        fraction: p.fraction,
      };
    });
  }
  loadForView();
});

onUnmounted(() => {
  if (stopProgress) stopProgress();
  if (portCheckTimer) clearTimeout(portCheckTimer);
});

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) loadForView();
  },
);
</script>

<template>
  <!-- ── Backend tab: remote access + desktop ───────────────────────────── -->
  <div v-if="isBackend" class="compute-backend">
    <SettingsSection
      title="Remote access"
      desc="Let other devices on your network open this library in a browser. The app window always uses a private local connection — this only controls the separate connection from the outside. Changing it restarts the local server."
      first
    >
      <template v-if="server">
        <SettingsRow
          label="Enable server"
          sub="Serve the library to other devices on your network."
        >
          <v-switch
            v-model="serverDraft.enabled"
            color="accent"
            density="compact"
            hide-details
            :disabled="busy"
          />
        </SettingsRow>

        <SettingsTwoCol>
          <SettingsRow
            label="Port"
            sub="The port other devices connect to."
            :dim="!serverDraft.enabled"
          >
            <div class="server-port-control">
              <v-tooltip
                v-if="portConflict"
                :text="portWarning"
                location="top"
                max-width="280"
              >
                <template #activator="{ props: tooltipProps }">
                  <v-icon
                    v-bind="tooltipProps"
                    color="error"
                    size="18"
                    tabindex="0"
                    class="server-port-warning"
                    :aria-label="portWarning"
                    >mdi-alert-circle</v-icon
                  >
                </template>
              </v-tooltip>
              <AppStepper
                :model-value="String(serverDraft.port)"
                :min="1024"
                :max="65535"
                :disabled="!serverDraft.enabled || busy"
                @update:model-value="serverDraft.port = Number($event)"
              />
            </div>
          </SettingsRow>
          <SettingsRow
            label="Use HTTPS (SSL)"
            sub="Self-signed certificate; browsers warn on first connect."
            :dim="!serverDraft.enabled"
          >
            <v-switch
              v-model="serverDraft.ssl"
              color="accent"
              density="compact"
              hide-details
              :disabled="!serverDraft.enabled || busy"
            />
          </SettingsRow>
        </SettingsTwoCol>

        <!-- Saved-on but the listener can't bind without an owner login. -->
        <div v-if="server.enabled && !accountClaimed" class="settings-error">
          Remote access is enabled but not active yet — set an owner login below
          to start it.
        </div>

        <!-- First-run owner setup. -->
        <div v-if="needsOwnerSetup" class="owner-setup">
          <div class="owner-setup-desc">
            Remote devices sign in with an owner username and password. Set one
            now — until you do, PixlStash keeps the network listener off so
            another device on your network can't claim your library.
          </div>
          <input
            v-model="ownerUsername"
            type="text"
            class="owner-input"
            placeholder="Username"
            autocomplete="username"
            :disabled="busy"
          />
          <div class="owner-password-field">
            <input
              v-model="ownerPassword"
              :type="ownerShowPassword ? 'text' : 'password'"
              class="owner-input"
              placeholder="Password (min 8 characters)"
              autocomplete="new-password"
              :disabled="busy"
              @keydown.enter.prevent="applyServer"
            />
            <button
              type="button"
              class="owner-password-toggle"
              :aria-label="
                ownerShowPassword ? 'Hide password' : 'Show password'
              "
              @click="ownerShowPassword = !ownerShowPassword"
            >
              <v-icon size="18">{{
                ownerShowPassword ? "mdi-eye-off" : "mdi-eye"
              }}</v-icon>
            </button>
          </div>
          <div v-if="ownerError" class="settings-error">{{ ownerError }}</div>
        </div>

        <div class="compute-apply-row">
          <div v-if="server.enabled && accountClaimed" class="server-urls">
            <template v-if="server.urls.length">
              Reachable at:
              <span v-for="url in server.urls" :key="url" class="server-url">
                {{ url }}
              </span>
            </template>
            <template v-else>
              Reachable on this machine's network address, port
              {{ server.port }}.
            </template>
          </div>
          <AppButton
            variant="primary_green"
            size="sm"
            class="compute-apply-btn"
            :disabled="
              busy || (needsOwnerSetup ? !ownerFormValid : !serverDirty)
            "
            @click="applyServer"
          >
            {{
              needsOwnerSetup
                ? "Create login &amp; enable"
                : "Apply &amp; restart"
            }}
          </AppButton>
        </div>
      </template>
      <div v-else class="compute-loading">Loading…</div>
    </SettingsSection>

    <SettingsSection title="Desktop">
      <SettingsTwoCol>
        <SettingsRow
          label="Hide to tray on close"
          sub="Keep PixlStash (and any remote server) running in the background when you close the window. Reopen it from the tray icon."
        >
          <v-switch
            :model-value="hideToTrayOnClose"
            color="accent"
            density="compact"
            hide-details
            @update:model-value="setHideToTray($event)"
          />
        </SettingsRow>
        <SettingsRow
          label="Check for updates"
          sub="Checks once a day and shows a sidebar notice when a new version is out. Sends only your app version and install type, anonymously."
        >
          <v-switch
            v-model="checkForUpdatesModel"
            color="accent"
            density="compact"
            hide-details
          />
        </SettingsRow>
      </SettingsTwoCol>
    </SettingsSection>

    <!-- Pinned to the bottom of the Backend pane. -->
    <div class="compute-links">
      <AppButton
        variant="ghost"
        size="sm"
        icon-left="folder-image"
        @click="openLibraryFolder"
      >
        Open library folder
      </AppButton>
      <AppButton
        variant="ghost"
        size="sm"
        icon-left="text-box-outline"
        @click="showLogs"
      >
        Show server logs
      </AppButton>
    </div>
  </div>

  <!-- ── Compute tab: acceleration runtime ──────────────────────────────── -->
  <div v-else>
    <SettingsSection
      title="Compute acceleration"
      desc="PixlStash includes a built-in runtime that works out of the box. If this machine has a discrete GPU, add acceleration for faster tagging and search. Switching the runtime restarts the local server."
      first
    >
      <template v-if="state">
        <SettingsRow
          :label="activeLabel"
          :sub="
            state.bundled.active
              ? 'Built-in runtime · active'
              : 'GPU acceleration · active'
          "
        >
          <AppButton
            v-if="!state.bundled.active"
            variant="ghost"
            size="sm"
            :disabled="busy"
            @click="useBuiltIn"
          >
            Use built-in
          </AppButton>
        </SettingsRow>

        <SettingsRow
          v-for="item in state.items"
          :key="item.accel"
          :label="item.label"
          :sub="subFor(item)"
        >
          <AppButton
            v-if="!item.installed"
            variant="primary_green"
            size="sm"
            :disabled="busy"
            @click="install(item.accel)"
          >
            {{ item.recommended ? "Install (recommended)" : "Install" }}
          </AppButton>
          <template v-else>
            <AppButton
              v-if="!item.active"
              variant="primary_green"
              size="sm"
              :disabled="busy"
              @click="use(item.accel)"
            >
              Use
            </AppButton>
            <AppButton
              variant="ghost"
              size="sm"
              :disabled="busy"
              @click="remove(item.accel)"
            >
              Remove
            </AppButton>
          </template>
        </SettingsRow>

        <div v-if="!state.items.length" class="compute-note">
          No discrete GPU detected — the built-in runtime is the best fit for
          this machine.
        </div>

        <SettingsRow
          v-if="state.items.length && backendLocation"
          label="Install location"
          :sub="backendLocation"
        >
          <AppButton
            variant="ghost"
            size="sm"
            :disabled="busy"
            @click="changeLocation"
          >
            Change…
          </AppButton>
        </SettingsRow>

        <div v-if="progress" class="compute-progress">
          <v-progress-linear
            :indeterminate="!(progress.fraction >= 0)"
            :model-value="progress.fraction >= 0 ? progress.fraction * 100 : 0"
            color="accent"
            height="6"
            rounded
          />
          <div class="compute-note">{{ progress.message }}</div>
        </div>

        <div v-if="error" class="settings-error">{{ error }}</div>
      </template>
      <div v-else class="compute-loading">Loading…</div>
    </SettingsSection>
  </div>
</template>

<style scoped>
.server-port-control {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.server-port-warning {
  cursor: help;
}

/* The "Reachable at …" line and the Apply button share one row: text on the
   left (truncating if long), button pinned right, both on a single line. */
.compute-apply-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-2);
}

.compute-apply-btn {
  margin-left: auto;
  flex-shrink: 0;
}

/* Fill the pane height so the links can be pushed to the bottom. */
.compute-backend {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.compute-links {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
  /* Pinned to the bottom of the Backend pane. */
  margin-top: auto;
  padding-top: var(--space-5);
}

.compute-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-snug);
}

.compute-loading {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.compute-progress {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* Reserve two lines so the layout doesn't jump as the message length changes. */
.compute-progress .compute-note {
  min-height: 2.4em;
}

.server-urls {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.server-url {
  font-family: var(--font-mono);
  color: rgb(var(--v-theme-on-surface));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.owner-setup {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: var(--radius-md);
}

.owner-setup-desc {
  margin-bottom: var(--space-1);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-snug);
}

.owner-input {
  width: 100%;
  padding: var(--space-3) var(--space-3);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.28);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: inherit;
  outline: none;
}

.owner-input:focus {
  border-color: rgb(var(--v-theme-primary));
}

.owner-password-field {
  position: relative;
  display: flex;
  align-items: center;
}

.owner-password-field .owner-input {
  padding-right: 40px;
}

.owner-password-toggle {
  position: absolute;
  right: 6px;
  display: inline-flex;
  align-items: center;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.7);
  cursor: pointer;
  padding: var(--space-2);
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: var(--text-xs);
  white-space: pre-wrap;
}
</style>
