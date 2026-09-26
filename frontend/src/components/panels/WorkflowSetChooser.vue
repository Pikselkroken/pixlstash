<template>
  <!-- A popup, not a dialog: the design puts it beside the ＋ tile or the fill
       button that opened it, and it holds one short job. v-menu returns focus to
       nothing we can rely on (the tray opens it programmatically), so `close`
       carries the focus back through the grid instead. -->
  <v-menu
    :model-value="open"
    :target="target"
    :close-on-content-click="false"
    location="bottom start"
    origin="top start"
    :offset="4"
    @update:model-value="(value) => !value && emit('close')"
  >
    <div
      class="tbm wsc"
      role="dialog"
      :aria-label="title"
      data-testid="workflow-set-chooser"
      @keydown.esc.stop.prevent="emit('close')"
    >
      <div class="tbm-header">
        <span class="tbm-title">{{ title }}</span>
      </div>
      <div class="tbm-section">
        <p v-if="note" class="wsc__note">{{ note }}</p>
        <div v-if="filterable" class="tbm-input-wrap">
          <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
          <input
            ref="fieldRef"
            :value="query"
            class="tbm-input tbm-input--with-icon"
            :placeholder="placeholder"
            :aria-label="placeholder"
            autocomplete="off"
            :aria-controls="listId"
            :aria-activedescendant="activeId"
            @input="emit('update:query', $event.target.value)"
            @keydown="onKeydown"
          />
        </div>
        <div
          :id="listId"
          ref="listRef"
          class="wsc__list"
          role="listbox"
          :aria-label="title"
          :aria-multiselectable="pick ? 'false' : 'true'"
          :tabindex="filterable ? -1 : 0"
          :aria-activedescendant="filterable ? undefined : activeId"
          @keydown="onKeydown"
        >
          <template v-for="section in shownSections" :key="section.id">
            <div
              v-if="section.label"
              class="section-label wsc__head"
              role="presentation"
            >
              {{ section.label }}
              <!-- The section's real size, not the rows drawn before "Show
                   all": a section of twenty cut to eight must say twenty. -->
              <!-- "none yet" says the group is empty; under a filter that is
                   not the claim, so it says nothing matched instead. -->
              <span class="wsc__count num">{{
                section.items.length + section.more ||
                (query.trim() ? "no matches" : "none yet")
              }}</span>
            </div>
            <div
              v-for="item in section.items"
              :id="optionId(item)"
              :key="item.id"
              class="wsc__item"
              :class="{ 'wsc__item--active': item.id === activeKey }"
              role="option"
              :aria-selected="pick ? undefined : String(checked.has(item.id))"
              @click="choose(item.id)"
              @mouseenter="activeKey = item.id"
            >
              <v-icon class="wsc__box" aria-hidden="true">{{
                pick
                  ? "mdi-plus"
                  : checked.has(item.id)
                    ? "mdi-checkbox-marked-outline"
                    : "mdi-checkbox-blank-outline"
              }}</v-icon>
              <span class="wsc__name">{{ item.name }}</span>
              <span v-if="item.detail" class="wsc__detail">{{
                item.detail
              }}</span>
            </div>
            <button
              v-if="section.more"
              type="button"
              class="tbm-ghost wsc__more"
              tabindex="-1"
              @click="expanded = new Set([...expanded, section.id])"
            >
              Show all {{ section.items.length + section.more }}
            </button>
          </template>
          <p v-if="!items.length" class="wsc__empty">{{ emptyText }}</p>
        </div>
      </div>
      <div class="tbm-footer wsc__footer">
        <span class="wsc__hint">{{
          pick
            ? "Click a model to add it"
            : "Adds only · nothing already in the set"
        }}</span>
        <span class="tbm-spacer"></span>
        <AppButton variant="ghost" size="sm" @click="emit('close')">{{
          pick ? "Done" : "Cancel"
        }}</AppButton>
        <AppButton
          v-if="!pick"
          variant="primary"
          size="sm"
          :disabled="!checked.size"
          @click="submit"
          >{{ addLabel }}</AppButton
        >
      </div>
    </div>
  </v-menu>
</template>

<script setup>
/**
 * The one chooser a hand-made workflow set is filled through (#1520).
 *
 * Three callers, two modes. A slot's ＋ tile is `pick`: ranked sections, type to
 * filter, and ONE click (or Enter, or Space) adds the model under it - no tick
 * and no Add button, because choosing a LoRA is one decision, not two. The
 * caller decides whether the popup stays open for the next one. **Fill from
 * pictures** and **Fill from a set** are checklists that arrive pre-ticked, so
 * their one click is Add. All of it only ADDS - nothing here takes a model out
 * of a set - which is why the footer says so.
 *
 * Keyboard: the arrows walk the options across sections, Enter adds the one
 * under the cursor (in a checklist, what is ticked, else that one), Space ticks
 * in a checklist, Escape closes.
 */
import { computed, nextTick, ref, useId, watch } from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";

/** How many rows a section draws before "Show all" is asked for. */
const SECTION_DEPTH = 8;

const props = defineProps({
  open: { type: Boolean, default: false },
  /** What v-menu anchors to: an element or `[x, y]`. */
  target: { type: [Object, Array], default: undefined },
  title: { type: String, default: "" },
  /** A line under the title, for what the list is drawn from. */
  note: { type: String, default: "" },
  /** `[{id, label, items: [{id, name, detail?, checked?}]}]`. */
  sections: { type: Array, default: () => [] },
  filterable: { type: Boolean, default: false },
  query: { type: String, default: "" },
  placeholder: { type: String, default: "Filter…" },
  /** One click adds the model under it; no ticks, no Add button. */
  pick: { type: Boolean, default: false },
  emptyText: { type: String, default: "Nothing to add." },
});

