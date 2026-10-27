<!--
  The toolbar search field (#2139, Tag Clipboard). Tokens before the text are
  the same filters as the filter strip and the Filter menu: "has" is
  filters.tagFilter, "lacks" is filters.tagRejectedFilter. Typing lists tags,
  tag sets and a words search; pasting comma or newline text makes tokens.
-->
<template>
  <div class="stf">
    <div class="stf-field" @click.self="focus">
      <span
        v-for="(token, i) in tokens"
        :key="tokenKey(token)"
        class="stf-token"
        :class="[
          `stf-token--${token.kind}`,
          { 'stf-token--selected': i >= selectedFrom },
        ]"
      >
        <button
          type="button"
          class="stf-token-body"
          :aria-label="`Copy ${token.kind} ${token.tag}`"
          @mousedown.prevent
          @click="copyToken(token)"
        >
          <span class="stf-token-kind">{{
            copiedKey === tokenKey(token) ? "copied" : token.kind
          }}</span>
          <span class="stf-token-tag">{{ token.tag }}</span>
        </button>
        <button
          type="button"
          class="stf-token-x"
          :aria-label="`Remove ${token.kind} ${token.tag}`"
          @mousedown.prevent
          @click="removeToken(token)"
        >
          <v-icon size="13">mdi-close</v-icon>
        </button>
      </span>
      <input
        ref="inputRef"
        v-model="searchStore.searchInput"
        class="stf-input"
        type="text"
        role="combobox"
        placeholder="Search, or paste a prompt to make tokens"
        autocomplete="off"
        aria-label="Search"
        aria-autocomplete="list"
        aria-controls="search-token-suggestions"
        :aria-expanded="listVisible ? 'true' : 'false'"
        :aria-activedescendant="
          listVisible && rows.length ? `search-token-option-${currentIndex}` : null
        "
        @input="selectionSize = 0"
        @keydown="onKeydown"
        @paste="onPaste"
      />
    </div>
    <p class="visually-hidden" role="status">
      {{ copiedKey ? "Copied to the Clipboard" : "" }}
    </p>
    <div
      v-if="listVisible"
      id="search-token-suggestions"
      class="stf-list"
      role="listbox"
      aria-label="Search suggestions"
    >
      <template v-for="section in sections" :key="section.label">
        <div class="stf-sec tbm-label" role="presentation">
          {{ section.label }}
        </div>
        <div
          v-for="{ row, index } in section.items"
          :id="`search-token-option-${index}`"
          :key="row.key"
          class="stf-row"
          :class="{ 'stf-row--active': index === currentIndex }"
          role="option"
          :aria-selected="index === currentIndex ? 'true' : 'false'"
          @mouseenter="activeIndex = index"
          @mousedown.prevent
          @click="pickRow(row)"
        >
          <template v-if="row.kind === 'tag'">
            <v-icon size="16" class="stf-row-icon">mdi-tag-outline</v-icon>
            <span class="stf-row-label">{{ row.tag }}</span>
            <span class="stf-row-count">{{ row.count }}</span>
          </template>
          <template v-else>
            <v-icon size="16" class="stf-row-icon">mdi-text-search</v-icon>
            <span class="stf-row-label"
              >Search descriptions and text for “{{ row.text }}”</span
            >
          </template>
          <span v-if="index === currentIndex" class="stf-row-keys">
            <template v-if="row.kind === 'words'"><kbd>Ctrl+↵</kbd></template>
            <template v-else
              ><kbd>↵</kbd> has <kbd>⇧↵</kbd> lacks</template
            >
          </span>
        </div>
      </template>
    </div>
    <div class="stf-foot">
      <span>Click a token to copy it</span>
      <span><kbd>⇧←</kbd> select tokens</span>
      <span><kbd>⌫</kbd> remove</span>
      <span><kbd>Esc</kbd> close</span>
    </div>
  </div>
</template>

<script>
// Tag vocabulary shared by every mount for ~30 s, as TbTagPanel shares its list.
const vocabularyCache = { rows: [], fetchedAt: 0 };
</script>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { listTags } from "../../api/tags";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSearchStore } from "../../stores/useSearchStore";
import {
  splitTagText,
  useTagClipboardStore,
} from "../../stores/useTagClipboardStore";

const emit = defineEmits(["close"]);

const MAX_TAG_ROWS = 8;
const VOCABULARY_TTL_MS = 30_000;
const COPIED_FLASH_MS = 1200;

