<script setup>
import { computed, ref, watch } from "vue";
import { isReadOnly, logout } from "../../utils/apiClient";
import AccountSection from "./AccountSection.vue";
import AppearanceSection from "./AppearanceSection.vue";
import BehaviourSection from "./BehaviourSection.vue";
import ComputeSection from "./ComputeSection.vue";
import SnapshotsSection from "./SnapshotsSection.vue";
import SmartScoreSection from "./SmartScoreSection.vue";
import WorkflowsSection from "./WorkflowsSection.vue";

const appVersion = __APP_VERSION__;

// The desktop app injects this bridge (Electron preload); a plain browser does
// not, so the Compute tab is desktop-only.
const isDesktop =
  typeof window !== "undefined" && !!window.pixlstashDesktop;

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
          <v-tab v-if="!isReadOnly" value="snapshots">Snapshots</v-tab>
          <v-tab v-if="isDesktop && !isReadOnly" value="compute">Backend</v-tab>
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
            <v-window-item value="snapshots">
              <SnapshotsSection :open="dialogOpen" />
            </v-window-item>
            <v-window-item v-if="isDesktop" value="compute">
              <ComputeSection :open="dialogOpen" />
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
  border-radius: var(--radius-lg);
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
  font-weight: var(--weight-semibold);
  font-size: var(--text-lg);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-shrink: 0;
}

.settings-logout-btn {
  margin-left: auto;
  font-size: var(--text-xs);
  opacity: 0.6;
  text-transform: none;
  letter-spacing: 0;
}

.settings-logout-btn:hover {
  opacity: 1;
}

.settings-logout-icon {
  margin-right: var(--space-1);
}

.settings-dialog-version {
  font-size: var(--text-xs);
  font-weight: var(--weight-regular);
  opacity: 0.5;
}

.settings-tabs {
  margin-top: var(--space-2);
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
  font-size: var(--text-xs);
  min-width: 0;
  padding: 0 var(--space-3);
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
  padding-top: var(--space-3);
  overflow: visible;
}

.settings-dialog-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  line-height: 1;
  overflow-y: auto !important;
  flex: 1 !important;
  min-height: 0 !important;
}

.settings-dialog-body
  :deep(.v-select .v-field--variant-filled .v-field__input) {
  padding-top: var(--space-2);
  padding-bottom: var(--space-2);
  min-height: 0;
}

.settings-dialog-body :deep(.v-select .v-field--variant-filled) {
  --v-input-control-height: 34px;
}

.settings-section {
  display: flex;
  line-height: 1;
  flex-direction: column;
  gap: var(--space-3);
}

.settings-section-title {
  font-weight: var(--weight-semibold);
}

.settings-section-desc {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-tagger-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-1);
}

.settings-tagger-checkbox {
  flex: 1;
  min-width: 0;
}

.settings-stepper {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-2);
  flex: 0 0 auto;
}

.settings-stepper-label {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.68);
  white-space: nowrap;
  line-height: 1;
}

.settings-stepper-controls {
  display: flex;
  align-items: center;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.28);
  border-radius: var(--radius-sm);
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
  font-size: var(--text-base);
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
  font-size: var(--text-xs);
  line-height: 1;
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 var(--space-1);
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
  border-radius: var(--radius-sm);
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
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  padding-bottom: var(--space-2);
}

.label-thresholds-dialog-body {
  padding-top: 0;
  max-height: 420px;
  overflow-y: auto;
}

.label-thresholds-loading,
.label-thresholds-empty {
  font-size: var(--text-base);
  opacity: 0.6;
  padding: var(--space-4) 0;
  text-align: center;
}

.label-thresholds-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-base);
}

.label-thresholds-table th,
.label-thresholds-table td {
  padding: var(--space-2) var(--space-3);
  vertical-align: middle;
  text-align: left;
}

.label-thresholds-table th {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.5;
  border-bottom: 1px solid rgba(var(--v-theme-on-background), 0.1);
  padding-bottom: var(--space-3);
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
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.settings-comfyui-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.settings-comfyui-label {
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), 0.7);
  min-width: 36px;
}

