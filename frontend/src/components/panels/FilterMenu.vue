<template>
  <div class="fm-cascade" @keydown.left="onLeft">
    <div ref="rootRef" class="tbm fm-root" role="group" aria-label="Filters">
      <span class="tbm-caret tbm-caret--start"></span>
      <div class="tbm-header">
        <v-icon size="18" class="tbm-header-icon">mdi-filter</v-icon>
        <span class="tbm-title">Filters</span>
        <span class="tbm-spacer"></span>
        <span class="fm-of">{{ ofLabel }}</span>
        <button
          class="tbm-ghost"
          type="button"
          :disabled="!chips.length"
          @click="clearAll"
        >
          Clear all
        </button>
      </div>

      <div class="tbm-section">
        <span class="tbm-label">Picture</span>
        <template v-for="k in PICTURE_KINDS" :key="k.id">
          <button
            v-if="k.id !== 'sharing' || !isReadOnly"
            :ref="(el) => (rowRefs[k.id] = el)"
            class="fm-row"
            type="button"
            :aria-expanded="sub === k.id ? 'true' : 'false'"
            :aria-controls="sub === k.id ? submenuId : undefined"
            @click="toggle(k.id)"
            @keydown.right.prevent="openKind(k.id)"
          >
            <v-icon size="16" class="fm-row-lead">{{ k.icon }}</v-icon>
            <span class="fm-row-label">{{ k.label }}</span>
            <span class="fm-n">{{ rowValue(k.id) }}</span>
            <v-icon size="16" class="fm-row-trail">mdi-chevron-right</v-icon>
          </button>
        </template>
      </div>
      <div class="tbm-section">
        <span class="tbm-label">Content</span>
        <template v-for="k in CONTENT_KINDS" :key="k.id">
          <button
            :ref="(el) => (rowRefs[k.id] = el)"
            class="fm-row"
            type="button"
            :aria-expanded="sub === k.id ? 'true' : 'false'"
            :aria-controls="sub === k.id ? submenuId : undefined"
            @click="toggle(k.id)"
            @keydown.right.prevent="openKind(k.id)"
          >
            <v-icon size="16" class="fm-row-lead">{{ k.icon }}</v-icon>
            <span class="fm-row-label">{{ k.label }}</span>
            <span class="fm-n">{{ rowValue(k.id) }}</span>
            <v-icon size="16" class="fm-row-trail">mdi-chevron-right</v-icon>
          </button>
        </template>
      </div>
      <div class="tbm-section">
        <button
          :ref="(el) => (rowRefs.problems = el)"
          class="fm-row"
          type="button"
          :aria-expanded="sub === 'problems' ? 'true' : 'false'"
          :aria-controls="sub === 'problems' ? submenuId : undefined"
          @click="toggle('problems')"
          @keydown.right.prevent="openKind('problems')"
        >
          <v-icon size="16" class="fm-row-lead">mdi-alert-outline</v-icon>
          <span class="fm-row-label">Problems</span>
          <span class="fm-n">{{ rowValue("problems") }}</span>
          <v-icon size="16" class="fm-row-trail">mdi-chevron-right</v-icon>
        </button>
      </div>
    </div>

    <div
      v-if="sub"
      :id="submenuId"
      ref="subRef"
      class="fm-sub-slot"
      :style="{ marginTop: `${subTop}px` }"
    >
      <!-- Media, Faces, Stacks, Sharing: pick one, led by the row that turns
           the filter off ("All" or "Any"), so no filter is a choice too. -->
      <div
        v-if="PICK_ONE[sub]"
        class="tbm fm-sub"
        role="group"
        :aria-label="PICK_ONE[sub].title"
      >
        <div class="tbm-header">
          <span class="tbm-title">{{ PICK_ONE[sub].title }}</span>
        </div>
        <div class="tbm-section">
          <OptionRows
            :options="pickOptions(sub)"
            :model-value="pickValue(sub)"
            :aria-label="PICK_ONE[sub].title"
            @update:model-value="(id) => setPick(sub, id)"
          >
            <template #meta="{ option }">
              <span class="fm-n">{{ formatCount(option.count) }}</span>
            </template>
          </OptionRows>
        </div>
      </div>

      <div
        v-else-if="sub === 'score'"
        class="tbm fm-sub"
        role="group"
        aria-label="Score"
      >
        <div class="tbm-header">
          <span class="tbm-title">Score</span>
        </div>
        <div
          class="tbm-section"
          role="radiogroup"
          aria-label="At least"
          @keydown="(e) => onStarKeydown(e, 'minScoreFilter')"
        >
          <span class="tbm-label">At least</span>
          <button
            v-for="o in starOptions('minScoreFilter')"
            :key="`minScoreFilter-${o.id}`"
            class="fm-row"
            type="button"
            role="radio"
            :aria-checked="store.minScoreFilter === o.id ? 'true' : 'false'"
            :aria-label="o.id == null ? 'Any' : `At least ${o.id} stars`"
            :tabindex="
              o.id ===
              tabStopId(starOptions('minScoreFilter'), store.minScoreFilter)
                ? 0
                : -1
            "
            :disabled="o.disabled"
            @click="setStar('minScoreFilter', o.id)"
          >
            <v-icon size="16" class="fm-row-lead">{{
              store.minScoreFilter === o.id
                ? "mdi-radiobox-marked"
                : "mdi-radiobox-blank"
            }}</v-icon>
            <span class="fm-row-label">
              <template v-if="o.id == null">Any</template>
              <span v-else class="fm-stars" aria-hidden="true">
                <v-icon
                  v-for="i in 5"
                  :key="i"
                  size="14"
                  :class="{ 'fm-star--off': i > o.id }"
                  >mdi-star</v-icon
                >
              </span>
            </span>
          </button>
        </div>
        <div
          class="tbm-section"
          role="radiogroup"
          aria-label="At most"
          @keydown="(e) => onStarKeydown(e, 'maxScoreFilter')"
        >
          <span class="tbm-label">At most</span>
          <button
            v-for="o in starOptions('maxScoreFilter')"
            :key="`maxScoreFilter-${o.id}`"
            class="fm-row"
            type="button"
            role="radio"
            :aria-checked="store.maxScoreFilter === o.id ? 'true' : 'false'"
            :aria-label="o.id == null ? 'Any' : `At most ${o.id} stars`"
            :tabindex="
              o.id ===
              tabStopId(starOptions('maxScoreFilter'), store.maxScoreFilter)
                ? 0
                : -1
            "
            :disabled="o.disabled"
            @click="setStar('maxScoreFilter', o.id)"
          >
            <v-icon size="16" class="fm-row-lead">{{
              store.maxScoreFilter === o.id
                ? "mdi-radiobox-marked"
                : "mdi-radiobox-blank"
            }}</v-icon>
            <span class="fm-row-label">
              <template v-if="o.id == null">Any</template>
              <span v-else class="fm-stars" aria-hidden="true">
                <v-icon
                  v-for="i in 5"
                  :key="i"
                  size="14"
                  :class="{ 'fm-star--off': i > o.id }"
                  >mdi-star</v-icon
                >
              </span>
            </span>
          </button>
        </div>
        <div class="tbm-section">
          <label class="tbm-check fm-check">
            <input
              type="checkbox"
              :checked="store.unscoredOnlyFilter"
              @change="store.unscoredOnlyFilter = $event.target.checked"
            />
            <span class="fm-check-label">Include unscored</span>
            <span class="fm-n">{{ formatCount(count("unscored=1")) }}</span>
          </label>
        </div>
        <div class="tbm-footer">{{ scoreHint }}</div>
      </div>

      <div
        v-else-if="sub === 'problems'"
        class="tbm fm-sub"
        role="group"
        aria-label="Problems"
      >
        <div class="tbm-header">
          <span class="tbm-title">Problems</span>
          <span class="tbm-spacer"></span>
          <button
            class="tbm-ghost"
            type="button"
            :disabled="!problemsActive"
            @click="clearProblems"
          >
            Clear
          </button>
        </div>
        <div class="tbm-section">
          <label
            class="tbm-check fm-check"
            :class="{ 'fm-check--off': !allPicturesView }"
          >
            <input
              type="checkbox"
              :disabled="!allPicturesView"
              :checked="allPicturesView && store.unassignedOnlyFilter"
              @change="store.unassignedOnlyFilter = $event.target.checked"
            />
            <span class="fm-check-label">No character</span>
            <span v-if="allPicturesView" class="fm-n">{{
              formatCount(count("character_id=UNASSIGNED"))
            }}</span>
          </label>
          <label class="tbm-check fm-check">
            <input
              type="checkbox"
              :checked="impossibleAll"
              :indeterminate="impossibleSome"
              @change="setAllImpossible($event.target.checked)"
            />
            <span class="fm-check-label">Impossible combinations</span>
            <span class="fm-n">{{
              formatCount(count(impossibleAllParams))
            }}</span>
          </label>
          <div class="fm-nest">
            <label
              v-for="opt in IMPOSSIBLE_OPTIONS"
              :key="opt.id"
              class="tbm-check fm-check"
            >
              <input
                type="checkbox"
                :checked="(store.impossibleSources || []).includes(opt.id)"
                @change="toggleImpossible(opt.id, $event.target.checked)"
              />
              <span class="fm-check-label">{{ opt.label }}</span>
              <span class="fm-n">{{
                formatCount(count(`impossible_tag_source=${opt.id}`))
              }}</span>
            </label>
          </div>
        </div>
        <div class="tbm-footer">
          {{
            allPicturesView
              ? 'Chips read "Problem no character".'
              : "No character works in All Pictures."
          }}
        </div>
      </div>

      <FilterChecklistMenu
        v-else-if="sub === 'tags'"
        title="Tags"
        placeholder="Filter tags…"
        :items="tagItems"
        :checked="tagMode === 'has' ? store.tagFilter : store.tagRejectedFilter"
        :clearable="
          Boolean(store.tagFilter.length || store.tagRejectedFilter.length)
        "
        empty-text="No tag matches."
        @toggle="toggleTag"
        @clear="clearTags"
      >
        <template #top>
          <Segmented
            v-model="tagMode"
            :options="TAG_MODE_OPTIONS"
            full
            aria-label="Tag rule"
            class="fm-seg"
          />
        </template>
      </FilterChecklistMenu>

      <FilterConfidenceMenu
        v-else-if="sub === 'confidence'"
        :tags="tagRows"
        :count="count"
      />

      <FilterChecklistMenu
        v-else-if="sub === 'model'"
        title="Checkpoint"
        placeholder="Filter checkpoints…"
        :items="modelItems"
        :checked="store.comfyuiModelFilter"
        :count-for="
          (item) => count(filterParams([['comfyui_model', item.value]]))
        "
        :clearable="store.comfyuiModelFilter.length > 0"
        empty-text="No pictures record a checkpoint."
        @toggle="(v, on) => toggleIn('comfyuiModelFilter', v, on)"
        @clear="store.comfyuiModelFilter = []"
      />

      <FilterChecklistMenu
        v-else-if="sub === 'lora'"
        title="LoRA"
        placeholder="Filter LoRAs…"
        :items="loraItems"
        :checked="store.comfyuiLoraFilter"
        :count-for="
          (item) => count(filterParams([['comfyui_lora', item.value]]))
        "
        :clearable="store.comfyuiLoraFilter.length > 0"
        empty-text="No pictures record a LoRA."
        @toggle="(v, on) => toggleIn('comfyuiLoraFilter', v, on)"
        @clear="store.comfyuiLoraFilter = []"
      />
    </div>
  </div>
