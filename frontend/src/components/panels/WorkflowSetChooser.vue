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
      :class="{ 'wsc--check': !pick }"
      role="dialog"
      :aria-label="label"
      data-testid="workflow-set-chooser"
      @keydown.esc.stop.prevent="emit('close')"
    >
      <!-- No title on a slot's picker: it hangs off the ＋ tile inside the tray
           that already names the set, so the set and the slot live in the
           accessible name and the placeholder (#1574). A Fill checklist is a
           bulk action and does get one. -->
      <div v-if="heading" class="wsc__hd">
        <span class="wsc__hd-title">{{ heading }}</span>
        <span v-if="subheading" class="wsc__hd-sub">{{ subheading }}</span>
      </div>
      <div class="wsc__fs">
        <div class="tbm-input-wrap">
          <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
          <input
            ref="fieldRef"
            :value="query"
            class="tbm-input tbm-input--with-icon"
            :placeholder="placeholder"
            :aria-label="placeholder"
            autocomplete="off"
            role="combobox"
            aria-expanded="true"
            :aria-controls="listId"
            :aria-activedescendant="targetId"
            @input="onInput"
            @keydown="onKeydown"
          />
        </div>
      </div>
      <p v-if="note" class="wsc__note">{{ note }}</p>
      <div
        :id="listId"
        class="wsc__list"
        role="listbox"
        :aria-label="label"
        :aria-multiselectable="pick ? 'false' : 'true'"
      >
        <template v-for="section in shownSections" :key="section.id">
          <!-- What the filter found that the set already holds: not a choice,
               but said, so a search for it does not look broken. -->
          <p v-if="section.held" class="wsc__gone">
            <v-icon size="14" aria-hidden="true">mdi-check</v-icon>
            {{ heldText(section.items) }}
          </p>
          <template v-else>
            <div
              v-if="section.label && !section.cut"
              class="wsc__gh"
              role="presentation"
            >
              <span class="wsc__gh-label">{{ section.label }}</span>
              <!-- The section's real size, not the rows drawn before "Show
                   more": a section of twenty cut to eight must say twenty. -->
              <span class="fm-n">{{ section.total }}</span>
            </div>
            <div
              v-for="item in section.items"
              :id="optionId(item)"
              :key="item.id"
              class="wsc__r"
              :class="{
                'wsc__r--cur': item.id === activeKey,
                'wsc__r--enter': pick && item.id === targetKey,
              }"
              role="option"
              :aria-selected="pick ? undefined : String(checked.has(item.id))"
              @mousedown.prevent
              @click="choose(item.id)"
            >
              <v-icon v-if="!pick" class="wsc__box" aria-hidden="true">{{
                checked.has(item.id)
                  ? "mdi-checkbox-marked-outline"
                  : "mdi-checkbox-blank-outline"
              }}</v-icon>
              <ModelMark :row="item.row" />
              <span class="wsc__two">
                <span class="wsc__nm" :class="`wsc__nm--${item.name.state}`">{{
                  item.name.text || item.row.filename
                }}</span>
                <span v-if="item.row.filename" class="wsc__fn"
                  >{{ item.file[0]
                  }}<mark v-if="item.file[1]">{{ item.file[1] }}</mark
                  >{{ item.file[2] }}</span
                >
              </span>
              <span v-if="item.quant" class="wsc__qc">{{ item.quant }}</span>
              <span v-else></span>
              <span class="wsc__detail">{{ item.detail }}</span>
              <!-- What Enter would add, where the size was: says which row
                   the key acts on without making it look pressed. -->
              <span
                v-if="pick && item.id === targetKey"
                class="fm-kbd wsc__enter"
                aria-hidden="true"
                >⏎</span
              >
            </div>
            <button
              v-if="section.more"
              type="button"
              class="wsc__more"
              tabindex="-1"
              @mousedown.prevent
              @click="expand(section.id)"
            >
              <v-icon size="16" aria-hidden="true">mdi-chevron-down</v-icon>
              {{
                section.cut ? section.collapsed : `Show ${section.more} more`
              }}
            </button>
          </template>
        </template>
        <p
          v-if="!items.length && !collapsedLeft && !heldOnly"
          class="wsc__empty"
        >
          {{ query.trim() ? `Nothing matches "${query.trim()}".` : emptyText }}
        </p>
      </div>
      <!-- Pick mode's confirmation: one status line, replaced on each add and
           never stacked, that appears only once something was added. The live
           region is always there so the first add is announced. -->
      <div v-if="pick" role="status" class="wsc__status">
        <div v-if="status" class="tbm-footer wsc__foot">
          <v-icon
            v-if="status.undo"
            size="16"
            class="wsc__ok"
            aria-hidden="true"
            >mdi-check</v-icon
          >
          <span class="wsc__foot-text">{{ status.text }}</span>
          <span class="tbm-spacer"></span>
          <AppButton
            v-if="status.undo"
            variant="ghost"
            size="sm"
            icon-left="undo"
            @click="emit('undo')"
            >Undo</AppButton
          >
        </div>
      </div>
      <div v-else class="tbm-footer wsc__foot">
        <span class="tbm-spacer"></span>
        <AppButton variant="ghost" size="sm" @click="emit('close')"
          >Cancel</AppButton
        >
        <AppButton
          variant="primary"
          size="sm"
          :disabled="!checkedShown.length"
          @click="submit"
          >{{ addLabel }}</AppButton
        >
      </div>
    </div>
  </v-menu>
