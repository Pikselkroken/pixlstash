<template>
  <div class="gb-filter-panel popup-panel">
    <div class="gb-filter-panel-header">
      <div class="gb-filter-panel-title">Filters</div>
      <v-btn
        v-if="filterStore.isActive"
        variant="text"
        density="compact"
        size="x-small"
        color="primary"
        class="gb-filter-clear-all-btn"
        @click="filterStore.resetFilters()"
        >Clear all</v-btn
      >
    </div>
    <div
      v-if="!isReadOnly || isAllPicturesView"
      class="gb-filter-shared-only-row"
    >
      <label v-if="!isReadOnly" class="gb-filter-shared-only-label">
        <input
          type="checkbox"
          :checked="gbSharedOnlyFilter"
          @change="gbSharedOnlyFilter = $event.target.checked"
        />
        Shared pictures only
      </label>
      <label
        v-if="isAllPicturesView"
        class="gb-filter-shared-only-label gb-filter-shared-only-label--right"
      >
        <input
          type="checkbox"
          :checked="gbUnassignedOnlyFilter"
          @change="gbUnassignedOnlyFilter = $event.target.checked"
        />
        Unassigned only
      </label>
    </div>
    <div class="gb-filter-section-label">Media</div>
    <div
      class="gb-media-type-toggle"
      role="group"
      aria-label="Media type filter"
    >
      <v-btn
        v-for="opt in gbMediaTypeOptions"
        :key="opt.value"
        class="gb-media-type-button"
        :class="{
          'gb-media-type-button--active': gbMediaTypeFilter === opt.value,
        }"
        variant="text"
        :title="opt.title"
        :aria-pressed="gbMediaTypeFilter === opt.value"
        @click="gbSetMediaTypeFilter(opt.value)"
      >
        <v-icon size="16">{{ opt.icon }}</v-icon>
      </v-btn>
    </div>
    <div class="gb-score-range-section">
      <div class="gb-score-range-headers">
        <span class="gb-score-range-header-label">Min Score</span>
        <span
          class="gb-score-range-header-label gb-score-range-header-label--right"
          >Max Score</span
        >
      </div>
      <div class="gb-score-range-filter">
        <div class="gb-score-range-stars">
          <button
            v-for="n in 5"
            :key="'min-' + n"
            class="gb-score-star-btn"
            type="button"
            :title="`Set minimum score ${n}`"
            @click="gbSetMinScore(n)"
          >
            <v-icon
              size="15"
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
        <div class="gb-score-range-stars gb-score-range-stars--right">
          <button
            v-for="n in 5"
            :key="'max-' + n"
            class="gb-score-star-btn"
            type="button"
            :title="`Set maximum score ${n}`"
            @click="gbSetMaxScore(n)"
          >
            <v-icon
              size="15"
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
    <div class="gb-filter-section-label" style="margin-top: 10px">Face</div>
    <div class="gb-media-type-toggle" role="group" aria-label="Face filter">
      <v-btn
        v-for="opt in gbFaceBboxFilterOptions"
        :key="String(opt.value)"
        class="gb-media-type-button"
        :class="{
          'gb-media-type-button--active': gbFaceBboxFilter === opt.value,
        }"
        variant="text"
        :title="opt.title"
        :aria-pressed="gbFaceBboxFilter === opt.value"
        @click="gbSetFaceBboxFilter(opt.value)"
      >
        <span
          v-if="opt.value === 'without_face'"
          class="gb-face-no-detection-icon"
        >
          <v-icon size="16">{{ opt.icon }}</v-icon>
        </span>
        <v-icon v-else size="16">{{ opt.icon }}</v-icon>
      </v-btn>
    </div>
    <div class="gb-filter-section-header" style="margin-top: 10px">
      <span class="gb-filter-section-label" style="margin-top: 0">Tags</span>
      <v-btn
        v-if="gbTagFilter.length || gbTagRejectedFilter.length"
        variant="text"
        density="compact"
        size="x-small"
        color="primary"
        class="gb-filter-clear-all-btn"
        @click="
          gbTagFilter = [];
          gbTagRejectedFilter = [];
        "
        >Clear</v-btn
      >
    </div>
    <div class="gb-tag-filter-input-wrap">
      <input
        v-model="gbTagFilterInput"
        class="gb-tag-filter-input"
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
      class="gb-tag-filter-chips"
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
    <div class="gb-filter-section-label" style="margin-top: 10px">
      Tag confidence
    </div>
    <div class="gb-confidence-filter-row">
      <div class="gb-tag-filter-input-wrap gb-confidence-filter-tag-wrap">
        <input
          v-model="gbConfidenceTagInput"
          class="gb-tag-filter-input gb-confidence-filter-tag-input"
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
              'gb-tag-filter-suggestion--active': idx === gbConfidenceTagIndex,
            }"
            type="button"
            @mousedown.prevent="gbSelectConfidenceTagSuggestion(tag)"
            @mousemove="gbConfidenceTagIndex = idx"
          >
            {{ tag }}
          </button>
        </div>
      </div>
      <div class="gb-confidence-threshold-stepper">
        <input
          v-model.number="gbConfidenceThreshold"
          type="number"
          min="0"
          max="1"
          step="0.05"
          class="gb-confidence-threshold-input"
        />
      </div>
      <button
        class="gb-confidence-mode-btn"
        type="button"
        :title="
          gbConfidenceMode === 'above'
            ? 'High confidence, not labelled – click to switch'
            : 'Low confidence, labelled – click to switch'
        "
        @click="
          gbConfidenceMode = gbConfidenceMode === 'above' ? 'below' : 'above'
        "
      >
        {{ gbConfidenceMode === "above" ? "≥" : "<" }}
      </button>
      <button
        class="gb-confidence-add-btn"
        type="button"
        :disabled="!gbConfidenceTagInput.trim()"
        @click="gbAddConfidenceFilter()"
      >
        Add
      </button>
    </div>
    <div
      v-if="
        gbTagConfidenceAboveFilter.length || gbTagConfidenceBelowFilter.length
      "
      class="gb-tag-filter-chips"
    >
      <button
        v-for="entry in gbTagConfidenceAboveFilter"
        :key="`ca-${entry}`"
        class="tag-chip tag-chip--filter tag-chip--confidence-above"
        type="button"
        :title="`Prediction ≥${Math.round(parseFloat(entry.split(':')[1]) * 100)}%, not labelled`"
      >
        <span class="tag-chip-label">≥{{ gbConfidenceEntryLabel(entry) }}</span>
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
    <template
      v-if="gbComfyuiModelOptions.length || gbComfyuiLoraOptions.length"
    >
      <div
        class="gb-filter-section-header gb-comfyui-section-header"
        style="margin-top: 10px; cursor: pointer"
        @click="gbComfyuiFilterExpanded = !gbComfyuiFilterExpanded"
      >
        <span class="gb-filter-section-label" style="margin-top: 0"
          >ComfyUI</span
        >
        <v-icon size="16" style="opacity: 0.6">{{
          gbComfyuiFilterExpanded ? "mdi-chevron-up" : "mdi-chevron-down"
        }}</v-icon>
      </div>
      <template v-if="gbComfyuiModelOptions.length && gbComfyuiFilterExpanded">
        <div
          style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 4px;
            width: 100%;
          "
        >
          <span
            style="font-size: 0.85em; color: rgb(var(--v-theme-on-background))"
            >Models</span
          >
          <v-btn
            v-if="gbComfyuiModelFilter.length"
            variant="text"
            density="compact"
            size="x-small"
            color="primary"
            style="
              min-width: 0;
              padding: 0 4px;
              height: 18px;
              font-size: 0.75em;
            "
            @click="gbComfyuiModelFilter = []"
            >Clear</v-btn
          >
        </div>
        <div
          style="
            width: 100%;
            height: 200px;
            overflow-y: auto;
            margin-bottom: 8px;
            border: 1px solid rgba(var(--v-theme-on-background), 0.18);
            border-radius: 6px;
            padding: 2px 4px;
            background: rgba(var(--v-theme-on-background), 0.04);
            color: rgb(var(--v-theme-on-background));
          "
        >
          <v-checkbox
            v-for="m in gbComfyuiModelOptions"
            :key="m"
            v-model="gbComfyuiModelFilter"
            :value="m"
            :label="m.replace(/\.[^/.]+$/, '')"
            density="compact"
            hide-details
            color="primary"
          />
        </div>
      </template>
      <template v-if="gbComfyuiLoraOptions.length && gbComfyuiFilterExpanded">
        <div
          style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 4px;
            width: 100%;
          "
        >
          <span
            style="font-size: 0.85em; color: rgb(var(--v-theme-on-background))"
            >LoRAs</span
          >
          <v-btn
            v-if="gbComfyuiLoraFilter.length"
            variant="text"
            density="compact"
            size="x-small"
            color="primary"
            style="
              min-width: 0;
              padding: 0 4px;
              height: 18px;
              font-size: 0.75em;
            "
            @click="gbComfyuiLoraFilter = []"
            >Clear</v-btn
          >
        </div>
        <div
          style="
            width: 100%;
            height: 200px;
            overflow-y: auto;
            border: 1px solid rgba(var(--v-theme-on-background), 0.18);
            border-radius: 6px;
            padding: 2px 4px;
            background: rgba(var(--v-theme-on-background), 0.04);
            color: rgb(var(--v-theme-on-background));
          "
        >
          <v-checkbox
            v-for="l in gbComfyuiLoraOptions"
            :key="l"
            v-model="gbComfyuiLoraFilter"
            :value="l"
            :label="l.replace(/\.[^/.]+$/, '')"
            density="compact"
            hide-details
            color="primary"
          />
        </div>
      </template>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";

