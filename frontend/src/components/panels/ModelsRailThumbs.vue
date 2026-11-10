<template>
  <div class="mrt" role="list">
    <router-link
      v-for="picture in pictures"
      :key="picture.picture_id"
      class="mrt-tile"
      role="listitem"
      :aria-label="`Open picture ${picture.picture_id}`"
      :to="{
        name: 'all-pictures',
        query: { overlay: String(picture.picture_id), from: route.path },
      }"
    >
      <img
        class="mrt-img"
        :src="pictureThumbnailUrl(picture.picture_id)"
        alt=""
        loading="lazy"
      />
    </router-link>
  </div>
</template>

<script setup>
/**
 * The newest pictures of a set or a model, as thumbnails that open each one
 * in the lightbox (`all-pictures`, with `overlay` set), the way the workflows
 * screen does.
 */
import { useRoute } from "vue-router";

import { pictureThumbnailUrl } from "../../api/pictures";

defineProps({
  /** `[{picture_id, at}]`, newest first. */
  pictures: { type: Array, required: true },
});

const route = useRoute();
</script>

<style scoped>
.mrt {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--space-2);
}

.mrt-tile {
  display: block;
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.mrt-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>
