<template>
  <div
    class="sidebar-section sidebar-section--description"
    :class="{ 'sidebar-section--collapsed': descriptionCollapsed }"
  >
    <div
      class="section-header section-header--collapsible"
      @click="
        descriptionCollapsed = !descriptionCollapsed;
        descriptionCollapsed && cancelEditDescription();
      "
    >
      <span>Description</span>
      <span class="section-meta-group">
        <button
          v-if="props.image && !readOnly"
          class="section-meta-btn"
          type="button"
          title="Regenerate description - deletes the current description and requeues it for captioning"
          :disabled="isDescriptionRefreshing"
          @click.stop="refreshDescription()"
        >
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
              title="Regenerate description with a specific model..."
              :disabled="isDescriptionRefreshing"
              v-bind="menuProps"
              @click.stop="fetchDescPlugins"
            >
              <v-icon size="14">mdi-refresh</v-icon>
              <v-icon size="10">mdi-chevron-down</v-icon>
            </button>
          </template>
          <v-list density="compact" min-width="160">
            <v-list-item
              v-if="descPluginsLoading"
              disabled
              title="Loading..."
            />
            <template v-if="!descPluginsLoading">
              <v-list-item
                v-for="plugin in descPlugins"
                :key="plugin.name"
                :title="plugin.display_name || plugin.name"
                @click="refreshDescription(plugin.name)"
              />
              <v-list-item
                v-if="!descPlugins.length"
                disabled
                title="No description models available"
              />
            </template>
          </v-list>
        </v-menu>
        <button
          class="section-meta-btn"
          type="button"
          title="Copy description"
          :disabled="!canCopyDescription"
          @click.stop="copyDescription"
        >
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
    <template v-if="!descriptionCollapsed">
      <div v-if="locked && lockNote" class="overlay-lock-note" :title="lockNote">
        <v-icon size="12">mdi-lock-outline</v-icon>
        <span>Locked - read-only. Unlock the set to edit.</span>
      </div>
      <div
        class="description-editor"
        :class="{ 'description-editor--sentinel': isSentinelDescription }"
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
              title="Save description"
              :disabled="isSavingDescription"
              @click.stop="saveDescription"
            >
              <v-icon size="18" :class="{ 'mdi-spin': isSavingDescription }">
                {{ isSavingDescription ? "mdi-loading" : "mdi-content-save" }}
              </v-icon>
            </button>
            <button
              class="overlay-icon-btn"
              type="button"
              title="Cancel editing"
              :disabled="isSavingDescription"
              @click.stop="cancelEditDescription"
            >
              <v-icon size="18">mdi-close</v-icon>
            </button>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch } from "vue";
import { API_BASE_URL, isReadOnly } from "../../utils/apiClient";
import {
  patchPicture,
  resetPictureDescription,
} from "../../api/pictures";
import { listTaggers } from "../../api/taggers";
import { copyText } from "../../utils/clipboard";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import {
  isDescriptionSentinel,
  formatDescriptionSentinel,
} from "../../utils/descriptions";

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
});

// Compose the app-wide read-only (token capability) with the data-state lock.
// The lock takes tooltip precedence, but for gating either one makes the panel
// read-only.
const readOnly = computed(() => isReadOnly.value || props.locked);

const emit = defineEmits(["update-description", "editing-finished"]);

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

defineExpose({
  isEditingDescription,
  cancelEditDescription,
  startEditDescription,
  resetCopyState,
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
  opacity: 0.85;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: var(--space-2);
  padding: var(--space-1) 0;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
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
  height: 32px;
  padding: 6px 14px;
  min-width: 32px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1em;
}

.overlay-icon-btn:hover {
  background: rgba(var(--v-theme-primary), 0.6);
}

.description-editor--sentinel textarea {
  font-style: italic;
  opacity: var(--opacity-text-secondary);
  cursor: default;
}
</style>
