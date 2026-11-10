<template>
  <AppDialog
    :open="open"
    title="New ComfyUI connection"
    size="sm"
    @close="emit('close')"
  >
    <div class="ncc">
      <AppInput
        v-model="address"
        label="Address"
        placeholder="http://192.0.2.20:8188"
        autofocus
        :error="error"
        @enter="add"
      />
      <AppInput
        v-model="name"
        label="Name"
        placeholder="Render box"
        @enter="add"
      />
      <p class="ncc__note">
        Added to Run on without switching to it. Leave the name empty and it
        is called after its host.
      </p>
    </div>
    <template #footer>
      <AppButton key-hint="esc" :disabled="busy" @click="emit('close')"
        >Cancel</AppButton
      >
      <AppButton
        variant="primary"
        key-hint="enter"
        :disabled="busy || !address.trim()"
        @click="add"
        >Add</AppButton
      >
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * Add another ComfyUI to the Workflows slot's Run on menu (#2167). Nothing is
 * asked of the address here: a render box that is off today is still worth
 * listing, and the menu says Offline until it answers.
 */
import { ref } from "vue";

import { addComfyuiConnection } from "../../api/comfyui";
import { errorMessage } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";

defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "added"]);

const address = ref("");
const name = ref("");
const error = ref("");
const busy = ref(false);

async function add() {
  const typed = address.value.trim();
  if (!typed || busy.value) return;
  // Typed as ComfyUI's console prints it, without the scheme.
  const url = /^[a-z]+:\/\//i.test(typed) ? typed : `http://${typed}`;
  busy.value = true;
  error.value = "";
  try {
    const list = await addComfyuiConnection({ url, name: name.value.trim() });
    emit("added", list);
  } catch (err) {
    console.warn(`[workflows] could not add the ComfyUI at ${url}`, err);
    error.value = errorMessage(err, "Could not add that address.");
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.ncc {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.ncc__note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
