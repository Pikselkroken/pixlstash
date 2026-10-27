<template>
  <!-- The Move dialogs' "New folder in <highlighted>…" row (#2206). Opens the
       shared name field under itself; the parent reveals and picks the folder it
       made, through `created`. -->
  <div class="mfn">
    <button
      v-if="!open"
      ref="triggerEl"
      type="button"
      class="mfn-trigger"
      data-testid="model-folder-new-in"
      v-bind="blockedAttrs(reason)"
      @click="onTrigger"
    >
      <v-icon size="14" class="mfn-glyph" aria-hidden="true"
        >mdi-folder-plus-outline</v-icon
      >
      <span class="mfn-label">New folder in {{ label }}…</span>
      <Tooltip v-if="reason" :text="reason" activator="parent" />
    </button>
    <ModelFolderNameField
      v-else
      :model-value="name"
      :label="`New folder in ${label}`"
      :error="error"
      @update:model-value="(value) => (name = value)"
      @submit="submit"
      @cancel="cancel"
    />
  </div>
</template>

<script setup>
import { nextTick, ref } from "vue";
import { VIcon } from "vuetify/components";

import { blockedAttrs } from "../../composables/useModelFolderGates";
import { errorMessage } from "../../utils/apiError";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import ModelFolderNameField from "./ModelFolderNameField.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** The registered folder the new one goes in. */
  folderId: { type: Number, required: true },
  /** The directory it goes in, POSIX, `""` for the folder's root. */
  path: { type: String, default: "" },
  /** The destination's name, as the row says it. */
  label: { type: String, required: true },
  /** Why it cannot be made right now, or "" when it can. */
  reason: { type: String, default: "" },
});

/** `{folderId, path}` of the directory the server made. */
const emit = defineEmits(["created"]);

const foldersStore = useModelFoldersStore();
const open = ref(false);
const name = ref("");
const error = ref("");
const busy = ref(false);
const triggerEl = ref(null);

function onTrigger() {
  if (props.reason) {
    useNoticeStore().push({ level: "info", text: props.reason });
    return;
  }
  name.value = "";
  error.value = "";
  open.value = true;
}

function cancel() {
  open.value = false;
  nextTick(() => triggerEl.value?.focus());
}

async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const created = await foldersStore.createDir(
      props.folderId,
      props.path,
      name.value,
    );
    open.value = false;
    emit("created", { folderId: props.folderId, path: created.path });
  } catch (err) {
    error.value = errorMessage(err, "Could not create that folder.");
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.mfn {
  margin-top: var(--space-3);
}

/* A text row in the dialog, not a button: it names where the folder goes. */
.mfn-trigger {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  min-height: var(--control-h-bar);
  padding: 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
}

.mfn-trigger:hover {
  background: var(--hover-wash);
}

.mfn-trigger:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

.mfn-trigger[aria-disabled="true"] {
  opacity: var(--opacity-disabled);
  cursor: default;
}

.mfn-trigger[aria-disabled="true"]:hover {
  background: transparent;
}

.mfn-glyph {
  flex-shrink: 0;
  opacity: var(--opacity-text-secondary);
}

.mfn-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
