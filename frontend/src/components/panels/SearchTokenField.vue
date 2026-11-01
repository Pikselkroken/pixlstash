<!--
  The toolbar search field: the field itself, not a trigger for a popover.
  At rest it is the 260 px field, wider up to 560 px when the bar has room,
  with a one-line summary of what narrows the grid; focused it widens in place
  to 560 px and drops one list beneath it.

  Tokens before the text are every filter in the filter strip and the Filter
  menu (`filterChips`), so search and the Filter menu are two doors to one set
  of filters. Typing lists every Filter menu entry whose name matches, grouped
  under the menu's own names. ↵ always searches the words; Tab adds the
  highlighted filter and ⇧Tab its opposite (Lacks, for a tag). Pasting comma
  or newline text makes one Has token per tag; clicking a tag token copies it.
-->
<template>
  <div
    ref="rootRef"
    class="stf"
    :class="{ 'stf--open': open }"
    @focusout="onFocusOut"
  >
    <button
      v-if="!open"
      type="button"
      class="stf-rest"
      :class="{ 'stf-rest--active': summary }"
      aria-label="Search"
      :aria-describedby="summary ? 'search-token-summary' : null"
      aria-keyshortcuts="F"
      @click="focus"
    >
      <v-icon size="18" class="stf-icon">mdi-magnify</v-icon>
      <!-- The name stays "Search"; what narrows the grid is its description. -->
      <span id="search-token-summary" class="stf-rest-text">{{
        summary || "Search"
      }}</span>
      <kbd class="fm-kbd stf-rest-kbd" aria-hidden="true">F</kbd>
    </button>
    <template v-else>
      <div class="stf-field" @mousedown.self.prevent @click.self="focusInput">
        <v-icon size="18" class="stf-icon">mdi-magnify</v-icon>
        <span
          v-for="(token, i) in tokens"
          :key="token.key"
          class="stf-token"
          :class="[
            `stf-token--${token.style}`,
            { 'stf-token--selected': i >= selectedFrom },
          ]"
        >
          <button
            v-if="token.tag != null"
            type="button"
            class="stf-token-body"
            :aria-label="`Copy ${token.label} ${token.value}`"
            @mousedown.prevent
            @click="copyToken(token)"
          >
            <span class="stf-token-kind">{{
              copiedKey === token.key ? "copied" : token.label
            }}</span>
            <span class="stf-token-tag">{{ token.value }}</span>
          </button>
          <span v-else class="stf-token-body stf-token-body--static">
            <span class="stf-token-kind">{{ token.label }}</span>
            <span class="stf-token-tag">{{ token.value }}</span>
          </span>
          <button
            type="button"
            class="stf-token-x"
            :aria-label="`Remove ${token.label} ${token.value}`"
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
          :aria-controls="listVisible ? 'search-token-suggestions' : null"
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
      <div v-if="listVisible" class="tbm stf-drop" @mousedown.prevent>
        <div
          id="search-token-suggestions"
          class="stf-list"
          role="listbox"
          aria-label="Search suggestions"
        >
          <div
            v-if="query"
            id="search-token-words"
            class="stf-row stf-row--words"
            role="option"
            aria-selected="false"
            @click="runTextSearch"
          >
            <v-icon size="16" class="stf-row-icon">mdi-text-search</v-icon>
            <span class="stf-row-label"
              >Search descriptions and text for “{{ query }}”</span
            >
            <span class="stf-row-keys"><kbd>↵</kbd></span>
          </div>
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
              @click="pickRow(row)"
            >
              <v-icon size="16" class="stf-row-icon">{{ row.icon }}</v-icon>
              <span class="stf-row-label"
                >{{ row.parts[0] }}<b class="stf-row-match">{{ row.parts[1] }}</b
                >{{ row.parts[2] }}</span
              >
              <span
                v-if="row.count != null"
                class="stf-row-meta stf-row-meta--count"
                >{{ formatCount(row.count) }}
                {{ row.count === 1 ? "picture" : "pictures" }}</span
              >
              <span v-else-if="row.where" class="stf-row-meta">{{
                row.where
              }}</span>
              <span v-if="index === currentIndex" class="stf-row-keys">
                <kbd>Tab</kbd>
              </span>
            </div>
          </template>
        </div>
        <div class="stf-foot">
          <span><kbd>↵</kbd> search words</span>
          <span><kbd>Tab</kbd> add filter</span>
          <span><kbd>⇧Tab</kbd> without tag</span>
          <span><kbd>⌫</kbd> remove</span>
          <span><kbd>Esc</kbd> close</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script>