.settings-comfyui-value {
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-mono);
}

.settings-slider-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-2);
  padding-right: var(--space-3);
}

.settings-slider-value {
  min-width: 64px;
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
}

.settings-slider {
  flex: 1 1 auto;
  margin-right: var(--space-3);
  overflow: visible;
}

.settings-account-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-3) 0 var(--space-1);
}

.settings-account-label {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.settings-account-value {
  font-weight: var(--weight-semibold);
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.settings-add-tag-row {
  display: flex;
  gap: var(--space-3);
  align-items: flex-end;
}

.settings-add-tag-input {
  flex: 1 1 auto;
}

.settings-number-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
  align-items: start;
}

.settings-number-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: var(--space-2);
}

.settings-number-spinner {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-self: center;
  transform: translateY(-10px);
}

.settings-number-btn {
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-on-surface), 0.08);
  border-radius: var(--radius-sm);
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
  font-size: var(--text-xs);
}

.settings-success {
  color: rgb(var(--v-theme-accent));
  font-size: var(--text-xs);
}

.settings-public-url-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.settings-watermark-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
  flex-wrap: wrap;
}

.settings-watermark-preview {
  max-height: 36px;
  max-width: 120px;
  object-fit: contain;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  padding: var(--space-1);
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
  gap: var(--space-3);
}

.token-field {
  font-size: var(--text-xs);
}

.token-field :deep(.v-label) {
  font-size: var(--text-xs);
}

.token-field :deep(.v-field__input) {
  font-size: var(--text-xs);
}

.settings-token-loading {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-token-list {
  max-height: 200px;
  overflow-y: auto;
  padding-right: var(--space-2);
}

.settings-token-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
}

.settings-token-table thead th {
  text-align: left;
  padding: var(--space-1) var(--space-3) var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.5);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  white-space: nowrap;
}

.settings-token-row td {
  padding: var(--space-1) var(--space-3);
  vertical-align: middle;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.settings-token-row:last-child td {
  border-bottom: none;
}

.settings-token-desc {
  font-weight: var(--weight-semibold);
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
  font-weight: var(--weight-semibold);
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
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.settings-tag-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.settings-tag-list .settings-token-empty {
  grid-column: 1 / -1;
}

.settings-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-2);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.settings-tag-chip--row {
  width: 100%;
  justify-content: space-between;
  padding-right: var(--space-2);
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
  font-size: var(--text-sm);
  background: transparent;
  box-shadow: none;
  border: none;
}

:deep(.settings-tag-importance .v-field__input) {
  min-height: 28px;
  height: 28px;
  padding-top: 0;
  padding-bottom: 0;
  padding-right: var(--space-2);
  font-size: var(--text-base);
  min-width: 0;
  overflow: hidden;
}

:deep(.settings-tag-importance .v-field__append-inner) {
  align-self: center;
  margin-left: var(--space-1);
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
  font-size: var(--text-base);
  line-height: 1.1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  display: block;
}

:deep(.settings-tag-importance .v-field__input input) {
  font-size: var(--text-base);
}

.settings-tag-label {
  font-size: var(--text-base);
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
  padding: var(--space-1);
}

.settings-tag-delete:hover {
  color: rgba(var(--v-theme-error), 0.9);
  min-width: 0;
  padding: var(--space-1);
}

.settings-token-dialog {
  padding-bottom: var(--space-3);
}

.settings-token-warning {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-bottom: var(--space-3);
}

.settings-token-value-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.settings-token-value {
  flex: 1;
  word-break: break-all;
  font-family: var(--font-mono);
  background: rgba(var(--v-theme-surface), 0.2);
  border-radius: var(--radius-md);
  padding: var(--space-1) var(--space-2);
}

.settings-token-copy-btn {
  flex-shrink: 0;
  opacity: 0.7;
}

.settings-token-copy-btn:hover {
  opacity: 1;
}

.settings-section-divider {
  margin: var(--space-2) 0 var(--space-3);
}

.settings-privacy-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: var(--space-2);
}

.settings-dialog-actions {
  padding-top: 0;
}
</style>
