import { onUnmounted } from "vue";
import { API_BASE_URL, isReadOnly } from "../utils/apiClient";
import { useDedupStore } from "../stores/useDedupStore";
import { useEntityListsStore } from "../stores/useEntityListsStore";
import { useLockedSetsStore } from "../stores/useLockedSetsStore";

const SIDEBAR_REFRESH_DEBOUNCE_MS = 150;
const SIDEBAR_REFRESH_PICTURES_DEBOUNCE_MS = 800;

/**
 * Debounced sidebar refreshes.
 *
 * A burst of websocket events would otherwise refetch the sidebar's entity
 * lists once per event; these coalesce that into one refresh. The pictures
 * variant is debounced far longer because it is the expensive one, and it
 * carries a flash flag so the refresh that lands can highlight what changed.
 *
 * @param {object} deps
 * @param {import("vue").Ref} deps.sidebarRef - the sidebar's template ref.
 */
export function useSidebarRefresh({ sidebarRef }) {
  const dedupStore = useDedupStore();
  const entityListsStore = useEntityListsStore();
  const lockedSetsStore = useLockedSetsStore();

  let sidebarRefreshDebounceTimeout = null;
  let sidebarRefreshPicturesDebounceTimeout = null;
  let sidebarRefreshPicturesFlash = false;

  function refreshSidebar(options = {}) {
    sidebarRef.value?.refreshSidebar(options);
    // The shared character/set/project lists ride the same triggers (this is what
    // `characters_changed` lands on). Refetch ONLY - a ws payload never writes
    // into the cache, since `origin_client_id` is echo-matching, not authority
    // (integration_architecture.md §8.1). The call is de-duplicated against the
    // sidebar's own refresh above, and it also covers the case where the sidebar
    // is unmounted but a context menu is still reading the lists.
    entityListsStore.invalidate(undefined, { baseUrl: API_BASE_URL });
    // The locked-sets store shares the sidebar's refresh triggers (manual emits,
    // characters_changed, and pictures_changed via the debounced pictures path,
    // which also fires on a lock/unlock PATCH's CHANGED_PICTURES event). The store
    // coalesces overlapping fetches, so calling it here on every refresh is cheap.
    lockedSetsStore.fetch();
    // The duplicates badge rides the same triggers: an import, a stack or a
    // verdict all move the count, and every one of them already causes a sidebar
    // refresh. The per-scope cache goes with it, since a context menu opened
    // afterwards must not quote a pre-change number.
    if (!isReadOnly.value) {
      dedupStore.invalidateScopeCounts();
      dedupStore.refreshCounts();
    }
  }

  function refreshSidebarDebounced() {
    if (sidebarRefreshDebounceTimeout) {
      clearTimeout(sidebarRefreshDebounceTimeout);
    }
    sidebarRefreshDebounceTimeout = setTimeout(() => {
      sidebarRefreshDebounceTimeout = null;
      refreshSidebar();
    }, SIDEBAR_REFRESH_DEBOUNCE_MS);
  }

  function refreshSidebarPicturesDebounced(flash) {
    if (flash) sidebarRefreshPicturesFlash = true;
    if (sidebarRefreshPicturesDebounceTimeout) {
      clearTimeout(sidebarRefreshPicturesDebounceTimeout);
    }
    sidebarRefreshPicturesDebounceTimeout = setTimeout(() => {
      sidebarRefreshPicturesDebounceTimeout = null;
      const doFlash = sidebarRefreshPicturesFlash;
      sidebarRefreshPicturesFlash = false;
      refreshSidebar(doFlash ? { flashCounts: true } : {});
    }, SIDEBAR_REFRESH_PICTURES_DEBOUNCE_MS);
  }

  onUnmounted(() => {
    if (sidebarRefreshDebounceTimeout) {
      clearTimeout(sidebarRefreshDebounceTimeout);
      sidebarRefreshDebounceTimeout = null;
    }
    if (sidebarRefreshPicturesDebounceTimeout) {
      clearTimeout(sidebarRefreshPicturesDebounceTimeout);
      sidebarRefreshPicturesDebounceTimeout = null;
    }
  });

  return {
    refreshSidebar,
    refreshSidebarDebounced,
    refreshSidebarPicturesDebounced,
  };
}
