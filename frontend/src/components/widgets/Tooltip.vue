<template>
  <v-tooltip
    v-if="armed"
    ref="tipRef"
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

// Tips that are open now, as { host: () => Element, close }. A native `title`
// shows only the innermost element's; two tips open at once on a row and the
// button inside it is noise, so only the innermost open tip survives.
const openTips = new Set();

// The descriptions live here: one hidden node per describing tip, pointed at
// by `aria-describedby`. `aria-description` would be simpler and is ARIA 1.3,
// which only Chromium exposes, and some tips (a read-only row's reason) are
// the only place their words are.
let descriptionHost = null;
let descriptionSeq = 0;
function newDescriptionNode() {
  if (!descriptionHost) {
    descriptionHost = document.createElement("div");
    descriptionHost.hidden = true;
    descriptionHost.className = "app-tooltip-descriptions";
    document.body.appendChild(descriptionHost);
  }
  const node = document.createElement("span");
  node.id = `app-tooltip-desc-${++descriptionSeq}`;
  descriptionHost.appendChild(node);
  return node;
}

const tokens = (value) => (value || "").split(/\s+/).filter(Boolean);
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
 * keyboard user asked for this control. Escape dismisses, and so does a
 * press. Of nested tips only the innermost stays open.
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
  // Describe the control with the tip's text (`aria-describedby`). Off where
  // the text is already the control's name, or it is said twice.
  describe: { type: Boolean, default: true },
});

const attrs = useAttrs();
const slots = useSlots();
const instance = getCurrentInstance();
const open = ref(false);
const tipRef = ref(null);

const { openDelay, closeDelay } = tooltipDelays();

const description = computed(() =>
  props.describe && props.text ? props.text : "",
);

// Vuetify points the activator's `aria-describedby` at the tip's id and writes
// it over whatever the control already had: a blocked button's pointer at its
// visible reason is lost, and the tip is not eager, so that id names nothing
// until it opens. With a slot activator the key is dropped from what the call
// site binds; the description is added to the element below.
function slotActivatorProps(slotProps) {
  const rest = { ...slotProps };
  delete rest["aria-describedby"];
  return rest;
}

// With `activator="parent"` the Vuetify tooltip is not built until the control
// is first hovered or focused. A `v-tooltip` costs roughly twenty times a
// native `title` to mount, and a grid tile carries several, so building them
// all up front made the grid pay for tips nobody opened. The slot form cannot
// defer: arming would re-render the activator under the tooltip and drop its
// focus, which is why a repeated control should take the parent form.
//
// Vuetify then binds straight onto the element, so the only way to keep the
// control's own `aria-describedby` is to hand it back. It is read at arming,
// when the parent's attributes are set; the description's id is re-added by
// the observer below whenever anything rewrites the attribute.
const deferred = attrs.activator === "parent";
const armed = ref(!deferred);
const parentEl = ref(null);
const ownDescribedby = ref(undefined);
let pendingOpen = 0;

/** The element the tip describes and anchors to, in either form. */
const host = () => parentEl.value ?? tipRef.value?.activatorEl ?? null;

function arm(e) {
  const el = parentEl.value;
  el.removeEventListener("mouseenter", arm);
  const own = tokens(el.getAttribute("aria-describedby")).filter(
    (id) => id !== descriptionNode?.id,
  );
  ownDescribedby.value = own.length ? own.join(" ") : undefined;
  armed.value = true;
  // Vuetify binds its own listeners a tick later and never sees the event
  // that armed it, so this one is honoured here.
  if (e.type !== "mouseenter" || props.disabled) return;
  if (!(props.text || slots.default)) return;
  pendingOpen = setTimeout(() => (open.value = true), openDelay);
  el.addEventListener("mouseleave", () => clearTimeout(pendingOpen), {
    once: true,
  });
}

// `focusin` and `focusout`, not `focus`: the parent is often a wrapper (a row,
// a label) around the control that takes focus, and `focus` does not bubble.
function onFocusIn(e) {
  if (!armed.value) arm(e);
  onFocus(e);
}

function onFocusOut(e) {
  if (!parentEl.value?.contains(e.relatedTarget)) close();
}

onMounted(() => {
  if (!deferred) return;
  // Vuetify's own `parent` walk skips `data-no-activator` wrappers (a v-btn's
  // content span); matched, so both anchor to the same element.
  let el = instance?.proxy?.$el?.parentElement ?? null;
  while (el?.hasAttribute("data-no-activator")) el = el.parentElement;
  parentEl.value = el;
  el?.addEventListener("mouseenter", arm);
  el?.addEventListener("focusin", onFocusIn);
  el?.addEventListener("focusout", onFocusOut);
  el?.addEventListener("mousedown", onPress);
  el?.addEventListener("mouseleave", onRelease);
});