</template>

<script setup>
/**
 * The toolbar funnel's menu. Its only job is to add filters to the strip under
 * the toolbar: a root menu of filter kinds, each opening its own menu beside it.
 * Nothing here closes the menu - every change lands on the strip at once - so
 * the way out is Esc, a click outside, or the funnel (the v-menu owns those).
 */
import { computed, nextTick, reactive, ref, watch } from "vue";
import OptionRows from "../widgets/OptionRows.vue";
import Segmented from "../widgets/Segmented.vue";
import { arrowStep, tabStopId } from "../../utils/radioGroup.js";
import FilterChecklistMenu from "./FilterChecklistMenu.vue";
import FilterConfidenceMenu from "./FilterConfidenceMenu.vue";
import { isReadOnly } from "../../utils/apiClient";
import { listTags } from "../../api/tags";
import { listComfyuiLoras, listComfyuiModels } from "../../api/pictures";
import { useFilterStore } from "../../stores/useFilterStore";
import { useGridStore } from "../../stores/useGridStore";
import { PIL_IMAGE_EXTENSIONS, VIDEO_EXTENSIONS } from "../../utils/media.js";
import {
  filterParams,
  useFilterCounts,
} from "../../composables/useFilterCounts";
import {
  FACE_OPTIONS,
  IMPOSSIBLE_OPTIONS,
  MEDIA_OPTIONS,
  STACK_OPTIONS,
  filterChips,
  modelLabel,
  scoreChipValue,
} from "../../utils/filterChips";

