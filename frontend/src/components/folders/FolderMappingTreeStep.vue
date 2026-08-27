<script setup>
/**
 * Wizard step 2 ("MapTree") - name what each folder level is.
 *
 * Direction A from DECISIONS.md: assign a whole level, override per row,
 * because two folders at the same depth can legitimately mean two different
 * things. A folder's resolved kind is, in order: its own row override, its
 * level's default, then the Phase 2 signal's own proposal - the same
 * override-beats-default-beats-signal chain the artboard's "press a number on
 * a row" promises.
 *
 * Nothing is written from here. `next` hands the parent the assignments the
 * Preview step will show and the commit will send; the level 1 row (the
 * scanned root itself) never gets one, matching the artboard's "the library
 * itself".
 */
import { computed, reactive } from "vue";
import { VMenu } from "vuetify/components";

import {
  ALL_KINDS,
  FACET_KINDS,
  JUST_A_FOLDER_KIND,
  kindByDigit,
  kindByValue,
} from "../../utils/folderMappingKinds";
import AppButton from "../widgets/AppButton.vue";

const props = defineProps({
  result: { type: Object, required: true },
});

const emit = defineEmits(["back", "next"]);

const VISIBLE_CAP = 8;

const levels = computed(() =>
  (props.result.levels || []).filter((level) => level.depth > 1),
);

// depth -> kind, seeded from each level's own proposal where the signal was
// confident enough to have one. Reactive Map: Vue 3 tracks get/set on it.
const levelDefaults = reactive(new Map());
for (const level of props.result.levels || []) {
  if (level.depth > 1 && level.proposal?.kind) {
    levelDefaults.set(level.depth, level.proposal.kind);
  }
}
// folder.id -> kind, the per-row override.
const overrides = reactive(new Map());

const filterText = reactive(new Map()); // depth -> string
const expandedLevels = reactive(new Set()); // depth

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

function kindLabel(folder) {
  // No resolved kind IS "Just a folder": `buildAssignments` and `summary` both
  // drop a null kind and a "folder" kind by the same test, so the control has
  // to say the thing the commit will do rather than a prompt to choose.
  const kind = resolvedKind(folder);
  return kindByValue(kind)?.label ?? kind ?? JUST_A_FOLDER_KIND.label;
}

function setLevelDefault(level, kindValue) {
  levelDefaults.set(level.depth, kindValue);
}

function setRowOverride(folder, kindValue) {
  overrides.set(folder.id, kindValue);
}

function resetRowOverride(folder) {
  overrides.delete(folder.id);
}

function onRowKeydown(folder, event) {
  if (event.target !== event.currentTarget) return;
  const kind = kindByDigit(event.key);
  if (!kind) return;
  event.preventDefault();
  setRowOverride(folder, kind.value);
}

function visibleFolders(level) {
  const filter = (filterText.get(level.depth) || "").trim().toLowerCase();
  if (filter) {
    return level.folders.filter((f) => f.name.toLowerCase().includes(filter));
  }
  if (expandedLevels.has(level.depth)) return level.folders;
  return level.folders.slice(0, VISIBLE_CAP);
}

function hiddenCount(level) {
  const filter = (filterText.get(level.depth) || "").trim();
  if (filter || expandedLevels.has(level.depth)) return 0;
  return Math.max(0, level.folders.length - VISIBLE_CAP);
}

function setFilter(level, value) {
  filterText.set(level.depth, value);
}

function expand(level) {
  expandedLevels.add(level.depth);
}

const summary = computed(() => {
  const projects = new Set();
  const people = new Set();
  const sets = new Set();
  const tags = new Set();
  for (const level of levels.value) {
    for (const folder of level.folders) {
      const kind = resolvedKind(folder);
      if (!kind || kind === "folder") continue;
      if (kind === "project") projects.add(folder.name);
      else if (kind === "person") people.add(folder.name);
      else if (kind === "set") sets.add(folder.name);
      else if (kind === "tag") tags.add(folder.name);
    }
  }
  return {
    project: projects.size,
    person: people.size,
    set: sets.size,
    tag: tags.size,
  };
});

function buildAssignments() {
  const rows = [];
  for (const level of levels.value) {
    for (const folder of level.folders) {
      const kind = resolvedKind(folder);
      if (!kind || kind === "folder") continue;
      const match = resolvedMatch(folder);
      const row = { relative_path: folder.relative_path, kind };
      if (match) row.match_id = match.id;
      rows.push(row);
    }
  }
  return rows;
}

