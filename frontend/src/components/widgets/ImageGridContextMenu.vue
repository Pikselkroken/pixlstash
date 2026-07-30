<template>
  <Teleport to="body">
    <div
      v-if="visible"
      ref="menuRef"
      class="image-ctx-menu"
      :class="{ 'ctx-flip-sub': submenusFlip, 'image-ctx-menu--on-dark': onDark }"
      :style="menuStyle"
      role="menu"
      aria-orientation="vertical"
      tabindex="-1"
      @keydown="onMenuKeydown"
    >
      <!-- ── Read-only indicator ───────────────────────────────────── -->
      <div v-if="isReadOnly" class="ctx-readonly-header">
        <span class="ctx-readonly-pill">
          <v-icon size="10">mdi-lock-outline</v-icon>
          Read only
        </span>
      </div>

      <!-- ════════════════════════════════════════════════════════════
           OVERLAY (lightbox) MODE — a restricted, dark-surface action
           set that strictly COMPLEMENTS the overlay chrome. Everything
           else the grid menu offers is already reachable from the
           lightbox chrome or is multi-select-only, so it is hidden here.
           ════════════════════════════════════════════════════════════ -->
      <template v-if="overlayMode">
        <template v-if="!isScrapheapView">
          <!-- 1. Share -->
          <button
            v-if="contextImage?.id"
            class="ctx-item"
            role="menuitem"
            :disabled="isReadOnly"
            @click="onAction('share-picture')"
          >
            <v-icon class="ctx-icon" size="15">mdi-link-variant</v-icon>
            Share picture
          </button>
          <!-- 2. Find similar faces -->
          <template v-if="contextImage?.id && contextImageFaces.length">
            <button
              v-if="contextClickedFace || contextImageFaces.length === 1"
              class="ctx-item"
              role="menuitem"
              title="Find pictures with similar faces"
              @click="
                onAction(
                  'find-similar-faces',
                  (contextClickedFace ?? contextImageFaces[0]).id,
                )
              "
            >
              <v-icon class="ctx-icon" size="15">mdi-face-recognition</v-icon>
              Find similar faces
            </button>
            <div
              v-else
              class="ctx-submenu-wrap"
              @mouseenter="openFaceSubmenu"
              @mouseleave="findFacesSubmenuOpen = false"
              @focusin="openFaceSubmenu"
            >
              <button class="ctx-item" role="menuitem" aria-haspopup="menu">
                <v-icon class="ctx-icon" size="15">mdi-face-recognition</v-icon>
                Find similar faces
                <v-icon class="ctx-arrow" size="14">mdi-chevron-right</v-icon>
              </button>
              <div
                v-if="findFacesSubmenuOpen"
                class="ctx-submenu ctx-face-submenu"
                role="menu"
              >
                <button
                  v-for="(face, idx) in contextImageFaces"
                  :key="face.id ?? idx"
                  class="ctx-item ctx-face-item"
                  role="menuitem"
                  @click="onAction('find-similar-faces', face.id)"
                >
                  <div
                    class="ctx-face-thumb"
                    :style="getFaceThumbStyle(face, idx)"
                  />
                  <span>{{ faceLabel(face, idx) }}</span>
                </button>
              </div>
            </div>
          </template>
          <!-- 3. Reverse image search -->
          <button
            v-if="contextImage?.id"
            class="ctx-item"
            role="menuitem"
            title="Find visually similar images"
            @click="onAction('reverse-image-search')"
          >
            <v-icon class="ctx-icon" size="15">mdi-image-search-outline</v-icon>
            Reverse image search
          </button>
          <!-- 4. Segment -->
          <button
            class="ctx-item"
            role="menuitem"
            title="Detect objects and store bounding boxes"
            :disabled="!selectedImageIds.length || isReadOnly"
            @click="onAction('segment')"
          >
            <v-icon class="ctx-icon" size="15">mdi-shape-outline</v-icon>
            Segment
          </button>
          <!-- 5. Restore from snapshot -->
          <div
            v-if="!isReadOnly && selectedImageIds.length >= 1"
            class="ctx-submenu-wrap"
            @mouseenter="restoreSubmenuOpen = true"
            @mouseleave="restoreSubmenuOpen = false"
            @focusin="restoreSubmenuOpen = true"
          >
            <button
              class="ctx-item"
              role="menuitem"
              aria-haspopup="menu"
              :disabled="!selectedImageIds.length || isReadOnly"
            >
              <v-icon class="ctx-icon" size="15">mdi-restore</v-icon>
              Restore from snapshot
              <v-icon class="ctx-arrow" size="14">mdi-chevron-right</v-icon>
            </button>
            <div v-if="restoreSubmenuOpen" class="ctx-submenu" role="menu">
              <button
                v-for="cp in recentSnapshots"
                :key="cp.id"
                class="ctx-item"
                role="menuitem"
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
              <button class="ctx-item" role="menuitem" @click="handleRestoreMore">
                <v-icon class="ctx-icon" size="14">mdi-dots-horizontal</v-icon>
                More…
              </button>
            </div>
          </div>
          <div class="ctx-sep" role="separator" />
          <!-- 6. Delete (soft-delete → scrapheap; NOT permanent) -->
          <button
            class="ctx-item ctx-item--danger"
            role="menuitem"
            :disabled="!selectedImageIds.length || isReadOnly || !!lockReason"
            :title="lockReason || 'Move to the scrapheap'"
            @click="onAction('delete-selected')"
          >
            <v-icon class="ctx-icon" size="15">mdi-delete</v-icon>
            Delete
          </button>
        </template>
        <!-- Scrapheap overlay: Restore / Delete forever -->
        <template v-else>
          <button
            class="ctx-item"
            role="menuitem"
            :disabled="!selectedImageIds.length || isReadOnly"
            title="Restore this picture to the library"
            @click="onAction('remove-from-group')"
          >
            <v-icon class="ctx-icon" size="15">mdi-backup-restore</v-icon>
            Restore
          </button>
          <div class="ctx-sep" role="separator" />
          <button
            class="ctx-item ctx-item--danger"
            role="menuitem"
            :disabled="!selectedImageIds.length || isReadOnly"
            title="Permanently delete — this cannot be undone"
            @click="onAction('delete-selected')"
          >
            <v-icon class="ctx-icon" size="15">mdi-delete-forever</v-icon>
            Delete forever
          </button>
        </template>
      </template>

      <!-- ════════════════════════════════════════════════════════════
           GRID MODE — the full context menu for grid cells (unchanged).
           ════════════════════════════════════════════════════════════ -->
      <template v-else>
      <!-- ── Set / Character / Project ─────────────────────────────── -->
      <template v-if="!isScrapheapView">
        <AddToEntityControl
          type="project"
          placement="right"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          :disabled="!selectedImageIds.length || !!groupingLockReason"
          :title="groupingLockReason || undefined"
          :readonly="isReadOnly"
          @selected="onAction('set-project', $event)"
        />
        <AddToEntityControl
          type="character"
          placement="right"
          allow-create
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          :disabled="!selectedImageIds.length || !!groupingLockReason"
          :title="groupingLockReason || undefined"
          :readonly="isReadOnly"
          @added="onAction('add-to-character', $event)"
          @removed="onAction('remove-from-character', $event)"
          @create="delegateWith('create-character', $event)"
        />
        <AddToEntityControl
          type="set"
          placement="right"
          :backend-url="backendUrl"
          :picture-ids="selectedImageIds"
          :disabled="!selectedImageIds.length || !!groupingLockReason"
          :title="groupingLockReason || undefined"
          :readonly="isReadOnly"
          :locked-set-ids="lockedSetIds"
          @added="onAction('added-to-set', $event)"
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
          :title="lockReason || 'Tag selected (T)'"
          :disabled="!selectedImageIds.length || isReadOnly || !!lockReason"
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
          <button
            class="ctx-item"
            :title="lockReason || undefined"
            :disabled="!selectedImageIds.length || isReadOnly || !!lockReason"
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
              :title="lockReason || undefined"
              :disabled="!selectedImageIds.length || isReadOnly || !!lockReason"
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
          <button
            class="ctx-item"
            :title="lockReason || undefined"
            :disabled="!selectedImageIds.length || isReadOnly || !!lockReason"
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
              :title="lockReason || undefined"
              :disabled="!selectedImageIds.length || isReadOnly || !!lockReason"
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
          title="Generate variants from this image"
          :disabled="!contextImage || isReadOnly"
          @click="delegateWith('open-remix-dialog', contextImage?.id)"
        >
          <v-icon class="ctx-icon" size="15">mdi-auto-fix</v-icon>
          Generate variants…
        </button>
        <button
          v-if="comfyuiConfigured"
          class="ctx-item"
          :disabled="!selectedImageIds.length || isReadOnly"
          @click="delegate('open-comfyui-panel')"
        >
          <v-icon class="ctx-icon" size="15">mdi-robot</v-icon>
          Edit with ComfyUI
        </button>
        <button
          class="ctx-item"
          title="Detect objects and store bounding boxes"
          :disabled="!selectedImageIds.length || isReadOnly"
          @click="onAction('segment')"
        >
          <v-icon class="ctx-icon" size="15">mdi-shape-outline</v-icon>
          Segment
        </button>
        <div class="ctx-sep" />
      </template>

      <!-- ── Restore from snapshot ─────────────────────────── -->
      <template
        v-if="!isReadOnly && selectedImageIds.length >= 1 && !isScrapheapView"
      >
        <div
          class="ctx-submenu-wrap"
          @mouseenter="restoreSubmenuOpen = true"
          @mouseleave="restoreSubmenuOpen = false"
        >
          <button
            class="ctx-item"
            :disabled="!selectedImageIds.length || isReadOnly"
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

      <!-- ── Find similar faces ─────────────────────────────── -->
      <template
        v-if="
          contextImage?.id &&
          !isScrapheapView &&
          selectedImageIds.length === 1 &&
          contextImageFaces.length
        "
      >
        <!-- Direct action when a specific face was right-clicked, or only one face exists -->
        <button
          v-if="contextClickedFace || contextImageFaces.length === 1"
          class="ctx-item"
          title="Find pictures with similar faces"
          @click="
            onAction(
              'find-similar-faces',
              (contextClickedFace ?? contextImageFaces[0]).id,
            )
          "
        >
          <v-icon class="ctx-icon" size="15">mdi-face-recognition</v-icon>
          Find similar faces
        </button>
        <!-- Submenu to pick a face when not right-clicking on one -->
        <div
          v-else
          class="ctx-submenu-wrap"
          @mouseenter="openFaceSubmenu"
          @mouseleave="findFacesSubmenuOpen = false"
        >
          <button class="ctx-item">
            <v-icon class="ctx-icon" size="15">mdi-face-recognition</v-icon>
            Find similar faces
            <v-icon class="ctx-arrow" size="14">mdi-chevron-right</v-icon>
          </button>
          <div v-if="findFacesSubmenuOpen" class="ctx-submenu ctx-face-submenu">
            <button
              v-for="(face, idx) in contextImageFaces"
              :key="face.id ?? idx"
              class="ctx-item ctx-face-item"
              @click="onAction('find-similar-faces', face.id)"
            >
              <div
                class="ctx-face-thumb"
                :style="getFaceThumbStyle(face, idx)"
              />
              <span>{{ faceLabel(face, idx) }}</span>
            </button>
          </div>
        </div>
      </template>

      <!-- ── Reverse image search ────────────────────────────── -->
      <template v-if="contextImage?.id && !isScrapheapView">
        <button
          class="ctx-item"
          :disabled="!selectedImageIds.length"
          title="Find visually similar images"
          @click="onAction('reverse-image-search')"
        >
          <v-icon class="ctx-icon" size="15">mdi-image-search-outline</v-icon>
          Reverse image search
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
        :disabled="!selectedImageIds.length || isReadOnly || !!lockReason"
        :title="lockReason || 'Delete selected items (DEL)'"
        @click="onAction('delete-selected')"
      >
        <v-icon class="ctx-icon" size="15">mdi-delete</v-icon>
        {{ deleteButtonLabel }}
      </button>
      </template>
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
import { hashCompareSnapshot } from "../../api/snapshots";
import { getCharacterName } from "../../api/characters";
import { faceBoxColor } from "../../utils/utils.js";
import { useSnapshotsStore } from "../../stores/useSnapshotsStore";
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
  groupingLockReason: { type: String, default: null },
  // Reason string when at least one selected picture is frozen by a locked set;
  // gates the label-data actions (tag / auto-tag / description / delete) and is
  // shown as their tooltip. Null when nothing in the selection is locked.
  lockReason: { type: String, default: null },
  // Ids of all locked sets, so the add-to-set control can grey them out.
  lockedSetIds: { type: Object, default: () => new Set() },
  availablePlugins: { type: Array, default: () => [] },
  taggerPlugins: { type: Array, default: () => [] },
  captionerPlugins: { type: Array, default: () => [] },
  contextImage: { type: Object, default: null },
  contextClickedFace: { type: Object, default: null },
  isShared: { type: Boolean, default: false },
  // Overlay (lightbox) mode: renders the restricted overlay action set,
  // applies the dark-surface skin, and enables keyboard focus management
  // (auto-focus first item, arrow roving, focus-return to the invoker on close).
  overlayMode: { type: Boolean, default: false },
});

