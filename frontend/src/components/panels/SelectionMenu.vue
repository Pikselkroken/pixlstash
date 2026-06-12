<template>
  <div
    ref="selectionMenuPanelRef"
    class="selection-menu-panel"
    :class="{ 'ctx-flip-sub': selectionPanelFlipped }"
    @keydown="onSelectionMenuKeydown"
  >
    <!-- ── Read-only indicator ──────────────────────────── -->
    <div v-if="isReadOnly" class="ctx-readonly-header">
      <span class="ctx-readonly-pill">
        <v-icon size="10">mdi-lock-outline</v-icon>
        Read only
      </span>
    </div>
    <!-- ── Set / Character / Project ─────────────────────── -->
    <template v-if="!isScrapheapView">
      <AddToEntityControl
        ref="ateProjectRef"
        type="project"
        placement="right"
        :backend-url="backendUrl"
        :picture-ids="selectedImageIds"
        :disabled="selectedCount === 0 || !!groupingLockReason"
        :title="groupingLockReason || undefined"
        :readonly="isReadOnly"
        @selected="$emit('set-project', $event)"
      />
      <AddToEntityControl
        ref="ateCharacterRef"
        type="character"
        placement="right"
        :backend-url="backendUrl"
        :picture-ids="selectedImageIds"
        :disabled="selectedCount === 0 || !!groupingLockReason"
        :title="groupingLockReason || undefined"
        :readonly="isReadOnly"
        @added="$emit('add-to-character', $event)"
        @removed="$emit('remove-from-character', $event)"
      />
      <AddToEntityControl
        ref="ateSetRef"
        type="set"
        placement="right"
        :backend-url="backendUrl"
        :picture-ids="selectedImageIds"
        :disabled="selectedCount === 0 || !!groupingLockReason"
        :title="groupingLockReason || undefined"
        :readonly="isReadOnly"
        @added="$emit('added-to-set', $event)"
      />
      <div class="ctx-sep" />
    </template>

    <!-- ── Stack / Unstack ───────────────────────────────── -->
    <template v-if="!isScrapheapView">
      <button
        v-if="showRemoveStackButton"
        class="ctx-item"
        :disabled="isReadOnly"
        title="Remove selected images from their stack"
        @click="
          $emit('remove-from-stack');
          $emit('close');
        "
      >
        <v-icon class="ctx-icon" size="15">mdi-layers-off</v-icon>
        Unstack
      </button>
      <button
        v-else-if="selectedCount > 1"
        class="ctx-item"
        :disabled="isReadOnly"
        title="Create a stack from the selected images"
        @click="
          $emit('create-stack');
          $emit('close');
        "
      >
        <v-icon class="ctx-icon" size="15">mdi-layers</v-icon>
        Stack
      </button>
      <button
        v-if="showUnstackMultipleButton"
        class="ctx-item"
        :disabled="isReadOnly"
        title="Dissolve all selected stacks"
        @click="
          $emit('dissolve-stacks');
          $emit('close');
        "
      >
        <v-icon class="ctx-icon" size="15">mdi-layers-off</v-icon>
        Unstack all
      </button>
      <button
        v-if="showGroupStackButton"
        class="ctx-item"
        :disabled="isReadOnly"
        title="Create stacks from selected likeness groups"
        @click="
          $emit('create-stacks-from-groups');
          $emit('close');
        "
      >
        <v-icon class="ctx-icon" size="15">mdi-layers-plus</v-icon>
        Stack groups
      </button>
      <div v-if="showAnyStackAction" class="ctx-sep" />
    </template>

    <!-- ── Tag / Filters / ComfyUI ───────────────────────── -->
    <template v-if="!isScrapheapView">
      <button
        class="ctx-item"
        :disabled="selectedCount === 0 || isReadOnly"
        title="Tag selected (T)"
        @click="
          $emit('open-tag-input');
          $emit('close');
        "
      >
        <v-icon class="ctx-icon" size="15">mdi-tag-plus</v-icon>
        Tag
      </button>
      <div
        v-if="taggerPlugins.length"
        ref="autoTagSubmenuWrapRef"
        class="ctx-submenu-wrap"
        @mouseenter="autoTagSubmenuOpen = true"
        @mouseleave="autoTagSubmenuOpen = false"
      >
        <button
          class="ctx-item"
          :disabled="selectedCount === 0 || isReadOnly"
        >
          <v-icon class="ctx-icon" size="15">mdi-tag-outline</v-icon>
          Tag automatically
          <v-icon class="ctx-arrow" size="14">mdi-chevron-right</v-icon>
        </button>
        <div v-if="autoTagSubmenuOpen" class="ctx-submenu">
          <button
            v-for="plugin in taggerPlugins"
            :key="plugin.name"
            class="ctx-item"
            :disabled="selectedCount === 0 || isReadOnly"
            @click="
              $emit('auto-tag', { model: plugin.name });
              $emit('close');
            "
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
        ref="descriptionSubmenuWrapRef"
        class="ctx-submenu-wrap"
        @mouseenter="descriptionSubmenuOpen = true"
        @mouseleave="descriptionSubmenuOpen = false"
      >
        <button
          class="ctx-item"
          :disabled="selectedCount === 0 || isReadOnly"
        >
          <v-icon class="ctx-icon" size="15">mdi-text-box-outline</v-icon>
          Generate description
          <v-icon class="ctx-arrow" size="14">mdi-chevron-right</v-icon>
        </button>
        <div v-if="descriptionSubmenuOpen" class="ctx-submenu">
          <button
            v-for="plugin in captionerPlugins"
            :key="plugin.name"
            class="ctx-item"
            :disabled="selectedCount === 0 || isReadOnly"
            @click="
              $emit('generate-description', { model: plugin.name });
              $emit('close');
            "
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
        v-if="hasPluginOptions"
        class="ctx-item"
        :disabled="selectedCount === 0 || isReadOnly"
        @click="
          $emit('open-plugin-panel');
          $emit('close');
        "
      >
        <v-icon class="ctx-icon" size="15">mdi-tune-variant</v-icon>
        Filters
      </button>
      <button
        v-if="comfyuiConfigured"
        class="ctx-item"
        :disabled="selectedCount === 0 || isReadOnly"
        @click="
          $emit('open-comfyui-panel');
          $emit('close');
        "
      >
        <v-icon class="ctx-icon" size="15">mdi-auto-fix</v-icon>
        Edit with ComfyUI
      </button>
      <div class="ctx-sep" />
    </template>

    <!-- ── Restore from snapshot ─────────────────────────── -->
    <template v-if="!isReadOnly && selectedCount >= 1 && !isScrapheapView">
      <div
        ref="restoreSubmenuWrapRef"
        class="ctx-submenu-wrap"
        @mouseenter="restoreSubmenuOpen = true"
        @mouseleave="restoreSubmenuOpen = false"
      >
        <button
          class="ctx-item"
          :disabled="selectedCount === 0 || isReadOnly"
        >
          <v-icon class="ctx-icon" size="15">mdi-restore</v-icon>
          Restore from snapshot
          <v-icon class="ctx-arrow" size="14">mdi-chevron-right</v-icon>
        </button>
        <div v-if="restoreSubmenuOpen" class="ctx-submenu">
          <button
            v-for="cp in recentSnapshots"
            :key="cp.id"
            class="ctx-item"
            :disabled="identicalSnapshotIds.has(cp.id)"
            :title="
              identicalSnapshotIds.has(cp.id)
                ? 'Selection is identical to this snapshot'
                : undefined
            "
            @click="handleRestoreFromSnapshot(cp.id)"
          >
            <v-icon class="ctx-icon" size="14">mdi-camera-outline</v-icon>
            {{ cp.label || cp.kind }}
            <span class="ctx-default-pill">{{
              cp.created_at ? formatSnapshotDate(cp.created_at) : ""
            }}</span>
          </button>
          <button class="ctx-item" @click="handleRestoreMore">
            <v-icon class="ctx-icon" size="14">mdi-dots-horizontal</v-icon>
            More…
          </button>
        </div>
      </div>
    </template>

    <!-- ── Reverse image search ──────────────────────────── -->
    <template v-if="!isScrapheapView">
      <button
        class="ctx-item"
        :disabled="selectedCount === 0"
        title="Find visually similar images"
        @click="
          $emit('reverse-image-search');
          $emit('close');
        "
      >
        <v-icon class="ctx-icon" size="15">mdi-image-search-outline</v-icon>
        Reverse image search
      </button>
      <div class="ctx-sep" />
    </template>

    <!-- ── Remove / Delete (danger) ──────────────────────── -->
    <button
      v-if="showRemoveButton"
      class="ctx-item ctx-item--danger"
      :disabled="selectedCount === 0 || isReadOnly"
      @click="
        $emit('remove-from-group');
        $emit('close');
      "
    >
      {{ removeButtonLabel }}
    </button>
    <button
      class="ctx-item ctx-item--danger"
      :disabled="selectedCount === 0 || isReadOnly"
      title="Delete selected items (DEL)"
      @click="
        $emit('delete-selected');
        $emit('close');
      "
    >
      <v-icon class="ctx-icon" size="15">mdi-delete</v-icon>
      {{ deleteButtonLabel }}
    </button>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";
