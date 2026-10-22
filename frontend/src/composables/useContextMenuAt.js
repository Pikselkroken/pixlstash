// A selection bar's card menu, opened at a point for the grid's right-click
// and Menu key.
//
// Opened from code, so v-menu has no activator to hand focus back to and drops
// it on <body>. The card that asked is refocused on close, unless a verb moved
// focus somewhere on purpose (a rename or Replace dialog).

import { ref, watch } from "vue";

/**
 * @returns {{contextOpen: import("vue").Ref<boolean>,
 *   contextAt: import("vue").Ref<number[]>,
 *   openContextMenu: (x: number, y: number, trigger?: Element|null) => void}}
 *   `contextOpen` and `contextAt` bind to the v-menu's `v-model` and `target`.
 */
export function useContextMenuAt() {
  const contextOpen = ref(false);
  const contextAt = ref([0, 0]);
  let menuTrigger = null;

  function openContextMenu(x, y, trigger = document.activeElement) {
    menuTrigger = trigger;
    contextAt.value = [x, y];
    contextOpen.value = true;
  }

  watch(contextOpen, (open) => {
    if (open || !menuTrigger) return;
    const trigger = menuTrigger;
    menuTrigger = null;
    setTimeout(() => {
      const active = document.activeElement;
      if (!active || active === document.body) trigger.focus?.();
    }, 0);
  });

  return { contextOpen, contextAt, openContextMenu };
}