function next() {
  emit("next", buildAssignments());
}
</script>

<template>
  <div class="map-tree">
    <div class="map-tree__header">
      <p class="map-tree__lead">
        from up to 20 pictures per folder - each row says what answered it
      </p>
      <div class="map-tree__summary">
        <span v-if="summary.project">{{ summary.project }} {{ summary.project === 1 ? "Project" : "Projects" }}</span>
        <span v-if="summary.person">{{ summary.person }} {{ summary.person === 1 ? "Person" : "People" }}</span>
        <span v-if="summary.set">{{ summary.set }} {{ summary.set === 1 ? "Set" : "Sets" }}</span>
        <span v-if="summary.tag">{{ summary.tag }} {{ summary.tag === 1 ? "Tag" : "Tags" }}</span>
      </div>
      <AppButton variant="secondary" size="sm" @click="emit('back')">
        Cancel
      </AppButton>
    </div>

    <div class="map-tree__levels">
      <section v-for="level in levels" :key="level.depth" class="map-tree__level">
        <div class="map-tree__level-header">
          <span class="map-tree__level-title">
            Level {{ level.depth }} · {{ level.folder_count }} folder{{ level.folder_count === 1 ? "" : "s" }}
          </span>
          <span v-if="level.proposal?.evidence?.[0]?.text" class="map-tree__level-evidence">
            {{ level.proposal.evidence[0].text }}
          </span>
          <input
            class="map-tree__filter"
            type="text"
            placeholder="filter…"
            :value="filterText.get(level.depth) || ''"
            @input="setFilter(level, $event.target.value)"
          />
          <div class="map-tree__level-kinds">
            <button
              v-for="kind in FACET_KINDS"
              :key="kind.value"
              type="button"
              class="map-tree__kind-chip"
              :class="{ 'map-tree__kind-chip--on': levelDefaults.get(level.depth) === kind.value }"
              @click="setLevelDefault(level, kind.value)"
            >
              <v-icon size="14">{{ kind.icon }}</v-icon>
              {{ kind.label }}
            </button>
            <button
              type="button"
              class="map-tree__kind-chip map-tree__kind-chip--ignore"
              :class="{ 'map-tree__kind-chip--on': levelDefaults.get(level.depth) === JUST_A_FOLDER_KIND.value }"
              @click="setLevelDefault(level, JUST_A_FOLDER_KIND.value)"
            >
              <v-icon size="14">{{ JUST_A_FOLDER_KIND.icon }}</v-icon>
              {{ JUST_A_FOLDER_KIND.label }}
            </button>
          </div>
        </div>

        <div class="map-tree__rows">
          <div
            v-for="folder in visibleFolders(level)"
            :key="folder.id"
            class="map-tree__row"
            :class="`map-tree__row--${resolvedKind(folder) || 'none'}`"
            tabindex="0"
            @keydown="onRowKeydown(folder, $event)"
          >
            <span class="map-tree__row-name">{{ folder.name }}</span>
            <span class="map-tree__row-count">{{ folder.picture_count.toLocaleString() }}</span>
            <span v-if="folder.proposal?.evidence?.[0]?.text" class="map-tree__row-evidence">
              {{ folder.proposal.evidence[0].text }}
            </span>
            <span v-else-if="folder.proposal?.candidates?.length" class="map-tree__row-evidence">
              one of: {{ folder.proposal.candidates.map((c) => kindByValue(c)?.label ?? c).join(", ") }}
            </span>

            <v-menu :close-on-content-click="true">
              <template #activator="{ props: menuProps }">
                <button type="button" class="map-tree__row-kind" v-bind="menuProps">
                  {{ kindLabel(folder) }} <v-icon size="14">mdi-chevron-down</v-icon>
                </button>
              </template>
              <div class="map-tree__row-menu">
                <button
                  v-for="kind in ALL_KINDS"
                  :key="kind.value"
                  type="button"
                  class="map-tree__row-menu-item"
                  @click="setRowOverride(folder, kind.value)"
                >
                  <v-icon size="14">{{ kind.icon }}</v-icon>
                  {{ kind.label }}
                  <span class="map-tree__row-menu-digit">{{ kind.digit }}</span>
                </button>
                <button
                  v-if="overrides.has(folder.id)"
                  type="button"
                  class="map-tree__row-menu-item map-tree__row-menu-item--reset"
                  @click="resetRowOverride(folder)"
                >
                  Use the level's default
                </button>
              </div>
            </v-menu>
          </div>

          <div v-if="hiddenCount(level) > 0" class="map-tree__more">
            {{ hiddenCount(level) }} more
            <button type="button" class="map-tree__show-all" @click="expand(level)">
              Show all {{ level.folder_count }}
            </button>
          </div>
        </div>
      </section>
    </div>

    <div class="map-tree__actions">
      <AppButton variant="primary" @click="next">Review and import</AppButton>
      <AppButton variant="secondary" @click="emit('back')">Cancel</AppButton>
    </div>
  </div>
