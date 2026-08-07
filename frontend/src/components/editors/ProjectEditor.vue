<script setup>
import { ref, watch } from "vue";
import {
  createProject,
  updateProject,
  deleteProject as deleteProjectRequest,
} from "../../api/projects";
import { useConfirm } from "../../composables/useConfirm";
import { useNoticeStore } from "../../stores/useNoticeStore";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import { errorDetail } from "../../utils/apiError";

import { API_BASE_URL } from "../../utils/apiClient";
const props = defineProps({
  open: { type: Boolean, default: false },
  project: { type: Object, default: null },
  backendUrl: { type: String, default: () => API_BASE_URL },
});

const emit = defineEmits(["close", "saved", "deleted"]);

// First adoption of the Phase 2 scaffolds. `useConfirm().confirm()` falls back
// to the native window.confirm until the design pass mounts a ConfirmDialog
// host, so behaviour is identical today; a delete failure now also raises a
// central notice (a no-op on screen until the notice host lands).
const { confirm } = useConfirm();
const noticeStore = useNoticeStore();

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
      await updateProject(
        props.project.id,
        {
          name: name.value.trim(),
          description: description.value.trim() || null,
        },
      );
      emit("saved", null);
    } else {
      const created = await createProject(
        {
          name: name.value.trim(),
          description: description.value.trim() || null,
        },
      );
      emit("saved", created?.id ?? null);
    }
  } catch (e) {
    error.value = errorDetail(e) || e.message || "Save failed.";
  } finally {
    saving.value = false;
  }
}

async function deleteProject() {
  if (!props.project?.id) return;
  const confirmed = await confirm({
    title: "Delete project",
    message: `Delete project "${props.project.name}"? This cannot be undone.`,
    confirmLabel: "Delete",
    danger: true,
  });
  if (!confirmed) return;
  deleting.value = true;
  error.value = null;
  try {
    await deleteProjectRequest(props.project.id);
    emit("deleted", props.project.id);
  } catch (e) {
    error.value = errorDetail(e) || e.message || "Delete failed.";
    noticeStore.error(error.value);
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <AppDialog
    :open="open"
    :title="project ? 'Edit project' : 'New project'"
    :width="480"
    @close="emit('close')"
  >
    <div class="project-editor-body">
      <AppInput
        v-model="name"
        label="Name *"
        icon="briefcase-outline"
        :disabled="saving"
        @enter="save"
      />
      <AppTextarea
        v-model="description"
        label="Description"
        placeholder="Optional description…"
        :rows="3"
        :disabled="saving"
      />
      <div v-if="error" class="project-editor-error">{{ error }}</div>
    </div>
    <template #footer>
      <AppButton
        v-if="project?.id"
        variant="danger"
        icon-left="delete-outline"
        :disabled="saving || deleting"
        @click="deleteProject"
      >
        Delete
      </AppButton>
      <span class="project-editor-spacer" />
      <AppButton
        variant="secondary"
        :disabled="saving || deleting"
        @click="emit('close')"
      >
        Cancel
      </AppButton>
      <AppButton
        variant="primary"
        icon-left="check"
        :disabled="saving || deleting"
        @click="save"
      >
        Save
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.project-editor-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.project-editor-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
}

.project-editor-spacer {
  flex: 1;
}
</style>