import { useSnapshotsStore } from "../../stores/useSnapshotsStore";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";

const LIKENESS_GROUPS_SORT_KEY = "LIKENESS_GROUPS";

const props = defineProps({
  open: Boolean,
  selectedCount: Number,
  selectedImageIds: { type: Array, default: () => [] },
  backendUrl: String,
  isReadOnly: Boolean,
  isScrapheapView: Boolean,
  groupingLockReason: { type: String, default: null },
  taggerPlugins: { type: Array, default: () => [] },
  captionerPlugins: { type: Array, default: () => [] },
  comfyuiConfigured: Boolean,
  hasPluginOptions: Boolean,
  selectedSort: { type: String, default: "" },
  selectedGroupName: String,
  selectedMultipleStackIds: { type: Array, default: () => [] },
  showRemoveFromStack: Boolean,
});

const emit = defineEmits([
  "set-project",
  "add-to-character",
  "remove-from-character",
  "added-to-set",
  "remove-from-stack",
  "create-stack",
  "dissolve-stacks",
  "create-stacks-from-groups",
  "open-tag-input",
  "auto-tag",
  "generate-description",
  "open-plugin-panel",
  "open-comfyui-panel",
  "reverse-image-search",
  "remove-from-group",
  "delete-selected",
  "close",
]);

