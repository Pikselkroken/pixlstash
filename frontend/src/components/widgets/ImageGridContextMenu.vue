<template>
  <Teleport to="body">
    <div
      v-if="visible"
      ref="menuRef"
      class="image-ctx-menu"
      :style="menuStyle"
      tabindex="-1"
    >
      <!-- ── Read-only indicator ───────────────────────────────────── -->
      <div v-if="isReadOnly" class="ctx-readonly-header">
        <span class="ctx-readonly-pill">
          <v-icon size="10">mdi-lock-outline</v-icon>
          Read only
        </span>
      </div>

      <!-- ── Set / Character / Project ─────────────────────────────── -->
      <template v-if="!isScrapheapView">
        <AddToEntityControl
          type="set"
          placement="right"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          :disabled="!selectedImageIds.length || isReadOnly"
          @added="onAction('added-to-set', $event)"
        />
        <AddToEntityControl
          type="character"
          placement="right"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          :disabled="!selectedImageIds.length || isReadOnly"
          @added="onAction('add-to-character', $event)"
          @removed="onAction('remove-from-character', $event)"
        />
        <AddToEntityControl
          type="project"
          placement="right"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          :disabled="!selectedImageIds.length || isReadOnly"
          @selected="onAction('set-project', $event)"
        />
        <div class="ctx-sep" />
      </template>

      <!-- ── Stack / Unstack ───────────────────────────────────────── -->
      <template v-if="!isScrapheapView">
        <button
          v-if="showRemoveStackButton"
          class="ctx-item"
          :disabled="isReadOnly"
          title="Remove selected images from their stack"
          @click="onAction('remove-from-stack')"
        >
          <v-icon class="ctx-icon" size="15">mdi-layers-off</v-icon>
          Unstack
        </button>
        <button
          v-else-if="selectedImageIds.length > 1"
          class="ctx-item"
          :disabled="isReadOnly"
          title="Create a stack from the selected images"
          @click="onAction('create-stack')"
        >
          <v-icon class="ctx-icon" size="15">mdi-layers</v-icon>
          Stack
        </button>
        <button
          v-if="showUnstackMultipleButton"
          class="ctx-item"
          :disabled="isReadOnly"
          title="Dissolve all selected stacks"
          @click="onAction('dissolve-stacks')"
        >
          <v-icon class="ctx-icon" size="15">mdi-layers-off</v-icon>
          Unstack all
        </button>
        <button
          v-if="showGroupStackButton"
          class="ctx-item"
          :disabled="isReadOnly"
          title="Create stacks from selected likeness groups"
          @click="onAction('create-stacks-from-groups')"
        >
          <v-icon class="ctx-icon" size="15">mdi-layers-plus</v-icon>
          Stack groups
        </button>
        <div v-if="showAnyStackAction" class="ctx-sep" />
      </template>

      <!-- ── Tag / Filters / ComfyUI (delegate to SelectionBar panels) ── -->
      <template v-if="!isScrapheapView">
        <button
          class="ctx-item"
          title="Tag selected (T)"
          :disabled="!selectedImageIds.length || isReadOnly"
          @click="delegate('open-tag-panel')"
        >
          <v-icon class="ctx-icon" size="15">mdi-tag-plus</v-icon>
          Tag
        </button>
        <div
          v-if="taggerPlugins.length"
          class="ctx-submenu-wrap"
          @mouseenter="autoTagSubmenuOpen = true"
          @mouseleave="autoTagSubmenuOpen = false"
        >
          <button class="ctx-item" :disabled="!selectedImageIds.length">
            <v-icon class="ctx-icon" size="15">mdi-tag-outline</v-icon>
            Tag automatically
            <v-icon class="ctx-arrow" size="14">mdi-chevron-right</v-icon>
          </button>
          <div v-if="autoTagSubmenuOpen" class="ctx-submenu">
            <button
              v-for="plugin in taggerPlugins"
              :key="plugin.name"
              class="ctx-item"
              :disabled="!selectedImageIds.length || isReadOnly"
              @click="onAction('auto-tag', { model: plugin.name })"
            >
              <v-icon class="ctx-icon" size="15">mdi-tag-outline</v-icon>
              {{ plugin.display_name || plugin.name }}
              <span v-if="plugin.default_enabled" class="ctx-default-pill"
                >default</span
              >
            </button>
          </div>
        </div>
        <div
          v-if="captionerPlugins.length"
          class="ctx-submenu-wrap"
          @mouseenter="descriptionSubmenuOpen = true"
          @mouseleave="descriptionSubmenuOpen = false"
        >
          <button class="ctx-item" :disabled="!selectedImageIds.length">
            <v-icon class="ctx-icon" size="15">mdi-text-box-outline</v-icon>
            Generate description
            <v-icon class="ctx-arrow" size="14">mdi-chevron-right</v-icon>
          </button>
          <div v-if="descriptionSubmenuOpen" class="ctx-submenu">
            <button
              v-for="plugin in captionerPlugins"
              :key="plugin.name"
              class="ctx-item"
              :disabled="!selectedImageIds.length || isReadOnly"
              @click="onAction('generate-description', { model: plugin.name })"
            >
              <v-icon class="ctx-icon" size="15">mdi-text-box-outline</v-icon>
              {{ plugin.display_name || plugin.name }}
              <span v-if="plugin.default_enabled" class="ctx-default-pill"
                >default</span
              >
            </button>
          </div>
        </div>
        <button
          v-if="pluginOptions.length"
          class="ctx-item"
          :disabled="!selectedImageIds.length || isReadOnly"
          @click="delegate('open-plugin-panel')"
        >
          <v-icon class="ctx-icon" size="15">mdi-tune-variant</v-icon>
          Filters
        </button>
        <button
          v-if="comfyuiConfigured"
          class="ctx-item"
          :disabled="!selectedImageIds.length || isReadOnly"
          @click="delegate('open-comfyui-panel')"
        >
          <v-icon class="ctx-icon" size="15">mdi-robot</v-icon>
          ComfyUI
        </button>
        <div class="ctx-sep" />
      </template>

      <!-- ── Share image ──────────────────────────────────────────── -->
      <template v-if="contextImage?.id && selectedImageIds.length === 1">
        <button
          class="ctx-item"
          :disabled="isReadOnly"
          @click="onAction('share-picture')"
        >
          <v-icon class="ctx-icon" size="15">mdi-link-variant</v-icon>
          Share image
        </button>
        <button
          v-if="isShared"
          class="ctx-item ctx-item--danger"
          :disabled="isReadOnly"
          @click="onAction('remove-picture-shares')"
        >
          <v-icon class="ctx-icon" size="15">mdi-link-variant-off</v-icon>
          Remove all shares
        </button>
        <div class="ctx-sep" />
      </template>

      <!-- ── Remove / Delete ───────────────────────────────────────── -->
      <button
        v-if="showRemoveButton"
        class="ctx-item ctx-item--danger"
        :disabled="!selectedImageIds.length || isReadOnly"
        @click="onAction('remove-from-group')"
      >
        {{ removeButtonLabel }}
      </button>
      <button
        class="ctx-item ctx-item--danger"
        :disabled="!selectedImageIds.length || isReadOnly"
        title="Delete selected items (DEL)"
        @click="onAction('delete-selected')"
      >
        <v-icon class="ctx-icon" size="15">mdi-delete</v-icon>
        {{ deleteButtonLabel }}
      </button>
    </div>
  </Teleport>
