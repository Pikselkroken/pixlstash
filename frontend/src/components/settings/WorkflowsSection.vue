<script setup>
import { computed, ref, watch } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";

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
const workflowImportDialogOpen = ref(false);
const workflowImportError = ref("");
const workflowImportName = ref("");
const workflowImportPayload = ref(null);
const workflowImportInputs = ref([]);
const workflowImportOutputs = ref([]);
const workflowImportImageTarget = ref("");
const workflowImportCaptionTarget = ref("");
const workflowImportOutputTargets = ref([]);
const workflowImportSaving = ref(false);

// ── Saved workflow list ──────────────────────────────────────────────────────
const workflowList = ref([]);
const workflowListLoading = ref(false);
const workflowListError = ref("");

function resetForm() {
  comfyuiUrlError.value = "";
  comfyuiUrlSuccess.value = "";
  comfyuiConfigDialogOpen.value = false;
  workflowImportError.value = "";
  workflowImportName.value = "";
  workflowImportPayload.value = null;
  workflowImportInputs.value = [];
  workflowImportOutputs.value = [];
  workflowImportImageTarget.value = "";
  workflowImportCaptionTarget.value = "";
  workflowImportOutputTargets.value = [];
  workflowImportSaving.value = false;
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
  } catch (e) {
    return null;
  }
}

