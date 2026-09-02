<script setup>
import { computed, ref } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import { clearGuestScoreSession } from "../../api/pictures";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { VSwitch } from "vuetify/components";
import AppSelect from "../widgets/AppSelect.vue";
import AppButton from "../widgets/AppButton.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsSliderRow from "./SettingsSliderRow.vue";

const sidebarStore = useSidebarStore();

const props = defineProps({
  sidebarThumbnailSize: { type: Number, default: 32 },
  themeMode: { type: String, default: "dark" },
  dateFormat: { type: String, default: "locale" },
  showKeyboardHint: { type: Boolean, default: true },
  thumbnailMode: { type: String, default: "square" },
});

const emit = defineEmits([
  "update:sidebar-thumbnail-size",
  "update:theme-mode",
  "update:date-format",
  "update:show-keyboard-hint",
  "update:thumbnail-mode",
]);

// Thumbnail layout: 'square' (uniform grid) vs 'justified' (variable-width rows).
// A two-option radiogroup, not a switch; both layouts are peers of a binary
// visual choice (ui-ux-expert decision). Arrow keys move between the options;
// Space/Enter selects the focused one.
const THUMBNAIL_MODES = ["square", "justified"];

// Justified needs the whole-frame aspect-ratio thumbnails. After the v1.8.0
// upgrade those regenerate in the background; until that finishes, justified
// would render old square crops stretched into variable-width slots, worse than
// square. So the Justified option is gated on the thumbnail-regeneration worker
// (the same signal the "Upgrading thumbnails" bar reads): disabled while any
// pictures still await regeneration. Reading the shared worker snapshot keeps
// this in lockstep with the progress bar and the Tasks tab.
const tasksStore = useTasksStore();
const thumbnailRegen = computed(() => {
  const s = tasksStore.workerSnapshots?.["ThumbnailGenerationTask"];
  const remaining = Number(s?.remaining) || 0;
  const total = Number(s?.total) || 0;
  const current = Number(s?.current) || 0;
  return { active: remaining > 0, remaining, total, current };
});

// Don't disable the option the user is already on (a disabled-but-checked radio
// is a dead end); the gate only blocks SWITCHING to justified mid-regeneration.
const justifiedDisabled = computed(
  () =>
    thumbnailRegen.value.active &&
    (props.thumbnailMode ?? "square") !== "justified",
);

function setThumbnailMode(next) {
  if (!THUMBNAIL_MODES.includes(next)) return;
  if (next === "justified" && justifiedDisabled.value) return;
  if (next === (props.thumbnailMode ?? "square")) return;
  emit("update:thumbnail-mode", next);
}
function onThumbnailModeKeydown(event) {
  const cur = THUMBNAIL_MODES.indexOf(props.thumbnailMode ?? "square");
  let nextIdx = null;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    nextIdx = (cur + 1) % THUMBNAIL_MODES.length;
  } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    nextIdx = (cur - 1 + THUMBNAIL_MODES.length) % THUMBNAIL_MODES.length;
  }
  if (nextIdx === null) return;
  event.preventDefault();
  // Skip past the disabled option rather than landing focus on a dead target.
  if (THUMBNAIL_MODES[nextIdx] === "justified" && justifiedDisabled.value) {
    return;
  }
  setThumbnailMode(THUMBNAIL_MODES[nextIdx]);
}

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
  get: () => props.themeMode ?? "dark",
  set: (value) => {
    const nextValue = value ?? "dark";
    if (nextValue === (props.themeMode ?? "dark")) return;
    emit("update:theme-mode", nextValue);
  },
});

