<script setup>
// Links the saved ComfyUI to this library: `POST /comfyui/link` mints a
// full-access token and writes it, with PixlStash's URL, into ComfyUI's
// settings. It posts the moment the dialog opens and renders the reply's four
// steps; a step that failed or needs the person carries its fix inline.
//
// When the nodes are missing and the reply says `can_install_pack`, the nodes
// step offers to install the copy PixlStash ships, then polls
// `GET /comfyui/pixlstash-node` until ComfyUI is back with them loaded and
// links again by itself.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { VIcon, VProgressCircular } from "vuetify/components";
import {
  getPixlstashNode,
  installComfyuiPack,
  linkComfyui,
} from "../../api/comfyui";
import { errorDetail } from "../../utils/apiError";
import {
  PIXLSTASH_PACK_INSTALL,
  PIXLSTASH_PACK_URL,
} from "../../utils/runReasons";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["close"]);

const STEPS = [
  { id: "reach", name: "Reach ComfyUI" },
  { id: "nodes", name: "PixlStash nodes" },
  { id: "link", name: "Link the two apps" },
  { id: "check", name: "Check both directions" },
];

const STATE_ICON = {
  done: "mdi-check-circle",
  failed: "mdi-alert-rhombus-outline",
  needs_you: "mdi-alert-outline",
  not_run: "mdi-circle-outline",
};

const REMOTE_REASONS = ["remote_access_off", "https_off"];

const POLL_EVERY_MS = 2000;
const POLL_FOR_MS = 120000;

const pending = ref(false);
const reply = ref(null);
const requestError = ref("");
const restarting = ref(false);
const restartError = ref("");
// The install's progress, or null when no install is under way. `phase` is
// `installing`, `waiting` (for ComfyUI to come back with the nodes),
// `timeout` or `failed`.
const install = ref(null);
const showManual = ref(false);

const linked = computed(() => reply.value?.linked === true);
const canInstall = computed(() => reply.value?.can_install_pack === true);
const replaced = computed(() => install.value?.replaced || []);

// The nodes row while an install runs, keyed on the phase: what the row says
// and which fix it carries (`reason`).
function installRow(got) {
  const { phase, restart, error } = install.value;
  if (phase === "installing") {
    return { state: "working", detail: "Installing the PixlStash nodes…" };
  }
  if (phase === "failed") {
    return {
      state: "needs_you",
      detail: got.detail || null,
      reason: "install_failed",
      error,
    };
  }
  if (phase === "timeout") {
    return {
      state: "needs_you",
      detail:
        "ComfyUI did not come back with the nodes. Restart it, then Try again.",
      reason: "install_timeout",
    };
  }
  if (restart === "manual") {
    return {
      state: "needs_you",
      detail: "Restart ComfyUI to load the nodes.",
      reason: "restart_manual",
    };
  }
  return { state: "working", detail: "Restarting ComfyUI…" };
}

// While the POST is in flight row 1 is working and the rest wait.
const rows = computed(() =>
  STEPS.map((step, i) => {
    if (pending.value || !reply.value) {
      return {
        ...step,
        state: pending.value && i === 0 ? "working" : "not_run",
        detail: null,
        reason: null,
      };
    }
    const got = reply.value.steps?.find((s) => s.id === step.id) || {};
    if (step.id === "nodes" && install.value) {
      return { ...step, reason: null, ...installRow(got) };
    }
    return {
      ...step,
      state: got.state || "not_run",
      detail: got.detail || null,
      reason: got.reason || null,
    };
  }),
);

// Read per instance, not at module load, so the bridge is looked up where the
// dialog actually runs.
function desktopBridge() {
  const d = typeof window !== "undefined" ? window.pixlstashDesktop : null;
  return d?.getServerSettings && d?.setServerSettings ? d : null;
}

async function link() {
  stopPolling();
  install.value = null;
  showManual.value = false;
  pending.value = true;
  requestError.value = "";
  restartError.value = "";
  try {
    reply.value = await linkComfyui();
  } catch (e) {
    reply.value = null;
    requestError.value =
      errorDetail(e) || e?.message || "Could not link ComfyUI.";
  } finally {
    pending.value = false;
  }
}

let pollTimer = null;
// Bumped on every stop, so a GET still in flight when the dialog closed (or a
// new poll began) cannot act on its answer.
let pollRun = 0;

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
  pollRun += 1;
}

