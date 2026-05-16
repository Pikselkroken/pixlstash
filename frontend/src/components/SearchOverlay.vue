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
      <v-card-text style="display: flex; flex-direction: column; gap: 0">
        <v-text-field
          v-if="!isClosing"
          v-model="input"
          dense
          outlined
          clearable
          autocomplete="off"
          name="pikselkroken_search_unique"
          @click:clear="clearInput"
          append-icon="mdi-magnify"
          @click:append="emitSearch"
          ref="inputField"
        ></v-text-field>
        <div v-if="tabSuggestion" class="search-tab-hint">
          <kbd>Tab</kbd> → {{ tabSuggestion }}
        </div>
      </v-card-text>
      <v-card-text
        v-if="history && history.length"
        class="search-history-section"
      >
        <div class="search-history-header">
          <span class="search-history-label">Recent searches</span>
          <button
            class="search-history-clear"
            type="button"
            title="Clear search history"
            @click="emit('clear-history')"
          >
            <v-icon size="14">mdi-close-circle-outline</v-icon>
          </button>
        </div>
        <div class="search-history-chips">
          <button
            v-for="item in history"
            :key="item"
            class="search-history-chip"
            type="button"
            @click="applyHistory(item)"
          >
            <v-icon size="14" style="opacity: 0.5; margin-right: 4px"
              >mdi-history</v-icon
            >
            {{ item }}
          </button>
        </div>
      </v-card-text>
    </v-card>
  </v-overlay>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from "vue";
import {
  VOverlay,
  VCard,
  VCardTitle,
  VCardText,
  VBtn,
  VIcon,
  VTextField,
} from "vuetify/components";

const props = defineProps({
  modelValue: { type: String, default: "" },
  history: { type: Array, default: () => [] },
});
const emit = defineEmits(["search", "close", "clear-history"]);
const input = ref(props.modelValue || "");
const inputField = ref(null);
const isClosing = ref(false);

const tabSuggestion = computed(() => {
  const needle = (input.value || "").trim().toLowerCase();
  if (!needle) return null;
  return (
    props.history.find((item) => item.toLowerCase().startsWith(needle)) ?? null
  );
});

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

  // Remove the input from the DOM (via isClosing v-if) before closing the
  // overlay to avoid focus-restoration side effects, then emit search before
  // close so the parent handler runs while the component is still mounted.
  nextTick(() => {
    emit("search", query);
    emit("close");
  });
}

function clearInput() {
  input.value = "";
}

function applyHistory(item) {
  input.value = item;
  emitSearch();
}

function closeOverlay() {
  emit("close");
}

function handleKeydown(event) {
  if (event.key === "Tab" && tabSuggestion.value) {
    event.preventDefault();
    input.value = tabSuggestion.value;
    return;
  }
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
  max-width: calc(100vw - 32px);
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

.search-history-section {
  padding-top: 0 !important;
}

.search-tab-hint {
  font-size: 0.8em;
  opacity: 0.55;
  margin-top: -12px;
  margin-bottom: 4px;
  padding-left: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.search-tab-hint kbd {
  font-family: inherit;
  font-size: 0.85em;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.3);
  border-radius: 3px;
  padding: 0 4px;
  opacity: 0.8;
}

.search-history-header {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
}

.search-history-label {
  font-size: 0.78em;
  opacity: 0.6;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.search-history-clear {
  font-size: 0.78em;
  opacity: 0.55;
  background: none;
  border: none;
  cursor: pointer;
  color: inherit;
  padding: 0;
}

.search-history-clear:hover {
  opacity: 1;
}

.search-history-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.search-history-chip {
  display: inline-flex;
  align-items: center;
  background: rgba(var(--v-theme-on-surface), 0.08);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 999px;
  padding: 3px 12px 3px 8px;
  font-size: 0.85em;
  cursor: pointer;
  color: inherit;
  transition: background 0.15s;
}

.search-history-chip:hover {
  background: rgba(var(--v-theme-primary), 0.18);
  border-color: rgb(var(--v-theme-primary));
}
</style>