// What the list searches, shared by every mount for ~30 s, as RailTags
// shares its tag list: the tag vocabulary, the checkpoints and LoRAs pictures
// record, and the owner's workflows.
const catalogCache = {
  tags: [],
  models: [],
  loras: [],
  workflows: [],
  fetchedAt: 0,
};
</script>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import { listTags } from "../../api/tags";
import { listComfyuiLoras, listComfyuiModels } from "../../api/pictures";
import { listWorkflowCards } from "../../api/workflows";
import { isReadOnly } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";
import { useImportFoldersStore } from "../../stores/useImportFoldersStore";
import { useSearchStore } from "../../stores/useSearchStore";
import {
  splitTagText,
  useTagClipboardStore,
} from "../../stores/useTagClipboardStore";
import {
  FACE_OPTIONS,
  IMPOSSIBLE_OPTIONS,
  MEDIA_OPTIONS,
  STACK_OPTIONS,
  filterChips,
  formatCount,
  modelLabel,
} from "../../utils/filterChips";
import { isGroupToken } from "../../stores/useTagGroupsStore";

const props = defineProps({
  // "No character" and "In no set" only exist in All Pictures.
  allPicturesView: { type: Boolean, default: true },
});
const emit = defineEmits(["update:open"]);

// A short query can match a lot: each kind shows at most this many rows.
const MAX_TAG_ROWS = 6;
const MAX_KIND_ROWS = 4;
const CATALOG_TTL_MS = 30_000;
const COPIED_FLASH_MS = 1200;

const filters = useFilterStore();
const importFoldersStore = useImportFoldersStore();
const searchStore = useSearchStore();
const tagClipboard = useTagClipboardStore();

const rootRef = ref(null);
const inputRef = ref(null);
const open = ref(false);
const selectionSize = ref(0);
const activeIndex = ref(0);
const listDismissedFor = ref(null);
const copiedKey = ref(null);
let copiedTimer = null;

const catalog = ref({ ...catalogCache });

async function loadCatalog() {
  if (Date.now() - catalogCache.fetchedAt < CATALOG_TTL_MS) return;
  try {
    const [tags, models, loras] = await Promise.all([
      listTags(),
      listComfyuiModels(),
      listComfyuiLoras(),
    ]);
    catalogCache.tags = Array.isArray(tags) ? tags : [];
    catalogCache.models = Array.isArray(models) ? models : [];
    catalogCache.loras = Array.isArray(loras) ? loras : [];
    // The Workflows grid is the owner's: a share token cannot read it.
    // Hidden and one-off workflows too: their pictures are still here.
    catalogCache.workflows = isReadOnly.value
      ? []
      : (await listWorkflowCards({ includeHidden: true, includeOneOffs: true }))
          .cards;
    catalogCache.fetchedAt = Date.now();
  } catch (e) {
    // The list keeps what it had; the words search still works.
    console.warn("Couldn't load the filter names for search suggestions", e);
  }
  catalog.value = { ...catalogCache };
}

// ── Tokens: every active filter, as the strip shows it ──────────────────────
const TAG_TOKEN_LABEL = { "Has tag": "has", "Lacks tag": "lacks" };

