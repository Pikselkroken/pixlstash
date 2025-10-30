<template>
  <div class="search-bar">
    <v-text-field
      v-model="input"
      :placeholder="placeholder"
      @keydown.enter="emitSearch"
      @click:append="emitSearch"
      :append-icon="appendIcon"
      clearable
      @click:clear="clearInput"
      hide-details
      dense
      outlined
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

const emit = defineEmits(["update:modelValue", "search"]);
const input = ref(props.modelValue || "");

watch(
  () => props.modelValue,
  (val) => {
    if (val !== input.value) input.value = val;
  }
);

function emitSearch() {
  emit("update:modelValue", input.value);
  emit("search", input.value);
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
}
.search-bar-text-field {
  flex: 1;
}
</style>
