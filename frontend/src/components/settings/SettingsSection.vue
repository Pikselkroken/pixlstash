<template>
  <div class="s-section" :class="{ 's-section--first': first }">
    <div v-if="title || $slots.action" class="s-section__head">
      <div v-if="title" class="s-section__title section-label">
        {{ title }}
        <span v-if="count !== null" class="s-section__count num">{{
          count
        }}</span>
      </div>
      <div v-if="$slots.action" class="s-section__action">
        <slot name="action" />
      </div>
    </div>
    <div v-if="desc" class="s-section__desc">{{ desc }}</div>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, default: "" },
  desc: { type: String, default: "" },
  // The first section in a pane has no top divider.
  first: { type: Boolean, default: false },
  // How many things the section lists, beside its title; null for none.
  count: { type: Number, default: null },
});
</script>

<style scoped>
.s-section {
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding: var(--space-3) 0;
}

.s-section--first {
  border-top: none;
  padding: 0 0 var(--space-3);
}

.s-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  min-height: 24px;
  margin-bottom: var(--space-1);
}

/* A section may carry more than one action (API Tokens has two), and two
   adjacent AppButtons would otherwise touch. Inert for a single-button slot. */
.s-section__action {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* The design system's SectionLabel (global `.section-label`): settings
   groups are ALL-CAPS labels, not Title Case headings. */
.s-section__title {
  line-height: var(--leading-snug);
}

/* Not a label: plain figures beside the label. */
.s-section__count {
  margin-left: var(--space-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-regular);
  letter-spacing: normal;
}

.s-section__desc {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  line-height: var(--leading-snug);
  margin-bottom: var(--space-3);
}
</style>
