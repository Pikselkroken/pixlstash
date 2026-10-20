<template>
  <div class="wtp" role="tabpanel" aria-labelledby="wfv-tab-templates">
    <!-- Where the list comes from, said before anything else on the tab: none
         of it is the owner's own work until it is added. -->
    <div v-if="store.reachable && store.loaded" class="wtp-strip">
      <span class="wtp-source" data-testid="wtp-source">
        <ComfyuiIcon :size="14" />
        Downloaded from your ComfyUI at
        <span class="wtp-url">{{ store.comfyuiUrl }}</span>
      </span>
      <AppButton
        size="sm"
        variant="ghost"
        icon-left="refresh"
        icon-only
        tooltip="Read the templates from ComfyUI again"
        :loading="store.loading"
        data-testid="wtp-reread"
        @click="store.fetchTemplates({ refresh: true })"
      />
      <FilterStrip
        inline
        class="wtp-chips"
        :chips="store.filterChips"
        :of-label="store.filterOfLabel"
        :on-clear-all="store.clearFilters"
      />
    </div>

    <p v-if="store.error" class="wtp-note wtp-note--error" role="alert">
      {{ store.error }}
    </p>

    <!-- No ComfyUI answering: the tab is still a tab, and says where its list
         would come from. Mine is unaffected. -->
    <div
      v-if="store.loaded && !store.reachable"
      class="wtp-empty"
      data-testid="wtp-unreachable"
    >
      <v-icon size="40" class="wtp-empty__icon">mdi-lan-disconnect</v-icon>
      <h2 class="wtp-empty__title">Templates come from your ComfyUI</h2>
      <p class="wtp-empty__lead">
        Nothing is answering at
        <span class="wtp-url">{{ store.comfyuiUrl }}</span>, so there is no
        list to show. Start ComfyUI and this fills in by itself.
      </p>
      <AppButton
        size="sm"
        variant="secondary"
        @click="emit('open-settings', 'comfyui')"
        >ComfyUI settings…</AppButton
      >
    </div>

    <p
      v-else-if="store.loaded && !store.sortedTemplates.length"
      class="wtp-note"
    >
      {{
        store.templates.length
          ? "No template matches these filters."
          : "Your ComfyUI serves no templates."
      }}
    </p>

    <div
      v-if="store.reachable"
      ref="scrollEl"
      class="wtp-scroll"
      tabindex="-1"
      @keydown.self="onKeyDown"
      @click.self="store.clearSelection()"
    >
      <div
        ref="gridEl"
        class="wtp-grid"
        role="grid"
        aria-label="ComfyUI templates"
        aria-multiselectable="true"
        :aria-busy="store.loading"
        :style="{ '--wf-columns': columns, '--wf-gap': `${columnGap}px` }"
        @keydown="onKeyDown"
      >
        <div
          v-for="(template, index) in store.sortedTemplates"
          :key="template.key"
          class="wtp-row"
          role="row"
          :aria-selected="selected(template.key)"
          :tabindex="index === cursorIndex ? 0 : -1"
          :data-key="template.key"
          @click="onRowClick(index, $event)"
        >
          <div class="wtp-cell" role="gridcell">
            <WorkflowTemplateCard
              :template="template"
              :selected="selected(template.key)"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- The selection's one verb, on the pill every grid in the app docks. -->
    <div class="selbar-float">
      <div
        v-if="store.selectedKeys.length"
        class="selbar wtp-selbar"
        role="toolbar"
        aria-label="Selected templates"
      >
        <span class="selbar-count" data-testid="wtp-count">{{
          countLabel
        }}</span>
        <span class="selbar-sep"></span>
        <AppButton
          size="sm"
          variant="primary"
          icon-left="plus"
          :loading="store.adding"
          data-testid="wtp-add"
          @click="emit('add')"
          >{{ addLabel }}</AppButton
        >
        <AppBarButton
          shape="round"
          icon="close"
          aria-label="Clear selection"
          tooltip="Clear selection"
          @click="store.clearSelection()"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The Workflows view's Templates tab (#2000, design B): the templates the
 * owner's ComfyUI serves, drawn as cards on the Mine grid's columns, with the
 * same click, Ctrl, Shift and keyboard selection, and one verb on the pill:
 * Add, which the view carries out (`add`), since it lands on the Mine tab.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import WorkflowTemplateCard from "../widgets/WorkflowTemplateCard.vue";
import FilterStrip from "./FilterStrip.vue";

