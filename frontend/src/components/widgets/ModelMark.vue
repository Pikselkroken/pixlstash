<template>
  <!-- Two boxes, not one. The RING is a pseudo-element on the outer box and the
       picture is clipped by the inner one: a single element cannot both hide an
       image's corners (`overflow: hidden`) and draw something outside its own
       edge, which is what a detached ring is.

       `aria-hidden` on the picture - the row's own name already says which
       model this is - but NOT on the ring's label, which is the only thing on
       the row that says what the model is assigned to now that the column is
       gone (#904). -->
  <span class="mmark" :class="ringClass">
    <Tooltip :text="ring?.label || ''" activator="parent" :describe="false" />
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
      <!-- The named tile (#1951): a family icon fills it and carries the
           name across its top; otherwise the name sits on the maker's
           colour. -->
      <span
        v-else-if="tile"
        class="mmark-named"
        :style="{
          background: tile.background,
          color: mark.ink,
          '--mmark-name': tile.scale,
        }"
      >
        <img v-if="tile.icon" class="mmark-img" :src="tile.icon.url" alt="" />
        <span
          class="mmark-name"
          :class="{
            'mmark-name--over': tile.icon,
            'mmark-name--band': tile.icon?.band,
          }"
        >
          <span v-for="(line, i) in tile.lines" :key="i">{{ line }}</span>
        </span>
      </span>
      <span
        v-else
        class="mmark-initials"
        :class="{ 'mmark-initials--two': detail }"
        :style="{ backgroundColor: mark.color, color: mark.ink }"
      >
        <span>{{ mark.initials }}</span>
        <span v-if="detail">{{ detail }}</span>
      </span>
    </span>
    <!-- Outside the face, because the face clips: the tab sits over the
         corner rather than inside it. -->
    <!-- The named tile's tab: a role, or a bolt for a few-step model with a
         speed LoRA's step count. Drawn over a family icon too. -->
    <span
      v-if="showTab"
      class="mmark-version mmark-tab"
      aria-hidden="true"
      :style="{
        backgroundColor: mark.ink,
        color: tile.edge,
        borderColor: tile.edge,
      }"
      ><v-icon v-if="tile.tab.bolt" class="mmark-bolt">mdi-lightning-bolt</v-icon
      >{{ tile.tab.text }}</span
    >
    <span
      v-else-if="showVersion"
      class="mmark-version"
      aria-hidden="true"
      :style="{
        backgroundColor: mark.ink,
        color: mark.color,
        borderColor: mark.color,
      }"
      >{{ mark.version }}</span
    >
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
import { namedTile } from "../../utils/modelTile";
import Tooltip from "./Tooltip.vue";

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
  /**
   * A second line of letters under the initials, for a host that draws
   * several marks of one family side by side with no names beside them (the
   * workflow card, `markDetails`).
   */
  detail: { type: String, default: null },
  /**
   * Draw the server's named tile (`mark`, `mark_tab`, `mark_colors`,
   * `mark_icon`) where the row has one, for a host that prints no name beside
   * the mark (the workflow card). The shelf leaves it off: its rows say the
   * name already.
   */
  named: { type: Boolean, default: false },
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

const tile = computed(() =>
  props.named ? namedTile(props.row, mark.value.color) : null,
);

// Like the version, the tab belongs to the tile and not to a picture of the
// model's own or a set's icon.
const showTab = computed(
  () => Boolean(tile.value?.tab) && !faceUrl.value && !props.ring?.icon,
);

// The version belongs to the generated mark: a picture or a set's icon in the
// square says nothing about which version the file is. A named tile carries
// its version in its name.
const showVersion = computed(
  () =>
    Boolean(mark.value.version) &&
    !tile.value &&
    !faceUrl.value &&
    !props.ring?.icon,
);

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
   let one of them change without the other.

   The letters are a share of the tile rather than a step of the type ramp, so
   they fill it at every size the mark is drawn at: 55% is the most two wide
   letters (WW, MW) take without touching the edges. The ComfyUI pack's
   `paintMark` uses the same share. */
.mmark-initials {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: calc(var(--entity-thumb) * 0.55);
  font-weight: var(--weight-medium);
  letter-spacing: -0.02em;
  line-height: 1;
}

/* Two lines of two, packed into the top-start corner so the version tab over
   the bottom-end one covers no letter: 0.36 of the card's 30px mark is 11px,
   and two lines of it at 0.9 end 2px above the tab. Semibold, because 10px
   at the single line's medium weight thins out on the tile. Measured in a
   browser against the widest pair (WM beside a two-digit tab). */
.mmark-initials--two {
  align-items: flex-start;
  justify-content: flex-start;
  box-sizing: border-box;
  padding: var(--space-1) 0 0 var(--space-1);
  font-size: calc(var(--entity-thumb) * 0.36);
  font-weight: var(--weight-semibold);
  line-height: 0.9;
}

/* The named tile (#1951). The name is packed into the top-start corner like
   the two-line initials, so the tab over the bottom-end one covers none of
   it, and sized by `namedTile` as a share of the tile's line (the tile less
   its padding), so it fills the line at whatever size the mark is drawn.
   Never below 8.25px, a FIXED floor and the design's own value rather than a
   step of the type ramp: the smallest a name stays legible on the tile.
   `model-tile-names.spec.js` holds every known name to it. */
.mmark-named {
  position: relative;
  display: flex;
  width: 100%;
  height: 100%;
}

.mmark-name {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  box-sizing: border-box;
  padding: var(--space-1) 0 0 var(--space-1);
  font-size: max(
    8.25px,
    calc((var(--entity-thumb) - 2 * var(--space-1)) * var(--mmark-name))
  );
  /* The face `namedTile` measures, named rather than inherited: the card sits
     inside Vuetify's application font, which is not the one measured. */
  font-family: var(--font-ui);
  font-weight: var(--weight-semibold);
  letter-spacing: -0.02em;
  line-height: 0.9;
  white-space: nowrap;
}

/* Across the top of a family icon, whose motif sits in the lower two-thirds
   so the name never covers it. */
.mmark-name--over {
  position: absolute;
  inset: 0 0 auto 0;
  padding-bottom: var(--space-1);
  line-height: 0.95;
}

/* Over a busy picture, a solid band: a gradient left the name illegible over
   the eye's stripes on the card. */
.mmark-name--band {
  background: var(--scrim-photo-strong);
}

.mmark-bolt {
  font-size: var(--text-2xs);
}

/* The version tab (the mark design round's D2): the tile's own pair inverted,
   so it needs no colour of its own and clears the same contrast the initials
   do. A fixed size, like the initials, rather than a share of the tile: at
   24px a proportional tab was 11px high with 8px digits, too small to read.
   It hangs 6px past the corner, over the ring where there is one. */
.mmark-version {
  position: absolute;
  right: -6px;
  bottom: -6px;
  z-index: var(--z-raised);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-width: 15px;
  height: 15px;
  padding: 0 var(--space-1);
  border: 1px solid;
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-bold);
  line-height: 1;
}

/* A named tile's tab, a step down: its widest word, BASE, is 29.4px at 10px
   in DejaVu Sans (32.3px at 11px), inside the card's 30px tile. */
.mmark-tab {
  font-size: var(--text-3xs);
}

/* ── The assignment ring ───────────────────────────────────────────────────
   A pseudo-element, deliberately not a border (which would push the picture in
   and make an assigned mark a different size from an unassigned one) and not an
   outline (which would fight the focus outline on the row).

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
