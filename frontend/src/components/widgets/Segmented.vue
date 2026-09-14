<template>
  <div
    role="radiogroup"
    class="seg"
    :class="[`seg--${variant}`, { 'seg--full': full, 'seg--disabled': disabled }]"
    :aria-label="ariaLabel || undefined"
    :aria-disabled="disabled ? 'true' : undefined"
    @keydown="onKeydown"
  >
    <button
      v-for="o in options"
      :key="String(o.id)"
      type="button"
      role="radio"
      class="seg__opt"
      :class="{ 'seg__opt--on': o.id === modelValue }"
      :aria-checked="o.id === modelValue ? 'true' : 'false'"
      :tabindex="o.id === tabStop ? 0 : -1"
      :disabled="disabled || o.disabled"
      :aria-label="variant === 'icon' ? o.title || o.label : undefined"
      :data-testid="o.testid"
      @click="select(o)"
    >
      <!-- An icon-only option's name is its tip as well (buttons.md,
           "Tooltips"): one string, so the two cannot drift. -->
      <Tooltip
        v-if="variant === 'icon'"
        :text="o.title || o.label"
        activator="parent"
        :describe="false"
      />
      <span v-if="variant === 'stacked'" class="seg__media">
        <slot name="media" :option="o">
          <v-icon v-if="o.icon" size="24">{{ iconName(o.icon) }}</v-icon>
        </slot>
      </span>
      <v-icon
        v-else-if="(variant === 'icon' || variant === 'icon-label') && o.icon"
        size="16"
        class="seg__icon"
        >{{ iconName(o.icon) }}</v-icon
      >
      <span v-if="variant !== 'icon'" class="seg__label">{{ o.label }}</span>
    </button>
  </div>
</template>

<script setup>
/**
 * Pick one of two to five short options (docs/design/buttons.md, "Pick
 * one"). A long or server-supplied list is OptionRows instead.
 *
 * The track is a trough with an INSET ring, so it stays --control-h-bar:
 * 4 + --control-h-sm + 4. Corners are concentric: a --radius-md track with
 * --space-2 padding holds --radius-sm segments, 8 - 4 = 4.
 *
 * Selected is olive (amber acts, olive selects). ARIA is radiogroup/radio
 * with aria-checked, never tablist: this sets a value.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";
import Tooltip from "./Tooltip.vue";
import { arrowStep, tabStopId } from "../../utils/radioGroup.js";

const props = defineProps({
  // [{ id, label, icon?, title?, disabled?, testid? }]
  options: { type: Array, required: true },
  modelValue: { type: [String, Number, Boolean, null], default: null },
  // label | icon-label | icon | stacked
  variant: { type: String, default: "label" },
  // Segments share the track's width equally.
  full: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  ariaLabel: { type: String, default: "" },
});

// `pick` fires for every click, the current value included, like OptionRows:
// a group whose value has SLACK (the zoom snaps match within 1%) reads as
// already selected while the thing it names is off the stop.
const emit = defineEmits(["update:modelValue", "pick"]);

const tabStop = computed(() => tabStopId(props.options, props.modelValue));

function iconName(icon) {
  return icon.startsWith("mdi-") ? icon : `mdi-${icon}`;
}

function select(option) {
  if (props.disabled || option.disabled) return;
  if (option.id !== props.modelValue) emit("update:modelValue", option.id);
  emit("pick", option.id);
}

function onKeydown(event) {
  if (props.disabled) return;
  const id = arrowStep(event, props.options, props.modelValue);
  if (id !== undefined && id !== props.modelValue) {
    emit("update:modelValue", id);
  }
}
</script>

<style scoped>
.seg {
  display: inline-flex;
  box-sizing: border-box;
  padding: var(--space-2);
  background: var(--track-trough);
  /* Inset, so the track stays 32px. A 1px border would make it 34. */
  box-shadow: inset 0 0 0 1px var(--track-ring);
  border-radius: var(--radius-md);
}

.seg--full {
  display: flex;
  width: 100%;
}

.seg--disabled {
  opacity: var(--opacity-disabled);
}

.seg__opt {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  height: var(--control-h-sm);
  min-width: 56px;
  padding: 0 var(--space-3);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-regular);
  white-space: nowrap;
  cursor: pointer;
  transition:
    background var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard);
}

.seg--full .seg__opt {
  flex: 1 1 0;
  min-width: 0;
}

.seg__label {
  overflow: hidden;
  text-overflow: ellipsis;
}

.seg__opt:disabled {
  cursor: not-allowed;
}

/* One unavailable option fades on its own; a disabled group fades as a whole. */
.seg:not(.seg--disabled) .seg__opt:disabled {
  opacity: var(--opacity-disabled);
}

.seg__opt:not(:disabled):not(.seg__opt--on):hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

/* Olive fill, warm-white label (4.86:1). The elevation is a second cue beyond
   hue, which matters most in light where olive sits 3.72:1 off the trough. */
.seg__opt--on {
  background-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  box-shadow: var(--elevation-1);
}

.seg__opt--on:not(:disabled):hover {
  background-image: var(--hover-shade);
}

.seg--icon .seg__opt {
  width: var(--control-h-bar);
  min-width: var(--control-h-bar);
  padding: 0;
}

.seg--stacked .seg__opt {
  flex-direction: column;
  height: var(--seg-stacked-h);
  min-width: 76px;
  padding: var(--space-3);
  font-size: var(--text-xs);
}

.seg__media {
  display: grid;
  place-items: center;
  height: var(--seg-media-h);
}
</style>
