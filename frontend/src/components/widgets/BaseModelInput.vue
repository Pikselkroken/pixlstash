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
      menuShown && shown >= 0 ? `${menuId}-${shown}` : undefined
    "
    @input="onInput"
    @keydown="onKeydown"
    @focus="onFocus"
    @blur="onBlur"
  />
  <!-- A layer on the overlay stack and fixed, like the tag panel's list,
       because both places this field lands in clip: a dialog body scrolls and a
       shelf row hides its overflow, so an absolutely positioned menu would be
       cut off in one and cropped in the other. The stack also puts it above the
       dialog, which no z-index can promise. -->
  <StackLayer :open="menuShown">
    <div
      v-if="menuShown"
      :id="menuId"
      class="bmi-menu"
      :style="{
        top: `${rect.bottom + 4}px`,
        left: `${rect.left}px`,
        minWidth: `${Math.max(rect.width, 160)}px`,
        maxWidth: `${Math.max(rect.width, 360)}px`,
      }"
      role="listbox"
      :aria-label="listLabel"
    >
      <div v-if="hint" class="bmi-hint-line" aria-hidden="true">{{ hint }}</div>
      <template v-for="(item, idx) in matches" :key="`${item.section}:${item.value}`">
        <div
          v-if="item.section && item.section !== matches[idx - 1]?.section"
          class="bmi-section"
          aria-hidden="true"
        >
          {{ item.section }}
        </div>
        <button
          :id="`${menuId}-${idx}`"
          type="button"
          tabindex="-1"
          class="bmi-item"
          :class="{
            'bmi-item--active': idx === shown,
            'bmi-item--own': item.removable,
            'bmi-use': item.use,
          }"
          role="option"
          :aria-selected="idx === shown"
          :aria-label="item.use ? `Use your own: ${item.value}` : undefined"
          :title="item.removable ? item.value : undefined"
          @mousedown.prevent="choose(item)"
        >
          {{ item.use ? "" : item.value }}
          <template v-if="item.use">
            <v-icon size="14" aria-hidden="true">mdi-pencil-outline</v-icon>
            Use “{{ item.value }}”
            <span class="bmi-detail">Adds your own line</span>
          </template>
          <span v-if="item.detail" class="bmi-detail">{{ item.detail }}</span>
          <!-- Hidden from the accessible name: it is a hint about the keyboard,
               and read out it turns every first option into "SDXL 1.0 TAB". -->
          <span
            v-if="!openOnFocus && idx === (index >= 0 ? index : 0)"
            class="bmi-hint"
            aria-hidden="true"
            >TAB</span
          >
          <!-- A pointer's way to forget an entry. Not a control of its own: a
               button inside an option is not valid ARIA, and Delete on the
               highlighted entry is the keyboard and assistive route. -->
          <span
            v-if="item.removable"
            class="bmi-remove"
            aria-hidden="true"
            @mousedown.prevent.stop="emit('remove', item.value)"
          >
            <v-icon size="14">mdi-close</v-icon>
          </span>
        </button>
      </template>
      <button
        v-if="footShown"
        :id="`${menuId}-${matches.length}`"
        type="button"
        tabindex="-1"
        class="bmi-item bmi-foot"
        :class="{ 'bmi-item--active': shown === matches.length }"
        role="option"
        :aria-selected="shown === matches.length"
        @mousedown.prevent="footAct"
      >
        {{ footAction }}
      </button>
    </div>
  </StackLayer>
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
 * two it had no answer for. The old lightbox tag field's list had no open state:
 * it was simply visible whenever the input held a prefix that matched, inside a
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
 *
 * **A caller styles the field with `:deep()`.** The template has two roots (the
 * input and the menu's layer), so Vue gives a scoped parent's attribute to
 * neither, and a plain `.my-class` rule in the caller's scoped style never
 * matches. The class still lands on the input through `$attrs`; write
 * `.wrapper :deep(.my-class)`, or use a global class like `tbm-input`.
 *
 * **`options` makes it any completing field.** Given a list, it completes
 * against that instead of the base models (the sheet step's recent clothes and
 * backgrounds, through `RecentInput`); `listLabel` names the menu for it.
 *
 * **The prompt assistant's rows add four opt-ins** (`PromptAssistGroup`).
 * `openOnFocus` opens the full list on focus, filtering only once something is
 * typed; those rows are never focused on their own, so the rule above does not
 * bite, and Tab there leaves the field rather than filling it. An option may be
 * `{ value, detail, section, removable }`: a second line, a heading over each
 * run of one `section`, and on a `removable` one an (x) and Delete on its
 * highlight, both emitting `remove`. `footAction` adds a last row, in the arrow
 * order, under a list holding a removable option; it emits `foot-action`.
 * Picking an option emits `pick` as well as the value. `ownText` says plainly
 * that typing is not only a filter: typed text no option equals is offered
 * first, as Use "...", and highlighted, so Enter takes it; typed text equal to
 * an option highlights that option instead. `hint` is a line atop the list.
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

import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import StackLayer from "./StackLayer.vue";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  modelValue: { type: String, default: "" },
  // What to complete against; null is this machine's base models.
  options: { type: Array, default: null },
  listLabel: { type: String, default: "Base model suggestions" },
  openOnFocus: { type: Boolean, default: false },
  maxShown: { type: Number, default: 8 },
  footAction: { type: String, default: "" },
  ownText: { type: Boolean, default: false },
  hint: { type: String, default: "" },
});
const emit = defineEmits([
  "update:modelValue",
  "confirm",
  "cancel",
  "pick",
  "remove",
  "foot-action",
]);