const filters = useFilterStore();
const searchStore = useSearchStore();
const tagClipboard = useTagClipboardStore();

const inputRef = ref(null);
const selectionSize = ref(0);
const activeIndex = ref(0);
const listDismissedFor = ref(null);
const copiedKey = ref(null);
let copiedTimer = null;

// Tag vocabulary with counts: [{ tag, count }].
const vocabulary = ref(vocabularyCache.rows);

async function loadVocabulary() {
  if (Date.now() - vocabularyCache.fetchedAt < VOCABULARY_TTL_MS) return;
  try {
    const rows = await listTags();
    if (Array.isArray(rows)) {
      vocabularyCache.rows = rows;
      vocabulary.value = rows;
      vocabularyCache.fetchedAt = Date.now();
    }
  } catch (e) {
    // The suggestion list just stays as it was; the text search still works.
    console.warn("Couldn't load the tag list for search suggestions", e);
  }
}
loadVocabulary();

const tokens = computed(() => [
  ...filters.tagFilter.map((tag) => ({ kind: "has", tag })),
  ...filters.tagRejectedFilter.map((tag) => ({ kind: "lacks", tag })),
]);

// Shift+← selects from the end, one token per press.
const selectedFrom = computed(
  () => tokens.value.length - Math.min(selectionSize.value, tokens.value.length),
);
const selectedTokens = computed(() => tokens.value.slice(selectedFrom.value));

const query = computed(() => (searchStore.searchInput || "").trim());
const queryKey = computed(() => query.value.toLowerCase());

const listVisible = computed(
  () =>
    queryKey.value !== "" && listDismissedFor.value !== searchStore.searchInput,
);

const sections = computed(() => {
  if (!listVisible.value) return [];
  let index = 0;
  const out = [];
  const add = (label, items) => {
    if (!items.length) return;
    out.push({
      label,
      items: items.map((row) => ({ row, index: index++ })),
    });
  };
  add("Tags", matchingTags());
  add("Words", [{ kind: "words", key: "words", text: query.value }]);
  return out;
});

const rows = computed(() =>
  sections.value.flatMap((section) => section.items.map((item) => item.row)),
);
const currentIndex = computed(() =>
  Math.min(activeIndex.value, Math.max(rows.value.length - 1, 0)),
);

function matchingTags() {
  const q = queryKey.value;
  // A tag already held as Has is not offered again; a Lacks tag stays
  // offered, so Enter on it moves it across to Has.
  const taken = new Set(filters.tagFilter.map((t) => t.toLowerCase()));
  const prefix = [];
  const contains = [];
  for (const row of vocabulary.value) {
    const name = String(row?.tag ?? "");
    const lower = name.toLowerCase();
    if (!lower || taken.has(lower)) continue;
    if (lower.startsWith(q)) prefix.push(row);
    else if (lower.includes(q)) contains.push(row);
  }
  return [...prefix, ...contains]
    .slice(0, MAX_TAG_ROWS)
    .map((row) => ({
      kind: "tag",
      key: `tag:${row.tag}`,
      tag: row.tag,
      count: row.count,
    }));
}

function tokenKey(token) {
  return `${token.kind}:${token.tag}`;
}

/** Put a tag in the Has or Lacks list, and take it out of the other one. */
function addToken(kind, tag) {
  const name = String(tag ?? "").trim();
  if (!name) return;
  const key = name.toLowerCase();
  const isHas = kind === "has";
  const sameList = isHas ? filters.tagFilter : filters.tagRejectedFilter;
  const otherList = isHas ? filters.tagRejectedFilter : filters.tagFilter;
  const present = sameList.some((t) => t.toLowerCase() === key);
  const others = otherList.filter((t) => t.toLowerCase() !== key);
  if (!present) {
    const next = [...sameList, name];
    if (isHas) filters.tagFilter = next;
    else filters.tagRejectedFilter = next;
  }
  if (others.length !== otherList.length) {
    if (isHas) filters.tagRejectedFilter = others;
    else filters.tagFilter = others;
  }
}

function removeToken(token) {
  if (token.kind === "has") {
    filters.tagFilter = filters.tagFilter.filter((t) => t !== token.tag);
  } else {
    filters.tagRejectedFilter = filters.tagRejectedFilter.filter(
      (t) => t !== token.tag,
    );
  }
}

function removeSelectedOrLast() {
  const targets = selectedTokens.value.length
    ? selectedTokens.value
    : tokens.value.slice(-1);
  targets.forEach(removeToken);
  selectionSize.value = 0;
}

