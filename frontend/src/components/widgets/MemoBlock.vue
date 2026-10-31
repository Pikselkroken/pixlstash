<script>
// `v-memo` with a cache of its own. The directive keeps one cache slot per
// `v-for` in the template, so a `v-for` nested in another (the model shelf's
// rows inside its groups) compares each group's rows with the previous
// group's and never hits. One instance per item gives each its own slot.
import { defineComponent, renderSlot, withMemo } from "vue";

export default defineComponent({
  name: "MemoBlock",
  props: {
    /** Redraws the slot only when one of these changes, as `v-memo` does. */
    deps: { type: Array, required: true },
  },
  setup(props, { slots }) {
    const cache = [];
    return () =>
      withMemo(props.deps, () => renderSlot(slots, "default"), cache, 0);
  },
});
</script>
