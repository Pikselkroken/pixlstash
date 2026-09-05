import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { isReadOnly } from "../utils/apiClient";
import { patchUserConfig } from "../api/config";

export const useUserPrefsStore = defineStore("userPrefs", () => {
  const dateFormat = ref("locale");
  const themeMode = ref("dark");
  const showKeyboardHint = ref(true);
  const hiddenTags = ref([]);
  const applyTagFilter = ref(false);
  const penalisedTagWeights = ref({});
  const checkForUpdates = ref(null); // null = undecided, true/false = user choice
  const sidebarThumbnailSize = ref(isReadOnly.value ? 32 : 48);
  // Expanded (non-docked) sidebar width in px. Drag-resizable, clamped 120–300.
  const sidebarWidth = ref(240);
  const publicUrl = ref(null);
  const embedWatermark = ref(false);
  // Dismissal of the "deleted pictures still in snapshots" warning shown after
  // a purge. Persisted server-side per user (hydrated from the user config in
  // App.vue), so it follows the account across devices/browsers.
  const hidePurgeSnapshotWarning = ref(false);

  // Opt-in telemetry. Every category is off until the user turns it on, on
  // every install and every install type. `telemetryConsentPrompted` records
  // that the question has been put to them, so it is asked exactly once and
  // never re-raised: declining, or dismissing the dialog, is a recorded
  // decision rather than an unanswered prompt.
  const telemetrySendInstallId = ref(false);
  const telemetrySendFeatureUsage = ref(false);
  const telemetrySendErrorReports = ref(false);
  const telemetrySendHardwareProfile = ref(false);
  const telemetryConsentPrompted = ref(false);

  /** True when any telemetry category is on. Drives the sidebar indicator. */
  const telemetryActive = computed(
    () =>
      telemetrySendInstallId.value ||
      telemetrySendFeatureUsage.value ||
      telemetrySendErrorReports.value ||
      telemetrySendHardwareProfile.value,
  );

  /**
   * Persist a consent/settings patch and mirror its relevant fields locally.
   *
   * The local refs are updated only after the PATCH resolves. An optimistic
   * update here would leave the sidebar indicator claiming telemetry is on
   * while the server still has it off, which is the one direction this
   * indicator must never be wrong in.
   */
  async function saveTelemetry(patch) {
    if (isReadOnly.value) return false;
    try {
      await patchUserConfig(patch);
    } catch (e) {
      console.error("Failed to persist telemetry settings:", e);
      return false;
    }
    const apply = {
      check_for_updates: checkForUpdates,
      telemetry_send_install_id: telemetrySendInstallId,
      telemetry_send_feature_usage: telemetrySendFeatureUsage,
      telemetry_send_error_reports: telemetrySendErrorReports,
      telemetry_send_hardware_profile: telemetrySendHardwareProfile,
      telemetry_consent_prompted: telemetryConsentPrompted,
    };
    for (const [key, value] of Object.entries(patch)) {
      if (apply[key]) apply[key].value = Boolean(value);
    }
    return true;
  }

  /** Hydrate the telemetry refs from a fetched user-config payload. */
  function hydrateTelemetry(cfg) {
    telemetrySendInstallId.value = Boolean(cfg?.telemetry_send_install_id);
    telemetrySendFeatureUsage.value = Boolean(
      cfg?.telemetry_send_feature_usage,
    );
    telemetrySendErrorReports.value = Boolean(
      cfg?.telemetry_send_error_reports,
    );
    telemetrySendHardwareProfile.value = Boolean(
      cfg?.telemetry_send_hardware_profile,
    );
    telemetryConsentPrompted.value = Boolean(cfg?.telemetry_consent_prompted);
  }

  async function setHidePurgeSnapshotWarning(val) {
    const next = Boolean(val);
    if (hidePurgeSnapshotWarning.value === next) return;
    hidePurgeSnapshotWarning.value = next;
    // Read-only/scoped tokens cannot patch user config (and never reach the
    // purge flow that shows this dialog), so skip the request for them.
    if (isReadOnly.value) return;
    try {
      await patchUserConfig({ hide_purge_snapshot_warning: next });
    } catch (e) {
      console.error("Failed to persist hide_purge_snapshot_warning:", e);
    }
  }

  // Guarded setters. Each of these preferences drives a watcher (persisting to
  // the server, and for a few of them repainting the grid), so writing an
  // invalid value or rewriting the same one is not free - it costs a PATCH and
  // sometimes a refetch. The guards used to sit in App.vue's handlers; they
  // belong with the state so every writer gets them.
  function setHiddenTags(tags) {
    const next = Array.isArray(tags) ? tags : [];
    if (
      hiddenTags.value.length === next.length &&
      hiddenTags.value.every((tag, i) => tag === next[i])
    ) {
      return;
    }
    hiddenTags.value = next;
  }

  function setApplyTagFilter(value) {
    const next = Boolean(value);
    if (applyTagFilter.value === next) return;
    applyTagFilter.value = next;
  }

  function setDateFormat(value) {
    if (value == null) return;
    const next = String(value);
    if (next === dateFormat.value) return;
    dateFormat.value = next;
  }

  function setThemeMode(value) {
    if (value == null) return;
    themeMode.value = String(value);
  }

  function setSidebarThumbnailSize(value) {
    const next = Number(value);
    if (!Number.isFinite(next)) return;
    sidebarThumbnailSize.value = next;
  }

  function setSidebarWidth(value) {
    const next = Number(value);
    if (!Number.isFinite(next)) return;
    sidebarWidth.value = next;
  }

  return {
    setHiddenTags,
    setApplyTagFilter,
    setDateFormat,
    setThemeMode,
    setSidebarThumbnailSize,
    setSidebarWidth,
    dateFormat,
    themeMode,
    showKeyboardHint,
    hiddenTags,
    applyTagFilter,
    penalisedTagWeights,
    checkForUpdates,
    sidebarThumbnailSize,
    sidebarWidth,
    publicUrl,
    embedWatermark,
    hidePurgeSnapshotWarning,
    setHidePurgeSnapshotWarning,
    telemetrySendInstallId,
    telemetrySendFeatureUsage,
    telemetrySendErrorReports,
    telemetrySendHardwareProfile,
    telemetryConsentPrompted,
    telemetryActive,
    saveTelemetry,
    hydrateTelemetry,
  };
});
