<template>
  <v-overlay class="search-overlay">
    <v-card class="search-card">
      <v-btn icon size="36px" class="close-icon" @click="closeOverlay">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card-title>
        Search
      </v-card-title>
      <v-card-text style="display: flex; align-items: center;">
        <v-text-field
          v-model="input"
          dense
          outlined
          clearable
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


function emitSearch() {
  emit("close");
  emit("search", input.value);
}

function clearInput() {
  input.value = "";
}

function closeOverlay() {
  console.log("[SearchBar.vue] Closing overlay"); // Debugging log
  emit("close");
}

function handleKeydown(event) {
  if (event.key === "Escape") {
    event.stopPropagation(); // Prevent event propagation
    event.preventDefault(); // Prevent default browser behavior
    closeOverlay();
  }
  else if (event.key === "Enter") {
    emitSearch();
  }
}

onMounted(() => {
  console.log("Mounted: Adding keydown listener"); // Debugging log
  window.addEventListener("keydown", handleKeydown);

    nextTick(() => {
    inputField.value?.focus(); // Focus the text field when the overlay opens
  });

});

onUnmounted(() => {
  console.log("Unmounted: Removing keydown listener"); // Debugging log
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
  color: white;
  background-color: #999;
  overflow: visible;
}
.close-icon {
  position: absolute;
  top: -16px;
  right: -16px;
  background-color: #ccc;
  border: none;
  color: white;
  cursor: pointer;
  z-index: 1;
}
.close-icon:hover {
  background-color: orange;
}
</style>
