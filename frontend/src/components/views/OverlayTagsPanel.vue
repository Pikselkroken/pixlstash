<template>
  <div
    class="sidebar-section sidebar-section--tags"
    :class="{ 'sidebar-section--collapsed': tagsCollapsed }"
  >
    <div
      class="section-header section-header--collapsible"
      @click="tagsCollapsed = !tagsCollapsed"
    >
      <span>Tags</span>
      <span class="section-meta-group">
        <button
          v-if="props.image && !isReadOnly"
          class="section-meta-btn"
          type="button"
          title="Reset and regenerate tags — deletes all tags and predictions for this picture and requeues it for re-tagging"
          :disabled="isTagsRefreshing"
          @click.stop="refreshPictureTags()"
        >
          <v-icon size="16">mdi-refresh</v-icon>
        </button>
        <v-menu
          v-if="props.image && !isReadOnly"
          v-model="tagPluginMenuOpen"
          :close-on-content-click="true"
          location="bottom end"
        >
          <template #activator="{ props: menuProps }">
            <button
              class="section-meta-btn section-meta-btn--with-chevron"
              type="button"
              title="Regenerate tags with a specific tagger..."
              :disabled="isTagsRefreshing"
              v-bind="menuProps"
              @click.stop="fetchTagPlugins"
            >
              <v-icon size="14">mdi-refresh</v-icon>
              <v-icon size="10">mdi-chevron-down</v-icon>
            </button>
          </template>
          <v-list density="compact" min-width="160">
            <v-list-item v-if="tagPluginsLoading" disabled title="Loading..." />
            <template v-if="!tagPluginsLoading">
              <v-list-item
                v-for="plugin in tagPlugins"
                :key="plugin.name"
                :title="plugin.display_name || plugin.name"
                :disabled="!!plugin.load_error"
                @click="refreshPictureTags(plugin.name)"
              />
              <v-list-item
                v-if="!tagPlugins.length"
                disabled
                title="No taggers available"
              />
            </template>
          </v-list>
        </v-menu>
        <button
          v-if="props.image && !isReadOnly"
          class="section-meta-btn"
          type="button"
          title="Add tag (T)"
          @click.stop="beginAddTag"
        >
          <v-icon size="16">mdi-plus</v-icon>
        </button>
        <v-icon size="16" style="opacity: 0.6">{{
          tagsCollapsed ? "mdi-chevron-right" : "mdi-chevron-down"
        }}</v-icon>
      </span>
    </div>
    <template v-if="!tagsCollapsed">
      <div class="tag-list" ref="tagListRef">
        <div v-if="isTagsRefreshing" class="tag-refresh-indicator">
          <v-progress-circular
            indeterminate
            size="16"
            width="2"
            color="primary"
          />
        </div>
        <div class="tag-section">
          <div
            class="tag-drop-zone"
            :class="{
              'tag-drop-zone--active': isDragOver('unassigned', null),
            }"
            @dragover.prevent="handleDragOver('unassigned', null)"
            @dragenter.prevent="handleDragOver('unassigned', null)"
            @dragleave="handleDragLeave('unassigned', null)"
            @drop.prevent="handleDropOnAllTags"
          >
            <span
              v-for="tag in allImageTags"
              :key="`unassigned-${tag.id ?? tag.tag}`"
              :class="[
                'overlay-tag',
                { 'overlay-tag--penalised': isPenalisedTag(tag) },
                predictionClassForTag(tagLabel(tag)),
              ]"
              :style="predictionStyleForTag(tagLabel(tag))"
              :title="predictionTitleForTag(tagLabel(tag))"
              draggable="true"
              @dragstart="
                startTagDrag(tagLabel(tag), 'unassigned', null, $event)
              "
              @dragend="clearTagDrag"
            >
              {{ tagLabel(tag) }}
              <button
                v-if="!isReadOnly"
                class="tag-delete-btn"
                @click.stop="removeAllTag(tag)"
                title="Remove tag"
              >
                <v-icon size="12">mdi-close</v-icon>
              </button>
            </span>
            <div v-if="!allImageTags.length" class="tag-drop-placeholder">
              Drop tags here
            </div>
            <input
              v-if="addingTag && !isReadOnly"
              ref="tagInputRef"
              v-model="newTag"
              @keydown.enter.prevent="confirmAddTag"
              @keydown="handleTagInputKey"
              @blur="cancelAddTag"
              class="tag-add-input"
              placeholder="New tag"
            />
          </div>
        </div>
      </div>
    </template>
  </div>

  <div v-if="nearMissPredictions.length" class="sidebar-section">
    <div
      class="section-header section-header--collapsible"
      @click="nearMissesCollapsed = !nearMissesCollapsed"
    >
      <span>Rejected Tags</span>
      <span class="section-meta-group">
        <v-icon size="16" style="opacity: 0.6">{{
          !nearMissesCollapsed ? "mdi-chevron-right" : "mdi-chevron-down"
        }}</v-icon>
      </span>
    </div>
    <div
      v-show="!nearMissesCollapsed"
      class="tag-drop-zone tag-drop-zone--predictions"
      :class="{
        'tag-drop-zone--active': isDragOver('rejected', null),
      }"
      @dragover.prevent="handleDragOver('rejected', null)"
      @dragenter.prevent="handleDragOver('rejected', null)"
      @dragleave="handleDragLeave('rejected', null)"
      @drop.prevent="handleDropOnRejectedTags"
    >
      <span
        v-for="pred in nearMissPredictions"
        :key="`pred-${pred.tag}`"
        :class="[
          'overlay-tag',
          predictionClassForTag(pred.tag),
          'overlay-tag--prediction',
        ]"
        :style="{ '--pred-confidence': pred.confidence }"
        :title="rejectedTagTitle(pred)"
        draggable="true"
        @dragstart="startTagDrag(pred.tag, 'rejected', null, $event)"
        @dragend="clearTagDrag"
      >
        {{ pred.tag }}
        <span class="tag-pred-confidence"
          >{{ (pred.confidence * 100).toFixed(0) }}%</span
        >
        <button
          v-if="!isReadOnly"
          class="tag-pred-btn tag-pred-btn--confirm"
          title="Confirm prediction (add as tag)"
          @click.stop="confirmPrediction(pred.tag)"
        >
          <v-icon size="11">mdi-check</v-icon>
        </button>
      </span>
    </div>
  </div>

  <Teleport to="body">
    <div
      v-if="addingTag && tagSuggestions.length && tagInputRect"
      class="tag-autocomplete-dropdown"
      :class="{
        'tag-autocomplete-dropdown--hover-enabled': autocompleteHoverEnabled,
      }"
      @mousemove.once="autocompleteHoverEnabled = true"
      :style="{
        top: tagInputRect.bottom + 4 + 'px',
        left: tagInputRect.left + 'px',
        minWidth: Math.max(tagInputRect.width, 160) + 'px',
      }"
    >
      <button
        v-for="(item, idx) in tagSuggestions"
        :key="item.tag"
        class="tag-autocomplete-item"
        :class="{ 'tag-autocomplete-item--active': idx === tagSuggestionIndex }"
        @mousedown.prevent="selectTagSuggestion(item)"
      >
        {{ item.tag }}
        <span
          v-if="idx === (tagSuggestionIndex >= 0 ? tagSuggestionIndex : 0)"
          class="tag-autocomplete-tab-hint"
          >TAB</span
        >
      </button>
    </div>
  </Teleport>
