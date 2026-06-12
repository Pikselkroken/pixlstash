<script setup>
// "PixlStash" brand wordmark in the Tiny5 pixel font.
//
// TRIAL: font-based rendering (was a vector-outline SVG). A font is trivial to use
// everywhere — logo, login, startup pages, even inline in prose — whereas the SVG
// has to be hand-placed. The tradeoff: live pixel text can soften at fractional OS
// scaling (125 % / 150 %), which CSS can't fix on Linux/Windows. If that bites, the
// SVG-outline version is the fallback (regenerate from Tiny5 with fonttools).
//
// Colour: "Pixl" uses currentColor; "Stash" uses var(--wordmark-accent), which falls
// back to currentColor — so a caller that sets only `color` gets a single-tone
// wordmark, while one that also sets --wordmark-accent gets the two-tone split.
// Size with `font-size` on the host element.
</script>

<template>
  <span class="wordmark-logo">Pixl<span class="wm-stash">Stash</span></span>
</template>

<style scoped>
.wordmark-logo {
  font-family: "Tiny5", monospace;
  line-height: 1;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.wm-stash {
  color: var(--wordmark-accent, currentColor);
}
</style>