</template>

<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { isReadOnly } from "../../utils/apiClient";
import AddToEntityControl from "./AddToEntityControl.vue";

const props = defineProps({
  visible: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  selectedImageIds: { type: Array, default: () => [] },
  selectedMediaSupport: {
    type: Object,
    default: () => ({ hasImages: false, hasVideos: false }),
  },
  selectedCharacter: { type: String, default: "" },
  selectedSet: { type: String, default: "" },
  selectedGroupName: { type: String, default: "" },
  selectedSort: { type: String, default: "" },
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  scrapheapPicturesId: { type: String, required: true },
  backendUrl: { type: String, required: true },
  comfyuiConfigured: { type: Boolean, default: false },
  showRemoveFromStack: { type: Boolean, default: false },
  selectedMultipleStackIds: { type: Array, default: () => [] },
  availablePlugins: { type: Array, default: () => [] },
  taggerPlugins: { type: Array, default: () => [] },
  captionerPlugins: { type: Array, default: () => [] },
  contextImage: { type: Object, default: null },
  isShared: { type: Boolean, default: false },
});

const emit = defineEmits([
  "close",
  "added-to-set",
  "add-to-character",
  "remove-from-character",
  "set-project",
  "remove-from-stack",
  "dissolve-stacks",
  "create-stack",
  "create-stacks-from-groups",
  "remove-from-group",
  "delete-selected",
  "open-tag-panel",
  "open-plugin-panel",
  "open-comfyui-panel",
  "auto-tag",
  "generate-description",
  "share-picture",
  "remove-picture-shares",
]);

const menuRef = ref(null);
const adjustedX = ref(props.x);
const adjustedY = ref(props.y);
const autoTagSubmenuOpen = ref(false);
const descriptionSubmenuOpen = ref(false);

// ── Position clamping ──────────────────────────────────────────────────────

