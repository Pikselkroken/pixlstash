<script setup>
import { computed, ref, watch } from "vue";
import { isReadOnly, logout } from "../../utils/apiClient";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AccountSection from "./AccountSection.vue";
import AppearanceSection from "./AppearanceSection.vue";
import BehaviourSection from "./BehaviourSection.vue";
import ComputeSection from "./ComputeSection.vue";
import SnapshotsSection from "./SnapshotsSection.vue";
import SmartScoreSection from "./SmartScoreSection.vue";
import WorkflowsSection from "./WorkflowsSection.vue";
import { VIcon } from "vuetify/components";

const appVersion = __APP_VERSION__;

// The desktop app injects this bridge (Electron preload); a plain browser does
// not, so the Compute tab is desktop-only.
const isDesktop = typeof window !== "undefined" && !!window.pixlstashDesktop;

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

// The left nav rail. Each entry: id, MDI icon, label, and a visibility flag.
const navItems = computed(() =>
  [
    {
      id: "appearance",
      icon: "palette-outline",
      label: "Appearance",
      show: true,
    },
    {
      id: "behaviour",
      icon: "tune-variant",
      label: "Models",
      show: !isReadOnly.value,
    },
    {
      id: "smart-score",
      icon: "star-four-points-outline",
      label: "Smart Score & Filters",
      show: !isReadOnly.value,
    },
    {
      id: "workflows",
      icon: "sitemap-outline",
      label: "Workflows",
      show: !isReadOnly.value,
    },
    {
      id: "snapshots",
      icon: "camera-outline",
      label: "Snapshots",
      show: !isReadOnly.value,
    },
    {
      id: "compute",
      icon: "expansion-card-variant",
      label: "Compute",
      show: isDesktop && !isReadOnly.value,
    },
    {
      id: "backend",
      icon: "server-outline",
      label: "Backend",
      show: isDesktop && !isReadOnly.value,
    },
    {
      id: "account",
      icon: "account-circle-outline",
      label: "Account Settings",
      show: !isReadOnly.value,
    },
  ].filter((n) => n.show),
);

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
  <AppDialog
    :open="dialogOpen"
    title="Settings"
    :subtitle="`v${appVersion}`"
    :width="820"
    :pad-body="false"
    @close="dialogOpen = false"
  >
    <template #header-right>
      <AppButton variant="ghost" size="sm" icon-left="logout" @click="logout">
        Log out
      </AppButton>
    </template>

    <nav class="settings-nav">
      <button
        v-for="item in navItems"
        :key="item.id"
        type="button"
        class="settings-nav-item"
        :class="{ 'settings-nav-item--active': settingsTab === item.id }"
        @click="settingsTab = item.id"
      >
        <span v-if="settingsTab === item.id" class="settings-nav-item__bar" />
        <v-icon size="17" class="settings-nav-item__icon">
          mdi-{{ item.icon }}
        </v-icon>
        {{ item.label }}
      </button>
    </nav>

    <div class="settings-content">
      <!-- Each pane is wrapped in a single v-show div: section components can
           have multiple root nodes (e.g. AccountSection renders dialogs as
           siblings), and v-show cannot toggle a multi-root component. The
           wrapper guarantees one element to show/hide. -->
      <div v-show="settingsTab === 'appearance'" class="settings-pane">
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
          @update:show-keyboard-hint="emit('update:show-keyboard-hint', $event)"
        />
      </div>
      <div
        v-if="!isReadOnly"
        v-show="settingsTab === 'behaviour'"
        class="settings-pane"
      >
        <BehaviourSection :open="dialogOpen" />
      </div>
      <div
        v-if="!isReadOnly"
        v-show="settingsTab === 'smart-score'"
        class="settings-pane"
      >
        <SmartScoreSection
          :open="dialogOpen"
          @update:hidden-tags="emit('update:hidden-tags', $event)"
          @update:apply-tag-filter="emit('update:apply-tag-filter', $event)"
        />
      </div>
      <div
        v-if="!isReadOnly"
        v-show="settingsTab === 'workflows'"
        class="settings-pane"
      >
        <WorkflowsSection
          :open="dialogOpen"
          @update:comfyui-configured="emit('update:comfyui-configured', $event)"
        />
      </div>
      <div
        v-if="!isReadOnly"
        v-show="settingsTab === 'snapshots'"
        class="settings-pane"
      >
        <SnapshotsSection :open="dialogOpen" />
      </div>
      <div
        v-if="isDesktop && !isReadOnly"
        v-show="settingsTab === 'compute'"
        class="settings-pane"
      >
        <ComputeSection :open="dialogOpen" view="compute" />
      </div>
      <div
        v-if="isDesktop && !isReadOnly"
        v-show="settingsTab === 'backend'"
        class="settings-pane"
      >
        <ComputeSection
          :open="dialogOpen"
          view="backend"
          :check-for-updates="props.checkForUpdates"
          @update:check-for-updates="emit('update:check-for-updates', $event)"
        />
      </div>
      <div
        v-if="!isReadOnly"
        v-show="settingsTab === 'account'"
        class="settings-pane"
      >
        <AccountSection
          :open="dialogOpen"
          @update:public-url="emit('update:public-url', $event)"
        />
      </div>
    </div>
  </AppDialog>
</template>

<style scoped>
/* Two-pane Settings: a left nav rail and a scrolling content area, both inside
   the flush AppDialog body. */
.settings-nav {
  width: 196px;
  flex-shrink: 0;
  border-right: 1px solid rgb(var(--v-theme-divider));
  padding: var(--space-3) 0;
  background: rgb(var(--v-theme-background));
  overflow-y: auto;
}

.settings-nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  text-align: left;
  position: relative;
  padding: var(--space-3) var(--space-4) var(--space-3) var(--space-5);
  border: none;
  cursor: pointer;
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), 0.6);
  background: transparent;
  transition:
    color var(--dur-1) var(--ease-standard),
    background var(--dur-1) var(--ease-standard);
}

.settings-nav-item:hover {
  color: rgb(var(--v-theme-on-surface));
  background: var(--hover-wash);
}

.settings-nav-item--active {
  color: rgb(var(--v-theme-accent));
  font-weight: var(--weight-semibold);
  background: var(--hover-wash);
}

.settings-nav-item__bar {
  position: absolute;
  left: 0;
  top: 6px;
  bottom: 6px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: rgb(var(--v-theme-accent));
}

.settings-nav-item__icon {
  flex-shrink: 0;
}

.settings-content {
  flex: 1;
  min-width: 0;
  padding: var(--space-4) var(--space-6);
  overflow-y: auto;
  height: 524px;
}

/* Each pane fills the content height so panes that pin content to the bottom
   (e.g. the Backend tab's library/log links, the Snapshots retention notes)
   have a full-height box to push that content against. */
.settings-pane {
  height: 100%;
}

/* Vuetify's switch label defaults to 1rem / 0.6 opacity, which reads oversized
   and washed-out in this dense pane. Pull it down to the UI ramp. :deep pierces
   into the pane components rendered below settings-content. */
.settings-content :deep(.v-switch .v-label),
.settings-content :deep(.v-checkbox .v-label) {
  font-size: var(--text-sm);
  opacity: 1;
  color: rgb(var(--v-theme-on-surface));
}

.settings-content :deep(.v-switch),
.settings-content :deep(.v-checkbox) {
  --v-input-control-height: 32px;
}
</style>
