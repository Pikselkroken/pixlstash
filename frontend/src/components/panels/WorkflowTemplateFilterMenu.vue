<template>
  <div class="wtf" @keydown.left.capture="onLeft">
    <div
      ref="rootRef"
      class="tbm wtf-root"
      role="group"
      aria-label="Template filters"
    >
      <span class="tbm-caret tbm-caret--start"></span>
      <div class="tbm-header">
        <v-icon size="18" class="tbm-header-icon">mdi-filter</v-icon>
        <span class="tbm-title">Filters</span>
        <span class="tbm-spacer"></span>
        <span class="wtf-of">{{ store.filterOfLabel }}</span>
        <button
          class="tbm-ghost"
          type="button"
          :disabled="!store.activeFilterCount && !store.filters.showPartner"
          @click="store.clearFilters()"
        >
          Clear all
        </button>
      </div>

      <!-- The toolbar's name field folds on a narrow bar; this one is always here. -->
      <div class="tbm-section">
        <NameFilterField
          ref="nameRef"
          :model-value="store.filters.name"
          placeholder="Filter templates…"
          label="Filter templates by title, tag or model"
          @update:model-value="(name) => store.setFilters({ name })"
          @keydown.down.prevent="rowRefs[ROW_ORDER[0]]?.focus()"
        />
      </div>

      <div v-for="section in SECTIONS" :key="section.label" class="tbm-section">
        <span class="tbm-label">{{ section.label }}</span>
        <button
          v-for="kind in section.kinds"
          :key="kind"
          :ref="(el) => (rowRefs[kind] = el)"
          class="fm-row"
          type="button"
          :data-kind="kind"
          :aria-expanded="sub === kind ? 'true' : 'false'"
          :aria-controls="sub === kind ? submenuId : undefined"
          @click="toggle(kind)"
          @keydown.right.prevent="openKind(kind)"
          @keydown.enter.prevent="openKind(kind)"
          @keydown.down.prevent="stepRow(kind, 1)"
          @keydown.up.prevent="stepRow(kind, -1)"
        >
          <v-icon size="16" class="fm-row-lead">{{ KINDS[kind].icon }}</v-icon>
          <span class="fm-row-label">{{ KINDS[kind].label }}</span>
          <span class="fm-n">{{ rowValue(kind) }}</span>
          <v-icon size="16" class="fm-row-trail">mdi-chevron-right</v-icon>
        </button>
      </div>
    </div>

    <div
      v-if="sub"
      :id="submenuId"
      ref="subRef"
      class="wtf-sub-slot"
      :style="{ marginTop: `${subTop}px` }"
    >
      <div
        class="tbm fm-sub"
        role="group"
        :aria-label="KINDS[sub].label"
        :data-testid="`wtf-sub-${sub}`"
      >
        <div class="tbm-header">
          <span class="tbm-title">{{ KINDS[sub].label }}</span>
        </div>

        <div v-if="SEARCHED.includes(sub)" class="tbm-section">
          <div class="tbm-input-wrap">
            <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
            <input
              v-model="query"
              class="tbm-input tbm-input--with-icon"
              :placeholder="`Find a ${KINDS[sub].noun}…`"
              :aria-label="`Find a ${KINDS[sub].noun}`"
              autocomplete="off"
              @keydown.down.prevent="focusChosen"
            />
          </div>
        </div>

        <div class="tbm-section">
          <div :class="{ 'wtf-scroll': SEARCHED.includes(sub) }">
            <OptionRows
              :options="rows(sub)"
              :model-value="store.filters[sub]"
              :aria-label="KINDS[sub].label"
              @update:model-value="(id) => store.setFilters({ [sub]: id })"
            >
              <template #meta="{ option }">
                <span v-if="option.count != null" class="fm-n">{{
                  option.count
                }}</span>
              </template>
            </OptionRows>
          </div>
          <p
            v-if="SEARCHED.includes(sub) && query.trim() && rows(sub).length === 1"
            class="fm-help"
          >
            No {{ KINDS[sub].noun }} matches.
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The Templates tab's Filters menu (#2000): ComfyUI's own template filters
 * (its category list, Model, Use case, Runs on) and PixlStash's Readiness, on
 * the Mine tab's cascade (`WorkflowFilterMenu`): a root row per filter, each
 * opening a pick-one submenu beside it, the long ones with a search field.
 *
 * Partner API templates are hidden by a default the strip shows as a chip
 * with its count, so they are never left out silently; the last row here is
 * the same switch.
 */
import { computed, nextTick, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useCascadeMenu } from "../../composables/useCascadeMenu";
import {
  FILTER_KIND_LABELS,
  READINESS,
  READINESS_LABELS,
  useWorkflowTemplatesStore,
} from "../../stores/useWorkflowTemplatesStore";
import NameFilterField from "../widgets/NameFilterField.vue";
import OptionRows from "../widgets/OptionRows.vue";