async function clampPosition() {
  await nextTick();
  if (!menuRef.value) {
    adjustedX.value = props.x;
    adjustedY.value = props.y;
    return;
  }
  const rect = menuRef.value.getBoundingClientRect();
  adjustedX.value = Math.max(
    4,
    Math.min(props.x, window.innerWidth - rect.width - 4),
  );
  adjustedY.value = Math.max(
    4,
    Math.min(props.y, window.innerHeight - rect.height - 4),
  );
}

watch(
  () => props.visible,
  (val) => {
    if (val) clampPosition();
  },
);

watch([() => props.x, () => props.y], () => {
  adjustedX.value = props.x;
  adjustedY.value = props.y;
  if (props.visible) clampPosition();
});

const menuStyle = computed(() => ({
  left: `${adjustedX.value}px`,
  top: `${adjustedY.value}px`,
}));

// ── Computed state (mirrors SelectionBar logic) ─────────────────────────────

const selectedCount = computed(() => props.selectedImageIds.length);

const isScrapheapView = computed(() => {
  const scrapId = String(
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  return String(props.selectedCharacter || "").toUpperCase() === scrapId;
});

const normalizedSelectedCharacter = computed(() => {
  const raw = String(props.selectedCharacter ?? "")
    .trim()
    .toUpperCase();
  return !raw || raw === "NULL" || raw === "UNDEFINED" ? "" : raw;
});

const hasSetSelectionContext = computed(() => {
  const setId = Number(props.selectedSet);
  return Number.isFinite(setId) && setId > 0;
});

const showRemoveButton = computed(() => {
  if (!selectedCount.value) return false;
  return isScrapheapView.value;
});

const removeButtonLabel = computed(() =>
  isScrapheapView.value
    ? "Restore selected"
    : `Remove from ${props.selectedGroupName || "group"}`,
);

const deleteButtonLabel = computed(() =>
  isScrapheapView.value ? "Permanently delete" : "Delete",
);

const showRemoveStackButton = computed(
  () => !isScrapheapView.value && props.showRemoveFromStack === true,
);

const showUnstackMultipleButton = computed(
  () =>
    !isScrapheapView.value &&
    !showRemoveStackButton.value &&
    props.selectedMultipleStackIds.length > 0,
);

const showGroupStackButton = computed(
  () =>
    !isScrapheapView.value &&
    selectedCount.value > 0 &&
    props.selectedSort === "LIKENESS_GROUPS",
);

const showAnyStackAction = computed(
  () =>
    showRemoveStackButton.value ||
    (!isScrapheapView.value && selectedCount.value > 1) ||
    showUnstackMultipleButton.value ||
    showGroupStackButton.value,
);

const pluginOptions = computed(() => {
  if (!Array.isArray(props.availablePlugins)) return [];
  const hasImages = props.selectedMediaSupport?.hasImages === true;
  const hasVideos = props.selectedMediaSupport?.hasVideos === true;
  return props.availablePlugins.filter((plugin) => {
    if (!plugin?.name) return false;
    if (hasImages && plugin.supports_images === false) return false;
    if (hasVideos && plugin.supports_videos !== true) return false;
    return true;
  });
});

// ── Actions ─────────────────────────────────────────────────────────────────

function onAction(eventName, payload) {
  emit("close");
  if (payload !== undefined) {
    emit(eventName, payload);
  } else {
    emit(eventName);
  }
}

function delegate(panelEvent) {
  emit("close");
  nextTick(() => emit(panelEvent));
}

// ── Click-outside + Escape ───────────────────────────────────────────────────

function onDocumentMousedown(event) {
  if (!props.visible) return;
  if (menuRef.value?.contains(event.target)) return;
  // Don't close when clicking inside a Vuetify overlay (e.g. AddToSet sub-menu)
  if (event.target.closest?.(".v-overlay-container")) return;
  // Don't close when clicking inside teleported flyout menus from the Add-to controls
  if (event.target.closest?.(".ate-menu")) return;
  emit("close");
}

function onDocumentKeydown(event) {
  if (!props.visible) return;
  if (event.key === "Escape") {
    event.stopImmediatePropagation();
    emit("close");
  }
}

onMounted(() => {
  document.addEventListener("mousedown", onDocumentMousedown);
  document.addEventListener("keydown", onDocumentKeydown, true);
});

onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onDocumentMousedown);
  document.removeEventListener("keydown", onDocumentKeydown, true);
});
</script>

<style scoped>
.image-ctx-menu {
  position: fixed;
  z-index: 2000;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 6px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.22);
  padding: 4px 0;
  min-width: 185px;
  max-width: 260px;
  user-select: none;
  outline: none;
}
</style>
