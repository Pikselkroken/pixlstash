<template>
  <!-- One picture, tag-first: who it is (the tile's rating strip, a small
       thumbnail and its faces), the tag field T focuses with its tags and
       rejected tags, then the Description panel, as tall as its text, and
       Properties. The grid rail's one-picture view and the lightbox's Info
       tab are both this; `on-dark` is the lightbox's pane. -->
  <div class="rail-picture" :class="{ 'rail-picture--on-dark': onDark }">
    <!-- In the lightbox (`pinnable`) the faces stand beside the title and
         stars, as tall as them, with no thumbnail: the picture is beside the
         pane. Without faces the title takes the width and "No faces" ends the
         stars' line. -->
    <div
      class="rail-id"
      :class="{
        'rail-id--head': pinnable,
        'rail-id--faceless': pinnable && !faces.length,
      }"
    >
      <!-- Only where nothing above names the picture (the lightbox's band
           holds tabs). No tip: the whole description is a section below.
           Pointing at it marks its words there; a click selects them. -->
      <button
        v-if="title"
        class="rail-picture__title"
        :class="{ 'rail-picture__title--tall': solo }"
        type="button"
        @mouseenter="titleHovered = true"
        @mouseleave="titleHovered = false"
        @focus="titleHovered = true"
        @blur="titleHovered = false"
        @vue:unmounted="titleHovered = false"
        @click="emit('pick-title')"
      >
        {{ title }}
      </button>
      <!-- The same strip as the grid tile: five stars, a hairline, the heart.
           It rates and favourites through the grid's own paths (App.vue), so
           the tile and the rail cannot disagree. -->
      <div class="rail-picture__strip">
        <StarRatingOverlay
          :score="score"
          :readonly="readonly"
          icon-size="18"
          :favouritable="favouritable"
          :favourite="Boolean(card?.favourite)"
          @set-score="emit('set-score', $event)"
          @toggle-favourite="emit('toggle-favourite')"
        />
      </div>
      <img
        v-if="thumbnailUrl && !pinnable"
        class="rail-id__thumb"
        :src="thumbnailUrl"
        alt=""
        draggable="false"
      />
      <div
        class="rail-faces"
        :class="{
          'rail-faces--compact': compact,
          'rail-faces--pinnable': pinnable,
          'rail-faces--solo': solo,
        }"
      >
        <!-- In the lightbox a face's box is on the picture beside it:
             pointing at the crop draws it, clicking keeps it on, and pointing
             at the face on the picture rings the crop here. -->
        <button
          v-for="(face, idx) in shownFaces"
          :key="face.id ?? `face-${idx}`"
          class="rail-face"
          :class="{
            'rail-face--unknown': face.character_id == null,
            'rail-face--ringed':
              pinnable && (ringedFace === idx || pinnedFaces.has(idx)),
          }"
          type="button"
          :aria-label="`Face ${idx + 1}: ${faceName(face)}`"
          :aria-controls="pinnable ? undefined : facesPanelId"
          :aria-pressed="pinnable ? String(pinnedFaces.has(idx)) : undefined"
          @mouseenter="pinnable && emit('hover-face', idx)"
          @mouseleave="pinnable && emit('hover-face', null)"
          @click="pinnable ? emit('pin-face', idx) : openFaces(face.id)"
        >
          <Tooltip v-if="compact" :text="faceName(face)" activator="parent" :describe="false" />
          <span
            class="rail-face__crop"
            :style="{ ...faceCropStyle(face, cropSize), '--ring': faceBoxColor(idx) }"
          ></span>
          <span v-if="!compact && !pinnable" class="rail-face__name">{{
            faceName(face)
          }}</span>
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
        <!-- Edit faces always touches a face or the word "faces": the
             block's last cell, the name under a lone portrait, or "No faces". -->
        <button
          v-if="!isReadOnly"
          ref="pencilRef"
          :class="
            pinnable
              ? {
                  'rail-face-edit': true,
                  'rail-face-edit--cell': !pencilText,
                  'rail-face-edit--on': facesOpen,
                }
              : { 'rail-ib': true, 'rail-ib--on': facesOpen }
          "
          :style="solo ? { '--ring': faceBoxColor(0) } : undefined"
          type="button"
          :aria-label="pencilText ? `${pencilText}: edit faces` : 'Edit faces'"
          :aria-controls="facesPanelId"
          :aria-expanded="facesOpen ? 'true' : 'false'"
          @click="openFaces(null)"
        >
          <Tooltip text="Edit faces" activator="parent" :describe="false" />
          <span
            v-if="pencilText"
            class="rail-face-edit__text"
            :class="{ 'rail-face-edit__text--named': soloNamed }"
            >{{ pencilText }}</span
          >
          <v-icon :size="pencilText ? 12 : 16">mdi-pencil-outline</v-icon>
        </button>
        <span
          v-else-if="pencilText"
          class="rail-face-edit"
          :style="solo ? { '--ring': faceBoxColor(0) } : undefined"
        >
          <span
            class="rail-face-edit__text"
            :class="{ 'rail-face-edit__text--named': soloNamed }"
            >{{ pencilText }}</span
          >
        </span>
      </div>
      <FacesPanel
        v-if="facesOpen && !isReadOnly"
        :id="facesPanelId"
        ref="facesPanelRef"
        class="rail-faces__panel"
        :rows="faceRows"
        :anchor="pencilRef"
        :focus-key="focusFaceId"
        :readonly="isReadOnly"
        :on-dark="onDark"
        :allow-create="allowCreate"
        @close="facesOpen = false"
        @hover="(row) => emit('hover-face', row ? row.idx : null)"
        @assign="(row, payload) => assignFace(row.face, payload)"
        @unassign="(row) => unassignFace(row.face)"
        @create="(row, query) => emit('create-person', row.face, query)"
      >
        <template v-if="$slots['faces-footer']" #footer>
          <slot name="faces-footer"></slot>
        </template>
      </FacesPanel>
    </div>

    <RailTags
      ref="tagsRef"
      :ids="[pictureId]"
      :locked="locked"
      :lock-note="lockNote"
      :takes-tag-key="takesTagKey"
      :boxed-tags="boxedTags"
      @tags-applied="emit('tags-applied', $event)"
      @hover-tag="emit('hover-tag', $event)"
    />

    <!-- The lightbox fills this with its own panel, wired to the picture's
         read text. -->
    <slot name="description" :highlight="descriptionMark">
      <LightboxDescriptionPanel
        ref="descriptionRef"
        class="rail-picture__desc"
        :highlight="descriptionMark"
        :image="image"
        :locked="locked"
        :lock-note="lockNote"
        @update-description="onDescriptionUpdated"
      />
    </slot>

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
            <span
              >{{ row.value
              }}<Tooltip v-if="row.path" :text="row.path" activator="parent"
            /></span>
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
import { getLightboxFormat, isSupportedVideoFile } from "../../utils/media.js";
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
  /** The stars show the rating but take no click (a locked picture). */
  readonly: { type: Boolean, default: false },
  /** The picture's name, where the host's head does not already say it. */
  title: { type: String, default: "" },
  /** The grid's rail: T (`useSidebarStore.requestTagField`) focuses the field. */
  takesTagKey: { type: Boolean, default: true },
  /** Drawn on the lightbox's pane, dark in both themes. */
  onDark: { type: Boolean, default: false },
  /**
   * The lightbox, where the picture's face boxes are beside the rail: a crop
   * reports `hover-face` and `pin-face` rather than opening the panel, and the
   * faces stand beside the title rather than beside a thumbnail.
   */
  pinnable: { type: Boolean, default: false },
  /** Face indices whose box is kept on (`pinnable`). */
  pinnedFaces: { type: Set, default: () => new Set() },
  /** The face index pointed at on the picture (`pinnable`). */
  ringedFace: { type: Number, default: null },
  /** The faces panel offers "new person" (the lightbox owns that dialog). */
  allowCreate: { type: Boolean, default: false },
  /** Lower-cased labels the object detector found (`RailTags`). */
  boxedTags: { type: Set, default: () => new Set() },
  /** A video's runtime in seconds, when the host measured it. */
  videoDuration: { type: Number, default: null },
  /** Words to mark in the description: the host's head title, pointed at. */
  descriptionHighlight: { type: String, default: "" },
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
  "hover-face",
  "pin-face",
  "create-person",
  "hover-tag",
  "pick-title",
]);

