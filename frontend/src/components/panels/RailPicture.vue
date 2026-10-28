<template>
  <!-- One selected picture in the grid's rail, tag-first: who it is (the
       tile's rating strip, a small thumbnail and its faces), the tag field T
       focuses with its tags and rejected tags, then the lightbox's own
       Description panel, which takes the room left, and Properties. The grid
       already shows the picture big; the rail only says which one it is. -->
  <div class="rail-picture">
    <div class="rail-id">
      <!-- The same strip as the grid tile: five stars, a hairline, the heart.
           It rates and favourites through the grid's own paths (App.vue), so
           the tile and the rail cannot disagree. -->
      <div class="rail-picture__strip">
        <StarRatingOverlay
          :score="score"
          icon-size="18"
          :favouritable="favouritable"
          :favourite="Boolean(card?.favourite)"
          @set-score="emit('set-score', $event)"
          @toggle-favourite="emit('toggle-favourite')"
        />
      </div>
      <img
        v-if="thumbnailUrl"
        class="rail-id__thumb"
        :src="thumbnailUrl"
        alt=""
        draggable="false"
      />
      <div class="rail-faces" :class="{ 'rail-faces--compact': compact }">
        <button
          v-for="(face, idx) in shownFaces"
          :key="face.id ?? `face-${idx}`"
          class="rail-face"
          :class="{ 'rail-face--unknown': face.character_id == null }"
          type="button"
          :aria-label="`Face ${idx + 1}: ${faceName(face)}`"
          :aria-controls="facesPanelId"
          @click="openFaces(face.id)"
        >
          <Tooltip v-if="compact" :text="faceName(face)" activator="parent" :describe="false" />
          <span
            class="rail-face__crop"
            :style="{ ...faceCropStyle(face, cropSize), '--ring': faceBoxColor(idx) }"
          ></span>
          <span v-if="!compact" class="rail-face__name">{{ faceName(face) }}</span>
        </button>
        <button
          v-if="moreFaces"
          class="rail-face"
          type="button"
          :aria-label="`${moreFaces} more faces`"
          :aria-controls="facesPanelId"
          @click="openFaces(null)"
        >
          <span class="rail-face__crop rail-face__more">+{{ moreFaces }}</span>
        </button>
        <button
          v-if="!isReadOnly"
          ref="pencilRef"
          class="rail-ib"
          type="button"
          aria-label="Edit faces"
          :aria-controls="facesPanelId"
          :aria-expanded="facesOpen ? 'true' : 'false'"
          @click="openFaces(null)"
        >
          <Tooltip text="Edit faces" activator="parent" :describe="false" />
          <v-icon size="16">mdi-pencil-outline</v-icon>
        </button>
      </div>
      <FacesPanel
        v-if="facesOpen"
        :id="facesPanelId"
        class="rail-faces__panel"
        :rows="faceRows"
        :anchor="pencilRef"
        :focus-key="focusFaceId"
        :readonly="isReadOnly"
        @close="facesOpen = false"
        @assign="(row, payload) => assignFace(row.face, payload)"
        @unassign="(row) => unassignFace(row.face)"
      />
    </div>

    <RailTags
      :ids="[pictureId]"
      :locked="locked"
      :lock-note="lockNote"
      takes-tag-key
      @tags-applied="emit('tags-applied', $event)"
    />

    <LightboxDescriptionPanel
      class="rail-picture__desc"
      :image="image"
      :locked="locked"
      :lock-note="lockNote"
      @update-description="onDescriptionUpdated"
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
          <dd v-if="row.folder && !isReadOnly" class="rail-picture__file">
            <span>{{ row.value }}</span>
            <button
              type="button"
              class="rail-picture__folder"
              aria-label="Show in folder"
              @click="showInFolder"
            >
              <Tooltip text="Show in folder" activator="parent" />
              <v-icon size="16">mdi-folder-open-outline</v-icon>
            </button>
          </dd>
          <dd v-else>{{ row.value }}</dd>
        </template>
      </dl>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, useId, watch } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import {
  openPictureLocation,
  pictureThumbnailUrl,
} from "../../api/pictures";
import {
  addCharacterFacesByFaceId,
  removeCharacterFacesByFaceId,
} from "../../api/characters";
import { faceBoxColor, formatUserDate } from "../../utils/utils.js";
import { formatModelSize } from "../../utils/modelShelf.js";
import { errorDetail } from "../../utils/apiError";
import { useSectionCollapse } from "../../composables/useSectionCollapse";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import FacesPanel from "../widgets/FacesPanel.vue";
import SectionHeader from "../widgets/SectionHeader.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import Tooltip from "../widgets/Tooltip.vue";
import LightboxDescriptionPanel from "../views/LightboxDescriptionPanel.vue";
import RailTags from "./RailTags.vue";

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
  "tags-applied",
  "update-description",
  "lightbox-change",
  "update:image",
  "update:faces",
  "refresh",
]);