const props = defineProps({
  // The grid's view with no filters, pre-encoded (buildFilterCountBaseQuery).
  countBaseQuery: { type: String, default: null },
  allPicturesView: { type: Boolean, default: true },
  open: { type: Boolean, default: false },
});

const store = useFilterStore();
const gridStore = useGridStore();

const PICTURE_KINDS = [
  { id: "media", label: "Media", icon: "mdi-image-multiple-outline" },
  { id: "faces", label: "Faces", icon: "mdi-face-recognition" },
  { id: "stacks", label: "Stacks", icon: "mdi-layers-outline" },
  { id: "sharing", label: "Sharing", icon: "mdi-share-variant-outline" },
];
const CONTENT_KINDS = [
  { id: "score", label: "Score", icon: "mdi-star-outline" },
  { id: "tags", label: "Tags", icon: "mdi-tag-outline" },
  { id: "confidence", label: "Tag confidence", icon: "mdi-tag-search-outline" },
  // The checkpoint glyph is the model shelf's (CAPABILITY_ICONS).
  { id: "model", label: "Checkpoint", icon: "mdi-package-variant-closed" },
  { id: "lora", label: "LoRA", icon: "mdi-puzzle-outline" },
];

const formatParams = (exts) =>
  filterParams(exts.map((e) => ["format", e.toUpperCase()]));

