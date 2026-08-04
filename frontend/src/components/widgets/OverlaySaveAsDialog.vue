<template>
  <AppDialog
    :open="open"
    title="Save as"
    :subtitle="mediaNoun"
    :width="440"
    @close="emit('close')"
    @accept="confirm"
  >
    <form class="save-as-form" @submit.prevent="confirm">
      <label for="overlay-save-as-name" class="save-as-label">Filename</label>
      <input
        id="overlay-save-as-name"
        ref="inputRef"
        v-model="filename"
        class="save-as-input"
        type="text"
        autocomplete="off"
        :aria-invalid="Boolean(error)"
        :aria-describedby="
          error
            ? 'overlay-save-as-help overlay-save-as-error'
            : 'overlay-save-as-help'
        "
        @input="error = ''"
      />
      <p id="overlay-save-as-help" class="save-as-help">
        Your browser controls the download folder. PixlStash will download the
        original file with this name.
      </p>
      <p
        v-if="error"
        id="overlay-save-as-error"
        class="save-as-error"
        role="alert"
      >
        {{ error }}
      </p>
    </form>
    <template #footer>
      <AppButton variant="secondary" @click="emit('close')">Cancel</AppButton>
      <AppButton variant="primary" icon-left="download" @click="confirm">
        Download
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
import { nextTick, ref, watch } from "vue";
import { normalizeOverlaySaveAsFilename } from "../../utils/overlaySaveAsFilename.js";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  suggestedName: { type: String, default: "" },
  originalExtension: { type: String, default: "" },
  mediaNoun: { type: String, default: "picture" },
});
const emit = defineEmits(["close", "save"]);
const filename = ref("");
const error = ref("");
const inputRef = ref(null);

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    filename.value = props.suggestedName;
    error.value = "";
    nextTick(() => inputRef.value?.select());
  },
);

function confirm() {
  const result = normalizeOverlaySaveAsFilename(
    filename.value,
    props.originalExtension,
  );
  if (result.error) {
    error.value = result.error;
    inputRef.value?.focus();
    return;
  }
  emit("save", result.filename);
}
</script>

<style scoped>
.save-as-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.save-as-label {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
}

.save-as-input {
  width: 100%;
  height: 32px;
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  outline: none;
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-surface));
}

.save-as-input:focus-visible {
  border-color: rgb(var(--v-theme-accent));
  box-shadow: var(--focus-ring);
}

.save-as-help,
.save-as-error {
  margin: 0;
  font-size: var(--text-xs);
}

.save-as-help {
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.save-as-error {
  color: rgb(var(--v-theme-error));
}
</style>