const namesStore = useEntityNamesStore();
const lockedSetsStore = useLockedSetsStore();
const noticeStore = useNoticeStore();
const userPrefsStore = useUserPrefsStore();

const propertiesCollapsed = useSectionCollapse("properties", {
  defaultCollapsed: true,
});

const locked = computed(() => lockedSetsStore.isLocked(props.pictureId));
const lockNote = computed(() => lockedSetsStore.lockReason(props.pictureId));

const thumbnailUrl = computed(
  () => props.card?.thumbnail || pictureThumbnailUrl(props.pictureId),
);

function personName(characterId) {
  if (characterId == null) return "";
  return namesStore.characterNames[characterId] || `Person ${characterId}`;
}

const faceName = (face) => personName(face.character_id) || "Unknown";

// ── Faces ────────────────────────────────────────────────────────────────────
// Four or more drop their names, step down and end in a "+N" tile.
const CROWD = 4;
const compact = computed(() => props.faces.length >= CROWD);
const cropSize = computed(() => (compact.value ? 32 : 42)); // px
const shownFaces = computed(() =>
  compact.value ? props.faces.slice(0, CROWD - 1) : props.faces,
);
const moreFaces = computed(() => props.faces.length - shownFaces.value.length);

// The face, cut from the thumbnail: it keeps the picture's aspect, so the
// detector's full-size box scales onto it.
function faceCropStyle(face, size) {
  const width = Number(props.image?.width || props.card?.width || 0);
  const height = Number(props.image?.height || props.card?.height || 0);
  const box = { width: `${size}px`, height: `${size}px` };
  if (!width || !height || !thumbnailUrl.value) return box;
  // Clamped to the picture first: a detector box can run past its edges.
  const clampX = (v) => Math.max(0, Math.min(Number(v) || 0, width));
  const clampY = (v) => Math.max(0, Math.min(Number(v) || 0, height));
  const [x1, y1, x2, y2] = [
    clampX(face.bbox[0]),
    clampY(face.bbox[1]),
    clampX(face.bbox[2]),
    clampY(face.bbox[3]),
  ];
  // The face covers the square box: scaled by its shorter side and centred,
  // so the longer side is trimmed rather than the box left part-empty.
  const faceW = Math.max(1, x2 - x1);
  const faceH = Math.max(1, y2 - y1);
  const scale = size / Math.min(faceW, faceH);
  const left = x1 * scale - (size - faceW * scale) / 2;
  const top = y1 * scale - (size - faceH * scale) / 2;
  return {
    ...box,
    backgroundImage: `url(${thumbnailUrl.value})`,
    backgroundSize: `${Math.round(width * scale)}px ${Math.round(height * scale)}px`,
    backgroundPosition: `${Math.round(-left)}px ${Math.round(-top)}px`,
  };
}

const facesOpen = ref(false);
const focusFaceId = ref(null);
const pencilRef = ref(null);
const facesPanelId = useId();

const faceRows = computed(() =>
  props.faces.map((face, idx) => ({
    key: face.id ?? `row-${idx}`,
    id: face.id,
    characterId: face.character_id,
    name: personName(face.character_id),
    label: `Face ${idx + 1}`,
    hue: faceBoxColor(idx),
    cropStyle: faceCropStyle(face, 36),
    face,
  })),
);

