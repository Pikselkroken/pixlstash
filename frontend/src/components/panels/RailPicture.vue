<template>
  <!-- One selected picture in the grid's rail (#2161): the preview, the
       tile's own rating strip, then the lightbox's own Description and Tags
       panels and the picture's faces and properties. The panels are the
       lightbox's, so an edit here is the same edit there; they are drawn for
       the lightbox's dark surface, and `.rail-picture` hands them this
       theme's inks instead. -->
  <div class="rail-picture">
    <div class="rail-picture__preview">
      <img
        v-if="thumbnailUrl"
        :src="thumbnailUrl"
        alt=""
        draggable="false"
      />
    </div>
    <!-- The same strip as the grid tile: five stars, a hairline, the heart.
         It rates and favourites through the grid's own paths (App.vue), so
         the tile and the rail cannot disagree. -->
    <div class="rail-picture__strip">
      <StarRatingOverlay
        :score="score"
        icon-size="20"
        :favouritable="favouritable"
        :favourite="Boolean(card?.favourite)"
        @set-score="emit('set-score', $event)"
        @toggle-favourite="emit('toggle-favourite')"
      />
    </div>

    <OverlayDescriptionPanel
      :image="image"
      :locked="locked"
      :lock-note="lockNote"
      @update-description="onDescriptionUpdated"
    />

    <section class="rail-picture__section" aria-label="Faces">
      <SectionHeader
        v-model:collapsed="facesCollapsed"
        label="Faces"
        :count="faces.length || null"
        on-dark
      />
      <template v-if="!facesCollapsed">
        <ul v-if="faces.length" class="rail-faces">
          <li v-for="(face, idx) in faces" :key="face.id ?? `face-${idx}`">
            <span class="rail-faces__crop" :style="faceCropStyle(face)"></span>
            <AddToEntityControl
              type="face"
              float-menu
              :subject-ids="[]"
              :face-id="face.id"
              :assigned-character-id="face.character_id"
              :assigned-character-name="personName(face.character_id)"
              :readonly="isReadOnly"
              :disabled="!face.id || isReadOnly"
              @assign="assignFace(face, $event)"
              @unassign="unassignFace(face)"
            />
          </li>
        </ul>
        <p v-else class="rail-picture__empty">No faces detected</p>
      </template>
    </section>

    <OverlayTagsPanel
      ref="tagsPanelRef"
      :image="image"
      :locked="locked"
      :lock-note="lockNote"
      @update-tags="onTagsUpdated"
      @overlay-change="emit('overlay-change', $event)"
      @add-tag="(id, tag) => emit('add-tag', id, tag)"
      @request-metadata-refresh="emit('refresh')"
    />

    <section class="rail-picture__section" aria-label="Properties">
      <SectionHeader
        v-model:collapsed="propertiesCollapsed"
        label="Properties"
        on-dark
      />
      <dl v-if="!propertiesCollapsed" class="rail-picture__props">
        <template v-for="row in properties" :key="row.label">
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </template>
      </dl>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import { pictureThumbnailUrl } from "../../api/pictures";
import {
  addCharacterFacesByFaceId,
  removeCharacterFacesByFaceId,
} from "../../api/characters";
import { formatUserDate } from "../../utils/utils.js";
import { formatModelSize } from "../../utils/modelShelf.js";
import { errorDetail } from "../../utils/apiError";
import { useSectionCollapse } from "../../composables/useSectionCollapse";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import SectionHeader from "../widgets/SectionHeader.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import OverlayDescriptionPanel from "../views/OverlayDescriptionPanel.vue";
import OverlayTagsPanel from "../views/OverlayTagsPanel.vue";

const props = defineProps({
  pictureId: { type: [Number, String], required: true },
  /** The grid's card: thumbnail, rating and heart. Null until it is loaded. */
  card: { type: Object, default: null },
  /** The details read for the rail (`useRailPicture`); null while reading. */
  image: { type: Object, default: null },
  faces: { type: Array, default: () => [] },
  /** The rating the tile shows (a guest's own, in a read-only session). */
  score: { type: Number, default: 0 },
  /** The tile's heart rule: not read-only, not the Scrapheap. */
  favouritable: { type: Boolean, default: true },
});

