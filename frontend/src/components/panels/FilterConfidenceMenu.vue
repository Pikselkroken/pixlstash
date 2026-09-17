<template>
  <div class="tbm fm-sub" role="group" aria-label="Tag confidence">
    <div class="tbm-header">
      <span class="tbm-title">Tag confidence</span>
      <span class="tbm-spacer"></span>
      <button
        class="tbm-ghost"
        type="button"
        :disabled="!entries.length"
        @click="clearAll"
      >
        Clear
      </button>
    </div>
    <div class="tbm-section">
      <Segmented
        v-model="kind"
        :options="KIND_OPTIONS"
        full
        aria-label="Rule kind"
      />
      <p class="fm-help">{{ kindHelp }}</p>
    </div>
    <div class="tbm-section">
      <span class="tbm-label">Tag</span>
      <div class="tbm-input-wrap fm-list-field">
        <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
        <input
          ref="fieldRef"
          v-model="query"
          class="tbm-input tbm-input--with-icon"
          placeholder="Filter tags…"
          aria-label="Filter tags"
          autocomplete="off"
          @keydown.enter.prevent="shownTags[0] && (tag = shownTags[0].id)"
        />
      </div>
      <OptionRows
        v-if="shownTags.length"
        v-model="tag"
        :options="shownTags"
        aria-label="Tag"
      />
      <p v-else class="fm-help">No tag matches.</p>
    </div>
    <div class="tbm-section">
      <span class="tbm-label">{{
        kind === "missing" ? "Tagger at least" : "Tagger below"
      }}</span>
      <OptionRows
        :options="thresholdOptions"
        :model-value="currentThreshold"
        :disabled="!tag"
        aria-label="Threshold"
        @update:model-value="setThreshold"
      >
        <template #meta="{ option }">
          <span class="fm-n">{{ formatCount(option.count) }}</span>
        </template>
      </OptionRows>
    </div>
    <div class="tbm-footer">
      <template v-if="tag">On the strip as "{{ chipPreview }}".</template>
      <template v-else>Pick a tag, then a threshold.</template>
    </div>
  </div>
</template>

<script setup>
/**
 * Tag confidence as one menu of three sections: kind, tag, threshold. Choosing
 * a threshold (click or arrow, the radiogroup contract) adds the chip for that
 * tag or moves it; the chip's × or Clear removes it. The menu stays open for
 * the next tag.
 */
import { computed, nextTick, onMounted, ref } from "vue";
import OptionRows from "../widgets/OptionRows.vue";
import Segmented from "../widgets/Segmented.vue";
import { useFilterStore } from "../../stores/useFilterStore";
import { filterParams } from "../../composables/useFilterCounts";
import {
  CONFIDENCE_THRESHOLDS,
  confidenceEntry,
  parseConfidenceEntry,
  percent,
} from "../../utils/filterChips";

const MAX_TAGS = 8;

const props = defineProps({
  // [{ tag, count }] from /tags, most used first.
  tags: { type: Array, required: true },
  count: { type: Function, required: true },
});

const store = useFilterStore();

const KIND_OPTIONS = [
  { id: "missing", label: "Missing tag" },
  { id: "doubtful", label: "Doubtful tag" },
];

const kind = ref("missing");
const tag = ref(null);
const query = ref("");
const fieldRef = ref(null);

const listKey = computed(() =>
  kind.value === "missing"
    ? "tagConfidenceAboveFilter"
    : "tagConfidenceBelowFilter",
);
const param = computed(() =>
  kind.value === "missing" ? "tag_confidence_above" : "tag_confidence_below",
);

const kindHelp = computed(() =>
  kind.value === "missing"
    ? "Missing: the tagger sees it, the tag isn't applied."
    : "Doubtful: the tag is applied, the tagger isn't sure.",
);

const entries = computed(() => [
  ...(store.tagConfidenceAboveFilter || []),
  ...(store.tagConfidenceBelowFilter || []),
]);

const shownTags = computed(() => {
  const q = query.value.trim().toLowerCase();
  const rows = q
    ? props.tags.filter((t) => t.tag.toLowerCase().includes(q))
    : props.tags;
  const out = rows.slice(0, MAX_TAGS).map((t) => ({ id: t.tag, label: t.tag }));
  // Keep the picked tag visible while the field narrows past it.
  if (tag.value && !out.some((o) => o.id === tag.value)) {
    out.unshift({ id: tag.value, label: tag.value });
  }
  return out;
});

const currentEntry = computed(() =>
  (store[listKey.value] || []).find(
    (e) => parseConfidenceEntry(e).tag === tag.value,
  ),
);
const currentThreshold = computed(() =>
  currentEntry.value
    ? parseConfidenceEntry(currentEntry.value).threshold
    : null,
);

const thresholdOptions = computed(() =>
  CONFIDENCE_THRESHOLDS.map((t) => ({
    id: t,
    label: percent(t),
    count: tag.value
      ? props.count(
          filterParams([[param.value, confidenceEntry(tag.value, t)]]),
        )
      : undefined,
  })),
);

const chipPreview = computed(() => {
  const t = percent(currentThreshold.value ?? 0.8);
  return kind.value === "missing"
    ? `Missing tag ${tag.value} ${t}+`
    : `Doubtful tag ${tag.value} under ${t}`;
});

function setThreshold(threshold) {
  if (!tag.value) return;
  const key = listKey.value;
  const kept = (store[key] || []).filter(
    (e) => parseConfidenceEntry(e).tag !== tag.value,
  );
  store[key] = [...kept, confidenceEntry(tag.value, threshold)];
}

function clearAll() {
  store.tagConfidenceAboveFilter = [];
  store.tagConfidenceBelowFilter = [];
}

function formatCount(n) {
  return n == null ? "" : Number(n).toLocaleString();
}

onMounted(() => nextTick(() => fieldRef.value?.focus()));
</script>

<style scoped>
.fm-list-field {
  margin-bottom: var(--space-2);
}
</style>
