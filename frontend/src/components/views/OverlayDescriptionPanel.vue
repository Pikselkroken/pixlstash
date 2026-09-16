<template>
  <div
    class="sidebar-section sidebar-section--description"
    :class="{ 'sidebar-section--collapsed': descriptionCollapsed }"
  >
    <div
      class="section-header section-header--collapsible section-label section-label--on-dark"
      @click="
        descriptionCollapsed = !descriptionCollapsed;
        descriptionCollapsed && cancelEditDescription();
      "
    >
      <!-- Description and Text share this header as label tabs, one panel on
           screen at a time. The Text tab only exists once the reader found
           words (or is reading them): a picture without text keeps the plain
           Description header, with no empty state. -->
      <div
        v-if="showTabs"
        class="section-tabs"
        role="tablist"
        aria-label="Description or text in picture"
      >
        <button
          :id="descTabId"
          ref="descTabRef"
          class="section-tab"
          type="button"
          role="tab"
          :aria-selected="!isTextTab"
          :aria-controls="descPanelId"
          :tabindex="isTextTab ? -1 : 0"
          @click.stop="pickTab(DESCRIPTION_TAB)"
          @keydown="onTabKeydown"
        >
          Description
        </button>
        <button
          :id="textTabId"
          ref="textTabRef"
          class="section-tab"
          type="button"
          role="tab"
          :aria-selected="isTextTab"
          :aria-controls="textPanelId"
          :tabindex="isTextTab ? 0 : -1"
          :disabled="textState !== 'read'"
          @click.stop="pickTab(TEXT_TAB)"
          @keydown="onTabKeydown"
        >
          Text
          <template v-if="textState === 'pending'">
            <v-icon size="12" class="mdi-spin" aria-hidden="true"
              >mdi-loading</v-icon
            >
            <span class="visually-hidden">, reading</span>
          </template>
        </button>
      </div>
      <span v-else>Description</span>
      <span v-if="isTextTab" class="section-meta-group">
        <button
          v-if="props.image && !readOnly"
          class="section-meta-btn"
          type="button"
          aria-label="Read the text again"
          :disabled="textReadBusy"
          @click.stop="emit('read-text-again')"
        >
          <Tooltip
            text="Read the text again"
            activator="parent"
            :describe="false"
          />
          <v-icon size="16" :class="{ 'mdi-spin': textReadBusy }">
            {{ textReadBusy ? "mdi-loading" : "mdi-refresh" }}
          </v-icon>
        </button>
        <button
          class="section-meta-btn"
          type="button"
          aria-label="Copy text"
          @click.stop="copyPictureText(fullText, 'text')"
        >
          <Tooltip text="Copy text" activator="parent" :describe="false" />
          <v-icon size="16">
            {{ textCopyState === "text" ? "mdi-check-bold" : "mdi-content-copy" }}
          </v-icon>
        </button>
        <span class="section-meta">{{ fullText.length }}</span>
        <v-icon size="16" style="opacity: 0.6">{{
          descriptionCollapsed ? "mdi-chevron-right" : "mdi-chevron-down"
        }}</v-icon>
      </span>
      <span v-else class="section-meta-group">
        <button
          v-if="props.image && !readOnly"
          class="section-meta-btn"
          type="button"
          aria-label="Regenerate description - deletes the current description and requeues it for captioning"
          :disabled="isDescriptionRefreshing"
          @click.stop="refreshDescription()"
        >
          <Tooltip
            text="Regenerate description - deletes the current description and requeues it for captioning"
            activator="parent"
            :describe="false"
          />
          <v-icon size="16" :class="{ 'mdi-spin': isDescriptionRefreshing }">
            {{ isDescriptionRefreshing ? "mdi-loading" : "mdi-refresh" }}
          </v-icon>
        </button>
        <v-menu
          v-if="props.image && !readOnly"
          v-model="descPluginMenuOpen"
          :close-on-content-click="true"
          location="bottom end"
        >
          <template #activator="{ props: menuProps }">
            <button
              class="section-meta-btn section-meta-btn--with-chevron"
              type="button"
              aria-label="Regenerate description with a specific model..."
              :disabled="isDescriptionRefreshing"
              v-bind="menuProps"
              @click.stop="fetchDescPlugins"
            >
              <Tooltip
                text="Regenerate description with a specific model..."
                activator="parent"
                :describe="false"
              />
              <v-icon size="14">mdi-refresh</v-icon>
              <v-icon size="10">mdi-chevron-down</v-icon>
            </button>
          </template>
          <div
            class="ctx-menu ctx-menu--on-dark"
            role="menu"
            tabindex="-1"
            style="min-width: 160px"
            @keydown="onMenuKeydown"
          >
            <button
              v-if="descPluginsLoading"
              type="button"
              class="ctx-item"
              role="menuitem"
              disabled
            >
              Loading...
            </button>
            <template v-else>
              <button
                v-for="plugin in descPlugins"
                :key="plugin.name"
                type="button"
                class="ctx-item"
                role="menuitem"
                @click="refreshDescription(plugin.name)"
              >
                {{ plugin.display_name || plugin.name }}
              </button>
              <button
                v-if="!descPlugins.length"
                type="button"
                class="ctx-item"
                role="menuitem"
                disabled
              >
                No description models available
              </button>
            </template>
          </div>
        </v-menu>
        <button
          class="section-meta-btn"
          type="button"
          aria-label="Copy description"
          :disabled="!canCopyDescription"
          @click.stop="copyDescription"
        >
          <Tooltip text="Copy description" activator="parent" :describe="false" />
          <v-icon size="16">
            {{
              descriptionCopyState === "copied"
                ? "mdi-check-bold"
                : "mdi-content-copy"
            }}
          </v-icon>
        </button>
        <span v-if="!isSentinelDescription" class="section-meta">
          {{ descriptionDraft.length }}
        </span>
        <v-icon size="16" style="opacity: 0.6">{{
          descriptionCollapsed ? "mdi-chevron-right" : "mdi-chevron-down"
        }}</v-icon>
      </span>
    </div>
    <template v-if="!descriptionCollapsed && isTextTab">
      <!-- Read-only: the text is what the reader saw, with a box behind every
           word, so a misread is fixed by reading again, not by typing. -->
      <div
        :id="textPanelId"
        ref="textFieldRef"
        class="picture-text"
        role="tabpanel"
        :aria-labelledby="textTabId"
      >
        <div
          v-for="(line, lineIdx) in wordLines"
          :key="lineIdx"
          class="picture-text-line"
        >
          <button
            v-for="word in line"
            :key="word.index"
            type="button"
            class="picture-text-word"
            :class="{
              'picture-text-word--match': word.matched,
              'picture-text-word--selected': selectedSet.has(word.index),
            }"
            :aria-pressed="selectedSet.has(word.index)"
            :data-word="word.index"
            @click.stop="emit('select-word', word.index, $event.shiftKey)"
          >
            {{ word.text }}
          </button>
        </div>
      </div>
      <div class="picture-text-selection" aria-live="polite">
        <template v-if="selectedText">
          <span class="picture-text-selection-words">{{ selectedText }}</span>
          <button
            class="picture-text-mini"
            type="button"
            @click.stop="copyPictureText(selectedText, 'selection')"
          >
            {{ textCopyState === "selection" ? "Copied" : "Copy" }}
          </button>
          <button
            class="picture-text-mini"
            type="button"
            @click.stop="emit('clear-word-selection')"
          >
            Clear
          </button>
        </template>
        <span v-else
          >Click a word to find it in the picture. Shift-click to add
          words.</span
        >
      </div>
    </template>
    <template v-else-if="!descriptionCollapsed">
      <div v-if="locked && lockNote" class="overlay-lock-note">
        <Tooltip :text="lockNote" activator="parent" />
        <v-icon size="12">mdi-lock-outline</v-icon>
        <span>Locked - read-only. Unlock the set to edit.</span>
      </div>
      <div
        :id="showTabs ? descPanelId : undefined"
        class="description-editor"
        :class="{ 'description-editor--sentinel': isSentinelDescription }"
        :role="showTabs ? 'tabpanel' : undefined"
        :aria-labelledby="showTabs ? descTabId : undefined"
      >
        <textarea
          ref="descriptionEditorRef"
          v-model="descriptionDraft"
          :readonly="!isEditingDescription || readOnly"
          @focus="!readOnly && startEditDescription()"
          @click="!readOnly && startEditDescription()"
          @keydown.enter.prevent="
            isEditingDescription && !$event.shiftKey && saveDescription()
          "
          @keydown="handleDescriptionEditorKey"
          @blur="cancelEditDescription"
        ></textarea>
        <div class="description-actions">
          <template v-if="isEditingDescription">
            <button
              class="overlay-icon-btn"
              type="button"
              aria-label="Save description"
              :disabled="isSavingDescription"
              @click.stop="saveDescription"
            >
              <Tooltip
                text="Save description"
                activator="parent"
                :describe="false"
              />
              <v-icon size="24" :class="{ 'mdi-spin': isSavingDescription }">
                {{ isSavingDescription ? "mdi-loading" : "mdi-content-save" }}
              </v-icon>
            </button>
            <button
              class="overlay-icon-btn"
              type="button"
              aria-label="Cancel editing"
              :disabled="isSavingDescription"
              @click.stop="cancelEditDescription"
            >
              <Tooltip
                text="Cancel editing"
                activator="parent"
                :describe="false"
              />
              <v-icon size="24">mdi-close</v-icon>
            </button>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onUnmounted, useId, watch } from "vue";
