<template>
  <article
    class="msc fav-host"
    :class="{
      'msc--open': expanded,
      'msc--on': selected,
      'msc--hand': card.handMade,
      'msc--hidden': card.hidden,
      'msc--favouritable': favouritable,
    }"
    role="group"
    :aria-label="accessibleName"
    data-testid="model-set-card"
  >
    <!-- The favourite heart on the cover's top-left corner (#1578). It leads the
         corner, and the badges step in beside it. -->
    <span v-if="favouritable" class="msc__fav">
      <FavouriteHeart
        variant="cover"
        :on="favourite"
        :label="card.name"
        @toggle="emit('favourite')"
      />
    </span>
    <!-- The cover's top-left badges, drawn over whichever cover the card has.
         Yours: the owner changed what the pictures ran with (#1972). Missing a
         file: a checkpoint or member is not on the shelf. Automatic draws
         nothing. The amber dot marks a model the pictures brought in that the
         owner has not seen: it rides the Yours badge, and stands alone on an
         automatic set, which has no badge to ride. -->
    <div v-if="badges" class="msc__badges" aria-hidden="true">
      <span
        v-if="card.fresh && !yours"
        class="msc__new"
        data-testid="set-new-dot"
      ></span>
      <span
        v-if="yours"
        class="msc__badge"
        data-testid="set-badge-yours"
        ><span v-if="card.fresh" class="msc__new"></span>Yours</span
      >
      <span
        v-if="card.missingFile"
        class="msc__badge msc__badge--warn"
        data-testid="set-badge-missing"
        >Missing a file</span
      >
      <span v-if="card.fromComfyUI" class="msc__badge">
        <v-icon size="12">mdi-sitemap-outline</v-icon>{{ runsLabel }}
      </span>
    </div>
    <div
      v-if="card.covers.length"
      class="msc__cover"
      :class="`msc__cover--${card.covers.length}`"
    >
      <span
        v-for="cover in card.covers"
        :key="cover.picture_id"
        class="msc__pic"
      >
        <img :src="coverSrc(cover)" alt="" loading="lazy" />
      </span>
      <span class="msc__badge msc__badge--end" aria-hidden="true">
        <v-icon size="12">mdi-image-multiple</v-icon
        >{{ card.pictures.toLocaleString() }}
      </span>
    </div>
    <!-- A hand-made set with no pictures yet: its checkpoint's own mark, or a
         dashed empty cover while it has none. -->
    <div
      v-else-if="card.handMade"
      class="msc__cover msc__cover--empty msc__cover--hand"
      aria-hidden="true"
    >
      <ModelMark
        v-if="card.markModel"
        class="msc__mark"
        :row="{
          display_name: card.markModel.name,
          filename: card.markModel.filename,
          base_model: card.markModel.base_model,
          icon_sha256: card.markIcon || null,
        }"
      />
      <!-- Only a set with NO checkpoint says so. One whose checkpoint file has
           left the shelf still has it, kept by hash: say that instead. -->
      <span v-else-if="card.incomplete" class="msc__empty-line"
        >No checkpoint yet</span
      >
      <span v-else class="msc__empty-line">Checkpoint not on shelf</span>
    </div>
    <div v-else class="msc__cover msc__cover--empty" aria-hidden="true">
      <!-- On the grid only because ComfyUI ran it (#1565): the run count sits in
           the badges above, and the cover says where the evidence came from. -->
      <span v-if="card.fromComfyUI" class="msc__empty-line">Ran in ComfyUI</span>
      <span v-else class="msc__empty-line">No picture to show</span>
    </div>
    <!-- The template offer (#1999), asked as a question on the cover's foot
         until the owner answers it. One model is answered here: Add to set is
         the ordinary members add, with its receipt and Undo. Several open the
         tray to review, since three files added blind is a bigger claim.
         Mouse-only: the grid owns Tab, and the keyboard answers through the
         tray and the set's menu, so the card's name carries the question.
         Its clicks stop here: reading the question must not select the set. -->
    <div
      v-if="card.offer"
      class="msc__ask"
      aria-hidden="true"
      data-testid="set-offer"
      @click.stop
      @dblclick.stop
    >
      <p class="msc__ask-q">
        <strong>{{ card.offer.who }}</strong>{{ card.offer.rest
        }}<template v-if="card.offer.model"> Add it?</template>
      </p>
      <span
        v-if="card.offer.model"
        class="msc__ask-model"
        :title="offerChip"
        ><v-icon size="12">mdi-cube-outline</v-icon
        ><span class="msc__ask-name">{{ offerChip }}</span></span
      >
      <div class="msc__ask-acts">
        <AppButton
          v-if="card.offer.model"
          variant="primary"
          size="sm"
          icon-left="plus"
          on-dark
          tabindex="-1"
          data-testid="set-offer-add"
          @click="emit('add')"
          >Add to set</AppButton
        >
        <AppButton
          v-else
          variant="primary"
          size="sm"
          icon-left="plus"
          on-dark
          tabindex="-1"
          data-testid="set-offer-review"
          @click="emit('offer')"
          >Review {{ card.offer.adds }}…</AppButton
        >
        <AppButton
          variant="outline"
          size="sm"
          on-dark
          tabindex="-1"
          data-testid="set-offer-no"
          :tooltip="
            card.offer.model
              ? 'Keeps this file out of the set. Reset to automatic suggests it again.'
              : 'Keeps these files out of the set. Reset to automatic suggests them again.'
          "
          @click="emit('keep-separate')"
        >
          Stop asking
        </AppButton>
      </div>
    </div>

    <!-- The visible rows are hidden from assistive tech: the card's own label
         already reads all of them, including the marks "+N" clipped. -->
    <div class="msc__meta">
      <div class="msc__row">
        <span class="msc__name" aria-hidden="true">{{ card.name }}</span>
        <span v-if="card.hidden" class="msc__tag" aria-hidden="true">Hidden</span>
        <!-- The owner's word on the set, never PixlStash's check. -->
        <VerdictIcon
          v-if="card.verdict"
          :verdict="card.verdict"
          :label="`Your verdict: ${setVerdictText(card.verdict)}`"
        />
        <!-- Edit is the double-click on a button: it opens (or closes) this
             set's tray and a shut Models rail. Mouse-only, like Hide: Enter
             on the card does the same from the keyboard. A second press of a
             double click is dropped: the first already toggled the tray. When
             that press moved a tray open on another card, the rows shift and
             the second press lands elsewhere; the grid's `onCardEdit` arms its
             reflow guard for that. -->
        <AppButton
          class="msc__edit"
          variant="ghost"
          size="sm"
          icon-left="pencil-outline"
          icon-only
          tabindex="-1"
          :tooltip="expanded ? 'Close this set' : 'Open this set to edit it'"
          data-testid="set-edit"
          @click.stop="$event.detail > 1 || emit('edit')"
          @dblclick.stop
        />
        <!-- A set from pictures is read off the library, so it is hidden rather
             than deleted (the next read would only draw it again). A hand-made
             set is the owner's own and can be deleted, and hiding it is the
             same view choice on top: out of the way in THIS library only.
             Mouse-only: the grid owns Tab, and Delete on the card deletes a
             hand-made set, hides a derived one; the selection pill's Hide set
             does the same from the keyboard. -->
        <AppButton
          class="msc__hide"
          variant="ghost"
          size="sm"
          :icon-left="card.hidden ? 'eye-outline' : 'eye-off-outline'"
          icon-only
          tabindex="-1"
          :tooltip="card.hidden ? 'Show this set again' : 'Hide this set'"
          data-testid="set-hide"
          @click.stop="emit(card.hidden ? 'unhide' : 'hide')"
          @dblclick.stop
        />
      </div>
      <!-- Each member as its Type letter and its name, three at most; the rest
           are "+N". A file the shelf lacks is named in warning ink. -->
      <div class="msc__row msc__marks" aria-hidden="true">
        <span
          v-for="(mark, index) in shownMarks"
          :key="index"
          class="msc__mark-chip"
          :class="{ 'msc__mark-chip--missing': mark.missing }"
        >
          <Tooltip :text="mark.title" activator="parent" />
          <TypeMark class="msc__k" :mark="mark.mark" :tint="mark.tint" />
          <span class="msc__mark-name">{{ mark.name }}</span>
        </span>
        <span v-if="moreMarks" class="msc__more">+{{ moreMarks }}</span>
        <span v-if="!shownMarks.length" class="msc__none">Nothing in it yet</span>
      </div>
      <div class="msc__row msc__row--facts">
        <!-- The one thing an incomplete set must say, in place of its facts:
             what is missing, so the owner knows what to add. -->
        <span
          v-if="card.handMade && card.incomplete"
          class="msc__warn"
          aria-hidden="true"
          ><v-icon size="14">mdi-alert-outline</v-icon>Incomplete: no
          checkpoint</span
        >
        <template v-else>
          <span v-if="card.baseModel" class="msc__fact">{{
            card.baseModel
          }}</span>
          <span v-if="sizeLabel" class="msc__fact">{{ sizeLabel }}</span>
          <span v-if="card.lastUsed" class="msc__fact">{{
            card.lastUsed
          }}</span>
        </template>
      </div>
    </div>
  </article>
</template>

<script setup>
// One workflow set on the Models workspace's set grid (#1438, #2168).
//
// **A card is one base model** - a checkpoint, or the diffusion file a Flux or
// Wan graph loads instead - and opening its tray shows the models that have run
// with it. The tray is opened by the grid's own gestures (Enter, double-click),
// by the card's Edit button (the double-click's twin), and by the rail's Edit set.
//
// **Deliberately not `WorkflowCard`.** The shape is the same on purpose - the
// cover mosaic, the scrim badges, the layered count, the rhythm of its meta rows
// - because a reader should not have to learn a second card. What is NOT shared
// is the vocabulary: that card's ⓘ panel says "Stack of 3 workflows", and a set
// is neither. The tokens, the badge treatment and the row rhythm are copied;
// nothing that says "workflow" is.
//
// The meta block is three rows as the design board draws it: the name, the
// member marks, and the facts (base model, size on disk, last used).
//
// **On an evidence card, selected means its BASE MODEL is selected**, never the
// set: the shelf's verbs write one file each and two of them destroy bytes, so a
// card standing for its whole tray would put a shared VAE behind a Delete aimed
// at a checkpoint. The other members are selected in the tray, one row each. One
// exception to the drawing: while the open tray shows the base model picked from
// its own row, the grid leaves the card unlit, so a tray pick does not read as
// the whole set picked (`trayPicked` in `ModelSetGrid.vue`).
//
// A HAND-MADE card (`card.handMade`, #1520) is the owner's set rather than the
// evidence for one: dashed, and selected means the SET is selected - its verbs
// touch no file. Its badge says Yours once the owner has changed what its
// pictures ran with (#1972); an automatic one draws no badge.

import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { pictureThumbnailUrl } from "../../api/pictures";
import { formatModelSize } from "../../utils/modelShelf";
import { setVerdictText } from "../../utils/setVerdicts";
import AppButton from "./AppButton.vue";
import FavouriteHeart from "./FavouriteHeart.vue";
import ModelMark from "./ModelMark.vue";
import VerdictIcon from "./VerdictIcon.vue";
import Tooltip from "./Tooltip.vue";
import TypeMark from "./TypeMark.vue";

/** The member marks a card draws before "+N" takes the rest. */
const MARKS_SHOWN = 3;

const props = defineProps({
  /** One card from `setCard` / `handMadeCard`, with the grid's facts added. */
  card: { type: Object, required: true },
  /** This card's tray is open. */
  expanded: { type: Boolean, default: false },
  /** This card's base model is in the shelf's selection. */
  selected: { type: Boolean, default: false },
  /** Draw the favourite heart: the grid's cards carry one, other lists do not. */
  favouritable: { type: Boolean, default: false },
  /** Is this set a favourite now? */
  favourite: { type: Boolean, default: false },
});

const emit = defineEmits([
  "favourite",
  "offer",
  "add",
  "keep-separate",
  "hide",
  "unhide",
  "edit",
]);

/** An evidence card is automatic by definition; a hand-made one says. */
const automatic = computed(
  () => !props.card.handMade || Boolean(props.card.automatic),
);

/** The owner's own set, changed from what its pictures ran with. */
const yours = computed(() => Boolean(props.card.handMade) && !automatic.value);

const badges = computed(
  () =>
    yours.value ||
    Boolean(props.card.fresh) ||
    props.card.missingFile ||
    props.card.fromComfyUI,
);

/**
 * The URL a browser loads one cover from.
 *
 * **An `<img src>` never reaches the Axios interceptor**, so nothing prepends
 * the API base and nothing appends the share token: the payload's
 * `{picture_id, version}` has to go through the api layer's own builder, or the
 * browser asks the PAGE origin for a path no route serves and every cover on
 * this grid breaks. `pictureThumbnailUrl` is where that path is spelled - the
 * same one the picture grid's tiles use, so a thumbnail the browser already
 * holds is not fetched twice.
 */
function coverSrc(cover) {
  return pictureThumbnailUrl(cover.picture_id, { version: cover.version });
}

/** The first marks drawn, each with the words its title and label give it. */
const shownMarks = computed(() =>
  (props.card.marks ?? []).slice(0, MARKS_SHOWN).map((mark) => ({
    ...mark,
    title: mark.missing
      ? `${mark.name} is not on the shelf`
      : [mark.type, mark.name].filter(Boolean).join(" "),
  })),
);

/** How many marks "+N" stands for. */
const moreMarks = computed(() =>
  Math.max(0, (props.card.marks ?? []).length - MARKS_SHOWN),
);

const sizeLabel = computed(() =>
  props.card.sizeBytes ? formatModelSize(props.card.sizeBytes) : "",
);

const runsLabel = computed(() =>
  props.card.historyRuns === 1
    ? "1 run"
    : `${props.card.historyRuns} runs`,
);

/**
 * The facts a reader sees, in words: the base model, the size and the last use,
 * as the chips draw them. None when the set is incomplete, which draws its
 * warning instead.
 */
const drawnFacts = computed(() => {
  if (props.card.handMade && props.card.incomplete) return [];
  return [props.card.baseModel, sizeLabel.value, props.card.lastUsed]
    .filter(Boolean);
});

// The cover's question, read in full: its buttons are off the tab order, so
// the name says where the keyboard answers it.
/** The one offered model as the chip prints it; its title reveals it clipped. */
const offerChip = computed(() => {
  const model = props.card.offer?.model;
  return model
    ? [model.kind, model.name, model.quant].filter(Boolean).join(" · ")
    : "";
});

const offerName = computed(() => {
  const offer = props.card.offer;
  if (!offer) return null;
  const statement = `${offer.who}${offer.rest}`;
  if (!offer.model) return `${statement} Answer in the set's tray or menu`;
  const model = [offer.model.kind, offer.model.name, offer.model.quant]
    .filter(Boolean)
    .join(" ");
  return `${statement} ${model}. Add it? Answer in the set's tray or menu`;
});

// "+N" is not a control, so this is the only place a screen reader hears the
// marks a narrow card clipped - and the only place it hears the whole set, with
// the count line (`facts`) that the card no longer draws.
const accessibleName = computed(() => {
  const card = props.card;
  const marks = (card.marks ?? []).map((mark) =>
    mark.missing ? `${mark.name} not on the shelf` : mark.name,
  );
  // One mark is the set's own file and nothing else: say so, as the drawn
  // "Nothing else" line used to.
  const withMarks = marks.length
    ? [
        `with ${marks.join(", ")}`,
        marks.length === 1
          ? card.handMade
            ? "nothing else in it yet"
            : "nothing else has run with it"
          : null,
      ]
        .filter(Boolean)
        .join(", ")
    : card.handMade
      ? "nothing in it yet"
      : "nothing else has run with it";
  if (card.handMade) {
    return [
      card.name,
      automatic.value ? "automatic" : "custom",
      card.fresh ? `${card.fresh} new from pictures` : null,
      card.hidden ? "hidden" : null,
      card.missingFile ? "a file missing from the shelf" : null,
      card.incomplete
        ? "incomplete: no checkpoint"
        : card.checkpointOffShelf
          ? "checkpoint not on shelf"
          : null,
      withMarks,
      ...drawnFacts.value,
      card.incomplete ? null : card.facts.join(", "),
      offerName.value,
    ]
      .filter(Boolean)
      .join(", ");
  }
  return [
    card.name,
    "automatic",
    card.missing ? "base model not on shelf" : null,
    card.missingFile ? "a file missing from the shelf" : null,
    card.fromComfyUI ? "ran in ComfyUI" : null,
    card.hidden ? "hidden" : null,
    withMarks,
    ...drawnFacts.value,
    card.facts.join(", "),
  ]
    .filter(Boolean)
    .join(", ");
});
</script>

<style scoped src="./cardCover.css"></style>
<style scoped>
/* The meta block is 8 + 3 rows + 2 gaps + 8, fixed whatever the card holds
   and `flex: none` so a change to that sum shows as a wrong height rather than
   being absorbed. Three rows of the small control height with the space-2 gap
   between them. Local on purpose, as `WorkflowCard`'s is; the `6 / 5` cover the
   two cards share is in `cardCover.css`. */
.msc {
  --msc-meta-h: calc(var(--space-3) * 2 + var(--control-h-sm) * 3 + var(--space-2) * 2);

  position: relative;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}

/* Open and selected are two states and they read differently on purpose: open is
   a border in the active ink, selected is the shell's own wash and ring. A card can be both, and then it wears both. */
.msc--open {
  border-color: var(--active-bar);
}

/* The selection mark. The wash sits over the card as an OVERLAY, not as the
   card's own background: an inset box-shadow paints under the children, and the
   top of this card is the cover's opaque `<img>`s, so the mark would stop dead
   at the pictures. `pointer-events: none` so the card under it still takes its
   click. The ring is an outline two space-1 wide and off the card by the same,
   in the active ink: the design board's `outline: 2px solid` at `outline-offset:
   2px`, on the tokens. */
.msc--on {
  outline: var(--space-1) solid var(--active-bar);
  outline-offset: var(--space-1);
}

.msc--on::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: var(--z-raised);
  border-radius: inherit;
  background: var(--active-wash);
  pointer-events: none;
}

.msc__pic {
  overflow: hidden;
  background: rgb(var(--v-theme-input-background));
}

.msc__pic img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.msc__cover--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4) var(--space-5);
  background: rgb(var(--v-theme-input-background));
}