// Each pick-one kind: its store field, its "off" value and that row's label,
// and the one query parameter a choice adds for its count.
const PICK_ONE = {
  media: {
    title: "Media",
    anyLabel: "All",
    field: "mediaTypeFilter",
    off: "all",
    options: MEDIA_OPTIONS,
    params: (id) =>
      formatParams(id === "images" ? PIL_IMAGE_EXTENSIONS : VIDEO_EXTENSIONS),
  },
  faces: {
    title: "Faces",
    anyLabel: "Any",
    field: "faceBboxFilter",
    off: null,
    options: FACE_OPTIONS,
    params: (id) => filterParams([["face_filter", id]]),
  },
  stacks: {
    title: "Stacks",
    anyLabel: "All",
    field: "stackStateFilter",
    off: "all",
    options: STACK_OPTIONS,
    params: (id) => filterParams([["stack_state", id]]),
  },
  // Only "Shared": the backend can list what an owner has shared, not the
  // complement, so "Not shared" waits on that.
  sharing: {
    title: "Sharing",
    anyLabel: "All",
    field: "sharedOnlyFilter",
    off: false,
    options: [
      {
        id: true,
        label: "Shared",
        chip: "shared",
        icon: "mdi-share-variant-outline",
      },
    ],
    params: () => "shared_only=true",
  },
};

const STAR_ROWS = [null, 1, 2, 3, 4, 5];
const TAG_MODE_OPTIONS = [
  { id: "has", label: "Has tag" },
  { id: "lacks", label: "Lacks tag" },
];

const sub = ref(null);
const subTop = ref(0);
const rootRef = ref(null);
const subRef = ref(null);
const rowRefs = reactive({});
const submenuId = "filter-submenu";

const { count, reset } = useFilterCounts(() => props.countBaseQuery);