</template>

<script setup>
/**
 * OverlayTagsPanel
 *
 * Sidebar tags section extracted from ImageOverlay. Owns all tag state,
 * prediction fetching, penalised-tag loading, and tag autocomplete.
 *
 * Props:
 *   image        - The current overlay image object (read-only).
 *   backendUrl   - Base URL for API calls (required).
 *   hiddenTags   - Array of hidden tag strings from user settings.
 *   applyTagFilter - Whether the hidden-tag filter is active.
 *
 * Emits:
 *   update-tags(newTagsArray)         - Tags changed locally; parent updates image.value.tags.
 *   overlay-change(payload)           - Re-emitted for grid/App awareness.
 *   add-tag(imageId, tag)             - New tag confirmed; for grid thumbnail updates.
 *   request-metadata-refresh(imageId) - Panel needs parent to call fetchOverlayMetadata.
 *
 * Exposes:
 *   addingTag              - Whether the tag input is active (for parent keyboard handler).
 *   beginAddTag()          - Activate tag input (keyboard shortcut T).
 *   cancelAddTag()         - Cancel tag input (keyboard ESC).
 *   refetchPredictions(id) - Re-fetch predictions after parent metadata refresh.
 */
import { ref, reactive, computed, watch, nextTick, onMounted } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import {
  dedupeTagList,
  getTagLabel as tagLabel,
  getTagList,
} from "../../utils/tags.js";