// The dark-surface skin is tied to overlay invocation — the menu renders over
// the dark lightbox there and nowhere else.
const onDark = computed(() => props.overlayMode);

const emit = defineEmits([
  "close",
  "added-to-set",
  "add-to-character",
  "remove-from-character",
  "create-character",
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
  "open-remix-dialog",
  "segment",
  "auto-tag",
  "generate-description",
  "share-picture",
  "remove-picture-shares",
  "reverse-image-search",
  "find-similar-faces",
]);

const menuRef = ref(null);
const adjustedX = ref(props.x);
const adjustedY = ref(props.y);
const submenusFlip = ref(false);
const autoTagSubmenuOpen = ref(false);
const descriptionSubmenuOpen = ref(false);
const findFacesSubmenuOpen = ref(false);
const restoreSubmenuOpen = ref(false);
const identicalSnapshotIds = ref(new Set());

// Run token guards against rapid submenu toggles: when the watcher fires
// again before its previous batch finishes, the old in-flight requests must
// not write their (now-stale) results into identicalSnapshotIds and overwrite
// the new batch's state. Same pattern as SnapshotsSection.vue:88.
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
        const body = await hashCompareSnapshot(cp.id, pictureIds);
        // Bail on stale apply — a newer run has superseded this one.
        if (token !== _hashCompareRunToken) return;
        const identicalSet = new Set(body.identical_ids);
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

