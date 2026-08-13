<template>
  <!-- One attachment, as one mark. `title` names it on hover; the
       `visually-hidden` span names it unconditionally, so the cell's accessible
       name lists the entities whether or not anything can be hovered. Both,
       because `title` is invisible to a screen reader on a non-interactive
       element and the hidden span is invisible to a mouse. -->
  <span
    class="emark"
    :class="{ 'emark--more': mark.more }"
    :style="ring"
    :title="mark.label"
  >
    <img
      v-if="thumbnailUrl && !failed"
      class="emark-img"
      :src="thumbnailUrl"
      alt=""
      loading="lazy"
      @error="failed = true"
    />
    <!-- The same "unset is never blank" rule the model's own mark follows: a
         character with no reference face and a set with no pictures both 404
         their thumbnail, and an empty square would read as a broken mark rather
         than as an entity without a picture. -->
    <span
      v-else-if="!mark.more"
      class="emark-fill"
      :style="{ backgroundColor: mark.tile, color: mark.ink }"
      >{{ mark.initials }}</span
    >
    <span v-else class="emark-fill emark-fill--more">+{{ mark.more }}</span>
    <!-- The full stop is load-bearing: the cell's accessible name is the
         concatenation of its marks' text, and without a separator two of them
         run together as "Character: AdaSet: Beach". -->
    <span class="visually-hidden">{{ mark.label }}. </span>
  </span>
</template>

<script setup>
// One mark in the shelf's `Assigned to` column (#892).
//
// The thumbnail is addressed by URL rather than fetched as a blob, so the
// browser caches one response however many rows attach the same character —
// which is the common case, a style adapter being assigned across a cast. Both
// routes are cookie-authenticated GETs, and `appendShareToken` covers the
// share-token session that an `<img>` cannot carry a header for.
//
// Shape carries nothing: character and set take one radius, and the type is in
// the label. Colour carries nothing either — see `assignmentMarks`.

import { computed, ref, watch } from "vue";

import { characterThumbnailUrl } from "../../api/characters";
import { pictureSetThumbnailUrl } from "../../api/pictureSets";

const props = defineProps({
  /** One descriptor from `assignmentMarks`. */
  mark: { type: Object, required: true },
});

/** entity type → the resource module that addresses its thumbnail. */
const THUMBNAIL_URL = {
  character: characterThumbnailUrl,
  set: pictureSetThumbnailUrl,
};

const failed = ref(false);

// A recycled mark (the same DOM node reused for a different row's attachment)
// would otherwise keep the previous entity's failure and never try its own
// thumbnail.
watch(
  () => props.mark?.key,
  () => {
    failed.value = false;
  },
);

const thumbnailUrl = computed(() => {
  const build = THUMBNAIL_URL[props.mark?.type];
  return build && props.mark?.id != null ? build(props.mark.id) : "";
});

// Bound inline because the hue is per-entity data, not a state of this
// component; everything that is a decision rather than a datum is in the
// stylesheet below.
const ring = computed(() =>
  props.mark?.hue ? { borderColor: props.mark.hue } : {},
);
</script>

<style scoped>
/* `--radius-sm` for every mark: §6 reserves the pill for avatar rings, and half
   of these are picture sets rather than faces. The border is the "bordered
   thumbnail" of the design — it is what separates two marks in a fan when the
   pictures behind them are similar, and it is 2px because a hairline is lost
   against a photograph. */
.emark {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* Stated rather than inherited: the fan's overlap is half of
     `--entity-thumb`, and a content-box mark would be 4px wider than the token
     says and would push the last one out of the two-mark track. */
  box-sizing: border-box;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
  flex: 0 0 auto;
  border: 2px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  overflow: hidden;
  /* The fan overlaps, so a mark has to be opaque over its neighbour rather than
     letting the row's wash show through the gap between border and image. The
     row canvas is `background`, not `surface`; the two differ by a hair and it
     is the corner radius that would show it. */
  background: rgb(var(--v-theme-background));
}

.emark-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* Tile and ink arrive as a pair from `markBackground`/`markForeground`, which
   pin the lightness precisely so white initials clear AA on all 48 hues; they
   are bound together inline so one cannot drift from the other. */
.emark-fill {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: var(--text-2xs);
  font-weight: var(--weight-medium);
  letter-spacing: 0.02em;
}

/* The counter is not an entity, so it takes no identity hue — it is chrome, and
   a hue here would imply a fifth character. */
.emark--more {
  border-color: rgb(var(--v-theme-divider));
}

.emark-fill--more {
  background: rgba(var(--v-theme-on-background), 0.08);
  color: rgba(var(--v-theme-on-background), 0.87);
  font-variant-numeric: tabular-nums;
}
</style>
