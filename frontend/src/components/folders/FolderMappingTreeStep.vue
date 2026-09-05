<script setup>
/**
 * Wizard step 2 ("MapTree") - name what each folder level is.
 *
 * Direction A from DECISIONS.md (pass 6): assign a whole level from its band,
 * override per row, because two folders at the same depth can legitimately
 * mean two different things. A folder's resolved kind is, in order: its own
 * row override, its level's default, then the Phase 2 signal's own proposal.
 *
 * The one selection rule: acting on a selected row acts on the whole
 * selection - its dropdown, or a digit while it is focused. Selection is
 * scoped to one level. The band's dropdown applies to the selection if one
 * exists, to the visible rows while the level is filtered, else to the level.
 *
 * Nothing is written from here. `next` hands the parent the assignments the
 * Preview step will show; `later` is "Drop this, organise later" - index
 * everything, map nothing. The root row (level 1, `relative_path` "") is
 * assignable: the commit service addresses the root as "".
 */
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from "vue";
import { VMenu } from "vuetify/components";

import { ALL_KINDS, JUST_A_FOLDER_KIND, kindByDigit, kindByValue, kindStyle } from "../../utils/folderMappingKinds";
import AppButton from "../widgets/AppButton.vue";

const props = defineProps({
  result: { type: Object, required: true },
});

const emit = defineEmits(["next", "later"]);

const ROW_CAP = 6;

const levels = computed(() => props.result.levels || []);

// depth -> kind, seeded from each level's own proposal where the signal was
// confident enough to have one. Reactive Map: Vue 3 tracks get/set on it.
const levelDefaults = reactive(new Map());
for (const level of levels.value) {
  if (level.proposal?.kind) levelDefaults.set(level.depth, level.proposal.kind);
}
// folder.id -> kind, the per-row override.
const overrides = reactive(new Map());
const filterText = reactive(new Map()); // depth -> string
const collapsed = reactive(new Set()); // depth

// Selection: one level at a time. `anchorId` is the Shift+click range start.
const selectedIds = reactive(new Set());
const selectedDepth = ref(null);
let anchorId = null;
const hoverLinked = ref(false);

const announcement = ref("");
const root = ref(null);
const sb = ref(0);

function resolvedKind(folder) {
  if (overrides.has(folder.id)) return overrides.get(folder.id);
  if (levelDefaults.has(folder.depth)) return levelDefaults.get(folder.depth);
  return folder.proposal?.kind ?? null;
}

function resolvedMatch(folder) {
  const kind = resolvedKind(folder);
  const proposal = folder.proposal;
  if (kind && proposal?.kind === kind && proposal?.match && proposal.match.entity_type !== "tag") {
    return proposal.match;
  }
  return null;
}

function visibleFolders(level) {
  const filter = (filterText.get(level.depth) || "").trim().toLowerCase();
  if (!filter) return level.folders;
  return level.folders.filter((f) => f.name.toLowerCase().includes(filter));
}

function filterActive(level) {
  return Boolean((filterText.get(level.depth) || "").trim());
}

function selectionCount(level) {
  return selectedDepth.value === level.depth ? selectedIds.size : 0;
}

function isSelected(folder) {
  return selectedIds.has(folder.id);
}

/** Distinct resolved kinds across a level's rows, in strip order. */
function levelKinds(level) {
  const present = new Set(level.folders.map(resolvedKind));
  return ALL_KINDS.filter((k) => present.has(k.value));
}

function bandKind(level) {
  const kinds = levelKinds(level);
  if (kinds.length > 1) return "mixed";
  return kinds[0]?.value ?? levelDefaults.get(level.depth) ?? null;
}

function bandLabel(level) {
  const selected = selectionCount(level);
  if (selected) return `Set ${selected} selected to`;
  if (filterActive(level)) return `Set these ${visibleFolders(level).length} to`;
  return "Set them all to";
}

function mixedStyle(level) {
  const stripes = levelKinds(level)
    .map((k, i) => `rgba(var(--v-theme-${k.color}), 0.16) ${i * 6}px ${(i + 1) * 6}px`)
    .join(", ");
  return { backgroundImage: `repeating-linear-gradient(45deg, ${stripes})` };
}

