<script setup>
import { computed, ref } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { VSwitch } from "vuetify/components";
import AppSelect from "../widgets/AppSelect.vue";
import AppButton from "../widgets/AppButton.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsSliderRow from "./SettingsSliderRow.vue";

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

// Bound straight to the sidebar store (like the Sidebar Width toggle above);
// App.vue watches sidebarStore.sidebarPinned and persists the change.
const sidebarPinnedModel = computed({
  get: () => sidebarStore.sidebarPinned,
  set: (value) => sidebarStore.setSidebarPinned(!!value),
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

// AppSelect takes { label, value }; map the existing { title, value } lists.
const themeSelectOptions = computed(() =>
  themeModeOptions.map((o) => ({ label: o.title, value: o.value })),
);
const dateSelectOptions = computed(() =>
  dateFormatOptions.map((o) => ({ label: o.title, value: o.value })),
);

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
  <div>
    <SettingsSection title="Sidebar Thumbnails" first>
      <SettingsSliderRow
        v-model="sidebarThumbnailSizeModel"
        :min="20"
        :max="64"
        :step="4"
        suffix="px"
      />
    </SettingsSection>

    <SettingsSection
      title="Sidebar Width"
      desc="Show the sidebar at full width or as a narrow icon dock."
    >
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
    </SettingsSection>

    <SettingsSection
      title="Sidebar Visibility"
      desc="Keep the sidebar pinned open, or let it auto-hide and slide in when you hover the edge."
    >
      <div class="appearance-switch">
        <v-switch
          v-model="sidebarPinnedModel"
          color="accent"
          density="compact"
          hide-details
          label="Keep sidebar pinned open"
        />
      </div>
    </SettingsSection>

    <div class="appearance-selects">
      <AppSelect
        v-model="themeModeModel"
        label="Theme"
        :options="themeSelectOptions"
      />
      <AppSelect
        v-model="dateFormatModel"
        label="Date Format"
        :options="dateSelectOptions"
      />
    </div>

    <div class="appearance-switch">
      <v-switch
        v-model="showKeyboardHintModel"
        color="accent"
        density="compact"
        hide-details
        label="Show keyboard shortcut (F1) indicator"
      />
    </div>

    <SettingsSection
      v-if="isReadOnly"
      title="Privacy"
      desc="If you previously accepted the ratings cookie, your scores are remembered across visits. Clicking below clears the cookie so your next visit starts fresh with no scores retrieved."
    >
      <AppButton
        variant="secondary"
        :disabled="!hasGuestSessionCookie || clearingGuestSession"
        @click="clearGuestSession"
      >
        Clear ratings cookie
      </AppButton>
      <div v-if="!hasGuestSessionCookie" class="appearance-note">
        No ratings cookie is currently set.
      </div>
    </SettingsSection>
  </div>
</template>

<style scoped>
.appearance-selects {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-5);
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding: var(--space-5) 0;
}

.appearance-switch {
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding-top: var(--space-5);
}

.appearance-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin-top: var(--space-2);
}

.sidebar-width-toggle {
  display: flex;
  gap: var(--space-3);
}
.sidebar-width-opt {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgb(var(--v-theme-on-surface));
  font-family: inherit;
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
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
  border-radius: var(--radius-sm);
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
  font-weight: var(--weight-semibold);
}
</style>