/** A face (its row in focus) or the edit button: the one faces panel. */
function openFaces(faceId) {
  if (facesOpen.value && faceId === focusFaceId.value) {
    facesOpen.value = false;
    return;
  }
  focusFaceId.value = faceId;
  facesOpen.value = true;
}

watch(
  () => props.pictureId,
  () => {
    facesOpen.value = false;
  },
);
function replaceFace(faceId, patch) {
  emit(
    "update:faces",
    props.faces.map((f) => (f.id === faceId ? { ...f, ...patch } : f)),
  );
  emit("lightbox-change", {
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

// The folder opens on the machine running PixlStash, so the reason a press
// did nothing is worth stating: 403 is the app opened from another machine
// (the route is loopback-only), 404 a file gone since it was imported.
async function showInFolder() {
  try {
    await openPictureLocation(props.pictureId);
  } catch (err) {
    const status = err?.response?.status;
    console.warn(`Failed to show picture ${props.pictureId} in its folder`, err);
    noticeStore.warning(
      status === 403
        ? "A file manager opens on the machine running PixlStash, so this only works when you are sitting at it."
        : status === 404
          ? "That file is not where PixlStash last saw it."
          : "Couldn't open that folder - the machine running PixlStash has no desktop file manager.",
      { key: "rail-show-in-folder" },
    );
  }
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
  // Shown in its folder only when there is a stored path to open.
  if (file) rows.push({ label: "File", value: file, folder: !!img.file_path });
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
/* The lightbox's Description panel inks itself for its always-dark surface.
   Here it sits on the rail, so each dark-surface ink is handed this theme's
   own. */
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
  /* At least the rail's height: the Description panel grows into what is
     left, so a long caption reads whole on a tall window. */
  min-height: 100%;
  font-size: var(--text-xs);
}

/* Identity: the rating on the first line, full width; the thumbnail and its
   faces on the second, so neither line leaves the rail's right side empty. */
.rail-id {
  position: relative;
  display: grid;
  grid-template-columns: 60px minmax(0, 1fr);
  column-gap: var(--space-4);
  row-gap: var(--space-3);
  align-items: center;
}

.rail-picture__strip {
  grid-column: 1 / -1;
  display: flex;
}

.rail-id__thumb {
  width: 60px;
  height: 60px;
  border-radius: var(--radius-sm);
  object-fit: cover;
  object-position: top center;
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

.rail-faces {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  grid-column: 2;
}

/* A face is a picture with its name under it, as in the lightbox's face
   grid: a crop ringed in the hue the lightbox draws round that face. */
.rail-face {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  min-width: 44px;
  max-width: 64px;
  padding: 0;
  border: 0;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  cursor: pointer;
}

.rail-faces--compact .rail-face {
  min-width: 32px;
}

.rail-face__crop {
  flex: none;
  border-radius: var(--radius-sm);
  background-color: rgba(var(--v-theme-on-surface), 0.06);
  background-repeat: no-repeat;
  box-shadow: 0 0 0 2px var(--ring, transparent);
}

.rail-face--unknown .rail-face__crop {
  box-shadow: none;
  outline: 1px dashed
    rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  outline-offset: 1px;
}

.rail-face__name {
  max-width: 64px;
  overflow: hidden;
  font-size: var(--text-2xs);
  line-height: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rail-face--unknown .rail-face__name {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-face__more {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-face:focus-visible,
.rail-ib:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
}

.rail-ib {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: var(--control-h);
  height: var(--control-h);
  margin-left: calc(-1 * var(--space-2));
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
}

.rail-ib:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.rail-faces__panel {
  position: absolute;
  top: calc(100% + var(--space-3));
  left: 0;
  right: 0;
  z-index: var(--z-dropdown);
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

.rail-picture__file {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}

.rail-picture__file > span {
  flex: 1;
  min-width: 0;
}

/* Quiet until hovered, as a bar button is: the file name is the content. */
.rail-picture__folder {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
}

.rail-picture__folder:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}
</style>
