<template>
  <!-- Two boxes, not one. The RING is a pseudo-element on the outer box and the
       picture is clipped by the inner one: a single element cannot both hide an
       image's corners (`overflow: hidden`) and draw something outside its own
       edge, which is what a detached ring is.

       `aria-hidden` on the picture - the row's own name already says which
       model this is - but NOT on the ring's label, which is the only thing on
       the row that says what the model is assigned to now that the column is
       gone (#904). -->
  <span class="mmark" :class="ringClass" :title="ring?.label || undefined">
    <span class="mmark-face" aria-hidden="true">
      <!-- KEYED on the URL, so each candidate in the fallback chain gets its
           own element. Reusing one element across two srcs is what makes an
           `error` impossible to attribute: the handler cannot tell which load
           failed, and blacklisting the wrong one drops a face that was never
           even requested. Keyed, the element that failed keeps its own `src`
           for as long as it exists, so a late error from it re-reports the
           failure already recorded and `dropFace` swallows it as a duplicate.
           (The listener does NOT die with the replaced node - Vue leaves it
           attached - which is exactly why the frozen `src` is what matters.) -->
      <img
        v-if="faceUrl"
        :key="faceUrl"
        class="mmark-img"
        :src="faceUrl"
        alt=""
        loading="lazy"
        @error="dropFace"
      />
      <v-icon
        v-else-if="ring?.icon"
        class="mmark-icon"
        :color="ring.iconHue || undefined"
        >{{ ring.icon }}</v-icon
      >
      <span
        v-else
        class="mmark-initials"
        :style="{ backgroundColor: mark.color, color: mark.ink }"
      >
        {{ mark.initials }}
      </span>
    </span>
    <!-- The full stop is load-bearing: this text is concatenated into the
         cell's accessible name, and without a separator it runs into whatever
         the next cell says. -->
    <span v-if="ring" class="visually-hidden">{{ ring.label }}. </span>
  </span>
</template>

<script setup>
// The shelf's identity slot (shelf plan, the sixth verb), wearing the
// assignment ring the resolved design puts on it (#904).
//
// **Unset is never blank.** PixlStash generates no sample for a checkpoint - it
// registers one in place, possibly at 24 GB - and 37% of real adapters carry no
// title, base model or trigger either, so an empty slot is the common case
// rather than the edge one. A row with no icon draws a generated mark computed
// at render from the row itself.
//
// The colour is keyed on a HASH of the folded base model, deliberately not on
// the first-unused rule `character_color` uses: models are unbounded and have no
// moment of assignment, and a mark that shifted when a neighbour was deleted
// would be worse than no mark. See `generatedMark`.
//
// The ring is a SECOND axis on the same 24px square: its hue is the entity's
// own and its style is hashed off the entity's id, so the pair survives
// greyscale where either alone would not. See `assignmentRing`.

import { computed, ref, watch } from "vue";

import { characterThumbnailUrl } from "../../api/characters";
import { modelIconUrl } from "../../api/modelIcons";
import { pictureSetThumbnailUrl } from "../../api/pictureSets";
import { generatedMark } from "../../utils/modelShelf";

/** entity type → the resource module that addresses its thumbnail. */
const ENTITY_THUMBNAIL = {
  character: characterThumbnailUrl,
  set: pictureSetThumbnailUrl,
};

const props = defineProps({
  /**
   * A shelf row. Only `icon_sha256`, the base-model fields, `display_name` and
   * `filename` are read.
   */
  row: { type: Object, required: true },
  /**
   * One descriptor from `assignmentRing`, or null for a mark that carries no
   * assignment at all (the folders dialog, a picker). Null draws NO ring, which
   * is a different thing from the `none` style: that one is the dashed grey
   * "assigned to nothing", and it belongs on a shelf row.
   */
  ring: { type: Object, default: null },
});

const iconUrl = computed(() =>
  props.row?.icon_sha256 ? modelIconUrl(props.row.icon_sha256) : "",
);

// The candidates that 404ed, not a single "it failed" flag: the chain below
// has two steps, and a flag set by the first one would take the second down
// with it - a model whose icon file is missing would draw nothing rather than
// falling through to the face of whoever it is assigned to.
const failed = ref([]);

// Read off the element that failed, never off `faceUrl`: that computed updates
// the moment `failed` is written, while the DOM catches up a tick later, so two
// errors arriving in one task would blacklist the SECOND candidate on the
// strength of the first one's failure. The `:key` above is what makes
// `event.target` unambiguous.
function dropFace(event) {
  const src = event.target.getAttribute("src");
  if (src && !failed.value.includes(src)) {
    failed.value = [...failed.value, src];
  }
}

// A recycled mark (the same DOM node reused for a different row) would
// otherwise keep the previous row's failures and never try its own pictures.
//
// The source is a STRING, deliberately. `ringFor(row)` is called inline in the
// shelf's `v-for` (ModelShelf.vue), so every parent render hands this component
// a new-but-identical ring object; a getter returning an array or the object
// itself compares unequal each time, and the reset would fire on every keystroke
// in the filter box - putting the mark back on the URL that just 404ed.
watch(
  () =>
    `${iconUrl.value}|${props.ring?.type}|${props.ring?.id}|${props.ring?.icon}`,
  () => {
    failed.value = [];
  },
);

/**
 * The face inside the ring, in priority order.
 *
 * 1. The model's OWN icon, always: somebody chose that picture for this file.
 * 2. The icon of whoever it is assigned to, if it carries one - a picture set's
 *    `set_icon` is what its thumbnail was replaced BY, and the sidebar already
 *    draws the set that way, in the same colour or the same theme ink.
 *    Deliberately not fallible: an icon is a name in a font rather than a
 *    fetch, so there is no `error` to catch and the step below is unreachable
 *    once a set carries one.
 * 3. The face of whoever it is assigned to. A LoRA of Sarah with no icon of
 *    its own is far better identified by Sarah's reference face than by the
 *    letters `SA` - and the ring around it is already that person's colour, so
 *    the two halves say one thing.
 * 4. The generated mark. A character with no reference face and a set with no
 *    pictures both 404 their thumbnail, and an empty square would read as a
 *    broken mark rather than as an entity without a picture.
 *
 * Each step that IS a URL is skipped once that URL has failed, so the chain runs all the
 * way down: a model pointing at an icon whose file is gone falls to the
 * assigned face, and then to the mark, rather than sitting on a broken image.
 *
 * Addressed by URL rather than fetched as a blob, so the browser caches one
 * response however many rows borrow the same face - which is the common case,
 * a cast of characters across 1,800 adapters.
 */
const faceUrl = computed(() => {
  const build = ENTITY_THUMBNAIL[props.ring?.type];
  // An entity that has chosen an icon has no thumbnail to lend: choosing one is
  // how the owner turns the picture off, so borrowing it here would put back the
  // face the sidebar no longer shows. `ring.icon` takes step 2 instead.
  const entityUrl =
    build && props.ring?.id != null && !props.ring.icon
      ? build(props.ring.id)
      : "";
  return (
    [iconUrl.value, entityUrl].find(
      (url) => url && !failed.value.includes(url),
    ) || ""
  );
});

const mark = computed(() => generatedMark(props.row));

const ringClass = computed(() =>
  props.ring ? `mmark--ring mmark--${props.ring.style}` : "",
);
</script>

<style scoped>
.mmark {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
  flex: 0 0 auto;
}

.mmark-face {
  display: flex;
  width: 100%;
  height: 100%;
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.mmark-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* The entity's chosen icon, at the slot's own size so it fills the square the
   thumbnail would have filled. `margin: auto` because the face is a flex box
   sized by its child here rather than by an image stretched to 100%. */
.mmark-icon {
  margin: auto;
  font-size: var(--entity-thumb);
}

/* The initials carry their own colour derived from the frozen 48, so the
   contrast pair is fixed rather than themed: a mark that inverted with the
   theme would be a different mark for the same model. Both halves are bound
   inline because `generatedMark` hands them out together - the tile is
   renormalised to a pinned lightness precisely so the ink can stay constant,
   and splitting the pair across inline style and a stylesheet is what would
   let one of them change without the other. */
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

/* ── The assignment ring ───────────────────────────────────────────────────
   A pseudo-element, deliberately not a border (which would push the picture in
   and make an assigned mark a different size from an unassigned one) and not an
   outline (which would fight `--focus-ring` on the row).

   The 2px GAP is what makes it legible. A ring drawn directly against an
   arbitrary thumbnail is not one contrast problem but one per image; detached,
   its inner edge sits on the row background - a known colour in both themes -
   so it becomes the same solved problem as an opaque chip while the picture
   stays. The hue is bound inline because it is per-entity data; everything that
   is a decision rather than a datum is here. */
.mmark--ring::before {
  content: "";
  position: absolute;
  inset: -4px;
  border-radius: calc(var(--radius-sm) + 3px);
  border: 2px solid var(--mmark-ring, transparent);
}

.mmark--dashed::before {
  border-style: dashed;
}

.mmark--thick::before {
  inset: -6px;
  border-width: 4px;
}

.mmark--double::before {
  inset: -6px;
  border-width: 4px;
  border-style: double;
}

/* Assigned to nothing. A dashed ring in the divider ink rather than no ring:
   under a design where every other mark wears one, a bare square reads as a
   mark that failed to render rather than as a state. It is also the one ring
   that carries no identity, so it takes no hue. */
.mmark--none::before {
  border-style: dashed;
  border-color: rgb(var(--v-theme-divider));
}
</style>
