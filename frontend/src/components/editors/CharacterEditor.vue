<template>
  <AppDialog
    :open="open"
    :title="isExisting ? 'Edit person' : 'New person'"
    :width="isExisting ? 720 : 480"
    @close="emit('close')"
  >
    <!-- Two columns rather than one tall stack, so the form stops outgrowing
         the viewport and scrolling its own body. The columns are a CSS reflow
         of unchanged source order: the form fields are in the first column and
         the reference grid - which picks the thumbnail - in the second, so tab
         order is exactly what it was single-column. Tabs were the stated alternative and were
         rejected - a field hidden behind a tab is a field you cannot check
         before Ctrl+Enter saves. Creating a person has no right column (no
         reference images, no adapters yet), so it stays the narrow one-column
         dialog it has always been. -->
    <div class="editor-body" :class="{ 'editor-body--split': isExisting }">
      <div class="editor-col">
        <AppInput
          ref="nameInputRef"
          v-model="localCharacter.name"
          label="Name *"
          placeholder="Name"
          icon="account-outline"
          @enter="save"
        />
        <AppTextarea
          v-model="localCharacter.description"
          label="Description"
          placeholder="A short description of this person…"
          :rows="3"
        />
        <AppTextarea
          v-model="localCharacter.extra_metadata"
          label="Metadata"
          placeholder="Notes, source, tags…"
          :rows="2"
        />
        <AppSelect
          v-model="projectSelection"
          label="Projects"
          :options="projectOptions"
          :multiple="true"
        />
      </div>
      <div v-if="isExisting" class="editor-col">
        <div class="ref-pictures-section">
          <div class="ref-pictures-header">
            <span :id="refsHeadingId" class="section-label">Reference Images</span>
          </div>
          <p class="ref-pictures-help">
            Automatically selected from the highest-scoring images of this
            person. Click one to use it as this person's thumbnail.
          </p>
          <!-- Named through its heading, or the group is an unlabelled pile of
               images - the same wiring AdapterTray puts on its list, and it
               matters more here now that the block is its own column rather
               than the thing directly under the heading in one stack. -->
          <div
            v-if="referencePictures.length > 0"
            class="ref-pictures-grid"
            role="group"
            :aria-labelledby="refsHeadingId"
          >
            <div
              v-for="(pic, index) in referencePictures"
              :key="pic.id"
              class="ref-picture-item"
            >
              <div class="ref-picture-frame">
                <!-- The picture itself is the control: clicking one makes it
                     this person's thumbnail, which is what the feature asked
                     for. Preview moved onto its own corner button rather than
                     sharing the click - one gesture cannot mean two things. -->
                <button
                  type="button"
                  class="ref-picture-pick"
                  :class="{
                    'ref-picture-pick--selected': isThumbnail(pic),
                  }"
                  :aria-pressed="isThumbnail(pic)"
                  :aria-label="`Use reference image ${index + 1} as the thumbnail`"
                  :title="
                    isThumbnail(pic)
                      ? 'This is the thumbnail - click to go back to the automatic choice'
                      : 'Use this as the thumbnail'
                  "
                  @click="toggleThumbnail(pic)"
                >
                  <img
                    :src="
                      appendShareToken(
                        `${props.backendUrl}/pictures/thumbnails/${pic.id}.webp`,
                      )
                    "
                    class="ref-picture-thumb"
                    alt="Reference image"
                    loading="lazy"
                  />
                  <v-icon
                    v-if="isThumbnail(pic)"
                    class="ref-picture-badge"
                    size="16"
                    >mdi-check-circle</v-icon
                  >
                </button>
                <button
                  type="button"
                  class="ref-picture-zoom"
                  :title="`Preview reference image ${index + 1}`"
                  :aria-label="`Preview reference image ${index + 1}`"
                  @click="previewPic = pic"
                >
                  <v-icon size="14">mdi-magnify</v-icon>
                </button>
              </div>
              <StarRatingOverlay
                :score="pic.score || 0"
                :max="5"
                :compact="true"
              />
            </div>
          </div>
          <p v-else-if="!referencePicturesLoading" class="ref-pictures-empty">
            No reference images yet - add more scored pictures of this person.
          </p>
          <!-- The pin survives changes to this list (it is recomputed from
               scores, so a pinned picture can drop out of it). Without this the
               person keeps a thumbnail the editor shows no mark for and offers
               no way back from - the badge is the only control, and it is not
               on screen. -->
          <p v-if="pinnedPictureIsOffList" class="ref-pictures-help">
            The thumbnail is pinned to a picture that is no longer among these.
            <button type="button" class="ref-pin-reset" @click="clearThumbnail">
              Use the automatic choice
            </button>
          </p>
        </div>
      </div>
      <!-- Keyed on the open count rather than gated on `open`. Vuetify does
           unboot the dialog body once the transition finishes, so the key is
           belt-and-braces for the re-read - the freshness of a list written on
           ANOTHER surface (the shelf) should not rest on that lazy-mount
           behaviour, which is a detail rather than a contract - but unlike
           `v-if` it does NOT unmount the tray as the dialog closes. That is
           what this is really for. `v-if="props.open"` did,
           and since this row spans both columns it took the widest block in the
           dialog out from under a leave transition still playing. Same reason
           the id comes from the latched local copy: the host nulls the prop on
           the way out. Spans both columns: its cards auto-fill at 180px, so a
           full-width row holds three of them instead of one (670px of inner
           width - 720 less the border and the --space-6 padding - against a
           180px minimum track and a --space-3 gap). -->
      <AdapterTray
        v-if="openCount > 0"
        :key="openCount"
        class="editor-span"
        entity-type="character"
        :entity-id="localCharacter.id"
      />
    </div>
    <template #footer>
      <AppButton variant="secondary" @click="emit('close')">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="check"
        :disabled="!isValid"
        :loading="saving"
        @click="save"
      >
        Save
      </AppButton>
    </template>
  </AppDialog>

  <Teleport to="body">
    <div
      v-if="previewPic"
      class="ref-preview-overlay"
      @click="previewPic = null"
    >
      <img
        :src="
          appendShareToken(
            `${props.backendUrl}/pictures/thumbnails/${previewPic.id}.webp`,
          )
        "
        class="ref-preview-img"
        alt="Reference image, enlarged"
        @click="previewPic = null"
      />
    </div>
  </Teleport>
