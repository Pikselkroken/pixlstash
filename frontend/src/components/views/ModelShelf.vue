<template>
  <!-- role="region" because a bare div is role `generic`, which prohibits an
       accessible name: without it the aria-label is dropped and #shelf-help is
       never announced, so the whole paragraph below is dead weight. -->
  <div
    ref="rootEl"
    class="shelf"
    role="region"
    tabindex="-1"
    aria-label="Model shelf"
    aria-describedby="shelf-help"
    @keydown.escape="onShelfEscape"
  >
    <p id="shelf-help" class="visually-hidden">
      Every adapter and checkpoint PixlStash has found on this machine. Show
      chooses which kinds are listed and which base models. Sort chooses the
      order and whether the list is cut into groups. A name in a monospaced face
      was taken from the filename, because nobody has named that file yet. A row
      that stands for a training run says how many files it holds; Right and
      Left open and close it. Escape clears the selection.
    </p>

    <!-- One announcement for a resort, because the rows reorder silently: the
         two buttons' own names change, but a reader who is not on them hears
         nothing. Group collapse gets none, because `aria-expanded` on the
         header already says it and a second announcer double-speaks. -->
    <p class="visually-hidden" role="status">{{ sortAnnouncement }}</p>

    <div class="shelf-toolbar">
      <span class="shelf-title">Models</span>
      <span class="shelf-sub">{{ countLabel }}</span>
      <span class="shelf-spacer"></span>

      <!-- The bar's own cluster gap. `.shelf-toolbar` separates the title from
           its controls at --space-4; the controls separate from each other at
           --space-3, which is what every other bar in the app uses. -->
      <div class="shelf-bar-cluster">
        <v-menu
          v-model="sortMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          :offset="8"
          transition="scale-transition"
        >
          <!-- The shipped split-button: a direction toggle welded to a menu
             trigger. `role="group"` names the pair; the two halves keep their
             own accessible names, and v-menu returns focus to the trigger on
             Escape, on an outside click and on a selection. -->
          <template #activator="{ props: menuProps }">
            <div
              class="bar-split-button"
              :class="{ 'bar-split-button--open': sortMenuOpen }"
              role="group"
              aria-label="Sort"
            >
              <!-- The accessible name IS the current state and flips on press,
                 which is what a keyboard user hears when focus returns. -->
              <button
                class="bar-btn bar-split-toggle"
                type="button"
                :title="directionLabel"
                :aria-label="directionLabel"
                @click.stop="toggleDirection"
              >
                <v-icon size="19">{{ directionIcon }}</v-icon>
              </button>
              <!-- `aria-haspopup="dialog"`, not `menu`: the panel is a div of
                 grouped toggles, and claiming a menu would promise roving
                 arrow keys nothing implements. Matches SearchResultBar. -->
              <button
                v-bind="menuProps"
                class="bar-btn bar-split-menu"
                type="button"
                aria-haspopup="dialog"
                :aria-expanded="sortMenuOpen"
                :title="sortButtonTitle"
              >
                <span class="bar-btn-prefix">Sort:</span>
                <v-icon size="19">{{ activeSort.icon }}</v-icon>
                <span class="bar-btn-sort-type">{{ activeSort.label }}</span>
                <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
              </button>
            </div>
          </template>
          <ShelfSortPanel />
        </v-menu>

        <v-menu
          v-model="showMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          :offset="8"
          transition="scale-transition"
        >
          <!-- The boxed bar button, its badge and the panel shell are the
             toolbar's shipped filter pattern; v-menu is also what returns
             focus to this button on Escape and on an outside click, so none
             of that is hand-rolled. -->
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--boxed"
              :class="{
                'bar-btn--active': store.activeCount > 0 && !showMenuOpen,
                'bar-btn--open': showMenuOpen,
              }"
              type="button"
              title="Show"
            >
              <span class="bar-icon-badge-wrap">
                <v-icon size="19">mdi-eye-outline</v-icon>
                <span v-if="store.activeCount > 0" class="bar-filter-badge">{{
                  store.activeCount
                }}</span>
              </span>
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <ShelfShowPanel />
        </v-menu>

        <!-- Shown only once an ai-toolkit output root is registered. Hidden
             rather than disabled, unlike the selection bar's verbs: those are
             about a selection the reader just made and owe an explanation, and
             this is about a folder they have not set up, which the folders
             dialog is the place to say. -->
        <button
          v-if="hasSourceFolder"
          ref="importBtnRef"
          class="bar-btn bar-btn--boxed"
          :class="{ 'bar-btn--open': importOpen }"
          type="button"
          title="Import from ai-toolkit"
          aria-label="Import from ai-toolkit"
          @click="openImport"
        >
          <v-icon size="19">mdi-import</v-icon>
        </button>

        <!-- Grouping is a sweep over the whole shelf rather than something done
             to a selection, so it lives in the toolbar and not in the selection
             bar. It opens a dry run; nothing is written until that is
             confirmed. -->
        <button
          ref="stacksBtnRef"
          class="bar-btn bar-btn--boxed"
          :class="{ 'bar-btn--open': stacksOpen }"
          type="button"
          title="Group training runs"
          aria-label="Group training runs"
          @click="stacksOpen = true"
        >
          <v-icon size="19">mdi-layers-outline</v-icon>
        </button>

        <!-- No count badge: `bar-filter-badge` counts a deviation from a default
           the user set, and a folder count never returns to zero (the managed
           store always exists), so a permanent number 8px from the Show
           button's identical pill would mean something else entirely. -->
        <button
          ref="foldersBtnRef"
          class="bar-btn bar-btn--boxed"
          :class="{ 'bar-btn--open': foldersOpen }"
          type="button"
          title="Model folders"
          aria-label="Model folders"
          @click="openFolders"
        >
          <v-icon size="19">mdi-folder-multiple-outline</v-icon>
        </button>
      </div>
    </div>

    <ShelfSelectionBar
      @rename="editVerb = 'rename'"
      @set-base-model="editVerb = 'base-model'"
      @set-kind="editVerb = 'kind'"
      @move="openMove(store.selectedRows)"
      @set-icon="pickIcon"
      @clear-icons="confirmClearIcons"
      @forget="confirmForget"
    />

    <!-- The file picker the Set icon button drives. A real <input type=file>
         rather than a drop zone or a dialog: it is the platform's own chooser,
         it is keyboard-accessible for free, and picking a file is the whole
         interaction. Hidden rather than styled, because the button beside it
         is already the affordance. -->
    <input
      ref="iconInputRef"
      class="visually-hidden"
      type="file"
      accept="image/png,image/jpeg,image/webp"
      @change="onIconChosen"
    />

    <!-- `inert` while a move runs, not merely dimmed. A move repoints
         `model_file` rows under the list, so a verb pressed mid-move acts on a
         location that is about to be wrong. A veil that only looks disabled
         leaves every row clickable and every one of them in the tab order,
         which is worse than no veil at all. The toolbar stays live: Show and
         Sort still answer correctly while files are in flight. -->
    <div class="shelf-body" :inert="moves.running || undefined">
      <!-- The visible half of the same statement. `inert` on the wrapper is
           what actually stops the interaction; this is what says so. -->
      <div v-if="moves.running" class="shelf-dim" aria-hidden="true"></div>
      <p v-if="store.loading" class="shelf-state">Reading the shelf…</p>
      <p v-else-if="store.error" class="shelf-state" role="alert">
        {{ store.error }}
      </p>
      <!-- Three empty states, deliberately distinct. Conflating "you filtered
           everything out" with "there is nothing here" is the failure: the
           first is one click from fixed and the second is not, so only the
           first two offer Reset. -->
      <div v-else-if="store.nothingSelected" class="shelf-state">
        <p>Nothing is selected in Show.</p>
        <button
          class="tbm-action tbm-action--secondary"
          type="button"
          @click="store.resetFilters()"
        >
          Reset filters
        </button>
      </div>
      <div v-else-if="!store.rows.length" class="shelf-state">
        <p>No models found.</p>
        <p>
          PixlStash lists what it finds in the model folders registered on this
          machine. Add the folder where you keep them.
        </p>
        <button
          class="tbm-action tbm-action--primary"
          type="button"
          @click="openFolders($event)"
        >
          Add a model folder
        </button>
      </div>
      <div v-else-if="!store.visibleRows.length" class="shelf-state">
        <p>No models match these filters.</p>
        <button
          class="tbm-action tbm-action--secondary"
          type="button"
          @click="store.resetFilters()"
        >
          Reset filters
        </button>
      </div>

      <!-- The row itself is still not a focus stop; its checkbox is. That is
           the change F3 made and the reason the old rule existed: a row with no
           verb and no selection would have been 1,800 empty tab stops, and a
           row with a selection has exactly one thing to do. Group headers stay
           stops too, so Tab still moves group to group when nothing inside is
           reached. -->
      <template v-else>
        <div v-for="group in shownGroups" :key="group.key" class="shelf-group">
          <!-- The drive band: the OUTER of the two levels the plan allows, and
               the second one is spent here rather than on stacks, which nest
               inside a row and not inside a header. Drawn on the first group of
               each band, never as a wrapper element, so the sticky folder
               headers below keep scrolling under it in one flow. -->
          <h3
            v-if="group.bandStart"
            class="shelf-band-heading"
            :class="{ 'shelf-band-heading--unknown': !group.band.measured }"
          >
            <span class="shelf-band-label" :title="group.band.mountPoint">
              <v-icon size="16" class="shelf-band-icon">mdi-harddisk</v-icon>
              <span>{{ group.band.label }}</span>
            </span>
            <!-- Two fills in one track, not two bars: the shelf's share is a
                 part of what is used, so drawing it separately would let the
                 two add up past the drive. -->
            <span
              v-if="usage(group.band)"
              class="shelf-band-meter"
              role="img"
              :aria-label="meterLabel(group.band)"
            >
              <span
                class="shelf-band-fill"
                :style="{ width: `${usage(group.band).usedPct}%` }"
              ></span>
              <span
                class="shelf-band-fill shelf-band-fill--shelf"
                :style="{ width: `${usage(group.band).shelfPct}%` }"
              ></span>
            </span>
            <span class="shelf-band-figures">{{ meterLabel(group.band) }}</span>
          </h3>

          <!-- The header IS the button, on the same four-column grid as the
               rows, so its label starts at their left edge. Column 2 stays
               reserved and empty exactly as a row with no thumbnail reserves it
               (§5.1). A heading as well as a button, so a screen reader can
               jump group to group by heading. -->
          <h3 v-if="grouped" class="shelf-group-heading">
            <!-- A folder header is also the drop target for a drag, which is
                 why the drag handlers sit on the button and not on a wrapper:
                 the button already spans the header's full width, and a second
                 element would put a dead strip between the two. `dragover`
                 does NOT carry `.prevent` — calling preventDefault() is what
                 ACCEPTS a drop, so it happens inside the handler and only for
                 a payload this target takes (#757). -->
            <button
              class="ps-row shelf-group-btn"
              :class="{ 'shelf-group-btn--drop': dropTargetKey === group.key }"
              type="button"
              :aria-expanded="!store.isCollapsed(group.key)"
              :aria-label="`${group.label}, ${modelCount(group.rows.length)}`"
              @click="store.toggleGroup(group.key)"
              @dragover="onGroupDragOver(group, $event)"
              @dragleave="onGroupDragLeave(group)"
              @drop="onGroupDrop(group, $event)"
            >
              <span
                class="ps-row-glyph shelf-group-chevron"
                :class="{
                  'shelf-group-chevron--open': !store.isCollapsed(group.key),
                }"
              >
                <v-icon size="16">mdi-chevron-right</v-icon>
              </span>
              <!-- Column 2 carries the axis glyph rather than sitting empty:
                   the reserved width is there either way, and a folder header
                   with a gap where the row thumbnails are reads as a missing
                   image rather than as alignment. -->
              <span class="shelf-group-mark">
                <v-icon size="18">{{
                  GROUP_BY_LABELS[store.view.groupBy].icon
                }}</v-icon>
              </span>
              <span
                class="shelf-group-label"
                :class="`shelf-group-label--${group.labelKind}`"
                >{{ group.label }}</span
              >
              <span class="shelf-group-count">{{
                modelCount(group.rows.length)
              }}</span>
            </button>
          </h3>

          <!-- `role="treegrid"`, which is what the rows became once they got
               columns. A listbox cannot carry a `columnheader`, so nothing
               named what the figures in a row meant (#891); a treegrid can,
               and its keyboard model is already the one this list implements —
               Up/Down walk rows, Right/Left open and close a run, and
               `aria-multiselectable` + `aria-selected` still say what is
               picked. A run's other steps are CHILD rows, which is the "tree"
               half: they carry `aria-level="2"` because the DOM draws them as
               siblings of their cover rather than nesting them.

               A grid also lifts the old ban on controls inside a row — that
               was a listbox rule. Nothing here takes advantage of it yet, and
               `.shelf-row-steps` deliberately stays a span. -->
          <ul
            v-if="!grouped || !store.isCollapsed(group.key)"
            class="shelf-list"
            role="treegrid"
            aria-multiselectable="true"
            :aria-label="grouped ? group.label : 'Models'"
          >
            <!-- The header row. One per grid, because columnheaders only name
                 the columns of the grid they are in and grouping makes one
                 grid per group; drawn ONCE, because a strip of column names
                 under every folder header is noise rather than information.
                 The other groups keep theirs `visually-hidden`, so the names
                 are still on every grid for a reader while the eye sees the
                 single line the design shows.

                 The two glyph columns are headed too — a grid row must have a
                 cell for every column — with names only a reader gets. -->
            <li
              class="ps-row shelf-head-row"
              :class="{ 'visually-hidden': group.key !== headGroupKey }"
              role="row"
            >
              <span role="columnheader" class="shelf-head-cell">
                <span class="visually-hidden">Selected</span>
              </span>
              <span role="columnheader" class="shelf-head-cell">
                <span class="visually-hidden">Icon</span>
              </span>
              <span role="columnheader" class="shelf-head-cell">Name</span>
              <span role="columnheader" class="shelf-head-cell">Kind</span>
              <span role="columnheader" class="shelf-head-cell">Base</span>
              <span role="columnheader" class="shelf-head-cell"
                >Assigned to</span
              >
              <span
                role="columnheader"
                class="shelf-head-cell shelf-head-cell--size"
                >Size</span
              >
              <span role="columnheader" class="shelf-head-cell">
                <span class="visually-hidden">Status</span>
              </span>
            </li>
            <!-- A row with one spanning cell, because a grid takes nothing but
                 rows: not selectable, because there is nothing here to select.
                 A registered folder with no models says which of the two
                 states it is in, because "we have not looked yet" is the
                 owner's to act on and "we looked and it is empty" is not. -->
            <li v-if="!group.rows.length" role="row" class="shelf-empty-folder">
              <span role="gridcell" :aria-colspan="COLUMN_COUNT">
                {{ EMPTY_FOLDER_NOTE[group.emptyReason] }}
              </span>
            </li>
            <!-- The `v-for` sits on a wrapping template, not on the row, so a
                 stack's expanded members can be siblings of their cover inside
                 the same iteration and still see `row`. -->
            <template v-for="row in group.rows" :key="row.rowKey">
              <li
                class="ps-row shelf-row"
                :class="{ 'shelf-row--selected': store.isSelected(row.id) }"
                :title="rowTitle(row)"
                role="row"
                aria-level="1"
                :aria-expanded="
                  row.memberCount > 1 ? isStackOpen(row.stack_id) : undefined
                "
                :aria-selected="store.isSelected(row.id)"
                :tabindex="row.rowKey === rovingRowKey ? 0 : -1"
                :data-row-key="row.rowKey"
                :draggable="canDrag(row)"
                @click="pickRow(row, $event)"
                @keydown="onRowKeydown(row, $event)"
                @focus="focusedRowKey = row.rowKey"
                @dragstart="onRowDragStart(row, $event)"
                @dragend="dropTargetKey = ''"
              >
                <!-- Column 1 stays the reserved glyph slot. The selection shows
                   as the row's own wash and a tick here, not as a checkbox: a
                   checkbox is a second, contradictory way to select in a list
                   whose click already selects, and it was the shelf teaching a
                   dialect the rest of the app does not speak. -->
                <span role="gridcell" class="ps-row-glyph shelf-row-pick">
                  <v-icon v-if="store.isSelected(row.id)" size="16"
                    >mdi-check</v-icon
                  >
                </span>
                <span role="gridcell" class="shelf-row-ident">
                  <!-- Deck ticks behind the mark say "this is more than one
                     file" before the count is read, exactly as they do on a
                     picture tile. Count-only, so the component reuses cleanly
                     here even though a model has no thumbnail. -->
                  <StackEdgeTicks
                    v-if="row.memberCount > 1"
                    :count="row.memberCount"
                  />
                  <!-- The identity slot: the model's icon if it has one, else a
                       generated mark. Never the bare kind glyph on its own —
                       every checkpoint row and 37% of adapter rows would then
                       be visually identical, which is the blank column the
                       icon verb exists to fill. The kind now has a column of
                       its own, so nothing is lost by the swap. -->
                  <ModelMark :row="row" />
                </span>
                <span role="gridcell" class="shelf-row-label">
                  <span
                    class="shelf-row-name"
                    :class="{ 'shelf-row-name--derived': row.name.derived }"
                    >{{ row.name.text
                    }}<span v-if="row.name.derived" class="visually-hidden">
                      (name taken from the filename)</span
                    ></span
                  >
                  <!-- Beside the name rather than in a column of its own: the
                       count belongs to the run's identity, and only stacked
                       rows carry one, so a track for it would be empty on
                       nearly every row.

                       A plain span, NOT `StackBadge`, which renders a real
                       <button>. The grid role would now permit one, but the
                       row is still the disclosure — Right/Left expand and
                       collapse — and a second, focusable way to do the same
                       thing is the dialect this list stopped speaking. No
                       longer `aria-hidden`: the count is a fact about the row
                       and a grid cell can simply say it, which is what the
                       row's hand-built `aria-label` used to do instead. -->
                  <span
                    v-if="row.memberCount > 1"
                    class="shelf-row-steps"
                    @click.stop="toggleStack(row.stack_id)"
                  >
                    <v-icon
                      size="14"
                      class="shelf-row-steps-chevron"
                      :class="{
                        'shelf-row-steps-chevron--open': isStackOpen(
                          row.stack_id,
                        ),
                      }"
                      >mdi-chevron-right</v-icon
                    >
                    {{ row.memberCount }}
                  </span>
                </span>
                <span role="gridcell" class="shelf-col">{{
                  kindLabel(row)
                }}</span>
                <!-- Base is a COLUMN, not a phrase on a metadata line: it is
                     the field a reader scans a shelf for, and it can only be
                     scanned if it aligns. The header names it, so the empty
                     case is "Not set" rather than the sentence the meta line
                     had to carry. -->
                <span role="gridcell" class="shelf-col">
                  <span v-if="row.base_model">{{ row.base_model }}</span>
                  <span v-else class="shelf-col-none">Not set</span>
                </span>
                <!-- The column exists here; the marks that belong in it are
                     #892. Until then it states the fact `row.attachments`
                     already carries — how many characters and sets a model is
                     assigned to — rather than sitting blank under a header
                     that promises something. `attachments` holds ids and no
                     names, so naming the entities is not available without the
                     lookup that issue owns. -->
                <span role="gridcell" class="shelf-col shelf-col--assigned">
                  <template v-if="attachedCount(row)">
                    {{ attachedCount(row)
                    }}<span class="visually-hidden"> assigned</span>
                  </template>
                  <template v-else>
                    <span aria-hidden="true">—</span>
                    <span class="visually-hidden">Not assigned</span>
                  </template>
                </span>
                <span role="gridcell" class="shelf-col shelf-col--size">{{
                  row.file_size ? formatModelSize(row.file_size) : ""
                }}</span>
                <span
                  role="gridcell"
                  class="shelf-row-loc"
                  :class="`shelf-row-loc--${row.locState}`"
                  :title="LOC_TITLE[row.locState]"
                >
                  <v-icon size="16">{{ LOC_ICON[row.locState] }}</v-icon>
                </span>
              </li>

              <!-- The run's other steps, rendered as ROWS rather than through
                 `StackExpansionStrip`: that component draws picture thumbnails
                 for the dedup queue, and a model file has no thumbnail. A
                 stack's members already ARE shelf rows, so they are drawn as
                 shelf rows — indented, and not selectable on their own, because
                 stacks are atomic here and a member cannot be acted on apart
                 from its run. -->
              <template v-if="row.memberCount > 1 && isStackOpen(row.stack_id)">
                <li
                  v-for="member in row.members.slice(1)"
                  :key="`${row.rowKey}:${member.id}`"
                  class="ps-row shelf-row shelf-row--member"
                  role="row"
                  aria-level="2"
                >
                  <span role="gridcell" class="ps-row-glyph"></span>
                  <span role="gridcell" class="shelf-row-ident">
                    <v-icon size="14">mdi-subdirectory-arrow-right</v-icon>
                  </span>
                  <span role="gridcell" class="shelf-row-label">
                    <span class="shelf-row-name">{{
                      memberLabel(member)
                    }}</span>
                  </span>
                  <!-- A step of a run has no kind, base or assignment of its
                       own — those are the run's, one row up — but a grid row
                       still owes a cell per column, and an empty one is the
                       honest way to say "same as the run". -->
                  <span role="gridcell" class="shelf-col"></span>
                  <span role="gridcell" class="shelf-col"></span>
                  <span role="gridcell" class="shelf-col"></span>
                  <span role="gridcell" class="shelf-col shelf-col--size">{{
                    member.file_size ? formatModelSize(member.file_size) : ""
                  }}</span>
                  <span role="gridcell" class="shelf-row-loc"></span>
                </li>
              </template>
            </template>
          </ul>
        </div>
      </template>
    </div>

    <ShelfEditDialog :verb="editVerb" @close="editVerb = ''" />
    <ShelfMoveDialog
      :open="moveOpen"
      :items="moveItems"
      :total-bytes="moveBytes"
      :destination-folder-id="movePreselected"
      @close="closeMove"
    />
    <ModelImportDialog :open="importOpen" @close="closeImport" />
    <ShelfStackProposalsDialog :open="stacksOpen" @close="closeStacks" />
    <ModelFoldersDialog :open="foldersOpen" @close="closeFolders" />

    <ProgressOverlay
      :visible="moves.running"
      :status="moves.running ? 'running' : 'idle'"
      :message="moveProgressMessage"
      :percent="moves.percent"
      :count="moves.done"
      :total="moves.total"
      :abort-label="moves.cancelRequested ? null : 'Stop'"
      @abort="moves.cancel()"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, shallowRef, watch } from "vue";