const props = defineProps({
  // The v-menu keeps its content mounted, so a closed menu says so here and
  // reopens on the root alone.
  open: { type: Boolean, default: false },
  // Bumped by the view to put focus in the name field.
  focusNameRequest: { type: Number, default: 0 },
});

const store = useWorkflowTemplatesStore();

const KINDS = {
  category: {
    label: FILTER_KIND_LABELS.category,
    icon: "mdi-shape-outline",
    noun: "category",
  },
  // The model glyph is the model shelf's, as in the Mine tab's menu.
  model: {
    label: FILTER_KIND_LABELS.model,
    icon: "mdi-package-variant-closed",
    noun: "model",
  },
  useCase: {
    label: FILTER_KIND_LABELS.useCase,
    icon: "mdi-tag-outline",
    noun: "use case",
  },
  runsOn: { label: FILTER_KIND_LABELS.runsOn, icon: "mdi-server-outline" },
  readiness: {
    label: FILTER_KIND_LABELS.readiness,
    icon: "mdi-check-circle-outline",
  },
  showPartner: { label: "Partner API", icon: "mdi-crown" },
};

// ComfyUI's filters, then what only PixlStash can say: whether it runs here.
const SECTIONS = [
  { label: "Template", kinds: ["category", "model", "useCase", "runsOn"] },
  { label: "On this machine", kinds: ["readiness", "showPartner"] },
];
const ROW_ORDER = SECTIONS.flatMap((section) => section.kinds);
// The lists that grow with what ComfyUI serves.
const SEARCHED = ["category", "model", "useCase"];

const { sub, subTop, rootRef, subRef, rowRefs, openKind, toggle, onLeft } =
  useCascadeMenu(
    () => props.open,
    // The search field first, else the chosen radio; never the header.
    (submenu) => {
      const field = submenu?.querySelector("input");
      if (field) field.focus();
      else focusChosen();
    },
  );
const submenuId = "wtf-submenu";
const nameRef = ref(null);
// Immediate, as Mine's menu is: the search key's first ask is what opens the
// menu, and the menu renders this panel only then.
watch(
  () => props.focusNameRequest,
  (n) => n && nextTick(() => nameRef.value?.focus()),
  { immediate: true },
);
const query = ref("");
// A search typed for one list is not carried to the next opening.
watch(sub, () => {
  query.value = "";
});

const total = computed(() => store.templates.length);

function rowValue(kind) {
  if (kind === "showPartner") return store.filters.showPartner ? "Shown" : "";
  return store.filters[kind] == null ? "" : "1";
}

/**
 * One submenu's radio rows, led by the row that turns the filter off. The
 * chosen row stays whatever is typed: a radiogroup whose value is not among
 * its rows puts its tab stop on Any, and the next arrow would then clear the
 * filter the reader never touched.
 */
function rows(kind) {
  const any = { id: null, label: "Any", count: total.value };
  if (kind === "readiness") {
    return [
      any,
      ...READINESS.filter((id) => id !== "partner").map((id) => ({
        id,
        label: READINESS_LABELS[id],
      })),
    ];
  }
  if (kind === "showPartner") {
    return [
      { id: false, label: "Hidden", count: store.partnerCount },
      { id: true, label: "Shown", count: store.partnerCount },
    ];
  }
  const options = store.filterOptions[kind] ?? [];
  const q = query.value.trim().toLowerCase();
  const chosen = store.filters[kind];
  return [
    any,
    ...(q
      ? options.filter(
          (option) =>
            option.id === chosen || option.label.toLowerCase().includes(q),
        )
      : options),
  ];
}

function focusChosen() {
  subRef.value?.querySelector('[role="radio"][tabindex="0"]')?.focus();
}

function stepRow(kind, step) {
  const at = ROW_ORDER.indexOf(kind);
  const next = ROW_ORDER[(at + step + ROW_ORDER.length) % ROW_ORDER.length];
  rowRefs[next]?.focus();
}
</script>

<style scoped>
.wtf {
  display: flex;
  /* A phone has no room for two menus side by side: the submenu wraps below. */
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--space-2);
}

.wtf-root {
  width: var(--filter-menu-w);
  max-width: 94vw;
}

.wtf-of {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  white-space: nowrap;
}

.wtf-sub-slot {
  max-height: min(80vh, 760px);
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* The lists that grow with what ComfyUI serves: eight rows of the shared
   menu-row height, then they scroll. */
.wtf-scroll {
  max-height: calc(var(--control-h-bar) * 8);
  /* Room for a row's focus ring, which reaches --space-2 past it. */
  margin: calc(var(--space-2) * -1);
  padding: var(--space-2);
  overflow-y: auto;
  scrollbar-width: thin;
}
</style>