const titleHovered = ref(false);
// The description panel marks the title being pointed at, the host's or this.
const descriptionMark = computed(
  () => props.descriptionHighlight || (titleHovered.value ? props.title : ""),
);

/** The faces panel; a host that must veto a close (a dialog over it) binds it. */
const facesOpen = defineModel("facesOpen", { type: Boolean, default: false });

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
// On the rail four or more drop their names, step down and end in a "+N"
// tile. In the lightbox a lone face is a portrait with its name under it, and
// more are a two-row block of nameless crops beside the title: three columns
// of six cells, the last the pencil, so five show and six or more show four
// and a "+N". A read-only session has no faces panel for "+N" to open, so
// there every face shows.
const CROWD = 4;
const HEAD_CELLS = 6;
const solo = computed(() => props.pinnable && props.faces.length === 1);
const compact = computed(() =>
  props.pinnable ? props.faces.length > 1 : props.faces.length >= CROWD,
);
const cropSize = computed(() => {
  if (solo.value) return 64; // px
  return compact.value ? 32 : 42;
});
const shownFaces = computed(() => {
  const faces = props.faces;
  if (props.pinnable) {
    return isReadOnly.value || faces.length < HEAD_CELLS
      ? faces
      : faces.slice(0, HEAD_CELLS - 2);
  }
  return compact.value ? faces.slice(0, CROWD - 1) : faces;
});
const moreFaces = computed(() => props.faces.length - shownFaces.value.length);
// The lightbox's pencil carries words where it has room: the lone face's name,
// or "No faces".
const soloNamed = computed(
  () => solo.value && props.faces[0].character_id != null,
);
const pencilText = computed(() => {
  if (!props.pinnable || props.faces.length > 1) return "";
  return solo.value ? faceName(props.faces[0]) : "No faces";
});

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