</template>

<script setup>
import { computed, ref, useId, watch, nextTick, onUnmounted } from "vue";
import { API_BASE_URL, appendShareToken } from "../../utils/apiClient";
import {
  createCharacter,
  patchCharacter,
  getReferencePictures,
} from "../../api/characters";
import { listPicturesByIds } from "../../api/pictures";
import { useSubmitGuard } from "../../composables/useSubmitGuard";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { getEntityProjectIds } from "../../utils/projectMembership";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import AppSelect from "../widgets/AppSelect.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";
import AdapterTray from "../widgets/AdapterTray.vue";
import { errorDetail } from "../../utils/apiError";

// Failures report through the notice surface instead of a blocking native
// alert() (docs/design/notice-surface.md §1).
const noticeStore = useNoticeStore();

const props = defineProps({
  open: { type: Boolean, default: false },
  character: { type: Object, default: null },
  backendUrl: { type: String, default: () => API_BASE_URL },
  projects: { type: Array, default: () => [] },
});

// An existing person is the only case with a right column: reference images are
// `v-if`'d on the id and the adapter tray renders nothing without one. Creating
// therefore keeps the 480 single-column dialog rather than a 720 one with an
// empty half. Written by the watcher below rather than computed off the prop -
// see the note there for why the close path must not recompute it.
const isExisting = ref(false);

// Bumped on every open. Keys the adapter tray so it remounts and re-reads each
// time the dialog is opened, without unmounting it as the dialog closes.
const openCount = ref(0);

// Ties the reference grid to its heading (see the template).
const refsHeadingId = useId();