const snapshotsStore = useSnapshotsStore();

const selectionMenuPanelRef = ref(null);
const selectionPanelFlipped = ref(false);
const ateProjectRef = ref(null);
const ateCharacterRef = ref(null);
const ateSetRef = ref(null);
const autoTagSubmenuWrapRef = ref(null);
const descriptionSubmenuWrapRef = ref(null);
const restoreSubmenuWrapRef = ref(null);
const restoreSubmenuOpen = ref(false);
const autoTagSubmenuOpen = ref(false);
const descriptionSubmenuOpen = ref(false);
const identicalSnapshotIds = ref(new Set());

const showRemoveStackButton = computed(() => {
  if (props.isScrapheapView) return false;
  return props.showRemoveFromStack === true;
});

// True when selected images span multiple stacks (or one stack mixed with
// non-stacked images) — the single-stack case is handled by showRemoveStackButton.
const showUnstackMultipleButton = computed(() => {
  if (props.isScrapheapView) return false;
  if (showRemoveStackButton.value) return false;
  return props.selectedMultipleStackIds.length > 0;
});

const showGroupStackButton = computed(() => {
  if (props.isScrapheapView) return false;
  return (
    props.selectedCount > 0 && props.selectedSort === LIKENESS_GROUPS_SORT_KEY
  );
});

const showAnyStackAction = computed(() => {
  if (props.isScrapheapView || props.isReadOnly) return false;
  return (
    showRemoveStackButton.value ||
    (props.selectedCount > 1 && !showRemoveStackButton.value) ||
    showUnstackMultipleButton.value ||
    showGroupStackButton.value
  );
});

const showRemoveButton = computed(() => {
  if (props.selectedCount <= 0) return false;
  return props.isScrapheapView;
});

const removeButtonLabel = computed(() => {
  if (props.isScrapheapView) return "Restore Selected";
  return `Remove from ${props.selectedGroupName ? props.selectedGroupName : "group"}`;
});