/* The owner's own set: a dashed edge, the design's "hand-made" mark, so it is
   told from an evidence card before a word is read. Dashed rather than a
   colour, because the difference is provenance, not state. */
.msc--hand {
  border-style: dashed;
}

/* Hidden, drawn only while the grid shows hidden sets: the cover fades, whether
   it holds pictures or is the empty stand-in, and the card's own text does not,
   so the card stays readable and the "Hidden" tag says why. */
.msc--hidden .msc__pic,
.msc--hidden .msc__cover--empty {
  opacity: var(--opacity-disabled);
}

.msc__edit,
.msc__hide {
  flex-shrink: 0;
  color: rgb(var(--v-theme-on-surface));
}

/* Edit leads the pair to the row's end; Hide sits right after it. */
.msc__edit {
  margin-left: auto;
}

.msc__cover--hand {
  position: relative;
  border-bottom: 1px dashed rgb(var(--v-theme-border));
}

/* The checkpoint's mark, drawn large as the cover. `ModelMark` sizes itself
   from `--entity-thumb`, which the sidebar already overrides the same way; the
   size is three of the largest spacing step rather than a new number. */
.msc__mark {
  --entity-thumb: calc(var(--space-6) * 3);
}

/* The top-left badge group, over the cover whichever kind of cover it is. The
   heart leads the corner; the group steps in past it. */
