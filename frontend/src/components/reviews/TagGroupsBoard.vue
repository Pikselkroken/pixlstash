<template>
  <div class="tgb" data-testid="tag-groups-board">
    <p class="tgb-caption">
      Drag a group to reorder; that order is the rail’s.
      <span class="tgb-scope-note"
        >Groups covers the whole library: the Project and Character scope does
        not apply here.</span
      >
    </p>

    <div class="tgb-body">
      <!-- Left: All, Ungrouped (the backlog), then the groups in rail order. -->
      <div class="tgb-groups" role="group" aria-label="Tag groups">
        <button
          type="button"
          class="tgb-row"
          :class="{ 'tgb-row--on': current === ALL }"
          :aria-pressed="current === ALL"
          @click="choose(ALL)"
        >
          <span class="tgb-row-name">All tags</span>
          <span class="tgb-row-count">{{ fmt(tags.length) }}</span>
        </button>

        <button
          type="button"
          class="tgb-row"
          :class="{
            'tgb-row--on': current === NONE,
            'tgb-row--drop': canDrop && dropKey === NONE,
          }"
          :aria-pressed="current === NONE"
          @click="choose(NONE)"
          @dragover="onTagsDragOver(NONE, $event)"
          @dragleave="onDragLeave(NONE, $event)"
          @drop="onTagsDrop(NONE, $event)"
        >
          <span class="tgb-row-name">Ungrouped</span>
          <span class="tgb-row-count">{{ fmt(ungroupedCount) }}</span>
          <span
            v-if="canDrop && dropKey === NONE"
            class="tgb-droplabel"
            role="status"
            >{{ dropLabel }}</span
          >
        </button>

        <div class="tgb-section-head">
          <span class="tgb-label">Groups</span>
          <span class="tgb-section-actions">
            <template v-if="!readOnly">
              <button
                ref="importBtnRef"
                type="button"
                class="tgb-text-btn"
                :disabled="importing"
                :aria-busy="importing ? 'true' : undefined"
                data-testid="tgb-import"
                @click="pickFile"
              >
                {{ importing ? "Importing…" : "Import…" }}
              </button>
              <input
                ref="fileRef"
                type="file"
                accept=".xlsx,.csv"
                hidden
                tabindex="-1"
                aria-hidden="true"
                data-testid="tgb-file"
                @change="onFilePicked"
              />
            </template>
            <button
              v-if="!readOnly"
              type="button"
              class="tgb-icon-btn"
              aria-label="New group"
              @click="startNew"
            >
              <Tooltip text="New group" activator="parent" :describe="false" />
              <v-icon size="14">mdi-plus</v-icon>
            </button>
          </span>
        </div>

        <div v-if="creating" class="tgb-new">
          <input
            ref="newRef"
            v-model="newName"
            class="tgb-input"
            type="text"
            placeholder="Group name"
            aria-label="New group name"
            @keydown.enter.prevent="commitNew"
            @keydown.escape.stop.prevent="cancelNew"
            @blur="cancelNew"
          />
        </div>

        <button
          v-for="g in tagGroups.groups"
          :key="g.id"
          type="button"
          class="tgb-row tgb-row--group"
          :class="{
            'tgb-row--on': current === g.id,
            'tgb-row--drop':
              (canDrop && dropKey === g.id) ||
              (dragKind === 'group' && groupTarget === g.id),
            'tgb-row--dragging': dragKind === 'group' && dragGroupId === g.id,
          }"
          :data-group-id="g.id"
          :draggable="!readOnly"
          :aria-pressed="current === g.id"
          :aria-keyshortcuts="readOnly ? undefined : 'Alt+ArrowUp Alt+ArrowDown'"
          @click="choose(g.id)"
          @keydown="onGroupKey(g, $event)"
          @dragstart="onGroupDragStart(g, $event)"
          @dragend="onDragEnd"
          @dragover="onGroupDragOver(g, $event)"
          @dragleave="onDragLeave(g.id, $event)"
          @drop="onGroupDrop(g, $event)"
        >
          <span class="tgb-row-name">{{ g.name }}</span>
          <span class="tgb-row-count">{{ fmt(groupCounts.get(g.id) || 0) }}</span>
          <span
            v-if="canDrop && dropKey === g.id"
            class="tgb-droplabel"
            role="status"
            >{{ dropLabel }}</span
          >
        </button>
      </div>

      <!-- Right: the chosen group's tags, with their picture counts. -->
      <section class="tgb-tags" aria-labelledby="tgb-tags-title">
        <div class="tgb-tags-head">
          <h3 id="tgb-tags-title" class="tgb-title">
            {{ currentName }}
            <span class="tgb-title-count">{{ fmt(currentTotal) }}</span>
          </h3>
          <select
            class="tgb-sort"
            :value="sortKey"
            aria-label="Sort the tags"
            @change="pickSort($event)"
          >
            <option value="pictures">Sort: Pictures</option>
            <option value="name">Sort: Name</option>
          </select>
        </div>

        <p v-if="current === NONE" class="tgb-hint">
          <template v-if="currentTotal">
            Tags no group claims yet. Select several and drag them onto a
            group, or use Move to group.
          </template>
          <template v-else>Every tag is in a group.</template>
        </p>

        <div v-if="loadError" class="tgb-empty" role="alert">
          {{ loadError }}
        </div>
        <div v-else-if="!visibleTags.length" class="tgb-empty">
          {{ needle ? "No tags match the filter." : "No tags here." }}
        </div>
        <div v-else class="tgb-tag-list" role="group" aria-label="Tags">
          <button
            v-for="t in visibleTags"
            :key="t.tag"
            type="button"
            class="tgb-tag"
            :class="{
              'tgb-tag--on': selected.has(t.tag),
              'tgb-tag--dragging': dragging.includes(t.tag),
            }"
            :aria-pressed="selected.has(t.tag)"
            :draggable="!readOnly"
            @click="onTagClick(t.tag, $event)"
            @dragstart="onTagDragStart(t.tag, $event)"
            @dragend="onDragEnd"
          >
            <span class="tgb-tag-name" :title="t.tag">{{ t.tag }}</span>
            <span class="tgb-tag-count">{{ fmt(t.count) }}</span>
          </button>
        </div>
      </section>
    </div>

    <!-- The selection pill: the app's selection bar, copied from its look.
         Read-only has no verbs, so it is not drawn at all. -->
    <div
      v-if="!readOnly && selected.size"
      class="tgb-pill selbar"
      role="toolbar"
      aria-label="Selected tags"
    >
      <span class="tgb-pill-count">{{ selectedLabel }}</span>
      <span class="selbar-sep" aria-hidden="true"></span>

      <span ref="moveWrapRef" class="tgb-move" @focusout="onMoveFocusOut">
        <button
          ref="moveBtnRef"
          type="button"
          class="tgb-pill-verb"
          aria-haspopup="menu"
          :aria-expanded="moveOpen ? 'true' : 'false'"
          @click="toggleMove"
          @keydown.down.prevent="openMove(true)"
        >
          Move to group
          <v-icon size="14">mdi-menu-down</v-icon>
        </button>
        <div
          v-if="moveOpen"
          ref="moveMenuRef"
          class="ctx-menu tgb-move-menu"
          role="menu"
          tabindex="-1"
          aria-label="Move to group"
          @keydown="onMoveMenuKey"
        >
          <button
            v-for="g in tagGroups.groups"
            :key="g.id"
            type="button"
            class="ctx-item"
            role="menuitem"
            @click="moveSelectionTo(g.id)"
          >
            {{ g.name }}
          </button>
          <button
            type="button"
            class="ctx-item"
            role="menuitem"
            @click="moveSelectionTo(null)"
          >
            Ungrouped
          </button>
        </div>
      </span>

      <!-- A disabled button takes no pointer events, so the reason rides on
           this wrapper (as TagHealthBoard's Start review does). -->
      <Tooltip :text="reviewTitle">
        <template #activator="{ props: tipProps }">
          <span class="tgb-review-wrap" v-bind="tipProps">
            <button
              type="button"
              class="tgb-pill-verb tgb-pill-verb--key"
              :disabled="!reviewable"
              @click="startReview"
            >
              Review
            </button>
          </span>
        </template>
      </Tooltip>
    </div>

    <!-- Mounted only while it asks: a closed dialog has nothing to draw. -->
    <TagImportDialog
      v-if="conflict"
      :open="Boolean(conflict)"
      :count="conflict?.plan.user_conflicts ?? 0"
      :sample="conflict?.plan.user_conflict_sample ?? []"
      @keep="answerConflict(false)"
      @move="answerConflict(true)"
      @close="answerConflict(null)"
    />
  </div>
</template>

<script setup>
// The Groups view of Tags to review (design: "Organising many tags"). Groups
// down the left in rail order, the chosen group's tags with their picture
// counts on the right, and a selection that can be dragged onto a group, moved
// from the pill, or reviewed (one tag at a time - a review covers one tag).
//
// Tags come from listTags(), the whole library. The board's Project and
// Character scope does not narrow this view; the filter field does.
import { computed, nextTick, onMounted, ref } from "vue";
import { listTags } from "../../api/tags";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTagGroupsStore } from "../../stores/useTagGroupsStore";
import { isReadOnly } from "../../utils/apiClient";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import Tooltip from "../widgets/Tooltip.vue";
import TagImportDialog from "./TagImportDialog.vue";

