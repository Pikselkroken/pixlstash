<template>
  <!-- Tagging a selection, from the grid's rail or a review card: the tag
       field T puts the caret in, its completions, the selection's tags and
       the tags the tagger rejected for it. Several pictures' chips are tinted
       by how many of them carry the tag (ink); one picture's by how sure the
       tagger was (teal); anomaly tags wear the error hue at the front. -->
  <div ref="rootRef" class="rail-tags">
    <div v-if="!readOnly" class="rail-tags__compose">
      <div
        class="rail-tags__field"
        :class="{ 'rail-tags__field--focus': focused }"
        @mousedown.self.prevent="focus()"
      >
        <v-icon size="16" aria-hidden="true">mdi-tag-plus-outline</v-icon>
        <input
          ref="inputRef"
          v-model="draft"
          class="rail-tags__input"
          :placeholder="many ? `Add a tag to all ${n}` : 'Add a tag'"
          :aria-label="many ? `Add a tag to all ${n}` : 'Add a tag'"
          aria-keyshortcuts="T"
          autocomplete="off"
          spellcheck="false"
          role="combobox"
          aria-autocomplete="list"
          :aria-controls="listId"
          :aria-expanded="suggestionsOpen ? 'true' : 'false'"
          :aria-activedescendant="activeId"
          @focus="onFocus"
          @blur="focused = false"
          @keydown="onKey"
        />
        <template v-if="focused && draft.trim()">
          <span v-if="many" class="rail-tags__to">to all {{ n }}</span>
          <kbd class="fm-kbd" aria-hidden="true">↵</kbd>
        </template>
        <kbd v-else-if="!focused" class="fm-kbd" aria-hidden="true">T</kbd>
      </div>
      <div
        v-if="suggestionsOpen"
        :id="listId"
        class="rail-tags__sugg"
        role="listbox"
        aria-label="Tag suggestions"
      >
        <!-- mousedown only keeps the field focused; the click picks. -->
        <span
          v-for="(item, i) in suggestions"
          :id="`${listId}-${i}`"
          :key="item.tag"
          class="rail-chip rail-chip--sugg"
          :class="{ 'rail-chip--on': i === suggestionIndex }"
          role="option"
          :aria-selected="i === suggestionIndex ? 'true' : 'false'"
          @mousedown.prevent
          @click="pick(item)"
        >
          <span
            ><mark>{{ item.tag.slice(0, query.length) }}</mark
            >{{ item.tag.slice(query.length) }}</span
          >
          <span class="rail-chip__n">{{ item.note }}</span>
          <!-- The completion Tab will take, marked on the chip itself. -->
          <kbd v-if="i === tabTarget" class="fm-kbd" aria-hidden="true">Tab</kbd>
        </span>
      </div>
      <p v-if="tagError" class="rail-tags__note rail-tags__note--error" role="alert">
        {{ tagError }}
      </p>
      <p v-else-if="retagNote" class="rail-tags__note" role="status">
        {{ retagNote }}
      </p>
    </div>

    <div class="rail-sec">
      <span class="rail-sec__label">Tags</span>
      <span v-if="applied.length" class="rail-sec__count">{{
        applied.length.toLocaleString()
      }}</span>
      <span class="rail-sec__sp"></span>
      <button
        v-if="!many && applied.length"
        class="rail-ib"
        type="button"
        aria-label="Copy all tags"
        @click="tagClipboard.copyTags(applied.map((t) => t.name))"
      >
        <Tooltip text="Copy all tags" activator="parent" :describe="false" />
        <v-icon size="16">mdi-content-copy</v-icon>
      </button>
      <button
        v-if="!readOnly && clipboardCount"
        class="rail-ib"
        type="button"
        :aria-label="pasteLabel"
        @click="pasteClipboard"
      >
        <Tooltip :text="pasteTooltip" activator="parent" :describe="false" />
        <v-icon size="16">mdi-clipboard-outline</v-icon>
      </button>
      <span v-if="!readOnly" class="rail-split">
        <button
          class="rail-ib"
          type="button"
          :aria-label="retagLabel"
          :disabled="retagging"
          @click="retag()"
        >
          <Tooltip :text="retagLabel" activator="parent" :describe="false" />
          <v-icon size="16">mdi-auto-fix</v-icon>
        </button>
        <v-menu
          v-model="taggerMenuOpen"
          :close-on-content-click="true"
          location="bottom end"
        >
          <template #activator="{ props: menuProps }">
            <button
              class="rail-ib rail-ib--chevron"
              type="button"
              aria-label="Generate tags with a specific tagger"
              :disabled="retagging"
              v-bind="menuProps"
              @click="fetchTaggers"
            >
              <Tooltip
                text="Generate tags with a specific tagger…"
                activator="parent"
                :describe="false"
              />
              <v-icon size="14">mdi-menu-down</v-icon>
            </button>
          </template>
          <div
            class="ctx-menu"
            role="menu"
            tabindex="-1"
            style="min-width: 180px"
            @keydown="onMenuKeydown"
          >
            <button
              v-if="taggersLoading"
              type="button"
              class="ctx-item"
              role="menuitem"
              disabled
            >
              Loading…
            </button>
            <template v-else>
              <button
                v-for="plugin in taggers"
                :key="plugin.name"
                type="button"
                class="ctx-item"
                role="menuitem"
                @click="retag(plugin.name)"
              >
                {{ plugin.display_name || plugin.name }}
              </button>
              <button
                v-if="!taggers.length"
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
      </span>
    </div>
    <!-- A rejected chip dropped here is accepted; a tag dropped on Rejected
         below is removed and rejected: the ✓ and × as one drag. -->
    <div
      class="rail-tags__body rail-tags__applied"
      :class="{ 'rail-tags__body--drop': dropOn === 'applied' }"
      @dragover="onDragOver('applied', $event)"
      @dragleave="onDragLeave('applied', $event)"
      @drop.prevent="onDrop('applied')"
    >
      <p v-if="locked && lockNote" class="rail-tags__note">
        <Tooltip :text="lockNote" activator="parent" />
        <v-icon size="12">mdi-lock-outline</v-icon>
        Locked - read-only. Unlock the set to edit.
      </p>
      <p v-if="tagDataLoading" class="rail-tags__note">Reading tags…</p>
      <p v-else-if="tagReadFailed" class="rail-tags__note rail-tags__note--error">
        Couldn't read {{ many ? "these pictures'" : "this picture's" }} tags.
      </p>
      <p v-else-if="!applied.length" class="rail-tags__note">No tags yet.</p>
      <div v-else class="rail-tags__chips">
        <span
          v-for="t in shownApplied"
          :key="t.name"
          class="rail-chip"
          :class="{
            'rail-chip--conf': !many && !t.anomaly,
            'rail-chip--anomaly': t.anomaly,
            'rail-chip--selected': isSelected(t.name),
            'rail-chip--remove-hot': removeHot && isSelected(t.name),
          }"
          :style="{ '--tint': t.tint }"
          :data-tag="t.name"
          :draggable="readOnly ? undefined : 'true'"
          @dragstart="onDragStart('applied', t.name, $event)"
          @dragend="onDragEnd"
          @contextmenu="onChipContextMenu(t.name, $event)"
          @mouseenter="hoverTag(t.name)"
          @mouseleave="hoverTag(null)"
        >
          <Tooltip :text="chipTip(t)" activator="parent" />
          <v-icon v-if="t.anomaly" class="rail-chip__warn" size="13"
            >mdi-alert-outline</v-icon
          >
          <!-- The object detector found it: pointing at it draws its box. -->
          <v-icon
            v-if="isBoxed(t.name)"
            class="rail-chip__box"
            size="12"
            aria-hidden="true"
            >mdi-vector-square</v-icon
          >
          <!-- A click selects the chip, except on a part-tinted one, where
               it adds the tag to the rest; a modifier selects there too. -->
          <button
            class="rail-chip__label"
            type="button"
            :aria-label="
              addsToRest(t)
                ? `Add ${t.label} to the other ${totalWithTagData - t.count}`
                : undefined
            "
            :aria-pressed="addsToRest(t) ? undefined : String(isSelected(t.name))"
            :disabled="addsToRest(t) && tagActionLoading.includes(t.name)"
            @click="onChipLabelClick(t, $event)"
          >
            {{ t.label }}
          </button>
          <span v-if="many" class="visually-hidden"
            >, on {{ t.count }} of {{ totalWithTagData }}</span
          >
          <button
            v-if="!readOnly"
            class="rail-chip__x"
            type="button"
            :aria-label="removeLabel(t)"
            :disabled="tagActionLoading.includes(t.name)"
            @click="onRemoveClick(t)"
            @mouseenter="removeHot = isSelected(t.name)"
            @mouseleave="removeHot = false"
          >
            <v-icon size="12">mdi-close</v-icon>
          </button>
        </span>
      </div>
      <button
        v-if="hiddenSingles && !showSingles"
        class="rail-tags__more"
        type="button"
        @click="showSingles = true"
      >
        + {{ hiddenSingles }} more on one picture
      </button>
      <p v-if="tagDataCapped" class="rail-tags__note">
        Tags of the first {{ MAX_TAG_FETCH }}.
      </p>
    </div>

    <!-- Rejected chips select too, so the bar sits under both lists. -->
    <div
      v-if="selectedLabels.length"
      class="rail-tags__selbar"
      role="toolbar"
      aria-label="Selected tags"
    >
      <span class="rail-tags__selcount" role="status"
        >{{ selectedLabels.length }} selected</span
      >
      <button
        class="rail-ib"
        type="button"
        aria-label="Copy selected tags"
        @click="copySelection"
      >
        <Tooltip text="Copy (Ctrl+C)" activator="parent" :describe="false" />
        <v-icon size="16">mdi-content-copy</v-icon>
      </button>
      <button
        class="rail-ib"
        type="button"
        aria-label="Clear selection"
        @click="clearSelection"
      >
        <Tooltip text="Clear selection (Esc)" activator="parent" :describe="false" />
        <v-icon size="16">mdi-selection-off</v-icon>
      </button>
    </div>

    <template v-if="rejected.length || predictionReadFailed">
      <div class="rail-sec">
        <span class="rail-sec__label">Rejected</span>
        <span v-if="rejected.length" class="rail-sec__count">{{
          rejected.length.toLocaleString()
        }}</span>
        <span class="rail-sec__sp"></span>
        <button
          v-if="rejected.length"
          class="rail-ib"
          type="button"
          aria-label="Copy all rejected tags"
          @click="tagClipboard.copyTags(rejected.map((p) => p.tag))"
        >
          <Tooltip text="Copy all rejected tags" activator="parent" :describe="false" />
          <v-icon size="16">mdi-content-copy</v-icon>
        </button>
      </div>
      <div
        class="rail-tags__body rail-tags__rejected"
        :class="{ 'rail-tags__body--drop': dropOn === 'rejected' }"
        @dragover="onDragOver('rejected', $event)"
        @dragleave="onDragLeave('rejected', $event)"
        @drop.prevent="onDrop('rejected')"
      >
        <p
          v-if="predictionReadFailed"
          class="rail-tags__note rail-tags__note--error"
        >
          Some pictures' rejected tags couldn't be read.
        </p>
        <div v-if="shownRejected.length" class="rail-tags__chips">
          <span
            v-for="p in shownRejected"
            :key="p.tag"
            class="rail-chip rail-chip--quiet"
            :class="{
              'rail-chip--anomaly-quiet': p.anomaly,
              'rail-chip--selected': isSelected(p.tag),
            }"
            :data-tag="p.tag"
            :draggable="readOnly ? undefined : 'true'"
            @dragstart="onDragStart('rejected', p.tag, $event)"
            @dragend="onDragEnd"
            @contextmenu="onChipContextMenu(p.tag, $event)"
          >
            <Tooltip :text="rejectedTip(p)" activator="parent" />
            <v-icon v-if="p.anomaly" class="rail-chip__warn" size="13"
              >mdi-alert-outline</v-icon
            >
            <button
              class="rail-chip__label"
              type="button"
              :aria-pressed="String(isSelected(p.tag))"
              @click="toggleSelected(p.tag)"
            >
              {{ p.tag }}
            </button>
            <span class="rail-chip__n">{{ p.note }}</span>
            <button
              v-if="!readOnly"
              class="rail-chip__x"
              type="button"
              :aria-label="
                many ? `Accept ${p.tag} on ${p.count}` : `Accept ${p.tag}`
              "
              :disabled="predActionLoading.includes(p.tag)"
              @click="confirmPredictionOnAll(p)"
            >
              <v-icon size="12">mdi-check</v-icon>
            </button>
          </span>
        </div>
        <button
          v-if="faintRejected"
          class="rail-tags__more"
          type="button"
          :aria-expanded="showFaint ? 'true' : 'false'"
          @click="showFaint = !showFaint"
        >
          {{
            showFaint
              ? `Hide those under ${FAINT_PCT}%`
              : `+ ${faintRejected} under ${FAINT_PCT}%`
          }}
        </button>
      </div>
    </template>
  </div>