const props = defineProps({
  // The Mine grid's two numbers (`WorkflowsView.vue`), so both tabs lay out
  // the same columns from the same arithmetic.
  columnMin: { type: Number, required: true },
  columnGap: { type: Number, required: true },
});

const emit = defineEmits(["add", "open-settings"]);

const store = useWorkflowTemplatesStore();
const gridEl = ref(null);
const scrollEl = ref(null);
const columns = ref(1);
// The cursor is a key, not an index, for the Mine grid's reason: a resort or
// a re-read rebuilds the list under it.
const cursorKey = ref("");

const cursorIndex = computed(() => {
  const at = store.sortedTemplates.findIndex((t) => t.key === cursorKey.value);
  return at >= 0 ? at : 0;
});

function selected(key) {
  return store.selectedKeys.includes(key);
}

const countLabel = computed(() => {
  const n = store.selectedKeys.length;
  return n === 1 ? "1 template" : `${n} templates`;
});
const addLabel = computed(() => {
  const n = store.selectedKeys.length;
  return n === 1 ? "Add to my workflows" : `Add ${n} to my workflows`;
});

function measure() {
  const width = gridEl.value?.clientWidth ?? 0;
  columns.value = Math.max(
    1,
    Math.floor((width + props.columnGap) / (props.columnMin + props.columnGap)),
  );
}

let observer = null;
watch(gridEl, (element) => {
  observer?.disconnect();
  observer = null;
  if (!element || typeof ResizeObserver === "undefined") return;
  observer = new ResizeObserver(measure);
  observer.observe(element);
  measure();
});
onBeforeUnmount(() => observer?.disconnect());

function onRowClick(index, event) {
  const template = store.sortedTemplates[index];
  if (!template) return;
  cursorKey.value = template.key;
  if (event?.shiftKey) {
    const anchor = store.sortedTemplates.findIndex((t) => selected(t.key));
    const from = anchor < 0 ? index : anchor;
    const [start, end] = from <= index ? [from, index] : [index, from];
    store.selectRange(
      store.sortedTemplates.slice(start, end + 1).map((t) => t.key),
    );
    return;
  }
  store.select(template.key, { additive: event?.ctrlKey || event?.metaKey });
}

function moveCursor(index) {
  const list = store.sortedTemplates;
  if (!list.length) return;
  const template = list[Math.min(Math.max(index, 0), list.length - 1)];
  cursorKey.value = template.key;
  nextTick(() =>
    Array.from(gridEl.value?.querySelectorAll(".wtp-row") ?? [])
      .find((element) => element.dataset.key === template.key)
      ?.focus(),
  );
}

function onKeyDown(event) {
  const template = store.sortedTemplates[cursorIndex.value];
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
    event.preventDefault();
    store.selectRange(store.sortedTemplates.map((t) => t.key));
    return;
  }
  const step = {
    ArrowRight: 1,
    ArrowLeft: -1,
    ArrowDown: columns.value,
    ArrowUp: -columns.value,
  }[event.key];
  if (step) {
    event.preventDefault();
    moveCursor(cursorIndex.value + step);
    return;
  }
  if (event.key === " " && template) {
    event.preventDefault();
    store.select(template.key, { additive: true });
  } else if (event.key === "Escape" && store.selectedKeys.length) {
    event.preventDefault();
    store.clearSelection();
  }
}
</script>

<style scoped>
.wtp {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}

/* The source line leads, the filter chips follow on the same row. */
.wtp-strip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--toolbar-height);
  padding: 0 var(--space-5);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wtp-source {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex: none;
  white-space: nowrap;
}

.wtp-url {
  font-family: var(--font-mono);
}

.wtp-chips {
  flex: 1;
  min-width: 0;
  border-bottom: none;
  background: transparent;
}

.wtp-note {
  margin: 0;
  padding: var(--space-3) var(--space-5);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-sm);
}

.wtp-note--error {
  color: rgb(var(--v-theme-error));
}

/* The Mine grid's scroller and track (`WorkflowsView.vue`), so the two tabs
   sit on one set of columns. */
.wtp-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: var(--space-3);
}

.wtp-scroll:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring-inset);
}

.wtp-grid {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  align-content: start;
  gap: var(--wf-gap);
}

.wtp-cell {
  border-radius: var(--radius-md);
}

/* The view's empty state, without the art: a fact about a connection, not a
   first run. */
.wtp-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-6);
  text-align: center;
}

.wtp-empty__icon {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wtp-empty__title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.wtp-empty__lead {
  margin: 0;
  max-width: 46ch;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.wtp-selbar {
  gap: var(--space-2);
}
</style>