// Bound straight to the sidebar store (like the Sidebar Width toggle above);
// App.vue watches sidebarStore.sidebarPinned and persists the change. The switch
// is phrased as "Auto hide sidebar" (the inverse of pinned) - the underlying
// store/persistence key stays `sidebarPinned`, so this is a label inversion only.
const sidebarAutoHideModel = computed({
  get: () => !sidebarStore.sidebarPinned,
  set: (value) => sidebarStore.setSidebarPinned(!value),
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
    await clearGuestScoreSession();
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

    <SettingsSection>
      <div class="thumb-layout-row">
        <span id="thumb-layout-label" class="thumb-layout-label"
          >Thumbnail layout</span
        >
        <div
          class="thumb-layout-toggle"
          role="radiogroup"
          aria-labelledby="thumb-layout-label"
          @keydown="onThumbnailModeKeydown"
        >
          <button
            class="thumb-layout-opt"
            :class="{ active: (props.thumbnailMode ?? 'square') === 'square' }"
            type="button"
            role="radio"
            title="Uniform squares for a tidy grid"
            :aria-checked="(props.thumbnailMode ?? 'square') === 'square'"
            :tabindex="(props.thumbnailMode ?? 'square') === 'square' ? 0 : -1"
            @click="setThumbnailMode('square')"
          >
            <span class="tli tli--square" aria-hidden="true"
              ><i></i><i></i><i></i><i></i><i></i><i></i
            ></span>
            <span class="thumb-layout-optlabel">Square</span>
          </button>
          <button
            class="thumb-layout-opt"
            :class="{
              active: props.thumbnailMode === 'justified',
              disabled: justifiedDisabled,
            }"
            type="button"
            role="radio"
            title="Each photo keeps its own shape, like Google Photos"
            :aria-checked="props.thumbnailMode === 'justified'"
            :aria-disabled="justifiedDisabled"
            :disabled="justifiedDisabled"
            :tabindex="props.thumbnailMode === 'justified' ? 0 : -1"
            @click="setThumbnailMode('justified')"
          >
            <span class="tli tli--just" aria-hidden="true"
              ><span class="tli-row"><i></i><i></i><i></i></span
              ><span class="tli-row"><i></i><i></i></span></span>
            <span class="thumb-layout-optlabel">Justified</span>
          </button>
        </div>
        <p v-if="justifiedDisabled" class="thumb-layout-notice" role="status">
          Available when thumbnails finish updating<template
            v-if="thumbnailRegen.total"
          >
            ({{ thumbnailRegen.current.toLocaleString() }} of
            {{ thumbnailRegen.total.toLocaleString() }})</template
          >.
        </p>
      </div>
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

    <div class="appearance-switch-row">
      <v-switch
        v-model="sidebarAutoHideModel"
        color="accent"
        density="compact"
        hide-details
        label="Auto hide sidebar"
      />
      <v-switch
        v-model="showKeyboardHintModel"
        color="accent"
        density="compact"
        hide-details
        label="Show keyboard shortcut indicator"
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

/* Two switches side by side (auto-hide + keyboard hint), mirroring the
   Theme / Date Format two-column row above - one row instead of two sections. */
.appearance-switch-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3) var(--space-5);
  align-items: center;
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding-top: var(--space-5);
}

.appearance-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin-top: var(--space-2);
}

/* Thumbnail-layout radiogroup: a two-option segmented control mirroring the
   Sidebar Width toggle's token treatment (accent border + wash when active),
   built as a real radiogroup for keyboard/AT. */
.thumb-layout-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.thumb-layout-label {
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}
.thumb-layout-toggle {
  display: flex;
  gap: var(--space-3);
}
.thumb-layout-opt {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-2);
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgb(var(--v-theme-on-surface));
  font-family: inherit;
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  transition:
    border-color 0.12s,
    background 0.12s,
    color 0.12s;
}
.thumb-layout-optlabel {
  font-weight: var(--weight-semibold);
}
/* Mini layout illustration: an even grid (square) vs uneven justified rows.
   currentColor → accent when the option is active (mirrors the Sidebar Width .swi). */
.tli {
  width: 64px;
  height: 40px;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  border: 1.5px solid currentColor;
  overflow: hidden;
  opacity: 0.85;
  padding: 3px;
}
.tli--square {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  grid-template-rows: repeat(2, 1fr);
  gap: 3px;
}
.tli--square i {
  background: currentColor;
  opacity: 0.5;
  border-radius: 1px;
}
.tli--just {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.tli-row {
  display: flex;
  gap: 3px;
  flex: 1;
}
.tli-row i {
  background: currentColor;
  opacity: 0.5;
  border-radius: 1px;
}
.tli--just .tli-row:first-child i:nth-child(1) {
  flex: 2;
}
.tli--just .tli-row:first-child i:nth-child(2) {
  flex: 3;
}
.tli--just .tli-row:first-child i:nth-child(3) {
  flex: 2;
}
.tli--just .tli-row:last-child i:nth-child(1) {
  flex: 3;
}
.tli--just .tli-row:last-child i:nth-child(2) {
  flex: 2;
}
.thumb-layout-opt:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}
.thumb-layout-opt.active {
  border-color: rgb(var(--v-theme-accent));
  background: rgba(var(--v-theme-accent), 0.1);
  color: rgb(var(--v-theme-accent));
}
.thumb-layout-opt.disabled,
.thumb-layout-opt:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-color: rgba(var(--v-theme-on-surface), 0.16);
  color: rgb(var(--v-theme-on-surface));
}
.thumb-layout-notice {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.75);
  line-height: var(--leading-snug);
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
  gap: var(--space-2);
  padding: var(--space-3) var(--space-2);
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgb(var(--v-theme-on-surface));
  font-family: inherit;
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
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
  width: 64px;
  height: 40px;
  flex-shrink: 0;
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
  width: 20px;
}
.swi--dock .swi-rail {
  width: 9px;
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