const deleteButtonLabel = computed(() => {
  if (props.isScrapheapView) return "Permanently Delete";
  return "Delete";
});

function getMenuItems() {
  const panel = selectionMenuPanelRef.value;
  if (!panel) return [];
  const focused = document.activeElement;
  // Inside an open ATE flyout menu?
  const openAteMenu = focused?.closest(".ate-menu.open");
  if (openAteMenu && panel.contains(openAteMenu)) {
    return Array.from(
      openAteMenu.querySelectorAll("input, button:not(:disabled)"),
    ).filter((el) => el.offsetParent !== null);
  }
  // Inside a ctx-submenu (v-if, only in DOM when open)?
  const ctxSub = focused?.closest(".ctx-submenu");
  if (ctxSub && panel.contains(ctxSub)) {
    return Array.from(ctxSub.querySelectorAll("button:not(:disabled)")).filter(
      (el) => el.offsetParent !== null,
    );
  }
  // Top level: buttons not inside any .ate-menu
  return Array.from(panel.querySelectorAll("button:not(:disabled)")).filter(
    (el) => el.closest(".ate-menu") === null && el.offsetParent !== null,
  );
}

async function onSelectionMenuKeydown(event) {
  const key = event.key;
  if (
    !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter"].includes(key)
  )
    return;
  const focused = document.activeElement;
  const panel = selectionMenuPanelRef.value;
  if (!panel) return;
  // Enter: activate the focused button (Vuetify intercepts Enter in menu overlays)
  if (key === "Enter") {
    if (focused?.tagName === "BUTTON" && !focused.disabled) {
      event.stopPropagation();
      focused.click();
    }
    return;
  }
  // For left/right in a text input, only intercept Left at cursor start
  if (focused?.tagName === "INPUT") {
    if (key === "ArrowRight") return;
    if (key === "ArrowLeft") {
      const atStart =
        focused.selectionStart === 0 && focused.selectionEnd === 0;
      if (!atStart) return;
    }
  }
  event.preventDefault();
  event.stopPropagation();
  const items = getMenuItems();
  const idx = items.indexOf(focused);
  if (key === "ArrowDown") {
    (idx === -1
      ? items[0]
      : items[Math.min(idx + 1, items.length - 1)]
    )?.focus();
    return;
  }
  if (key === "ArrowUp") {
    (idx === -1
      ? items[items.length - 1]
      : items[Math.max(idx - 1, 0)]
    )?.focus();
    return;
  }
  if (key === "ArrowRight") {
    if (!focused) return;
    // ATE flyout trigger → open it and focus its search input
    if (
      focused.classList.contains("ate-btn") &&
      focused.closest(".ate--flyout")
    ) {
      const ateRoot = focused.closest(".ate");
      if (!ateRoot.classList.contains("open")) focused.click();
      await nextTick();
      ateRoot.querySelector(".ate-menu.open input")?.focus();
      return;
    }
    // ctx-submenu-wrap trigger → open submenu and focus first item
    const wrap = focused.closest(".ctx-submenu-wrap");
    if (wrap) {
      if (autoTagSubmenuWrapRef.value === wrap) autoTagSubmenuOpen.value = true;
      else if (descriptionSubmenuWrapRef.value === wrap)
        descriptionSubmenuOpen.value = true;
      else if (restoreSubmenuWrapRef.value === wrap)
        restoreSubmenuOpen.value = true;
      await nextTick();
      wrap.querySelector(".ctx-submenu button:not(:disabled)")?.focus();
    }
    return;
  }
  if (key === "ArrowLeft") {
    if (!focused) return;
    // Inside an open ATE flyout → close it, refocus trigger
    const openAteMenu = focused.closest(".ate-menu.open");
    if (openAteMenu && panel.contains(openAteMenu)) {
      const ateRoot = openAteMenu.closest(".ate");
      for (const ateRef of [ateProjectRef, ateCharacterRef, ateSetRef]) {
        if (ateRef.value?.$el === ateRoot) {
          ateRef.value.closeMenu();
          break;
        }
      }
      await nextTick();
      ateRoot?.querySelector(".ate-btn")?.focus();
      return;
    }
    // Inside a ctx-submenu → close it, refocus trigger
    const ctxSub = focused.closest(".ctx-submenu");
    if (ctxSub && panel.contains(ctxSub)) {
      const wrap = ctxSub.closest(".ctx-submenu-wrap");
      if (autoTagSubmenuWrapRef.value === wrap)
        autoTagSubmenuOpen.value = false;
      else if (descriptionSubmenuWrapRef.value === wrap)
        descriptionSubmenuOpen.value = false;
      else if (restoreSubmenuWrapRef.value === wrap)
        restoreSubmenuOpen.value = false;
      await nextTick();
      wrap?.querySelector(":scope > button.ctx-item")?.focus();
    }
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      selectionPanelFlipped.value = false;
      return;
    }
    await nextTick();
    if (!selectionMenuPanelRef.value) return;
    const rect = selectionMenuPanelRef.value.getBoundingClientRect();
    selectionPanelFlipped.value = rect.right + 185 > window.innerWidth - 8;
  },
);

