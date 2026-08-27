<template>
  <input
    ref="inputEl"
    v-bind="$attrs"
    :value="modelValue"
    type="text"
    role="combobox"
    aria-autocomplete="list"
    :aria-expanded="String(menuShown)"
    :aria-controls="menuShown ? menuId : undefined"
    :aria-activedescendant="
      menuShown && index >= 0 ? `${menuId}-${index}` : undefined
    "
    @input="onInput"
    @keydown="onKeydown"
    @blur="onBlur"
  />
  <!-- Teleported and fixed, like the tag panel's list, because both places this
       field lands in clip: a dialog body scrolls and a shelf row hides its
       overflow, so an absolutely positioned menu would be cut off in one and
       cropped in the other. -->
  <Teleport to="body">
    <div
      v-if="menuShown"
      :id="menuId"
      class="bmi-menu"
      :style="{
        top: `${rect.bottom + 4}px`,
        left: `${rect.left}px`,
        minWidth: `${Math.max(rect.width, 160)}px`,
      }"
      role="listbox"
      aria-label="Base model suggestions"
    >
      <button
        v-for="(item, idx) in matches"
        :id="`${menuId}-${idx}`"
        :key="item"
        type="button"
        tabindex="-1"
        class="bmi-item"
        :class="{ 'bmi-item--active': idx === index }"
        role="option"
        :aria-selected="idx === index"
        @mousedown.prevent="choose(item)"
      >
        {{ item }}
        <!-- Hidden from the accessible name: it is a hint about the keyboard,
             and read out it turns every first option into "SDXL 1.0 TAB". -->
        <span
          v-if="idx === (index >= 0 ? index : 0)"
          class="bmi-hint"
          aria-hidden="true"
          >TAB</span
        >
      </button>
    </div>
  </Teleport>
</template>

<script setup>
/**
 * The base-model field, completing against what this machine knows.
 *
 * `base_model` is free text and stays that way - this constrains nothing, it
 * only offers. The list comes from `GET /models/base-models`: the labels
 * `known_base_models` ships, so the field is useful on a fresh install where
 * nothing has been recorded yet, plus every distinct string already recorded
 * here. Held in the shelf store rather than fetched per mount, because the two
 * places this appears (the bulk dialog and the inline editor on a row) are
 * opened and closed constantly and the list changes only when somebody saves.
 *
 * Keys follow the tag field as far as the tag field goes - Arrow highlights,
 * Tab fills, and the highlighted row wears the same TAB hint - and then add the
 * two it has no answer for. `OverlayTagsPanel`'s list has no open state at all:
 * it is simply visible whenever the input holds a prefix that matches, inside a
 * panel that is itself a mode. This field lives on a row and inside a dialog
 * that both own Escape, so it needs one: ArrowDown opens the menu, Escape
 * closes it, and only a second Escape reaches the host. Enter commits, taking
 * the highlight if there is one. Anything this consumes is stopped here, so a
 * parent that also listens for Enter or Escape does not act twice.
 *
 * **The menu opens on a keystroke, never on focus.** Both hosts focus this
 * field the moment they draw it, and a menu that opened with them would cover
 * the dialog before a key was pressed and would eat the Escape that dismisses
 * it. ArrowDown is the deliberate "show me everything" gesture. Shift+Tab is
 * left alone even with a menu up, or there would be no way back out of the
 * field for a keyboard reader.
 *
 * **Not a `<datalist>`**, which would have been free. Three things it cannot
 * do: match on the folded spelling (`sdxl` has to find `SDXL 1.0`, and a
 * datalist matches literally), render in the app's own theme rather than the
 * browser's chrome, and honour the key contract the tag field already taught -
 * Tab fills, Enter commits, Escape steps back one level. The first is the whole
 * point of the feature.
 *
 * The menu is drawn like the tag panel's and is deliberately NOT shared with
 * it: that one is wired into predictions, rejected-tag confidences and the
 * image's own tags, and prising it out is a refactor of a component this change
 * does not otherwise touch. The duplication is the treatment, not the logic,
 * and it is the thing to collapse if a third completion field ever appears.
 */
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  useId,
  watch,
} from "vue";

import { useModelShelfStore } from "../../stores/useModelShelfStore";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  modelValue: { type: String, default: "" },
});
const emit = defineEmits(["update:modelValue", "confirm", "cancel"]);

const store = useModelShelfStore();