const tokens = computed(() =>
  filterChips(filters, { allPicturesView: props.allPicturesView }).map(
    (chip) => {
      const tagLabel = TAG_TOKEN_LABEL[chip.kind];
      return {
        key: chip.key,
        label: tagLabel ?? chip.kind,
        value: chip.value,
        // Only a tag token copies: the Clipboard holds tags, not a group's words.
        tag: tagLabel && !isGroupToken(chip.term) ? chip.term : null,
        style: tagLabel === "lacks" ? "lacks" : "has",
        remove: chip.remove,
      };
    },
  ),
);

// At rest: what narrows the grid, in one line - the words, then each filter.
// A Lacks tag says so, or it would read as the Has tag of the same name.
const summary = computed(() =>
  [
    searchStore.searchQuery?.trim(),
    ...tokens.value.map((t) =>
      t.style === "lacks" ? `lacks ${t.value}` : t.value,
    ),
  ]
    .filter(Boolean)
    .join(" · "),
);

// Shift+← selects from the end, one token per press.
const selectedFrom = computed(
  () => tokens.value.length - Math.min(selectionSize.value, tokens.value.length),
);
const selectedTokens = computed(() => tokens.value.slice(selectedFrom.value));

const query = computed(() => (searchStore.searchInput || "").trim());
const queryKey = computed(() => query.value.toLowerCase());

const listVisible = computed(
  () => open.value && listDismissedFor.value !== searchStore.searchInput,
);

// ── The list: every Filter menu entry that matches, under its own kind ─────
/** The label split around the first match, for the bold part. */
function matchParts(label) {
  const at = label.toLowerCase().indexOf(queryKey.value);
  if (at < 0) return [label, "", ""];
  const end = at + queryKey.value.length;
  return [label.slice(0, at), label.slice(at, end), label.slice(end)];
}

/** Prefix matches first, then the rest, capped; a kind's name finds it all. */
function matching(kindLabel, entries, cap = MAX_KIND_ROWS) {
  const q = queryKey.value;
  const kindHit = q.length >= 2 && kindLabel.toLowerCase().startsWith(q);
  const prefix = [];
  const contains = [];
  for (const entry of entries) {
    const lower = entry.label.toLowerCase();
    if (lower.startsWith(q)) prefix.push(entry);
    else if (lower.includes(q) || kindHit) contains.push(entry);
  }
  return [...prefix, ...contains]
    .slice(0, cap)
    .map((entry) => ({ ...entry, parts: matchParts(entry.label) }));
}

/** A pick-one kind's choices that are not already the current value. */
function pickOne(kind, field, options, where) {
  return options
    .filter((o) => filters[field] !== o.id)
    .map((o) => ({
      key: `${kind}:${o.id}`,
      label: o.label,
      icon: o.icon,
      where,
      apply: () => {
        filters[field] = o.id;
      },
    }));
}

/** A ticked-list kind's values that are not ticked yet. */
function tickable(kind, field, items, icon, where) {
  return items
    .filter((item) => !(filters[field] || []).includes(item.value))
    .map((item) => ({
      key: `${kind}:${item.value}`,
      label: item.label,
      icon,
      where,
      apply: () => {
        filters[field] = [...(filters[field] || []), item.value];
      },
    }));
}

function tagEntries() {
  // A tag already held as Has is not offered again; a Lacks tag stays
  // offered, so Tab on it moves it across to Has.
  const taken = new Set(filters.tagFilter.map((t) => t.toLowerCase()));
  return catalog.value.tags
    .map((row) => ({ tag: String(row?.tag ?? ""), count: row?.count }))
    .filter(({ tag }) => tag && !taken.has(tag.toLowerCase()))
    .map(({ tag, count }) => ({
      key: `tag:${tag}`,
      label: tag,
      icon: "mdi-tag-outline",
      count,
      apply: () => addTag("has", tag),
      opposite: () => addTag("lacks", tag),
    }));
}

