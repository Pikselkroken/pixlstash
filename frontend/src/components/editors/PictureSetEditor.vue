<template>
  <AppDialog
    :open="open"
    :title="isExistingSet ? 'Edit picture set' : 'New picture set'"
    :width="720"
    @close="emit('close')"
  >
    <!-- Two columns rather than one tall stack, so the form stops outgrowing
         the viewport and scrolling its own body. Tabs were the stated
         alternative and were rejected: a field hidden behind a tab is one you
         cannot check before saving, and this form has a single required field
         and a single commit. The columns are a CSS reflow of unchanged source
         order - name/description left, projects/lock right - so tab order is
         exactly the sequence it was single-column: name, description, projects,
         locked. Unlike the person editor this column 2 holds writable controls,
         so that sequence now reads down the left column and then down the
         right, which is column-major and is what the eye does with two columns
         - but it does mean Tab travels bottom-left to top-right once.

         The appearance block does NOT go in a column. Eight 32px icon columns
         plus the scroll gutter, the "or" divider, the thumbnail and the colour
         box have an intrinsic width around 570px; half of any dialog on the
         width ladder is narrower than that, and fitting it would mean cutting
         icon columns. It spans instead. -->
    <div class="editor-body">
      <div class="editor-col">
        <AppInput
          ref="nameInputRef"
          v-model="localSet.name"
          label="Name *"
          placeholder="Picture set name"
          icon="layers-triple-outline"
          :disabled="isLockedSet"
          :title="isLockedSet ? LOCK_REASON : undefined"
          @enter="save"
        />
        <AppTextarea
          v-model="localSet.description"
          label="Description"
          placeholder="Optional description…"
          :rows="2"
          :disabled="isLockedSet"
          :title="isLockedSet ? LOCK_REASON : undefined"
        />
      </div>

      <div class="editor-col">
        <AppSelect
          v-model="projectSelection"
          label="Projects"
          :options="projectOptions"
          :multiple="true"
          :disabled="isLockedSet"
          :title="isLockedSet ? LOCK_REASON : undefined"
        />

        <!-- Locked toggle: the only control that stays active while the set is
             locked, so unticking + Save is the unlock path. -->
        <label class="lock-row">
          <input
            type="checkbox"
            class="lock-row__checkbox"
            :checked="localSet.locked"
            @change="localSet.locked = $event.target.checked"
          />
          <span class="lock-row__body">
            <span class="lock-row__title">Locked</span>
            <span class="lock-row__help">
              Locked sets are read-only: no edits to the set, its pictures'
              tags, or descriptions until unlocked.
            </span>
          </span>
        </label>
      </div>

      <!-- Appearance row -->
      <div
        class="appearance-row editor-span"
        :class="{ 'appearance-row--locked': isLockedSet }"
        :title="isLockedSet ? LOCK_REASON : undefined"
      >
        <FieldLabel>Choose icon or thumbnail &amp; color</FieldLabel>
        <div class="appearance-sections">
          <div class="icon-thumb-box">
            <!-- Icon grid (ICON_CARDS excluded) -->
            <div class="icon-grid">
              <template v-for="cat in SET_ICON_CATEGORIES" :key="cat.label">
                <div class="icon-cat-header">{{ cat.label }}</div>
                <template
                  v-for="ic in cat.icons.filter((i) => i.value !== ICON_CARDS)"
                  :key="ic.value"
                >
                  <button
                    type="button"
                    class="icon-btn"
                    :class="{ selected: localSet.set_icon === ic.value }"
                    :title="ic.label"
                    :disabled="isLockedSet"
                    @click="localSet.set_icon = ic.value"
                  >
                    <v-icon
                      size="20"
                      :color="localSet.set_color || undefined"
                      >{{ ic.value }}</v-icon
                    >
                  </button>
                </template>
              </template>
            </div>
            <!-- or divider -->
            <div class="icon-or-divider">
              <div class="icon-or-line"></div>
              <span class="icon-or-text">or</span>
              <div class="icon-or-line"></div>
            </div>
            <!-- Thumbnail aside -->
            <div class="icon-cards-aside">
              <div class="icon-cat-header">Thumbnail</div>
              <button
                type="button"
                class="icon-btn--cards-large"
                :class="{ selected: localSet.set_icon === ICON_CARDS }"
                title="Thumbnail"
                :disabled="isLockedSet"
                @click="localSet.set_icon = ICON_CARDS"
              >
                <img
                  v-if="props.thumbnailUrl && !thumbnailBroken"
                  :src="props.thumbnailUrl"
                  class="icon-btn-thumb"
                  alt="Thumbnail"
                  @error="thumbnailBroken = true"
                />
                <v-icon
                  v-else
                  size="30"
                  :color="localSet.set_color || undefined"
                  >mdi-layers-triple</v-icon
                >
              </button>
            </div>
          </div>
          <!-- Color box -->
          <div class="color-aside">
            <div class="icon-cat-header">Color</div>
            <div class="color-grid">
              <button
                v-for="col in SET_COLORS"
                :key="col.value"
                type="button"
                class="color-swatch"
                :class="{ selected: localSet.set_color === col.value }"
                :style="{ background: col.value }"
                :title="col.label"
                :disabled="isLockedSet"
                @click="localSet.set_color = col.value"
              />
            </div>
          </div>
        </div>
      </div>

      <!-- Outside the lock wash: the tray is read-only, so a locked set still
           shows what it uses. Keyed on the open count so an adapter attached on
           the shelf in between still shows up here - without the freshness
           resting on the dialog's lazy-mount behaviour, and without the widest
           block in the dialog vanishing from under a leave transition, which is
           what `v-if="props.open"` did. The id comes from the latched local
           copy for the same reason: the host nulls the prop on the way out. -->
      <AdapterTray
        v-if="openCount > 0"
        :key="openCount"
        class="editor-span"
        entity-type="set"
        :entity-id="localSet.id"
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
</template>

