<template>
  <div class="app-select">
    <FieldLabel v-if="label && !hideLabel">{{ label }}</FieldLabel>
    <div
      v-if="multiple"
      class="app-select__multiple"
      role="group"
      :aria-label="label || 'Options'"
    >
      <label
        v-for="opt in normalizedOptions"
        :key="String(opt.value)"
        class="app-select__multiple-option"
        :class="{ 'app-select__multiple-option--disabled': disabled }"
      >
        <input
          type="checkbox"
          :value="opt.value"
          :checked="isSelected(opt.value)"
          :disabled="disabled"
          @change="toggleValue(opt.value, $event.target.checked)"
        />
        <span>{{ opt.label }}</span>
      </label>
      <span v-if="normalizedOptions.length === 0" class="app-select__empty">
        No options available
      </span>
    </div>
    <!-- Two-line options (#1525): an option carrying `chips` needs a second
         line a native <select> cannot draw, so the field becomes the
         select-only combobox of the ARIA pattern. Focus stays on the field;
         the listbox follows it through aria-activedescendant. -->
    <div v-else-if="rich" ref="wrapEl" class="app-select__wrap" @focusout="onFocusOut">
      <div
        class="app-select__field app-select__combo"
        :class="{
          'app-select__field--compact': compact,
          'app-select__combo--disabled': disabled,
        }"
        role="combobox"
        :tabindex="disabled ? -1 : 0"
        :aria-label="label || undefined"
        aria-haspopup="listbox"
        :aria-expanded="String(open)"
        :aria-controls="listId"
        :aria-activedescendant="open ? optionId(activeIndex) : undefined"
        :aria-disabled="disabled || undefined"
        @click="toggle"
        @keydown="onComboKeydown"
      >
        <span class="app-select__value">{{ optionName(current) }}</span>
        <ChipRow
          v-if="current?.chips?.length"
          class="app-select__value-chips"
          :items="chipItems(current.chips.slice(0, 1))"
        />
      </div>
      <v-icon size="18" class="app-select__chevron">mdi-chevron-down</v-icon>
      <!-- mousedown is held back so a click on a row never takes focus off
           the field, which would close the list before the click lands. -->
      <div
        v-if="open"
        :id="listId"
        class="ctx-menu app-select__menu"
        role="listbox"
        :aria-label="label || undefined"
        @mousedown.prevent
      >
        <template v-for="(opt, i) in normalizedOptions" :key="String(opt.value)">
          <template
            v-if="opt.group && opt.group !== normalizedOptions[i - 1]?.group"
          >
            <div v-if="i > 0" class="ctx-sep" role="presentation" />
            <div class="ctx-label section-label" role="presentation">
              {{ opt.group }}
            </div>
          </template>
          <div
            :id="optionId(i)"
            :ref="(el) => (optionEls[i] = el)"
            class="ctx-item app-select__option"
            :class="{ 'app-select__option--active': i === activeIndex }"
            role="option"
            :aria-selected="String(isCurrent(opt))"
            :aria-label="optionSpoken(opt)"
            @click="choose(i)"
            @mousemove="activeIndex = i"
          >
            <span class="app-select__check" aria-hidden="true">
              <v-icon v-if="isCurrent(opt)" size="16">mdi-check</v-icon>
            </span>
            <span class="app-select__text">
              <span class="app-select__name">{{ optionName(opt) }}</span>
              <ChipRow v-if="opt.chips?.length" :items="chipItems(opt.chips)" />
            </span>
          </div>
        </template>
      </div>
    </div>
    <div v-else class="app-select__wrap">
      <select
        class="app-select__field"
        :class="{ 'app-select__field--compact': compact }"
        :value="modelValue"
        :disabled="disabled"
        :aria-label="label || undefined"
        @change="emit('update:modelValue', $event.target.value)"
      >
        <option
          v-for="opt in normalizedOptions"
          :key="String(opt.value)"
          :value="opt.value"
        >
          {{ opt.label }}
        </option>
      </select>
      <v-icon size="18" class="app-select__chevron">mdi-chevron-down</v-icon>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, useId, watch } from "vue";
