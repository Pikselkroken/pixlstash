<script setup>
import { computed, ref, watch } from "vue";
import { isReadOnly, logout } from "../../utils/apiClient";
import AccountSection from "./AccountSection.vue";
import AppearanceSection from "./AppearanceSection.vue";
import BehaviourSection from "./BehaviourSection.vue";
import CheckpointsSection from "./CheckpointsSection.vue";
import SmartScoreSection from "./SmartScoreSection.vue";
import WorkflowsSection from "./WorkflowsSection.vue";

const appVersion = __APP_VERSION__;

const props = defineProps({
  open: { type: Boolean, default: false },
  sidebarThumbnailSize: { type: Number, default: 32 },
  dateFormat: { type: String, default: "locale" },
  themeMode: { type: String, default: "light" },
  checkForUpdates: { type: Boolean, default: null },
  showKeyboardHint: { type: Boolean, default: true },
});

const emit = defineEmits([
  "update:open",
  "update:sidebar-thumbnail-size",
  "update:date-format",
  "update:theme-mode",
  "update:hidden-tags",
  "update:apply-tag-filter",
  "update:comfyui-configured",
  "update:check-for-updates",
  "update:show-keyboard-hint",
  "update:public-url",
]);

const dialogOpen = computed({
  get: () => props.open,
  set: (value) => emit("update:open", value),
});

const settingsTab = ref("appearance");

watch(
  () => dialogOpen.value,
  (isOpen) => {
    if (isOpen) {
      settingsTab.value = "appearance";
    }
  },
);
</script>

<template>
  <v-dialog
    v-model="dialogOpen"
    max-width="950"
    @click:outside="dialogOpen = false"
  >
    <div class="settings-dialog-shell">
      <v-btn
        icon
        size="36px"
        class="settings-dialog-close"
        @click="dialogOpen = false"
      >
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="settings-dialog-card">
        <v-card-title class="settings-dialog-title">
          Settings
          <span class="settings-dialog-version">v{{ appVersion }}</span>
          <v-btn
            variant="text"
            size="small"
            class="settings-logout-btn"
            title="Log out"
            @click="logout"
          >
            <v-icon size="16" class="settings-logout-icon">mdi-logout</v-icon>
            Log out
          </v-btn>
        </v-card-title>
        <v-tabs
          v-model="settingsTab"
          density="compact"
          class="settings-tabs"
          show-arrows
        >
          <v-tab value="appearance">Appearance</v-tab>
          <v-tab v-if="!isReadOnly" value="behaviour">Behaviour</v-tab>
          <v-tab v-if="!isReadOnly" value="smart-score">Smart Score</v-tab>
          <v-tab v-if="!isReadOnly" value="workflows">Workflows</v-tab>
          <v-tab v-if="!isReadOnly" value="checkpoints">Checkpoints</v-tab>
          <v-tab v-if="!isReadOnly" value="account">Account Settings</v-tab>
        </v-tabs>
        <v-card-text class="settings-dialog-body">
          <v-window v-model="settingsTab" class="settings-tab-body">
            <v-window-item value="appearance">
              <AppearanceSection
                :sidebar-thumbnail-size="props.sidebarThumbnailSize"
                :theme-mode="props.themeMode"
                :date-format="props.dateFormat"
                :show-keyboard-hint="props.showKeyboardHint"
                @update:sidebar-thumbnail-size="
                  emit('update:sidebar-thumbnail-size', $event)
                "
                @update:theme-mode="emit('update:theme-mode', $event)"
                @update:date-format="emit('update:date-format', $event)"
                @update:show-keyboard-hint="
                  emit('update:show-keyboard-hint', $event)
                "
              />
            </v-window-item>
            <v-window-item value="behaviour">
              <BehaviourSection
                :open="dialogOpen"
                :check-for-updates="props.checkForUpdates"
                @update:hidden-tags="emit('update:hidden-tags', $event)"
                @update:apply-tag-filter="
                  emit('update:apply-tag-filter', $event)
                "
                @update:check-for-updates="
                  emit('update:check-for-updates', $event)
                "
              />
            </v-window-item>
            <v-window-item value="smart-score">
              <SmartScoreSection :open="dialogOpen" />
            </v-window-item>
            <v-window-item value="workflows">
              <WorkflowsSection
                :open="dialogOpen"
                @update:comfyui-configured="
                  emit('update:comfyui-configured', $event)
                "
              />
            </v-window-item>
            <v-window-item value="checkpoints">
              <CheckpointsSection :open="dialogOpen" />
            </v-window-item>
            <v-window-item value="account">
              <AccountSection
                :open="dialogOpen"
                @update:public-url="emit('update:public-url', $event)"
              />
            </v-window-item>
          </v-window>
        </v-card-text>
      </v-card>
    </div>
  </v-dialog>
