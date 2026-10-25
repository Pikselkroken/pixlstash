<template>
  <!-- The favourite mark (#1578): a heart, never a star, because a star
       already means a rating in this app. Ink, not colour: the SHAPE carries
       the state, filled for on and an outline for off, so it passes every
       hover and selection wash. Off and not hovered draws nothing, so a column
       of these stays quiet; the space is kept so nothing shifts. -->
  <span
    v-if="passive"
    class="fav-heart fav-heart--passive"
    :class="`fav-heart--${variant}`"
    role="img"
    aria-label="Favourite"
  >
    <VIcon :size="iconSize">mdi-heart</VIcon>
  </span>
  <Tooltip
    v-else
    :text="tooltip"
    :shortcut="disabled ? '' : 'H'"
    :describe="false"
  >
    <template #activator="{ props: tipProps }">
      <button
        v-bind="tipProps"
        type="button"
        class="fav-heart"
        :class="[`fav-heart--${variant}`, { 'fav-heart--off': !on }]"
        :aria-pressed="on ? 'true' : 'false'"
        :aria-label="label ? `Favourite ${label}` : 'Favourite'"
        :aria-disabled="disabled ? 'true' : undefined"
        :tabindex="tabindex"
        data-testid="favourite-heart"
        @click.stop="!disabled && emit('toggle')"
        @dblclick.stop
        @mousedown.stop
        @keydown.enter.stop
        @keydown.space.stop
      >
        <VIcon :size="iconSize">{{
          on ? "mdi-heart" : "mdi-heart-outline"
        }}</VIcon>
      </button>
    </template>
  </Tooltip>
</template>

<script setup>
import { computed } from "vue";
import { VIcon } from "vuetify/components";
import Tooltip from "./Tooltip.vue";

const props = defineProps({
  /** Is this a favourite now? */
  on: { type: Boolean, default: false },
  /** What is favourited, for the accessible name. */
  label: { type: String, default: "" },
  /**
   * `row` - the 24px leading column on a shelf row or a list.
   * `cover` - the round scrim chip on a card cover's top-left corner.
   */
  variant: { type: String, default: "row" },
  /** A read-only mark (the pickers): drawn only when on, never a button. */
  passive: { type: Boolean, default: false },
  /** Why it cannot be favourited yet (an unhashed model). */
  disabledReason: { type: String, default: "" },
  /** Rows reach it with H, so it is out of the tab order by default. */
  tabindex: { type: [Number, String], default: -1 },
});

const emit = defineEmits(["toggle"]);

const disabled = computed(() => Boolean(props.disabledReason));
const iconSize = computed(() => (props.variant === "cover" ? 16 : 14));
const tooltip = computed(
  () => props.disabledReason || (props.on ? "Unfavourite" : "Favourite"),
);
</script>

<style scoped>
.fav-heart {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  cursor: pointer;
  transition:
    opacity var(--dur-1) var(--ease-standard),
    background-color var(--dur-1) var(--ease-standard);
}

.fav-heart--passive {
  width: var(--gutter-glyph);
  height: var(--gutter-glyph);
  cursor: default;
}

.fav-heart--off {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  opacity: 0;
}

/* The host (a row, a card) reveals the outline: the outline IS the button. */
:global(.fav-host:hover) .fav-heart--off,
:global(.fav-host:focus-within) .fav-heart--off,
:global(.fav-host:focus) .fav-heart--off,
.fav-heart--off:focus-visible {
  opacity: 1;
}

button.fav-heart:hover {
  background: var(--hover-wash);
}

/* Doubled to outrank the host's reveal: an unhashed model's heart stays dim. */
.fav-heart.fav-heart[aria-disabled="true"] {
  cursor: default;
  opacity: var(--opacity-disabled);
}

.fav-heart:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: calc(-1 * var(--focus-width));
}

/* On a photo: the shipped scrim chip, round, light ink in both themes. */
.fav-heart--cover {
  width: var(--control-h);
  height: var(--control-h);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  pointer-events: auto;
}

.fav-heart--cover.fav-heart--off {
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

button.fav-heart--cover:hover {
  background: var(--scrim-photo-strong);
}
</style>