const projectOptions = computed(() =>
  props.projects.map((p) => ({ value: String(p.id), label: p.name })),
);

const projectSelection = computed({
  get: () => localCharacter.value.project_ids.map(String),
  set: (v) => {
    localCharacter.value.project_ids = v.map(Number);
  },
});

const emit = defineEmits(["close", "saved"]);

const localCharacter = ref({
  id: null,
  name: "",
  description: "",
  extra_metadata: "",
  project_ids: [],
  thumbnail_picture_id: null,
});

// What the server had when the form was filled. The PATCH treats an ABSENT
// `thumbnail_picture_id` as "leave the pin alone" and `null` as "clear it", so
// the key is only sent when the user actually picked something - a host that
// hands the editor a character row without the field can then never wipe a pin
// it never showed.
const initialThumbnailPictureId = ref(null);

// One numeric form for the pin, everywhere. `isThumbnail` compared as strings
// while the "did the user pick?" check in `submitCharacter` compares with
// `===`, so a string id from either side would have made the two disagree -
// the badge on and the key suppressed.
function pictureId(pic) {
  const raw = pic?.id ?? pic;
  return raw == null ? null : Number(raw);
}

function isThumbnail(pic) {
  return (
    localCharacter.value.thumbnail_picture_id != null &&
    localCharacter.value.thumbnail_picture_id === pictureId(pic)
  );
}

// True while a pin names a picture the current reference list does not hold -
// including one whose read has not landed yet, which is why the loading flag is
// part of it: the notice must not flash under a grid that is still filling.
const pinnedPictureIsOffList = computed(
  () =>
    localCharacter.value.thumbnail_picture_id != null &&
    !referencePicturesLoading.value &&
    !referencePictures.value.some((pic) => isThumbnail(pic)),
);

function clearThumbnail() {
  localCharacter.value.thumbnail_picture_id = null;
}

// Clicking the current one clears the pin rather than doing nothing: it is the
// only way back to "whichever is best", and it is where a user looks for it.
function toggleThumbnail(pic) {
  localCharacter.value.thumbnail_picture_id = isThumbnail(pic)
    ? null
    : pictureId(pic);
}

const nameInputRef = ref(null);

const referencePictures = ref([]);
const referencePicturesLoading = ref(false);
const previewPic = ref(null);

// The read this fetch answers is two sequential round trips wide, and the
// dialog is a shared, permanently-mounted instance: the user can close it or
// open a different person inside that window, and the late response would then
// paint one person's reference images - their FACE - under another's name. Only
// the newest request may write anything, including the loading flag.
//
// A counter, not a comparison against `props.character.id`: the same person can
// be closed and reopened while the first read is still in flight, and an id
// equal to itself would let that abandoned request through - its late failure
// would then wipe the list the reopened dialog had already filled. Identity of
// the REQUEST is the question, and only a counter answers it.
let referenceRequestId = 0;

async function fetchReferencePictures(characterId, requestId) {
  const superseded = () => requestId !== referenceRequestId;
  referencePicturesLoading.value = true;
  try {
    const refBody = await getReferencePictures(characterId);
    const ids = refBody?.reference_picture_ids ?? [];
    if (!ids.length) {
      if (!superseded()) referencePictures.value = [];
      return;
    }
    const rows = await listPicturesByIds(ids);
    if (superseded()) return;
    const pics = Array.isArray(rows) ? rows : [];
    const picsById = new Map(pics.map((p) => [String(p.id), p]));
    referencePictures.value = ids
      .map((id) => picsById.get(String(id)))
      .filter(Boolean);
  } catch {
    if (!superseded()) referencePictures.value = [];
  } finally {
    // Only the newest request owns the flag: an older one clearing it would
    // drop a mid-fetch person to `[]` + not-loading, which renders as "No
    // reference images yet". A closing dialog starts no new request, so this
    // one is still the newest and does clear it - the flag cannot stick.
    if (!superseded()) referencePicturesLoading.value = false;
  }
}

