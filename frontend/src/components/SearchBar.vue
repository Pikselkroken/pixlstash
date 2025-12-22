<template>
  <v-overlay :value="true" class="search-overlay">
    <v-card class="search-card">
      <v-btn icon class="close-icon" @click="closeOverlay">
        <v-icon>mdi-close</v-icon>
      </v-btn>
      <v-card-title>
        Search
      </v-card-title>
      <v-card-text>
        <v-text-field
          v-model="input"
          label="Search"
          outlined
          clearable
          @click:clear="clearInput"
        ></v-text-field>
      </v-card-text>
      <v-card-actions>
        <v-btn class="search-button" @click="emitSearch">
          <v-icon>mdi-magnify</v-icon>
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-overlay>
</template>

<script setup>
import { ref, defineEmits } from "vue";
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

function emitSearch() {
  emit("search", input.value);
}

function clearInput() {
  input.value = "";
  emit("search", "");
}

function closeOverlay() {
  emit("close");
}
</script>

<style scoped>
.search-overlay {
  display: flex;
  justify-content: center;
  align-items: center;
}
.search-card {
  width: 400px;
  position: relative;
  color: white;
  background-color: #97a0ac;
  overflow: visible;
}
.search-button {
  background-color: #97a0ac;
  color: white;
}
.close-icon {
  position: absolute;
  top: -20px;
  right: -20px;
  background-color: #97a0ac;
  border: none;
  color: white;
  font-size: 20px;
  cursor: pointer;
  z-index: 1;
}
.close-icon:hover {
  position: absolute;
  top: -20px;
  right: -20px;
  background-color: orange;
  border: none;
  color: white;
  font-size: 20px;
  cursor: pointer;
  z-index: 1;
}
</style>
