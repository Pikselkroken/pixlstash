<template>
  <article
    class="combo"
    :class="{ 'combo--seed': card.seed }"
    role="group"
    :aria-label="accessibleName"
    data-testid="model-combo-card"
  >
    <div class="combo__top">
      <span class="combo__name" aria-hidden="true">{{ card.name }}</span>
      <span v-if="card.picture_count" class="combo__pill" aria-hidden="true">
        {{ card.picture_count.toLocaleString() }}
        {{ card.picture_count === 1 ? "picture" : "pictures" }}
      </span>
    </div>

    <div v-if="card.covers.length" class="combo__strip" aria-hidden="true">
      <span
        v-for="url in card.covers"
        :key="url"
        class="combo__pic"
      >
        <img :src="url" alt="" loading="lazy" />
      </span>
    </div>

    <!-- Every file, with its kind, never truncated. This is the level at which
         "run exactly this" is a real offer, and an offer with a hidden line is
         not one - so no chevron, no "+N", and the card grows with the set. -->
    <ul class="combo__files">
      <li v-for="file in card.files" :key="file.id" class="combo__file">
        <!-- The file's name is the way in to what ELSE it has run with: a
             reader looking at a set is one click from the same question about
             any file in it. `tabindex="-1"` because the grid's roving cursor
             owns Tab, exactly as `WorkflowCard`'s own two buttons do. -->
        <button
          class="combo__filename"
          type="button"
          tabindex="-1"
          @click.stop="emit('pick', file)"
        >
          <Tooltip
            :text="`What else has ${file.name} run with?`"
            activator="parent"
          />
          {{ file.name }}
        </button>
        <span v-if="file.kindLabel" class="combo__kind">{{
          file.kindLabel
        }}</span>
        <!-- The evidence could not tell this file from another of the same
             name. Said on the card rather than hidden: the shelf already treats
             not knowing as an answer, and a set drawn without the warning would
             be claiming a certainty the recipe does not have. -->
        <Tooltip
          v-if="file.ambiguous"
          text="A recipe named a file by this basename and more than one model on the shelf answers to it, so which of them ran is not recorded."
        >
          <template #activator="{ props: tipProps }">
            <v-icon v-bind="tipProps" size="14" class="combo__warn"
              >mdi-alert-outline</v-icon
            >
          </template>
        </Tooltip>
      </li>
    </ul>

    <p class="combo__note" aria-hidden="true">{{ card.note }}</p>
  </article>
</template>

<script setup>
// One combination in an open set's panel (#1438): the exact files a picture
// proves ran together, listed in full.
//
// Deliberately NOT `WorkflowCard`. That card is a fixed height with four
// single-line rows that clip to "+N", which is right for browsing and wrong
// here: this is the card a reader takes a set off, so the file list is the
// content rather than a summary of it, and clipping the fourth file would
// silently break the offer the card makes. The stack card above it IS a
// `WorkflowCard`, so the grid still has one browsing shape.
//
// The card is inert: the panel row around it owns selection and the roving
// cursor, exactly as the workflows grid's member rows do.

import { computed } from "vue";
import { VIcon } from "vuetify/components";

import Tooltip from "./Tooltip.vue";

const props = defineProps({
  /** One card from `comboCard` (see `utils/workflowSets.js`). */
  card: { type: Object, required: true },
});

/** A file line was pressed: the grid answers with that model's companions. */
const emit = defineEmits(["pick"]);

// The chips and the note are `aria-hidden`, so this is the only place a screen
// reader hears them - and the only place it hears which files are in the set.
const accessibleName = computed(() => {
  const files = props.card.files.map((file) =>
    [file.kindLabel, file.name, file.ambiguous ? "filename match only" : null]
      .filter(Boolean)
      .join(" "),
  );
  const pictures = props.card.picture_count ?? 0;
  return [
    props.card.name,
    files.length ? `files: ${files.join("; ")}` : null,
    pictures === 1 ? "1 picture" : `${pictures} pictures`,
    props.card.note,
  ]
    .filter(Boolean)
    .join(", ");
});
</script>

<style scoped>
.combo {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  box-sizing: border-box;
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}

/* The one member the others are described against. A left rail rather than a
   fill: a fill reads as "selected", and nothing here is. */
.combo--seed {
  box-shadow: inset 3px 0 0 var(--active-bar);
}

.combo__top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.combo__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

/* The shipped outlined pill, the one `WorkflowCard` draws over an empty cover. */
.combo__pill {
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
  font-variant-numeric: tabular-nums;
}

/* As many columns as there are covers, so two pictures are two squares rather
   than two squares and a hole. The depth is the server's (`SET_COVER_DEPTH`),
   which is the workflow card's own, so the strip and the cover above it are
   reading the same three bitmaps. */
.combo__strip {
  display: grid;
  grid-auto-columns: 1fr;
  grid-auto-flow: column;
  gap: var(--space-1);
  overflow: hidden;
  border-radius: var(--radius-sm);
}

.combo__pic {
  aspect-ratio: 1 / 1;
  overflow: hidden;
  background: rgb(var(--v-theme-input-background));
}

.combo__pic img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.combo__files {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.combo__file {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

/* A button wearing no button chrome: it is a name in a list, and a bordered
   control per file would make four of them the loudest thing on the card. The
   hover wash and the shell's own focus ring are what say it is pressable. */
.combo__filename {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: inherit;
  font: inherit;
  font-size: var(--text-xs);
  text-align: left;
  cursor: pointer;
}

.combo__filename:hover {
  text-decoration: underline;
}

/* `ChipRow`'s chip, restated inline because these are one datum per line rather
   than a clipping row: the row component's whole job is to fit chips on ONE
   line and drop the rest, which is exactly what this card must not do. */
.combo__kind {
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

/* `surface-warning`, not `warning`: the swatch is a fill colour and this is a
   glyph on the card surface, where the contrast-safe pair is the one the theme
   ships for text. */
.combo__warn {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

.combo__note {
  margin: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