import ShelfShowPanel from "../panels/ShelfShowPanel.vue";
import ShelfSortPanel from "../panels/ShelfSortPanel.vue";
import ShelfSelectionBar from "../panels/ShelfSelectionBar.vue";
import ShelfEditDialog from "../panels/ShelfEditDialog.vue";
import ShelfMoveDialog from "../panels/ShelfMoveDialog.vue";
import ModelFoldersDialog from "../panels/ModelFoldersDialog.vue";
import ModelImportDialog from "../panels/ModelImportDialog.vue";
import ShelfStackProposalsDialog from "../panels/ShelfStackProposalsDialog.vue";
import ModelMark from "../widgets/ModelMark.vue";
import ProgressOverlay from "../widgets/ProgressOverlay.vue";
import StackEdgeTicks from "../widgets/StackEdgeTicks.vue";
import { useConfirm } from "../../composables/useConfirm";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { isModelFileDrag, setInternalDragPayload } from "../../utils/media";
import {
  bandGroups,
  bandUsage,
  withEmptyFolders,
  formatModelSize,
  GROUP_BY_LABELS,
  movableCopies,
  SORT_LABELS,
  trainingStep,
  sortDirectionLabel,
} from "../../utils/modelShelf";

const store = useModelShelfStore();
const foldersStore = useModelFoldersStore();
const moves = useModelMovesStore();
const rootEl = ref(null);
const showMenuOpen = ref(false);
const sortMenuOpen = ref(false);
const foldersOpen = ref(false);
const foldersBtnRef = ref(null);
/** Which edit verb owns the dialog: `rename` | `base-model` | `kind` | "". */
const editVerb = ref("");
const { confirm } = useConfirm();