const chips = computed(() =>
  filterChips(store, { allPicturesView: props.allPicturesView }),
);

const total = computed(() => count(""));
const ofLabel = computed(() => {
  const n = Number(gridStore.matchCount || 0).toLocaleString();
  return total.value == null ? n : `${n} of ${total.value.toLocaleString()}`;
});

function formatCount(n) {
  return n == null ? "" : Number(n).toLocaleString();
}

// ── Root rows ────────────────────────────────────────────────────────────────
const KIND_OF_CHIP = {
  Media: "media",
  Faces: "faces",
  Stacks: "stacks",
  Sharing: "sharing",
  "Has tag": "tags",
  "Lacks tag": "tags",
  "Missing tag": "confidence",
  "Doubtful tag": "confidence",
  Checkpoint: "model",
  LoRA: "lora",
  Problem: "problems",
};

function rowValue(kind) {
  if (kind === "score") {
    return scoreChipValue(
      store.minScoreFilter,
      store.maxScoreFilter,
      store.unscoredOnlyFilter,
    );
  }
  const n = chips.value.filter((c) => KIND_OF_CHIP[c.kind] === kind).length;
  return n || "";
}

function openKind(kind) {
  sub.value = kind;
  const row = rowRefs[kind];
  const rowTop = row ? Math.max(0, row.offsetTop - 8) : 0;
  subTop.value = rowTop;
  nextTick(() => {
    // Line the submenu up with its row, but no lower than keeps its bottom
    // level with the root menu's: a tall submenu (Score) hanging below the
    // root made the whole cascade too tall and the menu jumped up over the
    // toolbar to fit.
    const rootHeight = rootRef.value?.offsetHeight ?? 0;
    const subHeight = subRef.value?.offsetHeight ?? 0;
    subTop.value = Math.max(0, Math.min(rowTop, rootHeight - subHeight));
    // Into the body, never the header's Clear. The checklist and confidence
    // menus focus their own search field once they mount.
    const body = subRef.value?.querySelector(".tbm-section");
    const target =
      body?.querySelector('[role="radio"][tabindex="0"]') ??
      body?.querySelector("input:not([disabled]), button:not([disabled])");
    target?.focus();
  });
}

function toggle(kind) {
  if (sub.value === kind) sub.value = null;
  else openKind(kind);
}

// Left arrow inside a submenu returns to its row, unless it is moving a caret.
function onLeft(event) {
  if (!sub.value || event.target?.tagName === "INPUT") return;
  if (!subRef.value?.contains(event.target)) return;
  event.preventDefault();
  rowRefs[sub.value]?.focus();
  sub.value = null;
}

function clearAll() {
  for (const chip of chips.value) chip.remove();
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      reset();
      loadLists();
    } else {
      sub.value = null;
    }
  },
  { immediate: true },
);

// ── Pick-one kinds ───────────────────────────────────────────────────────────
// The off row leads with the kind's own glyph and the unfiltered count.
function pickOptions(kind) {
  const def = PICK_ONE[kind];
  const icon = PICTURE_KINDS.find((k) => k.id === kind).icon;
  return [
    { id: null, label: def.anyLabel, icon, count: total.value },
    ...def.options.map((o) => ({ ...o, count: count(def.params(o.id)) })),
  ];
}

function pickValue(kind) {
  const def = PICK_ONE[kind];
  const v = store[def.field];
  return v === def.off ? null : v;
}

function setPick(kind, id) {
  const def = PICK_ONE[kind];
  store[def.field] = id == null ? def.off : id;
}

// ── Score ────────────────────────────────────────────────────────────────────
const scoreHint = computed(() => {
  const min = store.minScoreFilter;
  if (min === 2) return "1 is dimmed under At most: it is below the minimum.";
  if (min > 2) {
    return `1–${min - 1} are dimmed under At most: they are below the minimum.`;
  }
  return "At least and At most make one Score chip.";
});

// At most rows below the minimum are disabled, which arrowStep and tabStopId
// both skip.
function starOptions(field) {
  const min = store.minScoreFilter;
  return STAR_ROWS.map((id) => ({
    id,
    disabled:
      field === "maxScoreFilter" && id != null && min != null && id < min,
  }));
}

