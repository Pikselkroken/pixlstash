<template>
  <div
    class="sidebar-section sidebar-section--tags"
    :class="{ 'sidebar-section--collapsed': tagsCollapsed }"
  >
    <SectionHeader
      v-model:collapsed="tagsCollapsed"
      label="Tags"
      :count="allImageTags.length || null"
      on-dark
    >
      <template #actions>
        <button
          v-if="props.image && !readOnly && clipboardCount"
          class="tag-paste-btn"
          type="button"
          :aria-label="`Paste ${clipboardCount} ${tagWord(clipboardCount)}`"
          @click.stop="pasteClipboard"
        >
          <Tooltip :text="pasteTooltip" activator="parent" :describe="false" />
          <v-icon size="14">mdi-clipboard-outline</v-icon>
          Paste {{ clipboardCount }}
        </button>
        <button
          v-if="props.image && allImageTags.length"
          class="section-meta-btn"
          type="button"
          aria-label="Copy all tags"
          @click.stop="copyLabels(allImageTags.map((t) => tagLabel(t)))"
        >
          <Tooltip text="Copy all tags" activator="parent" :describe="false" />
          <v-icon size="16">mdi-content-copy</v-icon>
        </button>
        <button
          v-if="props.image && !readOnly"
          class="section-meta-btn"
          type="button"
          aria-label="Reset and regenerate tags - deletes all tags and predictions for this picture and requeues it for re-tagging"
          :disabled="isTagsRefreshing"
          @click.stop="refreshPictureTags()"
        >
          <Tooltip
            text="Reset and regenerate tags - deletes all tags and predictions for this picture and requeues it for re-tagging"
            activator="parent"
            :describe="false"
          />
          <v-icon size="16">mdi-refresh</v-icon>
        </button>
        <v-menu
          v-if="props.image && !readOnly"
          v-model="tagPluginMenuOpen"
          :close-on-content-click="true"
          location="bottom end"
        >
          <template #activator="{ props: menuProps }">
            <button
              class="section-meta-btn section-meta-btn--with-chevron"
              type="button"
              aria-label="Regenerate tags with a specific tagger..."
              :disabled="isTagsRefreshing"
              v-bind="menuProps"
              @click.stop="fetchTagPlugins"
            >
              <Tooltip
                text="Regenerate tags with a specific tagger..."
                activator="parent"
                :describe="false"
              />
              <v-icon size="14">mdi-refresh</v-icon>
              <v-icon size="10">mdi-chevron-down</v-icon>
            </button>
          </template>
          <div
            class="ctx-menu ctx-menu--on-dark"
            role="menu"
            tabindex="-1"
            style="min-width: 160px"
            @keydown="onMenuKeydown"
          >
            <button
              v-if="tagPluginsLoading"
              type="button"
              class="ctx-item"
              role="menuitem"
              disabled
            >
              Loading...
            </button>
            <template v-else>
              <button
                v-for="plugin in tagPlugins"
                :key="plugin.name"
                type="button"
                class="ctx-item"
                role="menuitem"
                @click="refreshPictureTags(plugin.name)"
              >
                {{ plugin.display_name || plugin.name }}
              </button>
              <button
                v-if="!tagPlugins.length"
                type="button"
                class="ctx-item"
                role="menuitem"
                disabled
              >
                No taggers available
              </button>
            </template>
          </div>
        </v-menu>
        <button
          v-if="props.image && !readOnly"
          class="section-meta-btn"
          type="button"
          aria-label="Add tag (T)"
          @click.stop="beginAddTag"
        >
          <Tooltip text="Add tag (T)" activator="parent" :describe="false" />
          <v-icon size="16">mdi-plus</v-icon>
        </button>
      </template>
    </SectionHeader>
    <template v-if="!tagsCollapsed">
      <div class="tag-list" ref="tagListRef">
        <div
          v-if="locked && lockNote"
          class="lightbox-lock-note"
        >
          <Tooltip :text="lockNote" activator="parent" />
          <v-icon size="12">mdi-lock-outline</v-icon>
          <span>Locked - read-only. Unlock the set to edit.</span>
        </div>
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
            @click.self="clearSelection"
          >
            <span
              v-for="tag in allImageTags"
              :key="`unassigned-${tag.id ?? tag.tag}`"
              :class="[
                'lightbox-tag',
                { 'lightbox-tag--penalised': isPenalisedTag(tag) },
                { 'lightbox-tag--sentinel': isSentinelTag(tagLabel(tag)) },
                { 'lightbox-tag--selected': isSelected(tagLabel(tag)) },
                {
                  'lightbox-tag--remove-hot':
                    removeHot && isSelected(tagLabel(tag)),
                },
                predictionClassForTag(tagLabel(tag)),
              ]"
              :style="predictionStyleForTag(tagLabel(tag))"
              :draggable="!readOnly"
              @dragstart="
                startTagDrag(tagLabel(tag), 'unassigned', null, $event)
              "
              @dragend="clearTagDrag"
              @click="onChipClick(tag, $event)"
              @contextmenu="onChipContextMenu(tag, $event)"
              @mouseenter="hoverTag(tag)"
              @mouseleave="hoverTag(null)"
            >
              <Tooltip
                :text="predictionTitleForTag(tagLabel(tag)) || ''"
                activator="parent"
              />
              <v-icon
                v-if="isBoxedTag(tag)"
                class="lightbox-tag-box"
                size="12"
                aria-hidden="true"
                >mdi-vector-square</v-icon
              >
              {{ formatSentinelTag(tagLabel(tag)) }}
              <button
                v-if="!readOnly"
                class="tag-delete-btn"
                :aria-label="removeLabel(tag)"
                @click.stop="onRemoveClick(tag)"
                @mouseenter="removeHot = isSelected(tagLabel(tag))"
                @mouseleave="removeHot = false"
              >
                <Tooltip :text="removeLabel(tag)" activator="parent" :describe="false" />
                <v-icon size="12">mdi-close</v-icon>
              </button>
            </span>
            <div v-if="!allImageTags.length" class="tag-drop-placeholder">
              Drop tags here
            </div>
            <input
              v-if="addingTag && !readOnly"
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
    <!-- Outside the collapse: rejected chips select too, under a collapsed Tags. -->
    <div
      v-if="selectedLabels.length"
      class="tag-selection-bar"
      role="toolbar"
      aria-label="Selected tags"
    >
      <span class="tag-selection-count" role="status"
        >{{ selectedLabels.length }} selected</span
      >
      <button type="button" class="tag-selection-btn" @click="copySelection">
        <Tooltip text="Copy (Ctrl+C)" activator="parent" :describe="false" />
        <v-icon size="14">mdi-content-copy</v-icon>
        Copy
      </button>
      <button
        type="button"
        class="tag-selection-btn tag-selection-btn--icon"
        aria-label="Clear selection"
        @click="clearSelection"
      >
        <Tooltip text="Clear selection (Esc)" activator="parent" :describe="false" />
        <v-icon size="14">mdi-selection-off</v-icon>
      </button>
    </div>
  </div>

  <div
    v-if="nearMissPredictions.length"
    class="sidebar-section sidebar-section--rejected-tags"
  >
    <SectionHeader
      v-model:collapsed="nearMissesCollapsed"
      label="Rejected tags"
      :count="nearMissPredictions.length"
      on-dark
    >
      <template #actions>
        <button
          class="section-meta-btn"
          type="button"
          aria-label="Copy all rejected tags"
          @click.stop="copyLabels(nearMissPredictions.map((p) => p.tag))"
        >
          <Tooltip
            text="Copy all rejected tags"
            activator="parent"
            :describe="false"
          />
          <v-icon size="16">mdi-content-copy</v-icon>
        </button>
      </template>
    </SectionHeader>
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
      @click.self="clearSelection"
    >
      <span
        v-for="pred in nearMissPredictions"
        :key="`pred-${pred.tag}`"
        :class="[
          'lightbox-tag',
          predictionClassForTag(pred.tag),
          'lightbox-tag--prediction',
          { 'lightbox-tag--selected': isSelected(pred.tag) },
        ]"
        :style="{ '--pred-confidence': pred.confidence }"
        :draggable="!readOnly"
        @dragstart="startTagDrag(pred.tag, 'rejected', null, $event)"
        @dragend="clearTagDrag"
        @click="onChipClick(pred.tag)"
        @contextmenu="onChipContextMenu(pred.tag, $event)"
      >
        <Tooltip :text="rejectedTagTitle(pred)" activator="parent" />
        {{ pred.tag }}
        <span class="tag-pred-confidence"
          >{{ (pred.confidence * 100).toFixed(0) }}%</span
        >
        <button
          v-if="!readOnly"
          class="tag-pred-btn tag-pred-btn--confirm"
          aria-label="Confirm prediction (add as tag)"
          @click.stop="confirmPrediction(pred.tag)"
        >
          <Tooltip
            text="Confirm prediction (add as tag)"
            activator="parent"
            :describe="false"
          />
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
 * LightboxTagsPanel
 *
 * Sidebar tags section extracted from Lightbox. Owns all tag state,
 * prediction fetching, penalised-tag loading, and tag autocomplete.
 *
 * Props:
 *   image        - The current lightbox image object (read-only).
 *   backendUrl   - Base URL for API calls (required).
 *   hiddenTags   - Array of hidden tag strings from user settings.
 *   applyTagFilter - Whether the hidden-tag filter is active.
 *
 * Emits:
 *   update-tags(newTagsArray)         - Tags changed locally; parent updates image.value.tags.
 *   lightbox-change(payload)           - Re-emitted for grid/App awareness.
 *   add-tag(imageId, tag)             - New tag confirmed; for grid thumbnail updates.
 *   request-metadata-refresh(imageId) - Panel needs parent to call fetchLightboxMetadata.
 *
 * Exposes:
 *   addingTag              - Whether the tag input is active (for parent keyboard handler).
 *   beginAddTag()          - Activate tag input (keyboard shortcut T).
 *   cancelAddTag()         - Cancel tag input (keyboard ESC).
 *   refetchPredictions(id) - Re-fetch predictions after parent metadata refresh.
 */
