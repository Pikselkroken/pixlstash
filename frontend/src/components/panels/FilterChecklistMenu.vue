<template>
  <div class="tbm fm-sub" role="group" :aria-label="title">
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
      <slot name="top" />
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
          <span class="fm-check-label">{{ item.label }}</span>
          <span class="fm-n">{{ formatCount(countOf(item)) }}</span>
        </label>
        <p v-if="!shown.length" class="fm-empty">{{ emptyText }}</p>
      </div>
    </div>
    <div v-if="hasMore" class="tbm-footer">
      Showing {{ shown.length }} of {{ matching.length }}. Type to narrow.
    </div>
    <div v-else-if="hint" class="tbm-footer">
      <span class="fm-kbd">Enter</span> {{ hint }}
    </div>
  </div>
</template>

<script setup>
/**
 * A filter field over a checklist with counts: the Tags, Checkpoint and
 * LoRA menus. Ticking emits `toggle`; the parent owns what that means.
 */
import { computed, nextTick, onMounted, ref, watch } from "vue";

const MAX_SHOWN = 50;

const props = defineProps({
  title: { type: String, required: true },
  placeholder: { type: String, default: "Filter…" },
  // [{ value, label, count? }]
  items: { type: Array, required: true },
  checked: { type: Array, default: () => [] },
  // (item) => number | undefined, for rows whose count is fetched per row.
  countFor: { type: Function, default: null },
  clearable: { type: Boolean, default: false },
  hint: { type: String, default: "ticks the highlighted row." },
  emptyText: { type: String, default: "Nothing matches." },
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
.fm-list-field {
  margin-bottom: var(--space-2);
}
.fm-list {
  max-height: 320px;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.fm-empty {
  margin: var(--space-2) 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
</style>
