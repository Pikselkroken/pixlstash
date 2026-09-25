<template>
  <div class="tbm fm-sub ftf" role="group" :aria-label="title">
    <div class="tbm-header">
      <span class="tbm-title">{{ title }}</span>
      <span class="tbm-spacer"></span>
      <button
        class="tbm-ghost"
        type="button"
        :disabled="!hasChips"
        @click="emit('clear')"
      >
        Clear
      </button>
    </div>
    <div class="tbm-section">
      <div class="tbm-input-wrap">
        <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
        <input
          ref="fieldRef"
          v-model="query"
          class="tbm-input tbm-input--with-icon ftf-input"
          placeholder="Add a tag…"
          aria-label="Add a tag"
          autocomplete="off"
          role="combobox"
          aria-autocomplete="list"
          :aria-expanded="suggestions.length ? 'true' : 'false'"
          :aria-controls="listId"
          :aria-activedescendant="
            suggestions[highlight] ? `${listId}-${highlight}` : undefined
          "
          :aria-describedby="`${listId}-hint`"
          @keydown.down.prevent="move(1)"
          @keydown.up.prevent="move(-1)"
          @keydown.enter.prevent="onEnter"
          @keydown.tab="onTab"
          @keydown.backspace="removeLast"
          @keydown.esc="onEsc"
        />
        <span :id="`${listId}-hint`" class="ftf-hint">
          <kbd class="fm-kbd">↵</kbd> {{ rows[0].short }} ·
          <kbd class="fm-kbd">⇧↵</kbd> {{ rows[1].short }}
        </span>
      </div>
      <div
        v-show="suggestions.length"
        :id="listId"
        class="ftf-list"
        role="listbox"
        :aria-label="`${title} suggestions`"
      >
        <div
          v-for="(item, idx) in suggestions"
          :id="`${listId}-${idx}`"
          :key="item.tag"
          class="tbm-check fm-check"
          :class="{ 'fm-check--kbd': idx === highlight }"
          role="option"
          :aria-selected="idx === highlight ? 'true' : 'false'"
          @mousedown.prevent
          @click="add(item.tag, $event.shiftKey)"
        >
          <span class="fm-check-label"
            >{{ item.before }}<b>{{ item.match }}</b
            >{{ item.after }}</span
          >
          <span class="fm-n">{{ formatCount(item.count) }}</span>
        </div>
      </div>
      <p v-if="query.trim() && !suggestions.length" class="fm-help">
        No tag matches.
      </p>
    </div>
    <div class="tbm-section">
      <template v-for="(row, r) in rows" :key="row.id">
        <span class="tbm-label" :class="{ 'ftf-label--next': r > 0 }">{{
          row.label
        }}</span>
        <div class="ftf-chips">
          <span
            v-for="chip in row.chips"
            :key="`${chip.tag}:${chip.threshold}`"
            class="ftf-chip"
            :class="row.tone && `ftf-chip--${row.tone}`"
          >
            <button
              class="ftf-chip-body"
              type="button"
              :title="chip.tag"
              :aria-label="`${chip.tag}: ${row.short}. Move to ${rows[1 - r].short}`"
              @click="act('flip', chip, row.id)"
            >
              <v-icon v-if="row.icon" size="14" class="ftf-chip-icon">{{
                row.icon
              }}</v-icon>
              <span class="ftf-chip-name">{{ chip.tag }}</span>
            </button>
            <span v-if="chip.threshold != null" class="ftf-thr">
              <select
                class="ftf-thr-select"
                :value="chip.threshold"
                :aria-label="`${chip.tag} ${row.short} threshold`"
                @change="emit('threshold', chip, row.id, +$event.target.value)"
              >
                <option
                  v-for="t in thresholdsFor(row, chip)"
                  :key="t"
                  :value="t"
                >
                  {{ row.sign }} {{ percent(t) }}
                </option>
              </select>
              <v-icon size="14" class="ftf-thr-caret">mdi-chevron-down</v-icon>
            </span>
            <button
              class="ftf-chip-x"
              type="button"
              :aria-label="`Remove ${chip.tag}`"
              @click="act('remove', chip, row.id)"
            >
              <v-icon size="14">mdi-close</v-icon>
            </button>
          </span>
        </div>
      </template>
    </div>
    <div class="tbm-footer">{{ footer }}</div>
  </div>
</template>

<script setup>
/**
 * A typeahead over the tag vocabulary above two rows of chips: the Tags and
 * Tag confidence menus. Enter or Tab adds the highlighted tag to the first row,
 * Shift+Enter or Shift+Tab to the second; Backspace in an empty field removes the last chip
 * of the lower row. Clicking a chip moves it to the other row, the mouse path
 * to the second row. The parent owns what adding, flipping and removing mean.
 */
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { percent } from "../../utils/filterChips";

const MAX_SUGGESTIONS = 4;

const props = defineProps({
  title: { type: String, required: true },
  // [{ tag, count }] from /tags, most used first.
  tags: { type: Array, required: true },
  // Exactly two: [{ id, label, short, chips: [{ tag, threshold? }], tone?,
  // icon?, sign?, thresholds? }]. The first is Enter's, the second Shift+Enter's.
  rows: { type: Array, required: true },
  footer: { type: String, default: "" },
});

const emit = defineEmits(["add", "flip", "remove", "threshold", "clear"]);

const query = ref("");
const highlight = ref(0);
const fieldRef = ref(null);
const listId = `ftf-list-${Math.random().toString(36).slice(2, 8)}`;

const hasChips = computed(() => props.rows.some((r) => r.chips.length));