const emit = defineEmits([
  "set-score",
  "toggle-favourite",
  "add-tag",
  "update-description",
  "overlay-change",
  "update:image",
  "update:faces",
  "refresh",
]);

const namesStore = useEntityNamesStore();
const lockedSetsStore = useLockedSetsStore();
const noticeStore = useNoticeStore();
const userPrefsStore = useUserPrefsStore();

const facesCollapsed = useSectionCollapse("faces");
const propertiesCollapsed = useSectionCollapse("properties");

const locked = computed(() => lockedSetsStore.isLocked(props.pictureId));
const lockNote = computed(() => lockedSetsStore.lockReason(props.pictureId));

const thumbnailUrl = computed(
  () => props.card?.thumbnail || pictureThumbnailUrl(props.pictureId),
);

function personName(characterId) {
  if (characterId == null) return "";
  return namesStore.characterNames[characterId] || `Person ${characterId}`;
}

const FACE_CROP = 32;

// The face, cut from the thumbnail: it keeps the picture's aspect, so the
// detector's full-size box scales onto it.
function faceCropStyle(face) {
  const width = Number(props.image?.width || props.card?.width || 0);
  const height = Number(props.image?.height || props.card?.height || 0);
  const [x1, y1, x2, y2] = face.bbox;
  if (!width || !height || !thumbnailUrl.value) return {};
  const scale = FACE_CROP / Math.max(1, x2 - x1, y2 - y1);
  return {
    backgroundImage: `url(${thumbnailUrl.value})`,
    backgroundSize: `${Math.round(width * scale)}px ${Math.round(height * scale)}px`,
    backgroundPosition: `${Math.round(-x1 * scale)}px ${Math.round(-y1 * scale)}px`,
  };
}

function replaceFace(faceId, patch) {
  emit(
    "update:faces",
    props.faces.map((f) => (f.id === faceId ? { ...f, ...patch } : f)),
  );
  emit("overlay-change", {
    imageId: Number(props.pictureId),
    fields: { faces: true },
  });
}

async function assignFace(face, payload) {
  const characterId = payload?.characterId;
  if (!face?.id || characterId == null) return;
  try {
    await addCharacterFacesByFaceId(characterId, [face.id]);
    replaceFace(face.id, { character_id: characterId });
  } catch (e) {
    console.error("Failed to assign a face from the rail", face.id, e);
    noticeStore.error(
      `Couldn't assign that person. ${errorDetail(e) || e?.message || "Please try again."}`,
      { key: "rail-assign-face" },
    );
  }
}

async function unassignFace(face) {
  if (!face?.id || face.character_id == null) return;
  try {
    await removeCharacterFacesByFaceId(face.character_id, [face.id]);
    replaceFace(face.id, { character_id: null });
  } catch (e) {
    console.error("Failed to unassign a face from the rail", face.id, e);
    noticeStore.error(
      `Couldn't unassign that person. ${errorDetail(e) || e?.message || "Please try again."}`,
      { key: "rail-unassign-face" },
    );
  }
}

// ── Keys for selected chips ─────────────────────────────────────────────────
// Chips selected in Tags take Escape, Delete and Ctrl+C here as they do in the
// lightbox, and ahead of the grid: a click on a chip leaves focus on the page,
// where the grid's Delete would send the picture itself to the Scrapheap.
const tagsPanelRef = ref(null);

function isEditable(el) {
  return (
    el instanceof HTMLElement &&
    (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))
  );
}

function onChipKeydown(e) {
  const panel = tagsPanelRef.value;
  if (!panel?.hasSelection || e.altKey || e.repeat || isEditable(e.target)) {
    return;
  }
  // The lightbox or a dialog over the grid owns the keys while it is open.
  if (document.querySelector(".image-overlay, .v-overlay--active .v-overlay__scrim")) {
    return;
  }
  const command = e.ctrlKey || e.metaKey;
  let act = null;
  if (e.key === "Escape" && !command) {
    act = () => panel.clearSelection();
  } else if ((e.key === "Delete" || e.key === "Backspace") && !command) {
    act = () => {
      if (!isReadOnly.value && !locked.value) void panel.removeSelection();
    };
  } else if (command && !e.shiftKey && e.key?.toLowerCase() === "c") {
    act = () => void panel.copySelection();
  }
  if (!act) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  act();
}

