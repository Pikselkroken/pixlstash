<template>
  <!-- `rowgroup`, not a bare div: the panel is a child of the grid's
       `treegrid`, where only rows and rowgroups are allowed, and the header
       below is a row of its own. -->
  <div
    :id="panelId"
    class="stack-panel"
    role="rowgroup"
    :style="notchStyle"
    data-testid="stack-panel"
  >
    <!-- One column has no centre worth pointing at, so the caret takes the
         shipped `--start` inset rather than a hand-copy of its 22px. -->
    <span
      class="tbm-caret"
      :class="columns > 1 ? 'stack-panel__notch' : 'tbm-caret--start'"
      aria-hidden="true"
    ></span>

    <div class="stack-panel__header" role="row" aria-level="2">
      <div role="gridcell">
        <div class="stack-panel__bar" role="toolbar" :aria-label="toolbarName">
          <span class="stack-panel__name">{{ name }}</span>
          <span class="stack-panel__count num">{{ countLabel }}</span>
          <span v-if="pending" class="stack-panel__pending">{{ pending }}</span>
          <span class="stack-panel__spacer"></span>
          <Segmented
            :options="VIEW_OPTIONS"
            :model-value="view"
            variant="icon-label"
            aria-label="Show the stack as"
            @update:model-value="prefs.setStackView"
          />
          <AppButton
            variant="ghost"
            size="sm"
            icon-left="close"
            icon-only
            tooltip="Close this stack"
            @click="emit('close')"
          />
        </div>
      </div>
    </div>

    <!-- `presentation`, so the member rows below are exposed to the
         rowgroup above rather than to an unroled div, which would break the
         chain a treegrid needs: rowgroup owns rows, rows own gridcells. -->
    <div v-if="view === 'grid'" class="stack-panel__grid" role="presentation">
      <div
        v-for="(member, index) in members"
        :id="`${panelId}-row-${member.key}`"
        :key="member.key"
        class="stack-panel__member"
        role="row"
        aria-level="2"
        :aria-posinset="index + 1"
        :aria-setsize="size || members.length"
        :aria-selected="selectedKeys.includes(member.key)"
        :tabindex="cursorKey === member.key ? 0 : -1"
        :data-key="member.key"
        @click="emit('select', member.key, $event)"
        @contextmenu.prevent="openMenu(member, index, $event)"
      >
        <div class="stack-panel__cell" role="gridcell">
          <WorkflowCard
            :card="member"
            member
            :selected="selectedKeys.includes(member.key)"
          />
          <!-- The cover is what the others are compared against, so it is the
               one member whose special row is empty; without the flag its row
               reads as "this one differs by nothing". -->
          <span v-if="index === 0" class="stack-cover-flag">Cover</span>
          <AppButton
            class="stack-panel__more"
            variant="ghost"
            size="sm"
            icon-left="dots-horizontal"
            icon-only
            tabindex="-1"
            tooltip="What you can do with this workflow"
            @click.stop="openMenu(member, index, $event)"
          />
        </div>
      </div>
    </div>

    <!-- List. The SAME level-2 rows with the same gridcells, drawn on a CSS
         grid rather than a `<table>`: the panel already sits inside the view's
         `treegrid`, and a table here would put a second, conflicting grid
         structure inside it. The header row is one of the treegrid's rows too,
         so its cells are `columnheader`. -->
    <div v-else class="stack-panel__list" role="presentation">
      <div class="stack-panel__listhead" role="row" aria-level="2">
        <span
          v-for="column in COLUMNS"
          :key="column.id"
          class="section-label"
          :class="column.cls"
          role="columnheader"
          >{{ column.label }}</span
        >
      </div>
      <div
        v-for="(member, index) in members"
        :id="`${panelId}-row-${member.key}`"
        :key="member.key"
        class="stack-panel__member stack-panel__row"
        role="row"
        aria-level="2"
        :aria-posinset="index + 1"
        :aria-setsize="size || members.length"
        :aria-selected="selectedKeys.includes(member.key)"
        :aria-label="rowName(member, index)"
        :tabindex="cursorKey === member.key ? 0 : -1"
        :data-key="member.key"
        @click="emit('select', member.key, $event)"
        @contextmenu.prevent="openMenu(member, index, $event)"
      >
        <!-- Everything the row draws is `aria-hidden`: the row's own label
             reads all of it, including whatever "+N" clipped, exactly as the
             card does in Grid. -->
        <span class="stack-panel__ident" role="gridcell" aria-hidden="true">
          <!-- Up to three thumbnails at 56×36, `alt=""`: they are the same
               pictures the card shows and the row is named by its workflow, so
               they carry nothing a reader would otherwise miss. -->
          <!-- The cover's own arrangement one size down, which since #1456
               means ONE CELL PER PICTURE: the card divides its cover by what
               the member actually has, and a row drawing a fixed three would
               put its one picture beside two painted boxes and disagree with
               the same stack's Grid view. `v-if` on the image, not `v-show`:
               an `<img>` with no `src` is a broken-image glyph in some
               browsers, and empty ones would make any assertion about the
               row's pictures vacuous. -->
          <span
            class="stack-panel__thumbs"
            :class="`stack-panel__thumbs--${thumbsOf(member).length}`"
          >
            <span
              v-for="(src, i) in thumbsOf(member)"
              :key="i"
              class="stack-panel__thumb"
            >
              <img v-if="src" :src="src" alt="" loading="lazy" />
            </span>
          </span>
          <span class="stack-panel__rowname">{{ member.name }}</span>
          <span v-if="index === 0" class="stack-panel__pill">Cover</span>
        </span>
        <!-- A chip only when it DIFFERS from the cover's: a column repeating
             one model name down every row says nothing about the stack, and
             the whole point of List is what is not shared. -->
        <span class="stack-panel__ckpt" role="gridcell" aria-hidden="true">
          <ChipRow
            v-if="checkpointChips(member, index).length"
            :items="checkpointChips(member, index)"
          />
        </span>
        <span class="stack-panel__facts" role="gridcell" aria-hidden="true">
          <ChipRow :items="factChips(member, index)" />
        </span>
        <span class="stack-panel__num num" role="gridcell" aria-hidden="true">{{
          member.picture_count ?? 0
        }}</span>
        <span class="stack-panel__num num" role="gridcell" aria-hidden="true">{{
          member.rating > 0 ? member.rating.toFixed(1) : "—"
        }}</span>
        <span class="stack-panel__end" role="gridcell">
          <AppButton
            variant="ghost"
            size="sm"
            icon-left="dots-horizontal"
            icon-only
            tabindex="-1"
            tooltip="What you can do with this workflow"
            @click.stop="openMenu(member, index, $event)"
          />
        </span>
      </div>
    </div>

    <!-- ONE menu for both views and for both ways in. The items are built from
         `MENU` below rather than written out per view, which is the only thing
         that makes the two genuinely identical rather than identical today. -->
    <v-menu
      v-model="menuOpen"
      :target="menuAt"
      :close-on-content-click="true"
      location="bottom end"
      origin="top start"
      :offset="2"
    >
      <div class="ctx-menu" role="menu" tabindex="-1" data-testid="member-menu">
        <button
          v-for="item in menuItems"
          :key="item.id"
          class="ctx-item"
          type="button"
          role="menuitem"
          :disabled="item.disabled"
          :data-item="item.id"
          @click="item.run()"
        >
          <v-icon class="ctx-icon">{{ `mdi-${item.icon}` }}</v-icon>
          <span class="ctx-label-text">{{ item.label }}</span>
        </button>
      </div>
    </v-menu>
  </div>