const focusFaceId = ref(null);
const pencilRef = ref(null);
const facesPanelRef = ref(null);
const tagsRef = ref(null);
const descriptionRef = ref(null);
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
    idx,
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
    replaceFace(face.id, {
      character_id: characterId,
      character_name: payload?.characterName ?? null,
    });
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
    replaceFace(face.id, { character_id: null, character_name: null });
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

function aspectRatio(width, height) {
  const gcd = (a, b) => (b === 0 ? a : gcd(b, a % b));
  const d = gcd(width, height);
  return `${Math.round(width / d)}:${Math.round(height / d)}`;
}

function formatRuntime(seconds) {
  const total = Math.round(Number(seconds));
  if (!Number.isFinite(total) || total <= 0) return "";
  const pad = (n) => String(n).padStart(2, "0");
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  return h
    ? `${h}:${pad(m)}:${pad(total % 60)}`
    : `${m}:${pad(total % 60)}`;
}

// The file name is a property here, never the title: files mostly have
// random names (#2161).
const properties = computed(() => {
  const img = props.image;
  if (!img) return [];
  // The id first (#1379): what a bug report or a share link names.
  const rows = img.id != null ? [{ label: "ID", value: String(img.id) }] : [];
  const file =
    img.original_file_name || String(img.file_path || "").split(/[\\/]/).pop();
  // Shown in its folder only when there is a stored path to open; the whole
  // path is the name's tooltip.
  if (file) {
    rows.push({
      label: "File",
      value: file,
      folder: !!img.file_path,
      path: img.file_path || "",
    });
  }
  const width = Number(img.width) || 0;
  const height = Number(img.height) || 0;
  if (width && height) {
    rows.push({ label: "Dimensions", value: `${width} × ${height}` });
    rows.push({ label: "Aspect", value: aspectRatio(width, height) });
  }
  const size = formatModelSize(img.size_bytes);
  if (size) rows.push({ label: "Size", value: size });
  const format = getLightboxFormat(img);
  const isVideo = format ? isSupportedVideoFile(format) : false;
  if (format) {
    rows.push({
      label: "Type",
      value: `${isVideo ? "Video" : "Image"} · ${format.toUpperCase()}`,
    });
  }
  // A still image is one frame, which says nothing.
  const frames = Number(img.frame_count) || 0;
  if (frames > 1 || (isVideo && frames > 0)) {
    rows.push({ label: "Frames", value: frames.toLocaleString() });
  }
  const runtime = isVideo
    ? formatRuntime(props.videoDuration || img.duration)
    : "";
  if (runtime) rows.push({ label: "Runtime", value: runtime });
  const models = modelNames(img.comfyui_models);
  if (models.length) rows.push({ label: "Model", value: models.join(", ") });
  const quality =
    typeof img.smart_score === "number" ? img.smart_score : img.smartScore;
  if (typeof quality === "number") {
    rows.push({ label: "Quality", value: quality.toFixed(2) });
  }
  if (img.created_at) {
    rows.push({
      label: "Created",
      value: formatUserDate(img.created_at, userPrefsStore.dateFormat),
    });
  }
  // The set(s) freezing this picture's tags and description.
  const lockedBy = (Array.isArray(img.locked_by_sets) ? img.locked_by_sets : [])
    .map((set) => set?.name)
    .filter(Boolean);
  if (lockedBy.length) rows.push({ label: "Locked by", value: lockedBy.join(", ") });
  return rows;
});

