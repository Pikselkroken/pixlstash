<template>
  <!-- A concept's picture: its real screenshot of the Demo Data library
       (frontend/e2e/screenshots/guide.spec.js). New in 2.x is a mosaic of its
       headlines' shots. A concept with no shot yet, Studio while it is
       unstable, draws its icon on a plain tile. -->
  <div class="gil" :class="{ 'gil--grey': grey }">
    <div v-if="mosaic.length" class="gil-mosaic" aria-hidden="true">
      <img v-for="src in mosaic" :key="src" :src="src" alt="" loading="lazy" />
    </div>
    <img
      v-else-if="shot"
      class="gil-shot"
      :src="shot"
      :alt="concept.illustration || ''"
      loading="lazy"
    />
    <div v-else class="gil-tile" aria-hidden="true">
      <v-icon size="40">{{ concept.icon || "mdi-book-open-variant" }}</v-icon>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import { VIcon } from "vuetify/components";
import { HEADLINES } from "../../guide/catalogue";

const props = defineProps({
  concept: { type: Object, required: true },
  /** Greyed: the picture loses its colour, never its frame. */
  grey: { type: Boolean, default: false },
});

const SHOTS = Object.fromEntries(
  Object.entries(
    import.meta.glob("../../assets/guide/*.jpg", {
      eager: true,
      import: "default",
    }),
  ).map(([path, url]) => [path.split("/").pop().replace(/\.jpg$/, ""), url]),
);

const shot = computed(() => SHOTS[props.concept.id] ?? null);

/** New in 2.x: the first four headlines that have a shot, newest first. */
const mosaic = computed(() => {
  if (props.concept.id !== "new") return [];
  const seen = new Set();
  for (const h of HEADLINES) {
    const src = SHOTS[h.concept];
    if (src && !h.unstable) seen.add(src);
    if (seen.size === 4) break;
  }
  return [...seen];
});
</script>

<style scoped>
.gil {
  position: relative;
  aspect-ratio: 16 / 10;
  overflow: hidden;
  background: rgb(var(--v-theme-background));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.gil-shot,
.gil-mosaic img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.gil-mosaic {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-auto-rows: 1fr;
  gap: var(--space-1);
  height: 100%;
}

.gil-tile {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* Greyed is a hint, not a lock (docs/guide/concepts.md): only the picture
   loses its colour; the frame and the click target stay. */
.gil--grey img,
.gil--grey .gil-tile {
  filter: grayscale(1);
  opacity: 0.5;
}
</style>
