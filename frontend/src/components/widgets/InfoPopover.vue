<template>
  <v-menu
    v-model="open"
    location="bottom end"
    origin="top end"
    :offset="8"
    :close-on-content-click="false"
  >
    <template #activator="{ props: menuProps }">
      <slot
        name="activator"
        :props="{ ...menuProps, 'aria-haspopup': 'dialog' }"
      />
    </template>

    <!-- Focused on open, so a screen reader announces the panel and its virtual
         cursor lands at the TOP of it. The panel holds one focusable child
         since F7 - the picture count is a link - and VMenu's own handling
         would move focus to it, landing the reader mid-panel on a control
         instead of on the thing they asked to read. VOverlay returns focus to
         ⓘ on Escape or a click outside. -->
    <div
      ref="panelEl"
      class="tbm info-popover"
      role="dialog"
      tabindex="-1"
      :aria-label="`About ${card.name}`"
      data-testid="workflow-info-popover"
    >
      <span class="tbm-caret tbm-caret--icon-sm-end" aria-hidden="true"></span>
      <p class="info-popover__title">{{ card.name }}</p>
      <p class="info-popover__sub">
        {{ subtitlePrefix
        }}<button
          v-if="card.picture_count"
          class="info-popover__link"
          type="button"
          data-testid="info-show-pictures"
          :aria-label="`Show all ${picturesLabel}`"
          @click="showPictures(card)"
        >
          {{ picturesLabel }}</button
        ><template v-else>{{ picturesLabel }}</template>
      </p>

      <section class="info-popover__group">
        <span class="tbm-label">Models</span>
        <p v-if="!models.length" class="info-popover__sub">No models</p>
        <div
          v-for="model in models"
          :key="model.key"
          class="info-popover__line"
        >
          <v-icon size="14" class="info-popover__icon">{{
            `mdi-${model.icon}`
          }}</v-icon>
          <span class="info-popover__name">{{ model.name }}</span>
          <span class="info-popover__note">{{ model.note }}</span>
        </div>
      </section>

      <section v-if="card.differs_by?.length" class="info-popover__group">
        <span class="tbm-label">Differs by</span>
        <div class="info-popover__chips">
          <span
            v-for="(fact, i) in card.differs_by"
            :key="i"
            class="info-popover__chip"
            >{{ fact }}</span
          >
        </div>
      </section>

      <section class="info-popover__group">
        <span class="tbm-label">Defaults</span>
        <dl class="info-popover__kv">
          <template v-for="entry in card.defaults ?? []" :key="entry.label">
            <dt>{{ entry.label }}</dt>
            <dd>{{ entry.value }}</dd>
          </template>
          <dt>Saved recipes</dt>
          <dd>{{ card.saved_recipe_count ?? 0 }}</dd>
        </dl>
      </section>
    </div>
  </v-menu>
</template>

<script setup>
// The ⓘ popover on a workflow card: everything the four card rows had to clip,
// grouped. The app had no shared popover COMPONENT (Tooltip and HelpTip are
// hover tips), so this is the `.tbm` panel shell on a `v-menu`, opened from
// whatever the activator slot renders.

import { computed, nextTick, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import { useWorkflowPictures } from "../../composables/useWorkflowPictures";
import { isStack } from "../../utils/workflowCard";

const props = defineProps({
  /** One workflow card (see utils/workflowCard.js for the shape). */
  card: { type: Object, required: true },
});

const { showPictures } = useWorkflowPictures();

const open = ref(false);
const panelEl = ref(null);

watch(open, async (isOpen) => {
  if (!isOpen) return;
  await nextTick();
  panelEl.value?.focus();
});

// Split in two so the figure can be the link (F7) while the words around it
// stay text. "Found in" leads the line on a lone card and follows the stack
// count on a stack, which is the only reason the prefix is not a constant.
const picturesLabel = computed(() => {
  const n = props.card.picture_count ?? 0;
  return n === 1 ? "1 picture" : `${n} pictures`;
});

const subtitlePrefix = computed(() =>
  isStack(props.card)
    ? `Stack of ${props.card.stack_size} workflows · found in `
    : "Found in ",
);

// Every slot, not just the checkpoint: a stack whose difference reads "other
// models" has to have those models listed somewhere, and this is the somewhere.
const models = computed(() => [
  ...(props.card.models ?? []).map((model, i) => ({
    key: `model-${i}`,
    icon: "cube-outline",
    name: model.name,
    note: model.kind,
  })),
  ...(props.card.loras ?? []).map((lora, i) => ({
    key: `lora-${i}`,
    icon: lora.mark === "recipe" ? "plus" : "layers",
    name: lora.mark === "recipe" ? "LoRA slot" : lora.name,
    note: lora.mark === "recipe" ? "filled by the recipe" : "in the workflow",
  })),
]);
</script>

<style scoped>
.info-popover {
  width: var(--stats-panel-w);
  padding: var(--space-4);
}

.info-popover__title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

/* The one control in a panel that otherwise holds none, so it carries the
   underline that says so rather than relying on colour alone. */
.info-popover__link {
  /* The inline-button reset, as `.wftab-pictures` carries it. */
  padding: 0;
  font: inherit;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
}
.info-popover__link:hover {
  color: rgb(var(--v-theme-primary));
}

.info-popover__sub {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.info-popover__group {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-top: var(--space-4);
}

.info-popover__group .tbm-label {
  margin: 0;
}

.info-popover__line {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  font-size: var(--text-xs);
}

.info-popover__icon {
  flex-shrink: 0;
  opacity: var(--opacity-text-secondary);
}

.info-popover__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.info-popover__note {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.info-popover__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

/* The card's fact chip, spelled a second time: the popover WRAPS its chips and
   a ChipRow clips to one line, so it cannot mount one. `ChipRow.test.js` holds
   the two copies to the same geometry - the chips the card showed and the ones
   it clipped are one list, and must not drift apart in two files.
   `on-panel`, not `on-surface`: this chip sits on the menu surface. */
.info-popover__chip {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-panel));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
}

.info-popover__kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2) var(--space-3);
  margin: 0;
  font-size: var(--text-xs);
}

.info-popover__kv dt {
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

/* --text-sm against the dt's --text-xs: the app kit's own `.kv` pair
   (`ui_kits/app/unified.css`), where the label recedes and the value is read. */
.info-popover__kv dd {
  margin: 0;
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}
</style>
