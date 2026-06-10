<script setup>
import { computed, ref } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import { useSidebarStore } from "../../stores/useSidebarStore";

const sidebarStore = useSidebarStore();

const props = defineProps({
  sidebarThumbnailSize: { type: Number, default: 32 },
  themeMode: { type: String, default: "light" },
  dateFormat: { type: String, default: "locale" },
  showKeyboardHint: { type: Boolean, default: true },
});

const emit = defineEmits([
  "update:sidebar-thumbnail-size",
  "update:theme-mode",
  "update:date-format",
  "update:show-keyboard-hint",
]);

const sidebarThumbnailSizeModel = computed({
  get: () => props.sidebarThumbnailSize ?? 32,
  set: (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(64, Math.max(20, parsed));
    const snapped = Math.round(clamped / 4) * 4;
    if (snapped === (props.sidebarThumbnailSize ?? 32)) return;
    emit("update:sidebar-thumbnail-size", snapped);
  },
});

const dateFormatModel = computed({
  get: () => props.dateFormat ?? "locale",
  set: (value) => {
    const nextValue = value ?? "locale";
    if (nextValue === (props.dateFormat ?? "locale")) return;
    emit("update:date-format", nextValue);
  },
});

const themeModeModel = computed({
  get: () => props.themeMode ?? "light",
  set: (value) => {
    const nextValue = value ?? "light";
    if (nextValue === (props.themeMode ?? "light")) return;
    emit("update:theme-mode", nextValue);
  },
});

const showKeyboardHintModel = computed({
  get: () => props.showKeyboardHint ?? true,
  set: (value) => {
    if (value === (props.showKeyboardHint ?? true)) return;
    emit("update:show-keyboard-hint", value);
  },
});

const dateFormatOptions = [
  { title: "Locale default", value: "locale" },
  { title: "ISO (YYYY-MM-DD, 24h)", value: "iso" },
  { title: "European (DD/MM/YYYY, 24h)", value: "eu" },
  { title: "British (DD/MM/YYYY, AM/PM)", value: "british" },
  { title: "American (MM/DD/YYYY, AM/PM)", value: "us" },
  { title: "China (YYYY/MM/DD, 24h)", value: "ymd-slash" },
  { title: "Korea (YYYY.MM.DD, 24h)", value: "ymd-dot" },
  { title: "Japan (YYYY年MM月DD日, 24h)", value: "ymd-jp" },
];

const themeModeOptions = [
  { title: "Light", value: "light" },
  { title: "Dark", value: "dark" },
];

const clearingGuestSession = ref(false);
const hasGuestSessionCookie = computed(() =>
  document.cookie
    .split(";")
    .some((c) => c.trim().startsWith("guest_session_active=1")),
);

async function clearGuestSession() {
  clearingGuestSession.value = true;
  try {
    await apiClient.delete("/pictures/guest-scores/session");
  } catch (err) {
    console.error("Failed to clear guest session:", err);
  } finally {
    clearingGuestSession.value = false;
  }
  localStorage.removeItem("guest_session_id");
  // Reload so the in-memory guest state (guestScoreMap, guestConsentState)
  // is fully reset and the page reflects the clean slate.
  window.location.reload();
}
</script>

