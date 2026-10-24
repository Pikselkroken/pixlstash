<template>
  <div class="wtp" role="tabpanel" aria-labelledby="wfv-tab-templates">
    <!-- Where the list comes from, first on the strip: none of it is the
         owner's own work until it is added. A ComfyUI mark with a connected
         dot, so the strip keeps its room for the chips; the address and the
         re-read are one hover and one click away. -->
    <div v-if="store.reachable && store.loaded" class="wtp-strip">
      <v-menu location="bottom start" :close-on-content-click="false">
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            type="button"
            class="wtp-link"
            :aria-label="`Templates from your ComfyUI at ${store.comfyuiUrl}`"
            data-testid="wtp-source"
          >
            <ComfyuiIcon :size="16" />
            <span class="wtp-link-dot" aria-hidden="true"></span>
            <Tooltip
              :text="`Templates from your ComfyUI at ${store.comfyuiUrl}`"
              activator="parent"
              :describe="false"
            />
          </button>
        </template>
        <div class="tbm wtp-link-menu" data-testid="wtp-link-menu">
          <div class="tbm-header">
            <ComfyuiIcon :size="16" />
            <span class="tbm-title">Connected to ComfyUI</span>
          </div>
          <div class="tbm-section">
            <p class="wtp-link-text">
              Templates are read from your ComfyUI at
              <span class="wtp-url">{{ store.comfyuiUrl }}</span>
            </p>
            <AppButton
              size="sm"
              variant="secondary"
              icon-left="refresh"
              :loading="store.loading"
              data-testid="wtp-reread"
              @click="store.fetchTemplates({ refresh: true })"
              >Read the templates again</AppButton
            >
          </div>
        </div>
      </v-menu>
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
          @contextmenu.prevent="onRowMenu(index, $event)"
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

    <!-- The card menu: right-click, the Menu key or Shift+F10 on a template.
         Everything the pill does, and the download of what they lack. -->
    <v-menu
      v-model="contextOpen"
      :target="contextAt"
      location="bottom end"
      origin="top start"
      :offset="2"
    >
      <div
        class="ctx-menu"
        role="menu"
        tabindex="-1"
        data-testid="wtp-menu"
        @keydown="onMenuKeydown"
      >
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          data-verb="add"
          @click="verb(() => emit('add'))"
        >
          <v-icon class="ctx-icon">mdi-plus</v-icon>
          <span class="ctx-label-text">{{ addLabel }}</span>
        </button>
        <!-- `aria-disabled`, not `disabled`, so the reason stays one hover
             away (`WorkflowSelectionBar`'s rule). -->
        <button
          class="ctx-item"
          :class="{ 'ctx-item--disabled': !downloadFiles.length }"
          type="button"
          role="menuitem"
          data-verb="download-missing"
          :aria-disabled="downloadFiles.length ? undefined : 'true'"
          @click="downloadFiles.length && verb(downloadMissing)"
        >
          <Tooltip :text="downloadTitle" activator="parent" />
          <v-icon class="ctx-icon">mdi-download-outline</v-icon>
          <span class="ctx-label-text">{{ downloadLabel }}</span>
        </button>
        <div class="ctx-sep"></div>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          data-verb="clear"
          @click="verb(() => store.clearSelection())"
        >
          <v-icon class="ctx-icon">mdi-close</v-icon>
          <span class="ctx-label-text">Clear selection</span>
          <span class="ctx-shortcut">Esc</span>
        </button>
      </div>
    </v-menu>
  </div>
</template>

<script setup>
/**
 * The Workflows view's Templates tab (#2000, design B): the templates the
 * owner's ComfyUI serves, drawn as cards on the Mine grid's columns, with the
 * same click, Ctrl, Shift and keyboard selection, and one verb on the pill:
 * Add, which the view carries out (`add`), since it lands on the Mine tab.
 * The card menu has Add too, and downloads the files the selection lacks.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import { useContextMenuAt } from "../../composables/useContextMenuAt";
import { downloadModels } from "../../composables/useModelDownloads";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import WorkflowTemplateCard from "../widgets/WorkflowTemplateCard.vue";
import Tooltip from "../widgets/Tooltip.vue";
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

const { contextOpen, contextAt, openContextMenu } = useContextMenuAt();

/** The selection's missing files PixlStash knows where to get. */
const downloadFiles = computed(() => [
  ...new Set(
    store.selectedTemplates.flatMap((template) =>
      (template.files ?? [])
        .filter((file) => file.downloadable)
        .map((file) => file.name),
    ),
  ),
]);
const downloadLabel = computed(() => {
  const n = downloadFiles.value.length;
  if (!n) return "Download missing models";
  return n === 1 ? "Download 1 missing model" : `Download ${n} missing models`;
});
const downloadTitle = computed(() => {
  if (downloadFiles.value.length) return downloadFiles.value.join(", ");
  const lacking = store.selectedTemplates.some((t) =>
    (t.files ?? []).some((file) => !file.present),
  );
  return lacking
    ? "PixlStash does not know where to get the files missing from your shelf"
    : "Every file they load is on your shelf";
});

function downloadMissing() {
  void downloadModels(downloadFiles.value);
}

/** Shut the menu, then do what its row says. */
function verb(run) {
  contextOpen.value = false;
  run();
}

/**
 * Right-click a card: the file-manager rule, as on Mine. One that is not
 * selected becomes the selection; one that is keeps the selection whole.
 */
function onRowMenu(index, event) {
  const template = store.sortedTemplates[index];
  if (!template) return;
  cursorKey.value = template.key;
  if (!selected(template.key)) store.select(template.key);
  openContextMenu(event.clientX, event.clientY, event.currentTarget);
}

/** The Menu key and Shift+F10: the menu under the cursor card. */
function openMenuAtCursor() {
  const template = store.sortedTemplates[cursorIndex.value];
  if (!template) return;
  if (!selected(template.key)) store.select(template.key);
  const row = Array.from(gridEl.value?.querySelectorAll(".wtp-row") ?? []).find(
    (element) => element.dataset.key === template.key,
  );
  const box = row?.getBoundingClientRect();
  openContextMenu(box ? box.left + 24 : 0, box ? box.bottom : 0, row);
}

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

// While the tab is open and ComfyUI did not answer, ask again every 10 s, so
// starting ComfyUI after PixlStash fills the tab without a click.
const UNREACHABLE_POLL_MS = 10_000;
const poll = setInterval(() => {
  if (store.loaded && !store.reachable) store.fetchTemplates();
}, UNREACHABLE_POLL_MS);
onBeforeUnmount(() => clearInterval(poll));

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
  if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
    event.preventDefault();
    openMenuAtCursor();
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

.wtp-link {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wtp-link:hover {
  background: var(--hover-wash);
}

.wtp-link:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

/* The connected dot: the attention dot's size, in the success hue, ringed in
   the strip's ground so it reads apart from the mark. */
.wtp-link-dot {
  position: absolute;
  right: var(--space-1);
  bottom: var(--space-1);
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-success));
  box-shadow: 0 0 0 2px rgb(var(--v-theme-background));
}

.wtp-link-menu {
  width: var(--filter-menu-w);
  max-width: 94vw;
}

.wtp-link-text {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
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