</template>

<style scoped>
.map-tree {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-height: 0;
}

.map-tree__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.map-tree__lead {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.65);
}

.map-tree__summary {
  display: flex;
  gap: var(--space-3);
  margin-left: auto;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.72);
  align-self: center;
}

.map-tree__levels {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  overflow-y: auto;
}

.map-tree__level-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid rgb(var(--v-theme-border));
}

.map-tree__level-title {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.map-tree__level-evidence {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.6);
}

.map-tree__filter {
  height: 24px;
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-size: var(--text-xs);
  width: 120px;
}

.map-tree__level-kinds {
  display: flex;
  gap: var(--space-2);
  margin-left: auto;
  flex-wrap: wrap;
}

.map-tree__kind-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: 24px;
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill, 999px);
  background: transparent;
  color: inherit;
  font-size: var(--text-2xs);
  cursor: pointer;
}

.map-tree__kind-chip--on {
  border-color: rgb(var(--v-theme-accent));
  background: rgba(var(--v-theme-accent), 0.14);
  color: rgb(var(--v-theme-accent));
}

.map-tree__kind-chip--ignore.map-tree__kind-chip--on {
  border-color: rgba(var(--v-theme-on-background), 0.4);
  background: rgba(var(--v-theme-on-background), 0.08);
  color: rgb(var(--v-theme-on-background));
}

.map-tree__rows {
  display: flex;
  flex-direction: column;
}

/* A separator per row and a whole-row hover, because the name is on the left
   and its control is on the right: with neither, the eye cannot carry a line
   across the gap and you cannot tell which dropdown belongs to which folder. */
.map-tree__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-2);
  border-radius: var(--radius-sm);
  border-left: 2px solid transparent;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.6);
}

.map-tree__row:last-child {
  border-bottom-color: transparent;
}

.map-tree__row:hover {
  background: var(--hover-wash);
}

.map-tree__row:focus-visible {
  outline: 2px solid rgb(var(--v-theme-accent));
  outline-offset: -2px;
}

.map-tree__row--project {
  border-left-color: rgb(var(--v-theme-accent));
}
.map-tree__row--person {
  border-left-color: rgb(var(--v-theme-accent));
}
.map-tree__row--set {
  border-left-color: rgb(var(--v-theme-accent));
}
.map-tree__row--tag {
  border-left-color: rgb(var(--v-theme-accent));
}

.map-tree__row-name {
  min-width: 0;
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
}

.map-tree__row-count {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.6);
}

.map-tree__row-evidence {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), 0.55);
}

.map-tree__row-kind {
  flex-shrink: 0;
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: 24px;
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-size: var(--text-2xs);
  cursor: pointer;
}

.map-tree__row-menu {
  display: flex;
  flex-direction: column;
  min-width: 180px;
  padding: var(--space-2);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-panel));
  box-shadow: var(--elevation-3);
}

.map-tree__row-menu-item {
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

.map-tree__row-menu-item:hover {
  background: var(--hover-wash, rgba(var(--v-theme-on-panel), 0.06));
}

.map-tree__row-menu-digit {
  margin-left: auto;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), 0.5);
}

.map-tree__row-menu-item--reset {
  margin-top: var(--space-1);
  border-top: 1px solid rgb(var(--v-theme-border));
  padding-top: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), 0.65);
}

.map-tree__more {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.6);
}

.map-tree__show-all {
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-size: var(--text-2xs);
  height: 22px;
  padding: 0 var(--space-2);
  cursor: pointer;
}

.map-tree__actions {
  display: flex;
  gap: var(--space-3);
}
</style>