</template>

<style scoped>
.settings-dialog-card {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: 12px;
  color-scheme: dark;
  display: flex !important;
  flex-direction: column !important;
  flex: 1;
  min-height: 420px;
  overflow: hidden;
  max-height: calc(92dvh - 20px);
}

.settings-dialog-shell {
  position: relative;
  width: 100%;
  min-height: 440px;
  max-height: 92dvh;
  display: flex;
  flex-direction: column;
  overflow: visible;
  padding-top: 20px;
}

.settings-dialog-close {
  position: absolute;
  top: 2px;
  right: -18px;
  background-color: rgb(var(--v-theme-primary));
  border: none;
  color: rgb(var(--v-theme-on-primary));
  cursor: pointer;
  z-index: 2;
}

.settings-dialog-close:hover {
  background-color: rgb(var(--v-theme-accent));
}

.settings-dialog-title {
  font-weight: 700;
  font-size: 1.2rem;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.settings-logout-btn {
  margin-left: auto;
  font-size: 0.78rem;
  opacity: 0.6;
  text-transform: none;
  letter-spacing: 0;
}

.settings-logout-btn:hover {
  opacity: 1;
}

.settings-logout-icon {
  margin-right: 3px;
}

.settings-dialog-version {
  font-size: 0.75rem;
  font-weight: 400;
  opacity: 0.5;
}

.settings-tabs {
  margin-top: 4px;
  overflow: hidden;
  flex-shrink: 0;
}

:deep(.settings-tabs .v-slide-group__content) {
  overflow-x: auto;
  scrollbar-width: none;
}

:deep(.settings-tabs .v-slide-group__content::-webkit-scrollbar) {
  display: none;
}

:deep(.settings-tabs .v-tab) {
  border: 1px solid transparent;
  box-shadow: none;
  font-size: 0.75rem;
  min-width: 0;
  padding: 0 8px;
}

:deep(.settings-tabs .v-tab--selected) {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
  box-shadow: none;
}

:deep(.settings-tabs .v-tab--active) {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
  box-shadow: none;
}

:deep(.settings-tabs .v-tab--selected::before),
:deep(.settings-tabs .v-tab--active::before) {
  opacity: 0;
}

:deep(.settings-tabs .v-tab .v-btn__overlay),
:deep(.settings-tabs .v-tab .v-btn__underlay) {
  opacity: 0;
}

:deep(.settings-tabs .v-tab:focus-visible) {
  outline: none;
  box-shadow: none;
  border-color: rgba(var(--v-theme-on-surface), 0.18);
}

:deep(.settings-tabs .v-tab:focus),
:deep(.settings-tabs .v-tab:active),
:deep(.settings-tabs .v-tab--active),
:deep(.settings-tabs .v-tab--selected) {
  outline: none;
  box-shadow: none;
}

:deep(.settings-tabs .v-tab--selected:focus-visible) {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
}

.settings-tab-body {
  padding-top: 6px;
  overflow: visible;
}

.settings-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  line-height: 1;
  overflow-y: auto !important;
  flex: 1 !important;
  min-height: 0 !important;
}

.settings-dialog-body
  :deep(.v-select .v-field--variant-filled .v-field__input) {
  padding-top: 4px;
  padding-bottom: 4px;
  min-height: 0;
}

.settings-dialog-body :deep(.v-select .v-field--variant-filled) {
  --v-input-control-height: 34px;
}

.settings-section {
  display: flex;
  line-height: 1;
  flex-direction: column;
  gap: 6px;
}

.settings-section-title {
  font-weight: 600;
}