const snapshotsStore = useSnapshotsStore();
const recentSnapshots = computed(() =>
  snapshotsStore.snapshots.filter((cp) => cp.is_compatible).slice(0, 5),
);

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
const faceCharacterNames = ref({}); // face.id -> character name string or null

// ── Face helpers ───────────────────────────────────────────────────────────

const contextImageFaces = computed(() => {
  if (!props.contextImage?.faces) return [];
  return props.contextImage.faces.filter(
    (f) => f.frame_index === 0 && f.id != null,
  );
});

async function loadFaceCharacterNames() {
  const faces = contextImageFaces.value;
  if (!faces.length || !props.backendUrl) return;
  const pending = faces.filter(
    (f) => f.character_id && !(f.id in faceCharacterNames.value),
  );
  await Promise.all(
    pending.map(async (face) => {
      try {
        const body = await getCharacterName(face.character_id, {
          baseUrl: props.backendUrl,
        });
        faceCharacterNames.value = {
          ...faceCharacterNames.value,
          [face.id]: body?.name || null,
        };
      } catch (e) {
        // Non-fatal: the menu entry just shows no name. Log it so a
        // systematically failing lookup is visible.
        console.debug(
          `Failed to resolve the name of character ${face.character_id}`,
          e,
        );
        faceCharacterNames.value = {
          ...faceCharacterNames.value,
          [face.id]: null,
        };
      }
    }),
  );
}

