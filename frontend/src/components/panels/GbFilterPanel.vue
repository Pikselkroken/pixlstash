<template>
  <div class="tbm gb-filter-panel">
    <span class="tbm-caret tbm-caret--end"></span>
    <div class="tbm-header">
      <v-icon size="18" class="tbm-header-icon">mdi-filter</v-icon>
      <span class="tbm-title">Filters</span>
      <span class="tbm-spacer"></span>
      <span class="tbm-count">{{ gbMatchCountLabel }}</span>
      <button
        class="tbm-ghost"
        type="button"
        :disabled="!filterStore.isActive"
        @click="filterStore.resetFilters()"
      >
        <v-icon size="15">mdi-close-circle-outline</v-icon>
        Clear
      </button>
    </div>

    <!-- Scrollable body so a tall filter set (incl. the ComfyUI models at the
         bottom) is never clipped off the bottom of the screen. -->
    <div class="gb-filter-body">
      <!-- Shared / Unassigned scope -->
      <div v-if="!isReadOnly || isAllPicturesView" class="tbm-section">
        <div class="tbm-check-grid">
          <label v-if="!isReadOnly" class="tbm-check">
            <input
              type="checkbox"
              :checked="gbSharedOnlyFilter"
              @change="gbSharedOnlyFilter = $event.target.checked"
            />
            Shared pictures only
          </label>
          <label v-if="isAllPicturesView" class="tbm-check">
            <input
              type="checkbox"
              :checked="gbUnassignedOnlyFilter"
              @change="gbUnassignedOnlyFilter = $event.target.checked"
            />
            Unassigned only
          </label>
        </div>
      </div>

      <!-- Media + Face — icon-only on one row; labels move to tooltips/aria. -->
      <div class="tbm-section">
        <div class="gb-media-face-row">
          <div
            class="tbm-seg gb-icon-group"
            role="group"
            aria-label="Media type filter"
          >
            <button
              v-for="opt in gbMediaTypeOptions"
              :key="opt.value"
              class="tbm-seg-btn gb-icon-btn"
              :class="{ 'tbm-seg-btn--on': gbMediaTypeFilter === opt.value }"
              type="button"
              :title="opt.title"
              :aria-label="opt.label"
              :aria-pressed="gbMediaTypeFilter === opt.value"
              @click="gbSetMediaTypeFilter(opt.value)"
            >
              <v-icon size="16">{{ opt.icon }}</v-icon>
            </button>
          </div>
          <div
            class="tbm-btngroup gb-icon-group"
            role="group"
            aria-label="Face filter"
          >
            <button
              v-for="opt in gbFaceBboxFilterOptions"
              :key="String(opt.value)"
              class="tbm-btn gb-icon-btn"
              :class="{ 'tbm-btn--on': gbFaceBboxFilter === opt.value }"
              type="button"
              :title="opt.title"
              :aria-label="opt.label"
              :aria-pressed="gbFaceBboxFilter === opt.value"
              @click="gbSetFaceBboxFilter(opt.value)"
            >
              <v-icon size="16">{{ opt.icon }}</v-icon>
            </button>
          </div>
        </div>
      </div>

      <!-- Score range -->
      <div class="tbm-section">
        <div class="tbm-grid-2">
          <div>
            <span class="tbm-label">Min score</span>
            <div class="gb-score-stars">
              <button
                v-for="n in 5"
                :key="'min-' + n"
                class="gb-score-star-btn"
                type="button"
                :title="`Set minimum score ${n}`"
                @click="gbSetMinScore(n)"
              >
                <v-icon
                  size="16"
                  :color="
                    gbMinScoreFilter != null && n <= gbMinScoreFilter
                      ? 'warning'
                      : undefined
                  "
                  >{{
                    gbMinScoreFilter != null && n <= gbMinScoreFilter
                      ? "mdi-star"
                      : "mdi-star-outline"
                  }}</v-icon
                >
              </button>
            </div>
          </div>
          <div>
            <span class="tbm-label tbm-label--right">Max score</span>
            <div class="gb-score-stars gb-score-stars--right">
              <button
                v-for="n in 5"
                :key="'max-' + n"
                class="gb-score-star-btn"
                type="button"
                :title="`Set maximum score ${n}`"
                @click="gbSetMaxScore(n)"
              >
                <v-icon
                  size="16"
                  :color="
                    gbMaxScoreFilter != null && n <= gbMaxScoreFilter
                      ? 'warning'
                      : undefined
                  "
                  >{{
                    gbMaxScoreFilter != null && n <= gbMaxScoreFilter
                      ? "mdi-star"
                      : "mdi-star-outline"
                  }}</v-icon
                >
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Impossible tags (collapsible) -->
      <div class="tbm-section">
        <div
          class="gb-section-head gb-collapse-head"
          @click="gbImpossibleExpanded = !gbImpossibleExpanded"
        >
          <span class="tbm-label gb-label-inline">
            Impossible tags
            <span v-if="gbImpossibleSources.length" class="gb-collapse-count">{{
              gbImpossibleSources.length
            }}</span>
          </span>
          <v-icon size="16" class="gb-collapse-chevron">{{
            gbImpossibleExpanded ? "mdi-chevron-up" : "mdi-chevron-down"
          }}</v-icon>
        </div>
        <div
          v-if="gbImpossibleExpanded"
          class="tbm-check-grid"
          role="group"
          aria-label="Impossible tags filter"
        >
          <label
            v-for="opt in gbImpossibleSourceOptions"
            :key="opt.value"
            class="tbm-check"
            :title="opt.tip"
          >
            <input
              type="checkbox"
              :checked="gbImpossibleSources.includes(opt.value)"
              @change="
                gbToggleImpossibleSource(opt.value, $event.target.checked)
              "
            />
            {{ opt.label }}
          </label>
        </div>
      </div>

      <!-- Tags -->
      <div class="tbm-section">
        <div class="gb-section-head">
          <span class="tbm-label gb-label-inline">Tags</span>
          <button
            class="tbm-ghost"
            type="button"
            :disabled="!gbTagFilter.length && !gbTagRejectedFilter.length"
            @click="
              gbTagFilter = [];
              gbTagRejectedFilter = [];
            "
          >
            Clear
          </button>
        </div>
        <div class="tbm-input-wrap gb-tags-input">
          <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
          <input
            v-model="gbTagFilterInput"
            class="tbm-input tbm-input--with-icon"
            placeholder="Filter by tag…"
            autocomplete="off"
            @keydown.enter.prevent="
              gbTagFilterIndex >= 0 && gbTagFilterSuggestions.length
                ? gbAddTagFilter(gbTagFilterSuggestions[gbTagFilterIndex])
                : gbAddTagFilter(gbTagFilterInput.trim())
            "
            @keydown.tab.prevent="
              gbTagFilterSuggestions.length
                ? gbAddTagFilter(
                    gbTagFilterSuggestions[
                      gbTagFilterIndex >= 0 ? gbTagFilterIndex : 0
                    ],
                  )
                : gbAddTagFilter(gbTagFilterInput.trim())
            "
            @keydown.down.prevent="
              gbTagFilterIndex = Math.min(
                gbTagFilterIndex + 1,
                gbTagFilterSuggestions.length - 1,
              )
            "
            @keydown.up.prevent="
              gbTagFilterIndex = Math.max(gbTagFilterIndex - 1, -1)
            "
            @keydown.escape.prevent="gbTagFilterSuggestions = []"
          />
          <div
            v-if="gbTagFilterSuggestions.length"
            class="gb-tag-filter-dropdown"
            :class="{
              'gb-tag-filter-dropdown--hover-enabled': gbTagFilterHoverEnabled,
            }"
            @mousemove.once="gbTagFilterHoverEnabled = true"
          >
            <button
              v-for="(tag, idx) in gbTagFilterSuggestions"
              :key="tag"
              class="gb-tag-filter-suggestion"
              :class="{
                'gb-tag-filter-suggestion--active': idx === gbTagFilterIndex,
              }"
              type="button"
              @mousedown.prevent="gbAddTagFilter(tag)"
              @mousemove="gbTagFilterIndex = idx"
            >
              {{ tag }}
            </button>
          </div>
        </div>
        <div
          v-if="gbTagFilter.length || gbTagRejectedFilter.length"
          class="gb-tag-chips"
        >
          <button
            v-for="tag in gbTagFilter"
            :key="`confirmed-${tag}`"
            class="tag-chip tag-chip--filter"
            type="button"
            :title="`'${tag}' – click to switch to rejected match`"
            @click.stop="gbToggleTagRejected(tag)"
          >
            <span class="tag-chip-label">{{ tag }}</span>
            <v-icon
              size="11"
              class="tag-chip-close"
              @click.stop="gbRemoveTagFilter(tag)"
              >mdi-close</v-icon
            >
          </button>
          <button
            v-for="tag in gbTagRejectedFilter"
            :key="`rejected-${tag}`"
            class="tag-chip tag-chip--filter tag-chip--filter-rejected"
            type="button"
            :title="`'${tag}' (rejected) – click to switch to confirmed match`"
            @click.stop="gbToggleTagRejected(tag)"
          >
            <span class="tag-chip-label">{{ tag }}</span>
            <v-icon
              size="11"
              class="tag-chip-close"
              @click.stop="gbRemoveTagFilter(tag)"
              >mdi-close</v-icon
            >
          </button>
        </div>
      </div>

      <!-- Tag confidence (collapsible) -->
      <div class="tbm-section">
        <div
          class="gb-section-head gb-collapse-head"
          @click="gbConfidenceExpanded = !gbConfidenceExpanded"
        >
          <span class="tbm-label gb-label-inline">
            Tag confidence
            <span v-if="gbConfidenceActiveCount" class="gb-collapse-count">{{
              gbConfidenceActiveCount
            }}</span>
          </span>
          <v-icon size="16" class="gb-collapse-chevron">{{
            gbConfidenceExpanded ? "mdi-chevron-up" : "mdi-chevron-down"
          }}</v-icon>
        </div>
        <template v-if="gbConfidenceExpanded">
          <div class="gb-confidence-row">
            <div class="tbm-input-wrap gb-confidence-tag-wrap">
              <input
                v-model="gbConfidenceTagInput"
                class="tbm-input"
                placeholder="Tag…"
                autocomplete="off"
                @keydown.enter.prevent="
                  gbConfidenceTagIndex >= 0 && gbConfidenceTagSuggestions.length
                    ? gbSelectConfidenceTagSuggestion(
                        gbConfidenceTagSuggestions[gbConfidenceTagIndex],
                      )
                    : gbAddConfidenceFilter(gbConfidenceTagInput.trim())
                "
                @keydown.tab.prevent="
                  gbConfidenceTagSuggestions.length
                    ? gbSelectConfidenceTagSuggestion(
                        gbConfidenceTagSuggestions[
                          gbConfidenceTagIndex >= 0 ? gbConfidenceTagIndex : 0
                        ],
                      )
                    : undefined
                "
                @keydown.down.prevent="
                  gbConfidenceTagIndex = Math.min(
                    gbConfidenceTagIndex + 1,
                    gbConfidenceTagSuggestions.length - 1,
                  )
                "
                @keydown.up.prevent="
                  gbConfidenceTagIndex = Math.max(gbConfidenceTagIndex - 1, -1)
                "
                @keydown.escape.prevent="gbConfidenceTagSuggestions = []"
              />
              <div
                v-if="gbConfidenceTagSuggestions.length"
                class="gb-tag-filter-dropdown"
                :class="{
                  'gb-tag-filter-dropdown--hover-enabled':
                    gbConfidenceTagHoverEnabled,
                }"
                @mousemove.once="gbConfidenceTagHoverEnabled = true"
              >
                <button
                  v-for="(tag, idx) in gbConfidenceTagSuggestions"
                  :key="tag"
                  class="gb-tag-filter-suggestion"
                  :class="{
                    'gb-tag-filter-suggestion--active':
                      idx === gbConfidenceTagIndex,
                  }"
                  type="button"
                  @mousedown.prevent="gbSelectConfidenceTagSuggestion(tag)"
                  @mousemove="gbConfidenceTagIndex = idx"
                >
                  {{ tag }}
                </button>
              </div>
            </div>
            <div
              class="tbm-seg"
              role="group"
              aria-label="Confidence comparison"
            >
              <button
                class="tbm-seg-btn"
                :class="{ 'tbm-seg-btn--on': gbConfidenceMode === 'above' }"
                type="button"
                title="High confidence, not labelled"
                @click="gbConfidenceMode = 'above'"
              >
                ≥
              </button>
              <button
                class="tbm-seg-btn"
                :class="{ 'tbm-seg-btn--on': gbConfidenceMode === 'below' }"
                type="button"
                title="Low confidence, labelled"
                @click="gbConfidenceMode = 'below'"
              >
                &lt;
              </button>
            </div>
            <input
              v-model.number="gbConfidenceThreshold"
              type="number"
              min="0"
              max="1"
              step="0.05"
              class="tbm-num"
            />
            <button
              class="tbm-action tbm-action--primary"
              type="button"
              :disabled="!gbConfidenceTagInput.trim()"
              @click="gbAddConfidenceFilter()"
            >
              <v-icon size="15">mdi-plus</v-icon>
              Add
            </button>
          </div>
          <div
            v-if="
              gbTagConfidenceAboveFilter.length ||
              gbTagConfidenceBelowFilter.length
            "
            class="gb-tag-chips gb-confidence-chips"
          >
            <button
              v-for="entry in gbTagConfidenceAboveFilter"
              :key="`ca-${entry}`"
              class="tag-chip tag-chip--filter tag-chip--confidence-above"
              type="button"
              :title="`Prediction ≥${Math.round(parseFloat(entry.split(':')[1]) * 100)}%, not labelled`"
            >
              <span class="tag-chip-label"
                >≥{{ gbConfidenceEntryLabel(entry) }}</span
              >
              <v-icon
                size="11"
                class="tag-chip-close"
                @click.stop="gbRemoveConfidenceAboveFilter(entry)"
                >mdi-close</v-icon
              >
            </button>
            <button
              v-for="entry in gbTagConfidenceBelowFilter"
              :key="`cb-${entry}`"
              class="tag-chip tag-chip--filter tag-chip--confidence-below"
              type="button"
              :title="`Prediction <${Math.round(parseFloat(entry.split(':')[1]) * 100)}%, labelled`"
            >
              <span class="tag-chip-label"
                >&lt;{{ gbConfidenceEntryLabel(entry) }}</span
              >
              <v-icon
                size="11"
                class="tag-chip-close"
                @click.stop="gbRemoveConfidenceBelowFilter(entry)"
                >mdi-close</v-icon
              >
            </button>
          </div>
        </template>
      </div>

      <!-- ComfyUI -->
      <div
        v-if="gbComfyuiModelOptions.length || gbComfyuiLoraOptions.length"
        class="tbm-section"
      >
        <div
          class="gb-section-head gb-comfyui-section-header"
          @click="gbComfyuiFilterExpanded = !gbComfyuiFilterExpanded"
        >
          <span class="tbm-label gb-label-inline">ComfyUI</span>
          <v-icon size="16" class="gb-comfyui-chevron">{{
            gbComfyuiFilterExpanded ? "mdi-chevron-up" : "mdi-chevron-down"
          }}</v-icon>
        </div>
        <template
          v-if="gbComfyuiModelOptions.length && gbComfyuiFilterExpanded"
        >
          <div class="gb-comfy-list-head">
            <span class="tbm-label gb-label-inline">Models</span>
            <button
              v-if="gbComfyuiModelFilter.length"
              class="tbm-ghost"
              type="button"
              @click="gbComfyuiModelFilter = []"
            >
              Clear
            </button>
          </div>
          <div class="gb-comfy-list">
            <label
              v-for="m in gbComfyuiModelOptions"
              :key="m"
              class="tbm-check gb-comfy-check"
              :title="m"
            >
              <input
                type="checkbox"
                :checked="gbComfyuiModelFilter.includes(m)"
                @change="gbToggleComfyui('model', m, $event.target.checked)"
              />
              <span class="gb-comfy-check-label">{{
                m.replace(/\.[^/.]+$/, "")
              }}</span>
            </label>
          </div>
        </template>
        <template v-if="gbComfyuiLoraOptions.length && gbComfyuiFilterExpanded">
          <div class="gb-comfy-list-head">
            <span class="tbm-label gb-label-inline">LoRAs</span>
            <button
              v-if="gbComfyuiLoraFilter.length"
              class="tbm-ghost"
              type="button"
              @click="gbComfyuiLoraFilter = []"
            >
              Clear
            </button>
          </div>
          <div class="gb-comfy-list">
            <label
              v-for="l in gbComfyuiLoraOptions"
              :key="l"
              class="tbm-check gb-comfy-check"
              :title="l"
            >
              <input
                type="checkbox"
                :checked="gbComfyuiLoraFilter.includes(l)"
                @change="gbToggleComfyui('lora', l, $event.target.checked)"
              />
              <span class="gb-comfy-check-label">{{
                l.replace(/\.[^/.]+$/, "")
              }}</span>
            </label>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";