</template>

<script>
// What every mount completes against and marks as anomalies, read at most
// every 30 s and shared, so a rail that remounts per selection reads it once.
const libraryCache = { tags: [], fetchedAt: 0 };
const anomalyCache = { tags: new Set(), fetchedAt: 0 };
const listSerial = { n: 0 };
</script>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { isReadOnly, newOperationBatchId } from "../../utils/apiClient";
import { listTags } from "../../api/tags";
import { listTaggers } from "../../api/taggers";
import { resetPicturesTags } from "../../api/pictures";
import { getUserConfig } from "../../api/config";
import { getPenalisedTags } from "../../api/users";
import { confirmRetag } from "../../composables/confirmRetag";
import {
  MAX_TAG_FETCH,
  useSelectionTags,
} from "../../composables/useSelectionTags";
import {
  splitTagText,
  useTagClipboardStore,
} from "../../stores/useTagClipboardStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWsStore } from "../../stores/useWsStore";
import { errorDetail } from "../../utils/apiError";
import { hasNativeCopyContext, isTypingTarget } from "../../utils/dom.js";
import { formatSentinelTag } from "../../utils/tags.js";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** The pictures being tagged, in selection order. */
  ids: { type: Array, required: true },
  /** One picture frozen by a locked set: shown, never edited. */
  locked: { type: Boolean, default: false },
  lockNote: { type: String, default: "" },
  /** The grid's rail: T (`useSidebarStore.requestTagField`) focuses this field. */
  takesTagKey: { type: Boolean, default: false },
  /** Put the caret in the field on mount (a review card's Tag manually). */
  autofocus: { type: Boolean, default: false },
  /**
   * Lower-cased labels the object detector found (the lightbox): those chips
   * carry a box glyph, and pointing at one emits `hover-tag`.
   */
  boxedTags: { type: Set, default: () => new Set() },
});

