<template>
  <div class="sidebar-section">
    <div
      class="section-header section-header--collapsible"
      @click="metadataCollapsed = !metadataCollapsed"
    >
      <span>Metadata</span>
      <v-icon size="16" style="opacity: 0.6">{{
        metadataCollapsed ? "mdi-chevron-right" : "mdi-chevron-down"
      }}</v-icon>
    </div>
    <template v-if="!metadataCollapsed">
      <div
        v-if="
          !metadataEntries.length &&
          !comfyMetadata &&
          !pictureInfoEntries.length
        "
        class="metadata-empty"
      >
        No metadata available
      </div>
      <div v-else class="metadata-tabbox">
        <div class="metadata-tab-strip">
          <button
            v-if="pictureInfoEntries.length"
            class="metadata-tab-btn"
            :class="{ active: metadataTab === 'info' }"
            @click="metadataTab = 'info'"
          >
            {{ infoHeaderLabel }}
          </button>
          <button
            v-if="comfyMetadata"
            class="metadata-tab-btn"
            :class="{ active: metadataTab === 'comfy' }"
            @click="metadataTab = 'comfy'"
          >
            ComfyUI
          </button>
        </div>
        <div
          v-if="metadataTab === 'info' && pictureInfoEntries.length"
          class="metadata-tab-panel"
        >
          <div class="metadata-info-grid">
            <div
              v-for="entry in pictureInfoEntries"
              :key="entry.label"
              :class="[
                'metadata-info-item',
                entry.fullWidth && 'metadata-info-item--full-width',
                entry.clickable && 'metadata-info-item--clickable',
              ]"
              :title="entry.fullWidth ? entry.value : undefined"
              @click="entry.clickable ? openSourceFileLocation() : undefined"
            >
              <div class="metadata-info-label">{{ entry.label }}</div>
              <div class="metadata-info-value">{{ entry.value }}</div>
            </div>
          </div>
        </div>
        <div
          v-if="metadataTab === 'comfy' && comfyMetadata"
          class="metadata-tab-panel metadata-comfy-panel"
        >
          <div class="metadata-comfy-subtitle">
            {{ comfyMetadata.summary }}
          </div>
          <div
            v-if="comfyMetadata.positive_prompt"
            class="metadata-comfy-field-group"
          >
            <div class="metadata-comfy-field-label">Prompt</div>
            <textarea
              class="metadata-comfy-textarea metadata-comfy-prompt"
              readonly
              :value="comfyMetadata.positive_prompt"
            ></textarea>
          </div>
          <div
            v-if="comfyMetadata.models?.length || comfyMetadata.loras?.length"
            class="metadata-comfy-chips-block"
          >
            <div
              v-if="comfyMetadata.models?.length"
              class="metadata-comfy-field-group"
            >
              <div class="metadata-comfy-field-label">Models</div>
              <div class="metadata-comfy-chip-row">
                <span
                  v-for="m in comfyMetadata.models"
                  :key="m"
                  class="metadata-comfy-chip"
                  :title="m"
                  >{{ m }}</span
                >
              </div>
            </div>
            <div
              v-if="comfyMetadata.loras?.length"
              class="metadata-comfy-field-group"
            >
              <div class="metadata-comfy-field-label">LoRAs</div>
              <div class="metadata-comfy-chip-row">
                <span
                  v-for="l in comfyMetadata.loras"
                  :key="l"
                  class="metadata-comfy-chip"
                  :title="l"
                  >{{ l }}</span
                >
              </div>
            </div>
          </div>
          <details v-if="comfyMetadata.workflow" class="metadata-comfy-details">
            <summary class="metadata-comfy-summary">
              <span class="metadata-comfy-summary-left">
                <span style="font-weight: 500; color: #fff">{{
                  comfyMetadata.isApiFormat
                    ? "API Workflow JSON"
                    : "Workflow JSON"
                }}</span>
              </span>
              <button
                v-if="!comfyMetadata.isApiFormat"
                class="metadata-comfy-workflow-action"
                type="button"
                @click.stop="copyMetadataValue(comfyMetadata.workflow)"
              >
                <v-icon size="14">mdi-content-copy</v-icon>
                Copy
              </button>
              <button
                class="metadata-comfy-workflow-action"
                type="button"
                @click.stop="downloadComfyWorkflow(comfyMetadata.workflow)"
              >
                <v-icon size="14">mdi-download</v-icon>
                Download
              </button>
            </summary>
            <textarea
              class="metadata-comfy-textarea"
              readonly
              :value="stringifyMetadata(comfyMetadata.workflow)"
            ></textarea>
          </details>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch } from "vue";
import { isSupportedVideoFile, getOverlayFormat } from "../../utils/media.js";
import { formatUserDate } from "../../utils/utils.js";
import { apiClient } from "../../utils/apiClient";
import { copyText } from "../../utils/clipboard";