import { ref, reactive, computed, watch, nextTick, onMounted } from "vue";
import { API_BASE_URL, isReadOnly, newOperationBatchId } from "../../utils/apiClient";
import {
  listTags,
  removeTagEverywhere,
  listTagPredictions,
  confirmTagPrediction,
  rejectTagPrediction,
} from "../../api/tags";
import { getPictureMetadata, resetPictureTags } from "../../api/pictures";
import { confirmRetag } from "../../composables/confirmRetag";
import { listTaggers } from "../../api/taggers";
import { getUserConfig } from "../../api/config";
import { getPenalisedTags } from "../../api/users";
import Tooltip from "../widgets/Tooltip.vue";
import SectionHeader from "../widgets/SectionHeader.vue";
import { useSectionCollapse } from "../../composables/useSectionCollapse";
import { useTagClipboardStore } from "../../stores/useTagClipboardStore";
import {
  dedupeTagList,
  getTagLabel as tagLabel,
  getTagList,
  isSentinelTag,
  formatSentinelTag,
} from "../../utils/tags.js";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";

const props = defineProps({
  image: { type: Object, default: null },
  backendUrl: { type: String, default: () => API_BASE_URL },
  hiddenTags: { type: Array, default: () => [] },
  applyTagFilter: { type: Boolean, default: false },
  // True when the picture is frozen by a locked set: render tags read-only.
  locked: { type: Boolean, default: false },
  // Lock-reason tooltip copy (single source from useLockedSetsStore).
  lockNote: { type: String, default: "" },
  // Lower-cased labels the object detector found on the picture. A tag that
  // names one carries a box glyph, and pointing at it draws that box.
  boxedTags: { type: Set, default: () => new Set() },
});