function menuGroups(candidates) {
  if (candidates?.length === 2) {
    const first = candidates.map(kindByValue).filter(Boolean);
    return [first, ALL_KINDS.filter((k) => !candidates.includes(k.value))];
  }
  return [ALL_KINDS];
}

function pluralOf(kind, n) {
  const k = kindByValue(kind) ?? JUST_A_FOLDER_KIND;
  return n === 1 ? k.label : k.plural;
}

function setRows(folders, kind) {
  for (const folder of folders) overrides.set(folder.id, kind);
  const n = folders.length;
  announcement.value = `${n} folder${n === 1 ? " is" : "s are"} now ${pluralOf(kind, n)}`;
}

/** The band's dropdown: selection, else the filtered rows, else the level. */
function applyToLevel(level, kind) {
  if (selectionCount(level)) {
    setRows(level.folders.filter(isSelected), kind);
  } else if (filterActive(level)) {
    setRows(visibleFolders(level), kind);
  } else {
    levelDefaults.set(level.depth, kind);
    for (const folder of level.folders) overrides.delete(folder.id);
    const n = level.folders.length;
    announcement.value = `Level ${level.depth}: all ${n} folder${n === 1 ? "" : "s"} ${n === 1 ? "is" : "are"} ${pluralOf(kind, n)}`;
  }
}

/** A row's dropdown or digit: the whole selection if the row is in it. */
function applyToRow(level, folder, kind) {
  setRows(isSelected(folder) ? level.folders.filter(isSelected) : [folder], kind);
}

function clearSelection() {
  selectedIds.clear();
  selectedDepth.value = null;
  anchorId = null;
}

function ensureLevel(level) {
  if (selectedDepth.value !== level.depth) {
    selectedIds.clear();
    selectedDepth.value = level.depth;
    anchorId = null;
  }
}

function toggleRow(level, folder) {
  ensureLevel(level);
  if (selectedIds.has(folder.id)) selectedIds.delete(folder.id);
  else selectedIds.add(folder.id);
  anchorId = folder.id;
  if (!selectedIds.size) selectedDepth.value = null;
}

function onRowClick(level, folder, event) {
  if (event.target.closest(".map-tree__kdd, .map-tree__menu")) return;
  ensureLevel(level);
  if (event.shiftKey && anchorId !== null) {
    const rows = visibleFolders(level);
    const a = rows.findIndex((f) => f.id === anchorId);
    const b = rows.indexOf(folder);
    if (a >= 0 && b >= 0) {
      selectedIds.clear();
      for (const f of rows.slice(Math.min(a, b), Math.max(a, b) + 1)) selectedIds.add(f.id);
      return;
    }
  }
  if (event.ctrlKey || event.metaKey) {
    toggleRow(level, folder);
    return;
  }
  selectedIds.clear();
  selectedIds.add(folder.id);
  anchorId = folder.id;
}

function onRowKeydown(level, folder, event) {
  if (event.target !== event.currentTarget) return;
  const digit = kindByDigit(event.key);
  if (digit) {
    event.preventDefault();
    applyToRow(level, folder, digit.value);
    return;
  }
  switch (event.key) {
    case "Escape":
      // Escape only ever deselects here; the wizard's dialog is persistent,
      // so nothing above us treats it as close.
      if (!selectedIds.size) return;
      event.preventDefault();
      clearSelection();
      return;
    case " ":
      event.preventDefault();
      toggleRow(level, folder);
      return;
    case "Enter":
      event.preventDefault();
      event.currentTarget.querySelector(".map-tree__kdd")?.click();
      return;
    case "ArrowDown":
    case "ArrowUp": {
      event.preventDefault();
      const rows = visibleFolders(level);
      const next = rows[rows.indexOf(folder) + (event.key === "ArrowDown" ? 1 : -1)];
      if (!next) return;
      if (event.shiftKey) {
        ensureLevel(level);
        selectedIds.add(folder.id);
        selectedIds.add(next.id);
      }
      const sibling = event.key === "ArrowDown" ? event.currentTarget.nextElementSibling : event.currentTarget.previousElementSibling;
      sibling?.focus();
    }
  }
}