<template>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div
      class="settings-section-title"
      title="Adjust the sidebar thumbnail size."
    >
      Sidebar Thumbnails
    </div>
    <div class="settings-slider-row">
      <span class="settings-slider-value">
        {{ sidebarThumbnailSizeModel }}px
      </span>
      <v-slider
        v-model="sidebarThumbnailSizeModel"
        :min="20"
        :max="64"
        :step="4"
        hide-details
        track-color="#666"
        thumb-color="primary"
        class="settings-slider"
      />
    </div>
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div
      class="settings-section-title"
      title="Show the sidebar at full width or as a narrow icon dock."
    >
      Sidebar Width
    </div>
    <div class="sidebar-width-toggle">
      <button
        class="sidebar-width-opt"
        :class="{ active: !sidebarStore.sidebarDocked }"
        type="button"
        @click="sidebarStore.setSidebarDocked(false)"
      >
        <span class="swi swi--full">
          <span class="swi-rail"></span>
          <span class="swi-content"></span>
        </span>
        <span class="sidebar-width-label">Full</span>
      </button>
      <button
        class="sidebar-width-opt"
        :class="{ active: sidebarStore.sidebarDocked }"
        type="button"
        @click="sidebarStore.setSidebarDocked(true)"
      >
        <span class="swi swi--dock">
          <span class="swi-rail"></span>
          <span class="swi-content"></span>
        </span>
        <span class="sidebar-width-label">Dock</span>
      </button>
    </div>
    <div class="settings-section-desc">
      Pin or unpin the sidebar from its header.
    </div>
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div
      class="settings-section-title"
      title="Choose a light or dark theme."
    >
      Theme
    </div>
    <v-select
      v-model="themeModeModel"
      :items="themeModeOptions"
      item-title="title"
      item-value="value"
      density="compact"
      variant="filled"
      class="settings-add-tag-input"
      hide-details
    />
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div
      class="settings-section-title"
      title="Choose how dates are shown in the grid and overlays."
    >
      Date Format
    </div>
    <v-select
      v-model="dateFormatModel"
      :items="dateFormatOptions"
      item-title="title"
      item-value="value"
      density="compact"
      variant="filled"
      class="settings-add-tag-input"
      hide-details
    />
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <v-checkbox
      v-model="showKeyboardHintModel"
      density="compact"
      hide-details
      label="Show keyboard shortcut (F1) indicator"
    />
  </div>
  <template v-if="isReadOnly">
    <v-divider class="settings-section-divider" />
    <div class="settings-section">
      <div class="settings-section-title">Privacy</div>
      <div class="settings-section-desc">
        If you previously accepted the ratings cookie, your scores
        are remembered across visits. Clicking below clears the
        cookie so your next visit starts fresh with no scores
        retrieved.
      </div>
      <v-btn
        variant="tonal"
        size="small"
        color="default"
        style="margin-top: 10px"
        :loading="clearingGuestSession"
        :disabled="!hasGuestSessionCookie"
        @click="clearGuestSession"
      >
        Clear ratings cookie
      </v-btn>
      <div
        v-if="!hasGuestSessionCookie"
        class="settings-section-desc"
        style="margin-top: 6px; opacity: 0.6"
      >
        No ratings cookie is currently set.
      </div>
    </div>
  </template>
</template>

<style scoped>
.settings-section-divider {
  margin: 4px 0 8px;
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

.settings-add-tag-input {
  flex: 1 1 auto;
}

.sidebar-width-toggle {
  display: flex;
  gap: 10px;
  margin-top: 4px;
}
.sidebar-width-opt {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px 8px;
  border-radius: 10px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgb(var(--v-theme-on-surface));
  font-family: inherit;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  transition:
    border-color 0.12s,
    background 0.12s,
    color 0.12s;
}
.sidebar-width-opt:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}
.sidebar-width-opt.active {
  border-color: rgb(var(--v-theme-accent));
  background: rgba(var(--v-theme-accent), 0.1);
  color: rgb(var(--v-theme-accent));
}
/* Mini layout illustration: a window frame with a filled left rail (wide for
   full, narrow for dock) over a dotted content area. currentColor → accent when
   the option is active. */
.swi {
  display: flex;
  width: 78px;
  height: 50px;
  border-radius: 6px;
  border: 1.5px solid currentColor;
  overflow: hidden;
  opacity: 0.85;
}
.swi-rail {
  background: currentColor;
  flex-shrink: 0;
}
.swi--full .swi-rail {
  width: 26px;
}
.swi--dock .swi-rail {
  width: 11px;
}
.swi-content {
  flex: 1;
  background: radial-gradient(currentColor 1px, transparent 1.5px) 0 0 / 10px
    10px;
  opacity: 0.35;
}
.sidebar-width-label {
  font-weight: 600;
}
</style>