/**
 * The second of the shelf's two confirmations.
 *
 * A prompt rather than an inline warning, because unlike the bulk base-model
 * overwrite this one is not a property of a form the reader is filling in: it
 * is a single press with nothing between it and the deletion. There is no undo
 * and no operation log behind the shelf, so this sentence is the whole safety
 * net, and it names what is destroyed rather than what is clicked.
 */
async function confirmForget() {
  const forgettable = store.selectedRows.filter(
    (row) => row.locState === "missing" || row.locState === "forgotten",
  );
  if (!forgettable.length) return;
  const many = forgettable.length !== 1;
  const ok = await confirm({
    title: many ? `Forget ${forgettable.length} models?` : "Forget this model?",
    message: many
      ? "Their files are already gone. This also deletes the names, base models and trigger words recorded for them."
      : "Its file is already gone. This also deletes the name, base model and trigger words recorded for it.",
    warning: "There is no undo for this.",
    confirmLabel: many ? "Forget them" : "Forget it",
    danger: true,
  });
  if (ok) await store.forgetSelected();
}

// ── Move (shelf plan F4) ─────────────────────────────────────────────────────
//
// Two ways in, one dialog: the selection bar's Move button and a drag onto a
// folder header. Both resolve to the same list of COPIES, because
// `model_file`'s key is `(folder_id, relpath)` and a model catalogued in three
// folders offers three of them.
//
// A drop does NOT move on release. It opens the dialog with the destination
// already chosen, so a 438 GB copy across a USB drive is never one slip of the
// pointer away from starting — and there is no undo behind a move to make that
// recoverable.