const props = defineProps({
  // The board's filter field: narrows the tag list here too.
  filter: { type: String, default: "" },
});
const emit = defineEmits(["start-review"]);

const tagGroups = useTagGroupsStore();
const readOnly = computed(() => isReadOnly.value);

// The chooser's keys: the two fixed rows, or a group id.
const ALL = "all";
const NONE = "none";

const tags = ref([]); // [{ tag, count }], the whole library
const loadError = ref("");
const choice = ref(ALL);
const sortKey = ref("pictures");

// A chosen group that was removed from under us falls back to All.
const current = computed(() =>
  typeof choice.value === "number" && !tagGroups.byId.has(choice.value)
    ? ALL
    : choice.value,
);

function choose(key) {
  choice.value = key;
}

function fmt(n) {
  return Number(n || 0).toLocaleString();
}

// --- Counts -----------------------------------------------------------------

// Group id -> number of library tags filed there; the Ungrouped count too.
const groupCounts = computed(() => {
  const counts = new Map();
  for (const t of tags.value) {
    const g = tagGroups.groupFor(t.tag);
    if (g) counts.set(g.id, (counts.get(g.id) || 0) + 1);
  }
  return counts;
});
const ungroupedCount = computed(
  () => tags.value.filter((t) => !tagGroups.groupFor(t.tag)).length,
);