</template>

<script setup>
/**
 * The one chooser a hand-made workflow set is filled through (#1520, #1574).
 *
 * Three callers, two modes, one row. A slot's ＋ tile is `pick`: ranked groups,
 * type to filter, and ONE click (or Enter) adds the model under it. The caller
 * decides whether the popup stays open for the next one, and narrates each add
 * through `status`. **Fill from pictures** and **Fill from a set** are
 * checklists that arrive pre-ticked, so their one click is Add. All of it only
 * ADDS - nothing here takes a model out of a set.
 *
 * Every row is the shelf's row in miniature: `ModelMark`, the resolved name in
 * the shelf's weight for its state, the filename on a second line, then the
 * quant chip and a right column (the size, or the evidence in a checklist).
 *
 * Keyboard: focus stays in the filter. Typing filters, the arrows move the
 * cursor (the inset ring), Enter adds the cursor row - or the top match while
 * the arrows are unused, which is what the ⏎ marks. In a checklist Space ticks
 * the cursor row once the arrows have put one down. Escape closes. The pointer
 * only washes a row; it never moves the cursor.
 */
import { computed, nextTick, ref, useId, watch } from "vue";
import { VIcon } from "vuetify/components";

import { modelName, quantBadge } from "../../utils/modelShelf";
import AppButton from "../widgets/AppButton.vue";
import ModelMark from "../widgets/ModelMark.vue";

/** How many rows a section draws before "Show more" is asked for. */
const SECTION_DEPTH = 8;

const props = defineProps({
  open: { type: Boolean, default: false },
  /** What v-menu anchors to: an element or `[x, y]`. */
  target: { type: [Object, Array], default: undefined },
  /** The accessible name: "Add LoRA to Portrait kit". */
  label: { type: String, default: "" },
  /** A checklist's title, and the line beside it. */
  heading: { type: String, default: "" },
  subheading: { type: String, default: "" },
  /** A line under the field, for why the list is not ranked. */
  note: { type: String, default: "" },
  /**
   * `[{id, label, total?, items: [{id, row, detail?, checked?}], collapsed?,
   * held?}]`. `row` is the shelf row. `collapsed` is the text a section starts
   * folded behind; `held` sections list matches the set already holds.
   */
  sections: { type: Array, default: () => [] },
  query: { type: String, default: "" },
  placeholder: { type: String, default: "Filter…" },
  /** One click adds the model under it; no ticks, no Add button. */
  pick: { type: Boolean, default: false },
  emptyText: { type: String, default: "Nothing to add." },
  /** Pick mode's status line: `{text, undo}`, or null before the first add. */
  status: { type: Object, default: null },
});

const emit = defineEmits(["close", "add", "update:query", "undo"]);

const listId = `wsc-${useId()}`;
const fieldRef = ref(null);
const checked = ref(new Set());
/** The keyboard cursor; null until an arrow puts it down. */
const activeKey = ref(null);
/** Sections the reader asked to see whole. */
const expanded = ref(new Set());

/** A row as drawn: the shelf's name, the filename split at the match. */
function drawn(item) {
  const row = item.row ?? {};
  const filename = String(row.filename ?? "");
  const needle = props.query.trim().toLowerCase();
  const at = needle ? filename.toLowerCase().indexOf(needle) : -1;
  return {
    ...item,
    row,
    name: modelName(row),
    quant: quantBadge(row.quant)?.label ?? "",
    file:
      at < 0
        ? [filename, "", ""]
        : [
            filename.slice(0, at),
            filename.slice(at, at + needle.length),
            filename.slice(at + needle.length),
          ],
  };
}

/**
 * The sections as drawn: empty ones skipped, each cut to `SECTION_DEPTH` until
 * its "Show more" is pressed, a `collapsed` one folded to its one line, and a
 * filter showing everything that matches.
 */