// Most pictures first, as the Filter menu's Workflow list; the count tells
// two workflows of one name apart.
function workflowEntries() {
  const ticked = new Set((filters.workflowFilter || []).map((w) => w.id));
  return catalog.value.workflows
    .filter((card) => !ticked.has(card.id))
    .sort((a, b) => (b.picture_count ?? 0) - (a.picture_count ?? 0))
    .map((card) => {
      const name = card.name || "Unnamed workflow";
      return {
        key: `workflow:${card.id}`,
        label: name,
        icon: "mdi-sitemap-outline",
        count: card.picture_count ?? 0,
        apply: () => {
          filters.workflowFilter = [
            ...(filters.workflowFilter || []),
            { id: card.id, name },
          ];
        },
      };
    });
}

function problemEntries() {
  const out = [];
  if (props.allPicturesView && !filters.noCharacterFilter) {
    out.push({
      key: "problem:no_character",
      label: "No character",
      field: "noCharacterFilter",
    });
  }
  if (props.allPicturesView && !filters.noSetFilter) {
    out.push({ key: "problem:no_set", label: "In no set", field: "noSetFilter" });
  }
  const flags = out.map(({ field, ...rest }) => ({
    ...rest,
    apply: () => {
      filters[field] = true;
    },
  }));
  const impossible = IMPOSSIBLE_OPTIONS.filter(
    (o) => !(filters.impossibleSources || []).includes(o.id),
  ).map((o) => ({
    key: `problem:${o.id}`,
    label: o.label,
    apply: () => {
      filters.impossibleSources = [...(filters.impossibleSources || []), o.id];
    },
  }));
  return [...flags, ...impossible].map((entry) => ({
    ...entry,
    icon: "mdi-alert-outline",
    where: "Problems",
  }));
}

const asItems = (names) =>
  names.map((m) => ({ value: m.value, label: m.name || modelLabel(m.value) }));

// Kinds in one fixed order, named as the Filter menu names them. Score and
// Tag confidence take a value as well as a name, so they stay in the menu.
const sections = computed(() => {
  if (!listVisible.value) return [];
  let index = 0;
  const out = [];
  const add = (label, items) => {
    if (!items.length) return;
    out.push({ label, items: items.map((row) => ({ row, index: index++ })) });
  };
  if (!query.value) {
    add(
      "Recent",
      searchStore.filteredSearchHistory.map((item) => ({
        key: `recent:${item}`,
        label: item,
        icon: "mdi-history",
        parts: [item, "", ""],
        apply: () => applyRecent(item),
      })),
    );
    return out;
  }
  const owner = !isReadOnly.value;
  add("Tags", matching("Tags", tagEntries(), MAX_TAG_ROWS));
  add(
    "Media",
    matching(
      "Media",
      pickOne("media", "mediaTypeFilter", MEDIA_OPTIONS, "Picture › Media"),
    ),
  );
  add(
    "Faces",
    matching(
      "Faces",
      pickOne("faces", "faceBboxFilter", FACE_OPTIONS, "Picture › Faces"),
    ),
  );
  add(
    "Stacks",
    matching(
      "Stacks",
      pickOne("stacks", "stackStateFilter", STACK_OPTIONS, "Picture › Stacks"),
    ),
  );
  if (owner) {
    add(
      "Sharing",
      matching(
        "Sharing",
        pickOne(
          "sharing",
          "sharedOnlyFilter",
          [{ id: true, label: "Shared", icon: "mdi-share-variant-outline" }],
          "Picture › Sharing",
        ),
      ),
    );
    add(
      "Imported from",
      matching(
        "Imported from",
        pickOne(
          "imported",
          "importedFromFilter",
          importFoldersStore.folders.map((folder) => ({
            id: folder.folder,
            label: folder.label || folder.folder,
            icon: "mdi-folder-download-outline",
          })),
          "Picture › Imported from",
        ),
      ),
    );
  }
  add(
    "Checkpoint",
    matching(
      "Checkpoint",
      tickable(
        "model",
        "comfyuiModelFilter",
        asItems(catalog.value.models),
        "mdi-package-variant-closed",
        "Content › Checkpoint",
      ),
    ),
  );
  add(
    "LoRA",
    matching(
      "LoRA",
      tickable(
        "lora",
        "comfyuiLoraFilter",
        asItems(catalog.value.loras),
        "mdi-puzzle-outline",
        "Content › LoRA",
      ),
    ),
  );
  if (owner) add("Workflow", matching("Workflow", workflowEntries()));
  // A share token cannot see the owner's hearts as its own.
  if (owner && !filters.favouriteOnlyFilter) {
    add(
      "Show",
      matching("Show", [
        {
          key: "favourites",
          label: "Favourites only",
          icon: "mdi-heart-outline",
          where: "Show",
          apply: () => {
            filters.favouriteOnlyFilter = true;
          },
        },
      ]),
    );
  }
  add("Problems", matching("Problems", problemEntries()));
  return out;
});