import { API_BASE_URL, isReadOnly } from "../../utils/apiClient";
import {
  patchPicture,
  resetPictureDescription,
} from "../../api/pictures";
import { listTaggers } from "../../api/taggers";
import { copyText } from "../../utils/clipboard";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import Tooltip from "../widgets/Tooltip.vue";
import {
  isDescriptionSentinel,
  formatDescriptionSentinel,
} from "../../utils/descriptions";
import {
  DESCRIPTION_TAB,
  TEXT_TAB,
  flattenWords,
  joinPictureText,
} from "../../composables/usePictureText";

// Failures report through the notice surface instead of a blocking native
// alert() (docs/design/notice-surface.md §1).
const noticeStore = useNoticeStore();

const props = defineProps({
  image: { type: Object, default: null },
  backendUrl: { type: String, default: () => API_BASE_URL },
  // True when the picture is frozen by a locked set: render read-only.
  locked: { type: Boolean, default: false },
  // Lock-reason tooltip copy (single source from useLockedSetsStore).
  lockNote: { type: String, default: "" },
  // The text found in the picture (usePictureText): "none" | "pending" | "read".
  textState: { type: String, default: "none" },
  textLines: { type: Array, default: () => [] },
  // The tab on screen, already resolved: "text" only while there is text.
  activeTab: { type: String, default: DESCRIPTION_TAB },
  selectedWords: { type: Array, default: () => [] },
  textReadBusy: { type: Boolean, default: false },
});