function setFilter(level, value) {
  filterText.set(level.depth, value);
}

function toggleCollapsed(level) {
  if (collapsed.has(level.depth)) collapsed.delete(level.depth);
  else collapsed.add(level.depth);
  nextTick(measureSb);
}

const tally = computed(() => {
  const counts = new Map(ALL_KINDS.map((k) => [k.value, 0]));
  for (const level of levels.value) {
    for (const folder of level.folders) {
      const kind = resolvedKind(folder);
      if (counts.has(kind)) counts.set(kind, counts.get(kind) + 1);
    }
  }
  return counts;
});

function buildAssignments() {
  const rows = [];
  for (const level of levels.value) {
    for (const folder of level.folders) {
      const kind = resolvedKind(folder);
      if (!kind || kind === JUST_A_FOLDER_KIND.value) continue;
      const match = resolvedMatch(folder);
      const row = { relative_path: folder.relative_path, kind };
      if (match) row.match_id = match.id;
      rows.push(row);
    }
  }
  return rows;
}

// The band sits outside its level's scroll area and is offset by the
// scrollbar's real width, measured rather than typed.
function measureSb() {
  const el = root.value?.querySelector(".map-tree__tree");
  if (el) sb.value = el.offsetWidth - el.clientWidth;
}

onMounted(() => {
  measureSb();
  window.addEventListener("resize", measureSb);
});
onUnmounted(() => window.removeEventListener("resize", measureSb));
</script>