function runTextSearch() {
  searchStore.commitSearch();
  emit("close");
}

/** Enter (Has) or Shift+Enter (Lacks) on a row; no row means a text search. */
function pickRow(row, lacks = false) {
  if (!row || row.kind === "words") {
    runTextSearch();
    return;
  }
  const kind = lacks ? "lacks" : "has";
  addToken(kind, row.tag);
  searchStore.searchInput = "";
  selectionSize.value = 0;
  activeIndex.value = 0;
}

async function copyToken(token) {
  await tagClipboard.copyTags([token.tag]);
  copiedKey.value = tokenKey(token);
  clearTimeout(copiedTimer);
  copiedTimer = setTimeout(() => {
    copiedKey.value = null;
  }, COPIED_FLASH_MS);
}

function onPaste(event) {
  const pasted = event.clipboardData?.getData("text") ?? "";
  // Plain text without a comma or newline is just text.
  if (!/[,\n]/.test(pasted)) return;
  event.preventDefault();
  splitTagText(pasted).forEach((tag) => addToken("has", tag));
}

function onKeydown(event) {
  const field = event.target;
  if (event.key === "Enter") {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) runTextSearch();
    else pickRow(rows.value[currentIndex.value], event.shiftKey);
    return;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    if (!rows.value.length) return;
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    const next = currentIndex.value + step;
    activeIndex.value = Math.min(Math.max(next, 0), rows.value.length - 1);
    return;
  }
  if (event.key === "ArrowLeft" && event.shiftKey) {
    if (field.selectionStart !== 0 || field.selectionEnd !== 0) return;
    if (!tokens.value.length) return;
    event.preventDefault();
    selectionSize.value = Math.min(selectionSize.value + 1, tokens.value.length);
    return;
  }
  if (event.key === "Backspace") {
    if (searchStore.searchInput || !tokens.value.length) return;
    event.preventDefault();
    removeSelectedOrLast();
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    if (selectionSize.value) {
      selectionSize.value = 0;
    } else if (listVisible.value) {
      listDismissedFor.value = searchStore.searchInput;
    } else {
      emit("close");
    }
  }
}

watch(queryKey, () => {
  activeIndex.value = 0;
});

function focus() {
  inputRef.value?.focus();
}

onBeforeUnmount(() => {
  clearTimeout(copiedTimer);
});

defineExpose({ focus });
</script>

<style scoped>
.stf {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.stf-field {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-h);
  padding: var(--space-2) var(--space-3);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  cursor: text;
}
.stf-field:focus-within {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
}
.stf-token {
  display: inline-flex;
  align-items: center;
  height: var(--control-h-sm);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  white-space: nowrap;
}
.stf-token--has {
  background: var(--active-wash);
  border: 1px solid transparent;
}
.stf-token--lacks {
  border: 1px dashed rgb(var(--v-theme-border));
}
.stf-token--selected {
  outline: 1px solid var(--active-bar);
}
.stf-token-body,
.stf-token-x {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 100%;
  padding: 0 var(--space-2);
  color: rgb(var(--v-theme-on-panel));
  background: transparent;
  border: 0;
  font: inherit;
  cursor: pointer;
}
.stf-token-body {
  padding-left: var(--space-3);
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
}
.stf-token-body:hover {
  background: var(--hover-wash);
}
.stf-token-x {
  padding: 0 var(--space-2);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  opacity: 0.55;
}
.stf-token-x:hover {
  opacity: 1;
  background: var(--hover-wash);
}
.stf-token-kind {
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  font-size: var(--text-xs);
}
.stf-input {
  flex: 1 1 120px;
  min-width: 120px;
  height: var(--control-h-sm);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-panel));
  background: transparent;
  border: 0;
  outline: none;
}
.stf-input::placeholder {
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
.stf-list {
  max-height: 320px;
  overflow-y: auto;
  padding-bottom: var(--space-2);
}
.stf-sec {
  padding: var(--space-3) var(--space-3) var(--space-2);
}
.stf-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h-sm);
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-panel));
  cursor: pointer;
}
.stf-row--active {
  background: var(--active-wash);
  color: var(--active-text);
}
.stf-row-icon {
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
.stf-row-label {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stf-row-count {
  margin-left: auto;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
.stf-row-keys {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  white-space: nowrap;
}
.stf-foot {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-2) var(--space-3) 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
kbd {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  padding: 0 var(--space-1);
  border: 1px solid currentColor;
  border-radius: var(--radius-sm);
}
</style>