const props = defineProps({
  backendUrl: { type: String, default: "" },
  selectedCharacter: { type: String, default: null },
  allPicturesId: { type: String, default: null },
  open: { type: Boolean, default: false },
});

const filterStore = useFilterStore();

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

const gbMediaTypeOptions = [
  { value: "all", icon: "mdi-multimedia", title: "Show all media" },
  { value: "images", icon: "mdi-image", title: "Show images only" },
  { value: "videos", icon: "mdi-video", title: "Show videos only" },
];

const gbFaceBboxFilterOptions = [
  { value: null, icon: "mdi-all-inclusive", title: "All pictures" },
  { value: "with_face", icon: "mdi-face-man", title: "With detected face" },
  {
    value: "without_face",
    icon: "mdi-face-man",
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
        } catch {}
      }
    } else {
      gbTagFilterInput.value = "";
      gbTagFilterSuggestions.value = [];
    }
  },
);
</script>

<style scoped>
/* ── Filter panel ─────────────────────────────────────────────────────────── */
.gb-filter-panel {
  align-items: flex-start;
  gap: 6px;
  padding: 10px 12px;
  min-width: 280px;
  max-width: 340px;
}

.gb-filter-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.gb-filter-panel-title {
  font-size: 1.02em;
  font-weight: 500;
  letter-spacing: 0.02em;
}