.msc__badges {
  position: absolute;
  top: var(--space-3);
  left: var(--space-3);
  z-index: var(--z-raised);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  pointer-events: none;
}

.msc__badges .msc__badge {
  position: static;
}

.msc__badge--warn {
  color: rgb(var(--v-theme-surface-warning));
}

.msc__badge--end {
  right: var(--space-3);
}

.msc__warn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  overflow: hidden;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-surface-warning));
  text-overflow: ellipsis;
}

/* The offer's question panel: a dark band across the cover's foot, dark in
   both themes like the scrim badges above it. Placed on the card rather than
   inside the cover so both hand-made covers (pictures or the checkpoint's mark)
   carry it from one element. */
.msc__ask {
  position: absolute;
  left: 0;
  right: 0;
  bottom: var(--msc-meta-h);
  z-index: var(--z-raised);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
  background: rgba(var(--v-theme-dark-surface), 0.95);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-xs);
}

.msc__ask-q {
  margin: 0;
  line-height: var(--leading-snug);
}

.msc__ask-q strong {
  font-weight: var(--weight-bold);
}

/* The model itself, dashed like its ghost tile in the tray. */
.msc__ask-model {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  gap: var(--space-2);
  max-width: 100%;
  box-sizing: border-box;
  height: var(--tag-h-xs);
  padding: 0 var(--space-3);
  border: 1px dashed rgb(var(--v-theme-dark-surface-accent));
  border-radius: var(--radius-sm);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}