// Prefix matches first, then the rest; each keeps /tags' most-used order.
const suggestions = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return [];
  const hits = props.tags
    .map((t) => ({ ...t, at: t.tag.toLowerCase().indexOf(q) }))
    .filter((t) => t.at >= 0);
  return [...hits.filter((t) => t.at === 0), ...hits.filter((t) => t.at > 0)]
    .slice(0, MAX_SUGGESTIONS)
    .map((t) => ({
      tag: t.tag,
      count: t.count,
      before: t.tag.slice(0, t.at),
      match: t.tag.slice(t.at, t.at + q.length),
      after: t.tag.slice(t.at + q.length),
    }));
});

// Also on a reload of the vocabulary, which can shorten the list under it.
watch(suggestions, () => {
  highlight.value = 0;
});

function move(step) {
  const last = suggestions.value.length - 1;
  highlight.value = Math.max(0, Math.min(last, highlight.value + step));
}

function add(tag, second) {
  emit("add", tag, props.rows[second ? 1 : 0].id);
  query.value = "";
  fieldRef.value?.focus();
}

function onEnter(event) {
  // Enter that commits an IME composition is not a pick.
  if (event.isComposing) return;
  const item = suggestions.value[highlight.value];
  if (item) add(item.tag, event.shiftKey);
}

// Tab completes like Enter while there is a suggestion to take; with none it
// moves focus as usual, so the field is never a keyboard trap.
function onTab(event) {
  const item = suggestions.value[highlight.value];
  if (!item || event.isComposing) return;
  event.preventDefault();
  add(item.tag, event.shiftKey);
}

// A first Esc empties the field; only an empty field lets the menu close.
function onEsc(event) {
  if (!query.value) return;
  event.stopPropagation();
  query.value = "";
}

// The chip's button goes with it, so focus comes back to the field rather
// than dropping to the page.
function act(what, chip, rowId) {
  emit(what, chip, rowId);
  fieldRef.value?.focus();
}

function removeLast() {
  if (query.value) return;
  const row = [...props.rows].reverse().find((r) => r.chips.length);
  if (row) emit("remove", row.chips[row.chips.length - 1], row.id);
}

// A threshold set elsewhere (the stats sidebar, an older menu) stays listed.
function thresholdsFor(row, chip) {
  const list = row.thresholds || [];
  return list.includes(chip.threshold)
    ? list
    : [...list, chip.threshold].sort((a, b) => b - a);
}

function formatCount(n) {
  return n == null ? "" : Number(n).toLocaleString();
}

onMounted(() => nextTick(() => fieldRef.value?.focus()));
</script>

<style scoped>
/* The design's submenu for this direction is the root menu's width: the hint
   and a long chip need the extra 40px over --filter-submenu-w. */
.ftf {
  width: var(--filter-menu-w);
}
.ftf-input {
  padding-right: 9rem;
}
.ftf-hint {
  position: absolute;
  right: var(--space-2);
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  white-space: nowrap;
  pointer-events: none;
}
/* The arrows are glyphs, not words: at the mono 2xs of .fm-kbd they shrink to
   specks, so the hint's keycaps take the UI face a step up. */
.ftf-hint .fm-kbd {
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  padding: 0 var(--space-2);
}
.ftf-list {
  margin-top: var(--space-2);
}
.ftf-list .fm-check {
  align-items: center;
  cursor: pointer;
}
.ftf-label--next {
  margin-top: var(--space-4);
}
.ftf-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
/* The design system's Tag: 4px radius, an ink wash at rest. Has is the olive
   selection wash with a check; lacks a red wash, ⊘ and a struck name, so the
   state never rests on colour alone. */
.ftf-chip {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  height: var(--control-h-sm);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: color-mix(in srgb, rgb(var(--v-theme-on-panel)) 10%, transparent);
  color: rgb(var(--v-theme-on-panel));
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  white-space: nowrap;
}
.ftf-chip--has {
  background: var(--active-wash);
  border-color: color-mix(in srgb, var(--selected-ink) 45%, transparent);
}
.ftf-chip--lacks {
  background: rgba(var(--v-theme-surface-error), 0.1);
  border-color: rgba(var(--v-theme-surface-error), 0.45);
}
.ftf-chip-body {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  height: 100%;
  padding: 0 var(--space-1) 0 var(--space-3);
  border-radius: var(--radius-sm);
  color: inherit;
  font: inherit;
}
button.ftf-chip-body {
  cursor: pointer;
}
button.ftf-chip-body:hover {
  background: var(--hover-wash);
}
.ftf-chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
}
.ftf-chip--has .ftf-chip-icon {
  color: var(--selected-ink);
}
.ftf-chip--lacks .ftf-chip-icon {
  color: rgb(var(--v-theme-surface-error));
}
.ftf-chip--lacks .ftf-chip-name {
  text-decoration: line-through;
  text-decoration-color: rgba(var(--v-theme-surface-error), 0.6);
}
/* The threshold is its own small menu on the chip. */
.ftf-thr {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: 100%;
  margin-left: var(--space-1);
  border-left: 1px solid rgba(var(--v-theme-on-panel), 0.2);
}
.ftf-thr-select {
  appearance: none;
  height: 100%;
  padding: 0 calc(var(--space-2) + 14px) 0 var(--space-2);
  border-radius: var(--radius-sm);
  color: inherit;
  font: inherit;
  font-variant-numeric: tabular-nums;
  background: none;
  cursor: pointer;
}
.ftf-thr-select:hover {
  background: var(--hover-wash);
}
.ftf-thr-caret {
  position: absolute;
  right: var(--space-1);
  pointer-events: none;
  opacity: var(--opacity-text-secondary);
}
.ftf-chip-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-right: var(--space-1);
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
.ftf-chip-x:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-surface-error));
}
</style>
