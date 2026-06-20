<template>
  <v-dialog :model-value="open" max-width="660" @click:outside="emit('close')">
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
            rows="3"
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
            <div class="appearance-joint-header">
              Choose Icon or Thumbnail &amp; Color
            </div>
            <div class="appearance-sections">
              <div class="icon-thumb-box">
                <!-- Icon grid (ICON_CARDS excluded) -->
                <div class="icon-grid">
                  <template v-for="cat in SET_ICON_CATEGORIES" :key="cat.label">
                    <div class="icon-cat-header">{{ cat.label }}</div>
                    <template
                      v-for="ic in cat.icons.filter(
                        (i) => i.value !== ICON_CARDS,
                      )"
                      :key="ic.value"
                    >
                      <button
                        class="icon-btn"
                        :class="{ selected: localSet.set_icon === ic.value }"
                        :title="ic.label"
                        @click="localSet.set_icon = ic.value"
                      >
                        <v-icon
                          size="22"
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
                      size="32"
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
import { apiClient } from "../../utils/apiClient";
import {
  SET_ICONS,
  SET_COLORS,
  SET_ICON_CATEGORIES,
  ICON_CARDS,
} from "../../utils/setAppearance";

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
  /* Cap the dialog at the viewport height and lay it out as a column so the
     body scrolls internally while the header and footer stay pinned — without
     this the tall icon grid pushes the Cancel/Save footer off-screen on short
     windows. */
  display: flex;
  flex-direction: column;
  max-height: 90dvh;
}

.editor-header,
.editor-footer {
  flex-shrink: 0;
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
  gap: var(--space-2);
  /* The only scrollable region; min-height:0 lets it shrink inside the
     flex column so overflow-y actually engages. */
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5) var(--space-5);
}

/* Appearance pickers */
.appearance-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.appearance-joint-header {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  opacity: 0.55;
  line-height: 1;
}

.appearance-sections {
  display: flex;
  gap: var(--space-3);
  align-items: stretch;
  /* Fallback for very narrow windows: let the colour box drop below the icon
     box rather than overflow and get clipped. */
  flex-wrap: wrap;
}

.icon-thumb-box {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3) var(--space-3);
  background: rgba(var(--v-theme-on-surface), 0.12);
}

.color-aside {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3) var(--space-3);
  background: rgba(var(--v-theme-on-surface), 0.12);
}

.appearance-label {
  font-size: var(--text-xs);
  opacity: 0.7;
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
}

.icon-or-divider {
  display: flex;
  flex-direction: column;
  align-items: center;
  align-self: stretch;
  padding: var(--space-7) var(--space-1);
  gap: var(--space-2);
}

.icon-or-line {
  flex: 1;
  width: 1px;
  background: rgba(var(--v-theme-on-surface), 0.12);
}

.icon-or-text {
  font-size: var(--text-2xs);
  opacity: 0.35;
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  line-height: 1;
}

.icon-section-wrap {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
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
  transition: border-color 0.15s;
}

.icon-btn--cards-large:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.icon-btn--cards-large.selected {
  border-color: rgba(var(--v-theme-on-surface), 0.7);
  background: rgba(var(--v-theme-on-surface), 0.1);
}

.icon-grid {
  display: grid;
  grid-template-columns: repeat(8, 32px);
  column-gap: var(--space-1);
  row-gap: var(--space-1);
}

.icon-cat-header {
  grid-column: 1 / -1;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  opacity: 0.45;
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
  transition: border-color 0.15s;
}

.icon-btn-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: var(--radius-sm);
  display: block;
}

.icon-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.icon-btn.selected {
  border-color: rgba(var(--v-theme-on-surface), 0.7);
  background: rgba(var(--v-theme-on-surface), 0.1);
}

.color-section-header {
  display: none;
}

.color-grid {
  display: grid;
  /* Fewer columns → narrower (fits the dialog) and taller, so the colours use
     the vertical space alongside the tall icon grid instead of leaving a gap. */
  grid-template-columns: repeat(4, 36px);
  gap: var(--space-3);
  align-items: start;
  margin-top: var(--space-1);
}

.color-swatch {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  border: 2px solid transparent;
  cursor: pointer;
  outline: none;
  padding: 0;
  box-sizing: border-box;
  aspect-ratio: 1 / 1;
  position: relative;
  transition:
    transform 0.12s,
    border-color 0.12s;
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

.btn {
  padding: var(--space-3) var(--space-6);
  border: none;
  border-radius: var(--radius-sm);
  font-size: var(--text-md);
  cursor: pointer;
  transition: all 0.2s;
  font-weight: var(--weight-medium);
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
  background: rgba(var(--v-theme-on-surface), 0.38);
  cursor: not-allowed;
}
</style>
