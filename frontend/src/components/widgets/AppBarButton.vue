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
        'bar-btn--on-dark': onDark,
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
        :class="{
          'mdi-spin': loading,
          'bar-btn-glyph--cornered': createMark === true && !loading,
        }"
        >{{ loading ? "mdi-loading" : iconName }}</v-icon
      >
      <!-- The amber "+" that says this makes a new one (the footer band's
           create verbs). Inside the glyph at [x, y] on its 24-unit grid when
           the shape has room, else top-right with the glyph pulled aside. -->
      <span
        v-if="createMark && !loading"
        class="bar-create-mark"
        :class="{ 'bar-create-mark--corner': createMark === true }"
        :style="createMarkStyle"
        aria-hidden="true"
      ></span>
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
import { computed, ref, useSlots } from "vue";
import { VIcon } from "vuetify/components";
import { useRefocusAfterLoading } from "../../utils/dom";
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
  // On a ground that stays dark in both themes (buttons.md, "The on-dark
  // context"). The paint is in App.css with the rest of `.bar-btn`.
  onDark: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  // Same pending contract as AppButton: disabled against a second press, the
  // glyph becomes the spinner, and focus comes back when it settles.
  loading: { type: Boolean, default: false },
  // The tip, and an icon-only button's accessible name (buttons.md, "Tooltips").
  tooltip: { type: String, default: "" },
  tooltipLocation: { type: String, default: "bottom" },
  // The amber "+" of a create verb: true for the top-right corner, or [x, y]
  // on the glyph's 24-unit grid to put it inside a shape with room for it.
  createMark: { type: [Boolean, Array], default: false },
});

const slots = useSlots();
const iconOnly = computed(() => !slots.default && !props.prefix);
const iconName = computed(() =>
  props.icon.startsWith("mdi-") ? props.icon : `mdi-${props.icon}`,
);

const createMarkStyle = computed(() =>
  Array.isArray(props.createMark)
    ? {
        left: `${(props.createMark[0] / 24) * 100}%`,
        top: `${(props.createMark[1] / 24) * 100}%`,
      }
    : undefined,
);

const rootEl = ref(null);
useRefocusAfterLoading(() => props.loading, rootEl);

function focus() {
  rootEl.value?.focus();
}

defineExpose({ focus, el: rootEl });
</script>