onMounted(() => window.addEventListener("keydown", onChipKeydown, true));
onBeforeUnmount(() =>
  window.removeEventListener("keydown", onChipKeydown, true),
);

function onTagsUpdated(tags) {
  if (props.image) emit("update:image", { ...props.image, tags });
}

function onDescriptionUpdated(imageId, description) {
  if (props.image) emit("update:image", { ...props.image, description });
  emit("update-description", imageId, description);
}

function modelNames(raw) {
  try {
    const list = JSON.parse(raw || "[]");
    return Array.isArray(list) ? list.filter(Boolean) : [];
  } catch (e) {
    console.warn("Unreadable model list on a picture", raw, e);
    return [];
  }
}

// The file name is a property here, never the title: files mostly have
// random names (#2161).
const properties = computed(() => {
  const img = props.image;
  if (!img) return [];
  const rows = [];
  const file =
    img.original_file_name || String(img.file_path || "").split(/[\\/]/).pop();
  if (file) rows.push({ label: "File", value: file });
  if (img.width && img.height) {
    rows.push({ label: "Dimensions", value: `${img.width} × ${img.height}` });
  }
  const size = formatModelSize(img.size_bytes);
  if (size) rows.push({ label: "Size", value: size });
  const models = modelNames(img.comfyui_models);
  if (models.length) rows.push({ label: "Model", value: models.join(", ") });
  if (typeof img.smart_score === "number") {
    rows.push({ label: "Quality", value: img.smart_score.toFixed(2) });
  }
  if (img.created_at) {
    rows.push({
      label: "Created",
      value: formatUserDate(img.created_at, userPrefsStore.dateFormat),
    });
  }
  return rows;
});
</script>

<style scoped>
/* The lightbox's panels ink themselves for its always-dark surface. Here they
   sit on the rail, so each dark-surface ink is handed this theme's own. */
.rail-picture {
  --v-theme-dark-surface: var(--v-theme-sidebar);
  --v-theme-on-dark-surface: var(--v-theme-on-surface);
  --v-theme-dark-surface-error: var(--v-theme-surface-error);
  --v-theme-dark-surface-warning: var(--v-theme-surface-warning);
  --v-theme-dark-surface-success: var(--v-theme-surface-success);
  --v-theme-dark-surface-info: var(--v-theme-surface-info);
  --v-theme-dark-surface-accent: var(--v-theme-accent);
  --v-theme-dark-surface-primary: var(--v-theme-primary);
  --v-theme-dark-surface-tertiary: var(--v-theme-tertiary);
  --v-theme-dark-surface-favourite: var(--v-theme-favourite);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  font-size: var(--text-xs);
}

.rail-picture__preview {
  display: flex;
  justify-content: center;
  height: 160px;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.06);
  overflow: hidden;
}

.rail-picture__preview img {
  max-width: 100%;
  height: 100%;
  object-fit: contain;
}

.rail-picture__strip {
  display: flex;
  justify-content: flex-end;
}

/* The heart is a clickable VIcon, and Vuetify stamps the theme class on it,
   which declares the theme's inks on the icon itself and so undoes the remap
   above. It is inked here in this surface's own terms instead. */
.rail-picture__strip :deep(.star-overlay__heart) {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-picture__strip :deep(.star-overlay__heart--on) {
  color: rgb(var(--v-theme-favourite));
}

.rail-picture__strip :deep(.star-overlay .v-icon:hover) {
  background: var(--hover-wash);
}

/* The lightbox's fields sit on a dark wash over the picture; on the rail they
   are this surface's quiet field, as a stat tile is. */
.rail-picture :deep(.description-editor textarea),
.rail-picture :deep(.picture-text) {
  background: rgba(var(--v-theme-on-surface), 0.05);
}

.rail-picture__section {
  display: flex;
  flex-direction: column;
}

.rail-picture__empty {
  margin: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-faces {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.rail-faces li {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.rail-faces__crop {
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background-color: rgba(var(--v-theme-on-surface), 0.06);
  background-repeat: no-repeat;
}

.rail-picture__props {
  display: grid;
  grid-template-columns: minmax(0, 96px) minmax(0, 1fr);
  gap: var(--space-3);
  margin: 0;
}

.rail-picture__props dt {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-picture__props dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums;
}
</style>