// One watcher over `[open, id]` owns both the layout branch and the reference
// list, because both answer the same question and a second copy of the source
// would only drift.
//
// It does nothing on the way OUT, and that is the point. The hosts null
// `character` in the same tick they set `open` false
// (`SideBar.closeCharacterEditor`) while Vuetify keeps the body mounted for the
// leave transition, so anything recomputed here plays out on screen: the dialog
// would snap 720 → 480 and lose its right column, and emptying the list would
// put "No reference images yet" under a person who has them, for the length of
// the animation. Leave the closing dialog exactly as the user last saw it; the
// next open recomputes everything before it is visible.
watch(
  () => [props.open, props.character?.id],
  ([isOpen, charId]) => {
    // The preview is the one exception to "change nothing on the way out", and
    // it is an exception because it is not part of the dialog: it is teleported
    // to <body> at z-index 9999 and covers the whole app. Left up, it outlives
    // the dialog that owned it - Ctrl+Enter saves and closes from underneath an
    // open preview, and the Escape that would dismiss it goes with the dialog's
    // own listener, so the scrim strands with only a mouse click to clear it.
    // Dropped on both edges: out, so nothing is orphaned; in, so a preview that
    // somehow survives cannot greet the next person.
    previewPic.value = null;
    if (!isOpen) return;
    openCount.value += 1;
    isExisting.value = !!charId;
    // Every open invalidates whatever was in flight, the create path included -
    // otherwise the previous person's read stays "newest" and writes under a
    // dialog that has moved on.
    const requestId = ++referenceRequestId;
    // Cleared on the way IN, so the previous person's thumbnails are not what
    // fills the new one's column while its own read is in flight. What keeps a
    // late response off the wrong person is that counter; this is about the gap
    // before any response, and both are needed.
    referencePictures.value = [];
    if (charId) fetchReferencePictures(charId, requestId);
  },
  { immediate: true },
);

const isValid = computed(() => {
  return (
    localCharacter.value.name && localCharacter.value.name.trim().length > 0
  );
});

// Focus and select the name field when dialog opens
watch(
  () => props.open,
  async (isOpen) => {
    if (isOpen) {
      await nextTick();
      nameInputRef.value?.focus?.();
      nameInputRef.value?.select?.();
    }
  },
);

// Also gated on `open`, for the same reason as the watcher above: the hosts
// null `character` as they close, and this one would blank all four fields
// under a dialog that is still on screen playing its leave transition. The
// form's contents are only ever read while it is open, so filling it on the way
// in is the whole contract.
watch(
  () => [props.open, props.character],
  ([isOpen, newChar]) => {
    if (!isOpen) return;
    if (newChar) {
      localCharacter.value = {
        id: newChar.id,
        name: newChar.name || "",
        description: newChar.description || "",
        extra_metadata: newChar.extra_metadata || "",
        project_ids: getEntityProjectIds(newChar),
        thumbnail_picture_id: pictureId(newChar.thumbnail_picture_id),
      };
    } else {
      localCharacter.value = {
        id: null,
        name: "",
        description: "",
        extra_metadata: "",
        project_ids: [],
        thumbnail_picture_id: null,
      };
    }
    initialThumbnailPictureId.value = localCharacter.value.thumbnail_picture_id;
  },
  { immediate: true },
);

async function submitCharacter() {
  if (!isValid.value) {
    console.error("Character data is not valid. Cannot save.");
    return;
  }

  const payload = { ...localCharacter.value };
  if (payload.thumbnail_picture_id === initialThumbnailPictureId.value) {
    delete payload.thumbnail_picture_id;
  }
  await saveCharacter(payload);
}

// One create at a time (#647). The button wears `saving`, and `save` refuses a
// re-entrant call, which is what covers the two keyboard doors into this form:
// Enter on the name field and the Ctrl+Enter listener below.
const { pending: saving, run: save } = useSubmitGuard(submitCharacter);