import { useGridStore } from "../../stores/useGridStore";

const props = defineProps({
  backendUrl: { type: String, default: "" },
  selectedCharacter: { type: String, default: null },
  allPicturesId: { type: String, default: null },
  open: { type: Boolean, default: false },
});

const filterStore = useFilterStore();
const gridStore = useGridStore();

// Live "N matches" count for the header — published by ImageGrid into the grid
// store (the full fetched set length, not the virtualised window).
const gbMatchCountLabel = computed(() => {
  const n = Number(gridStore.matchCount || 0);
  return `${n.toLocaleString()} ${n === 1 ? "match" : "matches"}`;
});

const isAllPicturesView = computed(
  () =>
    String(props.selectedCharacter ?? "") === String(props.allPicturesId ?? ""),
);

const gbMediaTypeFilter = computed({
  get: () => filterStore.mediaTypeFilter,
  set: (v) => {
    filterStore.mediaTypeFilter = v;
  },
});
const gbMinScoreFilter = computed({
  get: () => filterStore.minScoreFilter,
  set: (v) => {
    filterStore.minScoreFilter = v ?? null;
  },
});
const gbMaxScoreFilter = computed({
  get: () => filterStore.maxScoreFilter,
  set: (v) => {
    filterStore.maxScoreFilter = v ?? null;
  },
});
const gbFaceBboxFilter = computed({
  get: () => filterStore.faceBboxFilter,
  set: (v) => {
    filterStore.faceBboxFilter = v;
  },
});
const gbTagFilter = computed({
  get: () => filterStore.tagFilter,
  set: (v) => {
    filterStore.tagFilter = v ?? [];
  },
});
const gbTagRejectedFilter = computed({
  get: () => filterStore.tagRejectedFilter,
  set: (v) => {
    filterStore.tagRejectedFilter = v ?? [];
  },
});
const gbTagConfidenceAboveFilter = computed({
  get: () => filterStore.tagConfidenceAboveFilter,
  set: (v) => {
    filterStore.tagConfidenceAboveFilter = v ?? [];
  },
});
const gbTagConfidenceBelowFilter = computed({
  get: () => filterStore.tagConfidenceBelowFilter,
  set: (v) => {
    filterStore.tagConfidenceBelowFilter = v ?? [];
  },
});
const gbSharedOnlyFilter = computed({
  get: () => filterStore.sharedOnlyFilter,
  set: (v) => {
    filterStore.sharedOnlyFilter = Boolean(v);
  },
});
const gbUnassignedOnlyFilter = computed({
  get: () => filterStore.unassignedOnlyFilter,
  set: (v) => {
    filterStore.unassignedOnlyFilter = Boolean(v);
  },
});
const gbComfyuiModelFilter = computed({
  get: () => filterStore.comfyuiModelFilter,
  set: (v) => {
    filterStore.comfyuiModelFilter = v ?? [];
  },
});
const gbComfyuiLoraFilter = computed({
  get: () => filterStore.comfyuiLoraFilter,
  set: (v) => {
    filterStore.comfyuiLoraFilter = v ?? [];
  },
});