// While ComfyUI restarts the GET fails or answers null; both mean "not yet".
function pollForNodes() {
  stopPolling();
  const run = pollRun;
  const deadline = Date.now() + POLL_FOR_MS;
  const tick = async () => {
    let loaded = false;
    try {
      loaded = (await getPixlstashNode())?.can_open_workflows === true;
    } catch (e) {
      console.debug("ComfyUI not answering yet while it restarts:", e);
    }
    if (run !== pollRun) return;
    if (loaded) {
      link();
    } else if (Date.now() >= deadline) {
      stopPolling();
      install.value = { ...install.value, phase: "timeout" };
    } else {
      pollTimer = setTimeout(tick, POLL_EVERY_MS);
    }
  };
  pollTimer = setTimeout(tick, POLL_EVERY_MS);
}

async function installPack() {
  stopPolling();
  showManual.value = false;
  install.value = { phase: "installing" };
  const run = pollRun;
  try {
    const got = await installComfyuiPack();
    if (run !== pollRun) return;
    install.value = {
      phase: "waiting",
      restart: got?.restart,
      detail: got?.detail || null,
      replaced: got?.replaced || [],
    };
    pollForNodes();
  } catch (e) {
    if (run !== pollRun) return;
    install.value = {
      phase: "failed",
      error:
        errorDetail(e) ||
        e?.message ||
        "Could not install the PixlStash nodes.",
    };
  }
}

// The same bridge call Settings › Backend's Apply makes: it rewrites the
// server config and restarts the backend, which reloads this page. The port is
// kept as it is; only remote access and HTTPS change.
async function turnOnRemoteHttps() {
  const desktop = desktopBridge();
  if (!desktop) return;
  restarting.value = true;
  restartError.value = "";
  try {
    const current = await desktop.getServerSettings();
    await desktop.setServerSettings({
      enabled: true,
      port: current.port,
      ssl: true,
    });
  } catch (e) {
    restartError.value = e?.message || String(e);
  } finally {
    restarting.value = false;
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      stopPolling();
      return;
    }
    reply.value = null;
    link();
  },
  { immediate: true },
);

onBeforeUnmount(stopPolling);
</script>