/**
 * `tags-applied` after a change, for the grid to refresh what shows tags;
 * `leave` when Esc leaves the field with nothing left to dismiss;
 * `hover-tag` with the boxed tag pointed at, or null.
 */
const emit = defineEmits(["tags-applied", "leave", "hover-tag"]);

// Clicking to select changes the selection once per click; several pictures'
// reads wait for it to settle rather than going out for every step of it.
const SETTLE_MS = 250;
const SUGGESTIONS = 6;
// Rejections fainter than this are folded behind a link; anomalies never are.
const FAINT_PCT = 25;

const sidebarStore = useSidebarStore();
const userPrefsStore = useUserPrefsStore();
const wsStore = useWsStore();
const tagClipboard = useTagClipboardStore();

const readOnly = computed(() => isReadOnly.value || props.locked);
const pictureIds = computed(() =>
  props.ids.map(Number).filter((id) => Number.isFinite(id) && id > 0),
);
const n = computed(() => pictureIds.value.length);
const many = computed(() => n.value > 1);

const {
  fetchedPredictionData,
  predictionReadFailed,
  confidenceByTag,
  tagDataLoading,
  tagReadFailed,
  tagDataCapped,
  tagActionLoading,
  predActionLoading,
  tagError,
  predictionAcceptanceThreshold,
  labelThresholds,
  totalWithTagData,
  tagsOnAll,
  tagsOnSome,
  aggregatedPredictions,
  fetchSelectedImageTags,
  fetchSelectedImagePredictions,
  refreshSelectionTags,
  removeTagFromAll,
  addTagToRemaining,
  addTagsToAll,
  confirmPredictionOnAll,
  rejectTagOnAll,
} = useSelectionTags(() => props.ids, {
  onApplied: (payload) => emit("tags-applied", payload),
});