defineExpose({
  /** T: the tag field. */
  focusTags: () => tagsRef.value?.focus(),
  /** The caret is in the tag field. */
  tagsEditing: computed(() => Boolean(tagsRef.value?.editing)),
  /** Ctrl+V: the copied tags onto this picture. */
  pasteTags: () => tagsRef.value?.pasteClipboard(),
  focusPencil: () => pencilRef.value?.focus(),
  /** A row of the open faces panel (back from the new-person dialog). */
  focusFaceRow: (key) => facesPanelRef.value?.focusRow(key),
  /** The head's title was clicked: its words, selected in the description. */
  selectInDescription: (text) => descriptionRef.value?.selectText(text),
});
</script>

<style scoped>
.rail-picture {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  font-size: var(--text-xs);
}

/* The lightbox's Description panel inks itself for its always-dark surface.
   On the rail each dark-surface ink is handed this theme's own. */
.rail-picture:not(.rail-picture--on-dark) {
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
}

/* The lightbox's pane is dark in both themes, so the other way round: every
   ink the rail's parts use is handed its dark-surface twin. Not `tertiary`:
   it is only the confidence chips' fill, already dark enough to carry the
   light label (7.31:1 at full confidence), and the lifted twin painted them
   brighter than the same chips on the rail (#2282). */
.rail-picture--on-dark {
  --v-theme-on-surface: var(--v-theme-on-dark-surface);
  --v-theme-error: var(--v-theme-dark-surface-error);
  --v-theme-surface-error: var(--v-theme-dark-surface-error);
  --v-theme-primary: var(--v-theme-dark-surface-primary);
  --v-theme-accent: var(--v-theme-dark-surface-accent);
  --v-theme-favourite: var(--v-theme-dark-surface-favourite);
  --hover-wash: rgba(var(--v-theme-on-dark-surface), 0.16);
  --active-bar: rgb(var(--v-theme-dark-surface-primary));
  --active-wash: rgba(var(--v-theme-dark-surface-primary), 0.2);
  --rail-line: rgba(var(--v-theme-on-dark-surface), 0.16);
  --rail-field: rgba(var(--v-theme-on-dark-surface), 0.05);
  color: rgb(var(--v-theme-on-dark-surface));
}

