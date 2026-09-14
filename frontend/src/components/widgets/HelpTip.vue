<template>
  <!-- Always rendered, never `v-if`. `AppButton`'s icon-only box already
       carries its own width, so hiding it with `visibility` keeps the slot and
       a row whose actions are all available has the same geometry as one whose
       actions are all blocked. `visibility: hidden` also takes it out of the
       tab order, hit testing and the accessibility tree, so it needs no
       `tabindex`, no `aria-hidden` and no `pointer-events` rule
       (visual-language.md §5.1). -->
  <Tooltip :text="reason" :describe="false">
    <template #activator="{ props: tipProps }">
      <AppButton
        v-bind="reason ? tipProps : {}"
        class="helptip"
        :class="{ 'helptip--empty': !reason }"
        icon-only
        variant="ghost"
        icon-left="help-circle-outline"
        :aria-label="reason ? `${label}: ${reason}` : undefined"
      />
    </template>
  </Tooltip>
</template>

<script setup>
import AppButton from "./AppButton.vue";
import Tooltip from "./Tooltip.vue";

/**
 * The "why is this unavailable" mark that trails a group of blocked controls.
 *
 * A separate focusable `<button>` rather than a tooltip hung on the blocked
 * control: a natively `disabled` control fires no pointer events and holds no
 * focus, so a tooltip on it is unreachable by both routes. It is a preset of
 * the one `Tooltip` surface, which opens on hover and on focus and can itself
 * be hovered (WCAG 1.4.13).
 *
 * The reason is ALSO rendered as visible text on the surface and pointed at by
 * the blocked control's `aria-describedby`. This mark is the pointer-and-focus
 * route to it, never its only home.
 */
defineProps({
  /** The sentence to show. Empty renders the reserved box and nothing in it. */
  reason: { type: String, default: "" },
  /** What it explains. Must not be a bare "Help": every row would repeat it. */
  label: { type: String, required: true },
});
</script>

<style scoped>
/* 8px of separation on top of the group's own 4px gap, so the mark reads as an
   annotation trailing the actions rather than as a fourth action. On-grid, and
   no new token: the -outline glyph and the gap do the work. */
.helptip {
  margin-inline-start: var(--space-2);
}

.helptip--empty {
  visibility: hidden;
}
</style>
