/**
 * Bind a Vuetify activator's slot props AND keep a template ref of your own.
 *
 * `v-bind="props"` plus `ref="x"` on one element does not merge: Vue's
 * `mergeProps` has no special case for `ref`, so whichever comes last wins.
 * Lose Vuetify's and the tooltip or menu opens unanchored; lose yours and the
 * ref is null for ever. Neither is an error. Bind `withRef(props, set)` instead
 * and both hear about the element:
 *
 *   <AppInput v-bind="withRef(tipProps, (el) => (nameInputRef = el))" />
 *
 * @param {object} slotProps The activator slot's `props`, which carry `ref`.
 * @param {(el: any) => void} set Receives the element or component instance.
 */
export function withRef(slotProps, set) {
  return {
    ...slotProps,
    ref: (el) => {
      const theirs = slotProps?.ref;
      if (typeof theirs === "function") theirs(el);
      else if (theirs) theirs.value = el;
      set(el);
    },
  };
}