const moveOpen = ref(false);
const moveItems = ref([]);
const moveBytes = ref(0);
/** The group header the pointer is currently over, for the drop affordance. */
const dropTargetKey = ref("");
const moveInvoker = shallowRef(null);

/** `model_folder.id` to the folder row, for `movableCopies`' folder rules. */
const foldersById = computed(
  () =>
    new Map(foldersStore.folders.map((folder) => [Number(folder.id), folder])),
);

const moveProgressMessage = computed(() =>
  moves.cancelRequested
    ? "Stopping after the file in flight…"
    : "Moving model files…",
);

/**
 * Open the move dialog for a set of rows.
 *
 * @param {Array<Object>} rows - shelf rows.
 * @param {number|null} [destinationFolderId] - preselected, when a drop chose
 *   it. The dialog seeds the managed store otherwise.
 */
function openMove(rows, destinationFolderId = null) {
  const { items, totalBytes } = movableCopies(rows, foldersById.value);
  if (!items.length) return;
  moveInvoker.value =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  moveItems.value = destinationFolderId
    ? // A file already in the folder it was dropped on is dropped from the
      // batch here rather than sent for the server to skip: the dialog states
      // the move in numbers, and counting files that will not move would make
      // that statement wrong.
      items.filter((item) => item.folder_id !== destinationFolderId)
    : items;
  moveBytes.value = totalBytes;
  movePreselected.value = destinationFolderId;
  moveOpen.value = moveItems.value.length > 0;
}

/** The destination a drop chose, or null when the bar's button opened this. */
const movePreselected = ref(null);

async function closeMove() {
  const returnTo = moveInvoker.value;
  moveOpen.value = false;
  moveInvoker.value = null;
  await nextTick();
  (returnTo?.isConnected ? returnTo : rootEl.value)?.focus();
}

/**
 * Whether a row may start a drag.
 *
 * Only rows with a copy actually on this machine: dragging one whose file is
 * `missing` or on an unplugged drive would offer a gesture that can only end in
 * a refusal, and the pointer would say it works the whole way.
 *
 * Engines are excluded for a harder reason than a refusal. They live in the
 * three roots PixlStash declares `root_only` — its own downloads, the
 * InsightFace packs, and the HuggingFace cache — and the cache is a symlink
 * store shared with every other HF tool, where a row's path is a whole repo
 * directory. The server refuses the move, and this stops the gesture being
 * offered at all: a drag that looks like it works on 116 GB of somebody else's
 * bookkeeping is not a thing to find out about at the drop.
 */
function canDrag(row) {
  return (
    row.locState === "present" && row.file_kind !== "engine" && !moves.busy
  );
}

/**
 * Start a drag of the selection, selecting the dragged row if it is not in it.
 *
 * The same rule a file manager uses, and the same one the grid uses: dragging a
 * row that is not selected drags THAT row and makes it the selection, while
 * dragging one that is drags the whole selection untouched.
 */
function onRowDragStart(row, event) {
  if (!canDrag(row)) {
    event.preventDefault();
    return;
  }
  if (!store.isSelected(row.id)) {
    store.selectFromClick(row.id, {}, orderedRowIds.value);
  }
  const { items } = movableCopies(store.selectedRows, foldersById.value);
  if (!items.length) {
    event.preventDefault();
    return;
  }
  event.dataTransfer.effectAllowed = "move";
  setInternalDragPayload(event.dataTransfer, { type: "model-files", items });
}

/** True when this group is a folder a move may be sent to. */
function isDropTarget(group) {
  if (!Number.isInteger(group?.folderId)) return false;
  const folder = foldersById.value.get(Number(group.folderId));
  // Same two exclusions the dialog's destination list applies, checked here as
  // well so the pointer never suggests a drop the dialog would then refuse.
  return Boolean(
    folder && folder.kind !== "source" && folder.movable !== "external",
  );
}

/**
 * Accept the drag, or leave it refused.
 *
 * `preventDefault()` is what ACCEPTS a drop, so it is called inside the handler
 * and only for a payload this target takes — never as a `.prevent` modifier on
 * the template, which would accept everything including a picture drag from the
 * grid (#757, one payload kind later).
 */
