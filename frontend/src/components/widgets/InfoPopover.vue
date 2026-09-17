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
        :props="{
          ...menuProps,
          'aria-haspopup': 'dialog',
          'aria-expanded': String(open),
        }"
      />
    </template>

    <div
      class="tbm info-popover"
      role="dialog"
      :aria-label="`About ${card.name}`"
      data-testid="workflow-info-popover"
    >
      <span class="tbm-caret info-popover__caret" aria-hidden="true"></span>
      <h4 class="info-popover__title">{{ card.name }}</h4>
      <p class="info-popover__sub">{{ subtitle }}</p>

      <section class="info-popover__group">
        <h5 class="tbm-label">Models</h5>
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

      <section v-if="card.differsBy?.length" class="info-popover__group">
        <h5 class="tbm-label">Differs by</h5>
        <div class="info-popover__chips">
          <span
            v-for="(fact, i) in card.differsBy"
            :key="i"
            class="info-popover__chip"
            >{{ fact }}</span
          >
        </div>
      </section>

      <section class="info-popover__group">
        <h5 class="tbm-label">Defaults</h5>
        <dl class="info-popover__kv">
          <template v-for="entry in card.defaults ?? []" :key="entry.label">
            <dt>{{ entry.label }}</dt>
            <dd>{{ entry.value }}</dd>
          </template>
          <dt>Saved recipes</dt>
          <dd>{{ card.savedRecipeCount ?? 0 }}</dd>
        </dl>
      </section>
    </div>
  </v-menu>
</template>

<script setup>
// The ⓘ popover on a workflow card: everything the four card rows had to clip,
// grouped. The app had no shared popover, so this is the `.tbm` panel shell on
// a `v-menu`, opened from whatever the activator slot renders.

import { computed, ref } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import { isStack } from "../../utils/workflowCard";

const props = defineProps({
  /** One workflow card (see utils/workflowCard.js for the shape). */
  card: { type: Object, required: true },
});

const open = ref(false);

const subtitle = computed(() => {
  const n = props.card.pictureCount ?? 0;
  const pictures = n === 1 ? "found in 1 picture" : `found in ${n} pictures`;
  return isStack(props.card)
    ? `Stack of ${props.card.stackSize} workflows · ${pictures}`
    : pictures.charAt(0).toUpperCase() + pictures.slice(1);
});

const models = computed(() => [
  ...(props.card.checkpoint
    ? [
        {
          key: "checkpoint",
          icon: "cube-outline",
          name: props.card.checkpoint,
          note: "checkpoint",
        },
      ]
    : []),
  ...(props.card.loras ?? []).map((lora, i) => ({
    key: `lora-${i}`,
    icon: lora.recipe ? "plus" : "layers",
    name: lora.recipe ? "LoRA slot" : lora.name,
    note: lora.recipe ? "filled by the recipe" : "in the workflow",
  })),
]);
</script>

<style scoped>
.info-popover {
  width: var(--stats-panel-w);
  padding: var(--space-4);
}

/* Centred on the 24px ⓘ it opens from, which sits at the panel's end edge. */
.info-popover__caret {
  right: calc(var(--control-h-sm) / 2 - 5.5px);
}

.info-popover__title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
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

.info-popover__chip {
  display: inline-flex;
  align-items: center;
  height: var(--chip-h);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
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

.info-popover__kv dd {
  margin: 0;
  font-variant-numeric: tabular-nums;
}
</style>