// Impossible-tag sources. A value is "on" when present in the array; toggling a
// checkbox adds/removes its key, OR-ing the selected sources in the grid query.
const gbImpossibleSources = computed({
  get: () => filterStore.impossibleSources,
  set: (v) => {
    filterStore.impossibleSources = Array.isArray(v) ? v : [];
  },
});

const gbImpossibleSourceOptions = [
  {
    value: "no_face",
    label: "Face tags, no face",
    tip: "No detected face, but tagged face/nose/lips/eyes…",
  },
  {
    value: "no_humans",
    label: "People tags, no humans",
    tip: "Tagged 'no humans'/'scenery' but has people tags, no face",
  },
];

function gbToggleComfyui(kind, value, checked) {
  const target = kind === "model" ? gbComfyuiModelFilter : gbComfyuiLoraFilter;
  const current = Array.isArray(target.value) ? target.value : [];
  if (checked) {
    if (!current.includes(value)) target.value = [...current, value];
  } else {
    target.value = current.filter((v) => v !== value);
  }
}

function gbToggleImpossibleSource(value, checked) {
  const current = Array.isArray(gbImpossibleSources.value)
    ? gbImpossibleSources.value
    : [];
  if (checked) {
    if (!current.includes(value)) {
      gbImpossibleSources.value = [...current, value];
    }
  } else {
    gbImpossibleSources.value = current.filter((v) => v !== value);
  }
}

