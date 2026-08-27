// Registry for bottom-anchored floating chrome (notice-surface.md §2.2).
//
// The spec's rule is "whoever parks something on the bottom edge owns
// `--floating-bottom-h`". This composable is that ownership, made literal: the
// component that renders a bottom-anchored floating element registers it here,
// and App.vue - which owns the variable - reads one derived number and publishes
// it to `.app-viewport`.
//
// Why a registry and not a ResizeObserver in App.vue: the elements do not live
// in App.vue. The selection pill is rendered by SelectionBar inside ImageGrid's
// `.grid-content-area`, and the breadcrumb by ImageGrid. Reaching across two
// component boundaries with a querySelector would couple App.vue to another
// component's class names - and to the notice host's own placement rule, which
// is exactly the coupling §2.3 says to avoid. Registration inverts that: a new
// bottom-anchored element opts in with one call, and nothing in App.vue or the
// notice host changes.
//
// Heights are MEASURED, never assumed. The pill wraps, grows on coarse pointers,
// and changes height with its own content; the spec is explicit that 56px is a
// first-frame fallback and not a design token.

import { computed, onBeforeUnmount, ref, watch } from "vue";
import {
  computeFloatingBottomInset,
  NARROW_VIEWPORT_MAX_PX,
} from "../utils/floatingBottom";

// name → { height, visible, narrowOnly }. Module scope: exactly one bottom edge.
const anchors = ref({});

// Whether the viewport is narrow enough that the centred notice card widens over
// the bottom-left breadcrumb. Kept here so every consumer agrees on the answer.
const isNarrowViewport = ref(false);
let narrowMql = null;
let narrowRefCount = 0;

function onNarrowChange(event) {
  isNarrowViewport.value = event.matches;
}

function ensureNarrowWatcher() {
  narrowRefCount += 1;
  if (narrowMql || typeof window === "undefined" || !window.matchMedia) return;
  narrowMql = window.matchMedia(`(max-width: ${NARROW_VIEWPORT_MAX_PX}px)`);
  isNarrowViewport.value = narrowMql.matches;
  narrowMql.addEventListener("change", onNarrowChange);
}

function releaseNarrowWatcher() {
  narrowRefCount = Math.max(0, narrowRefCount - 1);
  if (narrowRefCount === 0 && narrowMql) {
    narrowMql.removeEventListener("change", onNarrowChange);
    narrowMql = null;
  }
}

function setAnchor(name, patch) {
  anchors.value = {
    ...anchors.value,
    [name]: { ...(anchors.value[name] ?? {}), ...patch },
  };
}

function removeAnchor(name) {
  const next = { ...anchors.value };
  delete next[name];
  anchors.value = next;
}

/**
 * Register a bottom-anchored floating element so the notice stack can sit clear
 * of it. Call from the component that renders the element.
 *
 * @param {string} name - unique key, e.g. `"selection-bar"`.
 * @param {import('vue').Ref<HTMLElement|null>} elRef - template ref. A `null`
 *   value (the element is `v-if`'d out) marks the anchor not visible.
 * @param {Object} [options]
 * @param {boolean} [options.narrowOnly=false] - the element only sits inside the
 *   notice column's footprint on narrow viewports (the grid breadcrumb: above
 *   600px it is bottom-left, well clear of the centred card).
 */
export function useBottomAnchor(name, elRef, { narrowOnly = false } = {}) {
  let observer = null;

  function stopObserving() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  watch(
    elRef,
    (el) => {
      stopObserving();
      if (!el) {
        // Not rendered → contributes nothing. Keep the entry so a later mount
        // reuses it rather than racing a removal.
        setAnchor(name, { height: 0, visible: false, narrowOnly });
        return;
      }
      setAnchor(name, {
        height: el.offsetHeight || 0,
        visible: true,
        narrowOnly,
      });
      if (typeof ResizeObserver === "undefined") return;
      observer = new ResizeObserver((entries) => {
        for (const entry of entries) {
          // `borderBoxSize` includes padding and border, which is what the
          // stack has to clear. Fall back to offsetHeight where unsupported.
          const box = entry.borderBoxSize?.[0];
          const height = box ? box.blockSize : entry.target.offsetHeight;
          setAnchor(name, { height: height || 0, visible: true, narrowOnly });
        }
      });
      observer.observe(el);
    },
    { immediate: true, flush: "post" },
  );

  onBeforeUnmount(() => {
    stopObserving();
    removeAnchor(name);
  });
}

/**
 * The measured height of ONE registered anchor, or 0 when it is not visible.
 *
 * The notice stack only ever needs the tallest, but a second bottom-anchored
 * element that stacks ON TOP of another (the action receipt above the selection
 * pill) needs that one element's height to lift itself clear. Reading it from
 * the registry keeps the height measured rather than guessed - the spec is
 * explicit that the pill's 56px is a first-frame fallback and not a token.
 *
 * @param {string} name - the key the element registered under.
 * @returns {{height: import('vue').ComputedRef<number>}} pixels, 0 when hidden.
 */
export function useAnchorHeight(name) {
  const height = computed(() => {
    const anchor = anchors.value[name];
    if (!anchor?.visible) return 0;
    const value = Number(anchor.height);
    return Number.isFinite(value) && value > 0 ? value : 0;
  });
  return { height };
}

/**
 * The height bottom-anchored floating chrome currently occupies in the notice
 * column's footprint, including the gap that must sit above it.
 *
 * @returns {{inset: import('vue').ComputedRef<number>, narrow: import('vue').Ref<boolean>}}
 */
export function useFloatingBottomInset() {
  ensureNarrowWatcher();
  onBeforeUnmount(releaseNarrowWatcher);

  const inset = computed(() =>
    computeFloatingBottomInset({
      anchors: Object.values(anchors.value),
      narrow: isNarrowViewport.value,
    }),
  );

  return { inset, narrow: isNarrowViewport };
}