<template>
  <div ref="root" class="map-tree" :style="{ '--sb': sb + 'px' }">
    <p class="map-tree__sub">
      Say what each level of folders stands for. Your files stay where they are; nothing is written until you have
      reviewed it.
    </p>
    <div class="map-tree__strip" role="status" aria-label="What this makes">
      <span
        v-for="kind in ALL_KINDS"
        :key="kind.value"
        class="map-tree__chip"
        :class="{ 'map-tree__chip--folder': kind.value === JUST_A_FOLDER_KIND.value }"
        :style="kindStyle(kind.value)"
      >
        <v-icon size="15">{{ kind.icon }}</v-icon>
        {{ kind.plural }}
        <span v-if="kind.value !== JUST_A_FOLDER_KIND.value && tally.get(kind.value)" class="map-tree__chip-count">
          {{ tally.get(kind.value) }}
        </span>
      </span>
    </div>

    <p class="visually-hidden" aria-live="polite">{{ announcement }}</p>

    <div class="map-tree__levels">
      <section v-for="level in levels" :key="level.depth" class="map-tree__level" :data-depth="level.depth">
        <div class="map-tree__band">
          <button
            type="button"
            class="map-tree__caret"
            :aria-expanded="!collapsed.has(level.depth)"
            :aria-label="`Level ${level.depth}`"
            @click="toggleCollapsed(level)"
          >
            <v-icon size="15">{{ collapsed.has(level.depth) ? "mdi-chevron-right" : "mdi-chevron-down" }}</v-icon>
          </button>
          <span class="map-tree__band-title">
            Level {{ level.depth }} · {{ level.folder_count }} folder{{ level.folder_count === 1 ? "" : "s" }}
          </span>
          <input
            v-if="level.folders.length > ROW_CAP"
            class="map-tree__filter"
            type="text"
            placeholder="filter…"
            :aria-label="`Filter level ${level.depth}`"
            :value="filterText.get(level.depth) || ''"
            @input="setFilter(level, $event.target.value)"
          />
          <span class="map-tree__band-label">{{ bandLabel(level) }}</span>
          <v-menu :close-on-content-click="true">
            <template #activator="{ props: menuProps }">
              <button
                type="button"
                class="map-tree__kdd map-tree__kdd--band"
                :class="{
                  'map-tree__kdd--mixed': bandKind(level) === 'mixed',
                  'map-tree__kdd--none': !bandKind(level),
                  'map-tree__kdd--folder': bandKind(level) === JUST_A_FOLDER_KIND.value,
                  'map-tree__kdd--linked': selectionCount(level) > 0,
                  'map-tree__kdd--echo': hoverLinked && selectionCount(level) > 0,
                }"
                :style="bandKind(level) === 'mixed' ? mixedStyle(level) : kindStyle(bandKind(level))"
                aria-haspopup="menu"
                :aria-label="`${bandLabel(level)}, ${bandKind(level) === 'mixed' ? 'Mixed: ' + levelKinds(level).map((k) => k.label).join(', ') : kindByValue(bandKind(level))?.label ?? 'choose'}`"
                :title="level.proposal?.evidence?.[0]?.text"
                v-bind="menuProps"
                @mouseenter="selectionCount(level) && (hoverLinked = true)"
                @mouseleave="hoverLinked = false"
              >
                <v-icon v-if="bandKind(level) === 'mixed'" size="13">mdi-shuffle-variant</v-icon>
                <v-icon v-else-if="bandKind(level)" size="13">{{ kindByValue(bandKind(level)).icon }}</v-icon>
                <span class="map-tree__kdd-label">
                  {{ bandKind(level) === "mixed" ? "Mixed" : kindByValue(bandKind(level))?.label ?? "choose…" }}
                </span>
                <v-icon size="12">mdi-chevron-down</v-icon>
              </button>
            </template>
            <div class="map-tree__menu" role="menu">
              <template v-for="(group, gi) in menuGroups(level.proposal?.candidates)" :key="gi">
                <div v-if="gi" class="map-tree__menu-divider" />
                <button
                  v-for="kind in group"
                  :key="kind.value"
                  type="button"
                  role="menuitem"
                  class="map-tree__menu-item"
                  :data-kind="kind.value"
                  :style="kindStyle(kind.value)"
                  @click="applyToLevel(level, kind.value)"
                >
                  <v-icon size="14">{{ kind.icon }}</v-icon>
                  {{ kind.label }}
                  <kbd class="map-tree__menu-digit">{{ kind.digit }}</kbd>
                </button>
              </template>
            </div>
          </v-menu>
        </div>

        <div
          v-if="!collapsed.has(level.depth)"
          class="map-tree__tree"
          role="tree"
          aria-multiselectable="true"
          :aria-label="`Level ${level.depth}`"
        >
          <div
            v-for="folder in visibleFolders(level)"
            :key="folder.id"
            class="map-tree__row"
            :class="{ 'map-tree__row--sel': isSelected(folder) }"
            role="treeitem"
            tabindex="0"
            :aria-selected="isSelected(folder)"
            :aria-label="`${folder.name}, ${folder.picture_count.toLocaleString()} pictures`"
            aria-keyshortcuts="1 2 3 4 0"
            @click="onRowClick(level, folder, $event)"
            @keydown="onRowKeydown(level, folder, $event)"
          >
            <v-icon class="map-tree__lead" size="15">mdi-folder-outline</v-icon>
            <span class="map-tree__name">{{ folder.name }}</span>
            <span class="map-tree__count">{{ folder.picture_count.toLocaleString() }}</span>
            <v-menu :close-on-content-click="true">
              <template #activator="{ props: menuProps }">
                <button
                  type="button"
                  class="map-tree__kdd"
                  :class="{
                    'map-tree__kdd--none': !resolvedKind(folder),
                    'map-tree__kdd--folder': resolvedKind(folder) === JUST_A_FOLDER_KIND.value,
                    'map-tree__kdd--linked': isSelected(folder),
                    'map-tree__kdd--echo': hoverLinked && isSelected(folder),
                  }"
                  :style="kindStyle(resolvedKind(folder))"
                  aria-haspopup="menu"
                  :aria-label="`Kind, ${kindByValue(resolvedKind(folder))?.label ?? 'choose'}`"
                  :aria-description="isSelected(folder) ? `applies to the ${selectedIds.size} selected folders` : undefined"
                  :title="folder.proposal?.evidence?.[0]?.text"
                  v-bind="menuProps"
                  @mouseenter="isSelected(folder) && (hoverLinked = true)"
                  @mouseleave="hoverLinked = false"
                >
                  <v-icon v-if="resolvedKind(folder)" size="13">{{ kindByValue(resolvedKind(folder)).icon }}</v-icon>
                  <span class="map-tree__kdd-label">{{ kindByValue(resolvedKind(folder))?.label ?? "choose…" }}</span>
                  <v-icon size="12">mdi-chevron-down</v-icon>
                </button>
              </template>
              <div class="map-tree__menu" role="menu">
                <template v-for="(group, gi) in menuGroups(folder.proposal?.candidates)" :key="gi">
                  <div v-if="gi" class="map-tree__menu-divider" />
                  <button
                    v-for="kind in group"
                    :key="kind.value"
                    type="button"
                    role="menuitem"
                    class="map-tree__menu-item"
                    :data-kind="kind.value"
                    :style="kindStyle(kind.value)"
                    @click="applyToRow(level, folder, kind.value)"
                  >
                    <v-icon size="14">{{ kind.icon }}</v-icon>
                    {{ kind.label }}
                    <kbd class="map-tree__menu-digit">{{ kind.digit }}</kbd>
                  </button>
                </template>
              </div>
            </v-menu>
          </div>
        </div>
      </section>
    </div>

    <div class="map-tree__footer">
      <AppButton variant="secondary" @click="emit('later')">Drop this, organise later</AppButton>
      <span class="map-tree__footer-note">nothing is written yet</span>
      <AppButton variant="primary" @click="emit('next', buildAssignments())">Review and import</AppButton>
    </div>
  </div>