const gbMediaTypeOptions = [
  {
    value: "all",
    icon: "mdi-multimedia",
    label: "All",
    title: "Show all media",
  },
  {
    value: "images",
    icon: "mdi-image-outline",
    label: "Images",
    title: "Show images only",
  },
  {
    value: "videos",
    icon: "mdi-video-outline",
    label: "Video",
    title: "Show videos only",
  },
];

const gbFaceBboxFilterOptions = [
  {
    value: null,
    icon: "mdi-all-inclusive",
    label: "Any",
    title: "All pictures",
  },
  {
    value: "with_face",
    icon: "mdi-face-recognition",
    label: "Has face",
    title: "With detected face",
  },
  {
    value: "without_face",
    icon: "mdi-account-off-outline",
    label: "No face",
    title: "Without detected face",
  },
];

function gbSetMediaTypeFilter(value) {
  gbMediaTypeFilter.value = value;
}

function gbSetFaceBboxFilter(value) {
  gbFaceBboxFilter.value =
    gbFaceBboxFilter.value === value && value !== null ? null : value;
}

function gbSetMinScore(n) {
  const newMin = gbMinScoreFilter.value === n ? null : n;
  gbMinScoreFilter.value = newMin;
  if (
    newMin !== null &&
    gbMaxScoreFilter.value !== null &&
    newMin > gbMaxScoreFilter.value
  ) {
    gbMaxScoreFilter.value = newMin;
  }
}

