<script setup>
// The ComfyUI server that workflows run on. Workflows themselves are added by
// dropping a file on the Workflows view or into the watched workflows folder.
import { ref, watch } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import { getUserConfig, patchUserConfig } from "../../api/config";
import { getPixlstashNode } from "../../api/comfyui";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import SettingsSection from "./SettingsSection.vue";
import { errorDetail } from "../../utils/apiError";
import {
  PIXLSTASH_PACK_INSTALL,
  PIXLSTASH_PACK_URL,
} from "../../utils/runReasons";
import { useFilterStore } from "../../stores/useFilterStore";

const props = defineProps({
  open: { type: Boolean, default: false },
  // First in its pane only where the acceleration section above it is absent.
  first: { type: Boolean, default: false },
});

const emit = defineEmits(["update:comfyui-configured"]);

// ── ComfyUI host/port ────────────────────────────────────────────────────────
const comfyuiHost = ref("");
const comfyuiPort = ref("");
const comfyuiEditHost = ref("");
const comfyuiEditPort = ref("");
const comfyuiConfigDialogOpen = ref(false);
const comfyuiUrlLoading = ref(false);
const comfyuiUrlError = ref("");
const comfyuiUrlSuccess = ref("");

// Whether the configured ComfyUI has the ComfyUI-PixlStash node pack: `true`,
// `false`, or `null` when there is no ComfyUI to ask or it did not answer.
// Only a definite answer is shown, as the Workflows tab's Open in ComfyUI does.
// The check looks for the pack's `open_workflow.js`, so an old install reads
// as `false` too, and the sentence says so.
const packInstalled = ref(null);
let packCheck = 0;

async function checkPixlstashPack() {
  const check = ++packCheck;
  packInstalled.value = null;
  if (!comfyuiHost.value) return;
  try {
    const { can_open_workflows: canOpen } = await getPixlstashNode();
    if (check === packCheck) packInstalled.value = canOpen ?? null;
  } catch (e) {
    console.warn("[settings] could not ask ComfyUI about ComfyUI-PixlStash", e);
  }
}

function resetForm() {
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  comfyuiConfigDialogOpen.value = false;
}

function parseComfyuiUrl(value) {
  if (!value) return null;
  try {
    const normalized = value.includes("://") ? value : `http://${value}`;
    const parsed = new URL(normalized);
    const host = parsed.hostname || "127.0.0.1";
    const port = parsed.port || "8188";
    return { host, port };
  } catch {
    return null;
  }
}

async function fetchComfyuiUrl() {
  try {
    const cfg = await getUserConfig();
    const comfyUrl = String(cfg?.comfyui_url || "").trim();
    if (comfyUrl) {
      const parsed = parseComfyuiUrl(comfyUrl);
      if (parsed) {
        comfyuiHost.value = parsed.host;
        comfyuiPort.value = parsed.port;
        checkPixlstashPack();
        return;
      }
    }
    comfyuiHost.value = "";
    comfyuiPort.value = "";
    checkPixlstashPack();
  } catch {
    // Leave state untouched on failure.
  }
}

function openComfyuiConfigDialog() {
  comfyuiEditHost.value = comfyuiHost.value;
  comfyuiEditPort.value = comfyuiPort.value;
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  comfyuiConfigDialogOpen.value = true;
}

async function saveComfyuiUrl() {
  const host = String(comfyuiEditHost.value || "").trim();
  const port = String(comfyuiEditPort.value || "").trim();
  // Empty host is treated as "not configured" - save null.
  if (!host) return clearComfyuiUrl();
  comfyuiUrlLoading.value = true;
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  const portNumber = Number(port);
  if (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535) {
    comfyuiUrlError.value = "Port must be between 1 and 65535.";
    comfyuiUrlLoading.value = false;
    return;
  }
  const nextUrl = `http://${host}:${portNumber}/`;
  try {
    await patchUserConfig({ comfyui_url: nextUrl });
    comfyuiHost.value = host;
    comfyuiPort.value = String(portNumber);
    checkPixlstashPack();
    useFilterStore().comfyuiUrl = nextUrl;
    emit("update:comfyui-configured", true);
    comfyuiUrlSuccess.value = "Saved.";
    setTimeout(() => {
      if (comfyuiUrlSuccess.value === "Saved.") {
        comfyuiUrlSuccess.value = "";
        comfyuiConfigDialogOpen.value = false;
      }
    }, 1200);
  } catch (e) {
    comfyuiUrlError.value =
      errorDetail(e) ||
      e?.message ||
      "Failed to update ComfyUI URL.";
  } finally {
    comfyuiUrlLoading.value = false;
  }
}

