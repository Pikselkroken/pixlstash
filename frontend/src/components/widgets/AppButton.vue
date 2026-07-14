<template>
  <button
    type="button"
    :class="[
      'app-btn',
      `app-btn--${variant}`,
      `app-btn--${size}`,
      { 'app-btn--icon-only': iconOnly },
    ]"
    :disabled="disabled"
    :title="title"
  >
    <v-icon
      v-if="iconLeft"
      :size="size === 'sm' ? 16 : 18"
      class="app-btn__icon"
    >
      mdi-{{ iconLeft }}
    </v-icon>
    <span v-if="!iconOnly" class="app-btn__label"><slot /></span>
  </button>
</template>

<script setup>
import { VIcon } from "vuetify/components";

defineProps({
  // primary (amber accent) | primary_green (olive) | secondary (neutral) |
  // danger (error) | ghost (transparent)
  variant: { type: String, default: "secondary" },
  size: { type: String, default: "md" }, // md | sm
  iconLeft: { type: String, default: "" },
  iconOnly: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  title: { type: String, default: "" },
});
</script>

<style scoped>
.app-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-family: var(--font-ui);
  font-weight: var(--weight-medium);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  white-space: nowrap;
  transition:
    background var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard),
    filter var(--dur-1) var(--ease-standard);
}

.app-btn--md {
  height: 27px;
  padding: 0 var(--space-5);
  font-size: var(--text-base);
}

.app-btn--sm {
  height: 23px;
  padding: 0 var(--space-4);
  font-size: var(--text-sm);
}

.app-btn--icon-only.app-btn--md {
  width: 27px;
  padding: 0;
}
.app-btn--icon-only.app-btn--sm {
  width: 23px;
  padding: 0;
}

.app-btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.app-btn:disabled {
  opacity: 0.38;
  cursor: not-allowed;
}

/* Primary — amber accent, the key action. */
.app-btn--primary {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}
.app-btn--primary:not(:disabled):hover {
  filter: brightness(1.08);
}

/* Primary green — olive primary, used for create/import affordances. */
.app-btn--primary_green {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}
.app-btn--primary_green:not(:disabled):hover {
  filter: brightness(1.08);
}

/* Secondary — neutral, bordered. The Cancel partner. */
.app-btn--secondary {
  background: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
}
.app-btn--secondary:not(:disabled):hover {
  filter: brightness(1.08);
}

/* Danger — destructive. */
.app-btn--danger {
  background: rgb(var(--v-theme-error));
  color: #fff;
}
.app-btn--danger:not(:disabled):hover {
  filter: brightness(1.08);
}

/* Ghost — transparent, recedes until hovered. */
.app-btn--ghost {
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
.app-btn--ghost:not(:disabled):hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.app-btn__icon {
  flex-shrink: 0;
}
</style>