</template>

<style scoped>
.map-tree {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
}

/* Colour is spent the way LibraryLayoutDialog spends its level hues: the
   kind's hue is the edge, the icon and a `--level-wash` tint; the words are
   plain on-surface ink. `accent`/`primary`/`secondary`/`tertiary` are never
   small text (visual-language.md, "the values"): on the light theme the amber
   measures ~2.3:1 over its own wash. */
.map-tree__sub {
  margin: 0;
  padding: var(--space-4) var(--space-5) 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The tally strip: one chip per kind, each edged in its own colour. */
.map-tree__strip {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.map-tree__chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 32px;
  padding: 0 var(--space-4) 0 var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgb(var(--kind));
  background: rgba(var(--kind), var(--level-wash));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  white-space: nowrap;
}

.map-tree__chip .v-icon,
.map-tree__kdd .v-icon:first-child {
  color: rgb(var(--kind));
}

.map-tree__chip--folder {
  border-color: rgba(var(--kind), var(--opacity-text-secondary));
  background: rgba(var(--kind), calc(var(--level-wash) * var(--opacity-text-secondary)));
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.map-tree__chip-count {
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.map-tree__levels {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-bottom: var(--space-4);
}

/* A level band: chrome tone, full width, outside its level's scroll area and
   offset by the measured scrollbar width so its dropdown lines up with the
   rows' column. */
.map-tree__band {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 36px;
  padding: 0 calc(var(--space-5) + var(--sb, 0px)) 0 var(--space-5);
  background: rgb(var(--v-theme-panel));
  border-top: 1px solid rgb(var(--v-theme-divider));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-2xs);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.map-tree__level:first-child .map-tree__band {
  border-top: 0;
}

.map-tree__caret {
  display: inline-flex;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.map-tree__band-title {
  white-space: nowrap;
}

.map-tree__filter {
  height: 24px;
  min-width: 160px;
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-input-text));
  font-size: var(--text-xs);
}

.map-tree__band-label {
  margin-left: auto;
  text-transform: none;
  letter-spacing: 0;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* Six rows, then the level scrolls on its own; the gutter is reserved so the
   dropdown column never shifts. Thin bars, as style.css. */
.map-tree__tree {
  max-height: 204px;
  overflow-y: auto;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-surface), 0.22) transparent;
}

/* Rows: a grid so the count and the dropdown sit at one x on every level,
   behind the house selection rail (always present, always transparent). */
.map-tree__row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) 72px 152px;
  column-gap: var(--space-4);
  align-items: center;
  min-height: 34px;
  padding: 0 var(--space-5) 0 var(--space-6);
  border-left: var(--rail-w) solid transparent;
  font-size: var(--text-sm);
  cursor: default;
}