function openFaceSubmenu() {
  findFacesSubmenuOpen.value = true;
  loadFaceCharacterNames();
}

function faceLabel(face, idx) {
  if (face.character_id) {
    const name = faceCharacterNames.value[face.id];
    if (name) return name.charAt(0).toUpperCase() + name.slice(1);
    if (name === undefined) return `Face ${idx + 1}`; // still loading
  }
  return "Unassigned";
}

function getFaceThumbStyle(face, idx) {
  const color = faceBoxColor(idx);
  const img = props.contextImage;
  const bbox = Array.isArray(face?.bbox) ? face.bbox : null;
  if (!img?.thumbnail || !bbox || bbox.length !== 4) {
    return { width: "34px", height: "34px", borderColor: color };
  }
  const [x1, y1, x2, y2] = bbox;
  const imageW = img.thumbnail_width || img.width || 1;
  const imageH = img.thumbnail_height || img.height || 1;
  const faceW = Math.max(1, x2 - x1);
  const faceH = Math.max(1, y2 - y1);
  const targetMax = 34;
  const scale = targetMax / Math.max(faceW, faceH);
  const targetW = Math.max(1, Math.round(faceW * scale));
  const targetH = Math.max(1, Math.round(faceH * scale));
  return {
    width: `${targetW}px`,
    height: `${targetH}px`,
    borderColor: color,
    backgroundImage: `url(${img.thumbnail})`,
    backgroundSize: `${Math.round(imageW * scale)}px ${Math.round(imageH * scale)}px`,
    backgroundPosition: `${Math.round(-x1 * scale)}px ${Math.round(-y1 * scale)}px`,
  };
}

