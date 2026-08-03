import { computed, markRaw, nextTick, ref, shallowRef } from "vue";
import { defineStore } from "pinia";

import { listLibraries, setActiveLibrary } from "../api/libraries";
import { reloadPage } from "../utils/reloadPage";

/**
 * App-level owner view of the hub registry.
 *
 * The shell and Settings consume the same response so the active-library name
 * cannot drift from the row marked Active. Share/read-only sessions never call
 * this store; App.vue owns that authorization-aware startup decision.
 */
export const useLibrariesStore = defineStore("libraries", () => {
  const libraries = ref([]);
  const canManage = ref(false);
  const cliHint = ref("");
  const inDocker = ref(false);
  const loading = ref(false);
  const loadError = ref("");
  const hasLoadedSuccessfully = ref(false);

  const activeLibrary = computed(() =>
    libraries.value.find((library) => library.is_active),
  );

  async function refresh() {
    loading.value = true;
    loadError.value = "";
    try {
      const body = await listLibraries();
      libraries.value = body?.libraries ?? [];
      canManage.value = Boolean(body?.can_manage);
      cliHint.value = body?.cli_hint ?? "";
      inDocker.value = Boolean(body?.in_docker);
      hasLoadedSuccessfully.value = true;
    } catch (error) {
      hasLoadedSuccessfully.value = false;
      loadError.value =
        error?.response?.data?.detail ||
        "Could not read the list of libraries.";
    } finally {
      loading.value = false;
    }
  }

  return {
    libraries,
    canManage,
    cliHint,
    inDocker,
    loading,
    loadError,
    hasLoadedSuccessfully,
    activeLibrary,
    refresh,
  };
});

/**
 * One app-wide switch state machine.
 *
 * Keeping it above Settings lets the blocking surface make the whole retired
 * application inert. The target button stays mounted underneath so a failed
 * switch can restore focus to the exact action that opened the flow.
 */
export const useLibrarySwitchStore = defineStore("library-switch", () => {
  const phase = ref("idle"); // idle | switching | failed
  const targetLibrary = ref(null);
  const currentLibrary = ref(null);
  const error = ref("");
  const triggerElement = shallowRef(null);

  const overlayOpen = computed(() => phase.value !== "idle");

  async function begin(target, current, trigger) {
    if (overlayOpen.value) return;

    targetLibrary.value = target;
    currentLibrary.value = current;
    triggerElement.value = trigger instanceof HTMLElement ? markRaw(trigger) : null;
    error.value = "";
    phase.value = "switching";

    // Render the modal and apply `inert` before starting the request. The
    // second tick lets LibrarySwitchOverlay inert Vuetify dialogs that were
    // already teleported beside VApp (Settings, Shortcuts, ...). This prevents
    // a fast click or key repeat from reaching any background surface.
    await nextTick();
    await nextTick();

    try {
      await setActiveLibrary(target.uuid);
      reloadPage();
    } catch (requestError) {
      error.value =
        requestError?.response?.data?.detail ||
        `Could not switch to ${target.name}.`;
      phase.value = "failed";
    }
  }

  async function stayOnCurrent() {
    const returnTarget = triggerElement.value;
    phase.value = "idle";
    targetLibrary.value = null;
    currentLibrary.value = null;
    error.value = "";
    triggerElement.value = null;
    await nextTick();
    returnTarget?.focus?.();
  }

  return {
    phase,
    targetLibrary,
    currentLibrary,
    error,
    overlayOpen,
    begin,
    stayOnCurrent,
  };
});
