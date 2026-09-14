<script setup>
import { computed, ref } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import { clearGuestScoreSession } from "../../api/pictures";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { VSwitch } from "vuetify/components";
import AppSelect from "../widgets/AppSelect.vue";
import AppButton from "../widgets/AppButton.vue";
import Segmented from "../widgets/Segmented.vue";
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
// visual choice (ui-ux-expert decision).

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

const thumbnailModeOptions = computed(() => [
  { id: "square", label: "Square" },
  { id: "justified", label: "Justified", disabled: justifiedDisabled.value },
]);

const SIDEBAR_WIDTH_OPTIONS = [
  { id: "full", label: "Full" },
  { id: "dock", label: "Dock" },
];

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

    <!-- Thumbnail layout + Sidebar Width share one row, side by side. The grid
         flows by column, so each setting's title, desc and control line up
         with its neighbour's. -->
    <SettingsSection>
      <div class="pair-set">
        <div class="pair-title">Thumbnail layout</div>
        <div class="pair-desc">How pictures sit in the grid.</div>
        <div class="pair-body">
          <Segmented
            class="pair-seg"
            full
            :model-value="props.thumbnailMode ?? 'square'"
            :options="thumbnailModeOptions"
            variant="stacked"
            aria-label="Thumbnail layout"
            @update:model-value="(id) => emit('update:thumbnail-mode', id)"
          >
            <template #media="{ option }">
              <span
                v-if="option.id === 'square'"
                class="tli tli--square"
                aria-hidden="true"
                ><i></i><i></i><i></i><i></i><i></i><i></i
              ></span>
              <span v-else class="tli tli--just" aria-hidden="true"
                ><span class="tli-row"><i></i><i></i><i></i></span
                ><span class="tli-row"><i></i><i></i></span
              ></span>
            </template>
          </Segmented>
          <p v-if="justifiedDisabled" class="thumb-layout-notice" role="status">
            Available when thumbnails finish updating<template
              v-if="thumbnailRegen.total"
            >
              ({{ thumbnailRegen.current.toLocaleString() }} of
              {{ thumbnailRegen.total.toLocaleString() }})</template
            >.
          </p>
        </div>

        <div class="pair-title">Sidebar Width</div>
        <div class="pair-desc">Full width, or a narrow icon dock.</div>
        <div class="pair-body">
          <Segmented
            class="pair-seg"
            full
            :model-value="sidebarStore.sidebarDocked ? 'dock' : 'full'"
            :options="SIDEBAR_WIDTH_OPTIONS"
            variant="stacked"
            aria-label="Sidebar width"
            @update:model-value="
              (id) => sidebarStore.setSidebarDocked(id === 'dock')
            "
          >
            <template #media="{ option }">
              <span :class="['swi', `swi--${option.id}`]" aria-hidden="true">
                <span class="swi-rail"></span>
                <span class="swi-content"></span>
              </span>
            </template>
          </Segmented>
        </div>
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
        color="primary"
        density="compact"
        hide-details
        label="Auto hide sidebar"
      />
      <v-switch
        v-model="showKeyboardHintModel"
        color="primary"
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

/* Two settings in one row: three rows (title, desc, control) flowed by column,
   so the titles, descs and controls align across the pair. Title and desc
   mirror SettingsSection's own. */
.pair-set {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: auto auto auto;
  grid-auto-flow: column;
  column-gap: var(--space-7);
  align-items: start;
}
.pair-title {
  font-weight: var(--weight-semibold);
  font-size: var(--text-base);
  margin-bottom: var(--space-2);
}
.pair-desc {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  line-height: var(--leading-snug);
  margin-bottom: var(--space-3);
}
.pair-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}
.pair-seg {
  max-width: 320px;
}
/* Half a phone-width pane is narrower than two stacked segments; stack the
   pair instead (same breakpoint as the dialog's own narrow layout). */
@media (max-width: 480px) {
  .pair-set {
    grid-template-columns: 1fr;
    grid-template-rows: none;
    grid-auto-flow: row;
  }
  .pair-body + .pair-title {
    margin-top: var(--space-5);
  }
}
/* Mini layout illustration: an even grid (square) vs uneven justified rows.
   currentColor, so it follows the option's ink (mirrors the Sidebar Width .swi). */
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
.thumb-layout-notice {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.75);
  line-height: var(--leading-snug);
}

/* Mini layout illustration: a window frame with a filled left rail (wide for
   full, narrow for dock) over a dotted content area. currentColor, so it follows
   the option's ink: no olive glyph on the olive selection wash. */
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
  width: 28%;
}
.swi--dock .swi-rail {
  width: 11%;
}
.swi-content {
  flex: 1;
  background: radial-gradient(currentColor 1px, transparent 1.5px) 0 0 / 7px 7px;
  opacity: 0.45;
}
</style>