</template>

<script setup>
/**
 * One open workflow stack (v1.12 Workflows & Recipes, F1a and F2).
 *
 * The panel is ONE element in the card grid at `grid-column: 1 / -1`, placed
 * after the last card of the stack's row, so opening it pushes the later rows
 * down rather than floating over them. The caret points back at the card that
 * opened it: `--notch` is derived from that card's COLUMN INDEX every time the
 * container resizes, never stored as a pixel offset, which is what would leave
 * it pointing at the wrong card after a resize.
 *
 * Neutral ground on purpose — `--panel`, a hairline border, `--radius-lg`. The
 * open card is marked by its rotated ▸ alone; an olive wash over the band said
 * "selected" about six cards nobody had selected.
 *
 * **Grid | List** (F2) is remembered for every stack, not per stack, in
 * `useWorkflowPrefsStore`: the switch answers "how do I read a stack" rather
 * than anything about the one in front of you. The view owns the selection and
 * the cursor, so switching keeps both and moves nothing in the grid.
 *
 * **List is not a `<table>`.** The panel is already inside the view's
 * `treegrid`; a table would nest a second grid structure in it and break the
 * rowgroup → row → gridcell chain the cursor's `aria-owns` depends on. The
 * columns are a CSS grid, shared by the header row and the member rows so the
 * two cannot drift.
 *
 * *Unstack all* and the hidden-member count are still not here, and both are
 * blocked on the READ side: `GET /workflows/cards` drops hidden cards BEFORE
 * grouping, so a hidden member is in neither `member_keys` nor the payload at
 * any level. Hide is offered because it is a write the panel can make; Unhide
 * is not, because nothing here can list what was hidden.
 */