// ── Reads ────────────────────────────────────────────────────────────────────
const anomalies = ref(anomalyCache.tags);

async function fetchAnomalies() {
  if (Date.now() - anomalyCache.fetchedAt < 30_000) return;
  try {
    // A READ share session cannot read the whole config, so it asks the
    // narrower endpoint for the same list.
    const body = isReadOnly.value
      ? await getPenalisedTags()
      : await getUserConfig();
    const raw = body?.smart_score_penalised_tags;
    const list = Array.isArray(raw) ? raw : Object.keys(raw || {});
    anomalyCache.tags = new Set(
      list.map((t) => String(t || "").trim().toLowerCase()).filter(Boolean),
    );
    anomalyCache.fetchedAt = Date.now();
    anomalies.value = anomalyCache.tags;
  } catch (e) {
    console.warn("Couldn't read the anomaly tags; none are marked", e);
  }
}

const library = ref(libraryCache.tags);

async function fetchLibrary({ force = false } = {}) {
  if (!force && Date.now() - libraryCache.fetchedAt < 30_000) return;
  try {
    const rows = await listTags();
    if (Array.isArray(rows)) {
      libraryCache.tags = rows;
      libraryCache.fetchedAt = Date.now();
      library.value = rows;
    }
  } catch (e) {
    console.warn("Couldn't read the tag list; completions stay as they were", e);
  }
}

// ── The chips ────────────────────────────────────────────────────────────────
const fold = (name) => String(name || "").trim().toLowerCase();
const isAnomaly = (name) => anomalies.value.has(fold(name));
const isBoxed = (name) => props.boxedTags.has(fold(name));

// The reader's hidden tags, while their filter is on. A tag added here stays
// in sight until the selection changes, or it would vanish as it is typed.
const keptInSight = ref(new Set());
const hiddenTagKeys = computed(() =>
  userPrefsStore.applyTagFilter
    ? new Set((userPrefsStore.hiddenTags || []).map(fold).filter(Boolean))
    : new Set(),
);
const isHidden = (name) =>
  hiddenTagKeys.value.has(fold(name)) && !keptInSight.value.has(fold(name));

function keepInSight(names) {
  const next = new Set(keptInSight.value);
  for (const name of names) next.add(fold(name));
  keptInSight.value = next;
}

/**
 * Every applied tag with its strength: coverage for several (the share of
 * them that carry it), the tagger's confidence for one (1 for a tag added by
 * hand). Anomalies first, then the strongest.
 */
const applied = computed(() => {
  const total = totalWithTagData.value || 1;
  return [...tagsOnAll.value, ...tagsOnSome.value]
    .filter((t) => !isHidden(t.name))
    .map((t) => {
      const confidence = confidenceByTag.value.get(t.name.trim().toLowerCase());
      return {
        ...t,
        label: formatSentinelTag(t.name),
        anomaly: isAnomaly(t.name),
        confidence,
        tint: many.value ? t.count / total : (confidence ?? 1),
      };
    })
    .sort(
      (a, b) =>
        b.anomaly - a.anomaly ||
        b.tint - a.tint ||
        a.name.localeCompare(b.name),
    );
});

// On three or more, the long tail of tags on just one picture folds behind a
// link; anomalies never fold.
const showSingles = ref(false);
const folds = (t) =>
  totalWithTagData.value > 2 && t.count === 1 && !t.anomaly;
const hiddenSingles = computed(
  () => applied.value.filter(folds).length,
);
const shownApplied = computed(() =>
  showSingles.value ? applied.value : applied.value.filter((t) => !folds(t)),
);

/**
 * ×: the tag goes and the tagger is told it was wrong, one gesture, so one
 * undo step. Several tags (a selection) are one gesture too.
 */
async function removeAndReject(...tags) {
  const batchId = newOperationBatchId();
  for (const t of tags) {
    await removeTagFromAll(t, { batchId });
    await rejectTagOnAll(t, { batchId });
  }
}

/** A part-tinted chip's click adds it to the rest of the selection. */
const addsToRest = (t) => !readOnly.value && t.count < totalWithTagData.value;

function chipTip(t) {
  if (many.value) {
    const total = totalWithTagData.value;
    if (t.count === total) return `On all ${total}`;
    const rest = total - t.count;
    return readOnly.value
      ? `On ${t.count} of ${total}`
      : `On ${t.count} of ${total} · click to add it to the other ${rest}`;
  }
  return t.confidence == null
    ? "Added by hand"
    : `Tagger: ${Math.round(t.confidence * 100)}% sure`;
}