function gbSetMaxScore(n) {
  const newMax = gbMaxScoreFilter.value === n ? null : n;
  gbMaxScoreFilter.value = newMax;
  if (
    newMax !== null &&
    gbMinScoreFilter.value !== null &&
    newMax < gbMinScoreFilter.value
  ) {
    gbMinScoreFilter.value = newMax;
  }
}

const gbTagFilterInput = ref("");
const gbTagFilterSuggestions = ref([]);
const gbTagFilterHoverEnabled = ref(false);
const gbTagFilterIndex = ref(-1);

async function gbLoadTagFilterSuggestions(input) {
  if (!input || input.length < 1) {
    gbTagFilterSuggestions.value = [];
    return;
  }
  try {
    const res = await apiClient.get(`${props.backendUrl ?? ""}/tags`);
    const all = Array.isArray(res.data) ? res.data : [];
    const q = input.toLowerCase();
    gbTagFilterSuggestions.value = all
      .filter(
        (t) =>
          t.tag.toLowerCase().includes(q) &&
          !gbTagFilter.value.includes(t.tag) &&
          !gbTagRejectedFilter.value.includes(t.tag),
      )
      .slice(0, 8)
      .map((t) => t.tag);
  } catch {
    gbTagFilterSuggestions.value = [];
  }
}