const currentName = computed(() => {
  if (current.value === ALL) return "All tags";
  if (current.value === NONE) return "Ungrouped";
  return tagGroups.byId.get(current.value)?.name || "Group";
});
const currentTotal = computed(() => {
  if (current.value === ALL) return tags.value.length;
  if (current.value === NONE) return ungroupedCount.value;
  return groupCounts.value.get(current.value) || 0;
});

// --- The list ---------------------------------------------------------------

const needle = computed(() => props.filter.trim().toLowerCase());

const visibleTags = computed(() => {
  const inChoice = tags.value.filter((t) => {
    if (current.value === ALL) return true;
    const g = tagGroups.groupFor(t.tag);
    return current.value === NONE ? !g : g?.id === current.value;
  });
  const matched = needle.value
    ? inChoice.filter((t) => t.tag.toLowerCase().includes(needle.value))
    : inChoice;
  return matched.slice().sort((a, b) => {
    if (sortKey.value === "name") return a.tag.localeCompare(b.tag);
    return b.count - a.count || a.tag.localeCompare(b.tag);
  });
});

function pickSort(event) {
  sortKey.value = event.target.value === "name" ? "name" : "pictures";
  event.target.blur();
}

// --- Loading ----------------------------------------------------------------

async function loadTags() {
  try {
    const rows = await listTags();
    tags.value = Array.isArray(rows) ? rows : [];
    loadError.value = "";
  } catch (e) {
    console.error("Couldn't read the tag list for the Groups view", e);
    loadError.value = "Couldn't read the tags.";
  }
}

onMounted(async () => {
  await tagGroups.fetch();
  await loadTags();
});

// --- Selection --------------------------------------------------------------

// Tag names selected, in no particular order; `anchor` is the Shift-click base.
const selected = ref(new Set());
const anchor = ref(null);

function setSelection(names, nextAnchor = anchor.value) {
  selected.value = new Set(names);
  anchor.value = nextAnchor;
}

function clearSelection() {
  if (!selected.value.size) return false;
  setSelection([], null);
  return true;
}

function onTagClick(name, event) {
  const names = visibleTags.value.map((t) => t.tag);
  if (event.shiftKey && anchor.value != null && names.includes(anchor.value)) {
    const a = names.indexOf(anchor.value);
    const b = names.indexOf(name);
    const [lo, hi] = a < b ? [a, b] : [b, a];
    setSelection(names.slice(lo, hi + 1), anchor.value);
    return;
  }
  if (event.ctrlKey || event.metaKey) {
    const next = new Set(selected.value);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    selected.value = next;
    anchor.value = name;
    return;
  }
  setSelection([name], name);
}

