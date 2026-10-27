<template>
  <!-- Several selected pictures in the grid's rail (#2161): who they are, the
       tags they share, the tags rejected across them, and what they add up
       to. The tag reads and gestures are the tag dialog's own
       (useSelectionTags), so the two agree on every count. -->
  <div class="rail-pictures">
    <div class="rail-pictures__thumbs">
      <img
        v-for="card in thumbCards"
        :key="card.id"
        :src="card.thumbnail || pictureThumbnailUrl(card.id)"
        alt=""
        draggable="false"
      />
      <span v-if="ids.length > thumbCards.length" class="rail-pictures__more"
        >+{{ (ids.length - thumbCards.length).toLocaleString() }}</span
      >
    </div>

    <section class="rail-pictures__section" aria-label="Tags">
      <SectionHeader
        v-model:collapsed="tagsCollapsed"
        label="Tags"
        :count="tagsOnAll.length + tagsOnSome.length || null"
      >
        <template v-if="!isReadOnly" #actions>
          <button
            v-if="clipboardCount"
            class="rail-pictures__verb rail-pictures__verb--text"
            type="button"
            :aria-label="`Paste ${clipboardCount} on all ${ids.length}`"
            @click="pasteClipboard"
          >
            <Tooltip :text="pasteTooltip" activator="parent" :describe="false" />
            Paste {{ clipboardCount }}
          </button>
          <button
            class="rail-pictures__verb"
            type="button"
            :aria-label="`Add a tag to all ${ids.length}`"
            @click="beginAdd"
          >
            <Tooltip
              :text="`Add a tag to all ${ids.length}`"
              activator="parent"
              :describe="false"
            />
            <v-icon size="16">mdi-plus</v-icon>
          </button>
        </template>
      </SectionHeader>
      <template v-if="!tagsCollapsed">
        <p v-if="tagDataLoading" class="rail-pictures__hint">Reading tags…</p>
        <p
          v-else-if="tagReadFailed"
          class="rail-pictures__hint rail-pictures__error"
        >
          Couldn't read these pictures' tags.
        </p>
        <div v-else class="rail-pictures__zone">
          <span
            v-for="t in tagsOnAll"
            :key="'all-' + t.name"
            class="rail-chip"
          >
            {{ t.name }}
            <button
              v-if="!isReadOnly"
              class="rail-chip__btn"
              type="button"
              :aria-label="`Remove ${t.name} from all ${totalWithTagData}`"
              :disabled="tagActionLoading.includes(t.name)"
              @click="removeTagFromAll(t)"
            >
              <v-icon size="12">mdi-close</v-icon>
            </button>
          </span>
          <span
            v-for="t in tagsOnSome"
            :key="'some-' + t.name"
            class="rail-chip rail-chip--some"
          >
            {{ t.name }}
            <span class="rail-chip__count"
              >{{ t.count }}/{{ totalWithTagData }}</span
            >
            <button
              v-if="!isReadOnly"
              class="rail-chip__btn"
              type="button"
              :aria-label="`Add ${t.name} to the rest`"
              :disabled="tagActionLoading.includes(t.name)"
              @click="addTagToRemaining(t)"
            >
              <v-icon size="12">mdi-plus</v-icon>
            </button>
          </span>
          <input
            v-if="adding"
            ref="addInputRef"
            v-model="newTag"
            class="rail-pictures__input"
            :aria-label="`New tag for all ${ids.length}`"
            placeholder="New tag"
            @keydown.enter.prevent="commitAdd"
            @keydown.esc.stop.prevent="adding = false"
            @blur="adding = false"
          />
        </div>
        <p v-if="tagDataCapped" class="rail-pictures__hint">
          Tags of the first {{ MAX_TAG_FETCH }}.
        </p>
        <p v-if="tagError" class="rail-pictures__hint rail-pictures__error">
          {{ tagError }}
        </p>
        <div v-if="totalWithTagData > 2" class="rail-pictures__coverage">
          <label class="rail-pictures__hint">
            Min coverage
            <input
              v-model.number="tagMinCoverage"
              type="range"
              min="1"
              :max="Math.max(1, totalWithTagData - 1)"
            />
            {{ tagMinCoverage }}/{{ totalWithTagData }}
          </label>
          <span class="rail-pictures__hint">dashed: on some</span>
        </div>
      </template>
    </section>

    <section
      v-if="aggregatedPredictions.length"
      class="rail-pictures__section"
      aria-label="Rejected tags"
    >
      <SectionHeader
        v-model:collapsed="rejectedCollapsed"
        label="Rejected tags"
        :count="aggregatedPredictions.length"
      />
      <div v-if="!rejectedCollapsed" class="rail-pictures__zone">
        <span
          v-for="p in aggregatedPredictions"
          :key="'pred-' + p.tag"
          class="rail-chip rail-chip--rejected"
        >
          <Tooltip
            :text="`Rejected on ${p.count} of ${fetchedPredictionData.length}, ${Math.round(p.avgConf * 100)}% on average`"
            activator="parent"
          />
          {{ p.tag }}
          <span class="rail-chip__count"
            >{{ p.count }}/{{ fetchedPredictionData.length }}</span
          >
          <button
            v-if="!isReadOnly"
            class="rail-chip__btn"
            type="button"
            :aria-label="`Confirm ${p.tag} on all ${p.count}`"
            :disabled="predActionLoading.includes(p.tag)"
            @click="confirmPredictionOnAll(p)"
          >
            <v-icon size="12">mdi-check</v-icon>
          </button>
        </span>
      </div>
    </section>

    <section class="rail-pictures__section" aria-label="Properties">
      <SectionHeader
        v-model:collapsed="propertiesCollapsed"
        label="Properties"
      />
      <dl v-if="!propertiesCollapsed" class="rail-pictures__props">
        <template v-if="ratingText">
          <dt>Rating</dt>
          <dd>{{ ratingText }}</dd>
        </template>
        <template v-if="totalSize">
          <dt>Total size</dt>
          <dd>{{ totalSize }}</dd>
        </template>
        <template v-if="models">
          <dt>Models</dt>
          <dd>{{ models }}</dd>
        </template>
      </dl>
      <p
        v-if="!propertiesCollapsed && ids.length > MAX_TAG_FETCH"
        class="rail-pictures__hint"
      >
        Size and models of the first {{ MAX_TAG_FETCH }}.
      </p>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import { listPicturesByIds, pictureThumbnailUrl } from "../../api/pictures";