const props = defineProps({
  image: { type: Object, default: null },
  comfyMetadata: { type: Object, default: null },
  dateFormat: { type: String, default: "locale" },
  backendUrl: { type: String, required: true },
  videoDuration: { type: Number, default: null },
});

const metadataCollapsed = ref(false);
const metadataTab = ref("info");

function parseMetadataValue(value) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return value;
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        return JSON.parse(trimmed);
      } catch (e) {
        return value;
      }
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => parseMetadataValue(item));
  }
  if (value && typeof value === "object") {
    const nested = {};
    Object.entries(value).forEach(([k, v]) => {
      nested[k] = parseMetadataValue(v);
    });
    return nested;
  }
  return value;
}

function buildMetadata(input) {
  if (!input || typeof input !== "object") return {};
  const output = {};
  Object.entries(input).forEach(([key, value]) => {
    output[key] = parseMetadataValue(value);
  });
  return output;
}

function stripComfyMetadata(input) {
  if (!input || typeof input !== "object") return {};
  const output = {};
  Object.entries(input).forEach(([key, value]) => {
    if (
      key === "workflow" ||
      key === "prompt" ||
      key === "comfyui_workflow" ||
      key === "comfyui_prompt"
    ) {
      return;
    }
    if (key === "png" && value && typeof value === "object") {
      const { workflow, prompt, ...rest } = value;
      if (Object.keys(rest).length) {
        output[key] = rest;
      }
      return;
    }
    if (key === "comfyui" && value && typeof value === "object") {
      const { workflow, prompt, ...rest } = value;
      if (Object.keys(rest).length) {
        output[key] = rest;
      }
      return;
    }
    output[key] = value;
  });
  return output;
}

function formatAspectRatio(width, height) {
  const gcd = (a, b) => (b === 0 ? a : gcd(b, a % b));
  const divisor = gcd(width, height);
  const ratioW = Math.round(width / divisor);
  const ratioH = Math.round(height / divisor);
  return `${ratioW}:${ratioH}`;
}

function formatMegabytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "";
  const total = Math.round(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const padded = (num) => String(num).padStart(2, "0");
  if (hours > 0) {
    return `${hours}:${padded(minutes)}:${padded(secs)}`;
  }
  return `${minutes}:${padded(secs)}`;
}

function stringifyMetadata(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (e) {
    return String(value);
  }
}

function isPrimitiveValue(value) {
  return (
    value === null ||
    value === undefined ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  );
}

async function copyMetadataValue(value) {
  const text = isPrimitiveValue(value)
    ? String(value)
    : stringifyMetadata(value);
  if (!text) return;
  const copied = await copyText(text);
  if (!copied) {
    console.warn("Failed to copy metadata value.");
  }
}

async function openSourceFileLocation() {
  if (!props.image?.id) return;
  try {
    await apiClient.post(
      `${props.backendUrl}/pictures/${props.image.id}/open-location`,
    );
  } catch {
    // silently ignore
  }
}

function downloadComfyWorkflow(workflow) {
  if (!workflow) return;
  const payload = stringifyMetadata(workflow);
  const blob = new Blob([payload], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "comfyui_workflow.json";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 100);
}

const metadataEntries = computed(() => {
  const base = buildMetadata(props.image?.metadata);
  const entries = Object.entries(stripComfyMetadata(base));
  return entries.map(([key, value]) => ({ key, value }));
});

const infoHeaderLabel = computed(() => {
  const format = props.image ? getOverlayFormat(props.image) : "";
  const isVideo = format ? isSupportedVideoFile(format) : false;
  return isVideo ? "Video information" : "Picture information";
});

const pictureInfoEntries = computed(() => {
  if (!props.image) return [];
  const entries = [];
  const fallbackW = Number(props.image.width || 0);
  const fallbackH = Number(props.image.height || 0);
  const width = fallbackW > 0 ? fallbackW : null;
  const height = fallbackH > 0 ? fallbackH : null;
  if (width && height) {
    entries.push({ label: "Size", value: `${width}×${height}` });
    const aspect = formatAspectRatio(width, height);
    if (aspect) entries.push({ label: "Aspect", value: aspect });
  }

  const sizeBytes =
    props.image.size_bytes ||
    props.image.sizeBytes ||
    props.image.file_size ||
    props.image.fileSize ||
    props.image.metadata?.size_bytes ||
    props.image.metadata?.file_size ||
    null;
  if (sizeBytes) {
    entries.push({ label: "MB", value: formatMegabytes(sizeBytes) });
  }

  const smartScoreValue =
    typeof props.image.smartScore === "number"
      ? props.image.smartScore
      : typeof props.image.smart_score === "number"
        ? props.image.smart_score
        : null;
  if (smartScoreValue != null) {
    entries.push({
      label: "Smart score",
      value: smartScoreValue.toFixed(2),
    });
  }

  const createdAt = props.image.created_at || props.image.createdAt;
  if (createdAt) {
    entries.push({
      label: "Created",
      value: formatUserDate(createdAt, props.dateFormat),
    });
  }

  const format = getOverlayFormat(props.image);
  if (format) {
    const isVideo = isSupportedVideoFile(format);
    entries.push({
      label: "Type",
      value: `${isVideo ? "Video" : "Image"} · ${format.toUpperCase()}`,
    });

    if (isVideo) {
      const frameCount =
        props.image.frame_count ||
        props.image.frames ||
        props.image.metadata?.frame_count ||
        props.image.metadata?.frames ||
        null;
      if (frameCount) {
        entries.push({ label: "Frames", value: String(frameCount) });
      }

      const durationSeconds =
        props.videoDuration ||
        props.image.duration ||
        props.image.runtime ||
        props.image.metadata?.duration ||
        props.image.metadata?.runtime ||
        null;
      if (durationSeconds) {
        entries.push({
          label: "Runtime",
          value: formatDuration(durationSeconds),
        });
      }
    }
  }

  if (props.image.reference_folder_id && props.image.file_path) {
    entries.push({
      label: "Source file",
      value: props.image.file_path,
      fullWidth: true,
      clickable: true,
    });
  }

  return entries;
});

