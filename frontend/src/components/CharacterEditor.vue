<template>
  <v-dialog :model-value="open" max-width="600" @click:outside="emit('close')">
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
  VSpacer,
  VTextField,
  VTextarea,
} from "vuetify/components";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  open: { type: Boolean, default: false },
  character: { type: Object, default: null },
  backendUrl: { type: String, required: true },
});

const emit = defineEmits(["close", "saved"]);

const localCharacter = ref({
  id: null,
  name: "",
  description: "",
  extra_metadata: "",
});

const nameInputRef = ref(null);

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
      };
    } else {
      localCharacter.value = {
        id: null,
        name: "",
        description: "",
        extra_metadata: "",
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