import { formatModelSize } from "../../utils/modelShelf.js";
import {
  MAX_TAG_FETCH,
  useSelectionTags,
} from "../../composables/useSelectionTags";
import { useSectionCollapse } from "../../composables/useSectionCollapse";
import {
  splitTagText,
  useTagClipboardStore,
} from "../../stores/useTagClipboardStore";
import SectionHeader from "../widgets/SectionHeader.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** The selected picture ids, in selection order. */
  ids: { type: Array, required: true },
  /** The grid's cards for them, where loaded: thumbnails and ratings. */
  cards: { type: Array, default: () => [] },
});

const emit = defineEmits(["tags-applied"]);

const MAX_THUMBS = 8;
// Selecting by clicking changes the selection once per click; the reads wait
// for it to settle rather than going out for every intermediate one.
const SETTLE_MS = 250;

const tagsCollapsed = useSectionCollapse("manyTags");
const rejectedCollapsed = useSectionCollapse("manyRejectedTags");
const propertiesCollapsed = useSectionCollapse("manyProperties");

const {
  fetchedPredictionData,
  tagDataLoading,
  tagReadFailed,
  tagDataCapped,
  tagActionLoading,
  predActionLoading,
  tagError,
  totalWithTagData,
  tagsOnAll,
  tagMinCoverage,
  tagsOnSome,
  aggregatedPredictions,
  fetchSelectedImageTags,
  fetchSelectedImagePredictions,
  refreshSelectionTags,
  removeTagFromAll,
  addTagToRemaining,
  addTagsToAll,
  confirmPredictionOnAll,
} = useSelectionTags(() => props.ids, {
  onApplied: (payload) => emit("tags-applied", payload),
});

const thumbCards = computed(() => props.cards.slice(0, MAX_THUMBS));

// ── Size and models: not on a grid card, so read for the selection ─────────
const fileRows = ref([]);
let fileRead = 0;

async function fetchFileRows() {
  const ids = props.ids.slice(0, MAX_TAG_FETCH);
  const mine = (fileRead += 1);
  try {
    const rows = await listPicturesByIds(ids, {
      fields: "id,size_bytes,comfyui_models",
    });
    if (mine === fileRead) fileRows.value = Array.isArray(rows) ? rows : [];
  } catch (e) {
    console.warn("Couldn't read the selected pictures' sizes and models", e);
    if (mine === fileRead) fileRows.value = [];
  }
}

let settleTimer = null;
watch(
  () => props.ids.join(","),
  () => {
    clearTimeout(settleTimer);
    tagMinCoverage.value = 1;
    settleTimer = setTimeout(() => {
      fetchSelectedImageTags();
      fetchSelectedImagePredictions();
      fetchFileRows();
    }, SETTLE_MS);
  },
  { immediate: true },
);
onBeforeUnmount(() => clearTimeout(settleTimer));

// "Mixed" only when the loaded cards differ. With some cards not loaded (a
// select-all past what the grid has read) and the rest agreeing, the rating
// is unknown, so the row goes rather than guess.
const ratingText = computed(() => {
  const scores = new Set(props.cards.map((c) => Number(c.score) || 0));
  if (scores.size > 1) return "Mixed";
  if (!scores.size || props.cards.length < props.ids.length) return "";
  const [score] = scores;
  return score ? `${score} ★` : "Unrated";
});

