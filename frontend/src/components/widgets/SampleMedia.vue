<template>
  <video
    v-if="isVideo"
    :src="playing ? src : `${src}#t=0.1`"
    :aria-label="alt || undefined"
    muted
    loop
    playsinline
    :autoplay="playing"
    :controls="playing"
    :preload="playing ? 'auto' : 'metadata'"
  />
  <img v-else :src="src" :alt="alt" />
</template>

<script setup>
/**
 * One training sample: an image, or a video model's MP4 clip. In a grid a
 * clip shows its first frame; `playing` (the full-size view) plays it, looping,
 * with controls. Attributes (class, role, tabindex, handlers) fall through to
 * whichever is drawn, so the template keeps a single root and no comment.
 */
import { computed } from "vue";

const props = defineProps({
  src: { type: String, required: true },
  alt: { type: String, default: "" },
  /** Play the clip, with controls, rather than show its first frame. */
  playing: { type: Boolean, default: false },
});

const isVideo = computed(() => /\.mp4(?:$|[?#])/i.test(props.src));
</script>