// Compose the app-wide read-only (token capability) with the data-state lock.
// Either makes tag editing (add / remove / confirm / drag) unavailable.
const readOnly = computed(() => isReadOnly.value || props.locked);

const emit = defineEmits([
  "update-tags",
  "lightbox-change",
  "add-tag",
  "request-metadata-refresh",
  "hover-tag",
]);

function isBoxedTag(tag) {
  return props.boxedTags.has(String(tagLabel(tag)).trim().toLowerCase());
}

/** The tag pointed at, when the detector found it: the lightbox draws its box. */
let hoveredLabel = null;
function hoverTag(tag) {
  hoveredLabel = tag && isBoxedTag(tag) ? tagLabel(tag) : null;
  emit("hover-tag", hoveredLabel);
}

// ── Tag UI state ───────────────────────────────────────────────────────────

const tagsCollapsed = useSectionCollapse("tags");
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

// ── Tag copy and paste state (#2139) ────────────────────────────────────────
// Chips selected by clicking them, labels in click order.
const tagClipboard = useTagClipboardStore();
const selectedLabels = ref([]);
const clipboardCount = computed(() => tagClipboard.clipboardTags.length);
const pasteTooltip = computed(
  () => `Paste ${tagClipboard.clipboardTags.join(", ")} (Ctrl+V)`,
);
const hasSelection = computed(() => selectedLabels.value.length > 0);
// The pointer is on the x of a selected chip: every selected chip's x lights
// up, because that click removes the whole selection.
const removeHot = ref(false);