const rows = computed(() =>
  sections.value.flatMap((section) => section.items.map((item) => item.row)),
);
const currentIndex = computed(() =>
  Math.min(activeIndex.value, Math.max(rows.value.length - 1, 0)),
);

// ── Changing filters ────────────────────────────────────────────────────────
/** Put a tag in the Has or Lacks list, and take it out of the other one. */
function addTag(kind, tag) {
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
  token.remove();
}

function removeSelectedOrLast() {
  const targets = selectedTokens.value.length
    ? selectedTokens.value
    : tokens.value.slice(-1);
  targets.forEach(removeToken);
  selectionSize.value = 0;
}

/**
 * Close the field. `returnFocus` puts focus back on the resting field, for a
 * close the field itself made (Enter, Esc, a recent search); never after a
 * focusout, where the user has already moved focus elsewhere.
 */
function close(returnFocus = false) {
  open.value = false;
  selectionSize.value = 0;
  listDismissedFor.value = null;
  if (returnFocus) {
    nextTick(() => rootRef.value?.querySelector(".stf-rest")?.focus());
  }
}

function runTextSearch() {
  searchStore.commitSearch();
  inputRef.value?.blur();
  close(true);
}

function applyRecent(item) {
  searchStore.searchInput = item;
  runTextSearch();
}

/** Tab (or a click) adds the row; ⇧Tab adds its opposite, where it has one. */
function pickRow(row, opposite = false) {
  if (!row) return;
  const isRecent = row.key.startsWith("recent:");
  (opposite && row.opposite ? row.opposite : row.apply)();
  if (isRecent) return;
  searchStore.searchInput = "";
  selectionSize.value = 0;
  activeIndex.value = 0;
}

async function copyToken(token) {
  await tagClipboard.copyTags([token.tag]);
  copiedKey.value = token.key;
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
  splitTagText(pasted).forEach((tag) => addTag("has", tag));
}

function onKeydown(event) {
  const field = event.target;
  if (event.key === "Enter") {
    // Enter searches the words, whichever row is highlighted.
    event.preventDefault();
    runTextSearch();
    return;
  }
  if (event.key === "Tab") {
    // With nothing to add, Tab moves focus as usual: never a keyboard trap.
    // Recent searches (an empty field) are not filters: they take a click.
    if (!listVisible.value || !query.value || !rows.value.length) return;
    event.preventDefault();
    pickRow(rows.value[currentIndex.value], event.shiftKey);
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
    } else if (listVisible.value && query.value) {
      listDismissedFor.value = searchStore.searchInput;
    } else {
      field.blur();
      close(true);
    }
  }
}

// Focus leaving the field and its list closes it; the list's rows keep focus
// in the input (mousedown is prevented on them). The resting button blurs as
// opening swaps it for the input (Chrome fires focusout on the removal), so
// its focusout is not a leaving.
function onFocusOut(event) {
  if (!open.value) return;
  if (event.target?.classList?.contains("stf-rest")) return;
  if (rootRef.value?.contains(event.relatedTarget)) return;
  close();
}