const props = defineProps({
  image: { type: Object, default: null },
  backendUrl: { type: String, required: true },
  hiddenTags: { type: Array, default: () => [] },
  applyTagFilter: { type: Boolean, default: false },
});

const emit = defineEmits([
  "update-tags",
  "overlay-change",
  "add-tag",
  "request-metadata-refresh",
]);

// ── Tag UI state ───────────────────────────────────────────────────────────

const tagsCollapsed = ref(false);
const isTagsRefreshing = ref(false);
const addingTag = ref(false);
const newTag = ref("");
const tagSuggestionIndex = ref(-1);
const tagInputRect = ref(null);
const autocompleteHoverEnabled = ref(false);
const tagInputRef = ref(null);
const tagListRef = ref(null);
const tagPluginMenuOpen = ref(false);
const tagPlugins = ref([]);
const tagPluginsLoading = ref(false);

// ── Prediction state ───────────────────────────────────────────────────────

const tagPredictions = ref([]);
const predictionAcceptanceThreshold = ref(0.95);
const labelThresholds = ref({});

// ── Rejected-tags collapsed state (persisted to sessionStorage) ────────────

function loadOverlayRejectedTagsCollapsed() {
  if (typeof window === "undefined") return false;
  const raw = window.sessionStorage?.getItem(
    "pixlstash:imageOverlay:rejectedTagsCollapsed",
  );
  if (raw == null) return false;
  return raw === "1";
}

function persistOverlayRejectedTagsCollapsed(value) {
  if (typeof window === "undefined") return;
  window.sessionStorage?.setItem(
    "pixlstash:imageOverlay:rejectedTagsCollapsed",
    value ? "1" : "0",
  );
}

const nearMissesCollapsed = ref(loadOverlayRejectedTagsCollapsed());

watch(nearMissesCollapsed, (value) => {
  persistOverlayRejectedTagsCollapsed(Boolean(value));
});

// ── Penalised tags ─────────────────────────────────────────────────────────

const penalisedTags = ref(new Set());
const penalisedTagsLoading = ref(false);

async function fetchPenalisedTags() {
  if (penalisedTagsLoading.value) return;
  penalisedTagsLoading.value = true;
  try {
    const endpoint = isReadOnly.value
      ? "/users/me/penalised-tags"
      : "/users/me/config";
    const res = await apiClient.get(endpoint);
    let list = [];
    if (Array.isArray(res.data?.smart_score_penalised_tags)) {
      list = res.data.smart_score_penalised_tags;
    } else if (
      res.data?.smart_score_penalised_tags &&
      typeof res.data.smart_score_penalised_tags === "object"
    ) {
      list = Object.keys(res.data.smart_score_penalised_tags);
    }
    const d = list
      .map((tag) =>
        String(tag || "")
          .trim()
          .toLowerCase(),
      )
      .filter(Boolean);
    penalisedTags.value = new Set(d);
  } catch {
    penalisedTags.value = new Set();
  } finally {
    penalisedTagsLoading.value = false;
  }
}

onMounted(() => {
  fetchPenalisedTags();
});

// ── Hidden-tag filtering ────────────────────────────────────────────────────

const userVisibleHiddenTagKeys = ref(new Set());

const hiddenTagSet = computed(() => {
  const values = Array.isArray(props.hiddenTags) ? props.hiddenTags : [];
  const cleaned = values
    .map((tag) =>
      String(tag || "")
        .trim()
        .toLowerCase(),
    )
    .filter(Boolean);
  return new Set(cleaned);
});

function filterHiddenTags(tags, options = {}) {
  if (!props.applyTagFilter) return tags;
  const set = hiddenTagSet.value;
  if (!set || set.size === 0) return tags;
  const keepVisible =
    options?.keepVisible instanceof Set ? options.keepVisible : null;
  return (tags || []).filter((tag) => {
    const key = tagLabel(tag).trim().toLowerCase();
    if (keepVisible?.has(key)) return true;
    return key && !set.has(key);
  });
}

const allImageTags = computed(() => {
  return filterHiddenTags(dedupeTagList(getTagList(props.image?.tags)), {
    keepVisible: userVisibleHiddenTagKeys.value,
  });
});