const selectedLabel = computed(() => {
  const n = selected.value.size;
  return `${n} tag${n === 1 ? "" : "s"}`;
});

// --- Review -----------------------------------------------------------------

const reviewable = computed(() => selected.value.size === 1);
const reviewTitle = computed(() =>
  reviewable.value
    ? "Start a review of this tag"
    : "A review covers one tag at a time - select a single tag to review it",
);

function startReview() {
  if (!reviewable.value) return;
  emit("start-review", [...selected.value][0]);
}

// --- Move to group ----------------------------------------------------------

const moveOpen = ref(false);
const moveWrapRef = ref(null);
const moveBtnRef = ref(null);
const moveMenuRef = ref(null);

function openMove(focusFirst) {
  moveOpen.value = true;
  nextTick(() => {
    const first = moveMenuRef.value?.querySelector(".ctx-item");
    if (focusFirst && first) first.focus();
    else moveMenuRef.value?.focus();
  });
}

function toggleMove() {
  if (moveOpen.value) closeMove(true);
  else openMove(false);
}

// An inline menu, not a v-menu: it has no activator to hand focus back, so the
// opener takes it back itself.
function closeMove(restoreFocus) {
  moveOpen.value = false;
  if (restoreFocus) nextTick(() => moveBtnRef.value?.focus());
}

function onMoveFocusOut(event) {
  if (!moveOpen.value) return;
  if (moveWrapRef.value?.contains(event.relatedTarget)) return;
  closeMove(false);
}

function onMoveMenuKey(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeMove(true);
    return;
  }
  onMenuKeydown(event);
}

async function moveSelectionTo(groupId) {
  const names = [...selected.value];
  closeMove(true);
  if (!names.length) return;
  const moved = await tagGroups.moveTags(names, groupId);
  if (moved) clearSelection();
}

// --- Drag: tags onto a group ------------------------------------------------

const dragKind = ref(null); // "tags" | "group" | null
const dragging = ref([]); // tag names in flight
const dropKey = ref(null); // a group id, or NONE
const dragGroupId = ref(null);
const groupTarget = ref(null); // a group row a dragged group would land on

const canDrop = computed(() => !readOnly.value && dragKind.value === "tags");

function onTagDragStart(name, event) {
  if (readOnly.value) return;
  // Dragging an unselected tag drags just that tag, as a file manager does.
  if (!selected.value.has(name)) setSelection([name], name);
  dragging.value = [...selected.value];
  dragKind.value = "tags";
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    // Firefox starts no drag without data.
    event.dataTransfer.setData("text/plain", dragging.value.join(", "));
  }
}

function onDragEnd() {
  dragKind.value = null;
  dragging.value = [];
  dropKey.value = null;
  dragGroupId.value = null;
  groupTarget.value = null;
}

function onDragLeave(key, event) {
  if (event.currentTarget?.contains?.(event.relatedTarget)) return;
  if (dropKey.value === key) dropKey.value = null;
  if (groupTarget.value === key) groupTarget.value = null;
}

// Every dragged tag is filed under `key` already: nothing would move, so
// no wash, no label and no drop (the rail's group drop does the same).
function alreadyThere(key) {
  const target = key === NONE ? null : key;
  return dragging.value.every((n) => (tagGroups.groupFor(n)?.id ?? null) === target);
}

function onTagsDragOver(key, event) {
  if (!canDrop.value || alreadyThere(key)) return;
  event.preventDefault();
  dropKey.value = key;
}

async function onTagsDrop(key, event) {
  if (!canDrop.value || alreadyThere(key)) return;
  event.preventDefault();
  const names = [...dragging.value];
  onDragEnd();
  const moved = await tagGroups.moveTags(names, key === NONE ? null : key);
  if (moved) clearSelection();
}

// The drop label: what the drop will do, on the tooltip surface.
const dropLabel = computed(() => {
  const names = dragging.value;
  if (!names.length) return "";
  const target = dropKey.value === NONE ? null : dropKey.value;
  const where = tagGroups.nameOf(target);
  const pictures = names.reduce(
    (sum, n) => sum + (tags.value.find((t) => t.tag === n)?.count || 0),
    0,
  );
  if (names.length === 1) {
    return `${names[0]} → ${where} · on ${fmt(pictures)} pictures`;
  }
  return `${names.length} tags → ${where} · up to ${fmt(pictures)} pictures`;
});

// --- Drag and keys: groups reorder ------------------------------------------

const groupIds = computed(() => tagGroups.groups.map((g) => g.id));