const shownSections = computed(() => {
  const filled = props.sections.filter((section) => section.items.length);
  // Folded only below something: a list whose one group is the folded one
  // would open as a single line to click.
  const above = filled.some((s) => !s.collapsed && !s.held);
  return filled.map((section) => {
    const total = section.total ?? section.items.length;
    const open = Boolean(props.query.trim()) || expanded.value.has(section.id);
    if (section.collapsed && above && !open) {
      return { ...section, total, items: [], more: total, cut: true };
    }
    const whole = open || section.items.length <= SECTION_DEPTH;
    return {
      ...section,
      total,
      items: (whole
        ? section.items
        : section.items.slice(0, SECTION_DEPTH)
      ).map(drawn),
      more: whole ? 0 : section.items.length - SECTION_DEPTH,
      cut: false,
    };
  });
});

/** Every drawn option in order, which is what the arrows walk. */
const items = computed(() =>
  shownSections.value.filter((s) => !s.held).flatMap((s) => s.items),
);

/** The filter found models the set already holds, which the list names. */
const heldOnly = computed(() => shownSections.value.some((s) => s.held));

/** A folded section still waiting below the last row. */
const collapsedLeft = computed(() => shownSections.value.some((s) => s.cut));

/**
 * The row Enter acts on in `pick` mode: the cursor, else the top match. A
 * checklist's Enter adds what is ticked, so there it is only the cursor.
 */
const targetKey = computed(() =>
  props.pick
    ? (activeKey.value ?? items.value[0]?.id ?? null)
    : activeKey.value,
);

/** Ticked rows the filter still shows: what Add adds. */
const checkedShown = computed(() =>
  items.value.filter((item) => checked.value.has(item.id)).map((i) => i.id),
);

const targetId = computed(() => {
  const item = items.value.find((entry) => entry.id === targetKey.value);
  return item ? optionId(item) : undefined;
});

function optionId(item) {
  return `${listId}-${item.id}`;
}

function heldText(rows) {
  if (rows.length === 1) {
    const { text } = modelName(rows[0]);
    return `${text || rows[0].filename} also matches, and is already in this set.`;
  }
  const lead = items.value.length
    ? `${rows.length} more match`
    : `${rows.length} match`;
  return `${lead}, and are already in this set.`;
}

const addLabel = computed(() =>
  checkedShown.value.length ? `Add ${checkedShown.value.length}` : "Add",
);

// A fresh open starts from the callers' pre-ticks with no cursor down, and
// focus in the field.
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
    activeKey.value = null;
    // v-menu mounts its teleported content asynchronously, and how long that
    // takes is not ours to know: keep looking for a few frames rather than
    // betting on one tick and leaving focus on <body> when it loses.
    for (let tries = 0; tries < 10 && props.open; tries += 1) {
      await nextTick();
      if (fieldRef.value) {
        fieldRef.value.focus();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 16));
    }
  },
  { immediate: true },
);

// An add takes the cursor's row out of the list: the cursor stays at the same
// place, on the row that moved up into it, so the next Enter adds the next one.
watch(items, (list, before) => {
  if (activeKey.value == null) return;
  if (list.some((item) => item.id === activeKey.value)) return;
  const at = (before ?? []).findIndex((item) => item.id === activeKey.value);
  activeKey.value = list.length
    ? list[Math.min(Math.max(at, 0), list.length - 1)].id
    : null;
});

function onInput(event) {
  // Typing starts over from the top match.
  activeKey.value = null;
  emit("update:query", event.target.value);
}

function expand(id) {
  expanded.value = new Set([...expanded.value, id]);
}

/** A click on an option: add it outright in `pick` mode, else tick it. */
function choose(id) {
  if (props.pick) emit("add", [id]);
  else toggle(id);
}

function toggle(id) {
  const next = new Set(checked.value);
  if (checked.value.has(id)) next.delete(id);
  else next.add(id);
  checked.value = next;
}

function move(step) {
  // Down off the last drawn row of a cut section reveals the rest of it, and
  // down off the very last row unfolds a folded one: the keyboard's "Show
  // more", since the button itself is not a stop and Enter belongs to adding.
  if (step > 0 && activeKey.value != null) {
    const cut = shownSections.value.find(
      (section) => section.more && section.items.at(-1)?.id === activeKey.value,
    );
    if (cut) expand(cut.id);
    else if (items.value.at(-1)?.id === activeKey.value) {
      const folded = shownSections.value.find((section) => section.cut);
      if (folded) expand(folded.id);
    }
  }
  const list = items.value;
  if (!list.length) return;
  const at = list.findIndex((item) => item.id === activeKey.value);
  const next = at < 0 ? 0 : Math.min(Math.max(at + step, 0), list.length - 1);
  activeKey.value = list[next].id;
  nextTick(() =>
    document
      .getElementById(optionId(list[next]))
      ?.scrollIntoView?.({ block: "nearest" }),
  );
}