<script setup>
import { computed, ref, watch, nextTick, onUnmounted } from "vue";
import { VIcon } from "vuetify/components";
import {
  createPictureSet,
  patchPictureSet,
} from "../../api/pictureSets";
import { useSubmitGuard } from "../../composables/useSubmitGuard";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { getEntityProjectIds } from "../../utils/projectMembership";
import {
  SET_COLORS,
  SET_ICON_CATEGORIES,
  ICON_CARDS,
} from "../../utils/setAppearance";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import AppSelect from "../widgets/AppSelect.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import AdapterTray from "../widgets/AdapterTray.vue";
import { errorDetail } from "../../utils/apiError";

import { API_BASE_URL } from "../../utils/apiClient";
// Failures report through the notice surface instead of a blocking native
// alert() (docs/design/notice-surface.md §1).
const noticeStore = useNoticeStore();

const props = defineProps({
  open: { type: Boolean, default: false },
  set: { type: Object, default: null },
  thumbnailUrl: { type: String, default: null },
  backendUrl: { type: String, default: () => API_BASE_URL },
  projects: { type: Array, default: () => [] },
});

const projectOptions = computed(() =>
  props.projects.map((p) => ({ value: String(p.id), label: p.name })),
);

const projectSelection = computed({
  get: () => localSet.value.project_ids.map(String),
  set: (v) => {
    localSet.value.project_ids = v.map(Number);
  },
});

const emit = defineEmits(["close", "refresh-sidebar"]);

// Tooltip on every field disabled by the lock. The set editor is set-scoped, so
// this reason is about the set (the store's picture-scoped lockReason is for
// the grid/overlay surfaces).
const LOCK_REASON =
  "This set is locked. Untick Locked and save to unlock it, then edit.";

const localSet = ref({
  id: null,
  name: "",
  description: "",
  project_ids: [],
  set_icon: ICON_CARDS,
  set_color: SET_COLORS[0].value,
  locked: false,
});

// The set's persisted locked state gates the fields. Editing the checkbox does
// not flip this - a set only becomes editable after an unlock PATCH round-trips
// and the dialog reopens with the fresh set.
//
// Written by the watcher below rather than computed off the prop, for the same
// reason as `isExistingSet`: `SideBar.closeSetEditor` nulls `set` in the same
// tick it closes the dialog, and a computed would lift the lock wash and
// re-enable every field on a dialog still on screen playing its leave
// transition. `submitSet` reads it too, so it must not flicker mid-save either.
const isLockedSet = ref(false);