function onGroupDragOver(group, event) {
  if (!isModelFileDrag(event.dataTransfer) || !isDropTarget(group)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  dropTargetKey.value = group.key;
}

function onGroupDragLeave(group) {
  if (dropTargetKey.value === group.key) dropTargetKey.value = "";
}

function onGroupDrop(group, event) {
  dropTargetKey.value = "";
  if (!isModelFileDrag(event.dataTransfer) || !isDropTarget(group)) return;
  event.preventDefault();
  openMove(store.selectedRows, Number(group.folderId));
}

/**
 * Escape clears the selection, from anywhere in the shelf.
 *
 * On the ROOT rather than on the row: a selection made by clicking leaves focus
 * on the row, but a selection survives Tab to the toolbar, a dialog opening and
 * closing, or a click on empty space — and "Escape clears the selection" has to
 * mean that everywhere, not only while a row holds the roving tab stop.
 *
 * A dialog is checked for first. Vuetify's own overlays stop the key before it
 * reaches here, but the shelf's `AppDialog`s and the entity picker are inside
 * this subtree, and Escape inside one of those means "close me" — clearing the
 * selection underneath at the same time would be a second, unasked-for effect.
 */
function onShelfEscape(event) {
  if (
    moveOpen.value ||
    importOpen.value ||
    stacksOpen.value ||
    editVerb.value
  ) {
    return;
  }
  if (event.target?.closest?.(".ate, [role='dialog']")) return;
  if (!store.selectedRows.length) return;
  event.preventDefault();
  store.clearSelection();
}

// ── The icon verb ───────────────────────────────────────────────────────────

const iconInputRef = ref(null);

function pickIcon() {
  // Cleared first, so choosing the SAME file twice still fires `change` — the
  // obvious way to retry after a refusal, and silent if the value persisted.
  if (iconInputRef.value) iconInputRef.value.value = "";
  iconInputRef.value?.click();
}

async function onIconChosen(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  await store.setIconOnSelected(file);
}

/**
 * Clearing one row needs no prompt; clearing a selection does.
 *
 * The shelf's rule is to confirm only where the prior state cannot be
 * reconstructed. One icon is one file-picker away from back; a bulk clear is
 * not, and falls on the same side of that test as the bulk base-model
 * overwrite. Counted on the rows that HAVE one, because that is what the verb
 * will actually destroy.
 */
async function confirmClearIcons() {
  const withIcons = store.selectedRows.filter((row) => row.icon_sha256);
  if (!withIcons.length) return;
  if (withIcons.length > 1) {
    const ok = await confirm({
      title: `Clear ${withIcons.length} icons?`,
      message:
        "Those models go back to a generated mark. The images stay in the " +
        "icon store, but which model wore which is not recorded anywhere else.",
      warning: "There is no undo for this.",
      confirmLabel: "Clear them",
      danger: true,
    });
    if (!ok) return;
  }
  await store.clearIconsOnSelected();
}

// ── Stacks (shelf plan F5) ──────────────────────────────────────────────────
//
// Which runs are open is view state and nothing more: it is not persisted and
// not shared, because an expansion is a glance rather than a preference.

const openStacks = ref(new Set());
const stacksOpen = ref(false);
const stacksBtnRef = ref(null);

async function closeStacks() {
  stacksOpen.value = false;
  await nextTick();
  stacksBtnRef.value?.focus();
}

function isStackOpen(stackId) {
  return openStacks.value.has(stackId);
}

/** A new Set, because Vue does not track `Set.add`. */
function toggleStack(stackId) {
  const next = new Set(openStacks.value);
  if (next.has(stackId)) next.delete(stackId);
  else next.add(stackId);
  openStacks.value = next;
}

/**
 * What one step of a run is called in the strip.
 *
 * The step, not the filename: every member of a run shares a name by
 * construction, so repeating it six times says nothing and hides the one field
 * that differs. A member with no step is the bare final the trainer wrote last.
 */
function memberLabel(member) {
  const step = trainingStep(member.filename);
  return step === null ? "Final" : `Step ${step.toLocaleString()}`;
}

// ── Import from ai-toolkit (shelf plan F6) ──────────────────────────────────

const importOpen = ref(false);
const importBtnRef = ref(null);

/** Whether any ai-toolkit output root is registered at all. */
const hasSourceFolder = computed(() =>
  foldersStore.folders.some((folder) => folder.kind === "source"),
);

function openImport() {
  importOpen.value = true;
}

async function closeImport() {
  importOpen.value = false;
  await nextTick();
  // The button can unmount under us: the import may have been the last run in
  // the only source folder, and `delete_after_import` then empties it. Falling
  // back to the shelf root beats dropping focus to <body>.
  (importBtnRef.value?.isConnected
    ? importBtnRef.value
    : rootEl.value
  )?.focus();
}

// Two controls open the same dialog, so which one gets focus back is a fact
// about the press rather than about the dialog. Held raw: it is a DOM node, and
// making it reactive would deep-track an element tree for nothing.
const folderInvoker = shallowRef(null);

function openFolders(event) {
  folderInvoker.value =
    event?.currentTarget instanceof HTMLElement ? event.currentTarget : null;
  foldersOpen.value = true;
}

async function closeFolders() {
  const returnTo = folderInvoker.value;
  foldersOpen.value = false;
  folderInvoker.value = null;
  await nextTick();
  // The empty-state button unmounts the moment the first folder is scanned in,
  // so fall back to the toolbar control rather than dropping focus to <body>.
  (returnTo?.isConnected ? returnTo : foldersBtnRef.value)?.focus();
}

// A closed vocabulary gets a glyph, an open one a word. `unknown` gets a plain
// file rather than a question mark (an unclassified file is a fact about our
// parser, not the user's mistake) and never the checkpoint cube.
const KIND_ICON = {
  adapter: "mdi-layers-outline",
  checkpoint: "mdi-cube-outline",
  unknown: "mdi-file-outline",
};

// `missing` is a fact (the folder was readable, the file was not in it);
// `unreachable` is the absence of one (we could not look). Only the fact wears
// a status colour — claiming a hue for "we do not know" would assert knowledge
// we do not have. `present` reserves its slot and shows nothing.
const LOC_ICON = {
  present: "mdi-check",
  missing: "mdi-file-remove-outline",
  unreachable: "mdi-help-circle-outline",
  forgotten: "mdi-folder-off-outline",
};

const LOC_TITLE = {
  present: "",
  missing: "The file is not where it was",
  unreachable: "Could not check this location",
  forgotten: "Every registered copy has been forgotten",
};

// Trainers spell these however they like; the shelf spells them one way.
const ALGO_LABEL = {
  lora: "LoRA",
  lokr: "LoKr",
  loha: "LoHa",
  dora: "DoRA",
  oft: "OFT",
};

/**
 * Every drawn row, in order, as `{key, id}`.
 *
 * TWO orders come out of this and they are not the same list, which is the
 * whole point. Focus moves over RENDERED ROWS: under folder grouping a model
 * with copies in two folders is drawn twice, and both draws are places the
 * cursor can be. Selection is over MODELS: the verbs write the model, so the
 * range de-duplicates.
 *
 * Keying focus by `row.id` instead put `tabindex="0"` on every draw of the
 * same model at once, gave `querySelector` the first duplicate whichever one
 * was focused, and made `indexOf` return the first draw's index when the
 * cursor was on the second.
 *
 * Not `store.groups`: banding re-orders the groups, and a range measured
 * against an order the reader cannot see would select a run they did not point
 * at.
 */
const drawnRows = computed(() => {
  const rows = [];
  for (const group of shownGroups.value) {
    if (grouped.value && store.isCollapsed(group.key)) continue;
    for (const row of group.rows) rows.push({ key: row.rowKey, id: row.id });
  }
  return rows;
});

/** Model ids in drawn order, de-duplicated: what a Shift-range spans. */
const orderedRowIds = computed(() => [
  ...new Set(drawnRows.value.map((row) => row.id)),
]);

/**
 * Which drawn row owns the list's single tab stop.
 *
 * Roving, and it falls back to the first drawn row: with no row at `tabindex=0`
 * the whole list is unreachable by Tab, which is the failure mode a roving
 * tabindex introduces if nothing seeds it.
 */
const focusedRowKey = ref(null);
const rovingRowKey = computed(
  () => focusedRowKey.value ?? drawnRows.value[0]?.key ?? null,
);

/**
 * Click, Ctrl+click, Shift+click — the grid's own three gestures.
 *
 * A drag across THIS row's text is not a pick: releasing it would otherwise
 * collapse the selection to that one row. Scoped to the row the click landed
 * in — asking only whether any text is selected anywhere would make the whole
 * list unclickable for as long as the reader had a selection somewhere else on
 * the page, which is a far bigger rule than the one intended.
 */
function pickRow(row, event) {
  const selection = window.getSelection?.();
  if (
    selection &&
    !selection.isCollapsed &&
    event.currentTarget?.contains?.(selection.anchorNode)
  ) {
    return;
  }
  focusedRowKey.value = row.rowKey;
  store.selectFromClick(
    row.id,
    { ctrl: event.ctrlKey || event.metaKey, shift: event.shiftKey },
    orderedRowIds.value,
  );
}

/**
 * The keyboard half of the same three gestures.
 *
 * Arrow keys move the tab stop without selecting, which is the roving-focus
 * contract: a reader can walk 1,800 rows without arming a verb against every
 * one they pass. Space and Enter pick; Shift+arrow extends from the anchor,
 * the keyboard's Shift+click. Escape clears, so there is always a way out that
 * does not involve finding the bar.
 */
/**
 * Move real focus to a drawn row by its key.
 *
 * Matched by reading `dataset` rather than building an attribute selector: a
 * row key carries a folder path, so it can hold quotes, brackets and
 * backslashes, and `CSS.escape` is not defined in jsdom — a selector here would
 * be both fragile and untestable.
 */
function focusDrawnRow(key) {
  for (const el of rootEl.value?.querySelectorAll("[data-row-key]") || []) {
    if (el.dataset.rowKey === key) {
      el.focus({ preventScroll: false });
      return;
    }
  }
}

function onRowKeydown(row, event) {
  const drawn = drawnRows.value;
  const index = drawn.findIndex((drawnRow) => drawnRow.key === row.rowKey);
  const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
  if (step !== undefined) {
    const next = drawn[index + step];
    if (next === undefined) return;
    event.preventDefault();
    // The cursor moves over DRAWN rows; the range it extends is over models.
    focusedRowKey.value = next.key;
    if (event.shiftKey) {
      store.selectFromClick(next.id, { shift: true }, orderedRowIds.value);
    }
    nextTick(() => focusDrawnRow(next.key));
    return;
  }
  // Right opens a run, Left closes it — the disclosure keys, on the row rather
  // than on a control inside it. Ignored for a row that is not a stack, so they
  // stay free for anything a single model might want later.
  if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
    if (row.memberCount > 1) {
      const open = isStackOpen(row.stack_id);
      if (open !== (event.key === "ArrowRight")) {
        event.preventDefault();
        toggleStack(row.stack_id);
      }
    }
    return;
  }
  if (event.key === " " || event.key === "Enter") {
    event.preventDefault();
    store.selectFromClick(
      row.id,
      { ctrl: event.key === " " || event.ctrlKey || event.metaKey },
      orderedRowIds.value,
    );
    return;
  }
  // Escape is NOT handled here. It is owned by the shelf root, so it works
  // wherever focus happens to be — on a row, on the toolbar, or nowhere at all
  // after a click — rather than only while a row holds the roving tab stop,
  // which is what it used to mean and is not what a reader expects from
  // "Escape clears the selection".
}