import { VIcon } from "vuetify/components";
import ChipRow from "./ChipRow.vue";
import FieldLabel from "./FieldLabel.vue";

const props = defineProps({
  modelValue: { type: [String, Number, Array, null], default: "" },
  label: { type: String, default: "" },
  // Array of strings OR { label, value } objects. An object may also carry
  // `name` and `chips` (a second line of chips under the name) and `group`
  // (a heading over each run of options sharing it); any option with `chips`
  // turns the field into a listbox that can draw them.
  options: { type: Array, default: () => [] },
  compact: { type: Boolean, default: false },
  // Keep `label` as the accessible name only, for a row whose text already
  // says what the select decides.
  hideLabel: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  multiple: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue"]);

const normalizedOptions = computed(() =>
  props.options.map((o) =>
    typeof o === "object" && o !== null ? o : { label: o, value: o },
  ),
);

const rich = computed(
  () =>
    !props.multiple && normalizedOptions.value.some((o) => o.chips?.length),
);
const current = computed(() =>
  normalizedOptions.value.find((o) => isCurrent(o)),
);

const listId = useId();
const wrapEl = ref(null);
const optionEls = [];
const open = ref(false);
const activeIndex = ref(-1);

function isCurrent(opt) {
  return String(opt.value) === String(props.modelValue);
}

function optionName(opt) {
  return opt ? (opt.name ?? opt.label) : "";
}

/** The row's name and chips as one phrase, since "+N" is not read out. */
function optionSpoken(opt) {
  return [optionName(opt), ...(opt.chips || [])].join(", ");
}

function optionId(i) {
  return i >= 0 ? `${listId}-${i}` : undefined;
}

function chipItems(chips) {
  return chips.map((label, i) => ({ key: `chip-${i}`, label, fact: true }));
}

function openList(index) {
  if (props.disabled || !normalizedOptions.value.length) return;
  const selected = normalizedOptions.value.findIndex((o) => isCurrent(o));
  activeIndex.value = index ?? Math.max(selected, 0);
  open.value = true;
}

function toggle() {
  if (open.value) open.value = false;
  else openList();
}

function choose(i) {
  const opt = normalizedOptions.value[i];
  open.value = false;
  if (opt && !isCurrent(opt)) emit("update:modelValue", opt.value);
}

function onFocusOut(e) {
  if (!wrapEl.value?.contains(e.relatedTarget)) open.value = false;
}

// Typing a name's first letters moves to it, as a native select does: the
// Run popup's list can hold the whole library.
let typed = "";
let typedTimer = null;
function typeahead(char) {
  clearTimeout(typedTimer);
  typedTimer = setTimeout(() => (typed = ""), 500);
  typed += char.toLowerCase();
  const opts = normalizedOptions.value;
  // A fresh single letter starts after the current row, so repeating it
  // walks every option that starts with it.
  const from = typed.length === 1 ? activeIndex.value + 1 : activeIndex.value;
  for (let step = 0; step < opts.length; step++) {
    const i = (Math.max(from, 0) + step) % opts.length;
    if (optionName(opts[i]).toLowerCase().startsWith(typed)) return i;
  }
  return -1;
}

function onComboKeydown(e) {
  if (props.disabled) return;
  const last = normalizedOptions.value.length - 1;
  const clamp = (i) => Math.min(Math.max(i, 0), last);
  if (!open.value) {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      openList();
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      openList(e.key === "Home" ? 0 : last);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const i = typeahead(e.key);
      if (i >= 0) openList(i);
    }
    return;
  }
  const to = {
    ArrowDown: activeIndex.value + 1,
    ArrowUp: activeIndex.value - 1,
    Home: 0,
    End: last,
    PageDown: activeIndex.value + 10,
    PageUp: activeIndex.value - 10,
  }[e.key];
  if (e.key === "ArrowUp" && e.altKey) {
    e.preventDefault();
    choose(activeIndex.value);
  } else if (to !== undefined) {
    e.preventDefault();
    activeIndex.value = clamp(to);
  } else if (e.key === "Enter" || e.key === " ") {
    // Also keeps AppDialog from reading this Enter as "accept".
    e.preventDefault();
    choose(activeIndex.value);
  } else if (e.key === "Escape") {
    // The list closes; the dialog around it stays.
    e.preventDefault();
    e.stopPropagation();
    open.value = false;
  } else if (e.key === "Tab") {
    open.value = false;
  } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
    const i = typeahead(e.key);
    if (i >= 0) activeIndex.value = i;
  }
}