import { computed, nextTick, ref } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import { workflowCoverUrl } from "../../api/workflows";
import { useWorkflowPrefsStore } from "../../stores/useWorkflowPrefsStore";
import {
  cardAccessibleName,
  checkpointModel,
  factChips as cardFactChips,
} from "../../utils/workflowCard";
import AppButton from "../widgets/AppButton.vue";
import ChipRow from "../widgets/ChipRow.vue";
import Segmented from "../widgets/Segmented.vue";
import WorkflowCard from "../widgets/WorkflowCard.vue";

const props = defineProps({
  /** The panel's DOM id — the stack card's `aria-controls`. */
  panelId: { type: String, required: true },
  /** The stack's name, as the cover card carries it. */
  name: { type: String, default: "" },
  /** The stack's cards, cover first — the cover alone while the rest load. */
  members: { type: Array, default: () => [] },
  /** How many cards the stack HAS, which is not how many have arrived. */
  size: { type: Number, default: 0 },
  /** A member request is still running. */
  loading: { type: Boolean, default: false },
  /** The grid's current column count, from its ResizeObserver. */
  columns: { type: Number, default: 1 },
  /** The stack card's 0-based column in that grid. */
  columnIndex: { type: Number, default: 0 },
  /** Keys of the selected cards, top-level and member alike. */
  selectedKeys: { type: Array, default: () => [] },
  /** The roving cursor's key, when it is inside this panel. */
  cursorKey: { type: String, default: "" },
  /** False while the stack has no id to address a reorder by. */
  canReorder: { type: Boolean, default: false },
});

const emit = defineEmits([
  "close",
  "select",
  "make-cover",
  "move",
  "unstack",
  "hide",
]);

const prefs = useWorkflowPrefsStore();
const view = computed(() => prefs.stackView);

const VIEW_OPTIONS = [
  { id: "grid", label: "Grid", icon: "view-grid-outline" },
  { id: "list", label: "List", icon: "view-list" },
];

/** The List columns, in one place: the header row and the rows share them. */
const COLUMNS = [
  { id: "workflow", label: "Workflow", cls: "stack-panel__ident" },
  { id: "checkpoint", label: "Checkpoint", cls: "stack-panel__ckpt" },
  { id: "differs", label: "Differs by", cls: "stack-panel__facts" },
  { id: "pictures", label: "Pictures", cls: "stack-panel__num" },
  { id: "rating", label: "Rating", cls: "stack-panel__num" },
  { id: "menu", label: "", cls: "stack-panel__end" },
];

// The stack's own size, not how many cards have arrived: counting what is on
// screen makes the header read "1 workflow" over a stack of six for as long as
// the member requests take — and permanently, if one of them fails.
const countLabel = computed(() => {
  const total = props.size || props.members.length;
  return total === 1 ? "1 workflow" : `${total} workflows`;
});

/** Said only while some of the stack is still missing from the panel. */
const pending = computed(() => {
  const missing = (props.size || props.members.length) - props.members.length;
  if (missing <= 0) return "";
  return props.loading ? "reading the rest…" : `${missing} could not be read`;
});

const toolbarName = computed(() => `${props.name || "Stack"} stack`);

