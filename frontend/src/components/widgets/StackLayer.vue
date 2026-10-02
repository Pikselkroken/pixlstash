<template>
  <!-- A layer on Vuetify's overlay stack, for something positioned by hand
       (a completion list under a field, a full-window preview) that has to
       clear whatever dialog it was opened from. The stack orders it above the
       topmost open overlay, so it needs no z-index of its own: a hand-written
       number above `--z-drawer` bets against a value that moves
       (visual-language.md §14, "The ladder stops at --z-drawer").

       Inert as an overlay: no scrim, no transition, no focus handling, and
       `persistent` so neither Escape nor an outside click closes it. Its owner
       still decides when it shows and handles its own keys; the overlay only
       supplies the stacking. -->
  <v-overlay
    :model-value="open"
    :scrim="false"
    persistent
    no-click-animation
    :close-on-back="false"
    :transition="false"
    location-strategy="static"
    scroll-strategy="none"
    content-class="stack-layer"
  >
    <slot />
  </v-overlay>
</template>

<script setup>
defineProps({
  /** Whether the layer is on the stack. The slot may stay mounted while false. */
  open: { type: Boolean, default: false },
});
</script>

<style>
/* Vuetify's content box is `contain: layout`, which makes it the containing
   block of a `position: fixed` child. The box is 0×0 at the viewport's origin,
   so a fixed child's `top`/`left` would still land, but its `inset` and
   percentages would resolve against nothing. Without containment the child is
   fixed against the viewport, as it was when it was teleported to <body>. */
.v-overlay__content.stack-layer {
  contain: none;
}
</style>
