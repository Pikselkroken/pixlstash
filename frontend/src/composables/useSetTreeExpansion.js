import { ref, watch } from "vue";

// Which parents in the sidebar's People and Sets trees are open (#2124). A
// per-browser view preference, so it stays on the client: one JSON array of
// keys (`c:<character id>`, `s:<set id>`) under its own localStorage key.
// Parents start closed, so the open ones are what is stored, capped so the
// list cannot grow forever; losing a key only means that parent starts closed.

const STORAGE_KEY = "pixlstash:sidebar:tree";
const MAX_KEYS = 500;

function load() {
  try {
    const parsed = JSON.parse(window.localStorage?.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((key) => typeof key === "string" && key !== "")
      : [];
  } catch (e) {
    console.warn(
      "Sidebar tree state could not be read from localStorage; every parent starts closed.",
      e,
    );
    return [];
  }
}

/**
 * The open parents of the sidebar's child-set tree, hydrated from and written
 * back to localStorage.
 *
 * @returns {{expandedTreeKeys: import("vue").Ref<Set<string>>,
 *   toggleTreeKey: (key: string) => void,
 *   expandTreeKeys: (keys: string[]) => void}}
 */
export function useSetTreeExpansion() {
  const expandedTreeKeys = ref(new Set(load().slice(-MAX_KEYS)));
  let warned = false;

  watch(expandedTreeKeys, (keys) => {
    try {
      window.localStorage?.setItem(
        STORAGE_KEY,
        JSON.stringify([...keys].slice(-MAX_KEYS)),
      );
    } catch (e) {
      // Warn once: a browser refusing storage would otherwise log per toggle.
      if (!warned) {
        warned = true;
        console.warn(
          "Sidebar tree state could not be saved (localStorage unavailable); it won't survive a reload.",
          e,
        );
      }
    }
  });

  function toggleTreeKey(key) {
    const next = new Set(expandedTreeKeys.value);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    expandedTreeKeys.value = next;
  }

  /** Open every key given (the branch a selection lives in); never closes. */
  function expandTreeKeys(keys) {
    const missing = keys.filter((key) => !expandedTreeKeys.value.has(key));
    if (missing.length) {
      expandedTreeKeys.value = new Set([...expandedTreeKeys.value, ...missing]);
    }
  }

  return { expandedTreeKeys, toggleTreeKey, expandTreeKeys };
}
