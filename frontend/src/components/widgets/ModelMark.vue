<template>
  <!-- One slot, two possible fills, and never a blank. `aria-hidden` because
       the row's own accessible name already says which model this is: a mark
       that also announced "FL" would be the same fact twice, less usefully. -->
  <span
    class="mmark"
    :class="{ 'mmark--generated': !iconUrl }"
    aria-hidden="true"
  >
    <img
      v-if="iconUrl"
      class="mmark-img"
      :src="iconUrl"
      alt=""
      loading="lazy"
    />
    <span
      v-else
      class="mmark-initials"
      :style="{ backgroundColor: mark.color, color: mark.ink }"
    >
      {{ mark.initials }}
    </span>
  </span>
</template>

<script setup>
// The shelf's identity slot (shelf plan, the sixth verb).
//
// **Unset is never blank.** PixlStash generates no sample for a checkpoint — it
// registers one in place, possibly at 24 GB — and 37% of real adapters carry no
// title, base model or trigger either, so an empty slot is the common case
// rather than the edge one. A row with no icon draws a generated mark computed
// at render from the row itself.
//
// The colour is keyed on a HASH of the folded base model, deliberately not on
// the first-unused rule `character_color` uses: models are unbounded and have no
// moment of assignment, and a mark that shifted when a neighbour was deleted
// would be worse than no mark. See `generatedMark`.

import { computed } from "vue";

import { modelIconUrl } from "../../api/modelIcons";
import { generatedMark } from "../../utils/modelShelf";

const props = defineProps({
  /**
   * A shelf row. Only `icon_sha256`, the base-model fields, `display_name` and
   * `filename` are read.
   */
  row: { type: Object, required: true },
});

const iconUrl = computed(() =>
  props.row?.icon_sha256 ? modelIconUrl(props.row.icon_sha256) : "",
);

const mark = computed(() => generatedMark(props.row));
</script>

<style scoped>
.mmark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
  border-radius: var(--radius-sm);
  overflow: hidden;
  flex: 0 0 auto;
}

.mmark-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* The initials carry their own colour from the frozen 48, so the contrast pair
   is fixed rather than themed: a mark that inverted with the theme would be a
   different mark for the same model. The INK is bound inline, because it is
   chosen per background — several of the 48 are light enough that white
   initials are unreadable on them. */
.mmark-initials {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  letter-spacing: 0.02em;
}
</style>