watch(gbTagFilterInput, (val) => {
  gbTagFilterIndex.value = -1;
  gbLoadTagFilterSuggestions(val);
});

function gbAddTagFilter(tag) {
  if (
    tag &&
    !gbTagFilter.value.includes(tag) &&
    !gbTagRejectedFilter.value.includes(tag)
  ) {
    gbTagFilter.value = [...gbTagFilter.value, tag];
  }
  gbTagFilterInput.value = "";
  gbTagFilterSuggestions.value = [];
  gbTagFilterIndex.value = -1;
}

function gbRemoveTagFilter(tag) {
  gbTagFilter.value = gbTagFilter.value.filter((t) => t !== tag);
  gbTagRejectedFilter.value = gbTagRejectedFilter.value.filter(
    (t) => t !== tag,
  );
}

function gbToggleTagRejected(tag) {
  if (gbTagFilter.value.includes(tag)) {
    gbTagFilter.value = gbTagFilter.value.filter((t) => t !== tag);
    gbTagRejectedFilter.value = [...gbTagRejectedFilter.value, tag];
  } else {
    gbTagRejectedFilter.value = gbTagRejectedFilter.value.filter(
      (t) => t !== tag,
    );
    gbTagFilter.value = [...gbTagFilter.value, tag];
  }
}

const gbConfidenceTagInput = ref("");
const gbConfidenceTagSuggestions = ref([]);
const gbConfidenceTagHoverEnabled = ref(false);
const gbConfidenceTagIndex = ref(-1);
const gbConfidenceThreshold = ref(0.7);
const gbConfidenceMode = ref("above");
let gbSuppressConfidenceSuggestionLoad = false;

async function gbLoadConfidenceTagSuggestions(input) {
  if (!input || input.length < 1) {
    gbConfidenceTagSuggestions.value = [];
    return;
  }
  try {
    const res = await apiClient.get(`${props.backendUrl ?? ""}/tags`);
    const all = Array.isArray(res.data) ? res.data : [];
    const q = input.toLowerCase();
    gbConfidenceTagSuggestions.value = all
      .filter((t) => t.tag.toLowerCase().includes(q))
      .slice(0, 8)
      .map((t) => t.tag);
  } catch {
    gbConfidenceTagSuggestions.value = [];
  }
}

watch(gbConfidenceTagInput, (val) => {
  if (gbSuppressConfidenceSuggestionLoad) {
    gbSuppressConfidenceSuggestionLoad = false;
    return;
  }
  gbConfidenceTagIndex.value = -1;
  gbLoadConfidenceTagSuggestions(val);
});

function gbSelectConfidenceTagSuggestion(tag) {
  gbSuppressConfidenceSuggestionLoad = true;
  gbConfidenceTagInput.value = tag;
  gbConfidenceTagSuggestions.value = [];
  gbConfidenceTagIndex.value = -1;
}