/**
 * How many characters and sets a model is assigned to.
 *
 * The count only, because `attachments` carries `entity_type` and `entity_id`
 * and no names: saying WHICH ones needs the character and set stores, which is
 * what #892 brings when it replaces this cell with marks.
 */
function attachedCount(row) {
  return (row.attachments ?? []).length;
}

/**
 * Cells per row, so the header row, the rows and the empty-folder row's
 * `aria-colspan` cannot drift apart. A grid where one row has a different cell
 * count is a grid a reader is lied to about.
 */
const COLUMN_COUNT = 8;

/**
 * What an empty folder group says, per reason.
 *
 * Never a bare "0 models": the count is already in the header, and the reader's
 * question is whether the shelf has looked.
 */
const EMPTY_FOLDER_NOTE = {
  unscanned: "Not scanned yet.",
  empty: "No models in this folder.",
};

/** "1 model" / "12 models", so no line ever reads "1 models". */
function modelCount(n) {
  return `${n.toLocaleString()} ${n === 1 ? "model" : "models"}`;
}

/**
 * The groups as drawn: banded by drive under `Folder` + `Drive, then folder`,
 * and the store's own order on every other axis.
 *
 * Banded HERE rather than in the store because the drives are the folder
 * store's data and the folder store already imports the shelf store; reaching
 * back the other way would close an import cycle. `bandGroups` is pure, so the
 * arrangement is still testable without a component.
 */
const shownGroups = computed(() => {
  if (store.view.groupBy !== "folder") return store.groups;
  // A registered folder holding nothing has no rows and therefore no group.
  // The managed store is exactly that on a fresh install, and it is the ruled
  // default destination for a drop or an import — which it cannot be while the
  // owner has no way to see it.
  const groups = withEmptyFolders(store.groups, foldersStore.folders);
  if (store.view.folderLayout !== "drive") return groups;
  return bandGroups(groups, foldersStore.deviceByFolderId);
});

function usage(band) {
  return bandUsage(band);
}

/**
 * What a band's meter says in words.
 *
 * Free space leads, because it is the number that decides whether the next
 * checkpoint fits. A drive we could not measure says so rather than reporting
 * zero, which would draw an empty meter for a drive that may well be full.
 */
function meterLabel(band) {
  if (!bandUsage(band)) return "Capacity unknown";
  const free = formatModelSize(band.freeBytes);
  const total = formatModelSize(band.totalBytes);
  const shelf = formatModelSize(band.shelfBytes);
  return `${free} free of ${total} · ${shelf} on the shelf`;
}

/**
 * The count under the title.
 *
 * Under folder grouping a model with copies in two folders is drawn under both,
 * so the group counts add up to more than the shelf holds. Both numbers are
 * stated when they differ rather than picking one and being wrong about the
 * other: `models` is distinct files on the shelf, `copies` is rows on screen.
 */
const countLabel = computed(() => {
  const models = modelCount(store.visibleRows.length);
  const drawn = store.renderedCount;
  if (drawn === store.visibleRows.length) return models;
  return `${models} · ${drawn.toLocaleString()} copies`;
});

/** True while the list is cut into groups, i.e. headers are drawn. */
const grouped = computed(() => store.view.groupBy !== "none");

/**
 * The one group whose column-header row is drawn rather than only announced.
 *
 * The first group that has rows and is not collapsed: a header names columns,
 * and columns the reader cannot see are not worth a line. Every other group
 * still carries its own copy `visually-hidden`, because a `columnheader` heads
 * the grid it is in and nothing else — grouping makes one grid per group.
 */
const headGroupKey = computed(
  () =>
    shownGroups.value.find(
      (group) =>
        group.rows.length && (!grouped.value || !store.isCollapsed(group.key)),
    )?.key ?? "",
);

const activeSort = computed(
  () => SORT_LABELS[store.view.sortKey] || SORT_LABELS.added_at,
);