function onGroupDragStart(group, event) {
  if (readOnly.value) return;
  dragKind.value = "group";
  dragGroupId.value = group.id;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", group.name);
  }
}

function onGroupDragOver(group, event) {
  if (readOnly.value) return;
  if (dragKind.value === "group") {
    if (group.id === dragGroupId.value) return;
    event.preventDefault();
    groupTarget.value = group.id;
    return;
  }
  if (canDrop.value && !alreadyThere(group.id)) {
    event.preventDefault();
    dropKey.value = group.id;
  }
}

function onGroupDrop(group, event) {
  if (dragKind.value === "group") {
    event.preventDefault();
    const from = dragGroupId.value;
    onDragEnd();
    if (from != null && from !== group.id) moveGroupTo(from, group.id);
    return;
  }
  onTagsDrop(group.id, event);
}

// Land `fromId` where `toId` sits: the dragged group takes the target's place.
function moveGroupTo(fromId, toId) {
  const ids = [...groupIds.value];
  const from = ids.indexOf(fromId);
  const to = ids.indexOf(toId);
  if (from < 0 || to < 0) return;
  ids.splice(from, 1);
  ids.splice(to, 0, fromId);
  tagGroups.reorder(ids);
}

// Keyboard path for reordering: Alt+Up / Alt+Down moves the focused group one
// place. Focus follows the group, so a second press keeps moving it.
function onGroupKey(group, event) {
  if (readOnly.value || !event.altKey) return;
  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
  event.preventDefault();
  const ids = groupIds.value;
  const at = ids.indexOf(group.id);
  const to = event.key === "ArrowUp" ? at - 1 : at + 1;
  if (at < 0 || to < 0 || to >= ids.length) return;
  moveGroupTo(group.id, ids[to]);
  nextTick(() => {
    const row = document.querySelector(
      `.tgb-row--group[data-group-id="${group.id}"]`,
    );
    row?.focus();
  });
}

// --- New group, named in place ----------------------------------------------

const creating = ref(false);
const newName = ref("");
const newRef = ref(null);

function startNew() {
  creating.value = true;
  newName.value = "";
  nextTick(() => newRef.value?.focus());
}

function cancelNew() {
  if (!creating.value) return;
  creating.value = false;
  newName.value = "";
}

async function commitNew() {
  const name = newName.value.trim();
  creating.value = false;
  newName.value = "";
  if (!name) return;
  const group = await tagGroups.createGroup(name);
  if (group) choose(group.id);
}

// --- Import a Danbooru tag file --------------------------------------------

// Preview first (a dry run answers the plan), then commit. Only a file with
// tags the user filed by hand in other groups asks: the dialog decides
// whether those move too.
const importing = ref(false);
const importBtnRef = ref(null);
const fileRef = ref(null);
// { file, plan } while the question is open.
const conflict = ref(null);

function pickFile() {
  if (!importing.value) fileRef.value?.click();
}

function focusImport() {
  nextTick(() => importBtnRef.value?.focus());
}

async function onFilePicked(event) {
  const file = event.target.files?.[0];
  // Cleared so picking the same file again still fires a change.
  event.target.value = "";
  if (!file) return;
  importing.value = true;
  try {
    const plan = await tagGroups.previewImport(file);
    if (!plan) return;
    if (plan.user_conflicts > 0) {
      conflict.value = { file, plan };
      return;
    }
    await applyImport(file, false);
  } finally {
    importing.value = false;
    if (!conflict.value) focusImport();
  }
}

async function applyImport(file, overwriteUser) {
  importing.value = true;
  try {
    const plan = await tagGroups.commitImport(file, overwriteUser);
    if (plan) useNoticeStore().success(importSummary(plan, file.name));
  } finally {
    importing.value = false;
  }
}

// `null` is a dismissal: nothing is filed, the question just closes.
async function answerConflict(overwriteUser) {
  const pending = conflict.value;
  conflict.value = null;
  if (overwriteUser !== null && pending) {
    await applyImport(pending.file, overwriteUser);
  }
  focusImport();
}

// The notice's sentence: the filing, then only the parts that are non-zero.
function importSummary(plan, name) {
  const parts = [`Filed ${fmt(plan.filed)} tags from ${name}`];
  const made = plan.groups_created?.length || 0;
  if (made) parts.push(`${fmt(made)} new group${made === 1 ? "" : "s"}`);
  if (plan.user_overwritten) {
    parts.push(`${fmt(plan.user_overwritten)} of your own filings moved`);
  }
  if (plan.skipped_not_in_library) {
    parts.push(`${fmt(plan.skipped_not_in_library)} not in your library`);
  }
  return parts.join(" · ");
}