.rail-picture__title {
  grid-column: 1 / -1;
  min-width: 0;
  color: inherit;
  text-align: left;
  overflow: hidden;
  font-size: var(--text-md);
  font-weight: var(--weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* The lightbox's head: the title over the stars, and the faces beside both
   as tall as them, so the title gets two lines (three beside a portrait)
   instead of one with an ellipsis. */
.rail-id--head {
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-rows: minmax(0, 1fr) auto;
  row-gap: var(--space-2);
  align-items: start;
}

.rail-id--head .rail-picture__title {
  display: -webkit-box;
  grid-column: 1;
  grid-row: 1;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-height: var(--leading-tight);
  white-space: normal;
}

.rail-id--head .rail-picture__title--tall {
  -webkit-line-clamp: 3;
}

.rail-id--head .rail-picture__strip {
  grid-column: 1;
  grid-row: 2;
}

.rail-id--head .rail-faces {
  grid-column: 2;
  grid-row: 1 / span 2;
}

.rail-id--faceless .rail-picture__title {
  grid-column: 1 / -1;
}

.rail-id--faceless .rail-faces {
  grid-row: 2;
  align-self: center;
}

/* Two rows of crops, as tall as two title lines and the stars. */
.rail-id--head .rail-faces--compact {
  display: grid;
  grid-template-rows: repeat(2, 32px);
  grid-auto-columns: 32px;
  grid-auto-flow: column;
  gap: var(--space-2);
}

/* The name may run a little wider than its portrait before it is cut. */
.rail-faces--solo {
  flex-direction: column;
  gap: var(--space-1);
  min-width: 64px;
  max-width: 96px;
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

/* The same theme class restates the page's inks on every star, so on the
   dark pane they are restated on the icons themselves. */
.rail-picture--on-dark .rail-picture__strip :deep(.v-icon) {
  --v-theme-on-background: var(--v-theme-on-dark-surface);
  --v-theme-on-surface: var(--v-theme-on-dark-surface);
  --v-theme-favourite: var(--v-theme-dark-surface-favourite);
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

/* Its box is drawn on the picture: the same ring, heavier, never a second. */
.rail-faces--pinnable .rail-face:hover .rail-face__crop,
.rail-face--ringed .rail-face__crop {
  box-shadow: 0 0 0 3px var(--ring, transparent);
}

.rail-faces--pinnable .rail-face--unknown:hover .rail-face__crop,
.rail-face--unknown.rail-face--ringed .rail-face__crop {
  box-shadow: none;
  outline-width: 2px;
  outline-color: rgb(var(--v-theme-on-surface));
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
.rail-face-edit:focus-visible,
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

.rail-ib:hover,
.rail-ib--on {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

/* The lightbox's Edit faces: the same quiet fill as the "+N" cell, so it
   reads as part of the faces it edits. */
.rail-face-edit {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  min-width: 0;
  height: var(--control-h-sm);
  padding: 0 var(--space-2) 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font: inherit;
}

button.rail-face-edit {
  cursor: pointer;
}

.rail-face-edit--cell {
  width: 32px;
  height: 32px;
  padding: 0;
}

button.rail-face-edit:hover,
.rail-face-edit--on {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

/* Under a lone portrait: its name and the pencil. */
.rail-faces--solo .rail-face-edit {
  gap: var(--space-1);
  width: 100%;
  height: auto;
  padding: var(--space-1);
  font-size: var(--text-2xs);
  line-height: 1;
}

.rail-face-edit__text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rail-face-edit__text--named {
  color: var(--ring);
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
.rail-picture:not(.rail-picture--on-dark) :deep(.description-editor textarea),
.rail-picture:not(.rail-picture--on-dark) :deep(.picture-text) {
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
