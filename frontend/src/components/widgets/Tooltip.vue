<template>
  <v-tooltip
    v-if="armed"
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
      <slot
        name="activator"
        v-bind="{ ...slotProps, props: slotActivatorProps(slotProps.props) }"
      />
    </template>
    <slot>{{ text }}</slot>
    <kbd v-if="shortcut" class="app-tooltip__key">{{ shortcut }}</kbd>
  </v-tooltip>
</template>

<script>
// Read from the tokens so each value has one home, and once per page: a
// computed-style read per instance is most of what a tip costs to mount, and
// a grid tile carries several. Unset (tests, a stylesheet that failed to load)
// means no delay, which is Vuetify's own.
let delays;
function tooltipDelays() {
  if (!delays) {
    const style = getComputedStyle(document.documentElement);
    const ms = (name) => parseFloat(style.getPropertyValue(name)) || 0;
    delays = {
      openDelay: ms("--tooltip-delay"),
      // Vuetify's `interactive` does not exist in 3.6: hoverable is the
      // `pointer-events` rule below plus this grace, without which the tip
      // closes on the next tick while the pointer crosses the gap to it.
      closeDelay: ms("--dur-1"),
    };
  }
  return delays;
}
</script>

<script setup>
import {
  computed,
  getCurrentInstance,
  onBeforeUnmount,
  onMounted,
  onUnmounted,
  ref,
  useAttrs,
  useSlots,
  watch,
  watchEffect,
} from "vue";
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
  // Expose the tip to assistive tech as the control's `aria-description`. Off
  // where the tip's text is already the control's name, or it is said twice.
  describe: { type: Boolean, default: true },
});

const attrs = useAttrs();
const slots = useSlots();
const instance = getCurrentInstance();
const open = ref(false);

const { openDelay, closeDelay } = tooltipDelays();

const description = computed(() =>
  props.describe && props.text ? props.text : undefined,
);

// Vuetify points the activator's `aria-describedby` at the tip's id, and
// writes it over whatever the control already had: a blocked button's
// pointer at its visible reason is lost. The tip is not eager, so that id
// names nothing until it opens anyway. The description travels as
// `aria-description` instead, and the control's own `aria-describedby` is
// left as the control set it.
//
// With a slot activator the key is simply dropped from what the call site binds.
function slotActivatorProps(slotProps) {
  const rest = { ...slotProps };
  delete rest["aria-describedby"];
  if (description.value) rest["aria-description"] = description.value;
  return rest;
}

// With `activator="parent"` the Vuetify tooltip is not built until the control
// is first hovered or focused. A `v-tooltip` costs roughly twenty times a
// native `title` to mount, and a grid tile carries several (the rating stars
// alone are five), so building them all up front made the grid pay for tips
// nobody opened. The slot form cannot defer: arming would re-render the
// activator under the tooltip and drop its focus.
//
// Vuetify then binds straight onto the element, so the only way to keep the
// control's own `aria-describedby` is to hand it back. It is read at arming,
// when the parent's attributes are set; Vue still owns later changes, because
// this value never changes again and so is never re-bound.
const deferred = attrs.activator === "parent";
const armed = ref(!deferred);
const parentEl = ref(null);
const ownDescribedby = ref(undefined);
let pendingOpen = 0;

function arm(e) {
  const el = parentEl.value;
  el.removeEventListener("mouseenter", arm);
  el.removeEventListener("focus", arm);
  ownDescribedby.value = el.getAttribute("aria-describedby") ?? undefined;
  armed.value = true;
  // Vuetify binds its own listeners a tick later and never sees the event
  // that armed it, so this one is honoured here.
  if (props.disabled || !(props.text || slots.default)) return;
  if (e.type === "focus") {
    onFocus(e);
    return;
  }
  pendingOpen = setTimeout(() => (open.value = true), openDelay);
  el.addEventListener("mouseleave", () => clearTimeout(pendingOpen), {
    once: true,
  });
}

onMounted(() => {
  if (!deferred) return;
  parentEl.value = instance?.proxy?.$el?.parentElement ?? null;
  parentEl.value?.addEventListener("mouseenter", arm);
  parentEl.value?.addEventListener("focus", arm);
});
// A tip can go while its control stays (`AppButton`'s `v-if="tooltip"`). On
// teardown Vuetify removes every attribute it bound, the control's own
// `aria-describedby` included, so the value the control holds now is put back
// once Vuetify is done; and a description nobody shows must not be announced.
let describedbyAtUnmount = null;
onBeforeUnmount(() => {
  clearTimeout(pendingOpen);
  const el = parentEl.value;
  if (!el) return;
  el.removeEventListener("mouseenter", arm);
  el.removeEventListener("focus", arm);
  el.removeAttribute("aria-description");
  describedbyAtUnmount = el.getAttribute("aria-describedby");
});
onUnmounted(() => {
  if (describedbyAtUnmount != null) {
    parentEl.value?.setAttribute("aria-describedby", describedbyAtUnmount);
  }
  document.removeEventListener("keydown", onDocumentKeydown);
});
watchEffect(() => {
  const el = parentEl.value;
  if (!el) return;
  if (description.value) el.setAttribute("aria-description", description.value);
  else el.removeAttribute("aria-description");
});

// Not eager: a tip is rendered when it opens, not once per button on the page.
// Merged after Vuetify's own activator handlers, which still run their delay;
// opening here first only makes focus immediate.
const activatorProps = computed(() => ({
  "aria-describedby": ownDescribedby.value,
  onFocus,
  // Pressing the control is using it: a tip left open over a menu the press
  // just opened would sit in its first row, hoverable and in the way.
  onMousedown: close,
}));

// Escape dismisses wherever focus is (WCAG 1.4.13). Vuetify's own Escape
// handling never closes a tooltip, which it marks persistent, and a tip
// opened by the pointer may not have focus anywhere near it.
watch(open, (isOpen) => {
  if (isOpen) document.addEventListener("keydown", onDocumentKeydown);
  else document.removeEventListener("keydown", onDocumentKeydown);
});

function onFocus(e) {
  if (!props.disabled && e.target?.matches?.(":focus-visible")) {
    open.value = true;
  }
}

function close() {
  clearTimeout(pendingOpen);
  open.value = false;
}

function onDocumentKeydown(e) {
  if (e.key === "Escape") close();
}
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
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-on-surface)) 6%,
    transparent
  );
}
</style>
