<template>
  <v-dialog :model-value="open" max-width="640" @click:outside="emit('close')">
    <div class="editor-shell">
      <v-btn icon size="36px" class="close-icon" @click="emit('close')">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="editor-card">
        <v-card-title class="editor-header">
          {{ character?.id ? "Edit Person" : "New Person" }}
        </v-card-title>
        <v-card-text class="editor-body">
          <v-text-field
            ref="nameInputRef"
            v-model="localCharacter.name"
            label="Name *"
            placeholder="Name"
            density="comfortable"
            variant="filled"
            @keydown.enter="save"
          />
          <v-textarea
            v-model="localCharacter.description"
            label="Description"
            placeholder="Description (used in embeddings)"
            density="comfortable"
            variant="filled"
            rows="4"
          />
          <v-textarea
            v-model="localCharacter.extra_metadata"
            label="Metadata"
            placeholder="Any other metadata associated with the person"
            density="comfortable"
            variant="filled"
            rows="3"
          />
          <v-select
            v-model="localCharacter.project_id"
            :items="projectItems"
            item-title="name"
            item-value="id"
            label="Project"
            density="comfortable"
            variant="filled"
            clearable
            clear-icon="mdi-close"
          />
          <div v-if="character?.id" class="ref-pictures-section">
            <div class="ref-pictures-header">
              <span class="ref-pictures-title">Reference Images</span>
            </div>
            <p class="ref-pictures-help">
              Automatically selected from the highest-scoring images of this
              person.
            </p>
            <div v-if="referencePictures.length > 0" class="ref-pictures-grid">
              <div
                v-for="pic in referencePictures"
                :key="pic.id"
                class="ref-picture-item"
                @click="previewPic = pic"
              >
                <img
                  :src="`${props.backendUrl}/pictures/thumbnails/${pic.id}.webp`"
                  class="ref-picture-thumb"
                  loading="lazy"
                />
                <StarRatingOverlay
                  :score="pic.score || 0"
                  :max="5"
                  :compact="true"
                />
              </div>
            </div>
            <p v-else-if="!referencePicturesLoading" class="ref-pictures-empty">
              No reference images yet — add more scored pictures of this person.
            </p>
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

  <Teleport to="body">
    <div
      v-if="previewPic"
      class="ref-preview-overlay"
      @click="previewPic = null"
    >
      <img
        :src="`${props.backendUrl}/pictures/thumbnails/${previewPic.id}.webp`"
        class="ref-preview-img"
        @click.stop
      />
    </div>
  </Teleport>
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
import StarRatingOverlay from "./StarRatingOverlay.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  character: { type: Object, default: null },
  backendUrl: { type: String, required: true },
  projects: { type: Array, default: () => [] },
});

const projectItems = computed(() => [
  { id: null, name: "— No project —" },
  ...props.projects,
]);

const emit = defineEmits(["close", "saved"]);

const localCharacter = ref({
  id: null,
  name: "",
  description: "",
  extra_metadata: "",
  project_id: null,
});

const nameInputRef = ref(null);

const referencePictures = ref([]);
const referencePicturesLoading = ref(false);
const previewPic = ref(null);

async function fetchReferencePictures(characterId) {
  referencePicturesLoading.value = true;
  try {
    const refRes = await apiClient.get(
      `${props.backendUrl}/characters/${characterId}/reference_pictures`,
    );
    const ids = refRes.data?.reference_picture_ids ?? [];
    if (!ids.length) {
      referencePictures.value = [];
      return;
    }
    const params = new URLSearchParams();
    ids.forEach((id) => params.append("id", String(id)));
    const picsRes = await apiClient.get(
      `${props.backendUrl}/pictures?${params.toString()}`,
    );
    const pics = Array.isArray(picsRes.data) ? picsRes.data : [];
    const picsById = new Map(pics.map((p) => [String(p.id), p]));
    referencePictures.value = ids
      .map((id) => picsById.get(String(id)))
      .filter(Boolean);
  } catch {
    referencePictures.value = [];
  } finally {
    referencePicturesLoading.value = false;
  }
}

watch(
  () => [props.open, props.character?.id],
  ([isOpen, charId]) => {
    if (isOpen && charId) {
      fetchReferencePictures(charId);
    } else {
      referencePictures.value = [];
    }
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
  () => props.character,
  (newChar) => {
    if (newChar) {
      localCharacter.value = {
        id: newChar.id,
        name: newChar.name || "",
        description: newChar.description || "",
        extra_metadata: newChar.extra_metadata || "",
        project_id: newChar.project_id ?? null,
      };
    } else {
      localCharacter.value = {
        id: null,
        name: "",
        description: "",
        extra_metadata: "",
        project_id: null,
      };
    }
  },
  { immediate: true },
);

function save() {
  if (!isValid.value) {
    console.error("Character data is not valid. Cannot save.");
    return;
  }

  saveCharacter({
    ...localCharacter.value,
  });
}

// Keyboard shortcuts
function handleKeydown(event) {
  if (event.key === "Escape") {
    emit("close");
  } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    // Ctrl+Enter or Cmd+Enter to save (avoid interfering with textarea)
    event.preventDefault();
    save();
  }
}

async function saveCharacter(charData) {
  try {
    const isNew = !charData.id;
    const url = isNew
      ? `${props.backendUrl}/characters`
      : `${props.backendUrl}/characters/${charData.id}`;

    if (isNew) {
      const res = await apiClient.post(url, JSON.stringify(charData));
    } else {
      const res = await apiClient.patch(url, JSON.stringify(charData));
    }
    emit("saved");
  } catch (e) {
    alert("Failed to save character: " + (e.message || e));
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

.ref-pictures-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 4px;
  border-top: 1px solid rgba(var(--v-theme-border, 127 127 127), 0.25);
}

.ref-pictures-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ref-pictures-title {
  font-size: 0.85rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.ref-pictures-help {
  font-size: 0.82rem;
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin: 0;
  font-style: italic;
  line-height: 1.4;
}

.ref-pictures-empty {
  font-size: 0.82rem;
  color: rgba(var(--v-theme-on-surface), 0.4);
  font-style: italic;
  margin: 0;
}

.ref-pictures-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ref-picture-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.ref-picture-thumb {
  width: 80px;
  height: 80px;
  object-fit: cover;
  border-radius: 4px;
  background: rgba(var(--v-theme-surface-variant, 127 127 127), 0.15);
  cursor: pointer;
}

.ref-preview-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.82);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: zoom-out;
}

.ref-preview-img {
  max-width: 90vw;
  max-height: 90vh;
  object-fit: contain;
  border-radius: 6px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.6);
  cursor: default;
}

.editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 8px 16px 16px;
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