// ── Prediction state ───────────────────────────────────────────────────────

const tagPredictions = ref([]);
const predictionAcceptanceThreshold = ref(0.95);
const labelThresholds = ref({});

const nearMissesCollapsed = useSectionCollapse("rejectedTags");

// ── Penalised tags ─────────────────────────────────────────────────────────

const penalisedTags = ref(new Set());
const penalisedTagsLoading = ref(false);

async function fetchPenalisedTags() {
  if (penalisedTagsLoading.value) return;
  penalisedTagsLoading.value = true;
  try {
    // A READ share session cannot read the whole config blob, so the same
    // data comes from a narrower endpoint for those sessions.
    const body = isReadOnly.value
      ? await getPenalisedTags()
      : await getUserConfig();
    let list = [];
    if (Array.isArray(body?.smart_score_penalised_tags)) {
      list = body.smart_score_penalised_tags;
    } else if (
      body?.smart_score_penalised_tags &&
      typeof body.smart_score_penalised_tags === "object"
    ) {
      list = Object.keys(body.smart_score_penalised_tags);
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

// A chip removed under the pointer sends no mouseleave: let its box go too.
watch(allImageTags, (tags) => {
  if (hoveredLabel && !tags.some((tag) => tagLabel(tag) === hoveredLabel)) {
    hoverTag(null);
  }
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
    const payload = await listTagPredictions(imageId);
    if (!props.image || props.image.id !== imageId) return;
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
    selectedLabels.value = [];
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

// A `model_version === "manual"` row is not a tagger prediction: the human-label
// ledger synthesises it to hold a manual POS/NEG decision when no real prediction
// exists, hard-coding confidence to 1.0 (POS) / 0.0 (NEG) as a placeholder (see
// backend label_ledger.record_human_label). Surfacing its confidence as a model
// score is the bug: a manually-added penalised tag reads "100%", and after removal
// the same synthetic row (now REJECTED, still confidence 1.0) lingers in Rejected
// Tags at 100%. Only genuine tagger rows carry a meaningful confidence, so the
// confidence UI must ignore synthetic manual rows entirely.
const modelPredictions = computed(() =>
  tagPredictions.value.filter((p) => p.model_version !== "manual"),
);

const confirmedTagNames = computed(() => {
  const names = new Set();
  for (const tag of allImageTags.value) {
    const label = tagLabel(tag);
    if (label) names.add(label.trim().toLowerCase());
  }
  return names;
});

const nearMissPredictions = computed(() => {
  return modelPredictions.value.filter(
    (p) =>
      p.status === "REJECTED" &&
      p.confidence >= 0.3 &&
      !confirmedTagNames.value.has(p.tag.trim().toLowerCase()),
  );
});

const pendingPredictionMap = computed(() => {
  const map = new Map();
  for (const p of modelPredictions.value) {
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
    return "lightbox-tag--predicted-anomaly";
  }
  return "lightbox-tag--predicted-normal";
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
    const rows = await listTags({ baseUrl: props.backendUrl });
    if (Array.isArray(rows)) {
      allAvailableTags.value = rows;
      allAvailableTagsFetchedAt = now;
    }
  } catch (e) {
    // Non-critical: autocomplete just stays empty. Log it so a persistently
    // failing fetch is visible.
    console.debug("Failed to refresh the tag autocomplete list", e);
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
  if (readOnly.value) return; // locked / read-only: drops never mutate labels
  if (!draggedTag || sourceType !== "rejected") return;
  confirmPrediction(draggedTag);
}

async function handleDropOnRejectedTags() {
  const draggedTag = dragState.tag;
  const sourceType = dragState.sourceType;
  clearTagDrag();
  if (readOnly.value) return; // locked / read-only: drops never mutate labels
  if (!draggedTag || sourceType !== "unassigned") return;
  const key = String(draggedTag).trim().toLowerCase();
  const tagObj = allImageTags.value.find(
    (entry) => tagLabel(entry).trim().toLowerCase() === key,
  );
  // One drag, one undo step: every request this gesture fans out shares a
  // batch id (docs/backend_architecture.md §21.2).
  const batchId = newOperationBatchId();
  await removeAllTag(tagObj || { tag: draggedTag }, { batchId });
  await rejectPrediction(draggedTag, { batchId });
}

// ── Tag mutation functions ──────────────────────────────────────────────────

/**
 * Delete a tag chip: remove the tag library-wide, then record the rejection
 * that keeps the tagger from re-suggesting it.
 *
 * Both requests carry one gesture batch id, so the two operations they record
 * are one history step and one Ctrl+Z restores the tag AND the ledger. Without
 * it the first undo reverted only the ledger and looked like a no-op.
 *
 * @param {Object|string} tag - the chip (or a bare `{ tag }`) to remove.
 * @param {Object} [options]
 * @param {string} [options.batchId] - an id from a caller that is already part
 *   of a larger gesture; a fresh one is minted otherwise.
 */
async function removeAllTag(tag, { batchId } = {}) {
  await removeTagsFromPicture([tag], { batchId });
}

/**
 * Remove several tags from this picture: one local update, one batch id, so a
 * multi-chip removal is one undo step.
 *
 * @param {Array<Object|string>} tags - chips or bare labels.
 * @param {Object} [options]
 * @param {string} [options.batchId] - an id from a larger gesture; a fresh one
 *   is minted otherwise.
 */
async function removeTagsFromPicture(tags, { batchId = newOperationBatchId() } = {}) {
  const labels = [...new Set((tags || []).map((t) => tagLabel(t)).filter(Boolean))];
  if (!labels.length) return;
  labels.forEach((label) => unpinUserVisibleHiddenTag(label));
  const currentTags = getTagList(props.image?.tags);
  const next = currentTags.filter((entry) => !labels.includes(entry.tag));
  const didUpdate = next.length !== currentTags.length;
  if (didUpdate) {
    emit("update-tags", next);
  }

  const capturedImageId = props.image?.id ?? null;
  if (capturedImageId && props.backendUrl) {
    for (const label of labels) {
      try {
        await removeTagEverywhere(capturedImageId, label, { batchId });
      } catch (err) {
        console.warn("Failed to remove tag everywhere:", err);
      }
    }
  }

  if (didUpdate && capturedImageId) {
    for (const label of labels) {
      await rejectPrediction(label, { batchId });
    }
    emit("lightbox-change", {
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
    await confirmTagPrediction(imageId, tag);
    void fetchTagPredictions(imageId);
  } catch (e) {
    emit("update-tags", prevTags);
    tagPredictions.value = prevPredictions;
    console.error("Failed to confirm prediction:", e);
  }
}

/**
 * Record the human NEG that makes a tag removal durable supervision.
 *
 * @param {string} tag - the tag being rejected.
 * @param {Object} [options]
 * @param {string} [options.batchId] - the gesture batch id of the removal this
 *   reject belongs to, so the pair is one undo step.
 */
async function rejectPrediction(tag, { batchId } = {}) {
  if (!props.image?.id || !props.backendUrl) return;
  const imageId = props.image.id;
  const key = String(tag).trim().toLowerCase();
  try {
    await rejectTagPrediction(imageId, tag, {
      batchId,
    });
    tagPredictions.value = tagPredictions.value.map((p) =>
      p.tag.trim().toLowerCase() === key ? { ...p, status: "REJECTED" } : p,
    );
  } catch (e) {
    // Network error: fall through to ensure the local entry below so the chip
    // still reflects the user's decision. Log it rather than drop it.
    console.debug(`Failed to reject the prediction "${tag}" server-side`, e);
  }
  if (
    !tagPredictions.value.some(
      (p) => p.tag.trim().toLowerCase() === key && p.status === "REJECTED",
    )
  ) {
    // Mirror the backend's synthetic 'manual' NEG row (record_human_label):
    // confidence is the tagger's P(applies), which is 0 for a tag it never
    // predicted - not 1.0. Using 0 keeps the optimistic chip consistent with
    // what a refetch returns (and below the 0.3 near-miss threshold, so a
    // removed manual tag doesn't masquerade as a high-confidence rejection).
    tagPredictions.value = [
      ...tagPredictions.value,
      { tag: String(tag), confidence: 0.0, status: "REJECTED" },
    ];
  }
}

async function fetchTagPlugins() {
  if (tagPluginsLoading.value || tagPlugins.value.length) return;
  tagPluginsLoading.value = true;
  try {
    const body = await listTaggers();
    tagPlugins.value = (body?.plugins ?? []).filter((p) => p.supports_tags);
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
  if (!(await confirmRetag(1))) return;

  isTagsRefreshing.value = true;
  try {
    const body = model ? { model } : {};
    await resetPictureTags(capturedImageId, body);
    tagPredictions.value = [];
    emit("update-tags", []);
    emit("lightbox-change", {
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

// ── Tag copy and paste: selection, copy, paste (#2139) ─────────────────────

function tagWord(n) {
  return n === 1 ? "tag" : "tags";
}

function isSelected(label) {
  return selectedLabels.value.includes(label);
}

function clearSelection() {
  selectedLabels.value = [];
  removeHot.value = false;
}

function toggleSelected(label) {
  selectedLabels.value = isSelected(label)
    ? selectedLabels.value.filter((l) => l !== label)
    : [...selectedLabels.value, label];
}

function copyLabels(labels) {
  return tagClipboard.copyTags([...new Set(labels)]);
}

function copySelection() {
  return copyLabels([...selectedLabels.value]);
}

/** A click selects or deselects a chip, with or without a modifier held. */
function onChipClick(tag) {
  const label = tagLabel(tag);
  if (label) toggleSelected(label);
}

/** A right-click selects the chip, so the selection bar is right there. */
function onChipContextMenu(tag, event) {
  const label = tagLabel(tag);
  if (!label) return;
  event.preventDefault();
  if (!isSelected(label)) selectedLabels.value = [...selectedLabels.value, label];
}

async function refreshTags() {
  const id = props.image?.id;
  if (!id) return;
  try {
    const data = await getPictureMetadata(id);
    if (props.image?.id !== id || !Array.isArray(data?.tags)) return;
    emit("update-tags", data.tags);
    emit("lightbox-change", {
      imageId: id,
      fields: { tags: true, smartScore: true },
    });
  } catch (err) {
    console.warn("Failed to refresh the tags of picture", id, err);
  }
}

/** Ctrl+V, or the Paste button: add the copied tags to this picture. */
async function pasteClipboard() {
  const id = props.image?.id;
  if (!id || readOnly.value) return;
  const result = await tagClipboard.pasteClipboard([id]);
  if (result?.pictureIds.length) await refreshTags();
}

/**
 * The selected chips that are this picture's tags, not rejected ones, spelt as
 * the tag is: removal matches the label exactly.
 */
function selectedTagLabels() {
  const keys = new Set(selectedLabels.value.map((l) => l.trim().toLowerCase()));
  return allImageTags.value
    .map((t) => tagLabel(t))
    .filter((l) => keys.has(l.trim().toLowerCase()));
}

/** "Remove tag", or for a selected chip, what its x removes: the selection. */
function removeLabel(tag) {
  const n = selectedTagLabels().length;
  if (n > 1 && isSelected(tagLabel(tag))) return `Remove ${n} selected tags (Del)`;
  return "Remove tag";
}

/** The x on a chip: the whole selection when the chip is in it, else the chip. */
function onRemoveClick(tag) {
  if (isSelected(tagLabel(tag))) void removeSelection();
  else void removeAllTag(tag);
}

/**
 * A selected chip's x, or Delete with chips selected: one batch for all. A
 * selected rejected chip is only there to copy; it is never removed.
 */
async function removeSelection() {
  const labels = selectedTagLabels();
  if (readOnly.value || !labels.length) return;
  clearSelection();
  await removeTagsFromPicture(labels);
}

// ── Public API (exposed to parent via template ref) ─────────────────────────

defineExpose({
  addingTag,
  beginAddTag,
  cancelAddTag,
  refetchPredictions: fetchTagPredictions,
  hasSelection,
  clearSelection,
  copySelection,
  removeSelection,
  pasteClipboard,
  refreshTags,
});
</script>

<style scoped>
/* Section layout must live here rather than in Lightbox's scoped block.
   This component has multiple root nodes, so Vue never stamps the parent's
   scope id onto them (the renderer only forwards it to a *single* root), which
   means the parent's `.sidebar-section--tags` rules silently never match.
   LightboxDescriptionPanel carries its own copy for the same reason. Without
   these, the sections are plain auto-height blocks and the `overflow-y: auto`
   below can never resolve into a scrollbar. */
/* A definite, shrinkable height is what gives `.tag-list` something to scroll
   within: the lightbox sidebar is a fixed-height flex column, so this section
   yields down to `min-height` when the column runs out of room. */
.sidebar-section--tags {
  display: flex;
  flex-direction: column;
  min-height: 104px;
}

.sidebar-section--tags.sidebar-section--collapsed {
  min-height: 0;
}

/* Already bounded by the drop zone's own `max-height`, so it must NOT shrink:
   a flex item squeezed below its content still paints that content (the zone is
   `overflow: visible` in its unshrunk state), which drops the rejected chips on
   top of the Metadata section below. */
.sidebar-section--rejected-tags {
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
}

/* The object detector found this one: pointing at it draws its box. */
.lightbox-tag-box {
  margin-right: var(--space-1);
  opacity: var(--opacity-text-secondary);
}

.section-meta-btn {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  padding: var(--space-1);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.section-meta-btn:hover:not(:disabled) {
  color: rgb(var(--v-theme-on-dark-surface));
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
  gap: var(--space-2);
  padding-right: var(--space-2);
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  /* Reserve the bar's lane up front: without it the chips reflow every time a
     tag is added or removed across the overflow threshold. */
  scrollbar-gutter: stable;
}

/* The overlay sidebar is a `dark-surface`, so the scrollbar keys off
   `on-dark-surface`. The global `.is-desktop` treatment in style.css (an amber
   thumb for the light chrome) does not apply in a plain browser at all, which
   left an OS-default bar on a translucent dark panel. The bar is
   also the whole "there is more below" affordance here, so it stays visible
   rather than hiding until hover. Track is transparent: the drop zone's dashed
   border already draws the edge and a second line would compete. */
.tag-list,
.tag-drop-zone--predictions {
  scrollbar-width: thin;
  /* 0.40 is the floor, not a taste call: over this panel it measures 3.19:1
     against the dark theme's pane (3.28:1 on the light theme's), clearing WCAG 1.4.11's 3:1 for a UI component. The
     0.1-0.2 alphas used for borders in this file land near 2:1 and would make
     the bar decorative rather than perceivable. */
  scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.4) transparent;
}

.tag-list:hover,
.tag-drop-zone--predictions:hover {
  scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.55) transparent;
}

.lightbox-lock-note {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.tag-refresh-indicator {
  display: inline-flex;
  align-items: center;
  padding: var(--space-1) var(--space-2);
  margin-right: var(--space-2);
}

.lightbox-tag {
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: 6px; /* no clean token: 6px is equidistant between --radius-sm(4px) and --radius-md(8px) */
  padding: 1px var(--space-1) 1px 6px; /* no clean token: 1px and 6px are optical nudges */
  font-size: var(--tag-chip-font, var(--text-xs));
  user-select: none;
  line-height: 1.2;
  justify-content: center;
  vertical-align: middle;
  cursor: pointer;
  /* Flex items floor at min-content, so a single long tag would otherwise force
     the row wider than the sidebar and get clipped by `overflow-x`. */
  min-width: 0;
  overflow-wrap: anywhere;
}

.lightbox-tag--penalised {
  color: rgb(var(--v-theme-dark-surface-error));
  font-size: var(--tag-chip-font, var(--text-xs));
  line-height: 1.2;
  border: 1px solid rgba(var(--v-theme-dark-surface-error), 0.6);
  background: rgba(var(--v-theme-dark-surface-error), 0.15);
}

.lightbox-tag--sentinel {
  font-weight: var(--weight-bold);
  opacity: 0.85;
  pointer-events: none;
  background-color: rgb(var(--v-theme-tertiary));
  color: rgb(var(--v-theme-on-tertiary));
}

.lightbox-tag--predicted-anomaly {
  --ac: clamp(0.35, var(--pred-confidence, 0.6), 1);
  font-size: var(--tag-chip-font, var(--text-xs));
  color: color-mix(
    in srgb,
    rgb(var(--v-theme-on-dark-surface)) calc((1 - var(--ac)) * 100%),
    rgb(var(--v-theme-dark-surface-error)) calc(var(--ac) * 100%)
  );
  border-color: color-mix(
    in srgb,
    rgba(var(--v-theme-on-dark-surface), 0.2) calc((1 - var(--ac)) * 100%),
    rgba(var(--v-theme-dark-surface-error), 0.7) calc(var(--ac) * 100%)
  );
  background: color-mix(
    in srgb,
    rgba(var(--v-theme-on-dark-surface), 0.05) calc((1 - var(--ac)) * 100%),
    rgba(var(--v-theme-dark-surface-error), 0.2) calc(var(--ac) * 100%)
  );
}

.lightbox-tag--predicted-normal {
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

.lightbox-tag--prediction {
  filter: saturate(0.82) brightness(0.9);
  opacity: 0.88;
  border-style: dashed;
  border-width: 1px;
}

.tag-pred-confidence {
  font-size: var(--text-2xs);
  opacity: 0.7;
  margin-left: var(--space-1);
}

.tag-pred-btn {
  margin: 0 1px;
  padding: 1px;
  font-size: 0.75em;
  line-height: 1;
  vertical-align: middle;
  opacity: 0.6;
}

.tag-pred-btn:hover {
  opacity: 1;
}

.tag-pred-btn--confirm:hover {
  color: rgb(var(--v-theme-dark-surface-success));
}

.tag-pred-btn--reject:hover {
  color: rgb(var(--v-theme-dark-surface-error));
}

.tag-drop-zone {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding: var(--space-2);
  border-radius: var(--radius-md);
  border: 1px dashed rgba(var(--v-theme-on-dark-surface), 0.2);
  min-height: 26px;
  max-height: none;
  overflow: visible;
}

.tag-drop-zone--active {
  border-color: rgba(var(--v-theme-on-dark-surface), 0.6);
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}

/* Secondary list: cap it so a long rejection set cannot push Metadata out of
   the sidebar, and scroll the remainder. */
.tag-drop-zone--predictions {
  gap: var(--space-2);
  /* ~4 chip rows: a 11px/1.2 chip is ~17px, plus the --space-2 row gap and the
     zone's own --space-2 padding. Deliberately shallower than the applied list
     above it, so the secondary list reads as secondary. */
  max-height: 92px;
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.tag-drop-placeholder {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.45);
}

/* A selected chip wears the selection colour: an outline and a wash of it. */
.lightbox-tag--selected {
  outline: 1px solid var(--selected-ink);
  background: color-mix(in srgb, var(--selected-ink) 28%, transparent);
}

.tag-selection-bar {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin-top: var(--space-2);
  padding: var(--space-1) var(--space-1) var(--space-1) var(--space-3);
  border: 1px solid var(--selected-ink);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--selected-ink) 16%, transparent);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-dark-surface));
}

.tag-selection-count {
  margin-right: auto;
  white-space: nowrap;
  font-weight: var(--weight-medium);
}

.tag-selection-btn,
.tag-paste-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: 24px;
  padding: 0 var(--space-2);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-dark-surface));
  font: inherit;
  font-size: var(--text-xs);
  white-space: nowrap;
  cursor: pointer;
}

.tag-selection-btn:hover,
.tag-paste-btn:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}

/* The x of every selected chip while the pointer is on one of them. */
.lightbox-tag--remove-hot .tag-delete-btn,
.lightbox-tag--remove-hot .tag-delete-btn:hover {
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-dark-surface-error));
  background: rgba(var(--v-theme-dark-surface-error), 0.2);
}

.tag-selection-btn--icon {
  padding: 0 var(--space-1);
}

/* In the section header, on the icon buttons' row: the same 24px height and
   the group's own gap, the label in the body weight rather than the header's
   uppercase semibold. */
.tag-paste-btn {
  border: 1px solid var(--selected-ink);
  line-height: 1;
  font-weight: var(--weight-medium);
  text-transform: none;
  letter-spacing: normal;
}

.tag-delete-btn {
  margin: 0;
  padding: var(--space-1);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  font-size: 0.8em; /* relative em scale, not absolute px; no token */
  line-height: 1;
  vertical-align: middle;
}

.tag-delete-btn:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}

.tag-add-input {
  background: rgba(var(--v-theme-shadow), 0.4);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: var(--radius-pill);
  padding: 1px 6px; /* no clean token: 1px and 6px are optical nudges */
  font-size: var(--text-2xs);
}

.tag-autocomplete-dropdown {
  position: fixed;
  z-index: var(--z-dropdown);
  background: color-mix(in srgb, rgb(var(--v-theme-shadow)) 85%, transparent);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-3);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.tag-autocomplete-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 5px 10px; /* no clean token: 5px is between --space-2(4px) and --space-3(8px); 10px between --space-3(8px) and --space-4(12px) */
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The dropdown is dark in both themes: hover is the dark-surface ink wash, the
   highlighted suggestion the dark-surface olive. */
.tag-autocomplete-dropdown--hover-enabled .tag-autocomplete-item:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}

.tag-autocomplete-item--active {
  background: rgba(var(--v-theme-dark-surface-primary), 0.2);
  color: rgb(var(--v-theme-on-dark-surface));
}

.tag-autocomplete-tab-hint {
  display: inline-block;
  margin-left: var(--space-3);
  padding: 0 var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.04em;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.15);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  vertical-align: middle;
  line-height: 1.5;
}
</style>