.gb-filter-clear-all-btn {
  min-width: 0;
  padding: 0 4px;
  height: 20px;
  font-size: 0.8em;
}

.gb-filter-shared-only-row {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.gb-filter-shared-only-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85em;
  cursor: pointer;
}

.gb-filter-shared-only-label--right {
  margin-left: auto;
}

.gb-filter-section-label {
  font-size: 0.8em;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.6;
  margin-top: 4px;
  width: 100%;
}

.gb-filter-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.gb-media-type-toggle {
  display: flex;
  gap: 2px;
  width: 100%;
}

.gb-media-type-button {
  flex: 1 !important;
  min-width: 0 !important;
  padding: 0 4px !important;
  height: 32px !important;
  border-radius: 4px !important;
  opacity: 0.55 !important;
}

.gb-media-type-button--active {
  opacity: 1 !important;
  color: rgb(var(--v-theme-on-primary)) !important;
  background: rgb(var(--v-theme-primary)) !important;
}

.gb-face-no-detection-icon {
  position: relative;
  display: inline-flex;
}

.gb-face-no-detection-icon::after {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%) rotate(45deg);
  width: 2px;
  height: 18px;
  background: currentColor;
  pointer-events: none;
}

.gb-score-range-section {
  width: 100%;
}

.gb-score-range-headers {
  display: flex;
  justify-content: space-between;
  margin-bottom: 2px;
}

.gb-score-range-header-label {
  font-size: 0.78em;
  opacity: 0.65;
}

.gb-score-range-filter {
  display: flex;
  justify-content: space-between;
}

.gb-score-range-stars {
  display: flex;
  flex-shrink: 0;
}

.gb-score-star-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 1px 2px;
  background: none;
  border: none;
  cursor: pointer;
  color: rgb(var(--v-theme-on-background));
  opacity: 0.7;
  line-height: 1;
}
.gb-score-star-btn:hover {
  opacity: 1;
}

.gb-tag-filter-input-wrap {
  position: relative;
  width: 100%;
}

