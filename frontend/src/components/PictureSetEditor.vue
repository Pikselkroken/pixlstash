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

const props = defineProps({
  open: { type: Boolean, default: false },
  set: { type: Object, default: null },
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
      };
    } else {
      localSet.value = {
        id: null,
        name: "",
        description: "",
        project_id: null,
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
