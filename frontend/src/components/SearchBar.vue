<template>
  <div class="search-bar">
    <v-text-field
      v-model="input"
      :placeholder="placeholder"
      @keydown.enter="emitSearch"
      @click:append="emitSearch"
      :prepend-inner-icon="appendIcon"
      clearable
      @click:clear="clearInput"
      hide-details
      dense
      variant="solo"
      class="search-bar-text-field"
    />
  </div>
</template>

<script setup>
import { ref, watch, defineEmits, defineProps } from "vue";

const props = defineProps({
  modelValue: String,
  placeholder: {
    type: String,
    default: "Search...",
  },
  appendIcon: {
    type: String,
    default: "mdi-magnify",
  },
});

const emit = defineEmits(["update:modelValue", "search", "collapse"]);
const input = ref(props.modelValue || "");
const searchInput = ref(null); // Add this line

watch(
  () => props.modelValue,
  (val) => {
    if (val !== input.value) input.value = val;
  }
);

function emitSearch() {
  emit("update:modelValue", input.value);
  emit("search", input.value);
  emit("collapse");
  // Focus the main image grid after search
  const grid = document.querySelector(".image-grid");
  if (grid) grid.focus();
}

function clearInput() {
  input.value = "";
  emit("update:modelValue", "");
  emit("search", "");
}
</script>

<style scoped>
.search-bar {
  display: flex;
  align-items: center;
  width: 100%;
  border-bottom: none !important;
}
.search-bar-text-field {
  flex: 1;
  border-bottom: none !important;
  box-shadow: none !important;
}

/* Remove bottom border/underline from v-text-field inside SearchBar */
::v-deep(
    .search-bar-text-field .v-field,
    .search-bar-text-field .v-field__outline,
    .search-bar-text-field .v-field__outline__notch
  ) {
  border-bottom: none !important;
  box-shadow: none !important;
  --v-field-border-width: 0 !important;
  --v-field-border-color: transparent !important;
}
::v-deep(.search-bar-text-field .v-field::after) {
  display: none !important;
}
/* Remove rounded corners from v-text-field (solo variant) */
::v-deep(.search-bar-text-field .v-field) {
  border-radius: 0 !important;
  background-color: #ddd;
}
</style>
