// useScrapheapRetentionStore.js - the scrapheap auto-empty retention window.
//
// Server-level setting (persisted to server-config.json, not per-user), read
// from and written to `/server-config/scrapheap-retention` via
// `src/api/serverConfig.js`.
//
// It lives in a store rather than in the settings section because three
// surfaces read the same value: the Settings control that edits it, the
// scrapheap view header that states the active policy, and - indirectly - the
// per-tile countdown, which needs to know whether a policy is active at all.
// One fetch, one truth, no prop-drilling between the settings dialog and the
// grid.
//
// What this store deliberately does NOT do: compute purge dates. The server
// stamps each scrapheap picture with an absolute `purge_at` that already
// carries the grace period applied when the window is shortened. The retention
// window here is display/edit state only.

import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { errorDetail } from "../utils/apiError";
import {
  getScrapheapRetention,
  setScrapheapRetentionDays,
  SCRAPHEAP_RETENTION_CHOICES_FIELD,
  SCRAPHEAP_RETENTION_FIELD,
  SCRAPHEAP_RETENTION_GRACE_FIELD,
} from "../api/serverConfig";
import {
  DEFAULT_RETENTION_DAYS,
  RETENTION_DAY_OPTIONS,
  normalizeRetentionDays,
  retentionLabel,
} from "../utils/retention";

export const useScrapheapRetentionStore = defineStore(
  "scrapheapRetention",
  () => {
    // `null` is a meaningful value ("Never"), so "not fetched yet" is tracked
    // separately by `loaded` rather than by a null sentinel. The pre-fetch value
    // is the shipped default, which is `null`: before the server answers we must
    // not imply a countdown is running that the user never turned on.
    const retentionDays = ref(DEFAULT_RETENTION_DAYS);
    // Declared by the server so the select can never offer a window the API
    // would reject with a 422; the local list is only the pre-fetch fallback.
    const choices = ref([...RETENTION_DAY_OPTIONS]);
    // Extra days granted to pictures already in the scrapheap when the window
    // is lowered. Drives the tooltip copy, so it is read, not hardcoded.
    const graceDays = ref(1);
    const loaded = ref(false);
    const loading = ref(false);
    const saving = ref(false);
    const error = ref("");

    /** Human label for the active policy, e.g. "30 days" / "Never". */
    const label = computed(() => retentionLabel(retentionDays.value));

    /** True when the policy is "Never" - nothing is auto-removed. */
    const isNever = computed(() => retentionDays.value === null);

    // De-duplicates concurrent first loads (the settings section and the grid
    // can both ask on the same tick).
    let inflight = null;

    /**
     * Absorb the server's declared choices / grace period from a response.
     * Both are optional: an older server that omits them keeps the defaults.
     * @param {Object} data - the endpoint's response body.
     */
    function applyServerMetadata(data) {
      const serverChoices = data?.[SCRAPHEAP_RETENTION_CHOICES_FIELD];
      if (Array.isArray(serverChoices)) {
        const parsed = serverChoices
          .map((d) => Number(d))
          .filter((d) => Number.isFinite(d) && d > 0);
        if (parsed.length) choices.value = parsed;
      }
      const serverGrace = Number(data?.[SCRAPHEAP_RETENTION_GRACE_FIELD]);
      if (Number.isFinite(serverGrace) && serverGrace >= 0) {
        graceDays.value = serverGrace;
      }
    }

    /**
     * Load the retention window from the server.
     * @param {{force?: boolean}} [options] - `force` refetches an already-loaded value.
     * @returns {Promise<number|null>} the retention in days, or null for Never.
     */
    async function fetchRetention({ force = false } = {}) {
      if (loaded.value && !force) return retentionDays.value;
      if (inflight) return inflight;
      loading.value = true;
      inflight = (async () => {
        try {
          const data = await getScrapheapRetention();
          retentionDays.value = normalizeRetentionDays(
            data?.[SCRAPHEAP_RETENTION_FIELD],
          );
          applyServerMetadata(data);
          loaded.value = true;
          error.value = "";
        } catch (err) {
          // Non-fatal: the scrapheap still works without knowing the policy, so
          // the UI keeps the last known value and simply omits the policy line.
          // Logged with the endpoint context so a contract mismatch is visible.
          console.warn(
            "Failed to load scrapheap retention from " +
              "/server-config/scrapheap-retention; keeping the last known value.",
            err,
          );
          error.value =
            errorDetail(err) ||
            err?.message ||
            "Failed to load the scrapheap retention setting.";
        } finally {
          loading.value = false;
          inflight = null;
        }
        return retentionDays.value;
      })();
      return inflight;
    }

    /**
     * Persist a new retention window. Optimistic, with rollback on failure.
     * @param {number|null} days - 30 / 60 / 90 / 120, or null for Never.
     * @returns {Promise<void>} rejects with the original error after rollback.
     */
    async function setRetention(days) {
      const next = days === null ? null : normalizeRetentionDays(days);
      const previous = retentionDays.value;
      if (next === previous && loaded.value) return;
      retentionDays.value = next;
      saving.value = true;
      error.value = "";
      try {
        const data = await setScrapheapRetentionDays(next);
        // Trust the server's echo when it sends one, so a clamp or coercion
        // server-side is reflected instead of silently diverging.
        if (data && SCRAPHEAP_RETENTION_FIELD in data) {
          retentionDays.value = normalizeRetentionDays(
            data[SCRAPHEAP_RETENTION_FIELD],
            next,
          );
        }
        applyServerMetadata(data);
        loaded.value = true;
      } catch (err) {
        retentionDays.value = previous;
        console.error(
          `Failed to save scrapheap retention (requested: ${String(next)} days, ` +
            `rolled back to: ${String(previous)}).`,
          err,
        );
        error.value =
          errorDetail(err) ||
          err?.message ||
          "Failed to update the scrapheap retention setting.";
        throw err;
      } finally {
        saving.value = false;
      }
    }

    return {
      // state
      retentionDays,
      choices,
      graceDays,
      loaded,
      loading,
      saving,
      error,
      // computed
      label,
      isNever,
      // actions
      fetchRetention,
      setRetention,
    };
  },
);