// ── The description ───────────────────────────────────────────────────────
let descriptionNode = null;
let observer = null;
let observedEl = null;

function describeInto(el) {
  const current = tokens(el.getAttribute("aria-describedby"));
  const id = descriptionNode?.id;
  const wanted = description.value && id;
  const next = current.filter((t) => t !== id);
  if (wanted) next.push(id);
  if (next.join(" ") === current.join(" ")) return;
  if (next.length) el.setAttribute("aria-describedby", next.join(" "));
  else el.removeAttribute("aria-describedby");
}

watchEffect(() => {
  const el = host();
  if (description.value && !descriptionNode) {
    descriptionNode = newDescriptionNode();
  }
  if (descriptionNode) descriptionNode.textContent = description.value;
  if (!el || !descriptionNode) return;
  if (observedEl !== el) {
    observer?.disconnect();
    // Vue re-renders the control's own value, and Vuetify binds and unbinds
    // it; either drops the id, so it goes back whenever the attribute moves.
    observer = new MutationObserver(() => describeInto(el));
    observer.observe(el, { attributeFilter: ["aria-describedby"] });
    observedEl = el;
  }
  describeInto(el);
});

// A tip can go while its control stays (`AppButton`'s `v-if="tooltip"`). On
// teardown Vuetify removes every attribute it bound, the control's own
// `aria-describedby` included, so the value the control holds now, less this
// tip's description, is put back once Vuetify is done.
let describedbyAtUnmount = null;
let unmountHost = null;
onBeforeUnmount(() => {
  clearTimeout(pendingOpen);
  openTips.delete(registration);
  observer?.disconnect();
  unmountHost = host();
  const el = parentEl.value;
  if (el) {
    el.removeEventListener("mouseenter", arm);
    el.removeEventListener("focusin", onFocusIn);
    el.removeEventListener("focusout", onFocusOut);
    el.removeEventListener("mousedown", onPress);
    el.removeEventListener("mouseleave", onRelease);
  }
  if (unmountHost) {
    const own = tokens(unmountHost.getAttribute("aria-describedby")).filter(
      (id) => id !== descriptionNode?.id,
    );
    describedbyAtUnmount = own.join(" ");
  }
  descriptionNode?.remove();
});
onUnmounted(() => {
  if (unmountHost) {
    if (describedbyAtUnmount) {
      unmountHost.setAttribute("aria-describedby", describedbyAtUnmount);
    } else {
      unmountHost.removeAttribute("aria-describedby");
    }
  }
  document.removeEventListener("keydown", onDocumentKeydown);
});

// Not eager: a tip is rendered when it opens, not once per button on the page.
// Merged after Vuetify's own activator handlers, which still run their delay;
// opening here first only makes focus immediate.
const activatorProps = computed(() => ({
  "aria-describedby": ownDescribedby.value,
  onFocus,
  onMousedown: onPress,
  onMouseleave: onRelease,
}));

const registration = { host, close };

watch(open, (isOpen) => {
  if (isOpen && pressed) {
    open.value = false;
    return;
  }
  if (!isOpen) {
    openTips.delete(registration);
    document.removeEventListener("keydown", onDocumentKeydown);
    return;
  }
  const mine = host();
  for (const other of openTips) {
    const theirs = other.host();
    if (!mine || !theirs || theirs === mine) continue;
    // An open tip inside this one's control wins; this one stays shut.
    if (mine.contains(theirs)) {
      open.value = false;
      return;
    }
    // This one is inside an open tip's control: that one gives way.
    if (theirs.contains(mine)) other.close();
  }
  openTips.add(registration);
  // Escape dismisses wherever focus is (WCAG 1.4.13). Vuetify's own Escape
  // handling never closes a tooltip, which it marks persistent, and a tip
  // opened by the pointer may not have focus anywhere near it.
  document.addEventListener("keydown", onDocumentKeydown);
});

function onFocus(e) {
  if (props.disabled || !(props.text || slots.default)) return;
  if (e.target?.matches?.(":focus-visible")) open.value = true;
}

// Pressing the control is using it, and the tip stays shut until the pointer
// leaves, as a native `title` does. Closing on the press alone is not enough:
// the pointer is still over the control, so the hover delay that was already
// running reopens the tip a moment later, over the menu the press just opened
// (a right-click on a sidebar row), where its hoverable surface takes the
// click meant for a menu row.
let pressed = false;

function onPress() {
  pressed = true;
  close();
}

function onRelease() {
  pressed = false;
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
