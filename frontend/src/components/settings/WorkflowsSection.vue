<script setup>
import { ref, watch } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import {
  listWorkflows,
  deleteWorkflow as deleteWorkflowRequest,
  importWorkflow,
} from "../../api/comfyui";
import { getUserConfig, patchUserConfig } from "../../api/config";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsChipGrid from "./SettingsChipGrid.vue";
import SettingsChip from "./SettingsChip.vue";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  open: { type: Boolean, default: false },
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

// ── Workflow import state ────────────────────────────────────────────────────
const workflowImportInputRef = ref(null);
const workflowImportError = ref("");

// ── Saved workflow list ──────────────────────────────────────────────────────
const workflowList = ref([]);
const workflowListLoading = ref(false);
const workflowListError = ref("");

function resetForm() {
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  comfyuiConfigDialogOpen.value = false;
  workflowImportError.value = "";
  workflowListError.value = "";
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
        return;
      }
    }
    comfyuiHost.value = "";
    comfyuiPort.value = "";
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
  comfyuiUrlLoading.value = true;
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  const host = String(comfyuiEditHost.value || "").trim();
  const port = String(comfyuiEditPort.value || "").trim();
  // Empty host is treated as "not configured" - save null.
  if (!host) {
    try {
      await patchUserConfig({ comfyui_url: null });
      comfyuiHost.value = "";
      comfyuiPort.value = "";
      emit("update:comfyui-configured", false);
      comfyuiConfigDialogOpen.value = false;
    } catch (e) {
      comfyuiUrlError.value =
        errorDetail(e) ||
        e?.message ||
        "Failed to update ComfyUI URL.";
    } finally {
      comfyuiUrlLoading.value = false;
    }
    return;
  }
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
    emit("update:comfyui-configured", false);
    comfyuiConfigDialogOpen.value = false;
  } catch (e) {
    comfyuiUrlError.value =
      errorDetail(e) || e?.message || "Failed to clear ComfyUI URL.";
  } finally {
    comfyuiUrlLoading.value = false;
  }
}

async function fetchWorkflowList() {
  workflowListLoading.value = true;
  workflowListError.value = "";
  try {
    const body = await listWorkflows();
    workflowList.value = Array.isArray(body?.workflows) ? body.workflows : [];
  } catch {
    workflowListError.value = "Failed to load workflows.";
  } finally {
    workflowListLoading.value = false;
  }
}

async function deleteWorkflow(workflow) {
  if (!workflow?.name) return;
  const confirmed = window.confirm(
    `Delete workflow '${workflow.display_name || workflow.name}'?`,
  );
  if (!confirmed) return;
  try {
    await deleteWorkflowRequest(workflow.name);
    await fetchWorkflowList();
  } catch (e) {
    workflowListError.value = errorDetail(e) || "Failed to delete workflow.";
  }
}

function openWorkflowImport() {
  workflowImportError.value = "";
  workflowImportInputRef.value?.click();
}

// The file is stored as it is: inputs are found from the graph, never mapped
// here. A copy of a stored workflow is matched to it, and a name already taken
// by a different workflow keeps both.
async function handleWorkflowFileChange(event) {
  const file = event?.target?.files?.[0];
  if (!file) return;
  workflowImportError.value = "";
  try {
    let workflow;
    try {
      workflow = JSON.parse(await file.text());
    } catch {
      workflowImportError.value = "Failed to parse workflow JSON.";
      return;
    }
    await importWorkflow({
      name: file.name.replace(/\.json$/i, ""),
      workflow,
      keepBoth: true,
    });
    await fetchWorkflowList();
  } catch (e) {
    workflowImportError.value = errorDetail(e) || "Failed to import workflow.";
  } finally {
    event.target.value = "";
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
    fetchWorkflowList();
  },
  { immediate: true },
);
</script>

<template>
  <div>
    <SettingsSection
      title="ComfyUI Host"
      desc="Configure the local ComfyUI server used for workflows."
      first
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
    </SettingsSection>

    <SettingsSection title="Import Workflow">
      <div class="wf-action-row">
        <div class="wf-import-line">
          Import a ComfyUI workflow JSON, UI or API format, as it is.
        </div>
        <AppButton
          class="wf-action-btn"
          variant="primary"
          size="sm"
          icon-left="tray-arrow-down"
          @click="openWorkflowImport"
        >
          Import Workflow
        </AppButton>
      </div>
      <div v-if="workflowImportError" class="wf-error">
        {{ workflowImportError }}
      </div>
    </SettingsSection>

    <SettingsSection
      title="Saved Workflows"
      desc="Manage your saved ComfyUI workflows."
    >
      <div v-if="workflowListLoading" class="wf-status">
        Loading workflows...
      </div>
      <div v-else-if="workflowListError" class="wf-error">
        {{ workflowListError }}
      </div>
      <SettingsChipGrid v-else empty="No workflows saved yet.">
        <template v-for="workflow in workflowList" :key="workflow.name">
          <SettingsChip
            v-if="workflow.source !== 'built-in'"
            :label="workflow.display_name || workflow.name"
            :meta="
              (workflow.valid
                ? `valid ${workflow.workflow_type || 'i2i'}`
                : 'invalid') + (workflow.flagged ? ' · check inputs' : '')
            "
            :meta-color="workflow.valid ? '' : 'rgb(var(--v-theme-error))'"
            @remove="deleteWorkflow(workflow)"
          />
          <div v-else class="wf-chip wf-chip--readonly">
            <span class="wf-chip__label">
              {{ workflow.display_name || workflow.name }}
            </span>
            <span
              class="wf-chip__meta"
              :style="
                workflow.valid ? null : { color: 'rgb(var(--v-theme-error))' }
              "
            >
              {{
                workflow.valid
                  ? `valid ${workflow.workflow_type || "i2i"}`
                  : "invalid"
              }}
            </span>
          </div>
        </template>
      </SettingsChipGrid>
    </SettingsSection>

    <input
      ref="workflowImportInputRef"
      type="file"
      accept="application/json"
      style="display: none"
      @change="handleWorkflowFileChange"
    />

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
/* ── Action rows - readout/description on the left, a fixed-width action button
   pinned right so the two section buttons line up vertically. ─────────────── */
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

/* ── Import Workflow descriptive line ─────────────────────────────────────── */
.wf-import-line {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-snug);
}

/* ── Status / error helper text ───────────────────────────────────────────── */
.wf-status {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.wf-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  margin-top: var(--space-2);
}

/* ── Built-in workflow chip (no remove control) - matches SettingsChip look ── */
.wf-chip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
}

.wf-chip__label {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wf-chip__meta {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
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