const rejected = computed(() =>
  aggregatedPredictions.value
    .map((p) => ({
      ...p,
      anomaly: isAnomaly(p.tag),
      note: many.value
        ? `${p.count}/${fetchedPredictionData.value.length}`
        : `${Math.round(p.avgConf * 100)}%`,
    }))
    .sort((a, b) => b.anomaly - a.anomaly),
);

// Faint rejections fold behind a link; anomalies never do.
const showFaint = ref(false);
const isFaint = (p) => !p.anomaly && p.avgConf * 100 < FAINT_PCT;
const faintRejected = computed(() => rejected.value.filter(isFaint).length);
const shownRejected = computed(() =>
  showFaint.value ? rejected.value : rejected.value.filter((p) => !isFaint(p)),
);

function rejectedTip(p) {
  const pct = Math.round(p.avgConf * 100);
  if (!many.value) {
    const perLabel = labelThresholds.value[p.tag];
    const threshold = Number.isFinite(perLabel)
      ? perLabel
      : Number(predictionAcceptanceThreshold.value) || 0.95;
    return `Rejected at ${pct}% (the tagger adds it from ${Math.round(threshold * 100)}%) · accept to add it`;
  }
  return `Rejected on ${p.count} of ${fetchedPredictionData.value.length}, ${pct}% on average · accept to add it to those ${p.count}`;
}

// ── Selection (#2139) ────────────────────────────────────────────────────────
// Chips picked to copy or remove together, labels in click order. Rejected
// chips select too, to copy; removal passes them over.
const selectedLabels = ref([]);
const hasSelection = computed(() => selectedLabels.value.length > 0);
// The pointer is on the × of a selected chip: every selected chip's × lights
// up, because that click removes the whole selection.
const removeHot = ref(false);

const isSelected = (label) => selectedLabels.value.includes(label);

function toggleSelected(label) {
  selectedLabels.value = isSelected(label)
    ? selectedLabels.value.filter((l) => l !== label)
    : [...selectedLabels.value, label];
}

function clearSelection() {
  selectedLabels.value = [];
  removeHot.value = false;
}

function onChipLabelClick(t, event) {
  const modified = event.ctrlKey || event.metaKey || event.shiftKey;
  if (addsToRest(t) && !modified) void addTagToRemaining(t);
  else toggleSelected(t.name);
}

/** A right-click selects the chip, so the selection bar is right there. */
function onChipContextMenu(label, event) {
  event.preventDefault();
  if (!isSelected(label)) selectedLabels.value = [...selectedLabels.value, label];
}

/** The selected chips that are applied tags, not rejected ones. */
const selectedApplied = () => applied.value.filter((t) => isSelected(t.name));

function copySelection() {
  return tagClipboard.copyTags([...new Set(selectedLabels.value)]);
}

async function removeSelection() {
  const tags = selectedApplied();
  if (readOnly.value || !tags.length) return;
  clearSelection();
  await removeAndReject(...tags);
}

function removeLabel(t) {
  const n = selectedApplied().length;
  if (n > 1 && isSelected(t.name)) return `Remove ${n} selected tags (Del)`;
  return many.value ? `Remove ${t.label} from ${t.count}` : `Remove ${t.label}`;
}

/** A selected chip's × removes the whole selection. */
function onRemoveClick(t) {
  if (isSelected(t.name)) void removeSelection();
  else void removeAndReject(t);
}

const rootRef = ref(null);

/** Out of sight: the lightbox's pane shut, or Info behind another tab. */
const hidden = () => Boolean(rootRef.value?.closest('[style*="display: none"]'));

/**
 * A selection is one gesture: a press anywhere outside these chips ends it,
 * so a chip picked a while ago never takes a key meant for the grid.
 */
function onWindowPointerdown(e) {
  if (hasSelection.value && !rootRef.value?.contains(e.target)) clearSelection();
}

/**
 * With chips selected, Ctrl+C copies them, Delete removes them and Escape
 * clears them, wherever focus is but a text field. Capture phase, so the
 * grid's and the lightbox's own handlers (Delete sends pictures to the
 * Scrapheap) never see the key.
 */
function onWindowKeydown(e) {
  if (!hasSelection.value || e.altKey || hidden()) return;
  // A menu or dialog over the panel takes its own keys.
  if (document.querySelector(".v-overlay--active.v-menu, .v-overlay--active.v-dialog")) {
    return;
  }
  let act = null;
  if (e.ctrlKey || e.metaKey) {
    if (!e.shiftKey && e.key?.toLowerCase() === "c" && !hasNativeCopyContext(e.target)) {
      act = copySelection;
    }
  } else if (!isTypingTarget(e.target)) {
    if (e.key === "Escape") act = clearSelection;
    else if (e.key === "Delete" || e.key === "Backspace") act = removeSelection;
  }
  if (!act) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  void act();
}

// ── Detected tags ────────────────────────────────────────────────────────────
let hoveredTag = null;
function hoverTag(name) {
  const next = name && isBoxed(name) ? name : null;
  if (next === hoveredTag) return;
  hoveredTag = next;
  emit("hover-tag", next);
}

// A chip removed under the pointer sends no mouseleave: let its box go too.
watch(applied, (tags) => {
  if (hoveredTag && !tags.some((t) => t.name === hoveredTag)) hoverTag(null);
});