// ── Position clamping ──────────────────────────────────────────────────────

async function clampPosition() {
  await nextTick();
  if (!menuRef.value) {
    adjustedX.value = props.x;
    adjustedY.value = props.y;
    submenusFlip.value = false;
    return;
  }
  const rect = menuRef.value.getBoundingClientRect();
  const newX = Math.max(
    4,
    Math.min(props.x, window.innerWidth - rect.width - 4),
  );
  adjustedX.value = newX;
  adjustedY.value = Math.max(
    4,
    Math.min(props.y, window.innerHeight - rect.height - 4),
  );
  // Flip submenus leftward when there is not enough room to the right for a ~185px submenu
  submenusFlip.value = newX + rect.width + 185 > window.innerWidth - 8;
}

// Element focused before the menu opened, so Escape / an action can return
// focus to the invoker (e.g. the lightbox media) instead of dropping it to
// document.body. Only tracked in overlay mode (grid right-click keeps its
// mouse-driven, focus-neutral behaviour).
let previouslyFocused = null;

function focusFirstItem() {
  const first = menuRef.value?.querySelector(".ctx-item:not([disabled])");
  first?.focus();
}

watch(
  () => props.visible,
  (val) => {
    if (val) {
      clampPosition();
      if (props.overlayMode) {
        previouslyFocused =
          document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        nextTick(focusFirstItem);
      }
    } else {
      // Reset transient submenu state so a reopen starts clean.
      autoTagSubmenuOpen.value = false;
      descriptionSubmenuOpen.value = false;
      findFacesSubmenuOpen.value = false;
      restoreSubmenuOpen.value = false;
      if (props.overlayMode && previouslyFocused) {
        try {
          previouslyFocused.focus();
        } catch {
          // Invoker may have been unmounted (e.g. lightbox closed) — nothing
          // to return focus to; the browser keeps it on <body>.
        }
        previouslyFocused = null;
      }
    }
  },
);

// Roving focus for keyboard users: Up/Down move between enabled items, Home/End
// jump to the ends. Only fires while focus is inside the menu, so the grid menu
// (never auto-focused) is unaffected. Every keystroke handled here also stops
// bubbling so it never reaches the overlay's window-level key handler — which
// would otherwise navigate prev/next on arrows or toggle chrome on Space while
// the user is driving the menu. (Escape is intercepted earlier, in the
// capture-phase document handler.)
function onMenuKeydown(event) {
  if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    const items = Array.from(
      menuRef.value?.querySelectorAll(".ctx-item:not([disabled])") || [],
    );
    if (items.length) {
      event.preventDefault();
      const current = items.indexOf(document.activeElement);
      let next;
      if (event.key === "Home") next = 0;
      else if (event.key === "End") next = items.length - 1;
      else if (event.key === "ArrowDown")
        next = current < 0 ? 0 : (current + 1) % items.length;
      else next = current <= 0 ? items.length - 1 : current - 1;
      items[next]?.focus();
    }
  }
  // Keep menu keystrokes from leaking to global (overlay) handlers. Native
  // button activation (Enter / Space → click) is a default action, unaffected
  // by stopping propagation.
  event.stopPropagation();
}

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

// `delegate` plus a payload, for panels that need to know which picture was
// right-clicked. The nextTick is the point of it: the menu's own teardown must
// finish before the dialog opens, or the two race over focus and the dialog
// loses it back to the closing menu.
function delegateWith(panelEvent, payload) {
  emit("close");
  nextTick(() => emit(panelEvent, payload));
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
  z-index: var(--z-overlay);
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-3);
  padding: var(--space-2) 0;
  min-width: 185px;
  max-width: 260px;
  user-select: none;
  outline: none;
}

/* Dark-surface skin used when the menu is invoked from the lightbox, which
   sits on a dark backdrop in both themes. Item/separator overrides live in the
   global styles/context-menu.css because the teleported node carries the plain
   .ctx-* classes and this modifier as a global ancestor. */
.image-ctx-menu--on-dark {
  background: rgb(var(--v-theme-dark-surface));
  border-color: rgba(var(--v-theme-on-dark-surface), 0.14);
}

.ctx-face-submenu {
  min-width: 160px;
}

.ctx-face-item {
  gap: var(--space-3);
  align-items: center;
}

.ctx-face-thumb {
  flex-shrink: 0;
  border: 2px solid;
  border-radius: var(--radius-sm);
  background-color: rgba(var(--v-theme-on-surface), 0.08);
  background-repeat: no-repeat;
}
</style>