// Compose the app-wide read-only (token capability) with the data-state lock.
// The lock takes tooltip precedence, but for gating either one makes the panel
// read-only.
const readOnly = computed(() => isReadOnly.value || props.locked);

const emit = defineEmits([
  "update-description",
  "editing-finished",
  "pick-tab",
  "select-word",
  "clear-word-selection",
  "read-text-again",
]);

const descTabId = useId();
const textTabId = useId();
const descPanelId = useId();
const textPanelId = useId();
const descTabRef = ref(null);
const textTabRef = ref(null);
const textFieldRef = ref(null);

const words = computed(() => flattenWords(props.textLines));
const hasWords = computed(
  () => props.textState === "read" && words.value.length > 0,
);
// No tab for a picture without text: today's Description header, exactly.
const showTabs = computed(
  () => props.textState === "pending" || hasWords.value,
);
const isTextTab = computed(
  () => hasWords.value && props.activeTab === TEXT_TAB,
);
const wordLines = computed(() => {
  const lines = [];
  for (const word of words.value) (lines[word.line] ||= []).push(word);
  return lines.filter(Boolean);
});
const fullText = computed(() => joinPictureText(props.textLines));
const selectedSet = computed(() => new Set(props.selectedWords));
const selectedText = computed(() =>
  words.value
    .filter((w) => selectedSet.value.has(w.index))
    .map((w) => w.text)
    .join(" "),
);
// Which copy button last succeeded ("text" | "selection"), for its check mark.
const textCopyState = ref("");
let textCopyTimer = null;

function pickTab(tab) {
  if (tab === TEXT_TAB && !hasWords.value) return;
  if (tab === TEXT_TAB) cancelEditDescription();
  descriptionCollapsed.value = false;
  emit("pick-tab", tab);
}

/** Arrow keys move between the two tabs; a disabled Text tab is skipped. */
function onTabKeydown(event) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  event.stopPropagation();
  const next = isTextTab.value ? DESCRIPTION_TAB : TEXT_TAB;
  if (next === TEXT_TAB && !hasWords.value) return;
  pickTab(next);
  nextTick(() =>
    (next === TEXT_TAB ? textTabRef : descTabRef).value?.focus?.(),
  );
}

async function copyPictureText(value, which) {
  if (!value) return;
  if (await copyText(value)) {
    textCopyState.value = which;
    if (textCopyTimer) clearTimeout(textCopyTimer);
    textCopyTimer = window.setTimeout(() => {
      textCopyState.value = "";
      textCopyTimer = null;
    }, 2000);
  } else {
    noticeStore.error("Couldn't copy the text to the clipboard.", {
      key: "picture-text-copy",
    });
  }
}