/**
 * One member's cover URLs, joined the way `WorkflowCard` joins its own.
 *
 * `covers` arrives API-RELATIVE (`/pictures/thumbnails/{id}.webp?v=…`) and an
 * `<img src>` bypasses Axios, so nothing prepends `/api/v1` and nothing
 * appends the share token. Used verbatim the browser asks the PAGE origin for
 * a path no route serves and every thumbnail in the list is a broken image —
 * the bug F1b fixed for the card, which this column reintroduced by reading
 * the payload directly. `workflowCoverUrl` is the api layer's one spelling of
 * that join; a second one here is exactly the drift it exists to prevent.
 */
function coversOf(member) {
  return (member.covers ?? [])
    .filter(Boolean)
    .slice(0, 3)
    .map(workflowCoverUrl);
}

/**
 * The cells one row's strip draws: one per picture, as the card's cover does.
 *
 * Empty entries are dropped above rather than counted, because
 * `workflowCoverUrl` does not guard them - it joins one into the truthy
 * `/api/v1null` - and a cell it cannot fill is the thing #1456 removed.
 * A member whose covers have not arrived keeps the arrangement its pictures
 * are about to land in; one with no pictures at all keeps a single cell, so
 * the strip is still a picture-shaped slot in the row.
 */
function thumbsOf(member) {
  const covers = coversOf(member);
  return covers.length
    ? covers
    : Array(Math.min(member.picture_count || 1, 3)).fill("");
}

/**
 * A List row's accessible name, plus the one thing the row adds: its place.
 *
 * **The cover's `differs_by` is dropped**, for the reason its two chip cells
 * are left blank: it is the UNION of what the OTHER rows differ by, carried
 * on the cover because the grid draws one tile per stack. Every cell but ⋯ is
 * `aria-hidden`, so this string is the whole of what a screen reader hears —
 * and left in, it tells a reader the cover differs from itself by the very
 * things its siblings differ from it by, while the row in front of a sighted
 * reader says nothing of the kind.
 */
function rowName(member, index) {
  if (index !== 0) return cardAccessibleName(member, { member: true });
  const name = cardAccessibleName(
    { ...member, differs_by: [] },
    { member: true },
  );
  return `${name}, the stack's cover`;
}

/**
 * The checkpoint chip, drawn only when it differs from the cover's.
 *
 * The cover's own row never draws one: it is what the column is measured
 * against, so a chip there would read as a difference from itself.
 */
function checkpointChips(member, index) {
  const model = checkpointModel(member);
  // `name` is nullable — a recipe whose asset names were forgotten is one of
  // the states the card shape carries — and a chip with no label is a
  // bordered glyph reading "differs, by nothing in particular". Two forgotten
  // names are not evidence of sharing a model either, so both are dropped
  // rather than compared.
  if (!model?.name || index === 0) return [];
  const cover = checkpointModel(props.members[0]);
  if (cover?.name === model.name) return [];
  return [{ key: "ckpt", label: model.name, icon: "cube-outline" }];
}

/**
 * The member's difference chips — and none at all on the cover's row.
 *
 * The cover card carries the UNION of what its members differ by: it is what
 * the grid draws, and one tile has to say what is under it. Inside the panel
 * that makes the first row list its siblings' differences as though they were
 * its own, in the one column whose whole job is what is NOT shared — and
 * beside a Checkpoint cell deliberately left blank for that same reason. The
 * Cover pill is what the row says instead.
 */
function factChips(member, index) {
  return index === 0 ? [] : cardFactChips(member);
}

// ── The member menu ───────────────────────────────────────────────────────
//
// Anchored to the pointer, which is what a context menu is, and opened from
// both ⋯ and right-click so the two cannot offer different things.

const menuOpen = ref(false);
const menuAt = ref([0, 0]);
const menuMember = ref(null);
const menuIndex = ref(0);

/**
 * Open the menu on one member, at a point.
 *
 * **Closed first, and reopened on the next tick**, even when it is already
 * open. A right button press fires `mousedown` and `contextmenu` but no
 * `click`, and Vuetify's click-outside closes on `click` — so right-clicking
 * a second row leaves the menu open where it was, while `menuMember` becomes
 * the row underneath the pointer: *Unstack* and *Hide* would then act on a
 * card the menu is not pointing at. Its location strategy re-reads `target`
 * on open and not on change, so reopening is also the only thing that moves
 * it.
 */