.msc__ask-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.msc__ask-acts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

/* A model the pictures brought in that the owner has not seen yet (#1972): the
   attention dot, in the amber kept for the dot alone. It rides the Yours badge. */
.msc__new {
  flex: none;
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
}

/* The heart leads the cover's top-left corner; the badges step in past it. */
.msc__fav {
  position: absolute;
  top: var(--space-3);
  left: var(--space-3);
  z-index: var(--z-raised);
}

.msc--favouritable .msc__badges {
  left: calc(var(--space-3) + var(--control-h) + var(--space-2));
}

.msc__meta {
  flex: none;
  box-sizing: border-box;
  height: var(--msc-meta-h);
  display: grid;
  grid-template-rows: repeat(3, var(--control-h-sm));
  row-gap: var(--space-2);
  padding: var(--space-3);
}

.msc__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

/* The "Hidden" tag, the one tag the name row keeps. */
.msc__tag {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  flex-shrink: 0;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
  white-space: nowrap;
}

/* The member marks: each a `TypeMark` letter and a name, at chip size. A row
   of them clips at its edge, never wraps. */
.msc__marks {
  min-width: 0;
  overflow: hidden;
}

.msc__mark-chip {
  display: inline-flex;
  align-items: center;
  /* Chips give way, their names ellipsing, so the +N after them is never the
     thing the row's clip cuts off. */
  flex-shrink: 1;
  min-width: 0;
  gap: var(--space-2);
  max-width: calc(var(--space-6) * 5);
  height: var(--tag-h-xs);
  padding-right: var(--space-2);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  font-size: var(--text-2xs);
}

.msc__mark-chip--missing .msc__mark-name {
  color: rgb(var(--v-theme-surface-warning));
}

/* The Type letter at chip height. */
.msc__k {
  width: var(--tag-h-xs);
  height: var(--tag-h-xs);
  font-size: var(--text-2xs);
}

.msc__mark-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msc__more {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A fact is a small bordered chip, as the design draws it: base model, size,
   last used. Tabular figures keep a size column from jittering. */
.msc__fact {
  /* Block, not flex, so a base model too long for a narrow card ellipses
     rather than pushing the size and last-used chips out of the clip. */
  display: inline-block;
  flex-shrink: 0;
  box-sizing: border-box;
  height: var(--tag-h-xs);
  line-height: calc(var(--tag-h-xs) - 2px);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.msc__fact:first-child {
  flex-shrink: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.msc__row--facts {
  overflow: hidden;
  padding-right: var(--space-3);
}
</style>