// Drives the title and would drive a width branch if this editor had one; both
// columns are populated on create, so it is only the title here.
const isExistingSet = ref(false);

// Bumped on every open, to key the adapter tray (see the template).
const openCount = ref(0);

const nameInputRef = ref(null);

// An empty set's thumbnail URL 404s; hide the <img> on error so the create
// path's mdi-layers-triple fallback shows instead of a broken-image glyph.
const thumbnailBroken = ref(false);
watch(
  () => props.thumbnailUrl,
  () => {
    thumbnailBroken.value = false;
  },
);

const isValid = computed(() => {
  return localSet.value.name && localSet.value.name.trim().length > 0;
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

// Gated on `open`: the host nulls `set` as it closes, and this watcher would
// otherwise blank all four fields, flip the title to "New picture set" and lift
// the lock wash under a dialog that is still visible. The form is only ever
// read while open, so filling it on the way in is the whole contract.
watch(
  () => [props.open, props.set],
  ([isOpen, newSet]) => {
    if (!isOpen) return;
    openCount.value += 1;
    isExistingSet.value = !!newSet?.id;
    isLockedSet.value = !!newSet?.locked;
    if (newSet) {
      localSet.value = {
        id: newSet.id,
        name: newSet.name || "",
        description: newSet.description || "",
        project_ids: getEntityProjectIds(newSet),
        set_icon: newSet.set_icon ?? ICON_CARDS,
        set_color: newSet.set_color ?? SET_COLORS[0].value,
        locked: !!newSet.locked,
      };
    } else {
      localSet.value = {
        id: null,
        name: "",
        description: "",
        project_ids: [],
        set_icon: ICON_CARDS,
        set_color: SET_COLORS[0].value,
        locked: false,
      };
    }
  },
  { immediate: true },
);

async function submitSet() {
  if (!isValid.value) return;
  // A locked set only accepts an unlock: sending the other (unchanged, but
  // disabled) fields would 423 server-side. Send just id + locked so unticking
  // Locked and saving is the unlock path, and saving without unticking is a
  // no-op the server allows.
  if (isLockedSet.value) {
    await saveSetFromEditor({
      id: localSet.value.id,
      locked: localSet.value.locked,
    });
    return;
  }
  await saveSetFromEditor({ ...localSet.value });
}

// One create at a time (#647): the button wears `saving`, and `save` refuses a
// re-entrant call so the name field's Enter cannot slip a second set past it.
const { pending: saving, run: save } = useSubmitGuard(submitSet);

// Keyboard shortcuts
function handleKeydown(event) {
  if (event.key === "Escape") {
    emit("close");
  }
}

async function saveSetFromEditor(setData) {
  try {
    const opts = { baseUrl: props.backendUrl };
    if (setData.id) {
      await patchPictureSet(setData.id, setData, opts);
    } else {
      await createPictureSet(setData, opts);
    }

    emit("close");
    emit("refresh-sidebar");
  } catch (e) {
    console.error("Failed to save picture set", e);
    noticeStore.error(
      `Couldn't save that set. ${errorDetail(e) || e?.message || "Please try again."}`,
      { key: "set-save" },
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
/* `minmax(0, 1fr)` and not a bare `1fr`: a bare track takes its width from the
   widest child, and the project list or a long option label would then push the
   row wider than the dialog instead of wrapping inside it. */
.editor-body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: var(--space-6);
  row-gap: var(--space-5);
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
   the single column the editor has always had; the appearance row's own
   `flex-wrap` keeps handling the narrower cases from there. */
@media (max-width: 720px) {
  .editor-body {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* Locked toggle row */
.lock-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  cursor: pointer;
}

.lock-row__checkbox {
  appearance: none;
  -webkit-appearance: none;
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  margin: 0;
  margin-top: var(--space-1);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition:
    background var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
}

.lock-row__checkbox:checked {
  background: rgb(var(--v-theme-accent));
  border-color: rgb(var(--v-theme-accent));
}

.lock-row__checkbox:checked::after {
  content: "";
  width: 5px;
  height: 9px;
  margin-top: -2px;
  border: solid rgb(var(--v-theme-on-accent));
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.lock-row__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.lock-row__title {
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
  line-height: var(--leading-snug);
}

.lock-row__help {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-body);
}

/* Appearance pickers */
.appearance-row {
  display: flex;
  flex-direction: column;
}

/* Dim the whole appearance block while the set is locked. The individual buttons
   already carry their own :disabled state (per the visual-language disabled
   rule), so this is a lighter wash on top of that - enough to read as inactive
   without dropping the block below legibility. Pointer events stay on the
   container so its lock-reason title still shows on hover. */
.appearance-row--locked {
  opacity: 0.55;
}

.appearance-sections {
  display: flex;
  gap: var(--space-4);
  align-items: stretch;
  /* Fallback for very narrow windows: let the colour box drop below the icon
     box rather than overflow and get clipped. */
  flex-wrap: wrap;
}

.icon-thumb-box {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
  flex: 1;
  min-width: 0;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: rgb(var(--v-theme-input-background));
}

.color-aside {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: rgb(var(--v-theme-input-background));
}

.icon-or-divider {
  display: flex;
  flex-direction: column;
  align-items: center;
  align-self: stretch;
  padding: var(--space-6) var(--space-1);
  gap: var(--space-2);
}

.icon-or-line {
  flex: 1;
  width: 1px;
  background: rgb(var(--v-theme-divider));
}

.icon-or-text {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.5);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  line-height: 1;
}

.icon-cards-aside {
  flex-shrink: 0;
  text-align: center;
}

.icon-btn--cards-large {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-md);
  border: 2px solid transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition:
    border-color var(--dur-1) var(--ease-standard),
    background var(--dur-1) var(--ease-standard);
}

.icon-btn--cards-large:hover {
  background: var(--hover-wash);
}

.icon-btn--cards-large.selected {
  border-color: rgb(var(--v-theme-accent));
  background: var(--active-wash);
}

.icon-grid {
  display: grid;
  /* Eight 32px-button columns need ~270px plus the scroll gutter; the dialog
     is sized 720 wide and the appearance row spans both of its columns, so that
     much is left after the thumbnail and colour asides. The stable gutter
     reserves the scrollbar's width up front so it can't eat into the last
     column when content overflows. */
  grid-template-columns: repeat(8, 1fr);
  column-gap: var(--space-1);
  row-gap: var(--space-1);
  flex: 1;
  min-width: 0;
  max-height: 188px;
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.icon-cat-header {
  grid-column: 1 / -1;
  font-size: var(--text-2xs);
  font-weight: var(--weight-bold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.5);
  padding: var(--space-2) 0 var(--space-1);
  line-height: 1;
}

.icon-btn {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  border: 2px solid transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition:
    border-color var(--dur-1) var(--ease-standard),
    background var(--dur-1) var(--ease-standard);
}

.icon-btn-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: var(--radius-sm);
  display: block;
}

.icon-btn:hover {
  background: var(--hover-wash);
}

.icon-btn.selected {
  border-color: rgb(var(--v-theme-accent));
  background: var(--active-wash);
}

.color-grid {
  display: grid;
  /* Fewer columns → narrower (fits the dialog) and taller, so the colours use
     the vertical space alongside the tall icon grid instead of leaving a gap. */
  grid-template-columns: repeat(4, 30px);
  gap: var(--space-3);
  align-items: start;
  max-height: 168px;
  overflow-y: auto;
  /* The scroll container clips at its padding box, so give the selected/hovered
     swatch's scale(1.1) overhang (~1.5px per side) room instead of clipping
     its border on the edge rows and columns. */
  padding: var(--space-1);
}

.color-swatch {
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  border: 2px solid transparent;
  outline: none;
  padding: 0;
  box-sizing: border-box;
  aspect-ratio: 1 / 1;
  position: relative;
  transition:
    transform var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
}

.color-swatch:hover {
  transform: scale(1.1);
  z-index: 1;
}

.color-swatch.selected {
  border-color: rgb(var(--v-theme-on-surface));
  transform: scale(1.1);
  z-index: 1;
}
</style>
