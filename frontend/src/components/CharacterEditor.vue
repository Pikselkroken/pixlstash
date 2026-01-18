<template>
  <div v-if="open" class="character-editor-overlay" @click.self="emit('close')">
    <div class="editor-content">
      <div class="editor-header">
        <h2>{{ character?.id ? "Edit Character" : "New Character" }}</h2>
        <button class="close-btn" @click="emit('close')" aria-label="Close">
          &times;
        </button>
      </div>

      <div class="editor-body">
        <div class="form-group">
          <label for="char-name">Name *</label>
          <input
            id="char-name"
            v-model="localCharacter.name"
            type="text"
            placeholder="Character name"
            class="form-input"
            required
            @keydown.enter="save"
          />
        </div>

        <div class="form-group">
          <label for="char-description">Description</label>
          <textarea
            id="char-description"
            v-model="localCharacter.description"
            placeholder="Character description (used in embeddings)"
            class="form-textarea"
            rows="4"
          ></textarea>
        </div>

        <div class="form-group">
          <label for="char-metadata">Metadata</label>
          <textarea
            id="char-metadata"
            v-model="localCharacter.extra_metadata"
            placeholder="Any other metadata associated with the character"
            class="form-textarea"
            rows="3"
          ></textarea>
        </div>
      </div>

      <div class="editor-footer">
        <button class="btn btn-cancel" @click="emit('close')">Cancel</button>
        <button class="btn btn-save" @click="save" :disabled="!isValid">
          Save
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from "vue";
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
      const nameInput = document.getElementById("char-name");
      if (nameInput) {
        nameInput.focus();
        nameInput.select();
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

    console.log("URL: ", url);

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
.character-editor-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.5);
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.editor-content {
  background: rgb(var(--v-theme-surface));
  border-radius: 8px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.3);
  width: 90%;
  max-width: 600px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.editor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid rgb(var(--v-theme-border));
}

.editor-header h2 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 500;
  color: rgb(var(--v-theme-on-surface));
}

.close-btn {
  background: none;
  border: none;
  font-size: 2rem;
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  line-height: 1;
  padding: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.2s;
}

.close-btn:hover {
  color: rgb(var(--v-theme-accent));
}

.editor-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.form-group {
  margin-bottom: 20px;
}

.form-group label {
  display: block;
  margin-bottom: 8px;
  font-weight: 500;
  color: rgb(var(--v-theme-on-surface));
  font-size: 0.95rem;
}

.form-input {
  width: 100%;
  padding: 10px 12px;
  background-color: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: 4px;
  font-size: 1rem;
  font-family: inherit;
  transition: border-color 0.2s;
}

.form-input:focus {
  outline: none;
  border-color: rgb(var(--v-theme-accent));
}

.form-textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid rgb(var(--v-theme-border));
  background-color: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: 4px;
  font-size: 1rem;
  font-family: inherit;
  resize: vertical;
  transition: border-color 0.2s;
}

.form-textarea:focus {
  outline: none;
  border-color: rgb(var(--v-theme-accent));
}

.editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid rgb(var(--v-theme-hover));
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
}

.btn-save {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}

.btn-save:disabled {
  background: rgb(var(--v-theme-disabled));
  cursor: not-allowed;
}
</style>
