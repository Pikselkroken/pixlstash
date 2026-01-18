<template>
  <div v-if="open" class="set-editor-overlay" @click.self="emit('close')">
    <div class="editor-content">
      <div class="editor-header">
        <h2>{{ set?.id ? "Edit Picture Set" : "New Picture Set" }}</h2>
        <button class="close-btn" @click="emit('close')" aria-label="Close">
          &times;
        </button>
      </div>

      <div class="editor-body">
        <div class="form-group">
          <label for="set-name">Name *</label>
          <input
            id="set-name"
            v-model="localSet.name"
            type="text"
            placeholder="Picture set name"
            class="form-input"
            required
            @keydown.enter="save"
          />
        </div>

        <div class="form-group">
          <label for="set-description">Description</label>
          <textarea
            id="set-description"
            v-model="localSet.description"
            placeholder="Optional description"
            class="form-textarea"
            rows="4"
            @keydown.ctrl.enter="save"
            @keydown.meta.enter="save"
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
  set: { type: Object, default: null },
  backendUrl: { type: String, required: true },
});

const emit = defineEmits(["close", "saved", "refresh-sidebar"]);

const localSet = ref({
  id: null,
  name: "",
  description: "",
});

const isValid = computed(() => {
  return localSet.value.name && localSet.value.name.trim().length > 0;
});

// Focus and select the name field when dialog opens
watch(
  () => props.open,
  async (isOpen) => {
    if (isOpen) {
      await nextTick();
      const nameInput = document.getElementById("set-name");
      if (nameInput) {
        nameInput.focus();
        nameInput.select();
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
      };
    } else {
      localSet.value = {
        id: null,
        name: "",
        description: "",
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

const exportTaskId = ref(null);
const exportStatus = ref(null);
const downloadUrl = ref(null);

async function startExport() {
  try {
    const response = await apiClient.get(
      `${props.backendUrl}/pictures/export`,
      {
        params: { set_id: props.set.id },
      },
    );
    exportTaskId.value = response.data.task_id;
    pollExportStatus();
  } catch (error) {
    alert("Failed to start export: " + (error.message || error));
  }
}

async function pollExportStatus() {
  if (!exportTaskId.value) return;

  const interval = setInterval(async () => {
    try {
      const response = await apiClient.get(
        `${props.backendUrl}/pictures/export/status`,
        { params: { task_id: exportTaskId.value } },
      );

      exportStatus.value = response.data.status;

      if (response.data.status === "completed") {
        downloadUrl.value = response.data.download_url;
        clearInterval(interval);
      } else if (response.data.status === "failed") {
        alert("Export failed.");
        clearInterval(interval);
      }
    } catch (error) {
      console.error("Error checking export status:", error);
      clearInterval(interval);
    }
  }, 2000);
}

async function downloadExport() {
  if (downloadUrl.value) {
    try {
      const response = await apiClient.get(
        `${props.backendUrl}${downloadUrl.value}`,
        {
          responseType: "blob", // Ensure binary data is handled correctly
        },
      );

      console.log("Response headers:", response.headers);
      console.log("Response status:", response.status);
      console.log("Blob size:", response.data.size, "bytes");

      // Create a downloadable link for the file
      const blob = new Blob([response.data], { type: "application/zip" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "exported_pictures.zip"; // Default file name
      link.click();

      // Clean up the object URL
      URL.revokeObjectURL(link.href);
    } catch (error) {
      console.error("Download failed:", error);
      alert("Failed to download export: " + (error.message || error));
    }
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
.set-editor-overlay {
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

.export-section {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid rgb(var(--v-theme-border));
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

.btn-export {
  background: rgb(var(--v-theme-tertiary));
  color: rgb(var(--v-theme-on-tertiary));
  width: 100%;
}

.export-status {
  margin-top: 12px;
  font-size: 0.95rem;
  color: rgb(var(--v-theme-on-surface));
}

.status-completed {
  color: rgb(var(--v-theme-success));
}

.status-failed {
  color: rgb(var(--v-theme-error));
}

.download-link {
  color: rgb(var(--v-theme-tertiary));
  text-decoration: underline;
  cursor: pointer;
}

.download-link:hover {
  text-decoration: none;
}
</style>
