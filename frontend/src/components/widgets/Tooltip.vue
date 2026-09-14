<template>
  <v-tooltip
    v-model="open"
    :location="location"
    :open-delay="openDelay"
    :close-delay="closeDelay"
    :disabled="disabled || !(text || $slots.default)"
    :activator-props="activatorProps"
    :eager="false"
    content-class="app-tooltip"
  >
    <template v-if="$slots.activator" #activator="slotProps">
      <slot name="activator" v-bind="slotProps" />
    </template>
    <slot>{{ text }}</slot>
    <kbd v-if="shortcut" class="app-tooltip__key">{{ shortcut }}</kbd>
  </v-tooltip>
</template>

<script setup>
import { ref } from "vue";
import { VTooltip } from "vuetify/components";

/**
 * The one tooltip surface (docs/design/buttons.md, "Tooltips").
 *
 * A name for the configuration `HelpTip` worked out: Vuetify's tooltip, which
 * already opens on focus and on touch, made hoverable so a long tip can be
 * read (WCAG 1.4.13; Vuetify's stylesheet sets `pointer-events: none`),
 * painted on the app's own surface, and given an open delay. Vuetify's
 * default delay is none, so a tip strobed open as the pointer crossed a bar.
 *
 * Hover waits `--tooltip-delay`; keyboard focus opens at once, because a
 * keyboard user asked for this control. Escape dismisses.
 *
 * Two ways to attach it: the `activator` slot, as with `v-tooltip`, or
 * `activator="parent"` from inside the control (how `AppButton`'s `tooltip`
 * prop does it). A tip is a second route to information, never its home.
 */
const props = defineProps({
  text: { type: String, default: "" },
  // A keyboard shortcut, worn as the Kbd spec beside the text.
  shortcut: { type: String, default: "" },
  location: { type: String, default: "top" },
  disabled: { type: Boolean, default: false },
  // Point the control's `aria-describedby` at the tip. Off where the tip's
  // text is already the control's name, or a screen reader says it twice.
  describe: { type: Boolean, default: true },
});

const open = ref(false);

// Read from the tokens so each value has one home. Unset (tests, a stylesheet
// that failed to load) means no delay, which is Vuetify's own.
const msToken = (name) =>
  parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue(name),
  ) || 0;
const openDelay = msToken("--tooltip-delay");
// Vuetify's `interactive` does not exist in 3.6: hoverable is the
// `pointer-events` rule below plus this grace, without which the tip closes
// on the next tick while the pointer crosses the gap to the surface.
const closeDelay = msToken("--dur-1");

// Not eager: a tip is rendered when it opens, not once per button on the page.
// Merged after Vuetify's own activator handlers, which still run their delay;
// opening here first only makes focus immediate.
const activatorProps = {
  ...(props.describe ? {} : { "aria-describedby": undefined }),
  onFocus: (e) => {
    if (!props.disabled && e.target?.matches?.(":focus-visible")) {
      open.value = true;
    }
  },
  onKeydown: (e) => {
    if (e.key === "Escape" && open.value) open.value = false;
  },
};
</script>

<style>
/* Global: the content teleports to the overlay container, out of reach of a
   scoped rule. Specificity beats `.v-tooltip > .v-overlay__content`. */
.v-tooltip > .v-overlay__content.app-tooltip {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  max-width: var(--tooltip-max-w);
  padding: var(--space-2) var(--space-4);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-3);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  pointer-events: auto;
}

.app-tooltip__key {
  flex-shrink: 0;
  padding: 0 var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  line-height: 1.5;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, rgb(var(--v-theme-on-surface)) 6%, transparent);
}
</style>
