<template>
  <article
    class="msm"
    :class="{
      'msm--head': member.head,
      'msm--unsure': member.ambiguous,
      'msm--on': selected,
    }"
    role="group"
    :aria-label="accessibleName"
    data-testid="model-set-member"
  >
    <!-- The shelf row's own mark when there is a row: the model's icon, or the
         face of the person or set it is assigned to, inside their ring. -->
    <ModelMark
      v-if="shelfMark"
      class="msm__shelfmark"
      :row="shelfMark.row"
      :ring="shelfMark.ring"
      :style="shelfMark.style"
    />
    <span
      v-else
      class="msm__mark"
      :style="{ backgroundColor: initials.color, color: initials.ink }"
      aria-hidden="true"
      >{{ initials.initials }}</span
    >
    <div class="msm__body">
      <div class="msm__top">
        <!-- The name is the way in to what ELSE this file has run with, which is
             the question a tray full of one checkpoint's companions provokes.
             `tabindex="-1"` because the grid's roving cursor owns Tab, exactly as
             the shipped cards' own buttons do. -->
        <button
          class="msm__name"
          type="button"
          tabindex="-1"
          @click.stop="emit('pick', member)"
        >
          <Tooltip
            :text="`What else has ${member.name} run with?`"
            activator="parent"
          />
          {{ member.name }}
        </button>
        <span v-if="member.head" class="msm__pill">Names this set</span>
      </div>
      <span v-if="member.filename" class="msm__file">{{
        member.filename
      }}</span>
      <div class="msm__line">
        <span v-if="member.kindLabel" class="msm__kind">{{
          member.kindLabel
        }}</span>
        <span v-if="size" class="msm__size num">{{ size }}</span>
        <!-- The evidence for THIS file inside THIS group, not the group's total:
             a LoRA used once in a group of forty pictures says so. -->
        <span class="msm__ev num">{{ evidence }}</span>
      </div>
      <p class="msm__also" :class="{ 'msm__also--warn': member.ambiguous }">
        <Tooltip
          v-if="member.ambiguous"
          :text="UNSURE_REASON"
          activator="parent"
        />
        <v-icon v-if="member.ambiguous" size="14">mdi-alert-outline</v-icon>
        {{ member.ambiguous ? "Which file ran is not recorded" : sharing }}
      </p>
    </div>
  </article>
</template>

<script setup>
// One model in an open set's tray (#1438) — the Grid view of it.
//
// **A member is a MODEL, not a combination.** The tray answers "what has run with
// this checkpoint", so each entry is one file with its own evidence: how many of
// this group's recipes name it, and how widely it is shared across the other
// groups on the grid. What it deliberately does NOT claim is that the files in one
// tray ran together — the tray's own header says so, because a group is the union
// of its recipes and a union is not reproducible.
//
// The tray row around it owns the cursor, the click and the right-click - a member
// is one model, so it selects and takes the shelf's verbs exactly as a row in the
// list does. The card itself draws whether it is ticked, and its name opens
// `Works with` for this file.

import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { formatModelSize, generatedMark } from "../../utils/modelShelf";
import {
  pictureCount,
  recipeCount,
  sharingLabel,
} from "../../utils/workflowSets";
import ModelMark from "./ModelMark.vue";
import Tooltip from "./Tooltip.vue";

/**
 * Why a member is drawn as uncertain.
 *
 * One sentence for all three causes `ambiguous` has — a shared basename, a short
 * digest matching more than one row, and a recipe naming a digest no row matches
 * while some row still waits for its hash. Saying "filename match only" would be a
 * specific wrong reason for two of them.
 */
const UNSURE_REASON =
  "The recipe does not pin this down to one file on the shelf — a basename " +
  "several rows answer to, a short digest that matches more than one, or a " +
  "model still waiting for its hash. It is listed because it is the best " +
  "answer the evidence gives, not hidden because it is not the only one.";

const props = defineProps({
  /** One member from a group (see `setGroups` in `utils/workflowSets.js`). */
  member: { type: Object, required: true },
  /** This model is in the shelf's selection. */
  selected: { type: Boolean, default: false },
  /** `{row, ring, style}` from the shelf, or null for a model with no row. */
  shelfMark: { type: Object, default: null },
});

/** The name was pressed: the grid answers with this model's companions. */
const emit = defineEmits(["pick"]);

// The shelf's own identity square, so a model looks the same here as in the row
// list. `generatedMark` reads a row's name and base model, so the member is
// spelled into that shape rather than a second mark being invented.
const initials = computed(() =>
  generatedMark({
    display_name: props.member.name,
    filename: props.member.filename,
    base_model: props.member.base_model ?? null,
  }),
);

const size = computed(() => formatModelSize(props.member.file_size));

const evidence = computed(() =>
  [recipeCount(props.member.recipes), pictureCount(props.member.pictures)].join(
    " · ",
  ),
);

const sharing = computed(() => sharingLabel(props.member.otherSets));

// Everything visible is either a control or `aria-hidden`, so this is the only
// place a screen reader hears the whole member.
const accessibleName = computed(() =>
  [
    props.member.name,
    props.member.head ? "names this set" : null,
    props.member.kindLabel,
    size.value,
    evidence.value,
    props.member.ambiguous ? "which file ran is not recorded" : sharing.value,
  ]
    .filter(Boolean)
    .join(", "),
);
</script>

<style scoped>
.msm {
  display: flex;
  gap: var(--space-3);
  box-sizing: border-box;
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}

/* The file the group is named after. `--rail-w` and `--active-bar`, the same two
   values `--selection-edge` resolves to, because a SELECTED row wears that rail
   and a head row must not jump by a pixel or change hue when it is ticked. The
   two marks therefore coincide on a selected head, and what tells head from
   ticked is the `Names this set` pill above - which is text, so it survives
   greyscale, and is always drawn. */
.msm--head {
  box-shadow: inset var(--rail-w) 0 0 var(--active-bar);
}

/* The evidence could not pin this file down. Dashed, the shelf's own treatment
   for a row it cannot be certain about, and drawn rather than dropped. */
.msm--unsure {
  border-style: dashed;
}

/* The row list's selected treatment: a wash and an inset bar, never a border,
   which would shift every glyph in the card by a pixel. The wash is what this
   adds to a head row, whose rail is already the same rail - see the note at
   `.msm--head`. Safe as a plain background here, unlike on the card above: a
   member draws no pictures, so nothing paints over it. */
.msm--on {
  background: var(--active-wash);
  box-shadow: var(--selection-edge);
}

.msm__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.02em;
}

.msm__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.msm__top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

/* A button wearing no button chrome: it is a name in a list, and a bordered
   control per member would make the tray louder than the grid above it. */
.msm__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: inherit;
  font: inherit;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.msm__name:hover {
  text-decoration: underline;
}

.msm__pill {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  flex-shrink: 0;
  height: var(--tag-h-xs);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
}

.msm__file {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msm__line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  flex-wrap: wrap;
}

/* `ChipRow`'s chip, restated: this is one datum on a line rather than a clipping
   row, and that component's whole job is to fit chips on one line and drop the
   rest. */
.msm__kind {
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

.msm__size,
.msm__ev {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.msm__also {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* `surface-warning`, not `warning`: the swatch is a fill colour and this is text
   on the card surface, where the contrast-safe pair is the one the theme ships
   for ink. */
.msm__also--warn {
  color: rgb(var(--v-theme-surface-warning));
}
</style>
