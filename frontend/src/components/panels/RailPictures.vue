<template>
  <!-- Several selected pictures in the grid's rail, tag-first: who they are
       (one row of thumbnails that scrolls sideways, so hundreds cost the
       height of six), how their ratings spread, then the tag field T focuses
       with the tags they carry and the tags rejected across them. -->
  <div class="rail-pictures">
    <div class="rail-pictures__thumbs" aria-label="Selected pictures">
      <img
        v-for="card in cards"
        :key="card.id"
        :src="card.thumbnail || pictureThumbnailUrl(card.id)"
        alt=""
        draggable="false"
        loading="lazy"
      />
      <span v-if="ids.length > cards.length" class="rail-pictures__more"
        >+{{ (ids.length - cards.length).toLocaleString() }}</span
      >
    </div>

    <!-- One column per rating, the count over each, in the score hue. Read
         from the loaded cards: a select-all past what the grid has read
         counts what it has. -->
    <div class="rail-hist" role="img" :aria-label="histLabel">
      <span v-for="col in histogram" :key="col.label" class="rail-hist__col">
        <span class="rail-hist__n">{{ col.count }}</span>
        <i
          :class="{ 'rail-hist__bar--zero': !col.count }"
          class="rail-hist__bar"
          :style="{ height: `${col.height}px` }"
        ></i>
      </span>
    </div>
    <div class="rail-hist__labels" aria-hidden="true">
      <span v-for="col in histogram" :key="col.label">{{ col.label }}</span>
    </div>

    <RailTags
      :ids="ids"
      takes-tag-key
      @tags-applied="emit('tags-applied', $event)"
    />
  </div>
</template>

<script setup>
import { computed } from "vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import RailTags from "./RailTags.vue";

const props = defineProps({
  /** The selected picture ids, in selection order. */
  ids: { type: Array, required: true },
  /** The grid's cards for them, where loaded: thumbnails and ratings. */
  cards: { type: Array, default: () => [] },
});

const emit = defineEmits(["tags-applied"]);

const BAR_MAX = 28; // px, the tallest column; the count sits over it

const histogram = computed(() => {
  const counts = [0, 0, 0, 0, 0, 0];
  for (const card of props.cards) {
    const score = Math.round(Number(card.score) || 0);
    counts[Math.min(5, Math.max(0, score))] += 1;
  }
  const top = Math.max(1, ...counts);
  return counts.map((count, score) => ({
    label: score ? `★${score}` : "unrated",
    count,
    height: Math.round((count / top) * BAR_MAX),
  }));
});

const histLabel = computed(() =>
  histogram.value.map((col) => `${col.label}: ${col.count}`).join(", "),
);
</script>

<style scoped>
.rail-pictures {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  font-size: var(--text-xs);
}

/* One row that scrolls sideways under a 2px scrollbar, which says only how
   much is hidden. */
.rail-pictures__thumbs {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding-bottom: var(--space-2);
  overflow-x: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-surface), 0.28)
    rgba(var(--v-theme-on-surface), 0.06);
}

.rail-pictures__thumbs::-webkit-scrollbar {
  height: 2px;
}

.rail-pictures__thumbs::-webkit-scrollbar-track {
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.rail-pictures__thumbs::-webkit-scrollbar-thumb {
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.28);
}

.rail-pictures__thumbs img {
  flex: none;
  width: 56px;
  height: 56px;
  border-radius: var(--radius-md);
  object-fit: cover;
  object-position: top center;
}

.rail-pictures__more {
  flex: none;
  padding: 0 var(--space-2);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
}

.rail-hist,
.rail-hist__labels {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: var(--space-2);
}

.rail-hist {
  align-items: end;
  height: 44px;
}

.rail-hist__col {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-1);
  height: 100%;
  min-height: 0;
}

.rail-hist__n,
.rail-hist__labels {
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rail-hist__bar {
  display: block;
  flex: 0 1 auto;
  width: 100%;
  min-height: 2px;
  border-radius: 2px 2px 0 0;
  background: rgb(var(--v-theme-tertiary));
}

.rail-hist__bar--zero {
  background: rgba(var(--v-theme-on-surface), 0.12);
}

.rail-hist__labels {
  margin-top: calc(-1 * var(--space-2));
  padding-top: var(--space-2);
  border-top: 1px solid rgb(var(--v-theme-divider));
  text-align: center;
}
</style>