/** Case, spacing and punctuation folded away - the server's `_norm`. */
function norm(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

const MAX_SHOWN = 8;

/** Unique per instance, because `aria-controls` has to name one menu. */
const menuId = `bmi-menu-${useId()}`;

const inputEl = ref(null);
const isOpen = ref(false);
const index = ref(-1);
const rect = ref(null);

const matches = computed(() => {
  const key = norm(props.modelValue);
  const options = store.baseModelCompletions;
  // Prefix matches ahead of substring ones, each already sorted by the server,
  // so typing narrows predictably instead of reshuffling.
  const starts = options.filter((o) => norm(o).startsWith(key));
  const contains = key
    ? options.filter((o) => norm(o).includes(key) && !norm(o).startsWith(key))
    : [];
  const hits = [...starts, ...contains].slice(0, MAX_SHOWN);
  // Nothing left to complete: the field already says exactly the one thing on
  // offer, so a menu would only cover the row under it.
  if (hits.length === 1 && norm(hits[0]) === key && key) return [];
  return hits;
});

/** The one condition the menu, `aria-expanded` and the key ladder all read. */
const menuShown = computed(() =>
  Boolean(isOpen.value && matches.value.length && rect.value),
);

watch(
  [isOpen, matches],
  () => {
    if (!isOpen.value || !matches.value.length) {
      rect.value = null;
      return;
    }
    nextTick(() => {
      rect.value = inputEl.value?.getBoundingClientRect() || null;
    });
  },
  { immediate: true },
);

/**
 * A scroll anywhere closes the menu.
 *
 * It is positioned `fixed` from one measurement, and the shelf's row list
 * scrolls under it: without this the menu stays parked over whichever rows
 * scrolled into its place, still clickable, still writing to a row that is no
 * longer there. Closing beats re-measuring - the field the menu belongs to has
 * moved too, and following it is animation nobody asked for.
 */
function onOutsideScroll() {
  if (isOpen.value) close();
}
onMounted(() => {
  window.addEventListener("scroll", onOutsideScroll, true);
  window.addEventListener("resize", onOutsideScroll);
});
onBeforeUnmount(() => {
  window.removeEventListener("scroll", onOutsideScroll, true);
  window.removeEventListener("resize", onOutsideScroll);
});

function open() {
  store.loadBaseModelCompletions();
  isOpen.value = true;
}

function close() {
  isOpen.value = false;
  index.value = -1;
}

function onInput(event) {
  index.value = -1;
  open();
  emit("update:modelValue", event.target.value);
}

function onBlur() {
  close();
}

/** Fill the field from the list. Never commits: a bulk write is a click away. */
function choose(item) {
  emit("update:modelValue", item);
  close();
}

function onKeydown(event) {
  const list = matches.value;
  if (event.key === "ArrowDown") {
    // The one gesture that OPENS the menu: with it shut, the first press asks
    // for it rather than moving a highlight nobody can see.
    if (!menuShown.value) {
      event.preventDefault();
      event.stopPropagation();
      open();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    index.value = Math.min(index.value + 1, list.length - 1);
  } else if (event.key === "ArrowUp" && menuShown.value) {
    event.preventDefault();
    event.stopPropagation();
    index.value = Math.max(index.value - 1, -1);
  } else if (event.key === "Tab" && !event.shiftKey && menuShown.value) {
    event.preventDefault();
    event.stopPropagation();
    choose(list[index.value >= 0 ? index.value : 0]);
  } else if (event.key === "Enter") {
    event.preventDefault();
    event.stopPropagation();
    if (menuShown.value && index.value >= 0 && list[index.value]) {
      emit("update:modelValue", list[index.value]);
    }
    close();
    // After the value the highlight chose, so the parent writes what the field
    // now shows rather than what was typed before Arrow was pressed.
    nextTick(() => emit("confirm"));
  } else if (event.key === "Escape") {
    // The first Escape only takes the menu back; it does not throw the edit
    // away, and it is left to bubble once there is no menu to close.
    if (menuShown.value) {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    emit("cancel");
  }
}

defineExpose({
  /** Focus only, no select-all: its sibling field in the same dialog does the
   *  same, and a seeded value that vanishes on the first keystroke is not what
   *  a correction wants. The shelf's inline editor selects on its own, where
   *  retyping IS the common case. */
  focus() {
    inputEl.value?.focus();
  },
});
</script>

<style scoped>
/* The tag field's treatment, repeated on purpose: two completion menus that
   looked different would be two things to learn. The values below are copied
   from `OverlayTagsPanel.vue` along with the reasons they are off-token. */
.bmi-menu {
  position: fixed;
  z-index: 9999;
  max-height: 240px;
  overflow-y: auto;
  background: color-mix(in srgb, rgb(var(--v-theme-shadow)) 85%, transparent);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  border-radius: 6px; /* no clean token: 6px equidistant between --radius-sm(4px) and --radius-md(8px) */
  box-shadow: var(--elevation-3);
  display: flex;
  flex-direction: column;
}

.bmi-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 5px 10px; /* no clean token: 5px is between --space-2(4px) and --space-3(8px); 10px between --space-3(8px) and --space-4(12px) */
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-on-dark-surface));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bmi-item:hover,
.bmi-item--active {
  background: rgba(var(--v-theme-primary), 0.22);
}

.bmi-hint {
  display: inline-block;
  margin-left: var(--space-3);
  padding: 0 var(--space-2);
  font-size: 0.55rem; /* no token: ~7.7px, well below --text-2xs=11px */
  font-weight: var(--weight-semibold);
  letter-spacing: 0.04em;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.15);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  vertical-align: middle;
  line-height: 1.5;
}
</style>
