<template>
  <!-- The flat bar control: toolbar, selection bar, undo group, overflow
       trigger, close and dismiss. Its raised counterpart is AppButton; the two
       are the whole button system (docs/design/buttons.md). The paint lives in
       App.css under `.bar-btn`, because split pairs and open states style the
       family from outside the component. -->
  <button
    ref="rootEl"
    type="button"
    :class="[
      'bar-btn',
      `bar-btn--${shape}`,
      {
        'bar-btn--icon': iconOnly && !chevron,
        'bar-btn--active': active,
        'bar-btn--open': open,
        'bar-btn--danger': danger,
        'bar-btn--loading': loading,
      },
    ]"
    :disabled="disabled || loading"
    :aria-busy="loading ? 'true' : undefined"
    :aria-label="iconOnly && tooltip ? tooltip : undefined"
  >
    <!-- Same contract as AppButton: an icon-only bar button's tooltip is also
         its name; an explicit `aria-label` still wins. -->
    <Tooltip
      v-if="tooltip"
      :text="tooltip"
      activator="parent"
      :location="tooltipLocation"
      :describe="!iconOnly"
    />
    <span v-if="loading || icon" class="bar-icon-badge-wrap">
      <!-- 24px when the glyph is the whole control, 18px beside a label: MDI
           is drawn on a 24-unit grid, and a labelled glyph tracks its text. -->
      <v-icon
        :size="iconSize || (iconOnly ? 24 : 18)"
        :class="{ 'mdi-spin': loading }"
        >{{ loading ? "mdi-loading" : iconName }}</v-icon
      >
      <span
        v-if="badge != null && badge !== '' && !loading"
        class="bar-filter-badge"
        >{{ badge }}</span
      >
    </span>
    <span v-if="prefix" class="bar-btn-prefix">{{ prefix }}</span>
    <slot />
    <v-icon v-if="chevron" size="16" class="bar-btn-chevron"
      >mdi-menu-down</v-icon
    >
  </button>
</template>

<script setup>
import { computed, nextTick, ref, useSlots, watch } from "vue";
import { VIcon } from "vuetify/components";
import Tooltip from "./Tooltip.vue";

const props = defineProps({
  // An mdi glyph, with or without the `mdi-` prefix.
  icon: { type: String, default: "" },
  // Only for the one stated exception: the dialog close button stays 20px
  // beside an 18px title. Everything else takes the component's size.
  iconSize: { type: [Number, String], default: 0 },
  // boxed (a strip) | round (the selection pill)
  shape: { type: String, default: "boxed" },
  prefix: { type: String, default: "" },
  badge: { type: [Number, String], default: null },
  chevron: { type: Boolean, default: false },
  active: { type: Boolean, default: false },
  open: { type: Boolean, default: false },
  danger: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  // Same pending contract as AppButton: disabled against a second press, the
  // glyph becomes the spinner, and focus comes back when it settles.
  loading: { type: Boolean, default: false },
  // The tip, and an icon-only button's accessible name (buttons.md, "Tooltips").
  tooltip: { type: String, default: "" },
  tooltipLocation: { type: String, default: "bottom" },
});

const slots = useSlots();
const iconOnly = computed(() => !slots.default && !props.prefix);
const iconName = computed(() =>
  props.icon.startsWith("mdi-") ? props.icon : `mdi-${props.icon}`,
);

const rootEl = ref(null);
let refocusWhenDone = false;

watch(
  () => props.loading,
  (isLoading) => {
    if (isLoading) {
      refocusWhenDone = rootEl.value === document.activeElement;
      return;
    }
    if (!refocusWhenDone) return;
    refocusWhenDone = false;
    nextTick(() => rootEl.value?.focus());
  },
);

function focus() {
  rootEl.value?.focus();
}

defineExpose({ focus, el: rootEl });
</script>