// Keyboard shortcuts
function handleKeydown(event) {
  if (event.key === "Escape") {
    if (previewPic.value) {
      previewPic.value = null;
      return;
    }
    emit("close");
  } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    // Ctrl+Enter or Cmd+Enter to save (avoid interfering with textarea)
    event.preventDefault();
    save();
  }
}

async function saveCharacter(charData) {
  try {
    const opts = { baseUrl: props.backendUrl };
    let envelope;
    if (charData.id) {
      envelope = await patchCharacter(charData.id, charData, opts);
    } else {
      envelope = await createCharacter(charData, opts);
    }
    // BOTH routes answer with `CharacterMutationResponse`, i.e. `{status,
    // character}`, so the record, and the server-assigned id on create, is
    // nested under `.character` (pixlstash/routes/characters.py: the POST at
    // ~1240 and the PATCH at ~589 share the model). The api module returns the
    // whole body by its documented convention, so the unwrap belongs here, at
    // the one place both paths pass through.
    //
    // Read the documented location and nothing else. A `?? envelope` style
    // fallback would "work" against either shape and is precisely what hid the
    // create-and-assign bug: every host read `.id` off the envelope, got
    // undefined, and fell into its own guard.
    const saved = envelope?.character ?? null;
    if (!saved?.id) {
      // The write itself succeeded; the response just did not carry the record,
      // so nothing downstream can chain off it. Report it rather than emitting
      // a payload that breaks `saved`'s contract.
      console.error(
        "Character mutation response carried no character record. Expected " +
          "CharacterMutationResponse {status, character:{id,...}}.",
        {
          operation: charData.id ? "patch" : "create",
          characterId: charData.id ?? null,
          receivedKeys:
            envelope && typeof envelope === "object"
              ? Object.keys(envelope)
              : typeof envelope,
        },
      );
      noticeStore.error(
        "That person was saved, but the server did not return the record, so the follow-up steps were skipped.",
        { key: "character-save" },
      );
      return;
    }
    // Carries the saved RECORD (with the server-assigned id on create) so hosts
    // can chain follow-up work, e.g. the create-and-assign flows. Existing
    // listeners that ignore the payload are unaffected.
    emit("saved", saved);
  } catch (e) {
    console.error("Failed to save character", e);
    noticeStore.error(
      `Couldn't save that person. ${errorDetail(e) || e?.message || "Please try again."}`,
      { key: "character-save" },
    );
  }
}

// Add/remove keyboard listener when dialog opens/closes
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeydown);
    } else {
      document.removeEventListener("keydown", handleKeydown);
    }
  },
);

// Guard against leaking the listener if the component unmounts while open.
onUnmounted(() => document.removeEventListener("keydown", handleKeydown));
</script>

<style scoped>
/* One column by default (create), two when there is a right column to fill.
   `minmax(0, 1fr)` and not a bare `1fr`: a bare track takes its min-content
   width from the widest child, and a long project name or a fixed-width
   control would then push the row wider than the dialog. */
.editor-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  column-gap: var(--space-6);
  row-gap: var(--space-5);
}

.editor-body--split {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.editor-col {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-width: 0;
}

.editor-span {
  grid-column: 1 / -1;
}

/* Vuetify caps the dialog at `calc(100% - 48px)`, so it stops being 720 wide
   below a 768px viewport and each column falls under the ~300px the fields
   want (299px at a 720px viewport). 720 is where that is unambiguous. Drop to
   the single column the editor has always had - and give the reference block
   back the rule that separates it from the fields stacked above it there. */
@media (max-width: 720px) {
  .editor-body--split {
    grid-template-columns: minmax(0, 1fr);
  }

  .ref-pictures-section {
    padding-top: var(--space-2);
    border-top: 1px solid rgba(var(--v-theme-border, 127 127 127), 0.25);
  }
}

/* No border-top in the two-column layout: the rule earned its place under a
   stack of fields, and at the top of its own column it is a hairline with
   nothing above it, aligning with nothing on the other side. */
.ref-pictures-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.ref-pictures-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* --text-xs, not --text-sm: this hint sits under a `.section-label` heading at
   --text-2xs, and it was a full two ramp steps above it. --text-xs over
   --text-2xs is the pairing AdapterTray already uses for exactly this - one
   step, with the heading carrying its rank on case, weight and tracking.
   Alpha 0.7 for the same reason AdapterTray's lines carry it: at 12px this is
   SMALL text and owes 4.5:1 (visual-language.md §4). It shipped at 0.5, which
   measures 3.19:1 light; 0.7 measures 5.94:1. Shrinking a line without
   re-checking its contrast is how that floor gets missed. */
.ref-pictures-help {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin: 0;
  font-style: italic;
  line-height: 1.4;
}

/* --text-xs and alpha 0.7 for the same reasons as the hint above it: these two
   render adjacently when the list is empty, and 0.4 measured 2.43:1 - the
   quietest thing in the dialog was the one line explaining why it is empty. */
.ref-pictures-empty {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-style: italic;
  margin: 0;
}

.ref-pictures-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.ref-picture-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
}