function gbAddConfidenceFilter(tagArg) {
  const tag = (tagArg ?? gbConfidenceTagInput.value).trim();
  if (!tag) return;
  const entry = `${tag}:${gbConfidenceThreshold.value.toFixed(2)}`;
  if (gbConfidenceMode.value === "above") {
    if (!gbTagConfidenceAboveFilter.value.includes(entry))
      gbTagConfidenceAboveFilter.value = [
        ...gbTagConfidenceAboveFilter.value,
        entry,
      ];
  } else {
    if (!gbTagConfidenceBelowFilter.value.includes(entry))
      gbTagConfidenceBelowFilter.value = [
        ...gbTagConfidenceBelowFilter.value,
        entry,
      ];
  }
  gbConfidenceTagInput.value = "";
  gbConfidenceTagSuggestions.value = [];
  gbConfidenceTagIndex.value = -1;
}

function gbRemoveConfidenceAboveFilter(entry) {
  gbTagConfidenceAboveFilter.value = gbTagConfidenceAboveFilter.value.filter(
    (e) => e !== entry,
  );
}

function gbRemoveConfidenceBelowFilter(entry) {
  gbTagConfidenceBelowFilter.value = gbTagConfidenceBelowFilter.value.filter(
    (e) => e !== entry,
  );
}

function gbConfidenceEntryLabel(entry) {
  const [tag, threshold] = entry.split(":");
  return `${tag} ${Math.round(parseFloat(threshold) * 100)}%`;
}

const gbComfyuiModelOptions = ref([]);
const gbComfyuiLoraOptions = ref([]);
const gbComfyuiFilterExpanded = ref(false);

// Collapsible sections (default collapsed to keep the panel short). The active
// count on the header keeps an in-use filter discoverable while collapsed.
const gbImpossibleExpanded = ref(false);
const gbConfidenceExpanded = ref(false);
const gbConfidenceActiveCount = computed(
  () =>
    gbTagConfidenceAboveFilter.value.length +
    gbTagConfidenceBelowFilter.value.length,
);

watch(
  () => props.open,
  async (isOpen) => {
    if (isOpen) {
      const backendUrl = props.backendUrl ?? "";
      if (
        backendUrl &&
        !gbComfyuiModelOptions.value.length &&
        !gbComfyuiLoraOptions.value.length
      ) {
        try {
          const [mRes, lRes] = await Promise.all([
            apiClient.get(`${backendUrl}/pictures/comfyui_models`),
            apiClient.get(`${backendUrl}/pictures/comfyui_loras`),
          ]);
          gbComfyuiModelOptions.value = Array.isArray(mRes.data)
            ? mRes.data
            : [];
          gbComfyuiLoraOptions.value = Array.isArray(lRes.data)
            ? lRes.data
            : [];
        } catch (err) {
          // Non-fatal: the ComfyUI model/LoRA filters just stay empty if the
          // metadata endpoints are unavailable. Log so it is not silent.
          console.warn("Failed to load ComfyUI filter options", err);
        }
      }
    } else {
      gbTagFilterInput.value = "";
      gbTagFilterSuggestions.value = [];
    }
  },
  // immediate: the menu mounts this panel lazily *already open*, so a plain
  // (change-only) watch would miss that first true and never load the ComfyUI
  // model/LoRA options — which is why the models filter wasn't showing.
  { immediate: true },
);
</script>

<style scoped>
.gb-filter-panel {
  width: 376px;
  max-width: 94vw;
  /* Cap the panel and scroll its body so the lower sections (ComfyUI models)
     stay reachable instead of being clipped off the bottom of the viewport. */
  display: flex;
  flex-direction: column;
  max-height: min(80vh, 760px);
}
.gb-filter-body {
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* Inline label that sits on a header row with a trailing action. */
.gb-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-3);
}
.gb-label-inline {
  margin-bottom: 0;
}

/* ── Media + Face: two icon-only groups sharing one row ───────────────────── */
.gb-media-face-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.gb-media-face-row .gb-icon-group {
  flex: 1;
}
.gb-icon-btn {
  /* Icon-only — drop the label's horizontal padding so the glyph centres. */
  padding: 0;
  min-height: 32px;
}

/* ── Collapsible section header (Tag confidence, Impossible tags) ──────────── */
.gb-collapse-head {
  cursor: pointer;
}
.gb-collapse-head .gb-label-inline {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.gb-collapse-chevron {
  opacity: 0.6;
}
.gb-collapse-count {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-primary));
  background: rgb(var(--v-theme-primary));
  border-radius: var(--radius-pill);
  padding: 0 var(--space-2);
  min-width: 16px;
  text-align: center;
  line-height: 16px;
}

/* ── Score stars ──────────────────────────────────────────────────────────── */
.gb-score-stars {
  display: flex;
}
.gb-score-stars--right {
  justify-content: flex-end;
}
.gb-score-star-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1) var(--space-1);
  background: none;
  border: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-panel), 0.7);
  line-height: 1;
}
.gb-score-star-btn:hover {
  color: rgb(var(--v-theme-on-panel));
}