watch([open, activeIndex], () =>
  nextTick(() =>
    optionEls[activeIndex.value]?.scrollIntoView?.({ block: "nearest" }),
  ),
);

function isSelected(value) {
  return (
    Array.isArray(props.modelValue) &&
    props.modelValue.some((selected) => String(selected) === String(value))
  );
}

function toggleValue(value, checked) {
  const selected = Array.isArray(props.modelValue)
    ? props.modelValue.filter((item) => String(item) !== String(value))
    : [];
  if (checked) selected.push(value);
  emit("update:modelValue", selected);
}
</script>

<style scoped>
.app-select {
  display: block;
}

.app-select__wrap {
  position: relative;
}

.app-select__multiple {
  display: flex;
  flex-direction: column;
  max-height: 132px;
  overflow-y: auto;
  padding: var(--space-2);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
}

.app-select__multiple-option {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h);
  padding: 0 var(--space-2);
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  cursor: pointer;
}

.app-select__multiple-option:hover {
  background: var(--hover-wash);
}

.app-select__multiple-option--disabled {
  cursor: default;
  opacity: 0.55;
}

.app-select__multiple-option input {
  accent-color: rgb(var(--v-theme-primary));
}

.app-select__empty {
  padding: var(--space-2);
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: var(--text-sm);
}

.app-select__field {
  appearance: none;
  -webkit-appearance: none;
  width: 100%;
  height: var(--control-h);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  padding: 0 38px 0 var(--space-4);
  cursor: pointer;
}

.app-select__field--compact {
  height: var(--control-h-sm);
  font-size: var(--text-xs);
  padding: 0 24px 0 var(--space-3);
}

/* ── The two-line listbox: the design system's Menu surface and row ─────── */
.app-select__combo {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}
.app-select__combo:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: 2px;
}
.app-select__combo--disabled {
  cursor: default;
  opacity: var(--opacity-disabled);
}
.app-select__value {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-select__value-chips {
  flex: 1 1 0;
}

/* The shared menu (`.ctx-menu` / `.ctx-item`), anchored under the field. */
.app-select__menu {
  position: absolute;
  top: calc(100% + var(--space-2));
  left: 0;
  right: 0;
  z-index: var(--z-dropdown);
  /* A ten-member stack is ~460px; past this the list scrolls. */
  max-height: 360px;
  overflow-y: auto;
  font-family: var(--font-ui);
}

/* A two-line row: the menu row's padding in the block axis too, so a name
   and its chips never touch the next row. No fill on the chosen row: a
   medium name and an olive check say it. The ink wash is only what the
   pointer or the arrow keys are on. */
.app-select__option {
  padding-block: var(--space-2);
  white-space: normal;
}
.app-select__option--active {
  background: var(--hover-wash);
}
.app-select__option[aria-selected="true"] .app-select__name {
  font-weight: var(--weight-medium);
}
/* Leading, as the design draws it: the OptionRows mark, in the same slot. */
.app-select__check {
  display: inline-flex;
  flex-shrink: 0;
  width: var(--gutter-glyph);
  color: var(--selected-ink);
}
.app-select__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}
.app-select__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-select__chevron {
  position: absolute;
  right: var(--space-4);
  top: 50%;
  transform: translateY(-50%);
  color: rgba(var(--v-theme-on-surface), 0.5);
  pointer-events: none;
}
</style>
