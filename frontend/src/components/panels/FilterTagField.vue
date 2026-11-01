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
          :aria-expanded="items.length ? 'true' : 'false'"
          :aria-controls="listId"
          :aria-activedescendant="
            items[highlight] ? `${listId}-${highlight}` : undefined
          "
          :aria-describedby="`${listId}-hint`"
          @keydown.down.prevent="move(1)"
          @keydown.up.prevent="move(-1)"
          @keydown.right="onRight"
          @keydown.enter.prevent.stop="onEnter"
          @keydown.tab="onTab"
          @keydown.backspace="removeLast"
          @keydown.esc="onEsc"
        />
        <span :id="`${listId}-hint`" class="ftf-hint">
          <kbd class="fm-kbd ftf-kbd">↵</kbd> {{ rows[0].short }} ·
          <kbd class="fm-kbd ftf-kbd">⇧↵</kbd> {{ rows[1].short }}
        </span>
      </div>
      <span v-if="browsing" class="tbm-label ftf-label--next">By group</span>
      <div
        v-show="items.length"
        :id="listId"
        class="ftf-list"
        role="listbox"
        :aria-label="browsing ? 'By group' : `${title} suggestions`"
      >
        <template v-for="(item, idx) in items" :key="item.key">
          <div
            :id="`${listId}-${idx}`"
            class="tbm-check fm-check"
            :class="{
              'fm-check--kbd': idx === highlight,
              'ftf-item--tag': browsing && item.kind === 'tag',
            }"
            role="option"
            :aria-selected="idx === highlight ? 'true' : 'false'"
            :aria-label="item.kind === 'group' ? groupAria(item) : undefined"
            @mousedown.prevent
            @click="activate(item, $event.shiftKey)"
          >
            <!-- The chevron opens a group's tags; the keyboard does it with →. -->
            <span
              v-if="item.kind === 'group' && browsing"
              class="ftf-twist"
              aria-hidden="true"
              @click.stop="toggleGroup(item)"
            >
              <v-icon size="14">{{
                item.open ? "mdi-chevron-down" : "mdi-chevron-right"
              }}</v-icon>
            </span>
            <v-icon
              v-if="item.kind === 'group'"
              size="14"
              class="ftf-group-icon"
              aria-hidden="true"
              >mdi-folder-outline</v-icon
            >
            <span class="fm-check-label"
              >{{ item.before }}<b>{{ item.match }}</b
              >{{ item.after }}</span
            >
            <span class="fm-n">{{ countText(item) }}</span>
            <!-- The row Tab (and Enter) will take, marked on the row itself. -->
            <kbd
              v-if="idx === highlight && !browsing"
              class="fm-kbd ftf-kbd ftf-row-kbd"
              aria-hidden="true"
              >Tab</kbd
            >
          </div>
          <div v-if="item.more" class="ftf-more" role="presentation">
            {{ item.more }} more · type to narrow
          </div>
        </template>
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
              :title="chipLabel(chip)"
              :aria-label="`${chipLabel(chip)}: ${row.short}. Move to ${rows[1 - r].short}`"
              @click="act('flip', chip, row.id)"
            >
              <v-icon v-if="row.icon" size="14" class="ftf-chip-icon">{{
                row.icon
              }}</v-icon>
              <span class="ftf-chip-name">{{ chipLabel(chip) }}</span>
            </button>
            <span v-if="chip.threshold != null" class="ftf-thr">
              <select
                class="ftf-thr-select"
                :value="chip.threshold"
                :aria-label="`${chipLabel(chip)} ${row.short} threshold`"
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
              :aria-label="`Remove ${chipLabel(chip)}`"
              @click="act('remove', chip, row.id)"
            >
              <v-icon size="14">mdi-close</v-icon>
            </button>
          </span>
          <span v-if="!row.chips.length" class="ftf-none">None</span>
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
 *
 * With `grouped` (the Tags menu) and groups in the library, an empty field
 * lists them under "By group": a group row adds that group as a term
 * (`__group:<id>`), its chevron (or →) opens its top tags, and a tag there is
 * picked as a typed one is. Typing replaces the list with suggestions, and a
 * group whose name matches is one of them. Tab never takes the browse list, so
 * it can never hold focus in the field.
 */
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { groupToken, useTagGroupsStore } from "../../stores/useTagGroupsStore";
import { formatCount, percent } from "../../utils/filterChips";