// ── Drag between Tags and Rejected ───────────────────────────────────────────
const drag = ref(null);
const dropOn = ref(null);

function onDragStart(from, tag, event) {
  if (readOnly.value) return;
  drag.value = { from, tag };
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    // Firefox starts no drag without data.
    event.dataTransfer.setData("text/plain", tag);
  }
}

function onDragEnd() {
  drag.value = null;
  dropOn.value = null;
}

function onDragOver(to, event) {
  if (!drag.value || drag.value.from === to) return;
  event.preventDefault();
  dropOn.value = to;
}

function onDragLeave(to, event) {
  if (dropOn.value === to && !event.currentTarget.contains(event.relatedTarget)) {
    dropOn.value = null;
  }
}

async function onDrop(to) {
  const dragged = drag.value;
  onDragEnd();
  if (!dragged || dragged.from === to || readOnly.value) return;
  if (to === "applied") {
    const p = rejected.value.find((r) => r.tag === dragged.tag);
    if (p) await confirmPredictionOnAll(p);
  } else {
    const t = applied.value.find((a) => a.name === dragged.tag);
    if (t) await removeAndReject(t);
  }
}

// ── The field ────────────────────────────────────────────────────────────────
const inputRef = ref(null);
const draft = ref("");
const focused = ref(false);
const suggestionIndex = ref(-1);
// The text the completions were dismissed for (Esc or a pick); typing on
// brings them back.
const dismissedFor = ref(null);
const listId = `rail-tag-suggestions-${(listSerial.n += 1)}`;

const query = computed(() => draft.value.trim().toLowerCase());

/**
 * Library tags that start with what is typed, none the whole selection
 * already has. Each says how much of the selection has it, or else how many
 * pictures in the library do.
 */
const suggestions = computed(() => {
  if (!query.value) return [];
  const coverage = new Map(
    applied.value.map((t) => [t.name.toLowerCase(), t.count]),
  );
  const total = totalWithTagData.value;
  return library.value
    .filter((item) => {
      const tag = String(item?.tag || "").toLowerCase();
      return tag.startsWith(query.value) && coverage.get(tag) !== total;
    })
    .sort((a, b) => (b.count || 0) - (a.count || 0))
    .slice(0, SUGGESTIONS)
    .map((item) => {
      const on = coverage.get(item.tag.toLowerCase());
      return {
        tag: item.tag,
        note: on ? `${on}/${total}` : (item.count || 0).toLocaleString(),
      };
    });
});

const suggestionsOpen = computed(
  () =>
    focused.value &&
    suggestions.value.length > 0 &&
    dismissedFor.value !== draft.value,
);
// Tab takes the arrowed completion, or else the first. Clamped: a refetched
// library can shorten the list under a held index.
const tabTarget = computed(() =>
  Math.min(Math.max(0, suggestionIndex.value), suggestions.value.length - 1),
);
const activeId = computed(() =>
  suggestionsOpen.value && suggestionIndex.value >= 0
    ? `${listId}-${suggestionIndex.value}`
    : undefined,
);

watch(draft, () => {
  suggestionIndex.value = -1;
});

function onFocus() {
  focused.value = true;
  fetchLibrary();
}

function focus() {
  nextTick(() => inputRef.value?.focus({ preventScroll: false }));
}

function fill(item) {
  draft.value = item.tag;
  dismissedFor.value = draft.value;
}

function pick(item) {
  fill(item);
  void commit();
}

async function commit() {
  // Arrowed to a completion: that is the tag. Otherwise what is typed is,
  // comma-separated text (a prompt pasted in) being one tag per item.
  const active = suggestionsOpen.value && suggestions.value[suggestionIndex.value];
  if (active) fill(active);
  const sent = draft.value;
  const tags = splitTagText(sent);
  if (!tags.length || !n.value) return;
  // Empty at once, so the next tag can be typed while this one is written;
  // a failed add gives its text back unless something new was typed.
  draft.value = "";
  dismissedFor.value = null;
  retagNote.value = "";
  keepInSight(tags);
  if (await addTagsToAll(tags)) void fetchLibrary({ force: true });
  else if (!draft.value) draft.value = sent;
}

function onKey(event) {
  const list = suggestions.value;
  if (event.key === "Enter") {
    event.preventDefault();
    void commit();
  } else if (event.key === "ArrowDown") {
    if (!list.length) return;
    event.preventDefault();
    if (!suggestionsOpen.value) {
      dismissedFor.value = null;
      suggestionIndex.value = 0;
      return;
    }
    suggestionIndex.value = Math.min(suggestionIndex.value + 1, list.length - 1);
  } else if (event.key === "ArrowUp") {
    if (!suggestionsOpen.value) return;
    event.preventDefault();
    suggestionIndex.value = Math.max(suggestionIndex.value - 1, -1);
  } else if (event.key === "Tab") {
    // Tab completes, never writes. With nothing to complete it falls through
    // and focus moves on (WCAG 2.1.2).
    if (!suggestionsOpen.value) return;
    event.preventDefault();
    fill(list[tabTarget.value]);
  } else if (event.key === "Escape") {
    // One layer per press: the completions, then the field. The grid's own
    // Esc (clear the selection) waits for the next press.
    event.preventDefault();
    event.stopPropagation();
    if (suggestionsOpen.value) {
      dismissedFor.value = draft.value;
      suggestionIndex.value = -1;
      return;
    }
    inputRef.value?.blur();
    emit("leave");
  }
}