// ── Restore from snapshot (mirrors ImageGridContextMenu.vue) ─────────────────
// The five most-recent compatible snapshots offered as quick-restore targets
// for the current selection. Lives here so the Selection ▾ dropdown — and its
// keyboard "S" entry point — exposes the same selection-scoped action as the
// right-click context menu. Like the context menu, it drives the app-wide
// RestoreConfirmDialog via the snapshots store (no parent wiring needed).
const recentSnapshots = computed(() =>
  snapshotsStore.snapshots.filter((cp) => cp.is_compatible).slice(0, 5),
);

// Run token guards against rapid submenu toggles: when the watcher fires again
// before its previous batch finishes, the old in-flight requests must not write
// their (now-stale) results into identicalSnapshotIds. Same pattern as
// ImageGridContextMenu.vue and SnapshotsSection.vue.
let _hashCompareRunToken = 0;

watch(restoreSubmenuOpen, async (isOpen) => {
  if (!isOpen || !props.selectedImageIds.length) {
    return;
  }
  const token = ++_hashCompareRunToken;
  identicalSnapshotIds.value = new Set();
  const pictureIds = props.selectedImageIds;
  const matchedIds = new Set();
  await Promise.all(
    recentSnapshots.value.map(async (cp) => {
      try {
        const res = await apiClient.post(
          `/api/v1/snapshots/${cp.id}/hash-compare`,
          { picture_ids: pictureIds },
        );
        // Bail on stale apply — a newer run has superseded this one.
        if (token !== _hashCompareRunToken) return;
        const identicalSet = new Set(res.data.identical_ids);
        const allIdentical = pictureIds.every((id) => identicalSet.has(id));
        if (allIdentical) {
          matchedIds.add(cp.id);
        }
      } catch (err) {
        // On error, leave the snapshot enabled (conservative).
        console.warn(`Hash-compare failed for snapshot ${cp.id}:`, err);
      }
    }),
  );
  if (token === _hashCompareRunToken) {
    identicalSnapshotIds.value = matchedIds;
  }
});

// Snapshot created_at may arrive as a bare ISO string (treat as UTC, append
// "Z") OR already carry a "Z" / offset suffix. Blindly appending "Z" to the
// latter yields "...+00:00Z" which Date parses as Invalid Date.
function formatSnapshotDate(iso) {
  if (!iso) return "";
  const hasTz = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString();
}

function handleRestoreFromSnapshot(cpId) {
  const resources = props.selectedImageIds.map((id) => ({
    type: "picture",
    id,
  }));
  snapshotsStore.openRestoreDialog(cpId, resources);
  emit("close");
}

function handleRestoreMore() {
  const resources = props.selectedImageIds.map((id) => ({
    type: "picture",
    id,
  }));
  snapshotsStore.openRestoreDialog(null, resources);
  emit("close");
}

function focusFirst() {
  getMenuItems()[0]?.focus();
}

function containsFocus() {
  return selectionMenuPanelRef.value?.contains(document.activeElement) ?? false;
}

defineExpose({ focusFirst, containsFocus });
</script>

<style scoped>
.selection-menu-panel {
  min-width: 160px;
  background: rgba(var(--v-theme-surface), 0.98);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
  padding: 4px 0;
}
</style>