const MAX_SUGGESTIONS = 4;
// A typed query can match at most this many group names; tags fill the rest.
const MAX_GROUP_SUGGESTIONS = 2;
// The tags a group opens with; the rest wait for a typed narrowing.
const GROUP_PREVIEW = 3;

const props = defineProps({
  title: { type: String, required: true },
  // [{ tag, count }] from /tags, most used first.
  tags: { type: Array, required: true },
  // Exactly two: [{ id, label, short, chips: [{ tag, threshold?, label? }], tone?,
  // icon?, sign?, thresholds? }]. The first is Enter's, the second Shift+Enter's.
  // A chip's `label`, when set, is what it shows; `tag` stays the filter value.
  rows: { type: Array, required: true },
  footer: { type: String, default: "" },
  // Offer the library's tag groups: the BY GROUP list and group matches.
  grouped: { type: Boolean, default: false },
});

const emit = defineEmits(["add", "flip", "remove", "threshold", "clear"]);

const query = ref("");
const highlight = ref(0);
const fieldRef = ref(null);
const listId = `ftf-list-${Math.random().toString(36).slice(2, 8)}`;
const tagGroups = useTagGroupsStore();
// Group keys ("none" for Ungrouped) whose tags the BY GROUP list shows.
const openGroups = ref(new Set());

const hasChips = computed(() => props.rows.some((r) => r.chips.length));
const chipLabel = (chip) => chip.label ?? chip.tag;

// Prefix matches first, then the rest; each keeps its order.
function prefixFirst(hits) {
  return [...hits.filter((h) => h.at === 0), ...hits.filter((h) => h.at > 0)];
}

// A row's name split around the typed text, for its bold run.
function around(text, q) {
  const at = q ? text.toLowerCase().indexOf(q) : -1;
  if (at < 0) return { before: text, match: "", after: "" };
  return {
    before: text.slice(0, at),
    match: text.slice(at, at + q.length),
    after: text.slice(at + q.length),
  };
}

// Every group in rail order, then Ungrouped; each with its tags in the
// vocabulary's order. A tag filed under a group that is gone is Ungrouped.
const groupLists = computed(() => {
  const lists = new Map(
    tagGroups.groups.map((g) => [
      g.id,
      { key: String(g.id), id: g.id, name: g.name, tags: [] },
    ]),
  );
  const ungrouped = { key: "none", id: null, name: "Ungrouped", tags: [] };
  for (const t of props.tags) {
    (lists.get(tagGroups.groupOf.get(t.tag)) ?? ungrouped).tags.push(t);
  }
  return [...lists.values(), ungrouped];
});

// The BY GROUP list: a group row, then its top tags when it is open.
const browsing = computed(
  () => props.grouped && tagGroups.hasGroups && !query.value.trim(),
);

const browseItems = computed(() => {
  const out = [];
  for (const g of groupLists.value) {
    const open = openGroups.value.has(g.key);
    out.push({
      kind: "group",
      key: `g:${g.key}`,
      id: g.id,
      name: g.name,
      groupKey: g.key,
      open,
      count: g.tags.length,
      ...around(g.name, ""),
    });
    const shown = open ? g.tags.slice(0, GROUP_PREVIEW) : [];
    for (const t of shown) {
      out.push({
        kind: "tag",
        key: `t:${g.key}:${t.tag}`,
        tag: t.tag,
        count: t.count,
        ...around(t.tag, ""),
      });
    }
    const rest = g.tags.length - shown.length;
    if (shown.length && rest > 0) out[out.length - 1].more = rest;
  }
  return out;
});

// Typed text: the groups whose name matches, then the tags that do.
const suggestions = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return [];
  const groups =
    props.grouped && tagGroups.hasGroups
      ? prefixFirst(
          groupLists.value
            .map((g) => ({ g, at: g.name.toLowerCase().indexOf(q) }))
            .filter((h) => h.at >= 0),
        )
          .slice(0, MAX_GROUP_SUGGESTIONS)
          .map(({ g }) => ({
            kind: "group",
            key: `g:${g.key}`,
            id: g.id,
            name: g.name,
            count: g.tags.length,
            ...around(g.name, q),
          }))
      : [];
  const hits = props.tags
    .map((t) => ({ ...t, at: t.tag.toLowerCase().indexOf(q) }))
    .filter((t) => t.at >= 0);
  const tags = prefixFirst(hits).map((t) => ({
    kind: "tag",
    key: `t:${t.tag}`,
    tag: t.tag,
    count: t.count,
    ...around(t.tag, q),
  }));
  return [...groups, ...tags].slice(0, MAX_SUGGESTIONS);
});