async function openMenu(member, index, event) {
  emit("select", member.key, {});
  menuOpen.value = false;
  await nextTick();
  menuMember.value = member;
  menuIndex.value = index;
  menuAt.value = [event.clientX, event.clientY];
  menuOpen.value = true;
}

/**
 * Open the menu on the member `key`, from the keyboard.
 *
 * The grid owns Tab and both ⋯ buttons sit at `tabindex="-1"`, so without
 * this the menu — and with it *Unstack* and *Hide* — has no keyboard route at
 * all. Anchored on the row's own box rather than a pointer there is none of,
 * which is where Shift+F10 is supposed to put it.
 */
function openMenuAt(key) {
  const index = props.members.findIndex((member) => member.key === key);
  if (index < 0) return;
  const row = document.getElementById(`${props.panelId}-row-${key}`);
  const box = row?.getBoundingClientRect();
  openMenu(props.members[index], index, {
    clientX: box ? box.left + 16 : 0,
    clientY: box ? box.bottom : 0,
  });
}

defineExpose({ openMenuAt });

/**
 * The menu, built once from the member under the pointer.
 *
 * *Run…* and *Rename* are the design's other two items and are not here.
 * *Rename* wants somewhere to type, which this panel still has nowhere for.
 *
 * *Run…* no longer wants for a surface: F5 (#1407) made running a popup,
 * mounted in `App.vue` and opened for a card by the Workflow tab on this very
 * screen (`runDialogStore.openRun({kind: "card", workflowKey})`). What it
 * wants now is the member menu's own decision - a member is a stack's other
 * card, so the popup would open on that key rather than on the cover's - and
 * that belongs to whoever adds the item rather than to the step that built
 * the popup. Neither is drawn disabled, because an item that has never done
 * anything is not a control a reader should have to discount.
 */
const menuItems = computed(() => {
  const member = menuMember.value;
  if (!member) return [];
  const index = menuIndex.value;
  const last = props.members.length - 1;
  return [
    {
      id: "cover",
      label: "Make it the cover",
      icon: "star",
      disabled: !props.canReorder || index === 0,
      run: () => emit("make-cover", member.key),
    },
    {
      id: "earlier",
      label: "Move earlier",
      icon: "arrow-up",
      disabled: !props.canReorder || index === 0,
      run: () => emit("move", member.key, -1),
    },
    {
      id: "later",
      label: "Move later",
      icon: "arrow-down",
      disabled: !props.canReorder || index >= last,
      run: () => emit("move", member.key, 1),
    },
    {
      id: "unstack",
      label: "Unstack",
      icon: "call-split",
      run: () => emit("unstack", member.key),
    },
    {
      id: "hide",
      label: "Hide",
      icon: "eye-off-outline",
      run: () => emit("hide", member.key),
    },
  ];
});

// The centre of the column the stack card sits in, as a share of the panel's
// own width — the panel spans every column, so the two grids share an origin.
// At one column the caret falls back to the shipped `--start` class instead,
// so nothing here hand-copies that rule's inset.
const notchStyle = computed(() => {
  const style = { "--wf-columns": props.columns };
  if (props.columns > 1) {
    style["--notch"] = `${((props.columnIndex + 0.5) / props.columns) * 100}%`;
  }
  return style;
});
</script>

<style scoped>
.stack-panel {
  position: relative;
  grid-column: 1 / -1;
  margin-bottom: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
}

/* The shipped `.tbm-caret`, aimed by `--notch` rather than by a modifier: the
   column it points at is a number this component computes, not one of the four
   fixed insets the toolbar menus use. `translateX(-50%)` re-centres it on that
   point; the rotation is the shipped one and has to be restated because the
   two live in one `transform`. */
.stack-panel__notch {
  left: var(--notch);
  transform: translateX(-50%) rotate(45deg);
}

