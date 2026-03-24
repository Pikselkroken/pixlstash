<script setup>
import { ref, watch } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  open: { type: Boolean, default: false },
  project: { type: Object, default: null },
  backendUrl: { type: String, required: true },
});

const emit = defineEmits(["close", "saved", "deleted"]);

const name = ref("");
const description = ref("");
const saving = ref(false);
const deleting = ref(false);
const error = ref(null);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      name.value = props.project?.name ?? "";
      description.value = props.project?.description ?? "";
      error.value = null;
    }
  },
  { immediate: true },
);

async function save() {
  if (!name.value.trim()) {
    error.value = "Name is required.";
    return;
  }
  saving.value = true;
  error.value = null;
  try {
    if (props.project?.id) {
      await apiClient.put(`${props.backendUrl}/projects/${props.project.id}`, {
        name: name.value.trim(),
        description: description.value.trim() || null,
      });
      emit("saved", null);
    } else {
      const res = await apiClient.post(`${props.backendUrl}/projects`, {
        name: name.value.trim(),
        description: description.value.trim() || null,
      });
      emit("saved", res.data?.id ?? null);
    }
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "Save failed.";
  } finally {
    saving.value = false;
  }
}

async function deleteProject() {
  if (!props.project?.id) return;
  if (
    !window.confirm(
      `Delete project "${props.project.name}"? This cannot be undone.`,
    )
  )
    return;
  deleting.value = true;
  error.value = null;
  try {
    await apiClient.delete(`${props.backendUrl}/projects/${props.project.id}`);
    emit("deleted", props.project.id);
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "Delete failed.";
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <v-dialog
    :model-value="open"
    max-width="480"
    @update:model-value="(v) => !v && emit('close')"
  >
    <v-card>
      <v-card-title>{{
        project ? "Edit Project" : "New Project"
      }}</v-card-title>
      <v-card-text>
        <v-text-field
          v-model="name"
          label="Name"
          autofocus
          :disabled="saving"
          @keydown.enter="save"
        />
        <v-textarea
          v-model="description"
          label="Description"
          rows="3"
          :disabled="saving"
        />
        <div v-if="error" class="text-error text-caption mt-1">{{ error }}</div>
      </v-card-text>
      <v-card-actions>
        <v-btn
          v-if="project?.id"
          color="error"
          variant="flat"
          :loading="deleting"
          :disabled="saving"
          @click="deleteProject"
          >Delete</v-btn
        >
        <v-spacer />
        <v-btn :disabled="saving || deleting" @click="emit('close')"
          >Cancel</v-btn
        >
        <v-btn
          color="primary"
          :loading="saving"
          :disabled="deleting"
          @click="save"
          >Save</v-btn
        >
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
