import { computed, inject, reactive } from "vue";

/**
 * Which inspector a section sits in. The grid's rail provides `"rail"`; the
 * lightbox's panels fall back to `"lightbox"`, so the same panel keeps a
 * separate collapsed state in each place it is mounted.
 */
export const SECTION_SCOPE = Symbol("sectionScope");

const STORAGE_KEY = "pixlstash:collapsedSections";

function load() {
  try {
    const raw = window.localStorage?.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (e) {
    // A corrupt or unreadable entry costs only the remembered collapse state;
    // every section then opens expanded.
    console.warn("Couldn't read the collapsed inspector sections", e);
    return {};
  }
}

// One map for the whole app, outside any component: a section that unmounts
// with its picture (one selected, then several, then one again) finds its
// state where it left it.
const collapsed = reactive(load());

function save() {
  try {
    window.localStorage?.setItem(STORAGE_KEY, JSON.stringify(collapsed));
  } catch (e) {
    console.warn("Couldn't save the collapsed inspector sections", e);
  }
}

/**
 * A section's collapsed flag, remembered per inspector and across sessions.
 *
 * Call from `setup`. The scope is injected from the inspector around the
 * caller; the inspector itself, which cannot inject what it provides, names
 * its own.
 *
 * @param {string} name - the section's stable name ("tags", "faces").
 * @param {Object} [options]
 * @param {boolean} [options.defaultCollapsed=false]
 * @param {string} [options.scope] - the inspector, when the caller is it, or
 *   a place outside any inspector (the sidebar's People and Sets: "sidebar").
 * @returns {import("vue").WritableComputedRef<boolean>}
 */
export function useSectionCollapse(
  name,
  { defaultCollapsed = false, scope } = {},
) {
  const key = `${scope ?? inject(SECTION_SCOPE, "lightbox")}:${name}`;
  return computed({
    get: () => collapsed[key] ?? defaultCollapsed,
    set: (value) => {
      collapsed[key] = Boolean(value);
      save();
    },
  });
}