.settings-section-desc {
  font-size: 0.92em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-tagger-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
}

.settings-tagger-checkbox {
  flex: 1;
  min-width: 0;
}

.settings-stepper {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
}

.settings-stepper-label {
  font-size: 0.8em;
  color: rgba(var(--v-theme-on-surface), 0.68);
  white-space: nowrap;
  line-height: 1;
}

.settings-stepper-controls {
  display: flex;
  align-items: center;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.28);
  border-radius: 4px;
  overflow: hidden;
  height: 26px;
}

.settings-stepper-btn {
  width: 26px;
  height: 100%;
  border: none;
  border-radius: 0;
  appearance: none;
  -webkit-appearance: none;
  background: rgba(var(--v-theme-on-surface), 0.06);
  cursor: pointer;
  font-size: 1em;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
  flex-shrink: 0;
  padding: 0;
  color: inherit;
  outline: none;
}

.settings-stepper-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.14);
}

.settings-stepper-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.settings-stepper-value {
  min-width: 30px;
  text-align: center;
  font-size: 0.88em;
  line-height: 1;
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 2px;
}

.settings-stepper--disabled .settings-stepper-label {
  opacity: 0.4;
}

.settings-stepper--disabled .settings-stepper-controls {
  opacity: 0.4;
}

.settings-threshold-preview-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.28);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  outline: none;
  padding: 0;
  color: rgba(var(--v-theme-on-surface), 0.68);
  flex-shrink: 0;
  transition:
    background 0.15s,
    color 0.15s;
}

.settings-threshold-preview-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.1);
  color: rgba(var(--v-theme-on-surface), 0.92);
}

/* Label thresholds dialog */
.label-thresholds-dialog {
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
}

.label-thresholds-dialog-title {
  font-size: 1rem;
  font-weight: 600;
  padding-bottom: 4px;
}

.label-thresholds-dialog-body {
  padding-top: 0;
  max-height: 420px;
  overflow-y: auto;
}

.label-thresholds-loading,
.label-thresholds-empty {
  font-size: 0.875rem;
  opacity: 0.6;
  padding: 12px 0;
  text-align: center;
}

.label-thresholds-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.label-thresholds-table th,
.label-thresholds-table td {
  padding: 4px 8px;
  vertical-align: middle;
  text-align: left;
}

.label-thresholds-table th {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.5;
  border-bottom: 1px solid rgba(var(--v-theme-on-background), 0.1);
  padding-bottom: 6px;
}

.label-thresholds-table tr:hover td {
  background: rgba(var(--v-theme-on-background), 0.04);
}

.lth-col-name {
  width: 60%;
}

.lth-col-base,
.lth-col-eff {
  width: 20%;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.lth-boosted {
  color: rgb(var(--v-theme-primary));
}

.lth-penalised {
  color: rgb(var(--v-theme-error));
}

.settings-comfyui-display {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

.settings-comfyui-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.93em;
}

.settings-comfyui-label {
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.7);
  min-width: 36px;
}

.settings-comfyui-value {
  color: rgb(var(--v-theme-on-surface));
  font-family: monospace;
}

.settings-slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
  padding-right: 8px;
}

.settings-slider-value {
  min-width: 64px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
}

.settings-slider {
  flex: 1 1 auto;
  margin-right: 6px;
  overflow: visible;
}

.settings-account-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0 2px;
}

.settings-account-label {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.settings-account-value {
  font-weight: 600;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-add-tag-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.settings-add-tag-input {
  flex: 1 1 auto;
}

.settings-number-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  align-items: start;
}

.settings-number-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 4px;
}

.settings-number-spinner {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-self: center;
  transform: translateY(-10px);
}

.settings-number-btn {
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 6px;
  width: 24px;
  height: 22px;
  min-width: 24px;
}

.settings-number-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.16);
}

.settings-number-input {
  width: 100%;
}

:deep(.settings-number-input input[type="number"]) {
  -moz-appearance: textfield;
  appearance: textfield;
}

:deep(.settings-number-input input[type="number"]::-webkit-inner-spin-button),
:deep(.settings-number-input input[type="number"]::-webkit-outer-spin-button) {
  -webkit-appearance: none;
  margin: 0;
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.9em;
}