.ref-picture-frame {
  position: relative;
  line-height: 0;
}

/* The picture is the button, so the button is only the picture: no padding, no
   chrome of its own, and the same 80px box the bare <img> used to occupy. */
.ref-picture-pick {
  position: relative;
  display: block;
  padding: 0;
  border: none;
  background: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.ref-picture-pick:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* The selected vocabulary from §11 of the design manual, in the form ImageGrid
   already uses for a selected picture: an --active-bar edge. `outline` rather
   than a border so the 80px box does not resize as the pin moves. */
.ref-picture-pick--selected .ref-picture-thumb {
  outline: var(--space-1) solid var(--active-bar);
  /* Fully inside the 80px box, so the edge cannot overlap the neighbouring
     thumbnail: the offset is the outline's own width, negated. */
  outline-offset: calc(-1 * var(--space-1));
}

.ref-picture-thumb {
  width: 80px;
  height: 80px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-surface-variant, 127 127 127), 0.15);
  cursor: pointer;
  display: block;
}

/* Bottom-right corner, over the image: the mark that says WHICH one is the
   thumbnail, so the edge is not the only carrier of the state. */
.ref-picture-badge {
  position: absolute;
  right: var(--space-1);
  bottom: var(--space-1);
  color: var(--active-bar);
  background: rgba(var(--v-theme-surface, 255 255 255), 0.9);
  border-radius: var(--radius-pill);
  pointer-events: none;
}

.ref-picture-zoom {
  position: absolute;
  top: var(--space-1);
  right: var(--space-1);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-surface, 255 255 255), 0.82);
  opacity: 0;
  cursor: zoom-in;
  transition: opacity var(--dur-2) var(--ease-standard);
}

/* Revealed on hover, and always for the keyboard - an affordance that only
   exists under a pointer is one a keyboard user cannot reach. */
.ref-picture-frame:hover .ref-picture-zoom,
.ref-picture-zoom:focus-visible {
  opacity: 1;
}

/* A touch device has no hover, so the same rule would leave an invisible button
   sitting over the corner of every thumbnail, swallowing the tap meant to pin
   it. Show it there instead. */
@media (hover: none) {
  .ref-picture-zoom {
    opacity: 1;
  }
}

/* A link in a sentence, not a button in a row: it appears only in the recovery
   case above, where it is one word of the explanation. */
.ref-pin-reset {
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  color: rgb(var(--v-theme-accent));
  text-decoration: underline;
  cursor: pointer;
}

.ref-pin-reset:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  border-radius: var(--radius-sm);
}

.ref-picture-zoom:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.ref-preview-overlay {
  position: fixed;
  /* Below the desktop title bar (0px in a browser) so the window controls stay
     usable; the preview image centres within the reduced box. */
  inset: var(--titlebar-h) 0 0 0;
  z-index: 9999;
  background: rgba(var(--v-theme-scrim), 0.82);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: zoom-out;
}

.ref-preview-img {
  max-width: 90vw;
  max-height: 90vh;
  object-fit: contain;
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-4);
  cursor: default;
}
</style>
