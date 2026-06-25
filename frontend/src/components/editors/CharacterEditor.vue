<template>
  <AppDialog
    :open="open"
    :title="character?.id ? 'Edit person' : 'New person'"
    :width="480"
    @close="emit('close')"
  >
    <div class="editor-body">
      <AppInput
        ref="nameInputRef"
        v-model="localCharacter.name"
        label="Name *"
        placeholder="Name"
        icon="account-outline"
        @enter="save"
      />
      <AppTextarea
        v-model="localCharacter.description"
        label="Description"
        placeholder="A short description of this person…"
        :rows="3"
      />
      <AppTextarea
        v-model="localCharacter.extra_metadata"
        label="Metadata"
        placeholder="Notes, source, tags…"
        :rows="2"
      />
      <AppSelect
        v-model="projectSelection"
        label="Project"
        :options="projectOptions"
      />
      <div v-if="character?.id" class="ref-pictures-section">
        <div class="ref-pictures-header">
          <span class="ref-pictures-title">Reference Images</span>
        </div>
        <p class="ref-pictures-help">
          Automatically selected from the highest-scoring images of this person.
        </p>
        <div v-if="referencePictures.length > 0" class="ref-pictures-grid">
          <div
            v-for="pic in referencePictures"
            :key="pic.id"
            class="ref-picture-item"
            @click="previewPic = pic"
          >
            <img
              :src="
                appendShareToken(
                  `${props.backendUrl}/pictures/thumbnails/${pic.id}.webp`,
                )
              "
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
    </div>
    <template #footer>
      <AppButton variant="secondary" @click="emit('close')">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="check"
        :disabled="!isValid"
        @click="save"
      >
        Save
      </AppButton>
    </template>
  </AppDialog>

  <Teleport to="body">
    <div
      v-if="previewPic"
      class="ref-preview-overlay"
      @click="previewPic = null"
    >
      <img
        :src="
          appendShareToken(
            `${props.backendUrl}/pictures/thumbnails/${previewPic.id}.webp`,
          )
        "
        class="ref-preview-img"
        @click="previewPic = null"
      />
    </div>
  </Teleport>
</template>

<script setup>
import { computed, ref, watch, nextTick } from "vue";
import { apiClient, appendShareToken } from "../../utils/apiClient";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import AppSelect from "../widgets/AppSelect.vue";
import StarRatingOverlay from "../widgets/StarRatingOverlay.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  character: { type: Object, default: null },
  backendUrl: { type: String, required: true },
  projects: { type: Array, default: () => [] },
});

// Native <select> carries string values, so map the "no project" sentinel to an
// empty string and back to a null project_id.
const projectOptions = computed(() => [
  { value: "", label: "— No project —" },
  ...props.projects.map((p) => ({ value: String(p.id), label: p.name })),
]);

const projectSelection = computed({
  get: () =>
    localCharacter.value.project_id == null
      ? ""
      : String(localCharacter.value.project_id),
  set: (v) => {
    localCharacter.value.project_id = v === "" ? null : Number(v);
  },
});

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
      nameInputRef.value?.focus?.();
      nameInputRef.value?.select?.();
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
    if (previewPic.value) {
      previewPic.value = null;
      return;
    }
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
.editor-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.ref-pictures-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-2);
  border-top: 1px solid rgba(var(--v-theme-border, 127 127 127), 0.25);
}

.ref-pictures-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.ref-pictures-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.ref-pictures-help {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin: 0;
  font-style: italic;
  line-height: 1.4;
}

.ref-pictures-empty {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.4);
  font-style: italic;
  margin: 0;
}

.ref-pictures-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.ref-picture-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
}

.ref-picture-thumb {
  width: 80px;
  height: 80px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-surface-variant, 127 127 127), 0.15);
  cursor: pointer;
}

.ref-preview-overlay {
  position: fixed;
  /* Below the desktop title bar (0px in a browser) so the window controls stay
     usable; the preview image centres within the reduced box. */
  inset: var(--titlebar-h) 0 0 0;
  z-index: 9999;
  background: rgba(var(--v-theme-scrim), 0.82);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: zoom-out;
}

.ref-preview-img {
  max-width: 90vw;
  max-height: 90vh;
  object-fit: contain;
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-4);
  cursor: default;
}
</style>