function submit() {
  // A checklist adds what is ticked and nothing else: its cursor row is only
  // where Space ticks.
  let ids = props.pick ? [] : [...checkedShown.value];
  if (!ids.length && props.pick && targetKey.value != null) {
    ids = [targetKey.value];
  }
  if (ids.length) emit("add", ids);
}

function onKeydown(event) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    move(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    move(-1);
  } else if (event.key === " " && !props.pick && activeKey.value != null) {
    // Space ticks once the arrows have put a cursor down; before that it is a
    // space in the filter, since names have them.
    event.preventDefault();
    toggle(activeKey.value);
  } else if (event.key === "Enter") {
    event.preventDefault();
    submit();
  }
}

defineExpose({ expand });
</script>

<style scoped>
/* The small-dialog width, still capped by the window. `box-sizing` so the
   border sits inside the cap. */
.wsc {
  box-sizing: border-box;
  width: min(var(--dialog-w-sm), calc(100vw - var(--space-6) * 2));
}

.wsc__hd {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  min-width: 0;
  padding: var(--space-4) var(--space-4) 0;
}

.wsc__hd-title {
  flex: none;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.wsc__hd-sub {
  min-width: 0;
  overflow: hidden;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wsc__fs {
  padding: var(--space-4) var(--space-4) var(--space-3);
}

.wsc__note {
  margin: 0;
  padding: 0 var(--space-4) var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  overflow-wrap: anywhere;
}

/* About eight 40px rows before it scrolls. */
.wsc__list {
  max-height: calc((var(--control-h-bar) + var(--space-3)) * 9);
  overscroll-behavior: contain;
  overflow-y: auto;
  padding: 0 var(--space-2) var(--space-3);
}

/* Group label as a rule, its count in its own right-hand column in the
   filter menus' regular-weight `.fm-n`, so it is not read as part of the word
   before it. */
.wsc__gh {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-3) var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.wsc__gh-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Fixed columns - mark, name over filename, quant, right column - so every
   row lines up with the next. */
.wsc__r {
  --wsc-right: calc(var(--space-8) + var(--space-2));
  position: relative;
  display: grid;
  grid-template-columns: var(--entity-thumb) minmax(0, 1fr) auto var(
      --wsc-right
    );
  align-items: center;
  column-gap: var(--space-3);
  height: calc(var(--control-h-bar) + var(--space-3));
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.wsc--check .wsc__r {
  --wsc-right: calc(var(--space-9) + var(--space-5) + var(--space-2));
  grid-template-columns:
    var(--text-md) var(--entity-thumb) minmax(0, 1fr) auto
    var(--wsc-right);
}

/* Pointer: the wash. Keyboard cursor: the wash and the inset ink ring, the
   filter checklist's `.fm-check--kbd`. The two never look alike. */
.wsc__r:hover,
.wsc__r--cur {
  background: var(--hover-wash);
}

.wsc__r--cur {
  box-shadow: var(--focus-ring-inset);
}

.wsc__box {
  font-size: var(--text-md);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

/* Ticked: an olive box and no fill. */
.wsc__r[aria-selected="true"] .wsc__box {
  color: var(--selected-ink);
}

.wsc__two {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: var(--leading-tight);
}

/* The shelf row's weights for each name state (`.shelf-row-name--*`). */
.wsc__nm {
  overflow: hidden;
  font-weight: var(--weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wsc__nm--derived {
  font-weight: var(--weight-regular);
}

.wsc__nm--from-file,
.wsc__nm--needs-a-name {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: var(--weight-regular);
}

/* The file on disk: what tells near-identical rows apart, and what somebody
   typing the filename is matching. */
.wsc__fn {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wsc__fn mark {
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-accent), 0.35);
  color: inherit;
}

/* The shelf's quant chip (`.shelf-chip--quant`). */
.wsc__qc {
  display: inline-flex;
  align-items: center;
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.wsc__detail {
  overflow: hidden;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wsc__r--enter .wsc__detail {
  visibility: hidden;
}

.wsc__enter {
  position: absolute;
  top: 50%;
  right: var(--space-3);
  transform: translateY(-50%);
}

.wsc__more {
  display: flex;
  border: 0;
  background: none;
  font: inherit;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  height: var(--control-h-bar);
  padding: 0 var(--space-3) 0 calc(var(--entity-thumb) + var(--space-5));
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  text-align: left;
  cursor: pointer;
}

.wsc__more:hover {
  background: var(--hover-wash);
}

.wsc__gone,
.wsc__empty {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.wsc__foot-text {
  min-width: 0;
  overflow: hidden;
  color: rgb(var(--v-theme-on-panel));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wsc__ok {
  flex: none;
  color: rgb(var(--v-theme-success));
}
</style>