/** Scroll a word's button into view (a box on the picture was clicked). */
function revealWord(index) {
  nextTick(() => {
    textFieldRef.value
      ?.querySelector?.(`[data-word="${index}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  });
}

const descriptionCollapsed = ref(false);
const isEditingDescription = ref(false);
const isSavingDescription = ref(false);
const descriptionDraft = ref(
  formatDescriptionSentinel(props.image?.description) || "",
);
const isSentinelDescription = computed(() =>
  isDescriptionSentinel(props.image?.description),
);
const descriptionEditorRef = ref(null);
const descriptionCopyState = ref("idle");
const isDescriptionRefreshing = ref(false);
const descPluginMenuOpen = ref(false);
const descPlugins = ref([]);
const descPluginsLoading = ref(false);
let copyResetTimer = null;

watch(
  () => props.image?.description,
  (desc) => {
    if (!isEditingDescription.value) {
      descriptionDraft.value = formatDescriptionSentinel(desc) || "";
    }
  },
);

const canCopyDescription = computed(() => {
  if (isSentinelDescription.value) return false;
  const source = isEditingDescription.value
    ? descriptionDraft.value
    : props.image?.description;
  return !!(source && source.length);
});

function startEditDescription() {
  if (!props.image || isSentinelDescription.value) return;
  descriptionDraft.value = props.image?.description || "";
  isEditingDescription.value = true;
  nextTick(() => {
    if (descriptionEditorRef.value) {
      descriptionEditorRef.value.focus();
    }
  });
}

function cancelEditDescription() {
  isEditingDescription.value = false;
  isSavingDescription.value = false;
  descriptionDraft.value =
    formatDescriptionSentinel(props.image?.description) || "";
  // Editing is over, so the keyboard must leave the field: a textarea that
  // keeps DOM focus after Escape still reads as a typing target, and the
  // overlay's Ctrl+Z (and every other shortcut) stays dead until a click.
  descriptionEditorRef.value?.blur?.();
  emit("editing-finished");
}

async function saveDescription() {
  if (!props.image || isSavingDescription.value) return;
  isSavingDescription.value = true;
  const capturedImageId = props.image.id;
  const newDescription = descriptionDraft.value.trim();
  const payload = { description: newDescription || null };
  try {
    await patchPicture(capturedImageId, payload);
    emit("update-description", capturedImageId, newDescription);
    isEditingDescription.value = false;
    // Same contract as cancel: a save ends the edit, so the keyboard goes
    // back to the overlay (the parent refocuses its canvas on this signal).
    descriptionEditorRef.value?.blur?.();
    emit("editing-finished");
  } catch (err) {
    console.error("Failed to update description", err);
    noticeStore.error(
      `Couldn't save the description. ${errorDetail(err) || err?.message || "Please try again."}`,
      { key: "description-save" },
    );
  } finally {
    isSavingDescription.value = false;
  }
}

function resetCopyState() {
  if (copyResetTimer) {
    clearTimeout(copyResetTimer);
    copyResetTimer = null;
  }
  descriptionCopyState.value = "idle";
}

async function copyDescription() {
  const text = isEditingDescription.value
    ? descriptionDraft.value
    : props.image?.description;
  if (!text) return;
  const copied = await copyText(text);
  if (copied) {
    descriptionCopyState.value = "copied";
    if (copyResetTimer) clearTimeout(copyResetTimer);
    copyResetTimer = window.setTimeout(() => {
      resetCopyState();
    }, 2000);
  } else {
    noticeStore.error("Couldn't copy the description to the clipboard.", {
      key: "description-copy",
    });
  }
}

async function fetchDescPlugins() {
  if (descPluginsLoading.value || descPlugins.value.length) return;
  descPluginsLoading.value = true;
  try {
    const body = await listTaggers();
    descPlugins.value = (body?.plugins ?? []).filter(
      (p) => p.supports_descriptions,
    );
  } catch {
    descPlugins.value = [];
  } finally {
    descPluginsLoading.value = false;
  }
}

async function refreshDescription(model = null) {
  if (!props.image?.id || !props.backendUrl || isDescriptionRefreshing.value)
    return;
  isDescriptionRefreshing.value = true;
  const capturedImageId = props.image.id;
  try {
    if (model) {
      await resetPictureDescription(
        capturedImageId,
        { model },
      );
    } else {
      await patchPicture(
        capturedImageId,
        { description: null },
      );
    }
    emit("update-description", capturedImageId, null);
    cancelEditDescription();
  } catch (err) {
    console.error("Failed to reset description", err);
    noticeStore.error(
      `Couldn't reset the description. ${errorDetail(err) || err?.message || "Please try again."}`,
      { key: "description-reset" },
    );
  } finally {
    isDescriptionRefreshing.value = false;
  }
}

function handleDescriptionEditorKey(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    cancelEditDescription();
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    saveDescription();
  }
}

