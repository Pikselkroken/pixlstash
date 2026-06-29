<template>
  <AppDialog
    :open="open"
    :title="set?.id ? 'Edit picture set' : 'New picture set'"
    :width="640"
    @close="emit('close')"
  >
    <div class="editor-body">
      <AppInput
        ref="nameInputRef"
        v-model="localSet.name"
        label="Name *"
        placeholder="Picture set name"
        icon="layers-triple-outline"
        @enter="save"
      />
      <AppTextarea
        v-model="localSet.description"
        label="Description"
        placeholder="Optional description…"
        :rows="2"
      />
      <AppSelect
        v-model="projectSelection"
        label="Project"
        :options="projectOptions"
      />

      <!-- Appearance row -->
      <div class="appearance-row">
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
                @click="localSet.set_icon = ICON_CARDS"
              >
                <img
                  v-if="props.thumbnailUrl"
                  :src="props.thumbnailUrl"
                  class="icon-btn-thumb"
                  alt="Thumbnail"
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
                @click="localSet.set_color = col.value"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
    <template #footer>
      <AppButton variant="secondary" @click="emit('close')">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="check"
        :disabled="!isValid"
        @click="save"
      >
        Save
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
import { computed, ref, watch, nextTick } from "vue";
import { VIcon } from "vuetify/components";
import { apiClient } from "../../utils/apiClient";
import {
  SET_ICONS,
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

const props = defineProps({
  open: { type: Boolean, default: false },
  set: { type: Object, default: null },
  thumbnailUrl: { type: String, default: null },
  backendUrl: { type: String, required: true },
  projects: { type: Array, default: () => [] },
});

// Native <select> carries string values, so map the "no project" sentinel to an
// empty string and back to a null project_id.
const projectOptions = computed(() => [
  { value: "", label: "— No project —" },
  ...props.projects.map((p) => ({ value: String(p.id), label: p.name })),
]);

const projectSelection = computed({
  get: () =>
    localSet.value.project_id == null ? "" : String(localSet.value.project_id),
  set: (v) => {
    localSet.value.project_id = v === "" ? null : Number(v);
  },
});

const emit = defineEmits(["close", "saved", "refresh-sidebar"]);

const localSet = ref({
  id: null,
  name: "",
  description: "",
  project_id: null,
  set_icon: ICON_CARDS,
  set_color: SET_COLORS[0].value,
});

const nameInputRef = ref(null);

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

watch(
  () => props.set,
  (newSet) => {
    if (newSet) {
      localSet.value = {
        id: newSet.id,
        name: newSet.name || "",
        description: newSet.description || "",
        project_id: newSet.project_id ?? null,
        set_icon: newSet.set_icon ?? ICON_CARDS,
        set_color: newSet.set_color ?? SET_COLORS[0].value,
      };
    } else {
      localSet.value = {
        id: null,
        name: "",
        description: "",
        project_id: null,
        set_icon: ICON_CARDS,
        set_color: SET_COLORS[0].value,
      };
    }
  },
  { immediate: true },
);

function save() {
  if (!isValid.value) return;
  saveSetFromEditor({ ...localSet.value });
}

// Keyboard shortcuts
function handleKeydown(event) {
  if (event.key === "Escape") {
    emit("close");
  }
}

async function saveSetFromEditor(setData) {
  try {
    const isNew = !setData.id;
    const url = isNew
      ? `${props.backendUrl}/picture_sets`
      : `${props.backendUrl}/picture_sets/${setData.id}`;

    if (isNew) {
      await apiClient.post(url, setData);
    } else {
      await apiClient.patch(url, setData);
    }

    emit("close");
    emit("refresh-sidebar");
  } catch (e) {
    alert("Failed to save picture set: " + (e.message || e));
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
</script>

<style scoped>
.editor-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

/* Appearance pickers */
.appearance-row {
  display: flex;
  flex-direction: column;
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
  background: transparent;
  cursor: pointer;
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
  grid-template-columns: repeat(8, 1fr);
  column-gap: var(--space-1);
  row-gap: var(--space-1);
  flex: 1;
  min-width: 0;
  max-height: 188px;
  overflow-y: auto;
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
  background: transparent;
  cursor: pointer;
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
}

.color-swatch {
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  border: 2px solid transparent;
  cursor: pointer;
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
