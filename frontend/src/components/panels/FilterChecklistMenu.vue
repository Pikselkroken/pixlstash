<template>
  <div class="tbm fm-sub fm-checklist" role="group" :aria-label="title">
    <div class="tbm-header">
      <span class="tbm-title">{{ title }}</span>
      <span class="tbm-spacer"></span>
      <button
        class="tbm-ghost"
        type="button"
        :disabled="!clearable"
        @click="emit('clear')"
      >
        Clear
      </button>
    </div>
    <div class="tbm-section">
      <div class="tbm-input-wrap fm-list-field">
        <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
        <input
          ref="fieldRef"
          v-model="query"
          class="tbm-input tbm-input--with-icon"
          :placeholder="placeholder"
          :aria-label="placeholder"
          autocomplete="off"
          :aria-controls="listId"
          :aria-activedescendant="
            shown[highlight] ? `${listId}-${highlight}` : undefined
          "
          @keydown.down.prevent="move(1)"
          @keydown.up.prevent="move(-1)"
          @keydown.enter.prevent="toggleHighlighted"
        />
      </div>
      <div
        :id="listId"
        ref="listRef"
        class="fm-list"
        role="group"
        :aria-label="title"
      >
        <label
          v-for="(item, idx) in shown"
          :id="`${listId}-${idx}`"
          :key="item.value"
          class="tbm-check fm-check"
          :class="{ 'fm-check--kbd': idx === highlight }"
          :title="item.label"
        >
          <input
            type="checkbox"
            :checked="isChecked(item.value)"
            @change="emit('toggle', item.value, $event.target.checked)"
          />
          <!-- A workflow's own covers (#1797): generated names read alike,
               and the cover is how the Workflows screen tells them apart. -->
          <span
            v-if="item.covers?.length"
            class="fm-mosaic"
            :class="`fm-mosaic--${item.covers.length}`"
            aria-hidden="true"
          >
            <img v-for="src in item.covers" :key="src" :src="src" alt="" />
          </span>
          <span class="fm-check-label">{{ item.label }}</span>
          <v-icon
            v-if="item.hidden"
            size="14"
            class="fm-check-hidden"
            aria-label="hidden"
            >mdi-eye-off-outline</v-icon
          >
          <span v-if="item.tag" class="fm-check-tag">{{ item.tag }}</span>
          <span class="fm-n">{{ formatCount(countOf(item)) }}</span>
        </label>
        <p v-if="!shown.length" class="fm-empty">{{ emptyText }}</p>
      </div>
    </div>
    <div v-if="hasMore" class="tbm-footer">
      Showing {{ shown.length }} of {{ matching.length }}. Type to narrow.
    </div>
    <div v-else-if="footer" class="tbm-footer">{{ footer }}</div>
    <div v-else-if="hint" class="tbm-footer">
      <span class="fm-kbd">Enter</span> {{ hint }}
    </div>
  </div>
</template>

<script setup>
/**
 * A filter field over a checklist with counts: the Checkpoint, LoRA and
 * Workflow menus. Ticking emits `toggle`; the parent owns what that means.
 */
import { computed, nextTick, onMounted, ref, watch } from "vue";

const MAX_SHOWN = 50;

const props = defineProps({
  title: { type: String, required: true },
  placeholder: { type: String, default: "Filter…" },
  // [{ value, label, count?, covers?: [url], tag?, hidden? }]
  items: { type: Array, required: true },
  checked: { type: Array, default: () => [] },
  // (item) => number | undefined, for rows whose count is fetched per row.
  countFor: { type: Function, default: null },
  clearable: { type: Boolean, default: false },
  hint: { type: String, default: "ticks the highlighted row." },
  emptyText: { type: String, default: "Nothing matches." },
  // Replaces the Enter hint when set; "Showing 50 of N" still wins.
  footer: { type: String, default: "" },
});

const emit = defineEmits(["toggle", "clear"]);

const query = ref("");
const highlight = ref(0);
const fieldRef = ref(null);
const listRef = ref(null);
const listId = `fm-list-${Math.random().toString(36).slice(2, 8)}`;

const matching = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return props.items;
  return props.items.filter((i) => i.label.toLowerCase().includes(q));
});
const shown = computed(() => matching.value.slice(0, MAX_SHOWN));
const hasMore = computed(() => matching.value.length > MAX_SHOWN);

watch(query, () => {
  highlight.value = 0;
});

function isChecked(value) {
  return props.checked.includes(value);
}

function countOf(item) {
  return props.countFor ? props.countFor(item) : item.count;
}

function formatCount(n) {
  return n == null ? "" : Number(n).toLocaleString();
}

function move(step) {
  const last = shown.value.length - 1;
  highlight.value = Math.max(0, Math.min(last, highlight.value + step));
  // The list scrolls; keep the row Enter would tick in view.
  nextTick(() =>
    listRef.value?.children[highlight.value]?.scrollIntoView?.({
      block: "nearest",
    }),
  );
}

function toggleHighlighted() {
  const item = shown.value[highlight.value];
  if (item) emit("toggle", item.value, !isChecked(item.value));
}

onMounted(() => nextTick(() => fieldRef.value?.focus()));
</script>

<style scoped>
/* The root menu's width, as the tag field takes: at --filter-submenu-w a
   workflow's name beside its cover, kind and count clips to a few letters. */
.fm-checklist {
  width: var(--filter-menu-w);
}
/* --space-3, not --space-2: the field's focus ring reaches 4px out, and would
   fuse with the highlighted first row's ring into one thick bar. */
.fm-list-field {
  margin-bottom: var(--space-3);
}
/* The rows bleed --space-3 past the section's column, so the list bleeds with
   them. Left at the column's width it clipped their highlight ring to two bars
   and scrolled sideways by the overhang. Names ellipsise (the row's title has
   them whole), so nothing here ever needs a horizontal scrollbar. */
.fm-list {
  max-height: 320px;
  margin-inline: calc(var(--space-3) * -1);
  padding-inline: var(--space-3);
  overflow: hidden auto;
  overscroll-behavior: contain;
}
/* 32×24, the card's own arrangement: the cover tall on the left. */
.fm-mosaic {
  flex: none;
  display: inline-grid;
  grid-template-columns: 2fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: var(--space-1);
  width: 32px;
  height: 24px;
  margin-right: var(--space-3);
  align-self: center;
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.fm-mosaic img {
  width: 100%;
  height: 100%;
  min-height: 0;
  object-fit: cover;
}
.fm-mosaic img:first-child {
  grid-row: 1 / 3;
}
.fm-mosaic--1 img:first-child {
  grid-column: 1 / 3;
}
.fm-mosaic--2 img:last-child {
  grid-row: 1 / 3;
}
.fm-check-hidden {
  flex: none;
  align-self: center;
  margin-left: var(--space-2);
  opacity: var(--opacity-text-secondary);
}
.fm-check-tag {
  flex: none;
  align-self: center;
  margin: 0 var(--space-3);
  padding: 0 var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
.fm-empty {
  margin: var(--space-2) 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
</style>
