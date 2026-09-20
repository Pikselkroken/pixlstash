<template>
  <!-- `rowgroup`, not a bare div: the tray is a child of the grid's `treegrid`,
       where only rows and rowgroups are allowed, and the header below is a row of
       its own. -->
  <div
    :id="panelId"
    class="msp"
    role="rowgroup"
    :style="notchStyle"
    data-testid="model-set-panel"
  >
    <!-- One column has no centre worth pointing at, so the caret takes the
         shipped `--start` inset rather than a hand-copy of its 22px. -->
    <span
      class="tbm-caret"
      :class="columns > 1 ? 'msp__notch' : 'tbm-caret--start'"
      aria-hidden="true"
    ></span>

    <div class="msp__header" role="row" aria-level="2">
      <div role="gridcell">
        <!-- No `role="toolbar"`: the bar opens with two runs of static text, and
             the switch and Close name themselves. -->
        <div class="msp__bar">
          <span class="msp__name">{{ name }}</span>
          <span class="msp__count num">{{ countLabel }}</span>
          <span class="msp__spacer"></span>
          <!-- The Workflows grid's own switch, by name and by behaviour: same two
               options, same icon-label variant, same remembered-for-every-tray
               rule. What differs is the List columns, because a set's members are
               models where a stack's are workflows. -->
          <Segmented
            :options="VIEW_OPTIONS"
            :model-value="view"
            variant="icon-label"
            aria-label="Show the models as"
            @update:model-value="(value) => emit('view', value)"
          />
          <AppButton
            variant="ghost"
            size="sm"
            icon-left="close"
            icon-only
            tooltip="Close this set"
            @click="emit('close')"
          />
        </div>
      </div>
    </div>

    <!-- **The one thing a union cannot claim, said before the list of it.** A
         group is every model its base model has co-occurred with across all of its
         recipes, so two files here may never have run together and nothing in the
         tray can tell which did. Without this line the tray reads as a
         reproducible set, which is the one claim this feature must not make. -->
    <div class="msp__note" role="row" aria-level="2">
      <div role="gridcell">
        <v-icon size="16">mdi-information-outline</v-icon>
        <span
          >Each of these has run with <strong>{{ name }}</strong
          >. They have not necessarily run with <em>each other</em> — for the
          exact files one picture used, open a model and read what it works
          with.</span
        >
      </div>
    </div>

    <!-- Grid. `presentation`, so the member rows below are exposed to the rowgroup
         above rather than to an unroled div, which would break the chain a
         treegrid needs: rowgroup owns rows, rows own gridcells. -->
    <div v-if="view === 'grid'" class="msp__grid" role="presentation">
      <div
        v-for="(member, index) in members"
        :id="`${panelId}-row-${member.id}`"
        :key="member.id"
        class="msp__member"
        role="row"
        aria-level="2"
        :aria-posinset="index + 1"
        :aria-setsize="members.length"
        :tabindex="cursorKey === String(member.id) ? 0 : -1"
        :data-key="String(member.id)"
      >
        <div class="msp__cell" role="gridcell">
          <ModelSetMemberCard
            :member="member"
            @pick="(model) => emit('pick', model)"
          />
        </div>
      </div>
    </div>

    <!-- List. The SAME level-2 rows with the same gridcells, drawn on a CSS grid
         rather than a `<table>`: the tray already sits inside the view's
         `treegrid`, and a table here would put a second, conflicting grid
         structure inside it. The header row is one of the treegrid's rows too, so
         its cells are `columnheader`. The shape is the Workflows stack panel's;
         the columns are a model's. -->
    <div v-else class="msp__list" role="presentation">
      <div class="msp__listhead" role="row" aria-level="2">
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
        :id="`${panelId}-row-${member.id}`"
        :key="member.id"
        class="msp__member msp__row"
        role="row"
        aria-level="2"
        :aria-posinset="index + 1"
        :aria-setsize="members.length"
        :aria-label="rowName(member)"
        :tabindex="cursorKey === String(member.id) ? 0 : -1"
        :data-key="String(member.id)"
        @click="emit('pick', member)"
      >
        <!-- Everything the row draws is `aria-hidden`: the row's own label reads
             all of it, exactly as the card does in Grid. -->
        <span class="msp__ident" role="gridcell" aria-hidden="true">
          <span
            class="msp__mark"
            :style="{
              backgroundColor: markOf(member).color,
              color: markOf(member).ink,
            }"
            >{{ markOf(member).initials }}</span
          >
          <span class="msp__rowname">{{ member.name }}</span>
          <span v-if="member.head" class="msp__pill">Names this set</span>
        </span>
        <span class="msp__kindcol" role="gridcell" aria-hidden="true">{{
          member.kindLabel
        }}</span>
        <span class="msp__num num" role="gridcell" aria-hidden="true">{{
          formatModelSize(member.file_size)
        }}</span>
        <span class="msp__num num" role="gridcell" aria-hidden="true">{{
          member.recipes
        }}</span>
        <span class="msp__num num" role="gridcell" aria-hidden="true">{{
          member.pictures
        }}</span>
        <span
          class="msp__shared"
          :class="{ 'msp__shared--warn': member.ambiguous }"
          role="gridcell"
          aria-hidden="true"
        >
          <v-icon v-if="member.ambiguous" size="14">mdi-alert-outline</v-icon>
          {{
            member.ambiguous
              ? "Not recorded"
              : member.otherSets || "Only this one"
          }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * One open workflow set's tray (#1438): the models that have run with its base
 * model, as cards or as a comparison list.
 *
 * The tray is ONE element in the card grid at `grid-column: 1 / -1`, placed after
 * the last card of the open card's row, so opening it pushes the later rows down
 * rather than floating over them. The caret points back at the card that opened
 * it: `--notch` is derived from that card's COLUMN INDEX every time the container
 * resizes, never stored as a pixel offset, which is what would leave it pointing
 * at the wrong card after a resize.
 *
 * **Grid | List is the Workflows stack panel's, deliberately** — the same two
 * options, the same `icon-label` Segmented, the same remembered-for-every-tray
 * rule, so a reader learns one switch. The component is not shared because that
 * panel's List columns are workflow columns and its member menu is workflow verbs;
 * what is shared is the design. The remembered choice lives in the shelf's own
 * view blob (`view.trayView`), which already persists, validates and migrates.
 *
 * **Not `StackPanel`.** Beyond the columns: that panel carries a per-member menu
 * of workflow verbs and a selection contract, and threading slots through all of
 * it to draw models would bend one component's contract around two screens. The
 * notch arithmetic and the treegrid roles are ~40 lines and are copied instead.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { formatModelSize, generatedMark } from "../../utils/modelShelf";
import { pictureCount } from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";
import ModelSetMemberCard from "../widgets/ModelSetMemberCard.vue";
import Segmented from "../widgets/Segmented.vue";

/** The two views, spelled as the Workflows stack panel spells them. */
const VIEW_OPTIONS = [
  { id: "grid", label: "Grid", icon: "view-grid-outline" },
  { id: "list", label: "List", icon: "view-list" },
];

/** The List columns, in one place: the header row and the rows share them. */
const COLUMNS = [
  { id: "model", label: "Model", cls: "msp__ident" },
  { id: "kind", label: "Kind", cls: "msp__kindcol" },
  { id: "size", label: "Size", cls: "msp__num" },
  { id: "recipes", label: "Recipes", cls: "msp__num" },
  { id: "pictures", label: "Pictures", cls: "msp__num" },
  { id: "shared", label: "In other sets", cls: "msp__shared" },
];

const props = defineProps({
  /** The tray's DOM id - the open card's `aria-controls`. */
  panelId: { type: String, required: true },
  /** The group's name, as its card carries it. */
  name: { type: String, default: "" },
  /** The group's members, the head first (see `setGroups`). */
  members: { type: Array, default: () => [] },
  /** `"grid"` | `"list"` - how to draw them. */
  view: { type: String, default: "grid" },
  /** The grid's current column count, from its ResizeObserver. */
  columns: { type: Number, default: 1 },
  /** The open card's 0-based column in that grid. */
  columnIndex: { type: Number, default: 0 },
  /** The roving cursor's key, when it is inside this tray. */
  cursorKey: { type: String, default: "" },
  /** The grid's column gutter in px, so the members line up with the cards. */
  gap: { type: Number, default: 12 },
});

const emit = defineEmits(["close", "view", "pick"]);

const countLabel = computed(() =>
  props.members.length === 1 ? "1 model" : `${props.members.length} models`,
);

function markOf(member) {
  return generatedMark({
    display_name: member.name,
    filename: member.filename,
    base_model: member.base_model ?? null,
  });
}

/** A List row's whole accessible name; everything inside it is `aria-hidden`. */
function rowName(member) {
  return [
    member.name,
    member.head ? "names this set" : null,
    member.kindLabel,
    formatModelSize(member.file_size),
    `${member.recipes} recipes`,
    pictureCount(member.pictures),
    member.ambiguous
      ? "which file ran is not recorded"
      : member.otherSets
        ? `also in ${member.otherSets} other sets`
        : "only in this set",
  ]
    .filter(Boolean)
    .join(", ");
}

// The centre of the column the open card sits in, as a share of the tray's own
// width - the tray spans every column, so the two grids share an origin. At one
// column the caret falls back to the shipped `--start` class instead, so nothing
// here hand-copies that rule's inset.
const notchStyle = computed(() => {
  const style = { "--wf-columns": props.columns, "--wf-gap": `${props.gap}px` };
  if (props.columns > 1) {
    style["--notch"] = `${((props.columnIndex + 0.5) / props.columns) * 100}%`;
  }
  return style;
});
</script>

<style scoped>
.msp {
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
   point; the rotation is the shipped one and has to be restated because the two
   live in one `transform`. */
.msp__notch {
  left: var(--notch);
  transform: translateX(-50%) rotate(45deg);
}

.msp__bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--bar-height);
  padding: 0 var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.msp__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.msp__count {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.msp__spacer {
  flex: 1;
}

/* The honesty line. An info wash rather than a plain muted paragraph, because it
   is a caveat about what the list below means rather than a caption for it. */
.msp__note > [role="gridcell"] {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: var(--space-4) var(--space-4) 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-surface-info), 0.14);
  font-size: var(--text-xs);
}