function pinUserVisibleHiddenTag(tag) {
  const key = normalizeTagKey(tag);
  if (!key) return;
  const next = new Set(userVisibleHiddenTagKeys.value);
  next.add(key);
  userVisibleHiddenTagKeys.value = next;
}

function unpinUserVisibleHiddenTag(tag) {
  const key = normalizeTagKey(tag);
  if (!key) return;
  const next = new Set(userVisibleHiddenTagKeys.value);
  next.delete(key);
  userVisibleHiddenTagKeys.value = next;
}

function normalizeTagKey(tag) {
  return String(tagLabel(tag) ?? tag ?? "")
    .trim()
    .toLowerCase();
}

// ── Predictions fetching ────────────────────────────────────────────────────

async function fetchTagPredictions(imageId) {
  if (!imageId || !props.backendUrl) return;
  try {
    const res = await apiClient.get(
      `${props.backendUrl}/pictures/${imageId}/tag_predictions?include_meta=1`,
    );
    if (!props.image || props.image.id !== imageId) return;
    const payload = res.data;
    const predictions = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.tag_predictions)
        ? payload.tag_predictions
        : [];
    const threshold = Number(payload?.meta?.acceptance_threshold);
    if (Number.isFinite(threshold) && threshold > 0 && threshold <= 1) {
      predictionAcceptanceThreshold.value = threshold;
    }
    labelThresholds.value = payload?.meta?.label_thresholds || {};
    tagPredictions.value = predictions;
  } catch {
    tagPredictions.value = [];
  } finally {
    isTagsRefreshing.value = false;
  }
}

// Reset state and refetch when the displayed image changes.
watch(
  () => props.image?.id,
  (newId) => {
    userVisibleHiddenTagKeys.value = new Set();
    tagPredictions.value = [];
    if (newId) {
      isTagsRefreshing.value = true;
      fetchTagPredictions(newId);
    } else {
      isTagsRefreshing.value = false;
    }
  },
  { immediate: true },
);

// ── Computed prediction helpers ─────────────────────────────────────────────

const confirmedTagNames = computed(() => {
  const names = new Set();
  for (const tag of allImageTags.value) {
    const label = tagLabel(tag);
    if (label) names.add(label.trim().toLowerCase());
  }
  return names;
});

const nearMissPredictions = computed(() => {
  return tagPredictions.value.filter(
    (p) =>
      p.status === "REJECTED" &&
      p.confidence >= 0.3 &&
      !confirmedTagNames.value.has(p.tag.trim().toLowerCase()),
  );
});

const pendingPredictionMap = computed(() => {
  const map = new Map();
  for (const p of tagPredictions.value) {
    map.set(p.tag.trim().toLowerCase(), p);
  }
  return map;
});

function isPenalisedTag(tag) {
  const key = tagLabel(tag).trim().toLowerCase();
  return penalisedTags.value.has(key);
}

function predictionClassForTag(label) {
  if (!label) return null;
  const pred = pendingPredictionMap.value.get(label.trim().toLowerCase());
  if (!pred) return null;
  if (penalisedTags.value.has(label.trim().toLowerCase())) {
    return "overlay-tag--predicted-anomaly";
  }
  return "overlay-tag--predicted-normal";
}

function predictionStyleForTag(label) {
  if (!label) return null;
  const pred = pendingPredictionMap.value.get(label.trim().toLowerCase());
  if (!pred) return null;
  return { "--pred-confidence": pred.confidence };
}

function _predThreshold(tag) {
  const perLabel = tag != null ? labelThresholds.value[tag] : undefined;
  return typeof perLabel === "number" && Number.isFinite(perLabel)
    ? perLabel
    : Number(predictionAcceptanceThreshold.value) || 0.95;
}

function predictionNeededToAccept(confidence, tag) {
  const current = Number(confidence) || 0;
  return Math.max(0, _predThreshold(tag) - current);
}

function predictionTitleForTag(label) {
  if (!label) return null;
  const pred = pendingPredictionMap.value.get(label.trim().toLowerCase());
  if (!pred) return null;
  const threshold = _predThreshold(pred.tag);
  const threshPct = Math.round(threshold * 100);
  const confPct = Math.round(pred.confidence * 100);
  const needed = predictionNeededToAccept(pred.confidence, pred.tag);
  if (needed <= 0) {
    return `Prediction confidence: ${confPct}% (auto-applied > ${threshPct}%)`;
  }
  return `Prediction confidence: ${confPct}% (needs +${Math.round(needed * 100)}% to auto-accept)`;
}

