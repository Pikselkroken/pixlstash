// useProjectStore.js - which project the app is scoped to, and the entity →
// project mappings the grid uses to resolve that scope.
//
// ── Security (issue #655, found by the store matrix rather than the issue's
// own item list) ────────────────────────────────────────────────────────────
// `characterProjectIds` and `setProjectIds` are id → project-id maps published
// by the sidebar from the same `SCOPED_LIST` reads that feed
// `useEntityNamesStore` (see `composables/useAppNavigation.js`, which assigns
// them on every character/set selection). `useGridFetch` reads them back to
// decide a picture's project context. That makes their CONTENT an
// authorization decision from the previous credential, in exactly the class
// this store was not originally listed under, so `reset()` is wired to the
// same `onSessionReset` chokepoint in `utils/apiClient.js`.
//
// `selectedProjectId` goes with them: a project id from one credential
// silently narrowing the next one's view is over-blocking, which is its own
// regression even though it leaks nothing.

import { ref, onScopeDispose } from "vue";
import { defineStore } from "pinia";
import { onSessionReset } from "../utils/apiClient";

export const useProjectStore = defineStore("project", () => {
  const projectViewMode = ref("global"); // 'global' | 'project'
  const selectedProjectId = ref(null);
  const characterProjectIds = ref({});
  const setProjectIds = ref({});

  /** Drop the previous credential's project scope and mappings. */
  function reset() {
    projectViewMode.value = "global";
    selectedProjectId.value = null;
    characterProjectIds.value = {};
    setProjectIds.value = {};
  }

  // Written by the sidebar, never fetched here, so there is no in-flight
  // response to guard - only the accumulated maps and the active scope.
  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    projectViewMode,
    selectedProjectId,
    characterProjectIds,
    setProjectIds,
    reset,
  };
});