// T from the grid or the selection pill, from before this field existed too.
watch(
  () => props.takesTagKey && sidebarStore.tagFieldPending,
  (pending) => {
    if (!pending) return;
    // Taken even where there is no field to focus (a locked picture), or
    // the next T would set a flag that is already set and change nothing.
    sidebarStore.tagFieldPending = false;
    if (!readOnly.value) focus();
  },
  { immediate: true },
);

// ── Verbs ────────────────────────────────────────────────────────────────────
const clipboardCount = computed(() => tagClipboard.clipboardTags.length);
const pasteLabel = computed(() => {
  const what = `${clipboardCount.value} tag${clipboardCount.value === 1 ? "" : "s"}`;
  return many.value ? `Paste ${what} on all ${n.value}` : `Paste ${what}`;
});
const pasteTooltip = computed(
  () => `Paste ${tagClipboard.clipboardTags.join(", ")}`,
);

async function pasteClipboard() {
  if (readOnly.value) return;
  keepInSight(tagClipboard.clipboardTags);
  const result = await tagClipboard.pasteClipboard(pictureIds.value);
  if (result?.pictureIds.length) {
    emit("tags-applied", {
      tag: tagClipboard.clipboardTags.join(", "),
      pictureIds: result.pictureIds,
      action: "add",
    });
    await refreshSelectionTags();
  }
}

const taggerMenuOpen = ref(false);
const taggers = ref([]);
const taggersLoading = ref(false);
const retagging = ref(false);
const retagNote = ref("");
const retagLabel = computed(() =>
  many.value
    ? `Generate tags for all ${n.value} with the default tagger`
    : "Generate tags with the default tagger",
);

async function fetchTaggers() {
  if (taggersLoading.value || taggers.value.length) return;
  taggersLoading.value = true;
  try {
    const body = await listTaggers();
    taggers.value = (body?.plugins ?? []).filter((p) => p.supports_tags);
  } catch (e) {
    console.warn("Couldn't list the taggers", e);
    taggers.value = [];
  } finally {
    taggersLoading.value = false;
  }
}

/** Reset the selection's tags and queue it for the tagger (`model`, or the default). */
async function retag(model = null) {
  const ids = pictureIds.value;
  if (!ids.length || retagging.value) return;
  if (!(await confirmRetag(ids.length))) return;
  retagging.value = true;
  tagError.value = "";
  retagNote.value = "";
  try {
    await resetPicturesTags(ids, model ? { model } : {});
    const what = ids.length === 1 ? "picture" : `${ids.length} pictures`;
    retagNote.value = `Queued ${what} for tagging${model ? ` with ${model}` : ""}`;
    emit("tags-applied", { pictureIds: ids, action: "reset" });
    await refreshSelectionTags();
  } catch (e) {
    console.error("Couldn't queue the selection for tagging", ids, e);
    tagError.value = `Couldn't queue them for tagging: ${errorDetail(e) || e?.message || e}`;
  } finally {
    retagging.value = false;
  }
}

// ── Reading what the selection carries ─────────────────────────────────────
let settleTimer = null;
watch(
  () => pictureIds.value.join(","),
  () => {
    clearTimeout(settleTimer);
    showSingles.value = false;
    showFaint.value = false;
    retagNote.value = "";
    clearSelection();
    keptInSight.value = new Set();
    const read = () => {
      fetchSelectedImageTags();
      fetchSelectedImagePredictions();
    };
    if (many.value) settleTimer = setTimeout(read, SETTLE_MS);
    else read();
  },
  { immediate: true },
);

// A tagger run or another window changed these pictures' tags. This window's
// own edits re-read as they land, so only another origin's count here.
let wsTimer = null;
watch(
  () => wsStore.wsTagUpdate,
  (update) => {
    if (!update?.external) return;
    const mine = new Set(pictureIds.value.map(String));
    if (!(update.pictureIds || []).some((id) => mine.has(String(id)))) return;
    clearTimeout(wsTimer);
    wsTimer = setTimeout(() => void refreshSelectionTags(), SETTLE_MS);
  },
);

onMounted(() => {
  fetchAnomalies();
  if (props.autofocus) focus();
  window.addEventListener("keydown", onWindowKeydown, true);
  window.addEventListener("pointerdown", onWindowPointerdown, true);
});
onBeforeUnmount(() => {
  clearTimeout(settleTimer);
  clearTimeout(wsTimer);
  window.removeEventListener("keydown", onWindowKeydown, true);
  window.removeEventListener("pointerdown", onWindowPointerdown, true);
  hoverTag(null);
});

defineExpose({
  focus,
  /** The caret is in the tag field: a host defers re-renders that would blur it. */
  editing: focused,
  pasteClipboard,
});
</script>

<style scoped>
/* `--rail-line` and `--rail-field` are a host's way to re-ink the field and
   the quiet chips: the lightbox's pane is dark in both themes. */
.rail-tags {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  font-size: var(--text-xs);
}

.rail-tags__field {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--control-h-bar);
  padding: 0 var(--space-2) 0 var(--space-3);
  border: 1px solid var(--rail-line, rgb(var(--v-theme-border)));
  border-radius: var(--radius-sm);
  background: var(--rail-field, rgb(var(--v-theme-input-background)));
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: text;
}