function rejectedTagTitle(pred) {
  const threshold = _predThreshold(pred?.tag);
  const threshPct = Math.round(threshold * 100);
  const confPct = Math.round((pred.confidence || 0) * 100);
  const needed = predictionNeededToAccept(pred.confidence, pred.tag);
  if (needed <= 0) {
    return `Confidence: ${confPct}% | > ${threshPct}% but manually rejected`;
  }
  return `Confidence: ${confPct}% | Needs +${Math.round(needed * 100)}% to reach ${threshPct}%`;
}

// ── Tag autocomplete ────────────────────────────────────────────────────────

const allAvailableTags = ref([]);
let allAvailableTagsFetchedAt = 0;

async function fetchAllAvailableTags() {
  if (!props.backendUrl) return;
  const now = Date.now();
  if (now - allAvailableTagsFetchedAt < 30_000) return;
  try {
    const res = await apiClient.get(`${props.backendUrl}/tags`);
    const data = res.data;
    if (Array.isArray(data)) {
      allAvailableTags.value = data;
      allAvailableTagsFetchedAt = now;
    }
  } catch {
    // Non-critical — autocomplete just stays empty
  }
}

const tagSuggestions = computed(() => {
  const query = newTag.value.trim().toLowerCase();
  if (!query) return [];
  const currentTags = new Set(getTagList(props.image?.tags).map((t) => t.tag));

  const rejectedConf = new Map();
  for (const p of tagPredictions.value) {
    if (p.status === "REJECTED" && typeof p.confidence === "number") {
      rejectedConf.set(p.tag.trim().toLowerCase(), p.confidence);
    }
  }

  return allAvailableTags.value
    .filter((item) => {
      const t = typeof item === "string" ? item : item.tag;
      return !currentTags.has(t) && t.toLowerCase().startsWith(query);
    })
    .sort((a, b) => {
      const aTag = (typeof a === "string" ? a : a.tag).toLowerCase();
      const bTag = (typeof b === "string" ? b : b.tag).toLowerCase();
      const aConf = rejectedConf.get(aTag) ?? -1;
      const bConf = rejectedConf.get(bTag) ?? -1;
      if (aConf !== bConf) return bConf - aConf;
      const aCount = (typeof a === "string" ? 0 : a.count) || 0;
      const bCount = (typeof b === "string" ? 0 : b.count) || 0;
      return bCount - aCount;
    })
    .slice(0, 8);
});

watch(newTag, () => {
  tagSuggestionIndex.value = -1;
});

watch(
  [addingTag, tagSuggestions],
  () => {
    if (addingTag.value && tagSuggestions.value.length) {
      autocompleteHoverEnabled.value = false;
      nextTick(() => {
        tagInputRect.value = tagInputRef.value
          ? tagInputRef.value.getBoundingClientRect()
          : null;
      });
    } else {
      tagInputRect.value = null;
    }
  },
  { deep: false },
);

// ── Tag editing functions ───────────────────────────────────────────────────

function resetTagInput() {
  addingTag.value = false;
  newTag.value = "";
  tagSuggestionIndex.value = -1;
}

function beginAddTag() {
  addingTag.value = true;
  newTag.value = "";
  fetchAllAvailableTags();
  nextTick(() => {
    if (tagInputRef.value) {
      tagInputRef.value.focus({ preventScroll: true });
      tagInputRef.value.select?.();
      if (tagListRef.value) {
        tagListRef.value.scrollTop = tagListRef.value.scrollHeight;
      }
    }
  });
}

function cancelAddTag() {
  resetTagInput();
}

function selectTagSuggestion(item) {
  newTag.value = typeof item === "string" ? item : item.tag;
  tagSuggestionIndex.value = -1;
  nextTick(() => confirmAddTag());
}

function confirmAddTag() {
  if (
    tagSuggestionIndex.value >= 0 &&
    tagSuggestions.value.length > tagSuggestionIndex.value
  ) {
    const item = tagSuggestions.value[tagSuggestionIndex.value];
    newTag.value = typeof item === "string" ? item : item.tag;
    tagSuggestionIndex.value = -1;
  }
  const trimmed = newTag.value.trim();
  if (!trimmed) {
    cancelAddTag();
    return;
  }
  const currentTags = getTagList(props.image?.tags);
  if (currentTags.some((tag) => tag.tag === trimmed)) {
    cancelAddTag();
    return;
  }
  pinUserVisibleHiddenTag(trimmed);
  emit("add-tag", props.image.id, trimmed);
  const next = dedupeTagList([...currentTags, { id: null, tag: trimmed }]);
  emit("update-tags", next);
  resetTagInput();
}

