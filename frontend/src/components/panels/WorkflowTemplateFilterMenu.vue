<template>
  <div class="tbm wtf" role="group" aria-label="Template filters">
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
      />
    </div>

    <div v-for="section in sections" :key="section.key" class="tbm-section">
      <span class="tbm-label">{{ section.label }}</span>
      <OptionRows
        :options="section.options"
        :model-value="store.filters[section.key]"
        :aria-label="section.label"
        @update:model-value="(id) => store.setFilters({ [section.key]: id })"
      />
    </div>
  </div>
</template>

<script setup>
/**
 * The Templates tab's Filters menu (#2000). Flat, unlike the Mine tab's
 * cascade (`WorkflowFilterMenu`): four short pick-one lists fit one popover,
 * and every filter here is the client's, over a list already in memory.
 *
 * Partner API templates are hidden by a default the strip shows as a chip
 * with its count, so they are never left out silently; the last list here is
 * the same switch.
 */
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import {
  READINESS,
  READINESS_LABELS,
  SOURCE_LABELS,
  useWorkflowTemplatesStore,
} from "../../stores/useWorkflowTemplatesStore";
import NameFilterField from "../widgets/NameFilterField.vue";
import OptionRows from "../widgets/OptionRows.vue";

const props = defineProps({
  // Bumped by the view to put focus in the name field.
  focusNameRequest: { type: Number, default: 0 },
});

const store = useWorkflowTemplatesStore();
const nameRef = ref(null);

watch(
  () => props.focusNameRequest,
  () => nameRef.value?.focus(),
);

const ANY = { id: null, label: "Any" };

function mediaLabel(type) {
  return type === "3d" ? "3D" : type.charAt(0).toUpperCase() + type.slice(1);
}

const sections = computed(() => [
  {
    key: "media",
    label: "Media",
    options: [ANY, ...store.mediaTypes.map((id) => ({ id, label: mediaLabel(id) }))],
  },
  {
    key: "source",
    label: "Source",
    options: [
      ANY,
      ...Object.entries(SOURCE_LABELS).map(([id, label]) => ({ id, label })),
    ],
  },
  {
    key: "readiness",
    label: "Readiness",
    options: [
      ANY,
      ...READINESS.filter((id) => id !== "partner").map((id) => ({
        id,
        label: READINESS_LABELS[id],
      })),
    ],
  },
  {
    key: "showPartner",
    label: "Partner API templates",
    options: [
      { id: false, label: `Hidden (${store.partnerCount})` },
      { id: true, label: "Shown" },
    ],
  },
]);
</script>

<style scoped>
.wtf {
  width: var(--filter-menu-w);
  max-width: 94vw;
  max-height: 70vh;
  overflow-y: auto;
}

.wtf-of {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