/* The keycap's own inks are the page's (App.css); it takes the field's. */
.rail-tags__field .fm-kbd {
  border-color: var(--rail-line, rgb(var(--v-theme-border)));
  background: var(--rail-field, rgb(var(--v-theme-input-background)));
}

.rail-tags__field--focus {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
  box-shadow: var(--focus-glow);
}

.rail-tags__input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-sm);
}

.rail-tags__input::placeholder {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-tags__to {
  font-size: var(--text-2xs);
  white-space: nowrap;
}

.rail-tags__sugg {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.rail-tags__sugg mark {
  background: none;
  color: inherit;
  font-weight: var(--weight-semibold);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.rail-tags__note {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-tags__body > .rail-tags__note:first-child {
  margin-top: 0;
}

.rail-tags__note--error {
  color: rgb(var(--v-theme-surface-error));
}

/* A section's label row, as in the inspector spec: label, quiet count, verbs
   at the right edge. Never collapses: tagging is what the rail is for. */
.rail-sec {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-h-sm);
  margin-top: var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-sec__count {
  font-weight: var(--weight-medium);
  font-variant-numeric: tabular-nums;
}

.rail-sec__sp {
  flex: 1;
}

.rail-split {
  display: inline-flex;
}

.rail-ib {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
}

.rail-ib--chevron {
  width: auto;
  margin-left: calc(-1 * var(--space-1));
}

.rail-ib:hover:not(:disabled) {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.rail-ib:disabled {
  cursor: default;
  opacity: var(--opacity-disabled);
}

.rail-tags__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

/* One chip shape; the strength of its tint is the measure. `--tint` runs
   0..1: coverage for several (ink), confidence for one (teal). */
.rail-chip {
  --ink: var(--v-theme-on-surface);
  --a: calc(0.04 + var(--tint, 1) * 0.2);
  --b: calc(var(--a) + 0.06);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  max-width: 100%;
  min-height: var(--control-h-sm);
  padding: 0 var(--space-3);
  border: 1px solid rgba(var(--ink), var(--b));
  border-radius: var(--radius-sm);
  background: rgba(var(--ink), var(--a));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--tag-chip-font, var(--text-xs));
  overflow-wrap: anywhere;
}

.rail-chip--conf {
  --ink: var(--v-theme-tertiary);
  --a: calc(0.08 + var(--tint, 1) * 0.44);
  --b: calc(var(--a) + 0.2);
}

.rail-chip--anomaly {
  --ink: var(--v-theme-error);
  --b: calc(var(--a) + 0.25);
}

.rail-chip__warn {
  margin-left: calc(-1 * var(--space-1));
  color: rgb(var(--v-theme-surface-error));
}

.rail-chip__box {
  margin-left: calc(-1 * var(--space-1));
  opacity: var(--opacity-text-secondary);
}

/* Selected is olive (shell rule 3): the active wash and edge over the chip's
   own tint, so its measure still reads. */
.rail-chip--selected {
  border-color: var(--active-bar);
  box-shadow: inset 0 0 0 1px var(--active-bar);
  background-image: linear-gradient(var(--active-wash), var(--active-wash));
}

/* The × of every selected chip while the pointer is on one of them. */
.rail-chip--remove-hot .rail-chip__x {
  opacity: 1;
  color: rgb(var(--v-theme-surface-error));
}

.rail-tags__body--drop {
  border-radius: var(--radius-sm);
  outline: 1px dashed var(--active-bar);
  outline-offset: var(--space-1);
}

.rail-tags__selbar {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-1) 0 var(--space-3);
  border: 1px solid var(--active-bar);
  border-radius: var(--radius-sm);
  background: var(--active-wash);
}

.rail-tags__selcount {
  margin-right: auto;
  font-weight: var(--weight-medium);
  white-space: nowrap;
}

.rail-chip--quiet {
  background: transparent;
  border-color: var(--rail-line, rgb(var(--v-theme-border)));
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-chip--anomaly-quiet {
  background: rgba(var(--v-theme-error), 0.08);
  border-color: rgba(var(--v-theme-error), 0.32);
}

.rail-chip--anomaly-quiet .rail-chip__warn {
  opacity: var(--opacity-text-secondary);
}

.rail-chip--sugg {
  --tint: 0;
  cursor: pointer;
}

.rail-chip--sugg:hover,
.rail-chip--on {
  background: var(--hover-wash);
  border-color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-chip__n {
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  opacity: var(--opacity-text-secondary);
}

.rail-chip__label,
.rail-chip__x {
  display: inline-flex;
  align-items: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.rail-chip__label {
  text-align: left;
}

.rail-chip__x {
  margin-right: calc(-1 * var(--space-1));
  opacity: var(--opacity-text-secondary);
}

.rail-chip__x:hover:not(:disabled) {
  opacity: 1;
}

.rail-chip__label:disabled,
.rail-chip__x:disabled {
  cursor: default;
  opacity: var(--opacity-disabled);
}

.rail-tags__more {
  margin-top: var(--space-3);
  padding: 0;
  border: 0;
  background: transparent;
  color: rgb(var(--v-theme-accent));
  font: inherit;
  font-size: var(--text-xs);
  cursor: pointer;
}

.rail-ib:focus-visible,
.rail-chip__label:focus-visible,
.rail-chip__x:focus-visible,
.rail-tags__more:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
}
</style>