// The rows the list shows and the arrows move through.
const items = computed(() =>
  browsing.value ? browseItems.value : suggestions.value,
);

// Also on a reload of the vocabulary or the groups, which can shorten the list
// under it. Opening a group does not: its row keeps the highlight.
watch([query, () => props.tags, () => tagGroups.groupOf], () => {
  highlight.value = 0;
});

function move(step) {
  const last = items.value.length - 1;
  highlight.value = Math.max(0, Math.min(last, highlight.value + step));
}

function add(tag, second) {
  emit("add", tag, props.rows[second ? 1 : 0].id);
  query.value = "";
  fieldRef.value?.focus();
}

// A group row adds its group's term, a tag row the tag: both as a typed pick.
function activate(item, second) {
  add(item.kind === "group" ? groupToken(item.id) : item.tag, second);
}

function toggleGroup(item) {
  const next = new Set(openGroups.value);
  if (next.has(item.groupKey)) next.delete(item.groupKey);
  else next.add(item.groupKey);
  openGroups.value = next;
}

// → opens the highlighted group in the BY GROUP list, and a second → steps
// into its tags. Anywhere else (a typed query, a tag row) it is the caret's.
function onRight(event) {
  const item = items.value[highlight.value];
  if (!browsing.value || item?.kind !== "group") return;
  event.preventDefault();
  if (item.open) move(1);
  else toggleGroup(item);
}

function countText(item) {
  if (item.kind !== "group") return formatCount(item.count);
  return `${item.count} ${item.count === 1 ? "tag" : "tags"}`;
}

function groupAria(item) {
  const state = browsing.value ? (item.open ? ", expanded" : ", collapsed") : "";
  return `${item.name}, ${countText(item)}${state}`;
}

function onEnter(event) {
  // Enter that commits an IME composition is not a pick.
  if (event.isComposing) return;
  const item = items.value[highlight.value];
  if (item) activate(item, event.shiftKey);
}

// Tab completes like Enter while a typed suggestion is there to take; with none
// it moves focus as usual, so the field is never a keyboard trap. The BY GROUP
// list is never taken by Tab: it would hold focus in the field.
//
// Both keys stop here when the field takes them: the menu around it (a
// VMenu that stays open on a click inside) reads Enter and Tab as "focus the
// next control, or close when there is none", so a tag typed before the
// vocabulary arrived, or one with no match, closed the whole Filters menu.
function onTab(event) {
  const item = suggestions.value[highlight.value];
  if (!item || event.isComposing) return;
  event.preventDefault();
  event.stopPropagation();
  activate(item, event.shiftKey);
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
   specks, so the field's keycaps take the UI face a step up. */
.ftf-kbd {
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  padding: 0 var(--space-2);
}
.ftf-none {
  display: inline-flex;
  align-items: center;
  height: var(--control-h-sm);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
.ftf-row-kbd {
  margin-left: var(--space-3);
  font-size: var(--text-xs);
}
/* --space-3: clear of the field's focus ring, as the checklist's list is. */
.ftf-list {
  margin-top: var(--space-3);
}
.ftf-list .fm-check {
  align-items: center;
  cursor: pointer;
}
/* The chevron and the group glyph share one 16px slot, so a tag under a group
   lines its name up with the group's. */
.ftf-twist,
.ftf-group-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 16px;
  width: 16px;
  height: 16px;
  margin-right: var(--space-2);
}
.ftf-twist {
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  cursor: pointer;
}
.ftf-twist:hover {
  background: var(--hover-wash);
}
.ftf-item--tag .fm-check-label {
  padding-left: calc(var(--gutter-glyph) + var(--space-2));
}
.ftf-more {
  display: flex;
  align-items: center;
  min-height: var(--control-h-sm);
  padding: 0 var(--space-4) 0 calc(var(--space-4) + var(--gutter-glyph) + var(--space-2));
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
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