function setStar(field, n) {
  store[field] = n;
  if (
    field === "minScoreFilter" &&
    n != null &&
    store.maxScoreFilter != null &&
    store.maxScoreFilter < n
  ) {
    store.maxScoreFilter = n;
  }
}

// The radiogroup contract (utils/radioGroup.js): one tab stop, arrows select.
function onStarKeydown(event, field) {
  const id = arrowStep(event, starOptions(field), store[field]);
  if (id !== undefined) setStar(field, id);
}

// ── Problems ─────────────────────────────────────────────────────────────────
const impossibleCount = computed(() => (store.impossibleSources || []).length);
const impossibleAll = computed(
  () => impossibleCount.value === IMPOSSIBLE_OPTIONS.length,
);
const impossibleSome = computed(
  () => impossibleCount.value > 0 && !impossibleAll.value,
);
const impossibleAllParams = filterParams(
  IMPOSSIBLE_OPTIONS.map((o) => ["impossible_tag_source", o.id]),
);
const problemsActive = computed(
  () =>
    impossibleCount.value > 0 ||
    (props.allPicturesView && store.unassignedOnlyFilter),
);

function setAllImpossible(on) {
  store.impossibleSources = on ? IMPOSSIBLE_OPTIONS.map((o) => o.id) : [];
}

function toggleImpossible(id, on) {
  toggleIn("impossibleSources", id, on);
}

function clearProblems() {
  store.impossibleSources = [];
  store.unassignedOnlyFilter = false;
}

// ── Lists: tags, models, LoRAs ───────────────────────────────────────────────
const tagRows = ref([]);
const modelNames = ref([]);
const loraNames = ref([]);
const tagMode = ref("has");

// /tags counts span the pictures this session may see, not the current view:
// one request for the whole vocabulary rather than one per tag.
const tagItems = computed(() =>
  tagRows.value.map((t) => ({ value: t.tag, label: t.tag, count: t.count })),
);
const modelItems = computed(() =>
  modelNames.value.map((m) => ({
    value: m.value,
    label: m.name || modelLabel(m.value),
  })),
);
const loraItems = computed(() =>
  loraNames.value.map((m) => ({
    value: m.value,
    label: m.name || modelLabel(m.value),
  })),
);

async function loadLists() {
  try {
    const [tags, models, loras] = await Promise.all([
      listTags(),
      listComfyuiModels(),
      listComfyuiLoras(),
    ]);
    tagRows.value = Array.isArray(tags) ? tags : [];
    modelNames.value = Array.isArray(models) ? models : [];
    loraNames.value = Array.isArray(loras) ? loras : [];
  } catch (err) {
    // The lists stay as they were; the other filters still work.
    console.warn("Failed to load filter menu lists", err);
  }
}

function toggleIn(field, value, on) {
  const current = store[field] || [];
  store[field] = on
    ? current.includes(value)
      ? current
      : [...current, value]
    : current.filter((v) => v !== value);
}

// A tag is either required or excluded, never both.
function toggleTag(tag, on) {
  const [into, other] =
    tagMode.value === "has"
      ? ["tagFilter", "tagRejectedFilter"]
      : ["tagRejectedFilter", "tagFilter"];
  toggleIn(into, tag, on);
  if (on) toggleIn(other, tag, false);
}

function clearTags() {
  store.tagFilter = [];
  store.tagRejectedFilter = [];
}
</script>

<style scoped>
.fm-cascade {
  display: flex;
  /* A phone has no room for two menus side by side: the submenu wraps below. */
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--space-2);
}
.fm-root {
  width: var(--filter-menu-w);
  max-width: 94vw;
}
.fm-of {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  white-space: nowrap;
}
.fm-sub-slot {
  max-height: min(80vh, 760px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.fm-stars {
  display: inline-flex;
  color: rgb(var(--v-theme-surface-warning));
}
.fm-star--off {
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  opacity: 0.6;
}
.fm-seg {
  margin-bottom: var(--space-2);
}
.fm-check--off {
  opacity: var(--opacity-disabled);
}
</style>
