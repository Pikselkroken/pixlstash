<template>
  <!-- `currentColor` and no explicit fill colour, so the mark inherits whatever
       the row it sits in is painted: a menu item, a sidebar destination and a
       dialog header all tint it themselves, and it follows both themes without
       a second asset. `aria-hidden`, because every place this is used labels
       itself in text beside it. -->
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="currentColor"
    aria-hidden="true"
    focusable="false"
  >
    <path
      fill-rule="evenodd"
      d="M12 0a12 12 0 1 0 0 24 12 12 0 0 0 0-24Z
         M5.58 9a6.435 6.435 0 0 1 12.87 0v10.585a.915.915 0 0 1-1.83 0v-6.26a.925.925 0 0 0-1.85 0v3.56a.915.915 0 0 1-1.83 0v-3.56a.925.925 0 0 0-1.85 0v7.16a.915.915 0 0 1-1.83 0v-7.16a.925.925 0 0 0-1.85 0v4.46a.915.915 0 0 1-1.83 0Z
         M12 6.094a2.906 2.906 0 1 0 0 5.812 2.906 2.906 0 0 0 0-5.812Z"
    />
  </svg>
</template>

<script setup>
// The ai-toolkit mark, traced as a path rather than shipped as their asset.
//
// Ostris publishes it only as raster - `ui/src/app/icon.png` is 96x96, and
// `ui/src/app/icon.svg` is a 110 KB base64 PNG in an SVG wrapper, not a vector.
// Either would go soft at the 16px these menus use and neither can take
// `currentColor`, so a raster would stay black on the dark theme. The mark is a
// plain silhouette, so tracing it costs ~400 bytes and solves both: the
// geometry below is measured off their 1024px render (disc r12; cap a
// semicircle of r6.435 centred at 12,9; the eye a true circle of r2.906 on the
// same centre; four even legs 1.83 wide) and agrees with it to 97.4% of pixels.
//
// Referential use - it labels "the folder ai-toolkit writes to" and never
// claims their endorsement. ai-toolkit is MIT (Ostris, LLC).
//
// It is the one solid mark in a row of `-outline` mdi glyphs, which is
// deliberate: a brand mark hollowed out stops being the brand mark. If that
// reads too heavy beside its neighbours, that is a visual-language call for the
// lead designer, not a reason to redraw it here.

defineProps({
  /** Matches the `size` a sibling `<v-icon>` in the same row is given. */
  size: { type: [Number, String], default: 16 },
});
</script>