watch(queryKey, () => {
  activeIndex.value = 0;
});

watch(open, (isOpen) => {
  emit("update:open", isOpen);
  if (!isOpen) return;
  loadCatalog();
  if (!isReadOnly.value) importFoldersStore.refresh();
});

function focusInput() {
  inputRef.value?.focus();
}

/** Open the field and put the caret in it (a click, or the F shortcut). */
async function focus() {
  open.value = true;
  await nextTick();
  focusInput();
}

onBeforeUnmount(() => {
  clearTimeout(copiedTimer);
});

defineExpose({ focus });
</script>

<style scoped>
/* At rest the 260 px toolbar field, growing into whatever room the bar has
   spare up to the 560 px it takes focused; focused it widens in place to
   560 px, giving way when the bar has less room. The list drops beneath it. */
.stf {
  position: relative;
  flex: 1 1 260px;
  width: 260px;
  max-width: 560px;
  min-width: 0;
  font-family: var(--font-ui);
  font-size: var(--text-sm);
}
.stf--open {
  flex-basis: 560px;
  width: 560px;
}
@container selbar (max-width: 700px) {
  .stf:not(.stf--open) {
    flex-basis: 160px;
    width: 160px;
  }
}
.stf-rest,
.stf-field {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  min-height: var(--control-h);
  padding: 0 var(--space-3);
  box-sizing: border-box;
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  cursor: text;
}
.stf-rest {
  height: var(--control-h);
  gap: var(--space-3);
  color: rgba(var(--v-theme-toolbar-text), var(--opacity-text-secondary));
  font: inherit;
  transition: border-color var(--dur-1) var(--ease-standard);
}
.stf-rest:hover {
  background-image: var(--hover-neutral);
}
/* A filtered view never passes for the whole library: the olive edge. */
.stf-rest--active {
  color: rgb(var(--v-theme-toolbar-text));
  border-color: var(--selected-ink);
}
.stf-rest--active .stf-icon {
  color: var(--selected-ink);
}
.stf-icon {
  flex-shrink: 0;
}
.stf-rest-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}
.stf-rest-kbd {
  flex-shrink: 0;
}
/* Open, the tokens stay on one line and scroll, so the bar keeps its height. */
.stf-field:focus-within {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
  box-shadow: var(--focus-glow);
}
.stf-field {
  overflow-x: auto;
  scrollbar-width: none;
  border-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-toolbar-text));
}
.stf-token {
  display: inline-flex;
  flex-shrink: 0;
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
.stf-token-body--static {
  cursor: default;
}
.stf-token-body:not(.stf-token-body--static):hover {
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
  flex: 1 0 120px;
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
.stf-drop {
  position: absolute;
  top: calc(100% + var(--space-2));
  left: 0;
  right: 0;
  z-index: var(--z-dropdown);
  display: flex;
  flex-direction: column;
  max-height: min(70vh, 560px);
}
.stf-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-2);
}
.stf-sec {
  padding: var(--space-3) var(--space-3) var(--space-2);
}
.stf-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h);
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-panel));
  cursor: pointer;
}
.stf-row:hover,
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
.stf-row-match {
  font-weight: var(--weight-semibold);
}
.stf-row-meta {
  margin-left: auto;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  white-space: nowrap;
}
.stf-row-keys {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  white-space: nowrap;
}
/* A count sits beside the name it counts; the Tab key stays at the right. */
.stf-row-meta--count {
  margin-left: 0;
}
.stf-row--words .stf-row-keys,
.stf-row-label + .stf-row-keys,
.stf-row-meta--count + .stf-row-keys {
  margin-left: auto;
}
.stf-foot {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
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
/* The field matches the bar buttons beside it on touch. */
@media (hover: none) and (pointer: coarse) {
  .stf-rest,
  .stf-field {
    min-height: 46px;
  }
}
</style>