async function fetchComfyuiUrl() {
  try {
    const res = await apiClient.get("/users/me/config");
    const comfyUrl = String(res.data?.comfyui_url || "").trim();
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
  } catch (e) {
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
  // Empty host is treated as "not configured" — save null.
  if (!host) {
    try {
      await apiClient.patch("/users/me/config", { comfyui_url: null });
      comfyuiHost.value = "";
      comfyuiPort.value = "";
      emit("update:comfyui-configured", false);
      comfyuiConfigDialogOpen.value = false;
    } catch (e) {
      comfyuiUrlError.value =
        e?.response?.data?.detail ||
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
    await apiClient.patch("/users/me/config", { comfyui_url: nextUrl });
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
      e?.response?.data?.detail ||
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
    await apiClient.patch("/users/me/config", { comfyui_url: null });
    comfyuiHost.value = "";
    comfyuiPort.value = "";
    comfyuiEditHost.value = "";
    comfyuiEditPort.value = "";
    emit("update:comfyui-configured", false);
    comfyuiConfigDialogOpen.value = false;
  } catch (e) {
    comfyuiUrlError.value =
      e?.response?.data?.detail || e?.message || "Failed to clear ComfyUI URL.";
  } finally {
    comfyuiUrlLoading.value = false;
  }
}

async function fetchWorkflowList() {
  workflowListLoading.value = true;
  workflowListError.value = "";
  try {
    const res = await apiClient.get("/comfyui/workflows");
    workflowList.value = Array.isArray(res.data?.workflows)
      ? res.data.workflows
      : [];
  } catch (e) {
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
    await apiClient.delete(
      `/comfyui/workflows/${encodeURIComponent(workflow.name)}`,
    );
    await fetchWorkflowList();
  } catch (e) {
    workflowListError.value =
      e?.response?.data?.detail || "Failed to delete workflow.";
  }
}

function openWorkflowImport() {
  workflowImportError.value = "";
  workflowImportInputRef.value?.click();
}

async function handleWorkflowFileChange(event) {
  const file = event?.target?.files?.[0];
  if (!file) return;
  workflowImportError.value = "";
  workflowImportPayload.value = null;
  workflowImportInputs.value = [];
  workflowImportOutputs.value = [];
  workflowImportImageTarget.value = "";
  workflowImportCaptionTarget.value = "";
  workflowImportOutputTargets.value = [];
  workflowImportName.value = file.name.replace(/\.json$/i, "");
  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    const inputs = extractWorkflowInputs(payload);
    const outputs = extractWorkflowOutputs(payload);
    workflowImportPayload.value = payload;
    workflowImportInputs.value = inputs;
    workflowImportOutputs.value = outputs;
    if (!inputs.length) {
      workflowImportError.value =
        "No inputs found. This workflow may not be in prompt format.";
    }
    const { imageTarget, captionTarget } = guessWorkflowTargets(inputs);
    workflowImportImageTarget.value = imageTarget || "";
    workflowImportCaptionTarget.value = hasCaptionInputs(inputs)
      ? captionTarget || ""
      : "";
    workflowImportOutputTargets.value = guessWorkflowOutputTargets(
      payload,
      outputs,
    );
    workflowImportDialogOpen.value = true;
  } catch (e) {
    workflowImportError.value = "Failed to parse workflow JSON.";
  } finally {
    event.target.value = "";
  }
}

function isNodeDisabled(node) {
  if (!node || typeof node !== "object") return false;
  if (node.disabled === true || node.is_disabled === true) return true;
  if (node.flags && typeof node.flags === "object") {
    if (node.flags.disabled === true) return true;
  }
  return false;
}

function extractWorkflowInputs(payload) {
  const entries = [];
  if (!payload || typeof payload !== "object") return entries;

  const prompt =
    payload.prompt && typeof payload.prompt === "object"
      ? payload.prompt
      : null;
  if (prompt) {
    Object.entries(prompt).forEach(([nodeId, node]) => {
      if (isNodeDisabled(node)) return;
      const inputs =
        node?.inputs && typeof node.inputs === "object" ? node.inputs : null;
      if (!inputs) return;
      Object.entries(inputs).forEach(([key, value]) => {
        if (value == null) return;
        if (typeof value !== "string" && typeof value !== "number") return;
        const nodeType = node?.class_type || node?.type || "Node";
        entries.push({
          id: `prompt:${nodeId}:${key}`,
          label: `${nodeType} · ${key}`,
          type: "prompt",
          nodeId,
          inputKey: key,
          nodeType,
        });
      });
    });
  }

  if (!prompt && !Array.isArray(payload.nodes)) {
    const values = Object.values(payload);
    const looksLikeGraph =
      values.length > 0 &&
      values.every(
        (node) =>
          node &&
          typeof node === "object" &&
          node.inputs &&
          typeof node.inputs === "object" &&
          (node.class_type || node.type),
      );
    if (looksLikeGraph) {
      Object.entries(payload).forEach(([nodeId, node]) => {
        if (isNodeDisabled(node)) return;
        const nodeType = node?.class_type || node?.type || "Node";
        const inputs =
          node?.inputs && typeof node.inputs === "object" ? node.inputs : null;
        if (!inputs) return;
        Object.entries(inputs).forEach(([key, value]) => {
          if (value == null) return;
          if (typeof value !== "string" && typeof value !== "number") return;
          entries.push({
            id: `graph:${nodeId}:${key}`,
            label: `${nodeType} · ${key}`,
            type: "graph",
            nodeId,
            inputKey: key,
            nodeType,
          });
        });
      });
    }
  }

  if (Array.isArray(payload.nodes)) {
    payload.nodes.forEach((node, nodeIndex) => {
      if (isNodeDisabled(node)) return;
      const nodeType = node?.type || node?.class_type || "Node";
      if (node?.inputs && typeof node.inputs === "object") {
        Object.entries(node.inputs).forEach(([key, value]) => {
          if (value == null) return;
          if (typeof value !== "string" && typeof value !== "number") return;
          entries.push({
            id: `node:${nodeIndex}:${key}`,
            label: `${nodeType} · ${key}`,
            type: "node_input",
            nodeIndex,
            inputKey: key,
            nodeType,
          });
        });
      }
      if (Array.isArray(node?.widgets_values)) {
        node.widgets_values.forEach((value, widgetIndex) => {
          if (typeof value !== "string") return;
          entries.push({
            id: `widget:${nodeIndex}:${widgetIndex}`,
            label: `${nodeType} · Widget ${widgetIndex + 1}`,
            type: "widget",
            nodeIndex,
            widgetIndex,
            nodeType,
          });
        });
      }
    });
  }

  return entries;
}

function extractWorkflowOutputs(payload) {
  const entries = [];
  if (!payload || typeof payload !== "object") return entries;

  const prompt =
    payload.prompt && typeof payload.prompt === "object"
      ? payload.prompt
      : null;
  if (prompt) {
    Object.entries(prompt).forEach(([nodeId, node]) => {
      if (isNodeDisabled(node)) return;
      const nodeType = node?.class_type || node?.type || "Node";
      if (nodeType !== "SaveImage") return;
      entries.push({
        id: String(nodeId),
        label: `${nodeType} · ${nodeId}`,
        type: "prompt",
        nodeId,
        nodeType,
      });
    });
  }

  if (!prompt && !Array.isArray(payload.nodes)) {
    const values = Object.values(payload);
    const looksLikeGraph =
      values.length > 0 &&
      values.every(
        (node) =>
          node &&
          typeof node === "object" &&
          node.inputs &&
          typeof node.inputs === "object" &&
          (node.class_type || node.type),
      );
    if (looksLikeGraph) {
      Object.entries(payload).forEach(([nodeId, node]) => {
        if (isNodeDisabled(node)) return;
        const nodeType = node?.class_type || node?.type || "Node";
        if (nodeType !== "SaveImage") return;
        entries.push({
          id: String(nodeId),
          label: `${nodeType} · ${nodeId}`,
          type: "graph",
          nodeId,
          nodeType,
        });
      });
    }
  }

  if (Array.isArray(payload.nodes)) {
    payload.nodes.forEach((node, nodeIndex) => {
      if (isNodeDisabled(node)) return;
      const nodeType = node?.type || node?.class_type || "Node";
      if (nodeType !== "SaveImage") return;
      entries.push({
        id: String(nodeIndex),
        label: `${nodeType} · ${nodeIndex + 1}`,
        type: "node",
        nodeIndex,
        nodeType,
      });
    });
  }

  return entries;
}

function guessWorkflowTargets(entries) {
  const loadImageTarget = entries.find((entry) =>
    /loadimage/i.test(entry.nodeType || ""),
  );
  const imageTarget =
    loadImageTarget ||
    entries.find((entry) =>
      /image/i.test(entry.nodeType || entry.inputKey || entry.label || ""),
    );
  const captionTarget = entries.find((entry) =>
    /cliptextencode|prompt|text|caption/i.test(
      entry.nodeType || entry.inputKey || entry.label || "",
    ),
  );
  return {
    imageTarget: imageTarget?.id || "",
    captionTarget: captionTarget?.id || "",
  };
}

function hasCaptionInputs(entries) {
  return entries.some((entry) =>
    /cliptextencode|prompt|text|caption/i.test(
      entry.nodeType || entry.inputKey || entry.label || "",
    ),
  );
}

function guessWorkflowOutputTargets(payload, outputs) {
  const safeOutputs = outputs ?? [];
  const rawTargets =
    payload?.pixlstash_output_nodes ??
    payload?.pixlstash_output_node ??
    payload?.output_node_ids ??
    payload?.output_node_id ??
    null;
  const available = new Set(safeOutputs.map((entry) => entry.id));
  const normalizeTargets = (value) => {
    if (value == null) return [];
    const list = Array.isArray(value) ? value : [value];
    return list
      .map((item) => String(item))
      .filter((item) => !available.size || available.has(item));
  };
  const explicit = normalizeTargets(rawTargets);
  if (explicit.length) return explicit;
  return safeOutputs.map((entry) => entry.id);
}

function getWorkflowInputPreview(payload, targetId) {
  if (!payload || !targetId) return "";
  const entry = workflowImportInputs.value.find((item) => item.id === targetId);
  if (!entry) return "";
  if (entry.type === "prompt") {
    const node = payload.prompt?.[entry.nodeId];
    return node?.inputs?.[entry.inputKey] ?? "";
  }
  if (entry.type === "graph") {
    const node = payload?.[entry.nodeId];
    return node?.inputs?.[entry.inputKey] ?? "";
  }
  if (entry.type === "node_input") {
    const node = payload.nodes?.[entry.nodeIndex];
    return node?.inputs?.[entry.inputKey] ?? "";
  }
  if (entry.type === "widget") {
    const node = payload.nodes?.[entry.nodeIndex];
    if (!node?.widgets_values) return "";
    return node.widgets_values[entry.widgetIndex] ?? "";
  }
  return "";
}

function applyWorkflowPlaceholders(payload, imageTargetId, captionTargetId) {
  const cloned = JSON.parse(JSON.stringify(payload));
  const replacements = [{ id: imageTargetId, value: "{{image_path}}" }];
  if (captionTargetId) {
    replacements.push({ id: captionTargetId, value: "{{caption}}" });
  }
  replacements.forEach(({ id, value }) => {
    const entry = workflowImportInputs.value.find((item) => item.id === id);
    if (!entry) return;
    if (entry.type === "prompt") {
      if (!cloned.prompt || !cloned.prompt[entry.nodeId]) return;
      const inputs = cloned.prompt[entry.nodeId].inputs || {};
      inputs[entry.inputKey] = value;
      cloned.prompt[entry.nodeId].inputs = inputs;
      return;
    }
    if (entry.type === "graph") {
      if (!cloned[entry.nodeId] || !cloned[entry.nodeId].inputs) return;
      cloned[entry.nodeId].inputs[entry.inputKey] = value;
      return;
    }
    if (entry.type === "node_input") {
      const node = cloned.nodes?.[entry.nodeIndex];
      if (!node || !node.inputs) return;
      node.inputs[entry.inputKey] = value;
      return;
    }
    if (entry.type === "widget") {
      const node = cloned.nodes?.[entry.nodeIndex];
      if (!node || !Array.isArray(node.widgets_values)) return;
      node.widgets_values[entry.widgetIndex] = value;
    }
  });
  return cloned;
}

function applyWorkflowOutputTargets(payload, outputTargets) {
  if (!payload || typeof payload !== "object") return payload;
  const targets = Array.isArray(outputTargets)
    ? outputTargets.filter(Boolean).map((value) => String(value))
    : [];
  if (targets.length) {
    payload.pixlstash_output_nodes = targets;
    if (payload.pixlstash_output_node != null) {
      delete payload.pixlstash_output_node;
    }
  } else {
    if (payload.pixlstash_output_nodes != null) {
      delete payload.pixlstash_output_nodes;
    }
    if (payload.pixlstash_output_node != null) {
      delete payload.pixlstash_output_node;
    }
  }
  return payload;
}

async function confirmWorkflowImport() {
  if (!workflowImportPayload.value) return;
  const name = String(workflowImportName.value || "").trim();
  if (!name) {
    workflowImportError.value = "Workflow name is required.";
    return;
  }
  workflowImportSaving.value = true;
  workflowImportError.value = "";
  try {
    const listRes = await apiClient.get("/comfyui/workflows");
    const existing = Array.isArray(listRes.data?.workflows)
      ? listRes.data.workflows
      : [];
    const exists = existing.some(
      (workflow) =>
        workflow?.name === `${name}.json` || workflow?.name === name,
    );
    let overwrite = false;
    if (exists) {
      overwrite = window.confirm(`Workflow '${name}' exists. Overwrite it?`);
      if (!overwrite) {
        workflowImportSaving.value = false;
        return;
      }
    }

    const updated = applyWorkflowPlaceholders(
      workflowImportPayload.value,
      workflowImportImageTarget.value,
      workflowImportCaptionTarget.value,
    );
    const outputTargets = Array.isArray(workflowImportOutputTargets.value)
      ? workflowImportOutputTargets.value
      : [];
    const updatedWithOutputs = applyWorkflowOutputTargets(
      updated,
      outputTargets,
    );
    await apiClient.post("/comfyui/workflows/import", {
      name,
      workflow: updatedWithOutputs,
      overwrite,
    });
    workflowImportDialogOpen.value = false;
    await fetchWorkflowList();
  } catch (e) {
    workflowImportError.value =
      e?.response?.data?.detail || "Failed to import workflow.";
  } finally {
    workflowImportSaving.value = false;
  }
}

// ── Computed: select option lists and preview values ─────────────────────────
const workflowImageInputOptions = computed(() => [
  { title: "None (text-to-image)", value: "" },
  ...(workflowImportInputs.value || []).map((entry) => ({
    title: entry.label,
    value: entry.id,
  })),
]);

const workflowCaptionInputOptions = computed(() => [
  { title: "No caption", value: "" },
  ...workflowImageInputOptions.value,
]);

const workflowOutputNodeOptions = computed(() =>
  (workflowImportOutputs.value || []).map((entry) => ({
    title: entry.label,
    value: entry.id,
  })),
);

const workflowImportImagePreview = computed(() => {
  return getWorkflowInputPreview(
    workflowImportPayload.value,
    workflowImportImageTarget.value,
  );
});

const workflowImportCaptionPreview = computed(() => {
  return getWorkflowInputPreview(
    workflowImportPayload.value,
    workflowImportCaptionTarget.value,
  );
});

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
    <v-divider class="settings-section-divider" />
    <div class="settings-section">
      <div class="settings-section-title">ComfyUI Host</div>
      <div class="settings-section-desc">
        Configure the local ComfyUI server used for workflows.
      </div>
      <div class="settings-comfyui-display">
        <div class="settings-comfyui-row">
          <span class="settings-comfyui-label">Host</span>
          <span class="settings-comfyui-value">{{
            comfyuiHost || "Not configured"
          }}</span>
        </div>
        <div class="settings-comfyui-row">
          <span class="settings-comfyui-label">Port</span>
          <span class="settings-comfyui-value">{{ comfyuiPort || "—" }}</span>
        </div>
        <v-btn
          variant="outlined"
          color="primary"
          size="small"
          class="settings-action-btn"
          style="margin-top: 8px"
          @click="openComfyuiConfigDialog"
        >
          Configure
        </v-btn>
      </div>
    </div>
    <v-divider class="settings-section-divider" />
    <div class="settings-section">
      <div class="settings-section-title">Import Workflow</div>
      <div class="settings-section-desc">
        Import a ComfyUI workflow JSON and map its image/caption inputs.
      </div>
      <div class="settings-form">
        <v-btn
          variant="outlined"
          color="primary"
          class="settings-action-btn"
          @click="openWorkflowImport"
        >
          Import Workflow
        </v-btn>
        <div v-if="workflowImportError" class="settings-error">
          {{ workflowImportError }}
        </div>
        <div v-else class="settings-success">
          {{ "\u00a0" }}
        </div>
      </div>
    </div>
    <v-divider class="settings-section-divider" />
    <div class="settings-section">
      <div class="settings-section-title">Saved Workflows</div>
      <div class="settings-section-desc">
        Manage your saved ComfyUI workflows.
      </div>
      <div class="settings-form">
        <div v-if="workflowListLoading" class="settings-success">
          Loading workflows...
        </div>
        <div v-else-if="workflowListError" class="settings-error">
          {{ workflowListError }}
        </div>
        <div v-else-if="!workflowList.length" class="settings-success">
          No workflows saved yet.
        </div>
        <div v-else class="settings-tag-list">
          <div
            v-for="workflow in workflowList"
            :key="workflow.name"
            class="settings-tag-chip settings-tag-chip--row"
          >
            <span class="settings-tag-label">
              {{ workflow.display_name || workflow.name }}
            </span>
            <span
              class="settings-tag-label"
              :style="{ opacity: workflow.valid ? 0.65 : 1 }"
            >
              {{
                workflow.valid
                  ? `valid ${workflow.workflow_type || "i2i"}`
                  : "invalid"
              }}
            </span>
            <v-btn
              v-if="workflow.source !== 'built-in'"
              icon
              variant="text"
              class="settings-tag-delete"
              @click="deleteWorkflow(workflow)"
            >
              <v-icon size="16">mdi-delete</v-icon>
            </v-btn>
          </div>
        </div>
      </div>
    </div>

    <input
      ref="workflowImportInputRef"
      type="file"
      accept="application/json"
      style="display: none"
      @change="handleWorkflowFileChange"
    />

    <v-dialog v-model="workflowImportDialogOpen" max-width="640">
      <v-card class="settings-token-dialog">
        <v-card-title class="settings-dialog-title">
          Import Workflow
        </v-card-title>
        <v-card-text class="settings-dialog-body">
          <v-text-field
            v-model="workflowImportName"
            label="Workflow name"
            density="compact"
            variant="filled"
          />
          <v-select
            v-model="workflowImportImageTarget"
            :items="workflowImageInputOptions"
            item-title="title"
            item-value="value"
            label="Image input"
            density="compact"
            variant="filled"
          />
          <div v-if="workflowImportImagePreview" class="settings-token-warning">
            Current value: {{ workflowImportImagePreview }}
          </div>
          <v-select
            v-model="workflowImportCaptionTarget"
            :items="workflowCaptionInputOptions"
            item-title="title"
            item-value="value"
            label="Caption input"
            density="compact"
            variant="filled"
          />
          <div
            v-if="workflowImportCaptionPreview"
            class="settings-token-warning"
          >
            Current value: {{ workflowImportCaptionPreview }}
          </div>
          <v-select
            v-model="workflowImportOutputTargets"
            :items="workflowOutputNodeOptions"
            item-title="title"
            item-value="value"
            label="SaveImage outputs"
            multiple
            density="compact"
            variant="filled"
            :disabled="!workflowOutputNodeOptions.length"
          />
          <div
            v-if="!workflowOutputNodeOptions.length"
            class="settings-token-warning"
          >
            No SaveImage nodes detected. Outputs will be auto-detected.
          </div>
          <div v-else class="settings-success">
            Leave empty to use all SaveImage nodes.
          </div>
          <div v-if="workflowImportError" class="settings-error">
            {{ workflowImportError }}
          </div>
        </v-card-text>
        <v-card-actions class="settings-dialog-actions">
          <v-spacer />
          <v-btn variant="text" @click="workflowImportDialogOpen = false">
            Cancel
          </v-btn>
          <v-btn
            variant="outlined"
            color="primary"
            :loading="workflowImportSaving"
            :disabled="workflowImportSaving"
            @click="confirmWorkflowImport"
          >
            Import
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="comfyuiConfigDialogOpen" max-width="420">
      <v-card class="settings-token-dialog">
        <v-card-title class="settings-dialog-title"
          >Configure ComfyUI</v-card-title
        >
        <v-card-text class="settings-dialog-body">
          <v-text-field
            v-model="comfyuiEditHost"
            label="Host"
            density="compact"
            variant="filled"
            :disabled="comfyuiUrlLoading"
            placeholder="e.g. 127.0.0.1"
          />
          <v-text-field
            v-model="comfyuiEditPort"
            label="Port"
            density="compact"
            variant="filled"
            :disabled="comfyuiUrlLoading"
            placeholder="e.g. 8188"
            @keydown.enter.prevent="saveComfyuiUrl"
          />
          <div v-if="comfyuiUrlError" class="settings-error">
            {{ comfyuiUrlError }}
          </div>
          <div v-else-if="comfyuiUrlSuccess" class="settings-success">
            {{ comfyuiUrlSuccess }}
          </div>
        </v-card-text>
        <v-card-actions class="settings-dialog-actions">
          <v-btn
            variant="outlined"
            color="error"
            :loading="comfyuiUrlLoading"
            :disabled="comfyuiUrlLoading"
            @click="clearComfyuiUrl"
          >
            Clear
          </v-btn>
          <v-spacer />
          <v-btn
            variant="text"
            :disabled="comfyuiUrlLoading"
            @click="comfyuiConfigDialogOpen = false"
          >
            Cancel
          </v-btn>
          <v-btn
            variant="outlined"
            color="primary"
            :loading="comfyuiUrlLoading"
            :disabled="comfyuiUrlLoading"
            @click="saveComfyuiUrl"
          >
            Save
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.settings-section-divider {
  margin: 4px 0 8px;
}

.settings-section {
  display: flex;
  line-height: 1;
  flex-direction: column;
  gap: 6px;
}

.settings-section-title {
  font-weight: 600;
}

.settings-section-desc {
  font-size: 0.92em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.9em;
}

.settings-success {
  color: rgb(var(--v-theme-accent));
  font-size: 0.9em;
}

.settings-action-btn {
  align-self: flex-start;
  background-color: rgb(var(--v-theme-primary)) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}

.settings-action-btn:hover {
  background-color: rgb(var(--v-theme-accent)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}

.settings-comfyui-display {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

.settings-comfyui-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.93em;
}

.settings-comfyui-label {
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.7);
  min-width: 36px;
}

.settings-comfyui-value {
  color: rgb(var(--v-theme-on-surface));
  font-family: monospace;
}

.settings-tag-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.settings-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.settings-tag-chip--row {
  width: 100%;
  justify-content: space-between;
  padding-right: 4px;
}

.settings-tag-label {
  font-size: 1em;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

.settings-tag-delete {
  color: rgba(var(--v-theme-on-surface), 0.65);
  min-width: 0;
  height: 12px;
  width: 12px;
  padding: 2;
}

.settings-tag-delete:hover {
  color: rgba(var(--v-theme-error), 0.9);
  min-width: 0;
  padding: 2;
}

.settings-token-dialog {
  padding-bottom: 8px;
}

.settings-dialog-title {
  font-weight: 700;
  font-size: 1.2rem;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.settings-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 8px;
}

.settings-dialog-actions {
  padding-top: 0;
}

.settings-token-warning {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-bottom: 6px;
}
</style>
