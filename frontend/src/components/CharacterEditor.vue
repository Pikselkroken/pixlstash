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
          <label for="char-original-prompt">Original Prompt</label>
          <textarea
            id="char-original-prompt"
            v-model="localCharacter.original_prompt"
            placeholder="The prompt used to originally generate this character"
            class="form-textarea"
            rows="3"
          ></textarea>
        </div>

        <div class="form-group">
          <label for="char-original-seed">Original Seed</label>
          <input
            id="char-original-seed"
            v-model.number="localCharacter.original_seed"
            type="number"
            placeholder="Seed number"
            class="form-input"
          />
        </div>

        <div class="form-group">
          <label>LoRAs</label>
          <div class="loras-list">
            <div
              v-for="(lora, index) in localCharacter.loras"
              :key="index"
              class="lora-item"
            >
              <input
                v-model="lora[0]"
                type="text"
                placeholder="LoRA name"
                class="form-input lora-name-input"
              />
              <input
                v-model.number="lora[1]"
                type="number"
                step="0.1"
                min="0"
                max="2"
                placeholder="Weight"
                class="form-input lora-weight-input"
              />
              <button
                class="remove-lora-btn"
                @click="removeLora(index)"
                title="Remove LoRA"
              >
                ×
              </button>
            </div>
            <button class="add-lora-btn" @click="addLora">
              <v-icon small>mdi-plus</v-icon>
              Add LoRA
            </button>
          </div>
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
  original_prompt: "",
  original_seed: null,
  loras: [],
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
  }
);

watch(
  () => props.character,
  (newChar) => {
    if (newChar) {
      localCharacter.value = {
        id: newChar.id,
        name: newChar.name || "",
        description: newChar.description || "",
        original_prompt: newChar.original_prompt || "",
        original_seed: newChar.original_seed,
        loras: Array.isArray(newChar.loras)
          ? JSON.parse(JSON.stringify(newChar.loras))
          : [],
      };
    } else {
      localCharacter.value = {
        id: null,
        name: "",
        description: "",
        original_prompt: "",
        original_seed: null,
        loras: [],
      };
    }
  },
  { immediate: true }
);

function addLora() {
  if (!Array.isArray(localCharacter.value.loras)) {
    localCharacter.value.loras = [];
  }
  localCharacter.value.loras.push(["", 1.0]);
}

function removeLora(index) {
  localCharacter.value.loras.splice(index, 1);
}

function save() {
  if (!isValid.value) {
    console.error("Character data is not valid. Cannot save.");
    return;
  }

  // Clean up loras - remove empty entries
  const cleanedLoras = localCharacter.value.loras.filter(
    (lora) => lora[0] && lora[0].trim().length > 0
  );

  console.log("Saving character:", {
    ...localCharacter.value,
    loras: cleanedLoras,
  });
  saveCharacter({
    ...localCharacter.value,
    loras: cleanedLoras,
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
    const method = isNew ? "POST" : "PATCH";
    const url = isNew
      ? `${props.backendUrl}/characters`
      : `${props.backendUrl}/characters/${charData.id}`;

    console.log("URL: ", url);

    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(charData),
    });

    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(errorText || "Failed to save character");
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
  }
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
  background: white;
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
  border-bottom: 1px solid #e0e0e0;
}

.editor-header h2 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 500;
  color: #333;
}

.close-btn {
  background: none;
  border: none;
  font-size: 2rem;
  color: #666;
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
  color: #ff5252;
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
  color: #555;
  font-size: 0.95rem;
}

.form-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 1rem;
  font-family: inherit;
  transition: border-color 0.2s;
}

.form-input:focus {
  outline: none;
  border-color: #4caf50;
}

.form-textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 1rem;
  font-family: inherit;
  resize: vertical;
  transition: border-color 0.2s;
}

.form-textarea:focus {
  outline: none;
  border-color: #4caf50;
}

.loras-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.lora-item {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 8px;
  align-items: center;
}

.lora-name-input {
  grid-column: 1;
}

.lora-weight-input {
  width: 100px;
}

.remove-lora-btn {
  background: #f44336;
  color: white;
  border: none;
  border-radius: 4px;
  width: 32px;
  height: 38px;
  font-size: 1.5rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.2s;
}

.remove-lora-btn:hover {
  background: #d32f2f;
}

.add-lora-btn {
  background: #e0e0e0;
  color: #333;
  border: none;
  border-radius: 4px;
  padding: 8px 16px;
  font-size: 0.95rem;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: background-color 0.2s;
  align-self: flex-start;
}

.add-lora-btn:hover {
  background: #d0d0d0;
}

.editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid #e0e0e0;
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

.btn-cancel {
  background: #f5f5f5;
  color: #666;
}

.btn-cancel:hover {
  background: #e0e0e0;
}

.btn-save {
  background: #4caf50;
  color: white;
}

.btn-save:hover {
  background: #45a049;
}

.btn-save:disabled {
  background: #ccc;
  cursor: not-allowed;
}
</style>
