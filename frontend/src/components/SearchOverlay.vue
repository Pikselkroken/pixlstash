<template>
  <v-overlay
    class="search-overlay"
    :model-value="true"
    @click:outside="closeOverlay"
  >
    <v-card class="search-card">
      <v-btn icon size="36px" class="close-icon" @click="closeOverlay">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card-title> Search </v-card-title>
      <v-card-text style="display: flex; align-items: center">
        <v-text-field
          v-if="!isClosing"
          v-model="input"
          dense
          outlined
          clearable
          autocomplete="off"
          name="pixelurgy_search_unique"
          @click:clear="clearInput"
          append-icon="mdi-magnify"
          @click:append="emitSearch"
          ref="inputField"
        ></v-text-field>
      </v-card-text>
    </v-card>
  </v-overlay>
</template>

<script setup>
import { ref, onMounted, onUnmounted, defineEmits, nextTick } from "vue";
import {
  VOverlay,
  VCard,
  VCardTitle,
  VCardText,
  VCardActions,
  VBtn,
  VIcon,
  VTextField,
} from "vuetify/components";

const emit = defineEmits(["search", "close"]);
const input = ref("");
const inputField = ref(null); // Reference to the text field
const isClosing = ref(false);

function emitSearch() {
  const query = input.value;
  isClosing.value = true;

  // 1. Force blur immediately - target the specific element AND document
  if (inputField.value) {
    inputField.value.blur();
    const inner = inputField.value.$el.querySelector("input");
    if (inner) inner.blur();
  }

  if (document.activeElement instanceof HTMLElement) {
    document.activeElement.blur();
  }

  // 2. Hide the input entirely using CSS or v-if (via isClosing)
  // This physically removes the input from the DOM *before* closing the overlay
  // Wait a tick for this update to happen
  nextTick(() => {
    emit("close");
    // Delay search slightly to allow overlay unmount to complete
    setTimeout(() => emit("search", query), 10);
  });
}

function clearInput() {
  input.value = "";
}

function closeOverlay() {
  emit("close");
}

function handleKeydown(event) {
  if (event.key === "Escape") {
    event.stopPropagation(); // Prevent event propagation
    event.preventDefault(); // Prevent default browser behavior
    closeOverlay();
  } else if (event.key === "Enter") {
    event.preventDefault(); // Prevent form submission/browser history
    emitSearch();
  }
}

onMounted(() => {
  window.addEventListener("keydown", handleKeydown);

  nextTick(() => {
    inputField.value?.focus(); // Focus the text field when the overlay opens
  });
});

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeydown);
});
</script>

<style>
.search-overlay {
  display: flex;
  justify-content: center;
  align-items: center;
}
.search-card {
  width: 600px;
  padding-left: 16px;
  padding-top: 8px;
  position: relative;
  color: rgb(var(--v-theme-on-surface));
  background-color: rgb(var(--v-theme-surface));
  overflow: visible;
  border-radius: 8px;
}
.close-icon {
  position: absolute;
  top: -16px;
  right: -16px;
  background-color: rgb(var(--v-theme-primary));
  border: none;
  color: rgb(var(--v-theme-on-primary));
  cursor: pointer;
  z-index: 1;
}
.close-icon:hover {
  background-color: rgb(var(--v-theme-accent));
}

/* Darker overlay for dialogs/overlays */
.v-overlay__scrim {
  background: rgba(0, 0, 0, 0.8) !important;
  opacity: 0.9 !important;
}
</style>