.settings-success {
  color: rgb(var(--v-theme-accent));
  font-size: 0.9em;
}

.settings-public-url-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.settings-watermark-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.settings-watermark-preview {
  max-height: 36px;
  max-width: 120px;
  object-fit: contain;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  padding: 2px;
}

.settings-action-btn {
  align-self: flex-start;
  background-color: rgb(var(--v-theme-primary)) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}

.settings-action-btn:hover {
  background-color: rgb(var(--v-theme-accent)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}

.settings-tokens {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.token-field {
  font-size: 0.85em;
}

.token-field :deep(.v-label) {
  font-size: 0.85em;
}

.token-field :deep(.v-field__input) {
  font-size: 0.85em;
}

.settings-token-loading {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-token-list {
  max-height: 200px;
  overflow-y: auto;
  padding-right: 4px;
}

.settings-token-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82em;
}

.settings-token-table thead th {
  text-align: left;
  padding: 2px 8px 4px;
  font-size: 0.78em;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.5);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  white-space: nowrap;
}

.settings-token-row td {
  padding: 3px 8px;
  vertical-align: middle;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.settings-token-row:last-child td {
  border-bottom: none;
}

.settings-token-desc {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 140px;
}

.settings-token-sub {
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
}

.settings-token-expired {
  color: rgb(var(--v-theme-error));
  font-weight: 600;
}

.settings-token-actions {
  text-align: right;
  white-space: nowrap;
  padding-left: 0;
}

.settings-token-delete {
  color: rgba(var(--v-theme-error), 0.9);
}

.settings-token-empty {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.settings-tag-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.settings-tag-list .settings-token-empty {
  grid-column: 1 / -1;
}

.settings-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.settings-tag-chip--row {
  width: 100%;
  justify-content: space-between;
  padding-right: 4px;
}

.settings-tag-importance {
  flex: 0 1 90px;
  min-width: 0;
  max-width: 90px;
  overflow: hidden;
}

:deep(.settings-tag-importance .v-field) {
  min-height: 28px;
  height: 28px;
  padding-top: 0;
  padding-bottom: 0;
  font-size: 0.9em;
  background: transparent;
  box-shadow: none;
  border: none;
}

:deep(.settings-tag-importance .v-field__input) {
  min-height: 28px;
  height: 28px;
  padding-top: 0;
  padding-bottom: 0;
  padding-right: 4px;
  font-size: 0.85rem;
  min-width: 0;
  overflow: hidden;
}

:deep(.settings-tag-importance .v-field__append-inner) {
  align-self: center;
  margin-left: 2px;
  padding-top: 0;
  padding-bottom: 0;
  height: 28px;
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

:deep(.settings-tag-importance .v-field__overlay),
:deep(.settings-tag-importance .v-field__underlay),
:deep(.settings-tag-importance .v-field__outline) {
  opacity: 0;
}

:deep(.settings-tag-importance .v-select__selection-text) {
  font-size: 0.85rem;
  line-height: 1.1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  display: block;
}

:deep(.settings-tag-importance .v-field__input input) {
  font-size: 0.85rem;
}

.settings-tag-label {
  font-size: 1em;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

.settings-tag-delete {
  color: rgba(var(--v-theme-on-surface), 0.65);
  min-width: 0;
  height: 12px;
  width: 12px;
  padding: 2;
}

.settings-tag-delete:hover {
  color: rgba(var(--v-theme-error), 0.9);
  min-width: 0;
  padding: 2;
}

.settings-token-dialog {
  padding-bottom: 8px;
}

.settings-token-warning {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-bottom: 6px;
}

.settings-token-value-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.settings-token-value {
  flex: 1;
  word-break: break-all;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  background: rgba(var(--v-theme-surface), 0.2);
  border-radius: 8px;
  padding: 2px 4px;
}

.settings-token-copy-btn {
  flex-shrink: 0;
  opacity: 0.7;
}

.settings-token-copy-btn:hover {
  opacity: 1;
}

.settings-section-divider {
  margin: 4px 0 8px;
}

.settings-privacy-note {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: 4px;
}

.settings-dialog-actions {
  padding-top: 0;
}
</style>
