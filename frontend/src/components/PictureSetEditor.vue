<template>
  <v-dialog :model-value="open" max-width="600" @click:outside="emit('close')">
    <div class="editor-shell">
      <v-btn icon size="36px" class="close-icon" @click="emit('close')">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="editor-card">
        <v-card-title class="editor-header">
          {{ set?.id ? "Edit Picture Set" : "New Picture Set" }}
        </v-card-title>
        <v-card-text class="editor-body">
          <v-text-field
            ref="nameInputRef"
            v-model="localSet.name"
            label="Name *"
            placeholder="Picture set name"
            density="comfortable"
            variant="filled"
            @keydown.enter="save"
          />
          <v-textarea
            v-model="localSet.description"
            label="Description"
            placeholder="Optional description"
            density="comfortable"
            variant="filled"
            rows="4"
            @keydown.ctrl.enter="save"
            @keydown.meta.enter="save"
          />
          <v-select
            v-model="localSet.project_id"
            :items="projectItems"
            item-title="name"
            item-value="id"
            label="Project"
            density="comfortable"
            variant="filled"
            clearable
            clear-icon="mdi-close"
          />

          <!-- Appearance row -->
          <div class="appearance-row">
            <div class="appearance-joint-header">Choose Icon or Thumbnail &amp; Color</div>
            <div class="appearance-sections">
              <div class="icon-thumb-box">
                <!-- Icon grid (ICON_CARDS excluded) -->
                <div class="icon-grid">
                <template v-for="cat in SET_ICON_CATEGORIES" :key="cat.label">
                  <div class="icon-cat-header">{{ cat.label }}</div>
                  <template v-for="ic in cat.icons.filter(i => i.value !== ICON_CARDS)" :key="ic.value">
                    <button
                      class="icon-btn"
                      :class="{ selected: localSet.set_icon === ic.value }"
                      :title="ic.label"
                      @click="localSet.set_icon = ic.value"
                    >
                      <v-icon size="22" :color="localSet.set_color || undefined">{{ ic.value }}</v-icon>
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
                  <v-icon v-else size="32" :color="localSet.set_color || undefined">mdi-layers-triple</v-icon>
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
        </v-card-text>
        <v-card-actions class="editor-footer">
          <v-spacer></v-spacer>
          <v-btn class="btn-cancel" @click="emit('close')">Cancel</v-btn>
          <v-btn class="btn-save" @click="save" :disabled="!isValid">
            Save
          </v-btn>
        </v-card-actions>
      </v-card>
    </div>
  </v-dialog>
</template>

<script setup>
import { computed, ref, watch, nextTick } from "vue";
import {
  VBtn,
  VCard,
  VCardActions,
  VCardText,
  VCardTitle,
  VDialog,
  VIcon,
  VSelect,
  VSpacer,
  VTextField,
  VTextarea,
} from "vuetify/components";
import { apiClient } from "../utils/apiClient";
import { SET_ICONS, SET_COLORS, SET_ICON_CATEGORIES, ICON_CARDS } from "../utils/setAppearance";

const props = defineProps({
  open: { type: Boolean, default: false },
  set: { type: Object, default: null },
  thumbnailUrl: { type: String, default: null },
  backendUrl: { type: String, required: true },
  projects: { type: Array, default: () => [] },
});

const projectItems = computed(() => [
  { id: null, name: "— No project —" },
  ...props.projects,
]);

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
      if (nameInputRef.value?.focus) {
        nameInputRef.value.focus();
      }
      const inputEl = nameInputRef.value?.$el?.querySelector("input");
      if (inputEl) {
        inputEl.select();
      }
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
.editor-shell {
  position: relative;
  width: 100%;
}

.editor-card {
  overflow: hidden;
}

.close-icon {
  position: absolute;
  top: -16px;
  right: -16px;
  background-color: rgb(var(--v-theme-primary));
  border: none;
  color: rgb(var(--v-theme-on-primary));
  cursor: pointer;
  z-index: 2;
}

.close-icon:hover {
  background-color: rgb(var(--v-theme-accent));
}

.editor-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 8px 16px 16px;
}

/* Appearance pickers */
.appearance-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.appearance-joint-header {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  opacity: 0.55;
  line-height: 1;
}

.appearance-sections {
  display: flex;
  gap: 8px;
  align-items: stretch;
}

.icon-thumb-box {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 4px 8px 8px;
  background: rgba(0, 0, 0, 0.12);
}

.color-aside {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 4px 8px 8px;
  background: rgba(0, 0, 0, 0.12);
}

.appearance-label {
  font-size: 0.75rem;
  opacity: 0.7;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.icon-or-divider {
  display: flex;
  flex-direction: column;
  align-items: center;
  align-self: stretch;
  padding: 30px 2px;
  gap: 4px;
}

.icon-or-line {
  flex: 1;
  width: 1px;
  background: rgba(255, 255, 255, 0.12);
}

.icon-or-text {
  font-size: 0.6rem;
  opacity: 0.35;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  line-height: 1;
}

.icon-section-wrap {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.icon-cards-aside {
  flex-shrink: 0;
  text-align: center;
}

.icon-btn--cards-large {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  border: 2px solid transparent;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition: border-color 0.15s;
}

.icon-btn--cards-large:hover {
  background: rgba(255, 255, 255, 0.08);
}

.icon-btn--cards-large.selected {
  border-color: rgba(255, 255, 255, 0.7);
  background: rgba(255, 255, 255, 0.10);
}

.icon-grid {
  display: grid;
  grid-template-columns: repeat(8, 32px);
  column-gap: 1px;
  row-gap: 2px;
}

.icon-cat-header {
  grid-column: 1 / -1;
  font-size: 0.58rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  opacity: 0.45;
  padding: 5px 0 2px;
  line-height: 1;
}

.icon-btn {
  width: 32px;
  height: 32px;
  border-radius: 5px;
  border: 2px solid transparent;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition: border-color 0.15s;
}

.icon-btn-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 3px;
  display: block;
}

.icon-btn:hover {
  background: rgba(255, 255, 255, 0.08);
}

.icon-btn.selected {
  border-color: rgba(255, 255, 255, 0.7);
  background: rgba(255, 255, 255, 0.10);
}

.color-section-header {
  display: none;
}

.color-grid {
  display: grid;
  grid-template-columns: repeat(6, 36px);
  gap: 8px;
  align-items: start;
  margin-top: 2px;
}

.color-swatch {
  width: 36px;
  height: 36px;
  border-radius: 6px;
  border: 2px solid transparent;
  cursor: pointer;
  outline: none;
  padding: 0;
  box-sizing: border-box;
  aspect-ratio: 1 / 1;
  position: relative;
  transition: transform 0.12s, border-color 0.12s;
}

.color-swatch:hover {
  transform: scale(1.1);
  z-index: 1;
}

.color-swatch.selected {
  border-color: #fff;
  transform: scale(1.1);
  z-index: 1;
}

.btn {
  padding: 10px 24px;
  border: none;
  border-radius: 4px;
  font-size: 1rem;
  cursor: pointer;
  transition: all 0.2s;
  font-weight: 500;
}

.btn:hover {
  filter: brightness(1.2);
}

.btn-cancel {
  background: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
  transition: filter 0.2s;
}

.btn-cancel:hover {
  filter: brightness(1.2);
}

.btn-save {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  transition: filter 0.2s;
}

.btn-save:hover {
  filter: brightness(1.2);
}

.btn-save:disabled {
  background: rgb(var(--v-theme-disabled));
  cursor: not-allowed;
}
</style>