.gb-tag-filter-input {
  width: 100%;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.25);
  background: rgba(var(--v-theme-on-background), 0.06);
  color: rgb(var(--v-theme-on-background));
  font-size: 0.85em;
  outline: none;
  box-sizing: border-box;
}

.gb-tag-filter-input:focus {
  border-color: rgb(var(--v-theme-primary));
}

.gb-tag-filter-dropdown {
  position: absolute;
  top: calc(100% + 2px);
  left: 0;
  right: 0;
  z-index: 200;
  background: rgba(var(--v-theme-background), 0.98);
  border: 1px solid rgba(var(--v-theme-on-background), 0.2);
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  overflow: hidden;
  pointer-events: none;
}

.gb-tag-filter-dropdown--hover-enabled {
  pointer-events: auto;
}

.gb-tag-filter-suggestion {
  display: block;
  width: 100%;
  padding: 5px 10px;
  text-align: left;
  cursor: pointer;
  font-size: 0.85em;
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-background));
}

.gb-tag-filter-suggestion--active,
.gb-tag-filter-suggestion:hover {
  background: rgba(var(--v-theme-primary), 0.12);
}

.gb-tag-filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  width: 100%;
}

/* Filter tag pills. The base `.tag-chip` and these `--filter`/`--confidence`
   variants live here because Vue scoped styles do not cross component
   boundaries: the chips in this panel can't inherit the `.tag-chip` rule that
   lives in TbTagPanel's scope, so the pill shape and colours are declared
   locally. The variants give each chip its fill: green for a confirmed match,
   red for a rejected (negative) match, success/warning for confidence bounds.
   Hover previews the opposite state, since clicking a chip toggles it. */
.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border-radius: 12px;
  padding: 2px 7px;
  font-size: 0.78rem;
  cursor: pointer;
  transition:
    background 0.15s,
    opacity 0.15s;
  line-height: 1.5;
  white-space: nowrap;
  border: none;
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
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-on-surface));
}

.tag-chip--filter:hover {
  background: rgba(var(--v-theme-error), 0.18);
  border-color: rgba(var(--v-theme-error), 0.55);
}

.tag-chip--filter-rejected {
  background: rgba(var(--v-theme-error), 0.14);
  border: 1px solid rgba(var(--v-theme-error), 0.5);
  color: rgb(var(--v-theme-error));
}

.tag-chip--filter-rejected:hover {
  background: rgba(var(--v-theme-primary), 0.18);
  border-color: rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-on-surface));
}

.tag-chip--confidence-above {
  background: rgba(var(--v-theme-success), 0.14);
  border: 1px solid rgba(var(--v-theme-success), 0.5);
  color: rgb(var(--v-theme-success));
}

.tag-chip--confidence-above:hover {
  background: rgba(var(--v-theme-error), 0.14);
  border-color: rgba(var(--v-theme-error), 0.5);
}

.tag-chip--confidence-below {
  background: rgba(var(--v-theme-warning), 0.14);
  border: 1px solid rgba(var(--v-theme-warning), 0.5);
  color: rgb(var(--v-theme-warning));
}

.tag-chip--confidence-below:hover {
  background: rgba(var(--v-theme-error), 0.14);
  border-color: rgba(var(--v-theme-error), 0.5);
}

.gb-confidence-filter-row {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  flex-wrap: wrap;
}

.gb-confidence-filter-tag-wrap {
  flex: 1;
  min-width: 80px;
}

.gb-confidence-filter-tag-input {
  width: 100%;
}

.gb-confidence-threshold-stepper {
  display: flex;
  align-items: center;
  gap: 2px;
}

.gb-confidence-threshold-input {
  width: 64px;
  padding: 4px 4px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.25);
  background: rgba(var(--v-theme-on-background), 0.06);
  color: rgb(var(--v-theme-on-background));
  font-size: 0.85em;
  text-align: center;
  height: 28px;
  box-sizing: border-box;
}

.gb-confidence-mode-btn {
  min-width: 28px;
  height: 28px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-on-background), 0.25);
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  cursor: pointer;
  font-size: 0.9em;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
}

.gb-confidence-add-btn {
  padding: 0 10px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  font-size: 0.85em;
  height: 28px;
  box-sizing: border-box;
}

.gb-confidence-add-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.gb-comfyui-section-header {
  width: 100%;
}
</style>