const directionLabel = computed(() =>
  sortDirectionLabel(store.view.sortKey, store.view.sortDirection),
);

const directionIcon = computed(() =>
  store.view.sortDirection === "asc"
    ? "mdi-sort-ascending"
    : "mdi-sort-descending",
);

// The direction phrase keeps its own capital: "A to Z" lowercased is "a to z",
// which reads as a typo and is why the two halves are joined by a colon rather
// than folded into one sentence.
const sortButtonTitle = computed(
  () =>
    `Sort by ${activeSort.value.label.toLowerCase()}: ${directionLabel.value}`,
);

const sortAnnouncement = computed(
  () =>
    `Sorted by ${activeSort.value.label.toLowerCase()}: ${directionLabel.value}`,
);

function toggleDirection() {
  store.setView({
    sortDirection: store.view.sortDirection === "asc" ? "desc" : "asc",
  });
}

/** The always-present anchor of the metadata line, whatever else is null. */
function kindLabel(row) {
  if (row.file_kind === "checkpoint") return "Checkpoint";
  if (row.file_kind === "unknown") return "Unclassified";
  const kind = String(row.kind || "").toLowerCase();
  return ALGO_LABEL[kind] || kind || "Adapter";
}

/** Filename and folder live in the tooltip; the row shows the name. */
function rowTitle(row) {
  const where = (row.locations || [])
    .map((loc) => `${loc.folder_path}/${loc.relpath}`)
    .join("\n");
  return [row.filename, where].filter(Boolean).join("\n");
}

onMounted(() => {
  // Tab out of the sidebar lands in the shelf, the same contract the duplicate
  // queue has. Synchronously, like DuplicateQueue: taking focus one round trip
  // after mount would discard wherever the user had moved in the meantime.
  rootEl.value?.focus();
  store.fetchRows();
  // Unawaited and never blocking the list: the drives decorate the bands, and a
  // slow or offline mount must not hold up the models. The folder list comes
  // with them now, because a folder holding nothing is only visible if the
  // shelf knows it is registered — the dialog used to be its only reader.
  //
  // NOT `quiet`: that suppresses the folder store's `loading`, and the folders
  // dialog reads it. Opening the dialog while this first fetch is in flight
  // would show an empty list with no "Reading the registered folders…" state.
  // Unawaited already means it does not hold up the shelf.
  foldersStore.refreshDevices();
  foldersStore.refresh();
  // A move is machine-wide and outlives this component, so one may already be
  // running: started before a reload, or from another tab. Adopting it is what
  // puts the progress back rather than leaving the list live over files that
  // are moving under it. Only a `running` job is adopted — a finished one
  // belongs to a receipt that has already been shown.
  moves.adopt();
});

// A credential change (logout, login, share token, restore) empties the store,
// and an empty shelf reads as "this machine has no models". Refetching rather
// than gating the empty state on `loaded`: the view is still on screen and its
// job is to show the shelf, so a blank body would be a second wrong answer.
// The store cannot do this itself: session-reset handlers run BEFORE the new
// credential is installed, whereas this pre-flush watcher runs after.
watch(
  () => store.loaded,
  (isLoaded) => {
    if (!isLoaded) store.fetchRows();
  },
);
</script>

<style scoped>
.shelf {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  outline: none;
}

.shelf-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: var(--bar-height);
  padding: 0 var(--space-5);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  flex-shrink: 0;
}

.shelf-title {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.shelf-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
  font-variant-numeric: tabular-nums;
}

.shelf-spacer {
  flex: 1 1 auto;
}

/* The toolbar separates the title from its controls at --space-4; the controls
   separate from each other at --space-3, which is the gap the grid bar uses.
   Without the cluster every child of .shelf-toolbar sat at the wider gap. */
.shelf-bar-cluster {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.shelf-body {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.shelf-state {
  padding: var(--space-7) var(--space-5);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.7);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-4);
  max-width: 60ch;
}

.shelf-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

/* The selection tick lives in the reserved glyph column, so ticking a row
   moves nothing. Sized to the 24px target the rest of the app uses. */
.shelf-row-pick {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* The row is the control, so it takes the pointer and the focus ring. The ring
   is inset rather than an outline: the rows sit flush against each other and an
   outer ring would be clipped by the neighbour above. */
.shelf-row {
  cursor: pointer;
}

.shelf-row:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}

/* A wash, not a border: a 1px outline on a selected row shifts every glyph in
   it by a pixel, and 200 selected rows would shimmer as the list scrolls. */
.shelf-row--selected {
  background: rgba(var(--v-theme-primary), 0.12);
}

/* ── Drive bands ───────────────────────────────────────────────────────────
   The OUTER of the two levels the plan allows, drawn only under `Folder` +
   `Drive, then folder`. Deliberately NOT sticky: two sticky levels need
   stacking arithmetic (the inner offset becomes the outer's measured height,
   which no token knows), and the band is a label with a meter rather than
   something the reader needs pinned while they scan a folder. The folder
   header below stays sticky and scrolls under nothing. */
.shelf-band-heading {
  display: grid;
  grid-template-columns: minmax(0, auto) minmax(80px, 1fr) auto;
  align-items: center;
  gap: var(--space-3);
  margin: var(--space-2) 0 var(--space-3);
  /* Tight vertically, roomy horizontally: a one-line band reads as loose at
     --space-3 top and bottom, and the next value down the scale is --space-2
     (the scale has nothing between them, and an off-grid 6px is a design
     decision rather than a nudge). The --space-4 inset is the point of the
     change: the meter and its figures were running to the panel edge. */
  padding: var(--space-2) var(--space-4);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-background));
}

/* Rank is size and weight, never opacity: a header must not be dimmer than the
   rows it heads. The unknown case loses the meter, not the contrast. */
.shelf-band-heading--unknown .shelf-band-figures {
  font-style: italic;
}

.shelf-band-label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The glyph says "this is a disk", which is what lets the label be a bare
   volume name rather than a path the reader has to parse to know what it is. */
.shelf-band-icon {
  flex: none;
}

/* One track, two fills, the wider drawn first: the shelf's share is PART of
   what is used, so two separate bars could add up past the drive. */
.shelf-band-meter {
  position: relative;
  height: 6px;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-panel), 0.08);
  overflow: hidden;
}

.shelf-band-fill {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-panel), 0.28);
}

.shelf-band-fill--shelf {
  background: rgb(var(--v-theme-primary));
}

.shelf-band-figures {
  font-size: var(--text-xs);
  white-space: nowrap;
}

/* ── Group headers ─────────────────────────────────────────────────────────
   The inner level, and the only sticky one. Folder is a grouping value; the
   band above is the outer tier and is static, so there is still one sticky
   offset and no stacking arithmetic. */

/* Space BETWEEN groups, no separator rule: a rule as well as the header's own
   hairline would draw two lines at every boundary. */
.shelf-group + .shelf-group {
  margin-top: var(--space-5);
}

.shelf-group-heading {
  margin: 0;
  font: inherit;
}

/* Sticky inside the body's own scroller, the same band DuplicateQueue's
   `.mixed-head` ships: an OPAQUE `background` (rows pass underneath it), the
   named `--z-sticky` rung, and one hairline. No elevation: a shadow is for an
   object floating above a surface, and this band is part of the list. */
.shelf-group-btn {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  width: 100%;
  display: grid;
  grid-template-columns:
    var(--gutter-glyph)
    var(--entity-thumb)
    minmax(0, 1fr)
    auto;
  align-items: center;
  gap: var(--space-2);
  padding-top: var(--space-2);
  padding-bottom: var(--space-2);
  text-align: left;
  background: rgb(var(--v-theme-background));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  color: rgb(var(--v-theme-on-background));
  transition: background var(--dur-1) var(--ease-standard);
}

