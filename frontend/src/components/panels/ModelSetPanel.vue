<template>
  <!-- `rowgroup`, not a bare div: the panel is a child of the grid's `treegrid`,
       where only rows and rowgroups are allowed, and the header below is a row
       of its own. -->
  <div
    :id="panelId"
    class="msp"
    role="rowgroup"
    :style="notchStyle"
    data-testid="model-set-panel"
  >
    <!-- One column has no centre worth pointing at, so the caret takes the
         shipped `--start` inset rather than a hand-copy of its 22px. -->
    <span
      class="tbm-caret"
      :class="columns > 1 ? 'msp__notch' : 'tbm-caret--start'"
      aria-hidden="true"
    ></span>

    <div class="msp__header" role="row" aria-level="2">
      <div role="gridcell">
        <div class="msp__bar" role="toolbar" :aria-label="`${name} stack`">
          <span class="msp__name">{{ name }}</span>
          <span class="msp__count num">{{ countLabel }}</span>
          <!-- What a card IS, said once where the reader has just opened one:
               nothing else on the grid can carry the sentence, and without it
               "4 combinations" is a number with no noun behind it. -->
          <span class="msp__note">{{ note }}</span>
          <span class="msp__spacer"></span>
          <!-- The one gesture that belongs to the FOLD rather than to a card, so
               a reader who distrusts the folding can see every set without
               hunting for the toolbar control. -->
          <AppButton
            v-if="foldable"
            variant="outline"
            size="sm"
            icon-left="call-split"
            @click="emit('unfold')"
            >Don’t fold</AppButton
          >
          <AppButton
            variant="ghost"
            size="sm"
            icon-left="close"
            icon-only
            tooltip="Close this stack"
            @click="emit('close')"
          />
        </div>
      </div>
    </div>

    <!-- `presentation`, so the member rows below are exposed to the rowgroup
         above rather than to an unroled div, which would break the chain a
         treegrid needs: rowgroup owns rows, rows own gridcells. -->
    <div class="msp__grid" role="presentation">
      <div
        v-for="(combo, index) in members"
        :id="`${panelId}-row-${combo.key}`"
        :key="combo.key"
        class="msp__member"
        role="row"
        aria-level="2"
        :aria-posinset="index + 1"
        :aria-setsize="members.length"
        :tabindex="cursorKey === combo.key ? 0 : -1"
        :data-key="combo.key"
      >
        <div class="msp__cell" role="gridcell">
          <ModelComboCard :card="combo" @pick="(file) => emit('pick', file)" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// One open workflow set on the model shelf's set grid (#1438).
//
// The panel is ONE element in the card grid at `grid-column: 1 / -1`, placed
// after the last card of the open card's row, so opening it pushes the later
// rows down rather than floating over them. The caret points back at the card
// that opened it: `--notch` is derived from that card's COLUMN INDEX every time
// the container resizes, never stored as a pixel offset, which is what would
// leave it pointing at the wrong card after a resize.
//
// **Deliberately not `StackPanel`.** That component is the workflows grid's, and
// it has grown into it: a Grid|List switch backed by a remembered preference, a
// per-member `⋯` menu of workflow verbs, and a selection contract. Threading a
// slot and a noun through all of that to draw a set would bend one component's
// contract around two screens; the notch arithmetic and the treegrid roles are
// ~40 lines and are copied instead. Neutral ground, the same as that panel's:
// `--panel`, a hairline border, `--radius-lg`.

import { computed } from "vue";

import AppButton from "../widgets/AppButton.vue";
import ModelComboCard from "../widgets/ModelComboCard.vue";

const props = defineProps({
  /** The panel's DOM id - the open card's `aria-controls`. */
  panelId: { type: String, required: true },
  /** The stack's name, as its card carries it. */
  name: { type: String, default: "" },
  /** The stack's combinations, as `comboCard` shapes them; the seed first. */
  members: { type: Array, default: () => [] },
  /** The grid's current column count, from its ResizeObserver. */
  columns: { type: Number, default: 1 },
  /** The open card's 0-based column in that grid. */
  columnIndex: { type: Number, default: 0 },
  /** The roving cursor's key, when it is inside this panel. */
  cursorKey: { type: String, default: "" },
  /** Some folding is in force, so *Don't fold* has something to undo. */
  foldable: { type: Boolean, default: false },
  /** The grid's column gutter in px, so the members line up with the cards. */
  gap: { type: Number, default: 12 },
});

const emit = defineEmits(["close", "unfold", "pick"]);

const countLabel = computed(() =>
  props.members.length === 1
    ? "1 combination"
    : `${props.members.length} combinations folded here`,
);

const note = computed(() =>
  props.members.length === 1
    ? "the files a picture proves ran together"
    : "each one is a set of files a picture proves ran together",
);

// The centre of the column the open card sits in, as a share of the panel's own
// width - the panel spans every column, so the two grids share an origin. At one
// column the caret falls back to the shipped `--start` class instead, so nothing
// here hand-copies that rule's inset.
const notchStyle = computed(() => {
  const style = { "--wf-columns": props.columns, "--wf-gap": `${props.gap}px` };
  if (props.columns > 1) {
    style["--notch"] = `${((props.columnIndex + 0.5) / props.columns) * 100}%`;
  }
  return style;
});
</script>

<style scoped>
.msp {
  position: relative;
  grid-column: 1 / -1;
  margin-bottom: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
}

/* The shipped `.tbm-caret`, aimed by `--notch` rather than by a modifier: the
   column it points at is a number this component computes, not one of the four
   fixed insets the toolbar menus use. `translateX(-50%)` re-centres it on that
   point; the rotation is the shipped one and has to be restated because the two
   live in one `transform`. */
.msp__notch {
  left: var(--notch);
  transform: translateX(-50%) rotate(45deg);
}

.msp__bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--bar-height);
  padding: 0 var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.msp__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.msp__count {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.msp__note {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.msp__spacer {
  flex: 1;
}

/* The SAME column count as the grid above, taken from `--wf-columns` rather than
   recomputed with `auto-fill`: the panel's padding narrows the track, and an
   auto-fill here would quietly drop to one column fewer - at which point a
   member's index stops naming the column it is drawn in and Down lands
   somewhere else. `minmax(0, 1fr)` so a wide card cannot push the track out. */
.msp__grid {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  /* The GRID's own gutter, inherited through `--wf-gap`, not `--space-4`: the
     panel shares the grid's column count so that a member's index names the
     column it is drawn in, and a different gutter breaks the alignment that
     makes true. Falls back to `--space-4` for a host that sets neither. */
  gap: var(--wf-gap, var(--space-4));
  padding: var(--space-4);
  align-items: start;
}

/* No focus rule: `style.css` paints the one ring, and a row that stays silent
   gets it. */
.msp__cell {
  position: relative;
  border-radius: var(--radius-md);
}
</style>