.map-tree__row:nth-child(even) {
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.map-tree__row:hover {
  background: var(--hover-wash);
}

.map-tree__row.map-tree__row--sel {
  background: var(--active-wash);
  border-left-color: var(--active-bar);
  color: var(--active-text);
}

.map-tree__row:focus-visible {
  box-shadow: inset var(--focus-ring);
}

.map-tree__lead {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.map-tree__row--sel .map-tree__lead {
  color: var(--active-bar);
}

.map-tree__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.map-tree__count {
  text-align: right;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.map-tree__row--sel .map-tree__count {
  color: inherit;
}

/* The kind dropdown: one width everywhere, edged in the kind's colour over its
   wash, the same box as a layout-level select. Hover doubles the wash, echo
   sits halfway. */
.map-tree__kdd {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  width: 152px;
  height: 26px;
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--kind));
  border-radius: var(--radius-sm);
  background: rgba(var(--kind), var(--level-wash));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  text-transform: none;
  letter-spacing: 0;
  white-space: nowrap;
  cursor: pointer;
}

.map-tree__kdd--echo {
  background: rgba(var(--kind), calc(var(--level-wash) * 1.5));
}

.map-tree__kdd:hover {
  background: rgba(var(--kind), calc(var(--level-wash) * 2));
}

.map-tree__kdd-label {
  flex: 1;
  min-width: 0;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
}

.map-tree__kdd--folder {
  border-color: rgba(var(--kind), var(--opacity-text-secondary));
  background: rgba(var(--kind), calc(var(--level-wash) * var(--opacity-text-secondary)));
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.map-tree__kdd--folder.map-tree__kdd--echo {
  background: rgba(var(--kind), calc(var(--level-wash) * 1.5 * var(--opacity-text-secondary)));
}

.map-tree__kdd--folder:hover {
  background: rgba(var(--kind), calc(var(--level-wash) * 2 * var(--opacity-text-secondary)));
}

.map-tree__kdd--none {
  border: 1px dashed rgb(var(--v-theme-border));
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-weight: var(--weight-medium);
}

.map-tree__kdd--none:hover {
  background: var(--hover-wash);
}

/* Mixed: neutral control, on-surface text, the level's kinds as 45° stripes
   (background-image is set inline from the kinds present). */
.map-tree__kdd--mixed {
  border-color: rgb(var(--v-theme-border));
  background-color: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-surface));
}

.map-tree__kdd--mixed.map-tree__kdd--echo,
.map-tree__kdd--mixed:hover {
  background-color: var(--hover-wash);
}

/* "These change together": a small arrow in the selection colour, in the
   gutter to the right of the dropdown, pointing at it. Absolutely positioned
   so the column keeps its width. */
.map-tree__kdd--linked::after {
  content: "";
  position: absolute;
  right: -11px;
  top: 50%;
  transform: translateY(-50%);
  width: 0;
  height: 0;
  border: 5px solid transparent;
  border-right: 6px solid var(--active-bar);
  border-left: 0;
}

.map-tree__menu {
  display: flex;
  flex-direction: column;
  min-width: 180px;
  padding: var(--space-2);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-panel));
  box-shadow: var(--elevation-3);
}

.map-tree__menu-divider {
  margin: var(--space-2) 0;
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.map-tree__menu-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
}

.map-tree__menu-item .v-icon {
  color: rgb(var(--kind));
}

.map-tree__menu-item:hover {
  background: var(--hover-wash);
}

.map-tree__menu-digit {
  margin-left: auto;
  min-width: 18px;
  padding: var(--space-1) var(--space-2);
  border: 1px solid currentColor;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  line-height: 1;
  text-align: center;
  opacity: var(--opacity-text-secondary);
}

.map-tree__footer {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  background: rgb(var(--v-theme-panel));
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.map-tree__footer-note {
  margin-left: auto;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