const totalSize = computed(() => {
  const bytes = fileRows.value.reduce(
    (sum, row) => sum + (Number(row.size_bytes) || 0),
    0,
  );
  return bytes > 0 ? formatModelSize(bytes) : "";
});

const models = computed(() => {
  const names = new Set();
  for (const row of fileRows.value) {
    try {
      for (const name of JSON.parse(row.comfyui_models || "[]")) {
        if (name) names.add(name);
      }
    } catch (e) {
      console.warn("Unreadable model list on picture", row.id, e);
    }
  }
  return [...names].join(", ");
});

// ── Adding and pasting on all ───────────────────────────────────────────────
const adding = ref(false);
const newTag = ref("");
const addInputRef = ref(null);

// The input renders only in the open, loaded section: "+" on a collapsed one
// opens it, and the input takes focus whenever it mounts, so a "+" pressed
// while tags are still being read lands there too.
function beginAdd() {
  adding.value = true;
  newTag.value = "";
  tagsCollapsed.value = false;
}
watch(addInputRef, (input) => input?.focus());

async function commitAdd() {
  const tags = splitTagText(newTag.value);
  if (tags.length && (await addTagsToAll(tags))) newTag.value = "";
}

const tagClipboard = useTagClipboardStore();
const clipboardCount = computed(() => tagClipboard.clipboardTags.length);
const pasteTooltip = computed(
  () =>
    `Paste ${tagClipboard.clipboardTags.join(", ")} on all ${props.ids.length} (Ctrl+V)`,
);

async function pasteClipboard() {
  const result = await tagClipboard.pasteClipboard(
    props.ids.map(Number).filter((id) => Number.isFinite(id) && id > 0),
  );
  if (result?.pictureIds.length) {
    emit("tags-applied", {
      tag: tagClipboard.clipboardTags.join(", "),
      pictureIds: result.pictureIds,
    });
    await refreshSelectionTags();
  }
}
</script>

<style scoped>
.rail-pictures {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  font-size: var(--text-xs);
}

.rail-pictures__thumbs {
  display: flex;
  align-items: center;
  padding-left: var(--space-1);
}

.rail-pictures__thumbs img {
  width: 28px;
  height: 28px;
  margin-left: calc(-1 * var(--space-1));
  border-radius: var(--radius-sm);
  object-fit: cover;
  box-shadow: 0 0 0 2px rgb(var(--v-theme-sidebar));
}

.rail-pictures__more {
  margin-left: var(--space-3);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
}

.rail-pictures__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.rail-pictures__zone {
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  gap: var(--space-2);
  min-height: var(--control-h-sm);
  padding: var(--space-2);
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.2);
  border-radius: var(--radius-md);
}

/* The lightbox's chip, in this surface's ink: solid is on every picture,
   dashed is on some, with how many. */
.rail-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  padding: 1px var(--space-1) 1px 6px; /* the lightbox chip's optical nudges */
  border: 1px solid transparent;
  border-radius: 6px; /* the lightbox chip's radius (OverlayTagsPanel) */
  background: rgba(var(--v-theme-on-surface), 0.1);
  color: rgb(var(--v-theme-on-surface));
  line-height: 1.2;
  overflow-wrap: anywhere;
}

.rail-chip--some,
.rail-chip--rejected {
  background: transparent;
  border-style: dashed;
  border-color: rgba(var(--v-theme-on-surface), 0.35);
}

.rail-chip--rejected {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-chip__count {
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  opacity: var(--opacity-text-secondary);
}

.rail-chip__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  opacity: var(--opacity-text-secondary);
  cursor: pointer;
}

.rail-chip__btn:hover:not(:disabled) {
  opacity: 1;
}

.rail-chip__btn:disabled {
  cursor: default;
  opacity: var(--opacity-disabled);
}

.rail-pictures__verb {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: var(--control-h-sm);
  min-width: var(--control-h-sm);
  justify-content: center;
  padding: 0 var(--space-1);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font: inherit;
  cursor: pointer;
}

.rail-pictures__verb--text {
  font-size: var(--text-xs);
}

.rail-pictures__verb:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.rail-pictures__input {
  flex: 1 1 96px;
  min-width: 0;
  font: inherit;
  color: inherit;
  background: transparent;
  border: 0;
  outline: none;
}

.rail-pictures__hint {
  margin: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-pictures__error {
  color: rgb(var(--v-theme-surface-error));
}

.rail-pictures__coverage {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.rail-pictures__coverage label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
}

.rail-pictures__coverage input {
  width: 80px;
  accent-color: rgb(var(--v-theme-primary));
}

.rail-pictures__coverage > span {
  margin-left: auto;
}

.rail-pictures__props {
  display: grid;
  grid-template-columns: minmax(0, 96px) minmax(0, 1fr);
  gap: var(--space-3);
  margin: 0;
}

.rail-pictures__props dt {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-pictures__props dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums;
}
</style>