.msp__note .v-icon {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-info));
}

/* The SAME column count as the grid above, taken from `--wf-columns` rather than
   recomputed with `auto-fill`: the tray's padding narrows the track, and an
   auto-fill here would quietly drop to one column fewer - at which point a
   member's index stops naming the column it is drawn in and Down lands somewhere
   else. `minmax(0, 1fr)` so a wide card cannot push the track out. */
.msp__grid {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  /* The GRID's own gutter, inherited through `--wf-gap`: the tray shares the
     grid's column count so that a member's index names the column it is drawn in,
     and a different gutter breaks the alignment that makes true. */
  gap: var(--wf-gap, var(--space-4));
  padding: var(--space-4);
  align-items: start;
}

/* No focus rule: `style.css` paints the one ring, and a row that stays silent
   gets it. */
.msp__cell {
  position: relative;
  border-radius: var(--radius-md);
}

/* ── List ──────────────────────────────────────────────────────────────────
   One track set, shared by the header and the rows, so a column cannot be one
   width in the head and another in the body. */
.msp__list {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-4);
}

.msp__listhead,
.msp__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 90px 80px 72px 72px 120px;
  align-items: center;
  gap: var(--space-3);
}

.msp__listhead {
  padding: 0 var(--space-3) var(--space-2);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.msp__row {
  min-height: var(--control-h);
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.msp__row:hover {
  background: var(--hover-wash);
}

.msp__ident {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.msp__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: var(--tag-h-xs);
  height: var(--tag-h-xs);
  border-radius: var(--radius-sm);
  font-size: 9px;
  font-weight: var(--weight-semibold);
}

.msp__rowname {
  overflow: hidden;
  font-size: var(--text-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msp__pill {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  flex-shrink: 0;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
}

.msp__kindcol,
.msp__shared {
  overflow: hidden;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msp__shared {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.msp__shared--warn {
  color: rgb(var(--v-theme-surface-warning));
}

.msp__num {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  text-align: right;
}
</style>
