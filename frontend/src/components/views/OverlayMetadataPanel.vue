<template>
  <div class="sidebar-section">
    <div
      class="section-header section-header--collapsible section-label section-label--on-dark"
      @click="metadataCollapsed = !metadataCollapsed"
    >
      <span>Metadata</span>
      <v-icon size="16" style="opacity: 0.6">{{
        metadataCollapsed ? "mdi-chevron-right" : "mdi-chevron-down"
      }}</v-icon>
    </div>
    <template v-if="!metadataCollapsed">
      <div v-if="!pictureInfoEntries.length" class="metadata-empty">
        No metadata available
      </div>
      <template v-else>
        <div class="metadata-info-heading">{{ infoHeaderLabel }}</div>
        <dl class="inspector-kv">
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
            <dt>{{ entry.label }}</dt>
            <dd class="metadata-info-value">{{ entry.value }}</dd>
          </div>
        </dl>
      </template>
    </template>
  </div>
</template>

<script setup>
import { ref, computed } from "vue";
import { isSupportedVideoFile, getOverlayFormat } from "../../utils/media.js";
import { formatUserDate } from "../../utils/utils.js";
import { openPictureLocation } from "../../api/pictures";
import { API_BASE_URL } from "../../utils/apiClient";
const props = defineProps({
  image: { type: Object, default: null },
  dateFormat: { type: String, default: "locale" },
  backendUrl: { type: String, default: () => API_BASE_URL },
  videoDuration: { type: Number, default: null },
});

const metadataCollapsed = ref(false);

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

async function openSourceFileLocation() {
  if (!props.image?.id) return;
  try {
    await openPictureLocation(props.image.id);
  } catch (e) {
    // Expected on a headless or remote server, which has no desktop file
    // manager to open. Log it rather than let the click do nothing silently.
    console.warn("Couldn't open the source file location", e);
  }
}

const infoHeaderLabel = computed(() => {
  const format = props.image ? getOverlayFormat(props.image) : "";
  const isVideo = format ? isSupportedVideoFile(format) : false;
  return isVideo ? "Video information" : "Picture information";
});

const pictureInfoEntries = computed(() => {
  if (!props.image) return [];
  const entries = [];
  if (props.image.id != null) {
    entries.push({ label: "ID", value: String(props.image.id) });
  }
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

    // From GET /pictures/{id}/metadata. A still image is 1 frame, which says
    // nothing, so an image only shows the row when it is animated (GIF etc.).
    const frameCount = Number(props.image.frame_count) || 0;
    if (frameCount > 1 || (isVideo && frameCount > 0)) {
      entries.push({ label: "Frames", value: frameCount.toLocaleString() });
    }

    if (isVideo) {
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

  // Locked-by row: names the set(s) freezing this picture's label data. Fed by
  // locked_by_sets from GET /pictures/{id}/metadata.
  const lockedBy = Array.isArray(props.image.locked_by_sets)
    ? props.image.locked_by_sets
    : [];
  if (lockedBy.length) {
    entries.push({
      label: "Locked by",
      value: lockedBy.map((s) => s?.name).filter(Boolean).join(", "),
      fullWidth: true,
    });
  }

  return entries;
});

</script>

<style scoped>
.section-header--collapsible {
  cursor: pointer;
  user-select: none;
}

.section-header--collapsible:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}

/* Type and ink come from the shared `.section-label`; this is layout only. */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
  padding: var(--space-1) 0;
}

.metadata-empty {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

/* The label that used to be the Info tab's own button. The picture's recipe is
   its own section now (#1313), so there is one list here and no tab band. */
.metadata-info-heading {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
  margin-bottom: var(--space-3);
}

.metadata-info-item {
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

.metadata-info-value {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

</style>