function handleTagInputKey(event) {
  if (event.key === "ArrowDown") {
    if (tagSuggestions.value.length) {
      event.preventDefault();
      tagSuggestionIndex.value = Math.min(
        tagSuggestionIndex.value + 1,
        tagSuggestions.value.length - 1,
      );
    }
  } else if (event.key === "ArrowUp") {
    if (tagSuggestions.value.length) {
      event.preventDefault();
      tagSuggestionIndex.value = Math.max(tagSuggestionIndex.value - 1, -1);
    }
  } else if (event.key === "Tab") {
    if (tagSuggestions.value.length) {
      event.preventDefault();
      const idx = tagSuggestionIndex.value >= 0 ? tagSuggestionIndex.value : 0;
      selectTagSuggestion(tagSuggestions.value[idx]);
    }
  } else if (event.key === "Backspace") {
    if (newTag.value || event.repeat) return;
    event.preventDefault();
    cancelAddTag();
  }
}

// ── Tag drag-and-drop ───────────────────────────────────────────────────────

const dragState = reactive({
  tag: null,
  sourceType: null,
  sourceId: null,
});
const dragOverTarget = ref({ type: null, id: null });

function startTagDrag(tag, sourceType, sourceId, event) {
  dragState.tag = tag;
  dragState.sourceType = sourceType;
  dragState.sourceId = sourceId;
  if (event?.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
  }
}

function clearTagDrag() {
  dragState.tag = null;
  dragState.sourceType = null;
  dragState.sourceId = null;
  dragOverTarget.value = { type: null, id: null };
}

function handleDragOver(type, id) {
  dragOverTarget.value = { type, id };
}

function handleDragLeave(type, id) {
  if (dragOverTarget.value?.type === type && dragOverTarget.value?.id === id) {
    dragOverTarget.value = { type: null, id: null };
  }
}

function isDragOver(type, id) {
  return dragOverTarget.value?.type === type && dragOverTarget.value?.id === id;
}

function handleDropOnAllTags() {
  const draggedTag = dragState.tag;
  const sourceType = dragState.sourceType;
  clearTagDrag();
  if (!draggedTag || sourceType !== "rejected") return;
  confirmPrediction(draggedTag);
}

async function handleDropOnRejectedTags() {
  const draggedTag = dragState.tag;
  const sourceType = dragState.sourceType;
  clearTagDrag();
  if (!draggedTag || sourceType !== "unassigned") return;
  const key = String(draggedTag).trim().toLowerCase();
  const tagObj = allImageTags.value.find(
    (entry) => tagLabel(entry).trim().toLowerCase() === key,
  );
  await removeAllTag(tagObj || { tag: draggedTag });
  await rejectPrediction(draggedTag);
}

// ── Tag mutation functions ──────────────────────────────────────────────────

async function removeAllTag(tag) {
  if (!tag) return;
  const label = tagLabel(tag);
  if (!label) return;
  unpinUserVisibleHiddenTag(label);
  let didUpdate = false;
  const currentTags = getTagList(props.image?.tags);
  const imageMatch = allImageTags.value.find((entry) => entry.tag === label);
  let next = currentTags;

  if (imageMatch && imageMatch.id != null) {
    next = currentTags.filter((entry) => entry.tag !== label);
    didUpdate = true;
  } else {
    const filtered = currentTags.filter((entry) => entry.tag !== label);
    if (filtered.length !== currentTags.length) {
      next = filtered;
      didUpdate = true;
    }
  }

  if (didUpdate) {
    emit("update-tags", next);
  }

  const capturedImageId = props.image?.id ?? null;
  if (capturedImageId && props.backendUrl) {
    try {
      await apiClient.post(
        `${props.backendUrl}/pictures/${capturedImageId}/tags/remove_all`,
        { tag: label },
      );
    } catch (err) {
      console.warn("Failed to remove tag everywhere:", err);
    }
  }

  if (didUpdate && capturedImageId) {
    await rejectPrediction(label);
    emit("overlay-change", {
      imageId: capturedImageId,
      fields: { tags: true, smartScore: true },
    });
  }
}