const store = useModelShelfStore();

/** Case, spacing and punctuation folded away - the server's `_norm`. */
function norm(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

/** Unique per instance, because `aria-controls` has to name one menu. */
const menuId = `bmi-menu-${useId()}`;

const inputEl = ref(null);
const isOpen = ref(false);
const index = ref(-1);
const rect = ref(null);
// Opened on focus, the list is everything until a key is typed.
const filtering = ref(true);

const matches = computed(() => {
  const key = filtering.value ? norm(props.modelValue) : "";
  const options = (props.options ?? store.baseModelCompletions).map((o) =>
    typeof o === "string" ? { value: o } : o,
  );
  const starts = (o) => norm(o.value).startsWith(key);
  // Prefix matches ahead of substring ones, each already sorted by the server,
  // so typing narrows predictably instead of reshuffling. A second line counts
  // as a substring.
  const contains = (o) =>
    !starts(o) && (norm(o.value).includes(key) || norm(o.detail).includes(key));
  const hits = [
    ...options.filter(starts),
    ...(key ? options.filter(contains) : []),
  ].slice(0, props.maxShown);
  if (props.ownText) {
    const typed = filtering.value ? String(props.modelValue || "").trim() : "";
    const exact = (o) => o.value.toLowerCase() === typed.toLowerCase();
    return typed && !hits.some(exact) ? [{ value: typed, use: true }, ...hits] : hits;
  }
  // Nothing left to complete: the field already says exactly the one thing on
  // offer, so a menu would only cover the row under it.
  if (hits.length === 1 && norm(hits[0].value) === key && key) return [];
  return hits;
});
/** The row highlighted before any arrow: Use "...", or the option typed. */
const defaultIndex = computed(() => {
  if (!props.ownText || !filtering.value) return -1;
  const typed = String(props.modelValue || "").trim().toLowerCase();
  return typed
    ? matches.value.findIndex((o) => o.use || o.value.toLowerCase() === typed)
    : -1;
});
/** The highlighted row: an arrow's, else the default. */
const shown = computed(() => (index.value >= 0 ? index.value : defaultIndex.value));
const footShown = computed(
  () => Boolean(props.footAction) && matches.value.some((o) => o.removable),
);
/** The rows the arrows walk: the options, then the foot action. */
const rowCount = computed(() => matches.value.length + (footShown.value ? 1 : 0));

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
  if (!props.options) store.loadBaseModelCompletions();
  isOpen.value = true;
}