const emit = defineEmits(["close", "add", "update:query"]);

const listId = `wsc-${useId()}`;
const fieldRef = ref(null);
const listRef = ref(null);
const checked = ref(new Set());
const activeKey = ref(null);
/** Sections the reader asked to see whole. */
const expanded = ref(new Set());

/**
 * The sections as drawn: each cut to `SECTION_DEPTH` until its "Show all" is
 * pressed, with a filter showing everything that matches.
 */
const shownSections = computed(() =>
  props.sections.map((section) => {
    const whole =
      props.query.trim() ||
      expanded.value.has(section.id) ||
      section.items.length <= SECTION_DEPTH;
    return {
      ...section,
      items: whole ? section.items : section.items.slice(0, SECTION_DEPTH),
      more: whole ? 0 : section.items.length - SECTION_DEPTH,
    };
  }),
);

/** Every drawn option in order, which is what the arrows walk. */
const items = computed(() => shownSections.value.flatMap((s) => s.items));

const activeId = computed(() => {
  const item = items.value.find((entry) => entry.id === activeKey.value);
  return item ? optionId(item) : undefined;
});

function optionId(item) {
  return `${listId}-${item.id}`;
}

const addLabel = computed(() =>
  checked.value.size ? `Add ${checked.value.size}` : "Add",
);

// A fresh open starts from the callers' pre-ticks and puts the cursor first,
// with the focus in the field (or the list when there is no field).
watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    expanded.value = new Set();
    checked.value = new Set(
      props.sections
        .flatMap((s) => s.items)
        .filter((item) => item.checked)
        .map((item) => item.id),
    );
    activeKey.value = items.value[0]?.id ?? null;
    await nextTick();
    // v-menu mounts its content a tick after it opens.
    setTimeout(() => (fieldRef.value ?? listRef.value)?.focus(), 0);
  },
  { immediate: true },
);

// A filter keystroke can take the cursor's option away; keep it on a real one.
watch(items, (list) => {
  if (!list.some((item) => item.id === activeKey.value)) {
    activeKey.value = list[0]?.id ?? null;
  }
});

/** A click on an option: add it outright in `pick` mode, else tick it. */
function choose(id) {
  if (props.pick) {
    activeKey.value = id;
    emit("add", [id]);
    return;
  }
  toggle(id);
}

function toggle(id) {
  const next = new Set(checked.value);
  if (checked.value.has(id)) next.delete(id);
  else next.add(id);
  checked.value = next;
  activeKey.value = id;
}

function move(step) {
  const list = items.value;
  if (!list.length) return;
  const at = list.findIndex((item) => item.id === activeKey.value);
  const next = Math.min(Math.max(at + step, 0), list.length - 1);
  activeKey.value = list[next].id;
  nextTick(() =>
    document
      .getElementById(optionId(list[next]))
      ?.scrollIntoView?.({ block: "nearest" }),
  );
}

function submit() {
  let ids = props.pick ? [] : [...checked.value];
  if (!ids.length && activeKey.value != null) ids = [activeKey.value];
  if (ids.length) emit("add", ids);
}

function onKeydown(event) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    move(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    move(-1);
  } else if (event.key === " " && event.target === listRef.value) {
    event.preventDefault();
    if (activeKey.value != null) choose(activeKey.value);
  } else if (event.key === "Enter") {
    event.preventDefault();
    submit();
  }
}

defineExpose({ expand: (id) => expanded.value.add(id) });
</script>

<style scoped>
/* A step wider than the filter menu, because its title carries a set's name
   and its rows a model name beside a detail: the small-dialog width, still
   capped by the window. `box-sizing` so the border sits inside the cap. */
.wsc {
  box-sizing: border-box;
  width: min(var(--dialog-w-sm), calc(100vw - var(--space-6) * 2));
}

/* Nothing in here may run out of the popup, whatever it is asked to name.
   `.tbm-title` is `nowrap` for one-word menu titles; this one names a set, so
   it wraps - at a word where it can, mid-word where a filename leaves none. */
.wsc .tbm-title {
  min-width: 0;
  white-space: normal;
  overflow-wrap: anywhere;
}

.wsc__note {
  overflow-wrap: anywhere;
}

.wsc__note {
  margin: 0 0 var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The filter checklist's own list height (`FilterChecklistMenu`'s `.fm-list`). */
.wsc__list {
  max-height: 320px;
  overscroll-behavior: contain;
  margin-top: var(--space-3);
  overflow-y: auto;
}

.wsc__list:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring-inset);
}

.wsc__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-2) var(--space-1);
}

.wsc__count {
  letter-spacing: normal;
  text-transform: none;
}

.wsc__item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-h-sm);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  cursor: pointer;
}

.wsc__item:hover,
.wsc__item--active {
  background: var(--hover-wash);
}

.wsc__item--active {
  box-shadow: var(--focus-ring-inset);
}

.wsc__box {
  flex-shrink: 0;
  font-size: var(--text-md);
}

.wsc__item[aria-selected="true"] .wsc__box {
  color: var(--selected-ink);
}

.wsc__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wsc__detail {
  flex-shrink: 0;
  max-width: 45%;
  overflow: hidden;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wsc__more {
  margin: var(--space-1) var(--space-2);
}

.wsc__empty {
  margin: 0;
  padding: var(--space-3) var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wsc__footer {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.wsc__hint {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