async function confirmPrediction(tag) {
  if (!props.image?.id || !props.backendUrl) return;
  const imageId = props.image.id;
  const prevTags = Array.isArray(props.image?.tags)
    ? [...props.image.tags]
    : [];
  const prevPredictions = Array.isArray(tagPredictions.value)
    ? [...tagPredictions.value]
    : [];

  const key = String(tag || "")
    .trim()
    .toLowerCase();
  if (key) {
    const current = getTagList(props.image?.tags);
    const hasTag = current.some(
      (entry) => tagLabel(entry).trim().toLowerCase() === key,
    );
    if (!hasTag) {
      emit(
        "update-tags",
        dedupeTagList([...current, { id: null, tag: String(tag) }]),
      );
    }
    tagPredictions.value = tagPredictions.value.map((p) =>
      p.tag.trim().toLowerCase() === key ? { ...p, status: "CONFIRMED" } : p,
    );
  }

  try {
    await apiClient.post(
      `${props.backendUrl}/pictures/${imageId}/tag_predictions/${encodeURIComponent(tag)}/confirm`,
    );
    void fetchTagPredictions(imageId);
  } catch (e) {
    emit("update-tags", prevTags);
    tagPredictions.value = prevPredictions;
    console.error("Failed to confirm prediction:", e);
  }
}

async function rejectPrediction(tag) {
  if (!props.image?.id || !props.backendUrl) return;
  const imageId = props.image.id;
  const key = String(tag).trim().toLowerCase();
  try {
    await apiClient.post(
      `${props.backendUrl}/pictures/${imageId}/tag_predictions/${encodeURIComponent(tag)}/reject`,
    );
    tagPredictions.value = tagPredictions.value.map((p) =>
      p.tag.trim().toLowerCase() === key ? { ...p, status: "REJECTED" } : p,
    );
  } catch {
    // Network error — fall through to ensure local entry below.
  }
  if (
    !tagPredictions.value.some(
      (p) => p.tag.trim().toLowerCase() === key && p.status === "REJECTED",
    )
  ) {
    tagPredictions.value = [
      ...tagPredictions.value,
      { tag: String(tag), confidence: 1.0, status: "REJECTED" },
    ];
  }
}

async function fetchTagPlugins() {
  if (tagPluginsLoading.value || tagPlugins.value.length) return;
  tagPluginsLoading.value = true;
  try {
    const res = await apiClient.get("/taggers");
    tagPlugins.value = (res.data?.plugins ?? []).filter((p) => p.supports_tags);
  } catch {
    tagPlugins.value = [];
  } finally {
    tagPluginsLoading.value = false;
  }
}

async function refreshPictureTags(model = null) {
  if (!props.image?.id || !props.backendUrl) return;
  if (isTagsRefreshing.value) return;
  const capturedImageId = props.image.id;

  isTagsRefreshing.value = true;
  try {
    const body = model ? { model } : {};
    await apiClient.post(
      `${props.backendUrl}/pictures/${capturedImageId}/reset_tags`,
      body,
    );
    tagPredictions.value = [];
    emit("update-tags", []);
    emit("overlay-change", {
      imageId: capturedImageId,
      fields: { tags: true, smartScore: true },
    });
    // Ask parent to refresh full metadata, then fetch our own predictions.
    emit("request-metadata-refresh", capturedImageId);
    await fetchTagPredictions(capturedImageId);
  } catch (err) {
    console.warn("Failed to refresh picture tags:", err);
  } finally {
    isTagsRefreshing.value = false;
  }
}

// ── Public API (exposed to parent via template ref) ─────────────────────────

defineExpose({
  addingTag,
  beginAddTag,
  cancelAddTag,
  refetchPredictions: fetchTagPredictions,
});
</script>

<style scoped>
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