watch(
  [() => !!props.comfyMetadata, () => !!pictureInfoEntries.value?.length],
  ([hasComfy, hasInfo]) => {
    if (metadataTab.value === "comfy" && !hasComfy) metadataTab.value = "info";
    if (metadataTab.value === "info" && !hasInfo && hasComfy)
      metadataTab.value = "comfy";
  },
);
</script>

<style scoped>
.sidebar-section {
  margin-bottom: 6px;
}

.section-header--collapsible {
  cursor: pointer;
  user-select: none;
}

.section-header--collapsible:hover {
  opacity: 0.85;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 4px;
  padding: 2px 0;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.metadata-empty {
  font-size: 0.85rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.metadata-tabbox {
  display: flex;
  flex-direction: column;
  border-radius: 10px;
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
  overflow: hidden;
}

.metadata-tab-strip {
  display: flex;
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.1);
}

.metadata-tab-btn {
  flex: 1;
  padding: 6px 8px;
  font-size: 0.78rem;
  font-weight: 600;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.5);
  cursor: pointer;
  transition:
    color 0.15s,
    background 0.15s;
  text-align: center;
  white-space: nowrap;
}

.metadata-tab-btn:first-child {
  border-radius: 10px 0 0 0;
}

.metadata-tab-btn:last-child {
  border-radius: 0 10px 0 0;
}

.metadata-tab-btn:only-child {
  border-radius: 10px 10px 0 0;
}

.metadata-tab-btn.active {
  color: rgb(var(--v-theme-on-dark-surface));
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}

.metadata-tab-btn:hover:not(.active) {
  color: rgba(var(--v-theme-on-dark-surface), 0.8);
  background: rgba(var(--v-theme-on-dark-surface), 0.04);
}

.metadata-tab-panel {
  padding: 10px;
}

.metadata-info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
}

.metadata-info-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.metadata-info-item--full-width {
  grid-column: 1 / -1;
}

.metadata-info-item--clickable {
  cursor: pointer;
}

.metadata-info-item--clickable:hover .metadata-info-value {
  text-decoration: underline;
}

.metadata-info-label {
  font-size: 0.7rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.metadata-info-value {
  font-size: 0.8rem;
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metadata-comfy-subtitle {
  font-size: 0.78rem;
  font-weight: 500;
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
}

.metadata-comfy-details {
  background: rgba(var(--v-theme-shadow), 0.25);
  border-radius: 8px;
  padding: 8px 10px;
}

.metadata-comfy-details summary {
  cursor: pointer;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
}

.metadata-comfy-details summary::-webkit-details-marker {
  display: none;
}

.metadata-comfy-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.metadata-comfy-summary-left {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.metadata-comfy-workflow-action {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
  font-size: 0.72rem;
  padding: 2px 2px;
  border-radius: 4px;
  cursor: pointer;
}

.metadata-comfy-workflow-action:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
  color: rgb(var(--v-theme-on-dark-surface));
}

.metadata-comfy-textarea {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  min-height: 160px;
  max-height: 280px;
  border-radius: 8px;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: 0.74rem;
  line-height: 1.4;
  padding: 8px;
  resize: vertical;
  overflow: auto;
  white-space: pre;
  word-break: normal;
}

.metadata-comfy-details:not([open]) .metadata-comfy-textarea {
  display: none;
}

.metadata-comfy-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metadata-comfy-field-label {
  font-size: 0.7rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.metadata-comfy-field-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metadata-comfy-prompt {
  min-height: 70px;
  max-height: 160px;
  white-space: pre-wrap;
  word-break: break-word;
}

.metadata-comfy-chips-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metadata-comfy-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.metadata-comfy-chip {
  display: inline-block;
  font-size: 0.7rem;
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
  border-radius: 4px;
  padding: 4px 6px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