// Esc, as the overlay routes it: an open Move menu closes first, then the
// selection clears. Returns whether a layer was spent.
function escape() {
  if (moveOpen.value) {
    closeMove(true);
    return true;
  }
  return clearSelection();
}

defineExpose({ clearSelection, escape, selected });
</script>

<style scoped>
/* Dark surface, as the rest of the board: the on-dark-surface ink and the
   same wash tokens TagHealthBoard uses. Olive marks selection and the drop
   target; amber is kept for actions. */
.tgb {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}
.tgb-caption {
  margin: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
.tgb-scope-note {
  display: block;
  margin-top: var(--space-1);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
.tgb-body {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
}
.tgb-groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}
.tgb-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--space-4);
  padding: 0 var(--space-3);
}
.tgb-label {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
.tgb-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
  cursor: pointer;
}
.tgb-icon-btn:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}
.tgb-section-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}
.tgb-text-btn {
  height: var(--control-h-sm);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  cursor: pointer;
}
.tgb-text-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}
.tgb-text-btn:disabled {
  opacity: var(--opacity-disabled);
  cursor: progress;
}
.tgb-new {
  padding: 0 var(--space-2);
}
.tgb-input {
  width: 100%;
  height: var(--control-h-sm);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-sm);
}
.tgb-row {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  width: 100%;
  min-height: var(--control-h-bar);
  padding: 0 var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
}
.tgb-row:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}
.tgb-row--on {
  background: var(--active-wash);
  border-color: var(--active-bar);
  font-weight: var(--weight-medium);
}
.tgb-row--drop {
  background: var(--active-wash);
  box-shadow: inset 0 0 0 1px var(--active-bar);
}
.tgb-row--dragging {
  opacity: 0.5;
}
.tgb-row-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tgb-row-count {
  flex-shrink: 0;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
.tgb-droplabel {
  position: absolute;
  left: 100%;
  top: 50%;
  transform: translateY(-50%);
  margin-left: var(--space-3);
  z-index: var(--z-floating);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgb(var(--v-theme-border));
  box-shadow: var(--elevation-3);
  font-size: var(--text-xs);
  white-space: nowrap;
  pointer-events: none;
}
.tgb-tags {
  min-width: 0;
}
.tgb-tags-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.tgb-title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-dark-surface));
}
.tgb-title-count {
  margin-left: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-regular);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
.tgb-sort {
  height: var(--control-h);
  max-width: 150px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color-scheme: dark;
}
.tgb-sort option {
  background-color: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
}
.tgb-hint,
.tgb-empty {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
.tgb-tag-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--space-1);
}
.tgb-tag {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-height: var(--control-h-bar);
  padding: 0 var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
}
.tgb-tag:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}
.tgb-tag--on {
  background: var(--active-wash);
  border-color: var(--active-bar);
}
.tgb-tag--dragging {
  opacity: 0.5;
}
.tgb-tag-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tgb-tag-count {
  flex-shrink: 0;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

/* The selection pill: the shared .selbar surface, floated at the foot of the
   board. The board scrolls, so it sits in the flow at the end, sticky. */
.tgb-pill {
  position: sticky;
  bottom: var(--space-5);
  align-self: center;
  z-index: var(--z-floating);
  width: max-content;
  margin-top: var(--space-4);
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
}
.tgb-pill-count {
  padding: 0 var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  font-variant-numeric: tabular-nums;
}
.tgb-pill-verb {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--control-h-bar);
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  border: 1px solid transparent;
  background: transparent;
  color: inherit;
  font-size: var(--text-sm);
  white-space: nowrap;
  cursor: pointer;
}
.tgb-pill-verb:hover:not(:disabled) {
  background: var(--hover-wash);
}
.tgb-pill-verb:disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}
.tgb-pill-verb--key {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  font-weight: var(--weight-medium);
}
.tgb-pill-verb--key:hover:not(:disabled) {
  background: rgb(var(--v-theme-accent));
  filter: brightness(1.05);
}
.tgb-move {
  position: relative;
  display: inline-flex;
}
.tgb-move-menu {
  position: absolute;
  bottom: calc(100% + var(--space-2));
  left: 0;
  min-width: 200px;
  z-index: var(--z-floating);
}
.tgb-review-wrap {
  display: inline-flex;
}
</style>