.shelf-group-btn:hover {
  background: var(--hover-wash);
}

/* A run's other steps. Indented into the identity column so the deck reads as
   belonging to the row above it, and quieter than a row because it is detail
   rather than something to act on — the members are not selectable, stacks
   being atomic here. */
.shelf-row--member {
  cursor: default;
  color: rgb(var(--v-theme-on-surface-variant));
}

.shelf-row--member .shelf-row-name {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}

/* The drop affordance, on the destination header only. An inset ring rather
   than a border: the header is sticky and on a grid, so a border would shift
   its columns by a pixel at the moment the reader is aiming at it. */
.shelf-group-btn--drop {
  background: rgba(var(--v-theme-primary), 0.12);
  box-shadow: inset 0 0 0 2px rgba(var(--v-theme-primary), 0.65);
}

/* Paired with `inert` on `.shelf-body`, which is what actually stops the
   interaction. Inside the body rather than over the whole view, so the toolbar
   stays undimmed as well as live: Show and Sort still answer correctly while
   files are in flight, and a veil over them would say otherwise. */
.shelf-dim {
  position: absolute;
  inset: 0;
  background: rgba(var(--v-theme-background), 0.55);
  /* Above the sticky group headers, which is the whole point: a header left
     bright over a dimmed list reads as the one thing still usable. */
  z-index: calc(var(--z-sticky) + 1);
  pointer-events: none;
}

/* One icon rotated, not two swapped: a swap cannot animate. --dur-2 is the
   ramp's expand/collapse step; reduced motion is handled globally in
   design-tokens.css and is not re-stated here. */
.shelf-group-chevron {
  transition: transform var(--dur-2) var(--ease-standard);
}

.shelf-group-chevron--open {
  transform: rotate(90deg);
}

/* Column 2. The same reserved width the row thumbnails occupy, so a header's
   label starts at the same x as the names under it; the axis glyph sits at its
   left edge rather than centred, or it would drift away from the label. */
.shelf-group-mark {
  width: var(--entity-thumb);
  display: inline-flex;
  align-items: center;
  /* 0.7 on the canvas colour, the same secondary weight `.shelf-col`
     carries and a defined theme key — `on-surface-variant` is Vuetify's and is
     not in this app's palettes. */
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* Rank is size, weight and tracking, never opacity: this label is at FULL
   strength above full-strength row names, and it is the case and the tracking
   that rank an 11px label above a 14px sentence-case one. */
.shelf-group-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
}

/* A folder header's label is a literal filesystem path. §3 gives the mono face
   to paths, and uppercasing one misstates the string, so this variant drops the
   case change and the tracking and takes the larger of the two ramp steps. */
.shelf-group-label--path {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  letter-spacing: normal;
  text-transform: none;
}

/* Column 4, where the row's own status glyph sits, so both align on one right
   edge. The count is meta ON the header rather than the header's label, so it
   takes the row meta line's alpha, not the label's full strength. */
.shelf-group-count {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  margin-left: var(--space-3);
}

/* The box, the rail and the indent come from the shared row system
   (SideBar.global.css, visual-language.md §5.1) via `.ps-row`; only the
   columns and the vertical rhythm are the shelf's own. Column 1 stays
   reserved and empty: grouping fills it, and a column that appears later
   would move every label sideways.

   The four data columns are FIXED widths, not `auto`. Grouping makes one grid
   per group, so `auto` tracks would be measured against that group's contents
   alone and the columns would step sideways from one folder to the next —
   which is the alignment the whole change is for. The three figures are the
   resolved design's own (ui_kits/app/model-shelf.html, row anatomy);
   `Assigned to` has no width there because that design carries assignment as
   a ring on the identity mark instead, so it is stated in the mark's own
   token — two marks wide is what a fanned pair needs (#892). */
.shelf {
  --shelf-col-kind: 64px;
  --shelf-col-base: 84px;
  --shelf-col-assigned: calc(var(--entity-thumb) * 2);
  --shelf-col-size: 74px;
}

.shelf-row,
.shelf-head-row {
  display: grid;
  grid-template-columns:
    var(--gutter-glyph)
    var(--entity-thumb)
    minmax(0, 1fr)
    var(--shelf-col-kind)
    var(--shelf-col-base)
    var(--shelf-col-assigned)
    var(--shelf-col-size)
    var(--gutter-glyph);
  align-items: center;
  gap: var(--space-2);
  padding-top: var(--space-2);
  padding-bottom: var(--space-2);
}

.shelf-row {
  transition: background var(--dur-1) var(--ease-standard);
  /* Native windowing: the browser skips layout and paint for rows outside the
     viewport, which is what 1,800 rows need and is two lines rather than a
     virtual scroller. The size hint is only the first guess — `auto` makes the
     browser remember each row's real height after it has painted once. */
  content-visibility: auto;
  contain-intrinsic-size: auto calc(var(--entity-thumb) + var(--space-5));
}

.shelf-row:hover {
  background: var(--hover-wash);
}

/* One hairline, which is what makes this read as a head rather than as a first
   row. It does not double up with the group header's: that one is a sticky band
   ABOVE this, a row's height away, not at the same boundary. Not sticky itself
   — the folder header is the shelf's one sticky level, and a second needs the
   stacking arithmetic no token knows (see the group-header block above). */
.shelf-head-row {
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

/* Rank is case, size and tracking, never opacity — the same treatment the
   group label carries, so a column name reads as one rank above the figures
   under it without being drawn darker than them. */
.shelf-head-cell {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-background), 0.7);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shelf-head-cell--size {
  text-align: right;
}

/* The figures the header names. 0.7, not 0.6: at 12px the lower alpha measures
   4.07:1 on the light canvas and misses the 4.5:1 floor. */
.shelf-col {
  min-width: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.7);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shelf-col--assigned {
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.shelf-col--size {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* An absent value, said in words rather than left blank: a blank cell reads as
   a rendering gap, and "which of these has no base model" is a question the
   column exists to answer. Italic, because rank here is style and not another
   step down in contrast. */
.shelf-col-none {
  font-style: italic;
}

.shelf-row-ident {
  display: inline-flex;
  justify-content: center;
  color: rgba(var(--v-theme-on-background), 0.7);
}

.shelf-row-label {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* The run's step count, riding with the name. `flex: none` so it is the name
   that ellipsises when the column is tight and never the count, which is two
   characters and the reason the row is stacked. */
.shelf-row-steps {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* One icon rotated, not two swapped, the same as the group chevron — the class
   was already bound here and had nothing to do. */
.shelf-row-steps-chevron {
  transition: transform var(--dur-2) var(--ease-standard);
}

.shelf-row-steps-chevron--open {
  transform: rotate(90deg);
}

.shelf-row-name {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The unnamed third. Mono at regular weight, at FULL strength: §3 gives the
   mono face to file paths, and a filename-derived name is one — so this says
   what the string is rather than demoting it. Rank is never opacity (§5.1),
   and 37% of rows faded would be a column of ghosts. */
.shelf-row-name--derived {
  font-family: var(--font-mono);
  font-weight: var(--weight-regular);
}

/* No `margin-left` any more: the status glyph is a column of its own now, and
   a margin inside a `--gutter-glyph` track would leave the icon 4px to sit in
   and push it out of its own cell. The grid's gap is what separates it. */
.shelf-row-loc {
  display: inline-flex;
  width: var(--gutter-glyph);
}

.shelf-row-loc--present {
  visibility: hidden;
}

.shelf-row-loc--missing,
.shelf-row-loc--forgotten {
  color: rgb(var(--v-theme-error));
}

.shelf-row-loc--unreachable {
  color: rgba(var(--v-theme-on-background), 0.7);
}
</style>
