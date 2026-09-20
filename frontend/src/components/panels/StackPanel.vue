<template>
  <!-- `rowgroup`, not a bare div: the panel is a child of the grid's
       `treegrid`, where only rows and rowgroups are allowed, and the header
       below is a row of its own. -->
  <div
    :id="panelId"
    class="stack-panel"
    :class="{ 'stack-panel--selected': selected }"
    role="rowgroup"
    :style="notchStyle"
    data-testid="stack-panel"
  >
    <!-- One column has no centre worth pointing at, so the caret takes the
         shipped `--start` inset rather than a hand-copy of its 22px. -->
    <span
      class="tbm-caret"
      :class="columns > 1 ? 'stack-panel__notch' : 'tbm-caret--start'"
      aria-hidden="true"
    ></span>

    <div class="stack-panel__header" role="row" aria-level="2">
      <div role="gridcell">
        <div class="stack-panel__bar" role="toolbar" :aria-label="toolbarName">
          <span class="stack-panel__name">{{ name }}</span>
          <span class="stack-panel__count num">{{ countLabel }}</span>
          <span v-if="pending" class="stack-panel__pending">{{ pending }}</span>
          <span class="stack-panel__spacer"></span>
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

    <!-- `presentation`, so the member rows below are exposed to the
         rowgroup above rather than to an unroled div, which would break the
         chain a treegrid needs: rowgroup owns rows, rows own gridcells. -->
    <div class="stack-panel__grid" role="presentation">
      <div
        v-for="(member, index) in members"
        :id="`${panelId}-row-${member.key}`"
        :key="member.key"
        class="stack-panel__member"
        role="row"
        aria-level="2"
        :aria-posinset="index + 1"
        :aria-setsize="size || members.length"
        :aria-selected="selectedKeys.includes(member.key)"
        :tabindex="cursorKey === member.key ? 0 : -1"
        :data-key="member.key"
        @click="emit('select', member.key, $event)"
      >
        <div class="stack-panel__cell" role="gridcell">
          <WorkflowCard :card="member" member />
          <!-- The cover is what the others are compared against, so it is the
               one member whose special row is empty; without the flag its row
               reads as "this one differs by nothing". -->
          <span v-if="index === 0" class="stack-cover-flag">Cover</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * One open workflow stack (v1.12 Workflows & Recipes, F1a — Grid only).
 *
 * The panel is ONE element in the card grid at `grid-column: 1 / -1`, placed
 * after the last card of the stack's row, so opening it pushes the later rows
 * down rather than floating over them. The caret points back at the card that
 * opened it: `--notch` is derived from that card's COLUMN INDEX every time the
 * container resizes, never stored as a pixel offset, which is what would leave
 * it pointing at the wrong card after a resize.
 *
 * Neutral ground for being merely OPEN — `--panel`, a hairline border,
 * `--radius-lg` — because the wash it used to wear on opening said "selected"
 * about six cards nobody had selected, and the open card is marked by its
 * rotated ▸ alone. The band takes the wash and the ring when `selected` says
 * those cards are in fact all selected: a stack is selected whole, so one mark
 * round the band is the mark for the stack, and what the wash stands for is now
 * exactly what it reads as.
 *
 * The Grid|List switch, *Unstack all* and the hidden-member count are not here.
 * The switch is F2's. The other two are blocked on the READ side rather than
 * the write side: B4 (#1396) shipped `POST /workflows/stacks/{id}/unstack`,
 * but `GET /workflows/cards` serves no `stack_id` to address it by — and it
 * drops hidden cards BEFORE grouping, so a stack's hidden-member count is not
 * in the payload at any level. Both want one more field on the card.
 */
import { computed } from "vue";

import AppButton from "../widgets/AppButton.vue";
import WorkflowCard from "../widgets/WorkflowCard.vue";

const props = defineProps({
  /** The panel's DOM id — the stack card's `aria-controls`. */
  panelId: { type: String, required: true },
  /** The stack's name, as the cover card carries it. */
  name: { type: String, default: "" },
  /** The stack's cards, cover first — the cover alone while the rest load. */
  members: { type: Array, default: () => [] },
  /** How many cards the stack HAS, which is not how many have arrived. */
  size: { type: Number, default: 0 },
  /** A member request is still running. */
  loading: { type: Boolean, default: false },
  /** The grid's current column count, from its ResizeObserver. */
  columns: { type: Number, default: 1 },
  /** The stack card's 0-based column in that grid. */
  columnIndex: { type: Number, default: 0 },
  /** Keys of the selected cards, top-level and member alike. */
  selectedKeys: { type: Array, default: () => [] },
  /**
   * The WHOLE stack is selected, so the band wears one mark and its rows wear
   * none. A partly selected stack leaves this false and the rows mark
   * themselves, which is the only way a reader can see which of them are in.
   */
  selected: { type: Boolean, default: false },
  /** The roving cursor's key, when it is inside this panel. */
  cursorKey: { type: String, default: "" },
});

const emit = defineEmits(["close", "select"]);

// The stack's own size, not how many cards have arrived: counting what is on
// screen makes the header read "1 workflow" over a stack of six for as long as
// the member requests take — and permanently, if one of them fails.
const countLabel = computed(() => {
  const total = props.size || props.members.length;
  return total === 1 ? "1 workflow" : `${total} workflows`;
});

/** Said only while some of the stack is still missing from the panel. */
const pending = computed(() => {
  const missing = (props.size || props.members.length) - props.members.length;
  if (missing <= 0) return "";
  return props.loading ? "reading the rest…" : `${missing} could not be read`;
});

const toolbarName = computed(() => `${props.name || "Stack"} stack`);

// The centre of the column the stack card sits in, as a share of the panel's
// own width — the panel spans every column, so the two grids share an origin.
// At one column the caret falls back to the shipped `--start` class instead,
// so nothing here hand-copies that rule's inset.
const notchStyle = computed(() => {
  const style = { "--wf-columns": props.columns };
  if (props.columns > 1) {
    style["--notch"] = `${((props.columnIndex + 0.5) / props.columns) * 100}%`;
  }
  return style;
});
</script>

<style scoped>
.stack-panel {
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
   point; the rotation is the shipped one and has to be restated because the
   two live in one `transform`. */
.stack-panel__notch {
  left: var(--notch);
  transform: translateX(-50%) rotate(45deg);
}

.stack-panel__bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--bar-height);
  padding: 0 var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.stack-panel__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.stack-panel__count {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.stack-panel__spacer {
  flex: 1;
}

.stack-panel__pending {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  font-style: italic;
}

/* The SAME column count as the grid above, taken from `--wf-columns` rather
   than recomputed with `auto-fill`: the panel's padding narrows the track, and
   an auto-fill here would quietly drop to one column fewer — at which point a
   member's index stops naming the column it is drawn in and Down lands
   somewhere else. `minmax(0, 1fr)` so a wide card cannot push the track out. */
.stack-panel__grid {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  gap: var(--space-4);
  padding: var(--space-4);
}

/* No focus rule: `style.css` paints the one ring, and a row that stays silent
   gets it. */
.stack-panel__cell {
  position: relative;
  border-radius: var(--radius-md);
}

/* The whole stack selected: one mark round the band, because the stack is what
   was selected. The shell's mark (`style.css`, "THE SELECTION MARK") — the wash
   plus `--selection-ring` rather than `--selection-edge`, because what is
   wanted here is a border round the whole stack and not a rail down one side
   of it; the grid card above takes the edge instead, so the pair carries one
   closed box between them.

   The wash is a `background-image` layer over `--panel` rather than a
   `background`, the same way `--hover-shade` layers over a filled control:
   replacing the colour would put a translucent olive over whatever is behind
   the band instead of over its own ground. The header's `on-panel` text sits on
   that layer, at 4.98:1 light and 4.63:1 dark for the secondary figures
   (11.22:1 / 7.52:1 at full ink) — above the 4.5:1 this system asks of small
   text, so the wash costs the header nothing. */
.stack-panel--selected {
  background-image: linear-gradient(var(--active-wash), var(--active-wash));
  box-shadow: var(--selection-ring);
}

/* The notch follows the band. `.tbm-caret` (`App.css`) hardcodes
   `background: rgb(var(--v-theme-panel))`, so without this a selected stack
   draws a bare panel-coloured caret against a washed band — a visible seam at
   the one point the design means as the join to the card. The shorthand there
   is lower specificity, so it cannot take this layer back off. */
.stack-panel--selected .tbm-caret {
  background-image: linear-gradient(var(--active-wash), var(--active-wash));
}

/* A PARTLY selected stack marks its rows instead — the same mark the top-level
   cards wear, so a mixed selection reads as one. Suppressed while the band
   carries it, or a row the reader had taken back out of the selection would
   still be wearing olive inside a washed band. */
.stack-panel:not(.stack-panel--selected)
  .stack-panel__member[aria-selected="true"]
  .stack-panel__cell {
  background: var(--active-wash);
  box-shadow: var(--selection-ring);
}
</style>
