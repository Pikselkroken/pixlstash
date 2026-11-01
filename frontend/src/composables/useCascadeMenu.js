// The cascade the filter menus share (the picture grid's `FilterMenu`, the
// Workflows screen's `WorkflowFilterMenu` and the Models shelf's
// `ShelfShowPanel`): a root list of rows, each opening one submenu beside it,
// so somebody who has learnt one knows the others.

import { getCurrentInstance, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";

/**
 * @param {() => boolean} isOpen - the v-menu keeps its content mounted, so a
 *   closed menu says so here and reopens on the root alone.
 * @param {(submenu: ?HTMLElement) => void} focusIn - focuses into a submenu
 *   once it has rendered; never the header.
 */
export function useCascadeMenu(isOpen, focusIn) {
  const sub = ref(null);
  const subTop = ref(0);
  const rootRef = ref(null);
  const subRef = ref(null);
  const rowRefs = reactive({});
  // The open submenu's row, which it lines up with when it can.
  let rowTop = 0;
  let resizeObserver = null;

  // Line the submenu up with its row, but no lower than keeps its bottom
  // level with the root menu's: a tall submenu (Score) hanging below the
  // root made the whole cascade too tall and the menu jumped up over the
  // toolbar to fit.
  function place() {
    const rootHeight = rootRef.value?.offsetHeight ?? 0;
    const subHeight = subRef.value?.offsetHeight ?? 0;
    subTop.value = Math.max(0, Math.min(rowTop, rootHeight - subHeight));
  }

  // A submenu can grow after it opens (the Tags field lists the tag groups
  // once they have been read), so it is placed again whenever its size
  // changes, not only on the tick it opened.
  function watchSize() {
    resizeObserver?.disconnect();
    resizeObserver = null;
    if (typeof ResizeObserver === "undefined" || !subRef.value) return;
    resizeObserver = new ResizeObserver(() => {
      if (sub.value) place();
    });
    resizeObserver.observe(subRef.value);
  }

  function openKind(kind) {
    sub.value = kind;
    const row = rowRefs[kind];
    rowTop = row ? Math.max(0, row.offsetTop - 8) : 0;
    subTop.value = rowTop;
    nextTick(() => {
      place();
      watchSize();
      focusIn(subRef.value);
    });
  }

  function toggle(kind) {
    if (sub.value === kind) sub.value = null;
    else openKind(kind);
  }

  // Back to the open submenu's row, closing it.
  function backToRow() {
    rowRefs[sub.value]?.focus();
    sub.value = null;
  }

  // Left arrow inside a submenu returns to its row, unless it is moving a caret
  // (in a field it goes back only from the start of the text) or a select. Bound
  // in the capture phase so a radio group's own arrow handling never sees it
  // and changes the pick instead.
  function onLeft(event) {
    const target = event.target;
    const field = target?.tagName === "INPUT" ? target : null;
    if (
      !sub.value ||
      target?.tagName === "SELECT" ||
      (field && (field.selectionStart || field.selectionEnd))
    ) {
      return;
    }
    if (!subRef.value?.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    backToRow();
  }

  watch(isOpen, (open) => {
    if (!open) sub.value = null;
  });

  watch(sub, (kind) => {
    if (!kind) {
      resizeObserver?.disconnect();
      resizeObserver = null;
    }
  });

  if (getCurrentInstance()) {
    onBeforeUnmount(() => resizeObserver?.disconnect());
  }

  return {
    sub,
    subTop,
    rootRef,
    subRef,
    rowRefs,
    openKind,
    toggle,
    backToRow,
    onLeft,
  };
}