<template>
  <AppDialog :open="open" title="Connect ComfyUI" @close="emit('close')">
    <ol
      class="cl-steps"
      role="status"
      :aria-busy="pending ? 'true' : 'false'"
      data-testid="comfyui-link-steps"
    >
      <li
        v-for="row in rows"
        :key="row.id"
        class="cl-step"
        :class="`cl-step--${row.state}`"
        :data-testid="`comfyui-link-step-${row.id}`"
        :data-state="row.state"
      >
        <v-progress-circular
          v-if="row.state === 'working'"
          class="cl-step__icon"
          indeterminate
          size="20"
          width="2"
        />
        <v-icon v-else class="cl-step__icon" size="20" aria-hidden="true">{{
          STATE_ICON[row.state]
        }}</v-icon>
        <div class="cl-step__body">
          <p class="cl-step__name">{{ row.name }}</p>
          <p v-if="row.detail" class="cl-step__detail">{{ row.detail }}</p>
          <p
            v-else-if="row.state === 'not_run'"
            class="cl-step__detail"
          >
            Waiting
          </p>

          <div
            v-if="row.state === 'failed' || row.state === 'needs_you'"
            class="cl-fix"
            :data-testid="`comfyui-link-fix-${row.reason || 'other'}`"
          >
            <p
              v-if="row.reason === 'pack_missing' && canInstall"
              class="cl-fix__text"
            >
              PixlStash installs the version of the nodes it is tested with. An
              older copy goes to the trash. ComfyUI restarts afterwards, which
              takes about a minute.
            </p>
            <p
              v-if="row.reason === 'install_failed'"
              class="cl-error"
              role="alert"
              data-testid="comfyui-link-install-error"
            >
              {{ row.error }}
            </p>
            <p
              v-if="row.reason === 'restart_manual' && install.detail"
              class="cl-fix__text"
            >
              {{ install.detail }}
            </p>
            <p
              v-if="
                (row.reason === 'pack_missing' && !canInstall) ||
                ((row.reason === 'pack_missing' ||
                  row.reason === 'install_failed') &&
                  showManual)
              "
              class="cl-fix__text"
              data-testid="comfyui-link-manual-steps"
            >
              <a
                class="cl-link"
                :href="PIXLSTASH_PACK_URL"
                target="_blank"
                rel="noopener noreferrer"
                >ComfyUI-PixlStash</a
              >
              is needed. {{ PIXLSTASH_PACK_INSTALL }}
            </p>
            <template v-else-if="REMOTE_REASONS.includes(row.reason)">
              <p class="cl-fix__text">
                ComfyUI's own page stays plain HTTP, so anyone on your network
                who opens it can see the key.
              </p>
              <p v-if="!desktopBridge()" class="cl-fix__text">
                Turn on remote access with HTTPS in PixlStash's server
                settings, then try again.
              </p>
              <p v-else-if="restarting" class="cl-fix__text">
                PixlStash restarts with remote access on. Press Try again once
                it is back.
              </p>
            </template>
            <p
              v-else-if="row.reason === 'password_missing'"
              class="cl-fix__text"
            >
              Set an owner password under Account first.
            </p>
            <p v-if="restartError" class="cl-error" role="alert">
              {{ restartError }}
            </p>
            <div class="cl-fix__actions">
              <AppButton
                v-if="REMOTE_REASONS.includes(row.reason) && desktopBridge()"
                variant="primary"
                :loading="restarting"
                data-testid="comfyui-link-remote-https"
                @click="turnOnRemoteHttps"
              >
                Turn on remote access with HTTPS
              </AppButton>
              <AppButton
                v-if="row.reason === 'pack_missing' && canInstall"
                variant="primary"
                data-testid="comfyui-link-install"
                @click="installPack"
              >
                Install and restart ComfyUI
              </AppButton>
              <AppButton
                v-if="
                  ((row.reason === 'pack_missing' && canInstall) ||
                    row.reason === 'install_failed') &&
                  !showManual
                "
                variant="ghost"
                data-testid="comfyui-link-show-manual"
                @click="showManual = true"
              >
                Show manual steps
              </AppButton>
              <AppButton
                v-if="
                  !(row.reason === 'pack_missing' && canInstall && !showManual)
                "
                variant="secondary"
                :disabled="restarting"
                data-testid="comfyui-link-retry"
                @click="link"
              >
                Try again
              </AppButton>
            </div>
          </div>

          <div
            v-if="row.id === 'nodes' && replaced.length"
            class="cl-fix"
            data-testid="comfyui-link-replaced"
          >
            <p class="cl-fix__text">Moved to the trash:</p>
            <ul class="cl-paths">
              <li v-for="path in replaced" :key="path" class="cl-path">
                {{ path }}
              </li>
            </ul>
          </div>
        </div>
      </li>
    </ol>

    <div v-if="requestError" class="cl-fix">
      <p class="cl-error" role="alert" data-testid="comfyui-link-error">
        {{ requestError }}
      </p>
      <div class="cl-fix__actions">
        <AppButton
          variant="secondary"
          data-testid="comfyui-link-retry"
          @click="link"
        >
          Try again
        </AppButton>
      </div>
    </div>

    <template #footer>
      <AppButton
        v-if="linked"
        variant="primary"
        data-testid="comfyui-link-done"
        @click="emit('close')"
      >
        Done
      </AppButton>
      <AppButton
        v-else
        variant="secondary"
        data-testid="comfyui-link-close"
        @click="emit('close')"
      >
        {{ reply || requestError ? "Close" : "Cancel" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.cl-steps {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cl-step {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
}

.cl-step__icon {
  flex-shrink: 0;
}

.cl-step--done .cl-step__icon {
  color: rgb(var(--v-theme-surface-success));
}

.cl-step--failed .cl-step__icon {
  color: rgb(var(--v-theme-surface-error));
}

.cl-step--needs_you .cl-step__icon {
  color: rgb(var(--v-theme-surface-warning));
}

.cl-step--not_run .cl-step__icon {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cl-step__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cl-step__name {
  margin: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.cl-step__detail,
.cl-fix__text {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A waiting step stays muted as a whole; pending (working) never dims. */
.cl-step--not_run .cl-step__name {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-weight: var(--weight-regular);
}

.cl-fix {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

.cl-fix__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.cl-error {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-error));
}

.cl-paths {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cl-path {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  overflow-wrap: anywhere;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The node pack link's shape (ComfyuiHostSection.vue). */
.cl-link {
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.cl-link:hover {
  text-decoration-thickness: 2px;
}
</style>