async function clearComfyuiUrl() {
  comfyuiUrlLoading.value = true;
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  try {
    await patchUserConfig({ comfyui_url: null });
    comfyuiHost.value = "";
    comfyuiPort.value = "";
    comfyuiEditHost.value = "";
    comfyuiEditPort.value = "";
    checkPixlstashPack();
    useFilterStore().comfyuiUrl = "";
    emit("update:comfyui-configured", false);
    comfyuiConfigDialogOpen.value = false;
  } catch (e) {
    comfyuiUrlError.value =
      errorDetail(e) || e?.message || "Failed to clear ComfyUI URL.";
  } finally {
    comfyuiUrlLoading.value = false;
  }
}

// ── Lifecycle: fetch data when the parent dialog opens ───────────────────────
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    resetForm();
    if (isReadOnly.value) return;
    fetchComfyuiUrl();
  },
  { immediate: true },
);
</script>

<template>
  <div>
    <SettingsSection
      title="ComfyUI Host"
      desc="Configure the local ComfyUI server used for workflows."
      :first="first"
    >
      <div class="wf-action-row">
        <div class="wf-host-readout">
          <div class="wf-host-pair">
            <span class="wf-host-key">Host</span>
            <span class="wf-host-value">{{
              comfyuiHost || "Not configured"
            }}</span>
          </div>
          <div class="wf-host-pair">
            <span class="wf-host-key">Port</span>
            <span class="wf-host-value">{{ comfyuiPort || "—" }}</span>
          </div>
        </div>
        <AppButton
          class="wf-action-btn"
          variant="primary"
          size="sm"
          icon-left="cog-outline"
          @click="openComfyuiConfigDialog"
        >
          Configure Host
        </AppButton>
      </div>
      <!-- The live region is always there, so a line that arrives in it is
           announced; one inserted already filled often is not. -->
      <div role="status">
        <p
          v-if="packInstalled !== null"
          class="wf-pack-status"
          data-testid="comfyui-pack-status"
        >
          <a
            class="wf-pack-link"
            :href="PIXLSTASH_PACK_URL"
            target="_blank"
            rel="noopener noreferrer"
            >ComfyUI-PixlStash</a
          >
          node pack:
          <template v-if="packInstalled">installed.</template>
          <template v-else>
            not found, or too old. Opening workflows in ComfyUI and some recipes
            need it. {{ PIXLSTASH_PACK_INSTALL }}
          </template>
        </p>
      </div>
    </SettingsSection>

    <AppDialog
      :open="comfyuiConfigDialogOpen"
      title="Configure ComfyUI"
      size="sm"
      @close="comfyuiConfigDialogOpen = false"
    >
      <div class="wf-dialog-body">
        <AppInput
          v-model="comfyuiEditHost"
          label="Host"
          placeholder="e.g. 127.0.0.1"
          mono
          :disabled="comfyuiUrlLoading"
        />
        <AppInput
          v-model="comfyuiEditPort"
          label="Port"
          placeholder="e.g. 8188"
          mono
          :disabled="comfyuiUrlLoading"
          @enter="saveComfyuiUrl"
        />
        <div v-if="comfyuiUrlError" class="wf-error">
          {{ comfyuiUrlError }}
        </div>
        <div v-else-if="comfyuiUrlSuccess" class="wf-status">
          {{ comfyuiUrlSuccess }}
        </div>
      </div>
      <template #footer>
        <AppButton
          variant="danger"
          :disabled="comfyuiUrlLoading"
          @click="clearComfyuiUrl"
        >
          Clear
        </AppButton>
        <span class="wf-footer-spacer" />
        <AppButton
          variant="secondary"
          :disabled="comfyuiUrlLoading"
          @click="comfyuiConfigDialogOpen = false"
        >
          Cancel
        </AppButton>
        <AppButton
          variant="primary"
          :disabled="comfyuiUrlLoading"
          @click="saveComfyuiUrl"
        >
          Save
        </AppButton>
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
/* ── Action row - readout on the left, a fixed-width action button pinned
   right. ──────────────────────────────────────────────────────────────────── */
.wf-action-row {
  display: flex;
  align-items: center;
  gap: var(--space-5);
}

.wf-action-btn {
  margin-left: auto;
  flex-shrink: 0;
  width: 168px;
  justify-content: flex-start;
}

/* ── ComfyUI Host readout - mono host/port pairs, design-system tokens ─────── */
.wf-host-readout {
  display: flex;
  align-items: center;
  gap: var(--space-5);
}

.wf-host-pair {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.wf-host-key {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-weight: var(--weight-medium);
}

.wf-host-value {
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-mono);
}

/* ── Node pack status line, under the readout ─────────────────────────────── */
.wf-pack-status {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The plugin catalogue link's shape (BehaviourSection.vue). */
.wf-pack-link {
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.wf-pack-link:hover {
  text-decoration-thickness: 2px;
}

/* ── Status / error helper text ───────────────────────────────────────────── */
.wf-status {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.wf-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
  margin-top: var(--space-2);
}

/* ── Dialog body (inside AppDialog) ───────────────────────────────────────── */
.wf-dialog-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.wf-footer-spacer {
  flex: 1;
}
</style>