function close() {
  isOpen.value = false;
  index.value = -1;
}

function onFocus() {
  if (!props.openOnFocus) return;
  filtering.value = false;
  open();
}

function onInput(event) {
  index.value = -1;
  filtering.value = true;
  open();
  emit("update:modelValue", event.target.value);
}

function onBlur() {
  close();
}

/** Fill the field from the list. Never commits: a bulk write is a click away. */
function choose(item) {
  emit("update:modelValue", item.value);
  emit("pick", item.value);
  close();
}

function footAct() {
  emit("foot-action");
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
    index.value = Math.min(shown.value + 1, rowCount.value - 1);
  } else if (event.key === "ArrowUp" && menuShown.value) {
    event.preventDefault();
    event.stopPropagation();
    index.value = Math.max(shown.value - 1, -1);
  } else if (
    event.key === "Delete" &&
    menuShown.value &&
    list[index.value]?.removable
  ) {
    event.preventDefault();
    event.stopPropagation();
    emit("remove", list[index.value].value);
    index.value = Math.min(index.value, rowCount.value - 2);
  } else if (
    event.key === "Tab" &&
    !event.shiftKey &&
    menuShown.value &&
    !props.openOnFocus
  ) {
    event.preventDefault();
    event.stopPropagation();
    choose(list[index.value >= 0 ? index.value : 0]);
  } else if (event.key === "Enter") {
    event.preventDefault();
    event.stopPropagation();
    if (menuShown.value && shown.value === list.length && footShown.value) {
      footAct();
      return;
    }
    if (menuShown.value && list[shown.value]) {
      emit("update:modelValue", list[shown.value].value);
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
  /** The whole list, as focusing an `openOnFocus` field shows it. */
  open() {
    inputEl.value?.focus();
    filtering.value = false;
    open();
  },
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
   from the old lightbox tag field along with the reasons they are off-token. */
.bmi-menu {
  position: fixed;
  max-height: 240px;
  overflow-y: auto;
  background: color-mix(in srgb, rgb(var(--v-theme-shadow)) 85%, transparent);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  border-radius: var(--radius-md);
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

.bmi-item:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}

/* The keyboard's current option is a selection on a dark menu: the dark-surface
   olive wash. Hover layers over it rather than replacing it. */
.bmi-item--active {
  background: rgba(var(--v-theme-dark-surface-primary), 0.2);
}

.bmi-item--active:hover {
  background: linear-gradient(
      rgba(var(--v-theme-on-dark-surface), 0.16),
      rgba(var(--v-theme-on-dark-surface), 0.16)
    )
    rgba(var(--v-theme-dark-surface-primary), 0.2);
}

.bmi-item--own {
  position: relative;
  padding-right: calc(var(--control-h-sm) + var(--space-3));
}

.bmi-detail {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: normal;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.bmi-section {
  padding: var(--space-3) var(--space-3) var(--space-2);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.bmi-section:first-child {
  border-top: 0;
}

/* The (x): seen on the row under the pointer, on the keyboard's row, and
   always where nothing hovers. */
.bmi-remove {
  position: absolute;
  top: 50%;
  right: var(--space-1);
  transform: translateY(-50%);
  display: inline-grid;
  place-items: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  border-radius: var(--radius-sm);
  visibility: hidden;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.bmi-item:hover .bmi-remove,
.bmi-item--active .bmi-remove {
  visibility: visible;
}

.bmi-remove:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
  color: rgb(var(--v-theme-on-dark-surface));
}

@media (hover: none), (pointer: coarse) {
  .bmi-remove {
    visibility: visible;
  }
}

.bmi-foot {
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.15);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.bmi-hint-line {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.bmi-hint {
  display: inline-block;
  margin-left: var(--space-3);
  padding: 0 var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.04em;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.15);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  vertical-align: middle;
  line-height: 1.5;
}
</style>
