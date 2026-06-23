<template>
  <div class="tbm tb-export-panel">
    <span class="tbm-caret tbm-caret--start"></span>
    <div class="tbm-header">
      <v-icon size="18" class="tbm-header-icon">mdi-tray-arrow-down</v-icon>
      <span class="tbm-title"
        >Export
        <span class="tbm-mono">{{ exportStore.exportCount ?? 0 }}</span>
        picture{{ (exportStore.exportCount ?? 0) === 1 ? "" : "s" }}</span
      >
    </div>

    <div class="tbm-section">
      <div class="tbm-grid-2">
        <label class="tbm-field">
          <span class="tbm-label">Export type</span>
          <div class="tbm-select-wrap">
            <select v-model="tbExportTypeModel" class="tbm-select">
              <option
                v-for="o in exportStore.exportTypeOptions ?? []"
                :key="o.value"
                :value="o.value"
              >
                {{ o.title }}
              </option>
            </select>
            <v-icon size="18" class="tbm-select-chevron">mdi-chevron-down</v-icon>
          </div>
        </label>
        <label class="tbm-field">
          <span class="tbm-label">Captions</span>
          <div class="tbm-select-wrap">
            <select
              v-model="tbExportCaptionModeModel"
              class="tbm-select"
              :disabled="exportStore.exportTypeLocksCaptions"
            >
              <option
                v-for="o in exportStore.exportCaptionOptions ?? []"
                :key="o.value"
                :value="o.value"
              >
                {{ o.title }}
              </option>
            </select>
            <v-icon size="18" class="tbm-select-chevron">mdi-chevron-down</v-icon>
          </div>
        </label>
        <label class="tbm-field">
          <span class="tbm-label">Resolution</span>
          <div class="tbm-select-wrap">
            <select v-model="tbExportResolutionModel" class="tbm-select">
              <option
                v-for="o in exportStore.exportResolutionOptions ?? []"
                :key="o.value"
                :value="o.value"
              >
                {{ o.title }}
              </option>
            </select>
            <v-icon size="18" class="tbm-select-chevron">mdi-chevron-down</v-icon>
          </div>
        </label>
        <label class="tbm-field">
          <span class="tbm-label">Bounding boxes</span>
          <div class="tbm-select-wrap">
            <select v-model="tbExportBboxModeModel" class="tbm-select">
              <option
                v-for="o in exportStore.exportBboxOptions ?? []"
                :key="o.value"
                :value="o.value"
              >
                {{ o.title }}
              </option>
            </select>
            <v-icon size="18" class="tbm-select-chevron">mdi-chevron-down</v-icon>
          </div>
        </label>
      </div>
      <label
        v-if="tbExportCaptionModeModel === 'tags'"
        class="tbm-field tb-export-tagformat"
      >
        <span class="tbm-label">Tag format</span>
        <div class="tbm-select-wrap">
          <select v-model="tbExportTagFormatModel" class="tbm-select">
            <option
              v-for="o in exportStore.exportTagFormatOptions ?? []"
              :key="o.value"
              :value="o.value"
            >
              {{ o.title }}
            </option>
          </select>
          <v-icon size="18" class="tbm-select-chevron">mdi-chevron-down</v-icon>
        </div>
      </label>
    </div>

    <div class="tbm-section">
      <v-switch
        v-model="tbExportIncludeCharacterNameModel"
        label="Include character name"
        color="primary"
        density="compact"
        hide-details
        :disabled="
          tbExportCaptionModeModel === 'none' ||
          exportStore.exportTypeLocksCaptions
        "
      />
      <v-switch
        v-model="tbExportUseOriginalFileNamesModel"
        label="Use original file names"
        color="primary"
        density="compact"
        hide-details
      />
    </div>

    <div class="tbm-section">
      <button
        class="tbm-action tbm-action--primary tbm-action--lg tbm-action--full"
        type="button"
        @click="onExport"
      >
        <v-icon size="18">mdi-tray-arrow-down</v-icon>
        Export
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import { useExportStore } from "../../stores/useExportStore";

const emit = defineEmits(["confirm-export"]);

const exportStore = useExportStore();

const tbExportTypeModel = computed({
  get: () => exportStore.exportType,
  set: (v) => {
    exportStore.exportType = v;
  },
});
const tbExportCaptionModeModel = computed({
  get: () => exportStore.exportCaptionMode,
  set: (v) => {
    exportStore.exportCaptionMode = v;
  },
});
const tbExportTagFormatModel = computed({
  get: () => exportStore.exportTagFormat,
  set: (v) => {
    exportStore.exportTagFormat = v;
  },
});
const tbExportResolutionModel = computed({
  get: () => exportStore.exportResolution,
  set: (v) => {
    exportStore.exportResolution = v;
  },
});
const tbExportBboxModeModel = computed({
  get: () => exportStore.exportBboxMode,
  set: (v) => {
    exportStore.exportBboxMode = v;
  },
});
const tbExportIncludeCharacterNameModel = computed({
  get: () => exportStore.exportIncludeCharacterName,
  set: (v) => {
    exportStore.exportIncludeCharacterName = v;
  },
});
const tbExportUseOriginalFileNamesModel = computed({
  get: () => exportStore.exportUseOriginalFileNames,
  set: (v) => {
    exportStore.exportUseOriginalFileNames = v;
  },
});

function onExport() {
  exportStore.exportMenuOpen = false;
  emit("confirm-export");
}
</script>

<style scoped>
.tb-export-panel {
  width: 392px;
  max-width: 92vw;
}

.tb-export-tagformat {
  margin-top: var(--space-4);
}

/* Stack the two switches with the section's group gap. */
.tb-export-panel .v-switch + .v-switch {
  margin-top: var(--space-2);
}
</style>
