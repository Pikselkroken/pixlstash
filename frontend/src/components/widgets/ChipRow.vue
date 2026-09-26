<template>
  <div ref="rowEl" class="chip-row">
    <span
      v-for="(item, i) in items"
      v-show="i < visibleCount"
      :key="item.key ?? i"
      :class="chipClass(item)"
      :title="item.title || undefined"
    >
      <v-icon v-if="item.icon" size="12" class="chip-row__icon">{{
        `mdi-${item.icon}`
      }}</v-icon>
      <span class="chip-row__label">{{ item.label }}</span>
    </span>
    <!-- Not a control: the owner's accessible name carries the whole list. -->
    <span v-if="hiddenCount > 0" class="chip-row__more" aria-hidden="true"
      >+{{ hiddenCount }}</span
    >

    <!-- Every chip at its natural width, so a chip hidden by the last fit can
         still be measured when the row grows again. It carries the icon too:
         a probe without one measures every glyphed chip 16px short. -->
    <div ref="measureEl" class="chip-row__measure" aria-hidden="true">
      <span
        v-for="(item, i) in items"
        :key="item.key ?? i"
        class="chip-row__chip"
      >
        <v-icon v-if="item.icon" size="12" class="chip-row__icon">{{
          `mdi-${item.icon}`
        }}</v-icon>
        <span class="chip-row__label">{{ item.label }}</span>
      </span>
      <span class="chip-row__more">+{{ items.length }}</span>
    </div>
  </div>
</template>

<script setup>
// One single-line row of chips that never wraps: what does not fit is clipped
// and counted as "+N". Used by WorkflowCard's LoRA and facts rows, and by the
// stack panel's List "Checkpoint" and "Differs by" columns. **Nothing reads
// `overflow`**: a List row's own `aria-label` already carries every chip the
// line clipped, so the event waits for a caller that wants to say so visibly.
//
// The chip is the design's `.uchip`: a bordered control-tier chip on the input
// surface, --tag-h-xs tall, --text-2xs, with a muted glyph. That is the app
// kit's own chip one size down: `ui_kits/app/unified.css` in the Design System
// project draws `.chip` - "the shape for every small labelled datum" - as
// `--input-bg` behind a 1px `--border` with the label at full `--text`, at
// --text-xs and `2px var(--space-3)`. The kit is the citable half of this; the
// workflow card's own spec lives in an artifact neither synced project holds,
// so cite the kit when the two agree. One place they do not: the kit's unfilled
// `.chip--quiet` drops the fill AND mutes the label, where `fact` below keeps
// full ink, because a fact the card states is not secondary to the model
// beside it.
//
// The glyph separates the three KINDS OF THING a row can name - `cube-outline`
// a model, `layers` a LoRA, `plus` a slot - and says nothing finer:
// `checkpointModel` falls back to the first model of any kind, so a card whose
// graph has no checkpoint draws the model glyph over a unet, exactly as ⓘ
// already lists it. Three variants, all from the design: `dashed` is a slot
// the recipe fills rather than a model the workflow carries; `fact` drops the
// fill because a fact is not a model; "+N" is a fact chip with a muted count.
//
// Approximated against the design, all in the app's favour: 18px tall where it
// draws 20 (--tag-h-xs, rather than a fourth chip height for 2px), --space-2
// padding and icon gap where it draws 6px and 3px (the spacing scale, and the
// token the design manual names for icon-to-label), and the glyph box 12px with
// the glyph at the inherited 11px, because `v-icon`'s numeric size sets the box
// and not the font.
//
// In the LIGHT theme `input-background` and `surface` are both #ffffff, so the
// fill does not separate a model chip from a fact chip there. The outline is
// the same on both, so what is left is the glyph alone - a model chip has one
// and a fact chip does not. That is the design's own light palette, not a
// mapping choice made here, and it is the part worth a designer's sign-off.

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { VIcon } from "vuetify/components";

import { fitChipCount } from "../../utils/workflowCard";

const props = defineProps({
  /** `{ key?, label, icon?, dashed?, fact?, title? }`; `title` is a hover. */
  items: { type: Array, default: () => [] },
});

const chipClass = (item) => [
  "chip-row__chip",
  { "chip-row__chip--dashed": item.dashed, "chip-row__chip--fact": item.fact },
];

const emit = defineEmits(["overflow"]);

const rowEl = ref(null);
const measureEl = ref(null);
const visibleCount = ref(props.items.length);
const hiddenCount = computed(() =>
  Math.max(props.items.length - visibleCount.value, 0),
);

function measure() {
  const row = rowEl.value;
  const probe = measureEl.value;
  if (!row || !probe) return;
  const chips = [...probe.children];
  const more = chips.pop();
  const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
  visibleCount.value = fitChipCount(
    chips.map((c) => c.getBoundingClientRect().width),
    row.clientWidth,
    gap,
    more.getBoundingClientRect().width,
  );
}

watch(hiddenCount, (n) => emit("overflow", n));
watch(
  () => props.items,
  () => nextTick(measure),
  { deep: true },
);

// ponytail: one observer per row, and a hidden copy of every chip. That is fine
// for the tens-to-hundreds of cards F1a expects; if a grid ever holds
// thousands, one shared observer keyed by element is the upgrade.
let observer = null;
onMounted(() => {
  measure();
  observer = new ResizeObserver(measure);
  observer.observe(rowEl.value);
});
onBeforeUnmount(() => observer?.disconnect());

defineExpose({ measure });
</script>

<style scoped>
.chip-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

/* The design's `.uchip`: bordered, on the input surface, the label at full ink. */
.chip-row__chip,
.chip-row__more {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: var(--space-2);
  box-sizing: border-box;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-regular);
  line-height: var(--leading-snug);
  overflow: hidden;
}

/* Muted with a colour, not `opacity`: `--dashed` mutes the chip's own ink, and
   an opacity on top of that would draw the glyph at 0.7 x 0.7 = 0.49 - 3.10:1
   on the light card, under the 4.5:1 `design-tokens.test.js` holds
   `--opacity-text-secondary` to. That guard measures one application of the
   token and cannot see a product of two, so the mute is applied here once. */
.chip-row__icon {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.chip-row__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The one chip allowed to shrink is the first: a single name longer than the
   row ellipsizes rather than being replaced by a bare "+N". Only in the row -
   a probe chip that shrank would measure its own clipped width. */
.chip-row > .chip-row__chip {
  max-width: 100%;
}

.chip-row > .chip-row__chip:first-child {
  flex-shrink: 1;
  min-width: 0;
}

/* A slot to be filled, not a model that is present: the design's `.uchip.rec`,
   dashed and unfilled, so an absence never reads as a model. */
.chip-row__chip--dashed {
  border-style: dashed;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A fact is not a model, so it keeps the outline and loses the fill. */
.chip-row__chip--fact {
  background: transparent;
}

/* An overflow count, not a thing: a fact chip with the count muted. */
.chip-row__more {
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
}

.chip-row__measure {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  gap: var(--space-2);
  height: 0;
  overflow: hidden;
  visibility: hidden;
  pointer-events: none;
}
</style>