.section-meta-group {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.section-meta-btn {
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  padding: 2px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.section-meta-btn:disabled {
  cursor: default;
  opacity: 0.5;
}

.section-meta-btn--with-chevron {
  gap: 1px;
}

.tag-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-right: 4px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.tag-refresh-indicator {
  display: inline-flex;
  align-items: center;
  padding: 2px 4px;
  margin-right: 4px;
}

.overlay-tag {
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: 6px;
  padding: 1px 2px 1px 6px;
  font-size: 0.72rem;
  line-height: 1.2;
  justify-content: center;
  vertical-align: middle;
  cursor: pointer;
}

.overlay-tag--penalised {
  color: rgb(var(--v-theme-error));
  font-size: 0.72rem;
  line-height: 1.2;
  border: 1px solid rgba(var(--v-theme-error), 0.6);
  background: rgba(var(--v-theme-error), 0.15);
}

.overlay-tag--predicted-anomaly {
  --ac: clamp(0.35, var(--pred-confidence, 0.6), 1);
  font-size: 0.72rem;
  color: color-mix(
    in srgb,
    rgb(var(--v-theme-on-dark-surface)) calc((1 - var(--ac)) * 100%),
    rgb(var(--v-theme-error)) calc(var(--ac) * 100%)
  );
  border-color: color-mix(
    in srgb,
    rgba(var(--v-theme-on-dark-surface), 0.2) calc((1 - var(--ac)) * 100%),
    rgba(var(--v-theme-error), 0.7) calc(var(--ac) * 100%)
  );
  background: color-mix(
    in srgb,
    rgba(var(--v-theme-on-dark-surface), 0.05) calc((1 - var(--ac)) * 100%),
    rgba(var(--v-theme-error), 0.2) calc(var(--ac) * 100%)
  );
}

.overlay-tag--predicted-normal {
  --nc: clamp(0.25, var(--pred-confidence, 0.7), 1);
  --nm: calc(25% + var(--nc) * 55%);
  color: color-mix(
    in srgb,
    rgb(var(--v-theme-primary)) var(--nm),
    rgb(var(--v-theme-on-dark-surface))
  );
  border-color: color-mix(
    in srgb,
    rgba(var(--v-theme-primary), 0.6) var(--nm),
    rgba(var(--v-theme-on-dark-surface), 0.15)
  );
  background: color-mix(
    in srgb,
    rgba(var(--v-theme-primary), 0.18) var(--nm),
    rgba(var(--v-theme-on-dark-surface), 0.06)
  );
}

.overlay-tag--prediction {
  filter: saturate(0.82) brightness(0.9);
  opacity: 0.88;
  border-style: dashed;
  border-width: 1px;
}

.tag-pred-confidence {
  font-size: 0.65rem;
  opacity: 0.7;
  margin-left: 2px;
}

.tag-pred-btn {
  margin: 0 1px;
  padding: 1px;
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 0.75em;
  line-height: 1;
  vertical-align: middle;
  opacity: 0.6;
}

.tag-pred-btn:hover {
  opacity: 1;
}

.tag-pred-btn--confirm:hover {
  color: rgb(var(--v-theme-success));
}

.tag-pred-btn--reject:hover {
  color: rgb(var(--v-theme-error));
}

.tag-drop-zone {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px;
  border-radius: 8px;
  border: 1px dashed rgba(255, 255, 255, 0.2);
  min-height: 26px;
  max-height: none;
  overflow: visible;
}

.tag-drop-zone--active {
  border-color: rgba(255, 255, 255, 0.6);
  background: rgba(255, 255, 255, 0.08);
}

.tag-drop-zone--predictions {
  gap: 4px;
}

.tag-drop-placeholder {
  font-size: 0.68rem;
  color: rgba(255, 255, 255, 0.45);
}

.tag-delete-btn {
  margin: 0;
  padding: 2px;
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  font-size: 0.8em;
  line-height: 1;
  vertical-align: middle;
}

.tag-delete-btn:hover {
  color: rgb(var(--v-theme-accent));
}

.tag-add-input {
  background: rgba(var(--v-theme-shadow), 0.4);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: 999px;
  padding: 1px 6px;
  font-size: 0.7rem;
}

.tag-autocomplete-dropdown {
  position: fixed;
  z-index: 9999;
  background: color-mix(in srgb, rgb(var(--v-theme-shadow)) 85%, transparent);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  border-radius: 6px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.45);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.tag-autocomplete-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 5px 10px;
  font-size: 0.75rem;
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tag-autocomplete-dropdown--hover-enabled .tag-autocomplete-item:hover,
.tag-autocomplete-item--active {
  background: rgba(var(--v-theme-primary), 0.22);
  color: rgb(var(--v-theme-on-dark-surface));
}

.tag-autocomplete-tab-hint {
  display: inline-block;
  margin-left: 8px;
  padding: 0 4px;
  font-size: 0.55rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  border-radius: 3px;
  background: rgba(var(--v-theme-on-dark-surface), 0.15);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  vertical-align: middle;
  line-height: 1.5;
}
</style>