onUnmounted(() => {
  if (textCopyTimer) clearTimeout(textCopyTimer);
});

defineExpose({
  isEditingDescription,
  cancelEditDescription,
  startEditDescription,
  resetCopyState,
  revealWord,
});
</script>

<style scoped>
.sidebar-section {
  margin-bottom: 6px;
}

.sidebar-section--description {
  flex: 1 1 114px;
  display: flex;
  flex-direction: column;
  min-height: 114px;
  overflow: visible;
}

.sidebar-section--description.sidebar-section--collapsed {
  flex: 0 0 auto;
  min-height: 0;
  overflow: hidden;
}

.section-header--collapsible {
  cursor: pointer;
  user-select: none;
}

.section-header--collapsible:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}

/* Type and ink come from the shared `.section-label`; this is layout only. */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
  padding: var(--space-1) 0;
}

.section-meta-group {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

.section-meta-btn {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
  padding: var(--space-1);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.section-meta-btn:hover:not(:disabled) {
  color: rgb(var(--v-theme-on-dark-surface));
}

.section-meta-btn:disabled {
  cursor: default;
  opacity: 0.5;
}

.section-meta-btn--with-chevron {
  gap: 1px;
}

.section-meta {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.overlay-lock-note {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.description-editor {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.description-editor textarea {
  flex: 1;
  width: 100%;
  min-height: 56px;
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  padding: 6px;
  resize: vertical;
  /* Same bar as the sidebar's other scroll regions (the two tag lists and the
     faces grid). Left on the browser default this is the loudest thing in the
     panel: a full-width light track with stepper arrows on a dark surface. */
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.4) transparent;
}

.description-editor textarea:hover {
  scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.55) transparent;
}

.description-actions {
  margin-top: 6px;
  display: flex;
  gap: var(--space-3);
}

.overlay-icon-btn {
  color: rgb(var(--v-theme-on-dark-surface));
  height: var(--control-h-bar);
  padding: 6px 14px;
  min-width: var(--control-h-bar);
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1em;
}

.overlay-icon-btn:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}

/* ── Description / Text tabs ─────────────────────────────────────────────
   Label tabs in the section label's own type (inherited from `.section-label`
   on the header): the selected one takes full ink and the olive bar. */
.section-tabs {
  display: flex;
  align-self: stretch;
  gap: var(--space-5);
}

.section-tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font: inherit;
  letter-spacing: inherit;
  text-transform: inherit;
  color: inherit;
  border-bottom: 2px solid transparent;
  padding-top: var(--space-1);
}

.section-tab:hover:not(:disabled) {
  color: rgb(var(--v-theme-on-dark-surface));
}

.section-tab[aria-selected="true"] {
  color: rgb(var(--v-theme-on-dark-surface));
  border-bottom-color: rgb(var(--v-theme-dark-surface-primary));
}

/* Pending is not "not allowed": the spinner carries the state, so the label
   stays legible rather than taking the disabled fade (visual-language §11). */
.section-tab:disabled {
  cursor: default;
}

/* ── Text panel ── */
.picture-text {
  flex: 1;
  min-height: 56px;
  overflow: auto;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  padding: var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.4) transparent;
}

.picture-text:hover {
  scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.55) transparent;
}

.picture-text-line {
  display: flex;
  flex-wrap: wrap;
  column-gap: 0.6ch;
  padding: var(--space-1) 0;
}

.picture-text-word {
  font: inherit;
  color: inherit;
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
  box-shadow: inset 0 0 0 1px transparent;
}

.picture-text-word:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}

/* Matched by the search: underlined. Selected: the olive wash and ring. The
   two never share a mark, so a matched word can also be selected. */
.picture-text-word--match {
  text-decoration: underline dotted;
  text-underline-offset: 3px;
  text-decoration-thickness: 1.5px;
}

.picture-text-word--selected {
  background: rgba(var(--v-theme-dark-surface-primary), 0.28);
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-dark-surface-primary));
}

.picture-text-selection {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: 28px;
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(
    var(--v-theme-on-dark-surface),
    var(--opacity-text-secondary)
  );
}

.picture-text-selection-words {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-dark-surface));
}

.picture-text-mini {
  display: inline-flex;
  align-items: center;
  flex: none;
  height: 24px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  color: rgb(var(--v-theme-on-dark-surface));
}

.picture-text-mini:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}

.description-editor--sentinel textarea {
  font-style: italic;
  opacity: var(--opacity-text-secondary);
  cursor: default;
}
</style>