.stack-panel__bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--bar-height);
  padding: 0 var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.stack-panel__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.stack-panel__count {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.stack-panel__spacer {
  flex: 1;
}

.stack-panel__pending {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  font-style: italic;
}

/* The SAME column count as the grid above, taken from `--wf-columns` rather
   than recomputed with `auto-fill`: the panel's padding narrows the track, and
   an auto-fill here would quietly drop to one column fewer — at which point a
   member's index stops naming the column it is drawn in and Down lands
   somewhere else. `minmax(0, 1fr)` so a wide card cannot push the track out. */
.stack-panel__grid {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  gap: var(--space-4);
  padding: var(--space-4);
}

/* No focus rule: `style.css` paints the one ring, and a row that stays silent
   gets it. */
.stack-panel__cell {
  position: relative;
  border-radius: var(--radius-md);
}

/* The selection mark is the CARD's own (`WorkflowCard.vue`,
   `.wf-card--selected`) - the same mark the top-level cards wear, so a mixed
   selection reads as one. It was here, on the cell, and never showed: the
   card paints an opaque surface across the whole cell and covered it. */

/* ⋯ sits opposite the card's own ⓘ, which is pinned bottom-right. Off the tab
   order like every other control inside a card: the grid's roving cursor owns
   Tab, and the row's right-click opens the same menu. */
.stack-panel__more {
  position: absolute;
  right: var(--space-2);
  top: var(--space-2);
  z-index: 1;
}

/* ── List ──────────────────────────────────────────────────────────────── */

/* ONE template, on the rows and on the header alike, so a column cannot be
   wide in one and narrow in the other. Fixed tracks rather than `auto`: the
   rows are separate grid containers (each is its own `role="row"`), so `auto`
   would be measured per row and the columns would step sideways down the
   list. */
.stack-panel__list {
  padding: var(--space-2) 0 var(--space-3);
}

.stack-panel__listhead,
.stack-panel__row {
  display: grid;
  grid-template-columns:
    minmax(0, 1fr) 10rem minmax(0, 14rem)
    5rem 5rem var(--space-8);
  align-items: center;
  gap: var(--space-3);
  /* The selected edge is always drawn and always transparent, so a row that
     gains it does not move a pixel. */
  border-left: var(--rail-w) solid transparent;
  padding: var(--space-2) var(--space-4);
}

.stack-panel__listhead {
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  padding-bottom: var(--space-3);
}

.stack-panel__row {
  cursor: pointer;
  font-size: var(--text-sm);
  transition: background var(--dur-1) var(--ease-standard);
}

.stack-panel__row:hover {
  background: var(--hover-wash);
}

/* Flush rows in a list have no room for the global ring's gap, so the same ink
   outline is drawn inside the row instead. */
.stack-panel__row:focus-visible {
  outline-offset: calc(var(--focus-width) * -1);
}

/* The rail, not the card's ring: a row HAS a left edge, and this is the mark
   every other list in the app wears. */
.stack-panel__row[aria-selected="true"] {
  background: var(--active-wash);
  border-left-color: var(--active-bar);
}

.stack-panel__ident {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

/* 56×36, and the cover's own arrangement one size down, so a row and a card
   show the same pictures in the same arrangement: one fills the box, two split
   it, three keep the 2fr/1fr mosaic (#1456). The box itself does not change
   size with the count, so the column stays aligned down the list. */
.stack-panel__thumbs {
  display: grid;
  gap: var(--space-1);
  width: 56px;
  height: 36px;
  flex: none;
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.stack-panel__thumbs--1 {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr;
}

.stack-panel__thumbs--2 {
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr;
}

.stack-panel__thumbs--3 {
  grid-template-columns: 2fr 1fr;
  grid-template-rows: 1fr 1fr;
}

.stack-panel__thumbs--3 .stack-panel__thumb:first-child {
  grid-row: 1 / 3;
}

.stack-panel__thumb {
  overflow: hidden;
  background: rgba(var(--v-theme-on-panel), 0.06);
}

.stack-panel__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.stack-panel__rowname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

/* The one pill a member row carries. `--tag-h-xs` is the app's small-datum
   height; the shape is the pill the design draws over the cover in Grid. */
.stack-panel__pill {
  flex: none;
  display: inline-flex;
  align-items: center;
  height: var(--tag-h-xs);
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-size: var(--text-2xs);
}

.stack-panel__ckpt,
.stack-panel__facts {
  min-width: 0;
  overflow: hidden;
}

.stack-panel__num {
  text-align: right;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.stack-panel__end {
  display: flex;
  justify-content: flex-end;
}
</style>