/* ── Tag input + suggestions ──────────────────────────────────────────────── */
.gb-tags-input {
  margin-top: 0;
}

.gb-tag-filter-dropdown {
  position: absolute;
  top: calc(100% + var(--space-1));
  left: 0;
  right: 0;
  z-index: 200;
  background: rgb(var(--v-theme-panel));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-3);
  overflow: hidden;
  /* Must stay clickable: with pointer-events:none the suggestions can't receive
     the mousedown and the click falls through to the control behind them. */
  pointer-events: auto;
}

.gb-tag-filter-dropdown--hover-enabled {
  pointer-events: auto;
}

.gb-tag-filter-suggestion {
  display: block;
  width: 100%;
  padding: var(--space-2) var(--space-4);
  text-align: left;
  cursor: pointer;
  font-size: var(--text-sm);
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-panel));
}

.gb-tag-filter-suggestion--active,
.gb-tag-filter-suggestion:hover {
  background: var(--hover-wash);
}

/* ── Tag chips ────────────────────────────────────────────────────────────── */
.gb-tag-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
.gb-confidence-chips {
  margin-top: var(--space-4);
}

/* Filter tag pills. The base `.tag-chip` and these `--filter`/`--confidence`
   variants are declared locally because Vue scoped styles do not cross
   component boundaries: the chips here can't inherit a `.tag-chip` rule that
   lives in another component's scope. Green = confirmed match, red = rejected,
   success/warning = confidence bounds. Hover previews the opposite state. */
.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  border-radius: var(--radius-pill);
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  cursor: pointer;
  transition:
    background var(--dur-1) var(--ease-standard),
    opacity var(--dur-1) var(--ease-standard);
  line-height: 1.5;
  white-space: nowrap;
  border: 1px solid transparent;
}

.tag-chip:disabled {
  opacity: 0.45;
  cursor: default;
}

.tag-chip-close {
  opacity: 0.6;
}

.tag-chip--filter {
  background: rgba(var(--v-theme-primary), 0.18);
  border-color: rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-on-panel));
}

.tag-chip--filter:hover {
  background: rgba(var(--v-theme-error), 0.18);
  border-color: rgba(var(--v-theme-error), 0.55);
}

.tag-chip--filter-rejected {
  background: rgba(var(--v-theme-error), 0.14);
  border-color: rgba(var(--v-theme-error), 0.5);
  color: rgb(var(--v-theme-error));
}

.tag-chip--filter-rejected:hover {
  background: rgba(var(--v-theme-primary), 0.18);
  border-color: rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-on-panel));
}

.tag-chip--confidence-above {
  background: rgba(var(--v-theme-success), 0.14);
  border-color: rgba(var(--v-theme-success), 0.5);
  color: rgb(var(--v-theme-success));
}

.tag-chip--confidence-above:hover {
  background: rgba(var(--v-theme-error), 0.14);
  border-color: rgba(var(--v-theme-error), 0.5);
}

.tag-chip--confidence-below {
  background: rgba(var(--v-theme-warning), 0.14);
  border-color: rgba(var(--v-theme-warning), 0.5);
  color: rgb(var(--v-theme-warning));
}

.tag-chip--confidence-below:hover {
  background: rgba(var(--v-theme-error), 0.14);
  border-color: rgba(var(--v-theme-error), 0.5);
}

/* ── Tag confidence row ───────────────────────────────────────────────────── */
.gb-confidence-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.gb-confidence-tag-wrap {
  flex: 1;
  min-width: 80px;
}

/* ── ComfyUI section ──────────────────────────────────────────────────────── */
.gb-comfyui-section-header {
  cursor: pointer;
  margin-bottom: var(--space-3);
}
.gb-comfyui-chevron {
  opacity: 0.6;
}

.gb-comfy-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
}
/* Sub-labels under the ComfyUI section: a notch smaller (75%) than the section
   labels so "Models"/"LoRAs" read as a level below "ComfyUI". Derived from the
   ramp floor rather than a raw px so it still tracks the token. */
.gb-comfy-list-head .tbm-label {
  font-size: calc(var(--text-2xs) * 0.75);
}

.gb-comfy-list {
  width: 100%;
  max-height: 180px;
  overflow-y: auto;
  margin-bottom: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  padding: var(--space-1) var(--space-2);
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-panel));
}
.gb-comfy-list:last-child {
  margin-bottom: 0;
}
/* One compact full-width row per model/LoRA — matches the menu's .tbm-check
   density instead of Vuetify's chunky checkbox rows. */
.gb-comfy-check {
  display: flex;
  width: 100%;
  padding: var(--space-1) var(--space-1);
}
.gb-comfy-check-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
</style>
