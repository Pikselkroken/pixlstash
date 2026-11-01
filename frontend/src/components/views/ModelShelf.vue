<template>
  <!-- role="region" because a bare div is role `generic`, which prohibits an
       accessible name: without it the aria-label is dropped and #shelf-help is
       never announced, so the whole paragraph below is dead weight. -->
  <div
    ref="rootEl"
    class="shelf"
    :style="columnStyle"
    role="region"
    tabindex="-1"
    aria-label="Model shelf"
  >
    <p id="shelf-help" class="visually-hidden">
      Every adapter and checkpoint PixlStash has found on this machine, one row
      each: its Type, name, base model, size, the day it was added, and how many
      sets hold it. The letter at a row's start is its Type: C for a checkpoint,
      L for a LoRA. Sort and Group by live in the Sort panel; Show chooses which
      kinds are listed and which base models. The Name, Base, Size and Added
      headings sort the list too. Every column but Name begins with a handle
      that resizes it: Left and Right move the edge, Home and End take it to its
      widest and narrowest, Enter puts it back. The bar ends in the app-wide
      controls: Settings and the Models toggle. Nothing on this screen
      can be undone. A heart after a name marks a favourite. A name in italics
      has not been given one. A row that stands for a training run says how many
      files it holds; Right and Left open and close it. Right-click a row for
      everything that can be done to it. Escape clears the selection.
    </p>

    <!-- One announcement for a resort, because the rows reorder silently: the
         two buttons' own names change, but a reader who is not on them hears
         nothing. Group collapse gets none, because `aria-expanded` on the
         header already says it and a second announcer double-speaks. -->
    <p class="visually-hidden" role="status">{{ sortAnnouncement }}</p>

    <!-- The toolbar is browse only (#2168): Sort, Filter, the name search and
         the app's own controls. Adding, managing folders, and the training-runs
         view are the sidebar's; the verbs are on the selection pill, the row
         menu and the shortcuts. No `Models` title: the title bar's segment and
         the sidebar already name the view. -->
    <div class="shelf-toolbar">
      <!-- An open run's status, Stop and back arrow (TrainingRunLive teleports
           them here): the design puts run status in the shelf's own bar. -->
      <div v-if="runOpen" id="shelf-run-bar" class="shelf-run-bar"></div>

      <span
        v-if="!runOpen"
        class="shelf-sub shelf-fold-1262"
        >{{ isShelfTab ? countLabel : runsCountLabel }}</span
      >

      <template v-if="isShelfTab">
        <v-menu
          v-if="!isSetGrid"
          v-model="sortMenuOpen"
          :close-on-content-click="false"
          location="bottom start"
          origin="top start"
          :offset="8"
          transition="scale-transition"
        >
          <!-- The split-button: a direction toggle welded to a menu trigger.
               `role="group"` names the pair; v-menu returns focus to the
               trigger on Escape, on an outside click and on a selection. -->
          <template #activator="{ props: menuProps }">
            <div
              class="bar-split-button"
              :class="{ 'bar-split-button--open': sortMenuOpen }"
              role="group"
              aria-label="Sort"
            >
              <AppBarButton
                class="bar-split-toggle"
                :icon="directionIcon"
                :tooltip="directionLabel"
                @click.stop="toggleDirection"
              />
              <AppBarButton
                v-bind="menuProps"
                class="bar-split-menu"
                :icon="activeSort.icon"
                chevron
                aria-haspopup="dialog"
                :aria-expanded="sortMenuOpen"
                :tooltip="sortButtonTitle"
              >
                <span class="bar-btn-value">{{ activeSort.label }}</span>
              </AppBarButton>
            </div>
          </template>
          <ShelfSortPanel />
        </v-menu>

        <v-menu
          v-model="showMenuOpen"
          :close-on-content-click="false"
          location="bottom start"
          origin="top start"
          :offset="8"
          transition="scale-transition"
        >
          <!-- The badge counts ACTIVE FILTERS, never results: it is the answer
               to "why is this list short". -->
          <template #activator="{ props: menuProps }">
            <AppBarButton
              v-bind="menuProps"
              class="shelf-show-btn"
              icon="filter-outline"
              chevron
              :badge="store.activeCount > 0 ? store.activeCount : null"
              :active="store.activeCount > 0 && !showMenuOpen"
              :open="showMenuOpen"
              :tooltip="showButtonTitle"
            />
          </template>
          <ShelfShowPanel
            :open="showMenuOpen"
            :focus-name-request="nameFocusRequest"
          />
        </v-menu>
      </template>

      <span class="shelf-spacer"></span>

      <div class="shelf-bar-cluster">
        <NameFilterField
          v-if="isShelfTab"
          ref="nameFieldRef"
          class="shelf-name-bar"
          :model-value="store.filters.name"
          label="Filter models by name"
          @update:model-value="(name) => store.setFilters({ name })"
        />
        <!-- The tail: [separator] [TbGlobalActions]
             (docs/design/toolbar-responsive-decisions.md). -->
        <span class="bar-separator" aria-hidden="true"></span>
        <TbGlobalActions
          rail="models"
          rail-name="Models"
        />
      </div>
    </div>

    <!-- The SECONDARY route into the thumbnail verb, kept rather than removed:
         any file on disk worked before the picker existed, and taking that away
         would be a regression on a shipped feature. It is reached from inside
         the picker (see its `footer-start` slot below), so the library route is
         the one that is offered first. A real <input type=file> because it is
         the platform's own chooser and keyboard-accessible for free; hidden
         rather than styled, because the button beside it is the affordance. -->
    <input
      ref="iconInputRef"
      class="visually-hidden"
      type="file"
      accept="image/png,image/jpeg,image/webp"
      @change="onIconChosen"
    />

    <!-- Set Thumbnail…, the primary route: a picture from the library, chosen
         by project / character / set. A picture that PixlStash can see is one
         it can search, reuse and repair; the file the OS chooser returns is one
         it never sees again. -->
    <PicturePicker
      :open="thumbnailPickerOpen"
      :subtitle="thumbnailSubject"
      offer-videos
      @close="thumbnailPickerOpen = false"
      @pick="onThumbnailPicked"
    >
      <template #footer-start>
        <AppButton variant="ghost" @click="pickIconFile"
          >Choose a file…</AppButton
        >
      </template>
    </PicturePicker>

    <!-- `inert` while a move runs, not merely dimmed. A move repoints
         `model_file` rows under the list, so a verb pressed mid-move acts on a
         location that is about to be wrong. A veil that only looks disabled
         leaves every row clickable and every one of them in the tab order,
         which is worse than no veil at all. The toolbar stays live: Show and
         Sort still answer correctly while files are in flight. -->
    <div
      v-if="isShelfTab"
      id="shelf-panel-shelf"
      class="shelf-body"
      role="region"
      aria-label="Model list"
      aria-describedby="shelf-help"
      :inert="moves.running || undefined"
    >
      <!-- The visible half of the same statement. `inert` on the wrapper is
           what actually stops the interaction; this is what says so. -->
      <div v-if="moves.running" class="shelf-dim" aria-hidden="true"></div>
      <!-- The closed Models rail's handle, the one Workflows shows
           (visual-language "Closed inspector"): it names the selection and
           bounces on a new one. With nothing selected it says "All models",
           without the selection stripe, and bounces when a card's tray
           opens. -->
      <InspectorEdgeTab
        v-if="!sidebarStore.modelsRailOpen"
        :label="railLabel || 'All models'"
        :plain="!railLabel"
        :name="
          railLabel
            ? `Show models: ${railLabel}`
            : `Show all models (${railCount.toLocaleString()})`
        "
        :nudge="sidebarStore.inspectorNudge"
        data-testid="models-rail-handle"
        @open="openRailFromTab"
      />
      <!-- An unplugged drive states its scope ONCE, here, rather than through
           300 rows each carrying the same mark. The rows still take the offline
           treatment - that is what tells one row from its neighbour - but the
           REASON is a fact about the mount and belongs to the mount.

           One line, one verb, dismissible: nothing here is broken and nothing
           needs fixing, so the reader who has read it once gets to put it
           away. It comes back when the shelf is refetched, because a drive that
           is still unplugged is still worth saying once. -->
      <p v-if="offlineNote && !offlineDismissed" class="shelf-banner">
        <Tooltip :text="offlineMountPaths" activator="parent" />
        <v-icon size="16">mdi-power-plug-off-outline</v-icon>
        <span class="shelf-banner-text">{{ offlineNote }}</span>
        <span class="shelf-spacer"></span>
        <AppBarButton
          icon="close"
          tooltip="Dismiss"
          aria-label="Dismiss the offline notice"
          @click="offlineDismissed = true"
        />
      </p>
      <!-- Models ComfyUI's own loaders cannot see here (#2205). A note, not an
           error: PixlStash's loaders find them by hash wherever they are. -->
      <p
        v-if="misplacedIds.size"
        class="shelf-banner"
        role="status"
        data-testid="shelf-misplaced-note"
      >
        <v-icon size="16" class="shelf-banner-warn" aria-hidden="true"
          >mdi-alert-outline</v-icon
        >
        <span class="shelf-banner-text"
          >{{ misplacedIds.size }}
          {{ misplacedIds.size === 1 ? "model in this folder is" : "models in this folder are" }}
          outside the folders ComfyUI loads from, so ComfyUI does not list
          {{ misplacedIds.size === 1 ? "it" : "them" }}. PixlStash's own loaders
          still find {{ misplacedIds.size === 1 ? "it" : "them" }}.</span
        >
        <span class="shelf-spacer"></span>
        <AppButton
          size="sm"
          variant="outline"
          data-testid="shelf-select-misplaced"
          @click="selectMisplaced"
          >Select {{ misplacedIds.size === 1 ? "it" : "them" }}</AppButton
        >
      </p>
      <!-- The set grid, ahead of every row-list state: its groups OVERLAP, so
           it is a different screen rather than a banded version of this one. It
           still reads `visibleRows`, so Show and the filters keep applying. -->
      <!-- `firstRead`, not `loading`: a refetch after an edit (assign, stack,
           rename) must leave the list mounted. Swapping it for the state line
           destroys the scrollport and drops the reader back at the top. -->
      <ModelSetGrid
        v-if="isSetGrid && !firstRead && !store.error"
        ref="setGridRef"
        @works-with="showWorksWith"
        @menu="openGridMenu"
        @rename="startRenameSelected"
        @set-menu="({ x, y, el }) => setBarRef?.openContextMenu(x, y, el)"
        @rename-set="startRenameSet"
        @replace-missing="(heads) => (replacingHeads = heads)"
        @missing-menu="({ x, y, el }) => missingBarRef?.openContextMenu(x, y, el)"
      />
      <p v-else-if="firstRead" class="shelf-state">Reading the shelf…</p>
      <p v-else-if="store.error" class="shelf-state" role="alert">
        {{ store.error }}
      </p>
      <!-- Three empty states, deliberately distinct. Conflating "you filtered
           everything out" with "there is nothing here" is the failure: the
           first is one click from fixed and the second is not, so only the
           first two offer Reset. -->
      <div v-else-if="store.nothingSelected" class="shelf-state">
        <p>Nothing is selected in Show.</p>
        <AppButton @click="store.resetFilters()">Reset filters</AppButton>
      </div>
      <!-- Ahead of the terminal state, and gated on the selection rather than
           on `rows`: a narrowed selection only FETCHES the blocks it asks for,
           so a shelf reopened with one block ticked and nothing in it arrives
           with `rows` empty for a machine holding 1,800 adapters. Reading that
           as "there is nothing here" dead-ends the reader with no Reset - the
           exact conflation the note above forbids. Reset refetches every block
           and the terminal state below then says so truthfully. -->
      <div
        v-else-if="!store.visibleRows.length && store.activeCount"
        class="shelf-state"
      >
        <p>No models match these filters.</p>
        <AppButton @click="store.resetFilters()">Reset filters</AppButton>
      </div>
      <!-- Loose needs the sets to know which models are in none of them. Until
           they are read, "In no set yet" is a question not an answer. -->
      <div
        v-else-if="!store.visibleRows.length && store.place === 'loose' && !store.setsKnown"
        class="shelf-state"
      >
        <p>Reading the sets…</p>
      </div>
      <div v-else-if="!store.visibleRows.length" class="shelf-state">
        <p>{{ emptyPlaceLine }}</p>
      </div>

      <!-- The row itself is not a focus stop unless it holds the roving one:
           1,800 tab stops would be a trap, and a row's verbs are on its context
           menu rather than inside it. Group headers stay stops too, so Tab
           still moves group to group. -->
      <template v-else>
        <!-- The column headings, drawn ONCE for the view and sticky above
             everything under them. Once per group is what the grid semantics
             want - a `columnheader` heads the grid it is in, which is why the
             hidden header row below is repeated per `<ul>` - but a visible
             strip repeated over eight folders would be eight identical bands
             of chrome down a list, and the widths are shared anyway.

             So this is a control group, not the grid's header: a `group` of
             buttons that set the sort and separators that size the columns.
             The grid keeps its own hidden headings, and those now carry
             `aria-sort`, so a reader inside the treegrid hears the order
             without this strip having to lie about being part of it. -->
        <div
          class="shelf-head"
          role="group"
          aria-label="Sort and size the columns"
        >
          <!-- Matches `.shelf-row-ident`, which holds the mark, so the
               headings keep their left edges over the names.
               There is nothing to name: the mark IS the row's identity and the
               hidden `Model` columnheader already says so. -->
          <span class="shelf-head-ident" aria-hidden="true"></span>
          <span class="shelf-head-col shelf-head-col--label">
            <button
              class="section-label shelf-head-cell"
              :class="{
                'shelf-head-cell--on': store.view.sortKey === NAME_COLUMN.sort,
              }"
              type="button"
              :aria-label="columnSortLabel(NAME_COLUMN)"
              @click="sortByColumn(NAME_COLUMN.sort)"
            >
              <Tooltip
                :text="columnSortLabel(NAME_COLUMN)"
                activator="parent"
                :describe="false"
              />
              <span class="shelf-head-text">{{ NAME_COLUMN.label }}</span>
              <v-icon class="shelf-head-arrow" size="14" aria-hidden="true">{{
                directionIcon
              }}</v-icon>
            </button>
          </span>
          <!-- The heading and its grip are one element wide, because the grip
               is positioned against the heading's own right edge. Without the
               wrapper it would either be a flex item of its own - widening
               every column past what the rows draw - or clipped by the
               heading's overflow. -->
          <span
            v-for="col in SHELF_COLUMNS"
            :key="col.key"
            class="shelf-head-col"
            :class="`shelf-head-col--${col.key}`"
          >
            <button
              class="section-label shelf-head-cell"
              :class="{
                'shelf-head-cell--on': store.view.sortKey === col.sort,
              }"
              type="button"
              :aria-label="columnSortLabel(col)"
              @click="sortByColumn(col.sort)"
            >
              <Tooltip
                :text="columnSortLabel(col)"
                activator="parent"
                :describe="false"
              />
              <span class="shelf-head-text">{{ col.label }}</span>
              <v-icon class="shelf-head-arrow" size="14" aria-hidden="true">{{
                directionIcon
              }}</v-icon>
            </button>
            <!-- `separator` with a value, which is the window-splitter pattern
                 and the only reason this is focusable: a grip that answered
                 the pointer alone would put the column widths out of reach of
                 anyone not using one. Left, Right, Home and End move the edge;
                 Enter and a double-click put it back, which is the only way
                 back from a mis-drag. -->
            <span
              class="shelf-head-grip"
              :class="{ 'shelf-head-grip--on': resizing?.key === col.key }"
              role="separator"
              aria-orientation="vertical"
              :aria-label="`Resize the ${col.label} column`"
              :aria-valuenow="store.view.columnWidths[col.key]"
              :aria-valuetext="`${store.view.columnWidths[col.key]} pixels`"
              :aria-valuemin="MIN_COLUMN_WIDTHS[col.key]"
              :aria-valuemax="widenable(col.key, MAX_COLUMN_WIDTH)"
              tabindex="0"
              @pointerdown="startResize(col.key, $event)"
              @pointermove="onResizeMove"
              @pointerup="endResize"
              @pointercancel="endResize"
              @lostpointercapture="endResize"
              @dblclick="resetColumn(col.key)"
              @keydown="onGripKeydown(col.key, $event)"
            >
              <Tooltip
                :text="`Drag to resize the ${col.label} column, double-click to reset it`"
                activator="parent"
              />
            </span>
          </span>
        </div>
        <!-- The scrollport, and the reason the strip above it is a sibling
             rather than a sticky child: a scroll container's scrollbar runs
             the full height of the container, so a header inside one has the
             bar climbing past it to the top of the panel - pointing at rows
             that are not there. Outside it, the bar starts where the rows do.

             It costs the strip nothing: it was sticky only to stay put, and a
             sibling above the scrollport is already put. The two stay aligned
             because both reserve the same scrollbar gutter. -->
        <div
          class="shelf-scroll"
          @pointerdown="
            pressedBackground =
              $event.button === 0 && isListBackground($event)
          "
          @click="onListBackgroundClick"
        >
          <div
            v-for="group in shownGroups"
            :key="group.key"
            class="shelf-group"
          >
            <!-- The header IS the button, so its whole width is the drop target
                 and the collapse control. A heading as well as a button, so a
                 screen reader can jump group to group by heading. -->
            <h3 v-if="grouped" class="shelf-group-heading">
              <button
                class="shelf-group-btn"
                type="button"
                :aria-expanded="!store.isCollapsed(group.key)"
                :aria-label="groupLabel(group)"
                @click="store.toggleGroup(group.key)"
              >
                <v-icon
                  size="16"
                  class="shelf-group-chevron"
                  :class="{
                    'shelf-group-chevron--open': !store.isCollapsed(group.key),
                  }"
                  >mdi-chevron-right</v-icon
                >
                <!-- The GROUP's own glyph where it has one, and the axis's only
                     where it does not. Under `Folder` that is the TIER's - one
                     mdi folder family, never a hand-drawn box - and an
                     unreachable folder wears the disconnected mark instead, which
                     is the shape half of the offline treatment. Under `Feature`
                     it is the feature's own mark, or eight headers read as eight
                     copies of one star. The fallback is what the unset group and
                     an unrecognised value get, both of which mean "no feature
                     here to name" and so are exactly the axis. -->
                <v-icon size="16" class="shelf-group-mark">{{
                  group.icon || GROUP_BY_LABELS[store.groupAxis].icon
                }}</v-icon>
                <!-- The label and everything that qualifies it. The chips are
                     WORDS on purpose: the rail's hue groups the folders on one
                     disk and the tier's glyph gives it a shape, but neither
                     survives greyscale on its own, and only the chip is readable
                     out loud. -->
                <span
                  class="shelf-group-label"
                  :class="`shelf-group-label--${group.labelKind}`"
                  >{{ group.label }}</span
                >
                <!-- Why the rows are not in date order: the favourites lead. -->
                <span
                  v-if="group.favourites && store.view.favouritesFirst"
                  class="shelf-chip"
                  >{{ group.favourites }} favourite{{
                    group.favourites === 1 ? "" : "s"
                  }}
                  first</span
                >
                <span class="shelf-spacer"></span>
                <span class="shelf-group-count">{{
                  modelCount(group.rows.length)
                }}</span>
              </button>
            </h3>

            <!-- `role="treegrid"`, which is what the rows became once they got
                 columns. A listbox cannot carry a `columnheader`, so nothing
                 named what the figures in a row meant (#891); a treegrid can,
                 and its keyboard model is already the one this list implements -
                 Up/Down walk rows, Right/Left open and close a run, and
                 `aria-multiselectable` + `aria-selected` still say what is
                 picked. A run's other steps are CHILD rows, which is the "tree"
                 half: they carry `aria-level="2"` because the DOM draws them as
                 siblings of their cover rather than nesting them. -->
            <ul
              v-if="!grouped || !store.isCollapsed(group.key)"
              class="shelf-list"
              role="treegrid"
              aria-multiselectable="true"
              :aria-label="grouped ? group.label : 'Models'"
            >
              <!-- The column names, on every grid and drawn on none of them.
                   `columnheader`s head the grid they are in and nothing else, so
                   grouping needs one strip per group, and eight identical
                   visible bands down a grouped list is exactly what the header
                   strip above the list exists to avoid. These stay hidden and
                   carry the `aria-sort` for their own grid, so the order is
                   readable from inside the treegrid rather than only from the
                   control group above it. -->
              <li class="visually-hidden" role="row">
                <span role="columnheader" :aria-sort="columnSortState('name')"
                  >Name</span
                >
                <span role="columnheader" :aria-sort="columnSortState('type')"
                  >Type</span
                >
                <span
                  role="columnheader"
                  :aria-sort="columnSortState('base_model')"
                  >Base</span
                >
                <span role="columnheader" :aria-sort="columnSortState('size')"
                  >Size</span
                >
                <!-- Named for the axis it is drawn in, because that axis changes
                     with the sort: a reader who hears `Size` then `Date added`
                     knows which of the two dates the column holds. -->
                <span
                  role="columnheader"
                  :aria-sort="columnSortState(DATE_COLUMN.sort)"
                  >{{ DATE_COLUMN.label }}</span
                >
                <span role="columnheader" :aria-sort="columnSortState('sets')"
                  >Sets</span
                >
              </li>
              <!-- The `v-for` sits on a wrapper, not on the row, so a stack's
                   expanded members can be siblings of their cover inside the
                   same iteration and still see `row`. The wrapper is a
                   `MemoBlock` rather than a template with `v-memo`, because
                   this list is nested in the groups' and the directive's one
                   cache slot per list would never hit (`rowMemo`). -->
              <MemoBlock
                v-for="row in group.rows"
                :key="row.rowKey"
                :deps="rowMemo(row)"
              >
                <li
                  class="shelf-row fav-host"
                  :class="{
                    'shelf-row--selected': store.isSelected(row.id),
                    'shelf-row--offline': row.locState === 'unreachable',
                    'shelf-row--broken': BROKEN_STATES.has(row.locState),
                    'shelf-row--cut': store.isCut(row.id),
                    'shelf-row--locked': !!lockedNote(row),
                  }"
                  role="row"
                  aria-level="1"
                  :aria-expanded="
                    row.memberCount > 1 ? isStackOpen(row.stack_id) : undefined
                  "
                  :aria-selected="store.isSelected(row.id)"
                  aria-keyshortcuts="F2 Shift+F2 Shift+F10 H"
                  :tabindex="row.rowKey === rovingRowKey ? 0 : -1"
                  :data-row-key="row.rowKey"
                  @click="pickRow(row, $event)"
                  @pointerdown="onRowPointerDown($event, row)"
                  @contextmenu.prevent="openRowMenu(row, $event)"
                  @keydown="onRowKeydown(row, $event)"
                  @focus="focusedRowKey = row.rowKey"
                >
                  <span role="gridcell" class="shelf-row-ident">
                    <!-- Deck ticks behind the mark say "this is more than one
                         file" before the count is read, exactly as they do on a
                         picture tile. The ticks fill their nearest positioned
                         box, so they get one the mark's size: against the whole
                         slot they drew a grey pill over it instead of a deck
                         behind it. -->
                    <span class="shelf-row-mark">
                      <StackEdgeTicks
                        v-if="row.memberCount > 1"
                        :count="row.memberCount"
                      />
                      <!-- A file that is gone has nothing to picture, so the
                           absence glyph takes the mark's place rather than
                           leading the name. -->
                      <Tooltip
                        v-if="BROKEN_STATES.has(row.locState)"
                        :text="LOC_TITLE[row.locState]"
                      >
                        <template #activator="{ props: tipProps }">
                          <v-icon
                            v-bind="tipProps"
                            class="shelf-row-loc shelf-row-gone"
                            :class="`shelf-row-loc--${row.locState}`"
                            >{{ LOC_ICON[row.locState] }}</v-icon
                          >
                        </template>
                      </Tooltip>
                      <!-- The model's own picture, else the face of the person
                           or set it is attached to, else the generated mark
                           (#2168 took the ring off the list; `bare` keeps it
                           off). -->
                      <ModelMark v-else :row="row" :ring="ringFor(row)" bare />
                      <!-- The lock rides the mark, where the eye already lands
                           to tell one model from another; the hatch behind the
                           row is what reads from across the list. The top-start
                           corner, because the other three hold the version
                           pill, the deck ticks and the cover chip. The words
                           stay on the file line, so this is aria-hidden. -->
                      <span
                        v-if="lockedNote(row)"
                        class="shelf-row-lock"
                        aria-hidden="true"
                        ><Tooltip
                          text="In a locked folder"
                          activator="parent"
                          :describe="false"
                        /><v-icon>mdi-lock</v-icon></span
                      >
                    </span>
                    <!-- Which file the run is being drawn from, said in words
                         rather than by position: once the strip is open the
                         reader is looking at six rows and choosing between
                         them, and "the top one" is not an answer a screen
                         reader can hear. On the thumbnail's bottom-left corner,
                         the grid's own cover chip in the grid's own place,
                         rather than among the name's chips, where it pushed the
                         line into the text around it. Only while the stack is
                         OPEN - on a collapsed run the cover is the only row
                         there is, so the chip would be noise on every stacked
                         row of the shelf. And only on the REAL cover: a filter
                         can hide position 0, and `collapseStacks` then draws
                         the lowest surviving member at the top - which is the
                         run's stand-in for the moment, not the file the owner
                         chose. -->
                    <span
                      v-if="
                        row.memberCount > 1 &&
                        isStackOpen(row.stack_id) &&
                        row.stack_position === 0
                      "
                      class="stack-cover-flag"
                      ><Tooltip
                        text="This file is what the shelf draws for the whole run."
                        activator="parent"
                      />Cover</span
                    >
                  </span>
                  <span role="gridcell" class="shelf-row-label">
                    <!-- The absence glyph leads the line, because it changes what
                         everything after it means: the name is still true, the
                         file behind it is out of reach. A file that is gone
                         draws it in the mark's place instead. -->
                    <Tooltip
                      v-if="
                        row.locState !== 'present' &&
                        !BROKEN_STATES.has(row.locState)
                      "
                      :text="LOC_TITLE[row.locState]"
                    >
                      <template #activator="{ props: tipProps }">
                        <v-icon
                          v-bind="tipProps"
                          size="14"
                          class="shelf-row-loc"
                          :class="`shelf-row-loc--${row.locState}`"
                          >{{ LOC_ICON[row.locState] }}</v-icon
                        >
                      </template>
                    </Tooltip>
                    <!-- The name is a FIELD, and it has four states, because
                         naming is the commonest fix on this shelf and the reader
                         has to be able to tell "somebody chose this" from "we
                         guessed" from "there is nothing here" without opening
                         anything. Rendering all four as one string is what made
                         an unnamed row look inert, and an inert row never gets
                         named (#897). Type and the accent rule carry the
                         distinction; only the file's own string gets a tag on
                         top, so the shelf is not a column of chips. -->
                    <input
                      v-if="editingRowKey === row.rowKey"
                      v-model="editingName"
                      class="shelf-row-rename"
                      type="text"
                      :placeholder="row.name.text || 'Name this model'"
                      :aria-label="`Name for ${row.filename || 'this model'}`"
                      @click.stop
                      @keydown="onRenameKeydown"
                      @blur="commitRename"
                    />
                    <template v-else>
                      <span
                        class="shelf-row-name"
                        :class="`shelf-row-name--${row.name.state}`"
                        @dblclick.stop="startRename(row)"
                        >{{ row.name.text || "Name this model" }}</span
                      >
                      <v-icon
                        v-if="store.isFavouriteRow(row)"
                        class="shelf-row-heart"
                        size="12"
                        role="img"
                        aria-label="Favourite"
                        >mdi-heart</v-icon
                      >
                      <VerdictIcon
                        v-if="rowVerdict(row)"
                        :verdict="rowVerdict(row).verdict"
                        :label="rowVerdict(row).tooltip"
                      />
                      <span
                        v-if="row.name.state === 'from-file'"
                        class="shelf-name-tag"
                        ><Tooltip
                          :text="FROM_FILE_TAG_TITLE"
                          activator="parent"
                        />from filename</span
                      >
                    </template>
                    <!-- Beside the name rather than in a column of its own: the
                         count belongs to the run's identity, and only stacked
                         rows carry one, so a track for it would be empty on
                         nearly every row. -->
                    <button
                      v-if="row.memberCount > 1"
                      class="shelf-stack-badge"
                      type="button"
                      :aria-expanded="isStackOpen(row.stack_id)"
                      @click.stop="toggleStack(row.stack_id)"
                    >
                      <Tooltip
                        :text="`${row.memberCount} files in this run`"
                        activator="parent"
                      />
                      {{ row.memberCount }}
                      <v-icon
                        size="13"
                        :class="{
                          'shelf-stack-chevron--open': isStackOpen(
                            row.stack_id,
                          ),
                        }"
                        >mdi-chevron-right</v-icon
                      >
                    </button>
                    <!-- The precision the file was stored at, which
                         `deriveModelName` has just taken out of the name to
                         its left. Two quant variants of one model are two rows
                         reading the same name, and this chip is the whole of
                         what tells them apart - so it is drawn on EVERY row
                         that records one, never only where two of them meet: a
                         name that changed when an unrelated file was
                         downloaded would be worse than no badge at all.
                         A row recording no precision gets no chip - not an
                         empty one and not `UNKNOWN`. -->
                    <span
                      v-if="quantBadge(row.quant)"
                      class="shelf-chip shelf-chip--quant"
                      ><Tooltip
                        :text="quantBadge(row.quant).title"
                        activator="parent"
                      />{{ quantBadge(row.quant).label }}</span
                    >
                    <!-- The step, on any row that is not a stack cover.
                         `deriveModelName` strips the trailing step from the
                         filename on the stated grounds that "the step is parsed
                         into its own field" - and that field was never rendered
                         anywhere except inside an expanded stack. So two
                         checkpoints of one run that the stack detector did not
                         fold both read `clementine-zib-3b`, with nothing on the
                         row telling them apart: exactly the outcome stripping it
                         was meant to prevent. -->
                    <span
                      v-if="stepLabel(row)"
                      class="shelf-chip shelf-chip--step"
                      >{{ stepLabel(row) }}</span
                    >
                    <!-- What to type in a prompt to call this model up. Mono
                         when the word is on record, because it is typed
                         character by character like the filename under it; the
                         dashed "nothing here yet" chip when it is only the name
                         of the person the model is assigned to. The prefix is
                         for a screen reader, which otherwise reads a bare word
                         after the step. -->
                    <span
                      v-if="triggerChip(row)"
                      class="shelf-chip shelf-chip--trigger"
                      :class="{ 'shelf-chip--none': triggerChip(row).isDefault }"
                      ><Tooltip
                        :text="triggerChip(row).title"
                        activator="parent"
                      /><span class="visually-hidden">Trigger word: </span
                      >{{ triggerChip(row).label }}</span
                    >
                    <!-- What the scan you just ran brought in. The SUCCESS
                         treatment, because an arrival is a good outcome - and
                         nothing else on a row is green, so it reads without a
                         key. Cleared by the next fetch, so it is never a stale
                         mark from three refreshes ago. -->
                    <span v-if="row.isNew" class="shelf-row-new">New</span>
                    <!-- Drawn only while Show asks for the hidden models: this
                         library leaves the row out otherwise. The neutral chip,
                         because it is a state of the listing and not of the
                         file. -->
                    <span v-if="row.hidden" class="shelf-chip">Hidden</span>
                    <!-- A run PixlStash trained and imported. Neutral, like
                         Hidden: it says where the file came from, not that
                         anything needs doing. -->
                    <span
                      v-if="row.training_run_id"
                      class="shelf-chip shelf-chip--trained"
                      ><Tooltip
                        text="Trained in PixlStash"
                        activator="parent"
                      />Trained</span
                    >
                    <!-- The filename, on its own line under the name. It is what
                         the file is actually called, which the name above it may
                         well not be, and it is the string the reader pastes into
                         a ComfyUI node - so it is monospaced and it is always
                         there rather than living in a tooltip. What IS in the
                         tooltip is the folder: no header names it since the
                         folder grouping went (#2168), so this line is the one
                         place in the list that can say where the file is. The
                         file's SHA-256 follows the paths (`modelFileTitle`). -->
                    <span class="shelf-row-file">
                      <Tooltip
                        :text="modelFileTitle(row)"
                        activator="parent"
                      />
                      {{ row.filename
                      }}<template v-if="LOC_NOTE[row.locState]">
                        · {{ LOC_NOTE[row.locState] }}</template
                      ><template v-else-if="lockedNote(row)">
                        · {{ lockedNote(row) }}</template
                      ><!-- The same bytes written twice. It rides the file line
                        rather than taking a chip of its own because it is a
                        fact ABOUT the file, and because the tooltip already
                        beside it is what answers the question the count
                        raises: where is the other one.
                        `copies` and not `presentCopies(row.locations)`: the
                        store counts before the folder axis narrows a draw to
                        its own single copy, so the count reads the same on
                        every axis instead of collapsing to `1` on the one axis
                        that draws the duplicate twice.
                        --><template v-if="row.copies > 1">
                        · {{ row.copies }} copies</template
                      ><span
                        v-if="misplacedIds.has(row.id)"
                        class="shelf-row-nolist"
                        data-testid="shelf-row-nolist"
                        >ComfyUI does not list it here</span
                      >
                    </span>
                  </span>
                  <span role="gridcell" class="shelf-col shelf-col--kind">
                    <!-- Set in place, like the name and the base model, with
                         the kind the workflows that load it agree on under the
                         chip (#1962). An engine is PixlStash's own and is not
                         curated, so its chip stays a chip. -->
                    <span
                      v-if="row.file_kind === 'engine'"
                      class="shelf-chip"
                      :title="shelfTypeLabel(row)"
                      >{{ shelfTypeLabel(row) }}</span
                    >
                    <KindMenu
                      v-else
                      :file-kind="row.file_kind"
                      :kind-text="shelfTypeLabel(row)"
                      :subject="row.name.text || row.filename || 'this model'"
                      :suggested-kind="row.suggested_kind || ''"
                      :suggested-by="row.suggested_by || 0"
                      :suggested-fields="row.suggested_fields || []"
                      :decided="Boolean(row.kind_decided)"
                      :tabindex="row.rowKey === rovingRowKey ? 0 : -1"
                      @pick="(kind) => setKindFromMenu(row, kind)"
                      @adapter="setKindByDialog(row)"
                      @suggest="(on) => setKindSuggestion(row, on)"
                    />
                  </span>
                  <!-- Base is a COLUMN, not a phrase on a metadata line: it is
                       the field a reader scans a shelf for, and it can only be
                       scanned if it aligns. -->
                  <span role="gridcell" class="shelf-col shelf-col--base">
                    <!-- Double-click edits it here, on the row, for the same
                         reason the name is edited here: this is where the value
                         is read, and "not set" is a value like any other, so it
                         opens the field rather than being the one state you have
                         to go to a dialog for. The dialog stays for the bulk
                         verb, which is a different gesture with a different
                         warning in front of it. -->
                    <BaseModelInput
                      v-if="editingBaseKey === row.rowKey"
                      v-model="editingBase"
                      class="shelf-row-base-edit"
                      placeholder="Base model"
                      :aria-label="`Base model for ${row.filename || 'this model'}`"
                      @click.stop
                      @keydown.stop
                      @confirm="commitBaseModel(true)"
                      @cancel="cancelBaseModel"
                      @blur="commitBaseModel()"
                    />
                    <template v-else>
                      <span
                        v-if="baseModelCell(row).text"
                        class="shelf-base-text"
                        @dblclick.stop="startBaseModelEdit(row)"
                        ><Tooltip
                          v-if="baseModelCell(row).note"
                          :text="baseModelCell(row).note"
                          activator="parent"
                        />{{ baseModelCell(row).text }}</span
                      >
                      <span
                        v-else
                        class="shelf-chip shelf-chip--none"
                        @dblclick.stop="startBaseModelEdit(row)"
                        >not set</span
                      >
                      <!-- A value we inferred rather than one the file or a
                           person stated. Under the label rather than beside it,
                           so a narrow column ellipsises neither, and a button:
                           pointing at it turns "guessed" into "confirm", and
                           clicking writes the guess as the owner's answer. -->
                      <button
                        v-if="baseModelCell(row).guess"
                        type="button"
                        class="shelf-name-tag shelf-base-guess"
                        :aria-label="`Guessed: confirm ${baseModelCell(row).text} as the base model`"
                        :tabindex="row.rowKey === rovingRowKey ? 0 : -1"
                        @click.stop="confirmBaseModel(row)"
                        @dblclick.stop
                        @keydown.enter.space.stop
                      >
                        <Tooltip
                          :text="baseModelCell(row).guess"
                          activator="parent"
                        /><span class="shelf-base-guess-word">guessed</span
                        ><span class="shelf-base-guess-confirm"
                          ><v-icon size="12" aria-hidden="true">mdi-check</v-icon
                          >confirm</span
                        >
                      </button>
                    </template>
                  </span>
                  <span role="gridcell" class="shelf-col shelf-col--size">{{
                    row.file_size ? formatModelSize(row.file_size) : ""
                  }}</span>
                  <!-- The DAY, not the stamp: a column is scanned, and the clock
                       is what stops it being scannable. The full stamp is in the
                       title, which also names which of the two dates this is -
                       the column follows the sort, so the same cell holds
                       `Date added` on one shelf and `File date` on the next. -->
                  <span role="gridcell" class="shelf-col shelf-col--date"
                    ><Tooltip :text="dateTitle(row)" activator="parent" />{{
                      dateCell(row)
                    }}</span
                  >
                  <span role="gridcell" class="shelf-col shelf-col--sets">{{
                    setsCell(row)
                  }}</span>
                </li>

                <!-- The stack's other members - later steps, earlier versions,
                   or both - rendered as ROWS rather than through
                   `StackExpansionStrip`: that component draws picture thumbnails
                   for the dedup queue, and a model file has no thumbnail. A
                   stack's members already ARE shelf rows, so they are drawn as
                   shelf rows - indented, and now selectable, with the same three
                   gestures and the same verb menu the cover rows carry.
                   Selecting the collapsed row still means the whole run: a
                   member is reached only by opening the run and pointing inside
                   it, which is what makes "take this one out" and "make this the
                   cover" gestures at all. -->
                <template
                  v-if="row.memberCount > 1 && isStackOpen(row.stack_id)"
                >
                  <!-- The run's loss, first in its expansion: the curve of the
                       training run these files came from. Only a trained run
                       carries one; a run in another library draws nothing. -->
                  <li
                    v-if="row.training_run_id"
                    class="shelf-run-loss-row"
                    role="row"
                    aria-level="2"
                    data-testid="shelf-run-loss"
                  >
                    <span role="gridcell">
                      <ShelfRunLoss :run-id="row.training_run_id" />
                    </span>
                  </li>
                  <li
                    v-for="member in row.members.slice(1)"
                    :key="memberKey(row, member)"
                    class="shelf-row shelf-row--member"
                    :class="{
                      'shelf-row--selected': store.isSelected(member.id),
                      'shelf-row--cut': store.isCut(member.id),
                    }"
                    role="row"
                    aria-level="2"
                    aria-keyshortcuts="Shift+F10"
                    :aria-selected="store.isSelected(member.id)"
                    :tabindex="memberKey(row, member) === rovingRowKey ? 0 : -1"
                    :data-row-key="memberKey(row, member)"
                    @click="pickMember(row, member, $event)"
                    @pointerdown="onRowPointerDown($event, member)"
                    @contextmenu.prevent="openMemberMenu(row, member, $event)"
                    @keydown="onMemberKeydown(row, member, $event)"
                    @focus="focusedRowKey = memberKey(row, member)"
                  >
                    <span role="gridcell" class="shelf-row-ident">
                      <v-icon size="14">mdi-subdirectory-arrow-right</v-icon>
                    </span>
                    <span role="gridcell" class="shelf-row-label">
                      <span class="shelf-row-name">{{
                        memberLabel(member, row)
                      }}</span>
                      <span class="shelf-row-file"
                        ><Tooltip
                          :text="modelFileTitle(member)"
                          activator="parent"
                        />{{ member.filename }}</span
                      >
                    </span>
                    <!-- A step of a run has no kind or base of its own - those
                         are the run's, one row up - but a grid row still owes a
                         cell per column, and an empty one is the honest way to
                         say "same as the run". -->
                    <span
                      role="gridcell"
                      class="shelf-col shelf-col--kind"
                    ></span>
                    <span
                      role="gridcell"
                      class="shelf-col shelf-col--base"
                    ></span>
                    <span role="gridcell" class="shelf-col shelf-col--size">{{
                      member.file_size ? formatModelSize(member.file_size) : ""
                    }}</span>
                    <!-- A step's OWN date, unlike its kind and base: when a run
                         was saved is the one thing that differs from step to
                         step, and answering it with the run's would print the
                         same stamp down the whole strip. -->
                    <span role="gridcell" class="shelf-col shelf-col--date"
                      ><Tooltip
                        :text="dateTitle(member, true)"
                        activator="parent"
                      />{{ dateCell(member, true) }}</span
                    >
                    <span role="gridcell" class="shelf-col shelf-col--sets"></span>
                  </li>
                </template>
              </MemoBlock>
            </ul>
          </div>
        </div>
      </template>
      <!-- The open folder's drive: how much room is left on it. -->
      <div
        v-if="inFolder"
        class="shelf-status"
        role="status"
        data-testid="shelf-status"
      >
        <template v-if="openDrive">
          <span
            >{{ openDriveName }}: {{ formatModelSize(openDrive.free) }} free of
            {{ formatModelSize(openDrive.total) }}</span
          >
          <span
            class="shelf-status-bar"
            :class="{ 'shelf-status-bar--full': openDrive.used >= 90 }"
            aria-hidden="true"
            ><span :style="{ width: `${openDrive.used}%` }"></span
          ></span>
          <span>{{ openDrive.used }}% full</span>
        </template>
        <span v-else>{{ openDriveName || "This drive" }} could not be measured</span>
      </div>
    </div>

    <!-- The other view of the same destination. `v-if` and not `v-show`: the
         runs grid reloads itself on window focus and tears those listeners down
         in `onBeforeUnmount`, so a hidden-but-mounted panel would keep fetching
         a list nobody is looking at. -->
    <TrainingRuns
      v-else
      id="shelf-panel-runs"
      role="region"
      aria-label="Training runs"
      @set-folder="openAddSource"
    />

    <!-- The pill floats bottom-centre OVER the list, exactly like the photo
         grid's: the list is what the selection was made in, and a docked strip
         between the toolbar and the rows pushed the whole list down every time
         a row was clicked. This wrapper is the float; the pill owns its own
         shape. `pointer-events` is off on the strip and back on for the pill,
         so the rows underneath it stay clickable. -->
    <!-- Over the set grid as well as the row list. That was not always true: it
         was held back while a card stood for a whole SET, because a Delete aimed
         at one would have taken a shared VAE with it. A card stands for its BASE
         MODEL now and a tray row for one file, so everything this bar can be
         aimed at on that screen is exactly one model, which is what its verbs
         write. One bar for both views, so the refusals cannot drift.
         A hand-made set card (#1520) selects its SET instead, which takes the
         second pill below: its verbs touch no file. The two selections can be
         held together (Ctrl+click, Ctrl+A), and then both pills are up side by
         side, each with its own verbs. -->
    <div v-if="isShelfTab" ref="selFloatEl" class="selbar-float">
      <ShelfSelectionBar
        ref="selBarRef"
        :sets="hideableSets"
        :card-keys="gridCardKeys"
        v-on="verbListeners"
      />
      <WorkflowSetSelectionBar
        v-if="isSetGrid"
        ref="setBarRef"
        :sets="hideableSets"
        @hide-sets="store.toggleHiddenSets(hideableSets)"
        @rename="startRenameSet"
        @duplicate="store.duplicateHandMadeSet(store.selectedSets[0])"
        @delete="store.deleteHandMadeSets(store.selectedSets)"
        @merge="({ into, others }) => store.mergeHandMadeSets(into, others)"
        @reset="store.resetHandMadeSet(store.selectedSets[0])"
        @merge-offer="setGridRef?.openOffer(store.selectedSets[0]?.id)"
        @keep-separate="store.keepOutOfHandMadeSet(store.selectedSets[0])"
      />
      <MissingSetSelectionBar
        v-if="isSetGrid"
        ref="missingBarRef"
        :sets="hideableSets"
        @replace="replacingHeads = store.selectedMissing"
        @hide-sets="store.toggleHiddenSets(hideableSets)"
      />
    </div>
    <ReplaceMissingDialog
      :open="replacingHeads.length > 0"
      :heads="replacingHeads"
      @close="replacingHeads = []"
    />
    <!-- The set receipts (#1573): the grid's own pill, lifted clear of the
         selection pill when both are up. Local receipts only - a library
         action's Undo would revert something this screen cannot show. -->
    <ActionReceipt v-if="isShelfTab" local-only :lift-px="receiptLift" />
    <WorkflowSetRenameDialog
      :set="renamingSet"
      @close="renamingSet = null"
      @save="saveSetName"
    />

    <ModelWorksWithDialog :model="worksWithModel" @close="closeWorksWith" />
    <ShelfEditDialog :verb="editVerb" @close="editVerb = ''" />
    <MergeCopiesDialog
      :open="mergeRow !== null"
      :row="mergeRow"
      @close="mergeRow = null"
    />
    <ShelfMoveDialog
      :open="moveOpen"
      :items="moveItems"
      :total-bytes="moveBytes"
      :initial-destination="moveDestination"
      @started="onMoveStarted"
      @close="closeMove"
    />
    <!-- A folder the Folders tree moved or was asked to move (#2206). -->
    <FolderMoveDialog
      :open="folderMoveOpen"
      :source="folderMoveSource"
      :initial-destination="folderMoveDestination"
      :plan="folderMovePlan"
      @close="closeFolderMove"
    />
    <!-- A folder the Folders tree asked to remove, holding files (#2207). -->
    <!-- Keyed by the request, so a request that replaces an open one (a batch's
         next folder racing a single Remove) redraws on its own facts. -->
    <ShelfRemoveFolderDialog
      :key="folderRemoveAt"
      :open="folderRemoveOpen"
      :source="folderRemoveSource"
      :plan="folderRemovePlan"
      :remaining="folderRemoveRemaining"
      @removed="folderRemoved = true"
      @move-models="moveModelsOutFirst"
      @close="closeFolderRemove"
    />
    <ModelDropTip />

    <!-- The shipped host-path picker again, in its file mode. A server-side
         picker rather than an `<input type=file>`: the file is on the machine
         running PixlStash and the server copies it there, so an upload would
         push a gigabyte through the browser to land it beside where it started.
         (The icon verb uses a real file input because an icon is small and its
         bytes genuinely have to travel.) -->
    <FolderBrowser
      :open="addFileOpen"
      pick-model-file
      @select="onFilePicked"
      @close="closeAddFile"
    />

    <!-- The same picker in its directory mode, registering what it returns as
         the ai-toolkit output root rather than as a folder to catalogue. -->
    <FolderBrowser
      :open="addSourceOpen"
      :registered-paths="foldersStore.registeredPaths"
      already-registered-label="Already a model folder"
      @select="onSourcePicked"
      @close="closeAddSource"
    />

    <!-- Corner-anchored inside the panel that is busy, never a centred modal:
         a move concerns THIS list, and a card in the middle of the window
         claims the whole product for it. This wrapper is the corner and
         `.shelf` is what it is measured from, because `ProgressOverlay` is
         multi-root and silently drops a class handed to it. -->
    <div class="shelf-progress">
      <ProgressOverlay
        :visible="moves.running || Boolean(moves.failure)"
        :status="moveProgressStatus"
        :message="moves.failure || moveProgressMessage"
        :percent="moves.failure ? 100 : moves.percent"
        :count="moves.failure || moves.kind === 'folder' ? null : moves.done"
        :total="moves.failure || moves.kind === 'folder' ? null : moves.total"
        :abort-label="moveProgressAction"
        @abort="moves.failure ? dismissMoveFailure() : moves.cancel()"
      />
    </div>
  </div>
</template>

<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  onUnmounted,
  provide,
  ref,
  shallowRef,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import ShelfShowPanel from "../panels/ShelfShowPanel.vue";
import NameFilterField from "../widgets/NameFilterField.vue";
import ShelfSortPanel from "../panels/ShelfSortPanel.vue";
import ShelfSelectionBar from "../panels/ShelfSelectionBar.vue";
import WorkflowSetRenameDialog from "../panels/WorkflowSetRenameDialog.vue";
import { handMadeName, headName } from "../../utils/workflowSets";
import WorkflowSetSelectionBar from "../panels/WorkflowSetSelectionBar.vue";
import MissingSetSelectionBar from "../panels/MissingSetSelectionBar.vue";
import ReplaceMissingDialog from "../io/ReplaceMissingDialog.vue";
import BaseModelInput from "../widgets/BaseModelInput.vue";
import KindMenu from "../widgets/KindMenu.vue";
import MemoBlock from "../widgets/MemoBlock.vue";
import ShelfEditDialog from "../panels/ShelfEditDialog.vue";
import MergeCopiesDialog from "../panels/MergeCopiesDialog.vue";
import ShelfMoveDialog from "../panels/ShelfMoveDialog.vue";
import FolderMoveDialog from "../panels/FolderMoveDialog.vue";
import ShelfRemoveFolderDialog from "../panels/ShelfRemoveFolderDialog.vue";
import ModelDropTip from "../panels/ModelDropTip.vue";
import ModelWorksWithDialog from "../panels/ModelWorksWithDialog.vue";
import ModelSetGrid from "./ModelSetGrid.vue";
import InspectorEdgeTab from "../widgets/InspectorEdgeTab.vue";
import ActionReceipt from "../widgets/ActionReceipt.vue";
import { FLOATING_BOTTOM_GAP_PX } from "../../utils/floatingBottom";
import TbGlobalActions from "../panels/TbGlobalActions.vue";
import TrainingRuns from "./TrainingRuns.vue";
import ShelfRunLoss from "./ShelfRunLoss.vue";
import { SOURCE_KIND } from "../../api/modelFolders";
import FolderBrowser from "../editors/FolderBrowser.vue";
import {
  FAVOURITE_KEY,
  useFavouritesStore,
} from "../../stores/useFavouritesStore";
import VerdictIcon from "../widgets/VerdictIcon.vue";
import { modelVerdictMark } from "../../utils/setVerdicts";
import PicturePicker from "../widgets/PicturePicker.vue";
import AppButton from "../widgets/AppButton.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import ModelMark from "../widgets/ModelMark.vue";
import ProgressOverlay from "../widgets/ProgressOverlay.vue";
import StackEdgeTicks from "../widgets/StackEdgeTicks.vue";
import { useConfirm } from "../../composables/useConfirm";
import { addModelFile } from "../../api/modelFiles";
import { getPictureThumbnailBlob } from "../../api/pictures";
import {
  fetchModelCompanions,
  openModelLocation,
} from "../../api/modelShelf";
import {
  createStack,
  removeStackMember,
  setStackCover,
  unstackStack,
} from "../../api/modelStacks";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import {
  DEFAULT_COLUMN_WIDTHS,
  GRID_GROUP_BY,
  MAX_COLUMN_WIDTH,
  MIN_COLUMN_WIDTHS,
  useModelShelfStore,
} from "../../stores/useModelShelfStore";
import {
  basename,
  useModelFoldersStore,
} from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { errorDetail } from "../../utils/apiError";
import { formatUserDate, formatUserDay } from "../../utils/utils";
import { isTypingTarget } from "../../utils/dom.js";
import { driveFullness } from "../../utils/libraryChrome";
import {
  companionsSentences,
  inFolderScopeOf,
  isMisplaced,
  modelFileTitle,
  dateColumnKey,
  defaultSortDirection,
  deletableModels,
  fileKindLabel,
  shelfTypeLabel,
  undeletableNotice,
  baseModelCell,
  lookupEligible,
  trashName,
  formatModelSize,
  modelDate,
  modelName,
  GROUP_BY_LABELS,
  modelVersion,
  movableCopies,
  moveMechanism,
  quantBadge,
  triggerChip,
  releaseReceipt,
  SORT_LABELS,
  shelfTypeOf,
  stackReceipt,
  trainingStep,
  unstackReceipt,
  sortDirectionLabel,
  isInFoldersTree,
  assignmentRing,
} from "../../utils/modelShelf";

// Settings lives in App.vue's sidebar dialog, so the toolbar's Settings button
// asks for it the way the duplicates queue does. The stats toggle needs no
// event: TbGlobalActions flips the sidebar store itself.
const emit = defineEmits(["open-settings"]);

const store = useModelShelfStore();
const entityLists = useEntityListsStore();
const foldersStore = useModelFoldersStore();
const favourites = useFavouritesStore();
const moves = useModelMovesStore();
// Both read by the window-level Escape, which has to know what else on screen
// owns the key before it clears anything. See `onShelfEscape`.
const reviewSessionsStore = useReviewSessionsStore();
const sidebarStore = useSidebarStore();

/** Every model the Models rail can offer: the count in its closed handle's name. */
const railCount = computed(() =>
  isSetGrid.value ? store.placeCounts.sets : store.visibleRows.length,
);

// ── The closed Models rail (visual-language "Closed inspector") ───────────
//
// Everything selected on the shelf, as `{key, noun, name}`: models (rows,
// cards and tray members), hand-made sets, and cards named after a missing
// file.
const railPicked = computed(() => [
  ...store.selectedRows.map((row) => ({
    key: `model:${row.id}`,
    noun: "models",
    name: modelName(row).text || row.filename || "",
  })),
  ...store.selectedSets.map((set) => ({
    key: `set:${set.id}`,
    noun: "sets",
    name: handMadeName(set),
  })),
  ...store.selectedMissingGroups.map((group) => ({
    key: group.key,
    noun: "missing models",
    name: headName(group.head),
  })),
]);

/** What the closed handle names: one name, `N models`, or "" for none. */
const railLabel = computed(() => {
  const picked = railPicked.value;
  if (picked.length < 2) return picked[0]?.name ?? "";
  const nouns = new Set(picked.map((item) => item.noun));
  return `${picked.length.toLocaleString()} ${
    nouns.size === 1 ? picked[0].noun : "selected"
  }`;
});

// The same act as the toolbar toggle, so it persists. The handle unmounts as
// the rail opens, so focus is handed into the rail (Clear while anything is
// selected, else its title) rather than dropping to `body`.
function openRailFromTab() {
  sidebarStore.setModelsRailOpen(true);
  nextTick(() =>
    (
      document.querySelector(".mrail .mrail-clear") ??
      document.querySelector(".mrail .mrail-title")
    )?.focus(),
  );
}

// A NEW selection nudges the rail: the handle bounces and the toolbar's rail
// glyph flashes, both only while the rail is closed. Keyed on the selection's
// identity, so re-clicking what is selected is not news, and clearing it is
// not either.
watch(
  () =>
    railPicked.value
      .map((item) => item.key)
      .sort()
      .join("\n"),
  (identity) => {
    if (identity) sidebarStore.inspectorNudge += 1;
  },
);
// So does a card's tray opening, with or without a selection and however it
// opened (the caret, or the store putting one back). Closing it is not news.
watch(
  () => store.openSetKey,
  (key) => {
    if (key) sidebarStore.inspectorNudge += 1;
  },
);
// The date column is stamped in the reader's own format, through the same
// `formatUserDate(iso, dateFormat)` pattern every other timestamp in the app
// uses.
const userPrefs = useUserPrefsStore();
const rootEl = ref(null);
const showMenuOpen = ref(false);
const nameFocusRequest = ref(0);
const nameFieldRef = ref(null);
// The app's search key (F) finds by name here: the toolbar's field when the bar
// has room for it, else the one at the top of Show.
watch(
  () => useSearchStore().searchFocusToken,
  () => {
    if (!isShelfTab.value) return;
    if (nameFieldRef.value?.isShown()) {
      nameFieldRef.value.focus();
      return;
    }
    showMenuOpen.value = true;
    nameFocusRequest.value += 1;
  },
);
const sortMenuOpen = ref(false);
/** The model whose companions the Works with dialog is answering for, or null. */
const worksWithModel = ref(null);
// Held raw: a DOM node, not reactive state. `VDialog`
// restores focus to its own `activatorEl` and this dialog has none - it opens
// from a prop - so without this, closing it dropped focus to <body> (WCAG
// 2.4.3). Both openers are transient: the context-menu item unmounts with the
// menu, and the grid's file button can go with a re-render.
const worksWithInvoker = shallowRef(null);
// The toolbar buttons behind the dialogs its left half opens, so focus has a
// place to come back to however the reader got there. A menu item cannot be
// that place - it unmounts with the menu.
/**
 * Hand focus to the first candidate that will actually take it, in order.
 * The one candidate is the shelf root, which every door returns to.
 */
function restoreFocus(...candidates) {
  for (const el of candidates) {
    if (!el?.isConnected) continue;
    el.focus();
    if (document.activeElement === el) return;
  }
}
const selBarRef = ref(null);
/** Read once, dismissed for this visit; a refetch says it again. */
const offlineDismissed = ref(false);
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

/**
 * Confirm, then ask Civitai for the selection's empty base models.
 *
 * Not `danger`: nothing is destroyed. The prompt is the consent for the one
 * shelf verb that leaves the machine, so it names the host and what is sent.
 */
async function confirmLookupBaseModel() {
  const ids = store.selectedModels.filter(lookupEligible).map((row) => row.id);
  if (!ids.length) return;
  const ok = await confirm({
    title:
      ids.length === 1
        ? "Look up 1 base model on Civitai?"
        : `Look up ${ids.length} base models on Civitai?`,
    message:
      "PixlStash sends only each file's SHA-256 hash to civitai.com. Files are not uploaded, and base models you set yourself are never changed.",
    confirmLabel: "Look up on Civitai",
  });
  if (ok) await store.lookupBaseModels(ids);
}

// Set while the delete prompt is being prepared or shown. The companions read
// sits before the prompt opens, and a second Delete press in that window would
// otherwise open a second prompt over the first and strand its promise.
/**
 * The row whose copies are being merged, or null.
 *
 * Taken from `selectedRows`, which carries the model's WHOLE `locations` array.
 * The folder-grouped draw narrows it to the one copy that folder holds, and a
 * dialog offering one of two choices would be the one screen that cannot do its
 * job (#1439).
 */
const mergeRow = ref(null);

/**
 * Open the keep-one-copy dialog for the single selected duplicate.
 *
 * One row, because the keeper is a per-model choice and a batch would be a table
 * of radio groups. The backend already takes a list, so the batch is a later
 * screen rather than a later route.
 */
function openMergeCopies() {
  const rows = store.selectedRows;
  if (rows.length !== 1) return;
  mergeRow.value = rows[0];
}

let deletePromptOpen = false;

/**
 * The third confirmation, and the only one standing in front of real bytes.
 *
 * It names the operation rather than the gesture, because the gesture is a
 * modifier the reader may not have meant to be holding: a trash says where the
 * files are going and a permanent delete says nothing gets them back.
 *
 * **The count is the exact list of models the call will send.** Two things make
 * it easy to get wrong in the destructive direction, and both are handled here
 * rather than left to the server: a selection of forty holding two
 * HuggingFace-cache rows deletes thirty-eight, and a selected STACK is one row
 * standing for six files. So the subset is taken first and then expanded to
 * member ids, and those ids are what is posted.
 *
 * `permanent` arrives from the event that triggered this and is passed straight
 * through: nothing here re-reads the keyboard, so the prompt the reader agreed
 * to is the call that runs.
 *
 * @param {boolean} permanent - Shift was down: unlink rather than trash.
 */
async function confirmDelete(permanent) {
  if (deletePromptOpen) return;
  deletePromptOpen = true;
  try {
    await askAndDelete(permanent);
  } finally {
    deletePromptOpen = false;
  }
}

async function askAndDelete(permanent) {
  const rows = deletableModels(store.selectedRows, foldersById.value);
  // MODELS, not rows: a stack is one row and six checkpoints, and it is deleted
  // whole exactly as it is moved whole. Counting rows would have offered
  // "Move this model to the Trash?" over tens of gigabytes - and these are the
  // ids the call sends, so what the prompt counts and what goes are one list.
  const ids = rows.flatMap((row) => row.memberIds ?? [row.id]);
  if (!ids.length) {
    // The keyboard can reach this with nothing deletable selected - the pill's
    // button is disabled there, but `Delete` has no disabled state. Silence
    // would read as a broken key.
    if (store.selectedRows.length) {
      useNoticeStore().push({
        level: "info",
        text: undeletableNotice(store.selectedRows, foldersById.value),
      });
    }
    return;
  }
  const many = ids.length !== 1;
  const subject = many ? `${ids.length} models` : "this model";
  const trash = trashName();
  // Asked before the prompt opens, so the reader decides with it in front of
  // them (#1314). A failed read is said in the prompt rather than skipped:
  // leaving the section out would read as "nothing else is affected".
  let companions = null;
  try {
    companions = await fetchModelCompanions(ids);
  } catch (err) {
    console.warn("[ModelShelf] could not read what the delete leaves behind", {
      ids,
      err,
    });
  }
  const leftBehind = companionsSentences(companions, ids.length);
  // The owner's own sets keep a deleted member by its hash, marked "Not on
  // shelf" (#1520), so the prompt says how far the delete reaches and names the
  // safer verb rather than leaving the reader to find a greyed tile later.
  // True on BOTH paths: set membership lives in its own table keyed by hash,
  // which neither the trash nor a permanent delete touches - "kept by its file
  // hash" is what squares it with a permanent delete's "everything recorded".
  const holding = store.handMadeSetsHolding(ids);
  const inSets = holding.length
    ? ` ${many ? "They stay" : "It stays"} in ${
        holding.length === 1
          ? `your set "${handMadeName(holding[0])}"`
          : `${holding.length} of your workflow sets`
      }, kept by ${many ? "their" : "its"} file hash and marked Not on shelf. To take ${many ? "them" : "it"} off a set only, use Remove from set.`
    : "";
  const ok = await confirm({
    title: permanent
      ? `Permanently delete ${many ? `${ids.length} models?` : "this model?"}`
      : `Move ${many ? `${ids.length} models` : "this model"} to the ${trash}?`,
    message:
      (permanent
        ? `The files for ${subject} are deleted permanently from this machine, along with everything recorded about them.`
        : `The files for ${subject} go to your ${trash}, where you can put them back. The shelf stops listing them.`) +
      (leftBehind ? ` ${leftBehind}` : "") +
      inSets,
    warning: permanent
      ? "There is no undo for this."
      : `A very large file may be too big for the ${trash} and be deleted outright.`,
    confirmLabel: permanent ? "Delete permanently" : `Move to ${trash}`,
    danger: true,
  });
  if (ok) await store.deleteSelected({ permanent, ids });
}

/**
 * Show the selected row's folder in the file manager of the SERVER's desktop.
 *
 * No confirmation, because it changes nothing - the one verb on the shelf that
 * only looks. The id posted is the ROW's, which for a collapsed stack is the
 * cover's: one press opens one window, and the cover is the file the reader
 * right-clicked. A stack the shelf built shares a folder (its own gate refuses
 * to group across folders), but a stack is not required to, so this is the
 * cover's folder rather than "the run's" - those are the same directory in
 * every case the shelf can create and not by a rule the server enforces.
 *
 * **Three different failures, three different sentences.** Nothing visible
 * happens on this screen when it works, so a wrong reason is as bad as no
 * reason: 403 is a shelf opened from another machine (the route is
 * loopback-only), 409 is a row whose file has gone since the list was drawn -
 * which the disabled state cannot catch, because it knows the recorded state
 * and not whether the file is still there - and anything else is a server with
 * no desktop to open anything on.
 */
async function openLocation() {
  const row = store.selectedRows[0];
  if (!row) return;
  try {
    await openModelLocation(row.id);
  } catch (err) {
    const status = err?.response?.status;
    useNoticeStore().push({
      level: "warning",
      text:
        status === 403
          ? "A file manager opens on the machine running PixlStash, so this only works when you are sitting at it."
          : status === 409
            ? "That file is not where the shelf last saw it. The shelf catches up in a moment."
            : "Couldn't open that folder - the machine running PixlStash has no desktop file manager.",
      key: "shelf-open-location",
    });
    console.warn(`Failed to open the location of model ${row.id}`, err);
  }
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
// pointer away from starting - and there is no undo behind a move to make that
// recoverable.

const moveOpen = ref(false);
const moveItems = ref([]);
const moveBytes = ref(0);
/** `{folderId, path}` a drop or a paste chose, or null from Move to…. */
const moveDestination = ref(null);
/** True when the open dialog is pasting what Ctrl X cut. */
const moveFromCut = ref(false);
const moveInvoker = shallowRef(null);

/** `model_folder.id` to the folder row, for `movableCopies`' folder rules. */
const foldersById = computed(
  () =>
    new Map(foldersStore.folders.map((folder) => [Number(folder.id), folder])),
);

const moveProgressMessage = computed(() => {
  // A folder is one job of one item: say which folder, not "1 of 1 files".
  if (moves.kind === "folder") {
    return moves.cancelRequested
      ? `Stopping ${moves.folderName}…`
      : `Moving ${moves.folderName}…`;
  }
  return moves.cancelRequested
    ? "Stopping after the file in flight…"
    : "Moving model files…";
});

/**
 * What the corner card is saying: progress, or the failure it ended in.
 *
 * A failed run keeps the card rather than handing its news to a notice that
 * clears itself, so the error is read in the place the progress was (#900).
 * The bar fills to 100% under it, which is what makes the failure take the
 * bar's whole width instead of freezing part-way like an interrupted run.
 */
const moveProgressStatus = computed(() => {
  if (moves.failure) return "failed";
  return moves.running ? "running" : "idle";
});

/** The card's one button: stop a run, or put a read failure away. */
const moveProgressAction = computed(() => {
  if (moves.failure) return "Dismiss";
  return moves.cancelRequested ? null : "Stop";
});

/**
 * Put the failure away and catch the focus it was holding.
 *
 * Dismiss destroys the element the keyboard is standing on, and focus would
 * fall to `<body>` - the next Tab restarts at the top of the document, which is
 * how a user who just cleared a card loses their place in a 1,800-row list. The
 * shelf root is the same landing the move dialog returns to.
 */
async function dismissMoveFailure() {
  moves.dismissFailure();
  await nextTick();
  rootEl.value?.focus();
}

/**
 * Open the move dialog for a set of rows.
 *
 * @param {Array<Object>} rows - shelf rows.
 */
function openMove(rows) {
  // In a folder view only the copy in the open folder: the file on screen.
  showMove(
    movableCopies(
      rows,
      foldersById.value,
      inFolder.value ? store.folderScope : null,
    ),
  );
}

/**
 * Open the dialog for copies already worked out.
 *
 * @param {{items: Array, totalBytes: number}} copies
 * @param {{folderId: number, path: string}|null} [destination] - pre-chosen.
 * @param {boolean} [fromCut] - pasting what Ctrl X cut.
 */
function showMove(copies, destination = null, fromCut = false) {
  if (!copies?.items?.length) return;
  moveInvoker.value =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  moveItems.value = copies.items;
  moveBytes.value = copies.totalBytes;
  moveDestination.value = destination;
  moveFromCut.value = fromCut;
  moveOpen.value = true;
}

// A folder in the sidebar's tree took a drop, or a paste named one: the
// destination is already chosen, so the only question left is whether to ask.
// A rename on one drive copies nothing and starts at once, as a folder drop
// does and as a file manager does; a copy across drives (or a drive that could
// not be measured) states its cost in the dialog first.
watch(
  () => store.moveRequest,
  async (request) => {
    if (!request) return;
    const { copies, destination } = request;
    const fromCut = Boolean(request.fromCut);
    const cut = store.cutCopies;
    const { how } = moveMechanism(
      copies,
      destination.folderId,
      foldersStore.deviceByFolderId,
    );
    if (how !== "rename" || moves.busy) {
      showMove(copies, destination, fromCut);
      return;
    }
    const started = await moves.start(
      destination.folderId,
      copies.items,
      destination.path,
    );
    // Only the cut this pasted: a Ctrl X pressed during the POST is a new one.
    if (started && fromCut && store.cutCopies === cut) store.clearCut();
  },
);

/** A paste's cut is spent once its move starts; the rows stop drawing dim. */
function onMoveStarted() {
  if (moveFromCut.value) store.clearCut();
}

/** The folder move dialog: the tree or its menu asked for it (#2206). */
const folderMoveOpen = ref(false);
const folderMoveSource = ref({ folderId: 0, path: "", name: "" });
const folderMoveDestination = ref(null);
const folderMovePlan = ref(null);
const folderMoveInvoker = shallowRef(null);

watch(
  () => store.folderMoveRequest,
  (request) => {
    if (!request) return;
    folderMoveInvoker.value =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    folderMoveSource.value = request.source;
    folderMoveDestination.value = request.destination;
    folderMovePlan.value = request.plan;
    folderMoveOpen.value = true;
  },
);

/** Close the folder dialog and give the focus back to what opened it. */
async function closeFolderMove() {
  const returnTo = folderMoveInvoker.value;
  folderMoveOpen.value = false;
  folderMoveInvoker.value = null;
  folderMoveDestination.value = null;
  folderMovePlan.value = null;
  await nextTick();
  (returnTo?.isConnected ? returnTo : rootEl.value)?.focus();
}

/** The folder removal dialog: the tree asked, with the dry run read (#2207). */
const folderRemoveOpen = ref(false);
const folderRemoveSource = ref({ folderId: 0, path: "", name: "" });
const folderRemovePlan = shallowRef(null);
const folderRemoveInvoker = shallowRef(null);
/** The open request's `at`, which keys the dialog. */
const folderRemoveAt = ref(0);
/** Picked folders still waiting behind the one shown. */
const folderRemoveRemaining = ref(0);
/** Whether the open dialog removed its folder, told to the tree on close. */
const folderRemoved = ref(false);

watch(
  () => store.folderRemoveRequest,
  (request) => {
    if (!request) return;
    folderRemoveInvoker.value =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    folderRemoveSource.value = request.source;
    folderRemovePlan.value = request.plan;
    folderRemoveRemaining.value = request.remaining ?? 0;
    folderRemoveAt.value = request.at;
    folderRemoved.value = false;
    folderRemoveOpen.value = true;
  },
);

/** Close the removal dialog and give the focus back to what opened it. */
async function closeFolderRemove() {
  const returnTo = folderRemoveInvoker.value;
  const closingAt = folderRemoveAt.value;
  const removed = folderRemoved.value;
  folderRemoveOpen.value = false;
  folderRemoveInvoker.value = null;
  folderRemovePlan.value = null;
  await nextTick();
  (returnTo?.isConnected ? returnTo : rootEl.value)?.focus();
  // Last, so a next picked folder's dialog opens after this one has gone. A
  // request that replaced this one meanwhile was answered when it arrived,
  // and its own dialog settles it.
  if (store.folderRemoveRequest?.at === closingAt) {
    store.settleFolderRemove(removed);
  }
}

// A batch waiting on a dialog that is going away stops rather than hanging.
onUnmounted(() => store.settleFolderRemove(false));

/** *Move the models out first…*: the move dialog, on the folder's copies. */
async function moveModelsOutFirst(copies) {
  await closeFolderRemove();
  showMove(copies);
}

// ── Drag and cut (#2204) ─────────────────────────────────────────────────

// A pointer drag, not the browser's. HTML5 drag-and-drop hands the gesture and
// its image to the window system, which on the desktop shell (X11) lifted the
// row late and lagged the whole drag, as it once did the Models rail's. Here
// the drop tip follows the pointer as the drag's preview, `elementFromPoint`
// finds the folder under it (`data-model-copies-drop`), and that folder takes
// `model-copies-over`, `-leave` and `-drop` events, accepting a drop with
// `preventDefault` as a `dragover` would (`useModelCopyDrop`).

/** How far the pointer moves before a press becomes a drag. */
const DRAG_START_PX = 4;
/** How close to a scrolling list's edge a drag scrolls it, and how fast. */
const EDGE_PX = 48;
const EDGE_SPEED_PX = 18;

/** `{row, x, y}` from the press, until it becomes a drag or is let go. */
let press = null;
let dragging = false;
let point = { x: 0, y: 0 };
let edgeFrame = 0;
/** The folder row under the pointer, and whether it takes the drop. */
let overEl = null;
let overOk = false;

function onRowPointerDown(event, row) {
  // A mouse or pen press with the main button. Touch scrolls the list, and a
  // press in the rename field selects its text.
  if (event.button !== 0 || event.pointerType === "touch") return;
  if (event.target.closest?.("input, textarea, button")) return;
  endDrag();
  press = { row, x: event.clientX, y: event.clientY };
  window.addEventListener("pointermove", onDragMove);
  window.addEventListener("pointerup", onDragRelease);
  window.addEventListener("pointercancel", endDrag);
  window.addEventListener("keydown", onDragKeydown, true);
}

function onDragMove(event) {
  if (!press) return;
  // The release happened somewhere this window never heard it (another
  // window, a lost grab): the drag is over, and nothing drops.
  if (event.buttons === 0) {
    endDrag();
    return;
  }
  point = { x: event.clientX, y: event.clientY };
  if (!dragging) {
    const travel = Math.hypot(
      event.clientX - press.x,
      event.clientY - press.y,
    );
    if (travel < DRAG_START_PX) return;
    if (!startDrag(press.row)) {
      endDrag();
      return;
    }
  }
  trackDrag(event.clientX, event.clientY);
}

/**
 * A row picks up the selection's movable copies. An unselected row is made the
 * selection first, alone, as a file manager does: what is dragged is what is
 * under the pointer. A drop on the same drive moves at once, as a file manager
 * does; across drives it asks first.
 *
 * @returns {boolean} whether anything was picked up.
 */
function startDrag(row) {
  if (!store.isSelected(row.id)) {
    // A row that cannot move refuses here, before it takes the selection: a
    // stray drag on an offline row must not drop forty selected ones.
    const scope = inFolder.value ? store.folderScope : null;
    if (!movableCopies([row], foldersById.value, scope).items.length) {
      noticeUnmovable();
      return false;
    }
    store.selectFromClick(row.id, {}, orderedRowIds.value);
  }
  const copies = store.movableSelection(foldersById.value);
  if (!copies.items.length) {
    noticeUnmovable();
    return false;
  }
  store.dragCopies = copies;
  dragging = true;
  // A wheel or the edge scroll moves a folder under a still pointer.
  window.addEventListener("scroll", onDragScroll, true);
  edgeFrame = requestAnimationFrame(edgeScroll);
  return true;
}

function onDragScroll() {
  trackDrag(point.x, point.y);
}

/**
 * Scroll whatever list is under the pointer when the pointer nears its top or
 * bottom edge, as the browser's own drag did: a folder further down the tree
 * is otherwise out of reach.
 */
function edgeScroll() {
  if (!dragging) return;
  let list = document.elementFromPoint?.(point.x, point.y) ?? null;
  while (
    list &&
    !(
      list.scrollHeight > list.clientHeight &&
      /auto|scroll/.test(getComputedStyle(list).overflowY)
    )
  ) {
    list = list.parentElement;
  }
  if (list) {
    const box = list.getBoundingClientRect();
    const top = point.y - box.top;
    const bottom = box.bottom - point.y;
    if (top < EDGE_PX) list.scrollTop -= EDGE_SPEED_PX * (1 - top / EDGE_PX);
    else if (bottom < EDGE_PX) {
      list.scrollTop += EDGE_SPEED_PX * (1 - bottom / EDGE_PX);
    }
  }
  edgeFrame = requestAnimationFrame(edgeScroll);
}

/** Light the folder under the pointer, and move the tip along with it. */
function trackDrag(x, y) {
  // The tip is `pointer-events: none`, so this is what is really underneath.
  const hit =
    document
      .elementFromPoint?.(x, y)
      ?.closest?.("[data-model-copies-drop]") ?? null;
  if (overEl && overEl !== hit) {
    overEl.dispatchEvent(new CustomEvent("model-copies-leave"));
  }
  overEl = hit;
  // A folder that takes the drop writes the tip itself: where they go.
  overOk = hit
    ? !hit.dispatchEvent(
        new CustomEvent("model-copies-over", {
          cancelable: true,
          detail: { x, y },
        }),
      )
    : false;
  // No drag cursor: a class on <html> restyles every element on the page,
  // which cost more than the drag it decorated. The tip and the folder's ring
  // or hatch say where the drop goes.
  if (!overOk) store.dropTip = { x, y, text: carriedWords(store.dragCopies) };
}

/**
 * What the tip says away from a folder: the file, or how many, and how many
 * selected models stay behind (see `movableCopies`). Without the second half a
 * three-model drag that carries one file reads as a lost selection. Models,
 * not files: a model with two movable copies carries two files.
 */
function carriedWords(copies) {
  const items = copies?.items ?? [];
  const carried =
    items.length === 1 ? basename(items[0].relpath) : `${items.length} files`;
  const stays = copies?.stays ?? 0;
  if (!stays) return carried;
  return `${carried} · ${stays} ${stays === 1 ? "model" : "models"} can't move`;
}

function onDragRelease() {
  const target = dragging && overOk ? overEl : null;
  const wasDragging = dragging;
  // Handed over before the drag is cleared: the folder reads `dragCopies`.
  target?.dispatchEvent(
    new CustomEvent("model-copies-drop", { cancelable: true }),
  );
  endDrag();
  if (wasDragging) swallowReleaseClick();
}

/**
 * The release would otherwise land as a click where the drag began, and
 * narrow the selection to that row.
 */
function swallowReleaseClick() {
  window.addEventListener("click", swallowClick, true);
  setTimeout(() => window.removeEventListener("click", swallowClick, true));
}

function swallowClick(event) {
  event.stopPropagation();
  event.preventDefault();
  window.removeEventListener("click", swallowClick, true);
}

function onDragKeydown(event) {
  if (event.key !== "Escape" || !dragging) return;
  // Escape puts the files back; it must not also clear the selection or a cut.
  event.stopPropagation();
  event.preventDefault();
  endDrag();
  // The button is still down, and its release is still a click to eat.
  window.addEventListener("pointerup", swallowReleaseClick, { once: true });
}

function endDrag() {
  window.removeEventListener("pointermove", onDragMove);
  window.removeEventListener("pointerup", onDragRelease);
  window.removeEventListener("pointercancel", endDrag);
  window.removeEventListener("keydown", onDragKeydown, true);
  window.removeEventListener("scroll", onDragScroll, true);
  cancelAnimationFrame(edgeFrame);
  overEl?.dispatchEvent(new CustomEvent("model-copies-leave"));
  press = null;
  overEl = null;
  overOk = false;
  if (dragging) {
    dragging = false;
    store.dragCopies = null;
    store.dropTip = null;
  }
}

onBeforeUnmount(endDrag);

/** Why a drag or a cut took nothing: otherwise the gesture just fails. */
function noticeUnmovable() {
  useNoticeStore().push({
    level: "info",
    text: "None of these files can be moved: they are missing, offline or in a locked folder.",
    // A few stray drags count up on one card rather than stacking.
    key: "shelf-unmovable",
  });
}

/** Ctrl X and the menu's Cut: hold the selection's movable copies. */
function cutSelected() {
  if (!store.cutSelection(foldersById.value)) noticeUnmovable();
}

/** Ctrl V and the menu's Paste: the cut copies into the open folder. */
function pasteCut() {
  store.pasteCut(store.folderScope);
}

async function closeMove() {
  const returnTo = moveInvoker.value;
  moveOpen.value = false;
  moveInvoker.value = null;
  moveDestination.value = null;
  moveFromCut.value = false;
  await nextTick();
  (returnTo?.isConnected ? returnTo : rootEl.value)?.focus();
}


/**
 * Does the shelf own this press, or does something in front of it?
 *
 * The guard all three window-level keys ask first - Escape, which clears the
 * selection from anywhere including outside the shelf, Delete, which is in
 * front of a file deletion and must never fire against a surface that is merely
 * drawn over the rows, and Ctrl+A, which selects the whole list.
 *
 * A declined key is handed back INTACT - no `preventDefault` - which for Ctrl+A
 * means the browser's own select-all runs behind a dialog or a menu. That is
 * the intent: those surfaces teleport out of `.shelf` and their text IS
 * selectable, so select-all there is a real gesture the shelf has no business
 * taking. It is the same bargain the `Esc` keycap in the pill's menu already
 * strikes - with the menu open that press closes the menu instead.
 *
 * On the WINDOW rather than on the shelf root, because a keydown only reaches
 * an element that contains the focus: bound to the root it worked from a row
 * and from the toolbar, and did nothing at all once the sidebar, the app bar or
 * anything else outside this view had been clicked. The selection is still on
 * screen at that point, so "Escape clears the selection" reads as broken. The
 * shelf is `v-else-if`'d away with the view, so the listener is only live while
 * there is a shelf to clear.
 *
 * Everything that can own the key ahead of the shelf gets it handed back
 * rather than taken from it, all one rule - Escape means "undo the thing in
 * front of you", and clearing the selection underneath would be a second,
 * unasked-for effect. The same list is what keeps Delete from reaching the
 * shelf while a dialog, a menu or the review overlay is up. What that means in
 * practice, and why each one is checked the way it is:
 *   * one of the shelf's OWN dialogs is open. By ref, not by target: those are
 *     `AppDialog`s inside this subtree and a press with nothing focused targets
 *     `<body>`, which no ancestor test can see. `docs/frontend_architecture.md`
 *     §"the create-person dialog" records that same body-target hole.
 *   * any Vuetify overlay that is not a tooltip is up - a menu, a dialog, a
 *     select. On the OVERLAY rather than on the target, because `VMenu` only
 *     pulls focus into its content on a later `focusin`: a menu opened with the
 *     mouse leaves focus on its activator, so the shelf's own Sort, Show and
 *     verb menus would close AND drop the selection. Tooltips are exempt or a
 *     hovered button elsewhere would swallow the key.
 *   * a full-screen surface is over the shelf. The review overlay renders
 *     OUTSIDE `App.vue`'s view switch, so the shelf is still mounted under it
 *     and would clear a selection nobody can see.
 *   * the auto-hide sidebar is showing. Escape dismisses it (WCAG 1.4.13) and
 *     `useGlobalKeydown` deliberately does not stop the event, so without this
 *     one press would hide the sidebar and wipe the selection behind it.
 *   * something is being typed in - the search field's own Escape clears the
 *     search.
 *
 * Bubble phase, not capture: every owner above is meant to resolve the key
 * FIRST, and a capture-phase listener would take it from them.
 *
 * **It does not test the selection.** Escape and Delete need one and ask for
 * it themselves; Ctrl+A is the key pressed precisely BECAUSE nothing is selected
 * yet, so a shared "something is selected" line here would be the one guard
 * that made the new key impossible.
 */
function shelfOwnsTheKey(event) {
  // The shelf component stays mounted while the runs panel is showing, and this
  // is a WINDOW listener, so without this line `Delete` would open the delete
  // confirmation for rows the reader cannot see and `Escape` would silently
  // clear a selection they did not know they still had.
  if (!isShelfTab.value) return false;
  // The Models rail sits beside the shelf with a list and keys of its own:
  // it asks for Delete and F2 itself (`store.requestVerb`), and its Escape
  // and Ctrl+A are about its own rows.
  if (event?.target?.closest?.(".mrail")) return false;
  // The set grid is NOT excluded, and the reason it once was has gone with the
  // card standing for a whole set: every selectable thing on that screen is one
  // model, the bar floats over it, and `selectVisible` answers the grid's own
  // idea of "shown". So Ctrl+A builds a selection the reader can see, and Delete
  // opens the confirmation for rows they can point at.
  // The Folders tree (#2206) is in the sidebar, and its F2, Ctrl+Shift+N and
  // Delete belong to the folder under the focus: Delete there is Remove
  // folder (#2207). Here it would be a file deletion, so this must step aside
  // even with no selection to delete.
  if (isInFoldersTree(event)) return false;
  if (
    moveOpen.value ||
    folderMoveOpen.value ||
    folderRemoveOpen.value ||
    addSourceOpen.value ||
    addFileOpen.value ||
    thumbnailPickerOpen.value ||
    editVerb.value
  ) {
    return false;
  }
  if (
    reviewSessionsStore.overlayOpen ||
    document.querySelector(".v-overlay--active:not(.v-tooltip), .lightbox")
  ) {
    return false;
  }
  if (sidebarStore.sidebarOverlay && sidebarStore.sidebarVisible) return false;
  if (event.target?.closest?.(".ate, [role='dialog']")) return false;
  if (isTypingTarget(event.target)) return false;
  return true;
}

/**
 * Escape clears, Delete deletes, Ctrl+A takes the lot.
 *
 * One handler and one set of guards, because the question all three keys ask
 * first is the same one: does the shelf own this press, or does something in
 * front of it? Splitting them would be copies of the list above, and the copy
 * that drifted would be the one in front of a file deletion.
 *
 * **Delete is the file-manager gesture and is spelled the way Explorer spells
 * it**: on its own it moves to the trash, with Shift it deletes permanently.
 * `event.shiftKey` is read off the press itself and handed to the same
 * confirmation the pill uses, so the key opens a prompt and never a deletion -
 * a stray Del with forty rows selected costs one Escape.
 *
 * **Ctrl+A is claimed rather than left to the browser**, the same chord the
 * photo grid (`useGridKeyboardNav`) and the duplicate queue
 * (`useDedupQueueKeyboard`) already claim. Unhandled it did not merely do
 * nothing: it fell through to the native select-all, and `.shelf` is
 * `user-select: none` (#932) while the app around it is not, so the one thing
 * a select-all aimed at the rows could highlight was whatever text the app
 * still leaves selectable everywhere else - which is what the reporter saw.
 * It runs the store action the selection pill's "Select all shown" already
 * runs, so the key and the button say the same thing: everything the current
 * `Show` selection DRAWS, runs taken whole - and on the set grid every
 * hand-made set too. Cmd counts as Ctrl (`metaKey`),
 * Shift and Alt do not - those are chords this list does not define and are
 * left to the browser, AltGr+A among them.
 *
 * **With nothing drawn the key is swallowed and the selection left alone**, and
 * `selectVisible` is where that is decided - see the note at the call. Two
 * things are true at once there and only one of them is obvious: there is
 * nothing to select, and `selectedIds` may still be full, because it is pruned
 * against a FETCH (`pruneSelection`) and not against the `Show` narrowing that
 * emptied the list. Replacing a selection the reader still holds with an empty
 * one is a silent clear, from a key that says "select", with no undo and (the
 * pill being gated on `selectedRows`) no control on screen to do it
 * deliberately.
 *
 * `event.repeat` is refused for the same reason `useDedupQueueKeyboard` refuses
 * it: a held chord would rebuild a set over every drawn row, up to 1,800 of
 * them, once per repeat.
 */
function onShelfKeydown(event) {
  // H toggles a favourite (#1578), before the Escape/Delete gate below, which
  // would otherwise return first. Claimed only when the shelf owns the key.
  if (isFavouriteKey(event)) {
    if (!shelfOwnsTheKey(event)) return;
    event.preventDefault();
    if (!event.repeat) toggleFavouriteFromKey();
    return;
  }
  const wantsSelectAll =
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.shiftKey &&
    String(event.key).toLowerCase() === "a";
  // Ctrl X cuts the selection's movable copies, Ctrl V pastes them into the
  // open folder, as a drop does (#2204). Inside a text field the guard
  // hands both back, so cutting text still cuts text.
  const clip =
    (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey
      ? String(event.key).toLowerCase()
      : "";
  if (clip === "x" || clip === "v") {
    if (!shelfOwnsTheKey(event) || event.repeat) return;
    if (clip === "x") {
      if (!ownSelection.value.length) return;
      event.preventDefault();
      cutSelected();
      return;
    }
    if (!store.cutCopies || !inFolder.value) return;
    event.preventDefault();
    pasteCut();
    return;
  }
  // F2 is let through for a selected hand-made set, whose pill and menu
  // advertise it; a row's own F2 is handled on the row.
  const renamesSet = event.key === "F2" && store.selectedSets.length > 0;
  if (
    event.key !== "Escape" &&
    event.key !== "Delete" &&
    !wantsSelectAll &&
    !renamesSet
  ) {
    return;
  }
  if (!shelfOwnsTheKey(event)) return;
  if (wantsSelectAll) {
    if (event.repeat) return;
    event.preventDefault();
    // The "nothing drawn, so leave the selection alone" refusal lives in
    // `selectVisible` now, not here: this guard read `visibleRows`, which is the
    // ROW LIST's list, and on the set grid that is a different screen - the key
    // would have passed the guard and then cleared the selection to nothing. The
    // pill's *Select all shown* never had the guard at all, so moving it into
    // the store fixed a second caller as well. The press is still claimed either
    // way, or declining would hand it to the native select-all. On the set grid
    // it takes the owner's own sets as well as the models.
    store.selectVisible();
    return;
  }
  // Escape first puts back a cut, one undo per press.
  if (event.key === "Escape" && store.cutCopies) {
    event.preventDefault();
    store.clearCut();
    return;
  }
  // Selected hand-made SETS (#1520) answer the same two keys with the set
  // vocabulary: Escape clears, Delete deletes the sets - never a file. The
  // early return above already admits only these two keys here; the check is
  // restated so this block is correct read on its own.
  //
  // Sets and models can be held together (Ctrl+click, Ctrl+A). Escape clears
  // both. Delete deletes the sets only when no model is selected: with both,
  // it falls through to the FILE confirmation below, so one key never deletes
  // sets unprompted while also arming a file delete. The sets' own pill still
  // deletes them. (With the cursor on a set from pictures, the grid has
  // already taken Delete to hide that set; it never arrives here.)
  // Missing-base cards answer Escape only: they have no file for Delete.
  if (
    store.selectedMissing.length &&
    isSetGrid.value &&
    event.key === "Escape"
  ) {
    event.preventDefault();
    store.clearMissingSelection();
    store.clearSetSelection();
    store.clearSelection();
    return;
  }
  if (store.selectedSets.length && isSetGrid.value) {
    if (event.key === "Escape") {
      event.preventDefault();
      store.clearSetSelection();
      store.clearSelection();
      return;
    }
    if (event.key === "Delete" && !ownSelection.value.length) {
      event.preventDefault();
      store.deleteHandMadeSets(store.selectedSets);
      return;
    }
    if (event.key === "F2") {
      event.preventDefault();
      startRenameSet();
      return;
    }
  }
  if (renamesSet) return;
  // Escape and Delete are both about a selection, and the guard above no longer
  // asks for one.
  if (!ownSelection.value.length) return;
  if (event.key === "Escape") {
    store.clearSelection();
    return;
  }
  // The row list is a listbox, and Delete means nothing else in it. Stopped so
  // it cannot also reach a browser shortcut on the way out.
  event.preventDefault();
  confirmDelete(event.shiftKey);
}

/** H with no modifiers: the favourite key, the same on every surface. */
function isFavouriteKey(event) {
  return (
    String(event.key).toLowerCase() === FAVOURITE_KEY &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey &&
    !event.shiftKey
  );
}

/** The evidence cards picked on the set grid, by key: a set, not a model. */
const gridCardKeys = computed(() =>
  isSetGrid.value ? store.pickedCardKeys : [],
);

/**
 * H: the selection when there is one, else the focused row or card. On the set
 * grid the cards picked there count as the selection, as a set always has.
 */
function toggleFavouriteFromKey() {
  const selected =
    store.selectedRows.length ||
    store.selectedSets.length ||
    store.selectedMissingKeys.size ||
    gridCardKeys.value.length;
  if (selected) {
    store.toggleFavourites(store.favouriteTargets(gridCardKeys.value));
    return;
  }
  // Nothing picked: the card the grid's cursor is on, when the grid has one.
  const cursorCard = isSetGrid.value
    ? setGridRef.value?.focusedCardKey
    : "";
  if (cursorCard) {
    store.toggleFavourites(store.favouriteTargets([cursorCard]));
    return;
  }
  // A drawn row, or a member of an open run, by the key the cursor holds.
  const key = focusedRowKey.value;
  for (const group of shownGroups.value) {
    for (const row of group.rows) {
      const member = (row.members ?? []).slice(1).find(
        (m) => memberKey(row, m) === key,
      );
      // A member is favourited through its run, which is keyed by the cover.
      if (row.rowKey === key || member) {
        if (row.sha256) {
          store.toggleFavourites({ kind: "model", keys: [row.sha256] });
        } else {
          useNoticeStore().push({
            level: "info",
            text: "This model can be a favourite once its hash is read.",
          });
        }
        return;
      }
    }
  }
}

/**
 * The rows the shelf's own keys act on.
 */
const ownSelection = computed(() => store.selectedRows);

onMounted(() => window.addEventListener("keydown", onShelfKeydown));
// The favourites are read once for the shelf; the store shares that read.
onMounted(() => favourites.ensureLoaded());
onUnmounted(() => window.removeEventListener("keydown", onShelfKeydown));

// ── Hand-made workflow sets (#1520) ─────────────────────────────────────────

const setGridRef = ref(null);

/**
 * The sets from pictures Hide acts on: every card the grid draws selected,
 * whichever pill it is counted on, so the two pills' menus offer one row with
 * one count and Ctrl+A then Hide leaves none of them behind. The model cards
 * are the grid's answer rather than the store's, because only it knows a head
 * picked in its open tray from one picked as its card. The selected hand-made
 * sets come from the store's groups, the shape Hide reads (`hideSetsVerb`,
 * `toggleHiddenSets`), so the set pill's row covers them as well.
 */
const hideableSets = computed(() =>
  isSetGrid.value
    ? [
        ...(setGridRef.value?.selectedCardGroups ?? []),
        ...store.selectedMissingGroups,
        ...store.handMadeGroups.filter((group) =>
          store.selectedSetIds.has(group.set.id),
        ),
      ]
    : [],
);

/** The missing-base heads the Replace dialog is open on, `[]` while closed. */
const replacingHeads = ref([]);
const missingBarRef = ref(null);
const setBarRef = ref(null);

// The set receipt sits above whichever selection pill is up, lifted by its
// MEASURED height the way the grid lifts its own (`actionReceiptLift`). Local
// rather than through the bottom-anchor registry: the pills never registered
// there, and doing so would move the notice stack as well.
const selFloatEl = ref(null);
const selFloatHeight = ref(0);
let selFloatObserver = null;
watch(
  selFloatEl,
  (el) => {
    selFloatObserver?.disconnect();
    selFloatObserver = null;
    selFloatHeight.value = el?.offsetHeight || 0;
    if (!el || typeof ResizeObserver === "undefined") return;
    selFloatObserver = new ResizeObserver(([entry]) => {
      selFloatHeight.value =
        entry.borderBoxSize?.[0]?.blockSize ?? entry.target.offsetHeight;
    });
    selFloatObserver.observe(el);
  },
  { flush: "post" },
);
onUnmounted(() => selFloatObserver?.disconnect());
const receiptLift = computed(() =>
  selFloatHeight.value > 0 ? selFloatHeight.value + FLOATING_BOTTOM_GAP_PX : 0,
);
/** The set the rename dialog is open on, or null. */
const renamingSet = ref(null);

function startRenameSet() {
  if (store.selectedSets.length === 1)
    renamingSet.value = store.selectedSets[0];
}

async function saveSetName(name) {
  const set = renamingSet.value;
  renamingSet.value = null;
  if (set) await store.renameHandMadeSet(set, name);
}

/**
 * "New workflow set with this checkpoint", from a checkpoint's menu.
 *
 * The set grid is where a set is filled, so the shelf goes there. The new set
 * holds what the pictures made with the checkpoint ran with (#1972), so it
 * opens reading Automatic, ready to be changed.
 */
async function newSetWithCheckpoint() {
  const row = store.selectedRows[0];
  if (!row) return;
  // The Sets place, not just the grid's axis, so the sidebar lights Sets and
  // the list's narrowing lets go of the grid.
  if (!isSetGrid.value) store.setPlace("sets");
  await store.createHandMadeSet({
    members: [{ model_id: row.id, slot: "checkpoint" }],
  });
}

/** Remove from set: the selected members of the open hand-made tray. */
function removeSelectedFromSet() {
  const open = store.handMadeGroups.find((g) => g.key === store.openSetKey);
  if (!open) return;
  const ids = new Set(store.selectedModelIds);
  const hashes = (open.set.members ?? [])
    .filter((m) => m.on_shelf && ids.has(m.id))
    .map((m) => m.sha256);
  store.removeFromHandMadeSet(open.set, hashes);
}

// ── The thumbnail verb ──────────────────────────────────────────────────────

const iconInputRef = ref(null);
const thumbnailPickerOpen = ref(false);

/**
 * What the thumbnail is FOR, in the picker's subtitle.
 *
 * A row in the `needs-a-name` state has no name by design, so this falls back
 * the same way the receipt does - the reader has to recognise what they are
 * about to change. A selection is named by its size instead: the picture is
 * about to land on all of them, and that is the fact worth stating before the
 * click rather than after it.
 */
const thumbnailSubject = computed(() => {
  const count = store.selectedModelIds.length;
  if (!count) return "";
  if (count > 1) return `for ${count} models`;
  const row = store.selectedRows[0];
  return `for ${row.name?.text || row.filename || "this model"}`;
});

/**
 * Ask before a bulk set that would REPLACE marks, not before one that adds.
 *
 * The shelf's rule is to confirm only where the prior state cannot be
 * reconstructed, and this falls on the same side of that test as the bulk
 * clear: the images survive in the content-addressed store, but which model
 * wore which is not recorded anywhere else. Counted on the models that already
 * HAVE one, because that is what the verb will actually overwrite - a
 * selection of forty bare rows loses nothing and is not worth a prompt. Asked
 * BEFORE the picker opens, so the reader is not made to choose a picture and
 * then defend the choice.
 *
 * Both counts are expanded through `members`, the way the write is: a ticked
 * run is one row wearing the cover's `icon_sha256`, and prompting on that
 * would be counting one of twelve.
 *
 * Everything material goes in `message`. `useConfirm` has no host mounted yet
 * and falls back to `window.confirm(message)`, which shows nothing else - a
 * count parked in `title` would simply not reach the reader.
 */
async function pickIcon() {
  const models = store.selectedRows.flatMap((row) => row.members ?? [row]);
  const withIcons = models.filter((row) => row.icon_sha256);
  if (models.length > 1 && withIcons.length) {
    const ok = await confirm({
      title: `Replace ${withIcons.length} thumbnails?`,
      message:
        `All ${models.length} selected models get the picture you pick next, ` +
        `replacing the ${withIcons.length} that already have one. The images ` +
        `themselves are kept, but which model wore which is not recorded ` +
        `anywhere else, and there is no undo.`,
      warning: "There is no undo for this.",
      confirmLabel: "Pick a picture",
      danger: true,
    });
    if (!ok) return;
  }
  thumbnailPickerOpen.value = true;
}

/**
 * The library route: send the picture's PIXELS, never a reference to it.
 *
 * The icon store is content-addressed and lives beside the hub, and `model` is
 * a hub row while a picture is a vault row - no key spans the two, and SQLite
 * recycles deleted ids, so a stored `picture_id` would silently re-point after
 * a delete-and-insert and break on every library switch
 * (`services/model_icons.py`). Copying the bytes is what makes the mark
 * survive. The thumbnail is the copy that gets sent: already WebP, generated on
 * demand for any file the server can still reach, and 384px on the short edge -
 * an icon's size rather than a 40 MB original the store would refuse.
 *
 * Read ONCE for the whole selection: every model gets the same bytes, and the
 * store is content-addressed, so they collapse to one file on disk.
 */
async function onThumbnailPicked(picture) {
  try {
    // Fetched with the picker still open, and only then shut. The read can
    // fail - a thumbnail is generated from the file, so an unplugged drive
    // 404s - and a dialog that has already closed leaves the refusal floating
    // over a shelf the reader has to reopen the picker from to try again.
    // Cache-busted because these bytes are about to be STORED: an hour-old
    // thumbnail is a fine tile and the wrong thing to keep.
    const blob = await getPictureThumbnailBlob(picture.id, {
      cacheBuster: Date.now(),
    });
    thumbnailPickerOpen.value = false;
    await store.setIconOnSelected(blob);
  } catch (err) {
    useNoticeStore().push({
      level: "error",
      text: errorDetail(err) || "Could not read that picture.",
    });
  }
}

/**
 * The secondary route, from inside the picker: any file on disk.
 *
 * The picker stays open behind the OS chooser and is closed only once a file
 * really came back. Cancelling the chooser is the ordinary way to change your
 * mind about the file route, and it should land the reader back where they
 * were rather than on a bare shelf.
 */
function pickIconFile() {
  // Cleared first, so choosing the SAME file twice still fires `change` - the
  // obvious way to retry after a refusal, and silent if the value persisted.
  if (iconInputRef.value) iconInputRef.value.value = "";
  iconInputRef.value?.click();
}

async function onIconChosen(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  thumbnailPickerOpen.value = false;
  await store.setIconOnSelected(file);
}

/**
 * Clearing one row needs no prompt; clearing a selection does.
 *
 * The shelf's rule is to confirm only where the prior state cannot be
 * reconstructed. One thumbnail is one picker away from back; a bulk clear is
 * not, and falls on the same side of that test as the bulk base-model
 * overwrite. Counted on the rows that HAVE one, because that is what the verb
 * will actually destroy.
 */
async function confirmClearIcons() {
  const withIcons = store.selectedRows.filter((row) => row.icon_sha256);
  if (!withIcons.length) return;
  if (withIcons.length > 1) {
    const ok = await confirm({
      title: `Clear ${withIcons.length} thumbnails?`,
      message:
        "Those models go back to a generated mark. The images themselves are " +
        "kept, but which model wore which is not recorded anywhere else.",
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

/**
 * Group the selection into one stack.
 *
 * A confirmation rather than a dry run: the reader assembled this group
 * themselves and is looking at it, so there is nothing to show them they have
 * not already chosen. It is still a prompt,
 * because every verb afterwards acts on the whole stack rather than the file
 * that was clicked - but it is no longer a warning about a one-way door, since
 * Ungroup takes it back.
 *
 * **Fusing is the same gesture.** If anything selected is already in a stack,
 * this stacks the stacks: `fuse` lets the route absorb them whole and remove
 * the emptied rows. The prompt says which of the two it is about to do, because
 * merging two groups somebody built is a bigger claim than collapsing loose
 * files and should not be described in the same sentence.
 *
 * This is also the path for files detection will not propose: it groups only
 * what a step or a version suffix explains, and a person's own reading of two
 * files being the same subject is not something the filenames can be made to
 * say.
 *
 * The bar refuses anything the route would, so a failure here is the shelf
 * having changed underneath (409) rather than a gesture that should not have
 * been offered; it is reported and nothing local is guessed at.
 */
async function confirmStack() {
  const ids = store.selectedModelIds;
  if (ids.length < 2) return;
  const fuse = store.selectedRows.some((row) => row.stack_id != null);
  const ok = await confirm({
    title: fuse
      ? `Fuse ${store.selectedRows.length} rows into one stack?`
      : `Group ${ids.length} files into one stack?`,
    message: fuse
      ? "Every member of every stack selected comes along, including any not " +
        "shown, and the stacks they came from are removed. The newest version " +
        "stands for the result, and every verb then acts on all of them."
      : "They become one row on the shelf - the newest version, and within it " +
        "the bare final file or the highest step, stands for the stack - and " +
        "every verb then acts on all of them.",
    confirmLabel: fuse ? "Fuse them" : "Group them",
  });
  if (!ok) return;
  const notices = useNoticeStore();
  try {
    await createStack(ids, null, { fuse });
    await store.fetchRows();
    notices.push({ level: "success", text: stackReceipt(1, 0) });
  } catch (err) {
    notices.push({
      level: "error",
      text: errorDetail(err) || "Those files could not be grouped.",
    });
  }
}

/**
 * Break the selected stacks up, leaving their files loose on the shelf.
 *
 * The undo the shelf never had, and the reason Group no longer has to warn that
 * nothing takes a stack back. Confirmed rather than immediate because it is a
 * structural edit somebody may have spent a while assembling - but deliberately
 * *not* warned about: **no file is moved, renamed or deleted**, so treating it
 * with the vocabulary reserved for the verbs that destroy bytes would teach the
 * reader to ignore that vocabulary.
 *
 * One call per stack: each is one row's worth of work, and one that fails must
 * not discard the others.
 */
async function confirmUnstack() {
  const stackIds = [
    ...new Set(
      store.selectedRows
        .map((row) => row.stack_id)
        .filter((id) => id != null)
        .map(Number),
    ),
  ];
  if (!stackIds.length) return;
  const ok = await confirm({
    title:
      stackIds.length === 1
        ? "Break this stack up?"
        : `Break ${stackIds.length} stacks up?`,
    message:
      "Their files go back to being separate rows on the shelf. Nothing is " +
      "moved, renamed or deleted - and PixlStash may offer to regroup them, " +
      "because they still look like one subject.",
    confirmLabel: stackIds.length === 1 ? "Ungroup it" : "Ungroup them",
  });
  if (!ok) return;
  const notices = useNoticeStore();
  const results = await Promise.allSettled(
    stackIds.map((id) => unstackStack(id)),
  );
  const failed = results.filter((r) => r.status === "rejected");
  await store.fetchRows();
  notices.push({
    level: failed.length === results.length ? "error" : "success",
    text: unstackReceipt(results.length - failed.length, failed.length),
  });
}

/**
 * Make the selected member the file the shelf draws for its run.
 *
 * **No confirmation.** Nothing is moved, renamed or regrouped - one column
 * changes and the strip re-sorts under the reader's eyes - and the gesture is
 * its own undo: the old cover is still in the strip, one right-click away from
 * taking the role back. A prompt in front of that would spend the vocabulary
 * the destructive verbs need.
 *
 * The choice sticks. Detection only ever looks at loose adapters, so no later
 * scan recomputes the order and picks the filename's answer again.
 */
async function makeCover() {
  const member = store.selectedRows[0];
  if (!member || member.stack_id == null) return;
  const notices = useNoticeStore();
  try {
    await setStackCover(member.stack_id, member.id);
    await store.fetchRows();
    notices.push({
      level: "success",
      text: `${member.filename} now stands for this run.`,
    });
  } catch (err) {
    notices.push({
      level: "error",
      text: errorDetail(err) || "That file could not be made the cover.",
    });
  }
}

/**
 * Take the selected members out of their runs, leaving them loose.
 *
 * The single-file counterpart to Ungroup, for the checkpoint that turns out to
 * be a different subject - and confirmed for the same reason Ungroup is: it is
 * a structural edit to something somebody assembled, and the sentence is worth
 * reading because a run left with one member dissolves entirely rather than
 * becoming a stack of one.
 *
 * One call per member, so one refusal does not discard the rest.
 */
async function confirmRemoveFromStack() {
  const members = store.selectedRows.filter(
    (row) => row.stack_id != null && !row.members,
  );
  if (!members.length) return;
  const ok = await confirm({
    title:
      members.length === 1
        ? "Take this file out of its run?"
        : `Take ${members.length} files out of their runs?`,
    message:
      "It goes back to being a separate row on the shelf. Nothing is moved, " +
      "renamed or deleted - and a run left with a single file stops being a " +
      "run at all, so both of its files come loose.",
    confirmLabel: members.length === 1 ? "Take it out" : "Take them out",
  });
  if (!ok) return;
  const notices = useNoticeStore();
  const results = await Promise.allSettled(
    members.map((member) => removeStackMember(member.stack_id, member.id)),
  );
  const failed = results.filter((r) => r.status === "rejected");
  const landed = results.filter((r) => r.status === "fulfilled");
  const dissolved = landed.filter((r) => r.value?.dissolved).length;
  // The server's own count, not the number of calls: taking one file out of a
  // pair dissolves the run and sends BOTH of them loose, and a receipt saying
  // "1 file" over two moved rows is the half of the outcome the reader did not
  // ask for going unreported.
  const released = landed.reduce(
    (total, r) => total + (Number(r.value?.released) || 1),
    0,
  );
  await store.fetchRows();
  notices.push({
    level: failed.length === results.length ? "error" : "success",
    text: releaseReceipt(released, dissolved, failed.length),
  });
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
 * What one member of a stack is called in the strip.
 *
 * Not the filename: every member shares a name by construction, so repeating it
 * six times says nothing and hides the fields that differ. Those are the
 * version and the step, and a stack can now vary by either - `Foxglove` beside
 * `Foxglove_v2` is one subject across two training runs, and labelling both
 * "Final" would make its two halves indistinguishable in the one place the
 * reader looks to tell them apart.
 */
function memberLabel(member, row) {
  // `training_step` from the API when the row carries one, and the filename
  // only as the fallback it always was. The column is what the scanner parsed;
  // re-deriving it here made the shelf's answer depend on which of two parsers
  // ran, and they are only equal by convention. There is no version column, so
  // the version is always derived.
  const step =
    member.training_step ?? trainingStep(member.filename ?? "") ?? null;
  const label = step === null ? "Final" : `Step ${step.toLocaleString()}`;
  // The version only when the stack actually spans versions. A run whose files
  // all say `v2` says it once, in the run's own name; repeating it on every
  // member would be the "shares a name by construction" noise this label exists
  // to avoid. `spansVersions` is computed once per stack in `collapseStacks`
  // and compares PARSED versions, so it agrees with the server about whether
  // `v2` and `V2.0` are one version.
  if (!row?.spansVersions) return label;
  // `v1` for a member that names no version - not invented, but the version the
  // server sorted it as: an unversioned file existed before `v2` did. Saying
  // nothing here would leave the one unlabelled row in a strip whose whole
  // point is telling versions apart.
  return `${modelVersion(member.filename ?? "") ?? "v1"} · ${label}`;
}

/** The check or cross the owner's set verdicts give this model, if any. */
function rowVerdict(row) {
  return modelVerdictMark(row.set_verdicts);
}

/**
 * The step to show beside a row's name, or "" when there is none to show.
 *
 * Empty for a stack cover: it stands for every step in the run, so naming one
 * would be false. The cover shows its member count instead.
 */
function stepLabel(row) {
  if (row.memberCount > 1) return "";
  const step = row.training_step;
  return typeof step === "number" ? `Step ${step.toLocaleString()}` : "";
}

// ── The two views of this destination (shelf plan F6) ───────────────────────
//
// The tab is DERIVED from the route rather than held beside it, so there is no
// second source of truth to desync: reload and back-navigation land on the same
// view by construction. `/models/runs` is a path and not `?view=runs` because
// the query string is reserved here for modifiers layered on a destination
// (`?overlay=`, `?review=`, the duplicates `?scope=`), and this is a different
// list of different objects with its own keyboard model.

const route = useRoute();
const router = useRouter();

const isShelfTab = computed(() => route.name !== "models-runs");

/** A training run's own page is open on the runs tab (#2118). */
const runOpen = computed(
  () =>
    !isShelfTab.value &&
    [route.query.run, route.query["folder-run"]].some(
      (value) => typeof value === "string" && value !== "",
    ),
);
// TrainingRunLive teleports its status into `#shelf-run-bar` while it is
// rendered, and draws it in place otherwise.
provide("shelf-run-bar", runOpen);

// Set receipts exist only while this tab shows them: their Undo means nothing
// on the runs tab or another screen, and a write landing after the reader left
// must not raise one there.
const operationStore = useOperationStore();
watch(isShelfTab, (on) => operationStore.setLocalReceiptHost(on), {
  immediate: true,
});
onUnmounted(() => operationStore.setLocalReceiptHost(false));

/** How many runs the Training runs place holds, for the count in the bar. */
const trainingRuns = useTrainingRunsStore();
const runsCountLabel = computed(() => {
  const n = trainingRuns.count;
  return n == null ? "" : `${n.toLocaleString()} ${n === 1 ? "run" : "runs"}`;
});

function showTab(which) {
  const name = which === "runs" ? "models-runs" : "models";
  if (route.name !== name) router.push({ name });
}

// ── Set the ai-toolkit output folder (shelf plan F6) ────────────────────────
//
// Setting the folder is all this menu does. The runs inside it are a
// destination of their own (`/training-runs`), reached from the sidebar, and
// the sidebar entry appears from the same signal that hides this item: once the
// output root exists there is nothing left here to add.

const addSourceOpen = ref(false);
// Held raw: a DOM node, not reactive state.
const addSourceInvoker = shallowRef(null);

/** Whether the ai-toolkit output root has been set. */

function openAddSource(invoker) {
  addSourceInvoker.value = invoker ?? null;
  addSourceOpen.value = true;
}

async function closeAddSource() {
  const returnTo = addSourceInvoker.value;
  addSourceOpen.value = false;
  addSourceInvoker.value = null;
  await nextTick();
  restoreFocus(returnTo, rootEl.value);
}

/**
 * Register the picked folder as the output root, then go straight to its runs.
 *
 * Navigating is the point of having set it: the owner asked for this because
 * they have runs to import, and landing them on the list is one less step than
 * telling them a new sidebar entry now exists.
 */
async function onSourcePicked(path) {
  const added = await foldersStore.add({ path, kind: SOURCE_KIND });
  await closeAddSource();
  if (added) showTab("runs");
}

// ── Add file (shelf plan F6's remainder) ────────────────────────────────────
//
// The loose-file path: one adapter that belongs to no training run and does not
// deserve a registered folder of its own. It lands in the managed store - the
// ruled default destination - and the server registers it as it copies, so the
// row is on the shelf when the call returns and no rescan is needed.
//
// No confirmation and no destination picker. A copy into PixlStash's own store
// writes nothing the owner had, removes nothing, and is undone by forgetting the
// row; asking twice would be ceremony around the least dangerous shelf verb
// there is. Choosing another destination is what a drag onto a folder already
// does, and it does it better, with the folder in front of you.

const addFileOpen = ref(false);
const adding = ref(false);
// Held raw: a DOM node, not reactive state.
const addFileInvoker = shallowRef(null);

function openAddFile(invoker) {
  if (adding.value) return;
  addFileInvoker.value = invoker ?? null;
  addFileOpen.value = true;
}

async function closeAddFile() {
  const returnTo = addFileInvoker.value;
  addFileOpen.value = false;
  addFileInvoker.value = null;
  await nextTick();
  restoreFocus(returnTo, rootEl.value);
}

/**
 * Copy the chosen file into the managed store and refresh what it changed.
 *
 * Both stores, for the reason the import has: the shelf gained a row, and the
 * store's file count and `shelf_bytes` moved with it, so the drive bands are
 * stale too.
 */
async function onFilePicked(path) {
  if (!path || adding.value) return;
  const notices = useNoticeStore();
  adding.value = true;
  try {
    const added = await addModelFile(path);
    await Promise.all([
      store.fetchRows(),
      foldersStore.refresh({ quiet: true }),
    ]);
    notices.push({
      level: "success",
      text: `Added ${added?.filename || "the file"} to the shelf. The original is still where it was.`,
    });
  } catch (err) {
    notices.push({
      level: "error",
      text: errorDetail(err) || "Could not add that file.",
    });
  } finally {
    adding.value = false;
  }
}

// The ⋯ the left group folds into below 690px; its rows open dialogs, so they
// need its trigger to hand focus back to.

/**
 * The folder registry lives in Settings › Model folders; every shelf door opens it
 * there. Settings gives focus back to whatever held it when it opened, and a
 * menu item is gone by the time it closes, so a door names the durable control
 * it hangs off and that takes focus first.
 *
 * @param {HTMLElement} [invoker] control to hand focus back to on close.
 */
function openFolders(invoker) {
  invoker?.focus?.();
  emit("open-settings", "models");
}

// `missing` is a fact (the folder was readable, the file was not in it);
// `unreachable` is the absence of one (we could not look). Only the fact wears
// a status colour - claiming a hue for "we do not know" would assert knowledge
// we do not have. `present` reserves its slot and shows nothing.
//
// `not_downloaded` is a fourth thing and wears no status colour either: it is
// one of PixlStash's own engines that nothing has needed yet, which is the
// normal state of about half of them. A download glyph, not a broken-file one.
//
// `removed` is a fifth, and the only absence that is not an absence to report: a
// copy the reader deleted themselves to keep another (#1439). Its row is here on
// purpose - it is what keeps a recipe naming that file resolving to the model -
// so drawing it with the broken-file glyph would report a fault about the
// tidy-up the reader just asked for. A merge glyph, and no status colour.
const LOC_ICON = {
  present: "mdi-check",
  missing: "mdi-file-remove-outline",
  not_downloaded: "mdi-cloud-download-outline",
  unreachable: "mdi-help-circle-outline",
  removed: "mdi-call-merge",
  forgotten: "mdi-folder-off-outline",
};

// What the file line says after the filename, per absence state. On the line
// rather than only in a tooltip, because the reader's next question after "the
// name is fine" is "so where is the file", and a tooltip is not an answer you
// can scan a column for.
const LOC_NOTE = {
  present: "",
  missing: "file is not where it was",
  unreachable: "out of reach",
  removed: "removed as a duplicate",
  forgotten: "every registered copy forgotten",
};

/**
 * The file line's note for a file that is there but cannot be dragged: every
 * copy sits in a folder whose files do not leave it one at a time (the lock in
 * the Folders tree). Without it, All models draws such a row like any other
 * and the drag that refuses it is the only answer.
 *
 * @param {Object} row - a shelf row.
 * @returns {string}
 */
function lockedNote(row) {
  return lockedRows.value.has(row) ? "in a locked folder" : "";
}

/**
 * The rows {@link lockedNote} speaks for, worked out when the rows, folders or
 * scope change rather than per row on every render: `rowMemo` reads the note
 * for every row on each click, and `movableCopies` over 1,500 rows was a third
 * of what a drag paid before its row lifted.
 */
const lockedRows = computed(() => {
  // The drag's own scope, so the note and the refusal never disagree.
  const scope = inFolder.value ? store.folderScope : null;
  const locked = new Set();
  for (const group of shownGroups.value) {
    for (const row of group.rows) {
      if (
        row.locState === "present" &&
        !movableCopies([row], foldersById.value, scope).items.length
      ) {
        locked.add(row);
      }
    }
  }
  return locked;
});

const LOC_TITLE = {
  present: "",
  missing: "The file is not where it was",
  not_downloaded:
    "Not downloaded - PixlStash fetches this when something needs it",
  // Not "the drive is unplugged", however common that is: a subdirectory the
  // scan could not list lands here too, and naming a cause we did not observe
  // is the same overclaim the muted glyph exists to avoid.
  unreachable: "Out of reach: this location could not be read",
  removed: "You removed this copy to keep another one",
  forgotten: "Every registered copy has been forgotten",
};

// The two states that mean SOMETHING IS WRONG, as against `unreachable`, which
// means nothing is. Both are a registered file that is not there: `missing` was
// looked for in a readable folder and not found, `forgotten` has no registered
// copy left at all. They share the row treatment because they share the fact.
const BROKEN_STATES = new Set(["missing", "forgotten"]);

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
    for (const row of group.rows) {
      rows.push({ key: row.rowKey, id: row.id });
      // An OPEN stack's members are drawn rows like any other, so the cursor
      // walks into the strip and a Shift-range spans it. Closed, they are not
      // on screen - and a range that swept up files nobody can see is the one
      // thing `visibleRows` exists to prevent.
      if (row.memberCount > 1 && isStackOpen(row.stack_id)) {
        for (const member of row.members.slice(1)) {
          rows.push({ key: memberKey(row, member), id: member.id });
        }
      }
    }
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
const rovingRowKey = computed(() => {
  // Checked against what is DRAWN, not merely non-null. A remembered key can
  // stop existing under the reader - closing a run they were standing inside,
  // a filter, or a verb that removed the row - and a stale one beats the
  // fallback, leaving no row at `tabindex="0"` and the whole list out of the
  // tab order until something is clicked.
  const remembered = focusedRowKey.value;
  const drawn = drawnRows.value;
  if (remembered && drawn.some((row) => row.key === remembered)) {
    return remembered;
  }
  return drawn[0]?.key ?? null;
});

/**
 * Click, Ctrl+click, Shift+click - the grid's own three gestures.
 *
 * The guard that used to sit here - ignore a click ending a text drag anchored
 * inside THIS row - went with the panel's `user-select: none` (#932): the only
 * text left to drag across is the rename field, which opts back in. The
 * row-being-renamed check is what covers that one remaining case. A drag out of
 * the field ends in a mouseup the field's own `@click.stop` never sees, so the
 * click lands here - and picking a row from under an open field is the same
 * "that was a text drag, not a pick" mistake as before.
 *
 * It cannot swallow a plain click on the row: mousedown there blurs the field
 * first, and `commitRename` clears `editingRowKey` synchronously, so by the
 * time the click arrives this row is no longer the one being renamed. Clicking
 * away to commit still commits and still picks.
 */
function pickRow(row, event) {
  if (editingRowKey.value === row.rowKey) return;
  if (editingBaseKey.value === row.rowKey) return;
  focusedRowKey.value = row.rowKey;
  store.selectFromClick(
    row.id,
    { ctrl: event.ctrlKey || event.metaKey, shift: event.shiftKey },
    orderedRowIds.value,
  );
}

/**
 * A click on the list's background - below the last row, or the gap between
 * groups - clears the selection, as on the picture grid.
 *
 * Decided by where the press STARTED, not by the click's target: a press that
 * slips from one row to another, or a text drag out of the rename field, fires
 * its click on a container here, and neither is a click on nothing.
 * Primary button only, and a click with `detail` 0 (Enter or Space on a
 * control inside, or a programmatic one) never counts: a press can end
 * without a click, and its leftover flag must not arm a later keyboard one.
 */
let pressedBackground = false;

function isListBackground(event) {
  const target = event.target;
  return (
    target === event.currentTarget ||
    target.classList.contains("shelf-group") ||
    target.classList.contains("shelf-list")
  );
}

function onListBackgroundClick(event) {
  if (pressedBackground && event.detail > 0) store.clearSelection();
  pressedBackground = false;
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
 * backslashes, and `CSS.escape` is not defined in jsdom - a selector here would
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

// ── Arriving from a picture's Recipe section (#1313) ───────────────────────
//
// `?model=<id>` picks that model and puts the cursor on it, so a chip clicked
// in the lightbox lands on the file the picture used rather than at the top of
// a shelf of a couple of thousand rows. A model the shelf is not drawing - a
// filter hides it, its group is collapsed, its stack is closed - is left alone
// rather than forced into view: the row the link names is a fact, and what the
// reader has filtered the shelf down to is a decision of theirs.
//
// Honoured once per value, because `drawnRows` recomputes on every filter,
// sort and refresh while the query string stays put, and applying it twice
// would take focus back off whatever the reader had moved to.
let honouredModel = null;

function honourModelQuery() {
  const wanted = route?.query?.model;
  if (!wanted) {
    honouredModel = null;
    return;
  }
  if (honouredModel === wanted) return;
  const row = drawnRows.value.find(
    (entry) => String(entry.id) === String(wanted),
  );
  if (!row) {
    // The shelf has the model but is not drawing it: a filter excludes it, its
    // group is collapsed, or it is a member of a closed stack. Say so instead
    // of landing at the top of the list having visibly done nothing - and
    // offer the one gesture that is certain to reveal it rather than guessing
    // which of the three is in the way. The reader's filters are theirs; this
    // does not clear them behind their back.
    const known = store.rows.find(
      (entry) => String(entry.id) === String(wanted),
    );
    if (!known) return; // not fetched yet, or not on this shelf at all
    honouredModel = wanted;
    useNoticeStore().push({
      level: "info",
      text: `${known.name || known.filename || "That model"} is on the shelf, but the current filters are hiding it.`,
      action: { label: "Show everything", handler: () => store.resetFilters() },
    });
    return;
  }
  honouredModel = wanted;
  focusedRowKey.value = row.key;
  store.selectFromClick(row.id, {}, orderedRowIds.value);
  nextTick(() => focusDrawnRow(row.key));
}

// Registered on mount rather than at setup. A watcher reads its source once
// when it is created, and `drawnRows` reads `shownGroups`, which is declared
// below this point - so a watcher created here would evaluate it inside its own
// temporal dead zone. The rows are fetched asynchronously anyway, so nothing is
// missed by waiting for the mount.
onMounted(() => {
  watch([() => route?.query?.model, drawnRows], honourModelQuery);
});

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
  // Right opens a run, Left closes it - the disclosure keys, on the row rather
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
  // The keyboard half of the pencil. F2 is the rename key everywhere a list has
  // one, and it keeps the affordance off the tab order: the shelf's dialect is
  // that the ROW is the control, so a focusable pencil per row would be 1,800
  // new tab stops for the gesture one key already covers.
  if (event.key === "F2") {
    event.preventDefault();
    // Shift+F2 edits the other field on the row. The base model needs a
    // keyboard path for the same reason the name does - the gesture is a double
    // click and a double click is not reachable without a pointer - and it
    // stays off the tab order for the same reason too.
    if (event.shiftKey) startBaseModelEdit(row);
    else startRename(row);
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
  if (isMenuKey(event)) {
    event.preventDefault();
    openMenuAtRow(event.currentTarget);
    return;
  }
  // Escape is NOT handled here. It is owned by a window listener, so it works
  // wherever focus happens to be - on a row, on the toolbar, on the sidebar, or
  // nowhere at all - rather than only while a row holds the roving tab stop,
  // which is what it used to mean and is not what a reader expects from
  // "Escape clears the selection".
}

/**
 * One state gets a word beside the name, and it is `from-file`.
 *
 * `derived` used to carry one too, and it was a chip on most of the column
 * saying nothing a reader acts on: it is the commonest state on the shelf, and
 * a name we made already reads as ours from its face and its accent rule. The
 * file's own string is different news - that one is worth a word, and the word
 * is what carries it, so it survives greyscale (§4).
 */
const FROM_FILE_TAG_TITLE =
  "This is the file's own name. Nobody has named this model.";

// Inline rename. One row at a time, held by row key: the field is what makes
// the dashed rule and the pencil honest - an affordance that opened a dialog
// would be advertising a field the row does not have.
const editingRowKey = ref("");
const editingName = ref("");
let editingRow = null;
let editingSeed = "";

/** Put the field on a row, seeded with the name it shows and selected. */
function startRename(row) {
  editingRow = row;
  editingRowKey.value = row.rowKey;
  // Seeded with the SHOWN name, selected, so typing replaces it and an edit is
  // one word rather than a retype. A derived name is a guess, so committing the
  // seed untouched writes nothing (`commitRename`) rather than adopting it.
  editingSeed = row.name?.text || row.display_name || "";
  editingName.value = editingSeed;
  nextTick(() => {
    const el = rootEl.value?.querySelector(".shelf-row-rename");
    el?.focus();
    el?.select();
  });
}

/**
 * The pill's Rename, which is the inline field and not a dialog.
 *
 * The row is where a name is edited - the dashed rule under it is what says so
 * - so the pill's button opens that field rather than a second, contradictory
 * way to do the same thing. Gated on one row by the pill; this finds the DRAWN
 * row for it, because a model with copies in two folders is two draws and the
 * field belongs to whichever one is on screen.
 */
function startRenameSelected() {
  const id = store.selectedRows[0]?.id;
  if (id == null) return;
  // The set grid has no row to open a field on, ever: a name lives on a card,
  // and the dashed rule under a row is what makes an inline field honest. It
  // takes `ShelfEditDialog`'s `rename`, which already owns that one string, so
  // the verb is live there rather than being a menu item that does nothing.
  // The row list is left exactly as it was, including its silent no-op when the
  // selection is held but the row is not drawn - that is a separate question
  // and not this change's to answer.
  if (isSetGrid.value) {
    editVerb.value = "rename";
    return;
  }
  for (const group of shownGroups.value) {
    const row = group.rows.find((candidate) => candidate.id === id);
    if (row) {
      startRename(row);
      return;
    }
  }
}

/**
 * Right-click a row: the full verb inventory, at the pointer.
 *
 * The file-manager rule, which is also the grid's: right-clicking a row that is
 * NOT selected selects it and acts on it alone; right-clicking one that is
 * leaves the selection alone, so a menu opened on any of forty selected rows
 * acts on all forty. Without that, the commonest gesture in a bulk edit -
 * select, then right-click one of them - would silently drop the other 39.
 */
function openRowMenu(row, event) {
  focusedRowKey.value = row.rowKey;
  if (!store.isSelected(row.id)) {
    store.selectFromClick(row.id, {}, orderedRowIds.value);
  }
  selBarRef.value?.openContextMenu(event.clientX, event.clientY);
}

/**
 * The set grid asked for the verb menu at a pointer.
 *
 * The grid has already made the card or tray row under it the selection, by the
 * same file-manager rule {@link openRowMenu} follows; this is only the half that
 * needs the bar's ref, which lives here because there is one bar for both views.
 */
function openGridMenu({ x, y, setKey }) {
  selBarRef.value?.openContextMenu(x, y, setKey);
}

/**
 * The context-menu key, which a row owes as much as it owes the right button.
 *
 * Two spellings, because two platforms spell it differently and a browser
 * reports whichever the keyboard sent: the dedicated Menu key, and Shift+F10.
 */
function isMenuKey(event) {
  return event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey);
}

/** Open the verb menu over a row's own box, for the keyboard's sake. */
function openMenuAtRow(el) {
  const box = el?.getBoundingClientRect?.();
  selBarRef.value?.openContextMenu(
    box ? box.left + 24 : 0,
    box ? box.bottom : 0,
  );
}

/**
 * The key a member row is drawn and focused by.
 *
 * The cover's own row key carries the group, so a model catalogued in two
 * folders is two draws with two keys; a member hangs off whichever draw it was
 * expanded under, and gets a distinct tab stop for each.
 */
function memberKey(row, member) {
  return `${row.rowKey}:${member.id}`;
}

/**
 * Click a member of an open run: the same three gestures the cover rows have.
 *
 * The member is selected on its OWN, never as the run - that is the whole
 * distinction the strip is for. Clicking the collapsed row still takes the run
 * whole, so nothing about the atomic gesture is lost; this is the second one,
 * reached only by opening the stack.
 */
function pickMember(row, member, event) {
  focusedRowKey.value = memberKey(row, member);
  store.selectFromClick(
    member.id,
    { ctrl: event.ctrlKey || event.metaKey, shift: event.shiftKey },
    orderedRowIds.value,
  );
}

/** Right-click a member: the same file-manager rule `openRowMenu` follows. */
function openMemberMenu(row, member, event) {
  focusedRowKey.value = memberKey(row, member);
  if (!store.isSelected(member.id)) {
    store.selectFromClick(member.id, {}, orderedRowIds.value);
  }
  selBarRef.value?.openContextMenu(event.clientX, event.clientY);
}

/**
 * The keyboard on a member row.
 *
 * The cover's own handler minus the two keys a member has no answer for: F2
 * renames the RUN, and Right opens a stack this row is already inside. Left
 * closes the run and takes the cursor back up to it, which is the tree-grid
 * convention and the only way out of a strip that just stopped existing.
 */
function onMemberKeydown(row, member, event) {
  const key = memberKey(row, member);
  const drawn = drawnRows.value;
  const index = drawn.findIndex((drawnRow) => drawnRow.key === key);
  const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
  if (step !== undefined) {
    const next = drawn[index + step];
    if (next === undefined) return;
    event.preventDefault();
    focusedRowKey.value = next.key;
    if (event.shiftKey) {
      store.selectFromClick(next.id, { shift: true }, orderedRowIds.value);
    }
    nextTick(() => focusDrawnRow(next.key));
    return;
  }
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    toggleStack(row.stack_id);
    focusedRowKey.value = row.rowKey;
    nextTick(() => focusDrawnRow(row.rowKey));
    return;
  }
  if (event.key === " " || event.key === "Enter") {
    event.preventDefault();
    store.selectFromClick(
      member.id,
      { ctrl: event.key === " " || event.ctrlKey || event.metaKey },
      orderedRowIds.value,
    );
    return;
  }
  if (isMenuKey(event)) {
    event.preventDefault();
    openMenuAtRow(event.currentTarget);
  }
}

function endRename() {
  editingRow = null;
  editingRowKey.value = "";
  editingName.value = "";
}

/**
 * Commit the field, on Enter or on losing focus.
 *
 * Closes BEFORE it writes, so the blur the unmount fires finds nothing to do
 * and the row cannot be written twice. An empty box clears the name back to
 * `NULL`, which is what puts the model back on the backend's naming queue.
 */
async function commitRename() {
  const row = editingRow;
  if (!row) return;
  const next = editingName.value.trim();
  const seed = editingSeed.trim();
  endRename();
  if (next === seed || next === String(row.display_name || "").trim()) return;
  // A cover stands for every member of the run, and they share one name.
  await store.editModelIds(row.memberIds ?? [row.id], {
    display_name: next || null,
  });
}

/**
 * The field's own keys.
 *
 * Everything is stopped from reaching the row and the shelf root: Arrow walks
 * the list, Space and Enter pick, and Escape clears the selection, so a name
 * could not be typed with any of them live underneath.
 */
function onRenameKeydown(event) {
  event.stopPropagation();
  if (event.key !== "Enter" && event.key !== "Escape") return;
  event.preventDefault();
  const key = editingRowKey.value;
  if (event.key === "Enter") commitRename();
  else endRename();
  // Focus goes back to the row it came from: the field is gone and a keyboard
  // reader would otherwise be dropped at the top of the document.
  nextTick(() => focusDrawnRow(key));
}

const editingBaseKey = ref("");
const editingBase = ref("");
let editingBaseRow = null;

/**
 * Put the base-model field on a row, seeded with what the cell shows.
 *
 * That is the file's own string, or for a GUESSED row the label it was read
 * as (`baseModelCell`), so a correction is one word rather than a retype and
 * the commit below compares against the same value. Enter on a guess left as
 * it is accepts it, the same write as the tag's confirm; clicking away leaves
 * it a guess. Emptying the field says "none of these", which then sticks
 * across rescans.
 */
function startBaseModelEdit(row) {
  editingBaseRow = row;
  editingBaseKey.value = row.rowKey;
  editingBase.value = baseModelCell(row).text;
  nextTick(() => {
    const el = rootEl.value?.querySelector(".shelf-row-base-edit");
    el?.focus();
    el?.select();
  });
}

function endBaseModelEdit() {
  editingBaseRow = null;
  editingBaseKey.value = "";
  editingBase.value = "";
}

function cancelBaseModel() {
  const key = editingBaseKey.value;
  endBaseModelEdit();
  // Focus goes back to the row it came from, exactly as the rename field does:
  // the field is gone, and the grid's roving tab stop would otherwise be left
  // at the top of the document.
  nextTick(() => focusDrawnRow(key));
}

/**
 * Commit the field, on Enter (`byKey`) or on losing focus.
 *
 * An unchanged value writes nothing, except that Enter on an untouched guess
 * accepts it, as the tag's confirm does. Closes BEFORE it writes, so the blur
 * the unmount fires finds nothing to do and the row cannot be written twice -
 * the same order the rename above uses.
 * An empty box clears the base model back to `NULL`, which is the state the
 * shelf draws as "not set" and filters as `UNASSIGNED`.
 */
async function commitBaseModel(byKey = false) {
  const row = editingBaseRow;
  if (!row) return;
  const next = editingBase.value.trim();
  const key = editingBaseKey.value;
  endBaseModelEdit();
  // Only when a KEY committed it. A blur committed it by moving the focus
  // somewhere the reader chose, and dragging it back to the row would undo
  // their click.
  if (byKey) nextTick(() => focusDrawnRow(key));
  const cell = baseModelCell(row);
  // Enter on an untouched guess is the reader saying "yes, that one"; a blur
  // is only them leaving, so it keeps the guess a guess.
  if (next === String(cell.text).trim() && !(byKey && cell.guess)) return;
  // A cover stands for every file of the run, and one run was trained against
  // one base model.
  await store.editModelIds(row.memberIds ?? [row.id], {
    base_model: next || null,
  });
}

/**
 * Accept a guessed base model as it stands.
 *
 * Writes the label the cell shows, which the server records as the owner's
 * own answer, so the tag goes and a rescan cannot guess over it.
 */
async function confirmBaseModel(row) {
  await store.editModelIds(row.memberIds ?? [row.id], {
    base_model: baseModelCell(row).text,
  });
}

/**
 * File a row as the kind its Kind menu picked (#1962).
 *
 * Any pick is a person deciding what the file is, so the server stops the
 * offer with it. The receipt names where the row went, because on the feature
 * axis it leaves the group it was picked in; Undo puts back the kind and the
 * offer both.
 */
async function setKindFromMenu(row, fileKind) {
  const ids = row.memberIds ?? [row.id];
  const label = fileKindLabel(fileKind);
  const undo = { file_kind: row.file_kind, suggest_kind: !row.kind_decided };
  await store.editModelIds(
    ids,
    { file_kind: fileKind },
    {
      text:
        `Filed ${row.name.text || row.filename || "the model"} as ${label}.` +
        (store.groupAxis === "feature" ? ` It is now under ${label}.` : ""),
      action: { label: "Undo", handler: () => store.editModelIds(ids, undo) },
    },
  );
  // Whether or not the write landed: the menu has closed either way.
  focusKindMenu(row.id);
}

/** "Adapter…": an adapter names its algorithm, which the Set kind dialog asks. */
function setKindByDialog(row) {
  store.selectFromClick(row.id, {}, orderedRowIds.value);
  editVerb.value = "kind";
}

/** "Don't suggest a kind" (false) and "Suggest a kind again" (true). */
async function setKindSuggestion(row, on) {
  await store.editModelIds(
    row.memberIds ?? [row.id],
    { suggest_kind: on },
    {
      text: on
        ? "The shelf will suggest a kind for it again."
        : "The shelf will not suggest a kind for it.",
    },
  );
  // Whether or not the write landed: the menu has closed either way.
  focusKindMenu(row.id);
}

/**
 * Back to the row's Kind menu once the shelf has redrawn: a refiled row moves
 * group, and its row key carries the group, so it is found by its model id.
 */
function focusKindMenu(modelId) {
  nextTick(() => {
    const id = String(modelId);
    for (const el of rootEl.value?.querySelectorAll("[data-row-key]") || []) {
      const key = el.dataset.rowKey;
      if (key === id || key.startsWith(`${id}:`)) {
        el.querySelector("[data-testid='kind-menu']")?.focus();
        return;
      }
    }
  });
}

/**
 * The date column, named and sorted by whichever date axis it is drawn in.
 *
 * The only entry in the header strip that is not a constant. There is ONE date
 * column and there are two date sort keys, so the heading has to move: it says
 * `Date added` while the shelf is ordered on anything that is not a date and
 * while it is ordered on `added_at`, and `File date` once the Sort panel has
 * moved the shelf onto `file_mtime`. Pressing it then does what every other
 * heading does - sort on the key it names, flip the direction if that key is
 * already the sorted one - so the two axes stay reachable from the panel while
 * the heading always names the one the cells beneath it are showing.
 */
const DATE_COLUMN = computed(() => {
  const sort = dateColumnKey(store.view.sortKey);
  return { key: "date", label: SORT_LABELS[sort].label, sort };
});

/**
 * The day a row shows in the date column, or nothing when it cannot answer.
 *
 * An empty cell rather than a dash, exactly as the size column does it: the two
 * are the row's figures, and a placeholder in a figure column is noise the eye
 * has to step over on every scan.
 *
 * @param {Object} row - a shelf row, or a stack member with `own` set.
 * @param {boolean} [own=false] - read the member's own date, not its run's.
 */
function dateCell(row, own = false) {
  const iso = modelDate(row, store.view.sortKey, own);
  return iso ? formatUserDay(iso, userPrefs.dateFormat) : "";
}

/** The same date in full, named by its axis, for the cell's tooltip. */
function dateTitle(row, own = false) {
  const iso = modelDate(row, store.view.sortKey, own);
  return iso
    ? `${DATE_COLUMN.value.label}: ${formatUserDate(iso, userPrefs.dateFormat)}`
    : undefined;
}

/** "1 model" / "12 models", so no line ever reads "1 models". */
function modelCount(n) {
  return `${n.toLocaleString()} ${n === 1 ? "model" : "models"}`;
}

// ── The open folder (#2203) ──────────────────────────────────────────────

/** True on a folder of the Folders tree. */
const inFolder = computed(
  () => store.place === "folder" && store.folderScope != null,
);

/** The registered folder the open one is in. */
const openFolderRow = computed(() =>
  inFolder.value
    ? foldersStore.folders.find(
        (one) => Number(one.id) === store.folderScope.folderId,
      )
    : null,
);

const openDevice = computed(() =>
  openFolderRow.value
    ? foldersStore.deviceByFolderId.get(Number(openFolderRow.value.id))
    : null,
);
const openDrive = computed(() => driveFullness(openDevice.value));
const openDriveName = computed(
  () => openDevice.value?.label || openDevice.value?.mount_point || "",
);

// The open level, read fresh on every open: the tree's copy may be from
// before a move.
watch(
  () => (inFolder.value ? store.folderScope : null),
  (scope) => {
    if (scope) foldersStore.loadDirs(scope.folderId, scope.path, { force: true });
  },
  { immediate: true },
);

/** The open folder's subfolders (a locked folder's listing has none). */
const subfolders = computed(() => {
  if (!inFolder.value) return [];
  const { folderId, path } = store.folderScope;
  return (
    foldersStore.dirListings.get(foldersStore.dirsKey(folderId, path))?.listing
      ?.dirs ?? []
  );
});

/**
 * The open folder's models ComfyUI's own loaders cannot see there (#2205), by
 * row id. Empty while where ComfyUI loads from is unknown, and in a locked
 * folder, whose files cannot be dragged out to fix it.
 */
const misplacedIds = computed(() => {
  const ids = new Set();
  const scope = inFolder.value ? store.folderScope : null;
  const reads = foldersStore.comfyuiReads;
  if (!scope || scope.locked || !reads.known) return ids;
  for (const row of store.visibleRows) {
    const kind = shelfTypeOf(row);
    const here = (row.locations ?? []).filter(
      (loc) => loc?.state === "present" && inFolderScopeOf(loc, scope),
    );
    if (here.some((loc) => isMisplaced(loc, kind, reads))) ids.add(row.id);
  }
  return ids;
});

function selectMisplaced() {
  store.selectRows(
    store.visibleRows.filter((row) => misplacedIds.value.has(row.id)),
  );
}

const NO_MODELS_LINE =
  "No models found. PixlStash lists what it finds in the model folders registered on this machine.";
/** What an empty place says in its own words; Show's own empty state is above. */
const emptyPlaceLine = computed(() => {
  // An empty shelf says so on every place: "every model is in a set" would be
  // true of nothing.
  if (!store.placeCounts.all) return NO_MODELS_LINE;
  if (store.place === "favourites") return "No favourite models yet.";
  if (store.place === "recent") return "Nothing was added in the last 30 days.";
  if (store.place === "loose") return "Every model is in a set.";
  if (inFolder.value) {
    // The models are known from the shelf; only "directly" waits on the
    // listing. Until it has answered, the line claims no more than is known.
    const { folderId, path } = store.folderScope;
    const listing = foldersStore.dirListings.get(
      foldersStore.dirsKey(folderId, path),
    )?.listing;
    return !listing || subfolders.value.length
      ? "No models directly in this folder."
      : "No models in this folder.";
  }
  return NO_MODELS_LINE;
});

/**
 * The shelf runs the dialogs the sidebar asks for. Each one returns focus to the
 * control that asked, which the sidebar passes along.
 */
watch(
  () => store.shellRequest,
  (request) => {
    if (!request) return;
    if (request.what === "add-folder" || request.what === "manage-folders") {
      openFolders(request.el);
    } else if (request.what === "add-file") {
      openAddFile(request.el);
    } else if (request.what === "set-source") {
      openAddSource(request.el);
    }
  },
);

const shownGroups = computed(() => store.groups);

/**
 * What a group header says out loud: its label and how many models it holds.
 */
function groupLabel(group) {
  return [
    group.label,
    modelCount(group.rows.length),
  ]
    .filter(Boolean)
    .join(", ");
}



/**
 * The offline mounts, said once, with the number of rows they take with them.
 *
 * One sentence however many folders are out: the reader's question is "is
 * something wrong or is a disk just unplugged", and a list of paths is the
 * answer to a question they have not asked yet. The paths ride in the `title`
 * for when they have.
 *
 * Deliberately NOT the error voice. Nothing here is lost and nothing needs
 * fixing - the models come back the moment the drive does - so the line states
 * the fact and stops.
 */
const offlineNote = computed(() => {
  const mounts = store.offlineMounts;
  if (!mounts.length) return "";
  const models = modelCount(
    mounts.reduce((total, mount) => total + mount.count, 0),
  );
  if (mounts.length === 1) {
    return `${mounts[0].path} is offline - ${models} on it cannot be read.`;
  }
  const folders = `${mounts.length.toLocaleString()} model folders`;
  return `${folders} are offline - ${models} on them cannot be read.`;
});

/** The offline paths, for the banner's tooltip. */
const offlineMountPaths = computed(() =>
  store.offlineMounts.map((mount) => mount.path).join("\n"),
);

/**
 * The count at the toolbar's start.
 *
 * Under folder grouping a model with copies in two folders is drawn under both,
 * so the group counts add up to more than the shelf holds. Both numbers are
 * stated when they differ rather than picking one and being wrong about the
 * other: `models` is distinct files on the shelf, `copies` is rows on screen.
 */
const countLabel = computed(() => {
  // On the set axis the row count is about a list that is not on screen. The two
  // figures a reader needs are how many sets there are and how much evidence is
  // behind them, which is the combination count rather than a second card count.
  if (isSetGrid.value) {
    const sets = store.setGroups.length;
    const recipes = store.visibleCombinations.length;
    const setsLabel = sets === 1 ? "1 set" : `${sets.toLocaleString()} sets`;
    if (!recipes) return setsLabel;
    return `${setsLabel} · ${recipes.toLocaleString()} ${
      recipes === 1 ? "combination" : "combinations"
    }`;
  }
  const models = modelCount(store.visibleRows.length);
  const drawn = store.renderedCount;
  if (drawn === store.visibleRows.length) return models;
  return `${models} · ${drawn.toLocaleString()} copies`;
});

/** True while the list is cut into groups, i.e. headers are drawn. */
const grouped = computed(() => store.groupAxis !== "none");

/**
 * True while the set grid has the content area instead of the row list.
 *
 * The one axis whose groups overlap, so the one that cannot be a band: see
 * `GRID_GROUP_BY`. Gated on the tab as well, because the training runs own the
 * area on the other one.
 */
const isSetGrid = computed(
  () => isShelfTab.value && store.view.groupBy === GRID_GROUP_BY,
);

/**
 * True while a read is in flight with nothing to show yet. A refetch over rows
 * already on screen keeps them until the new ones land, so the list keeps its
 * scroll position; one over an empty view (Reset from a narrowed, empty Show)
 * still says it is reading rather than flashing an empty state.
 */
const firstRead = computed(
  () => store.loading && (!store.loaded || !store.visibleRows.length),
);

/**
 * Answer "what else has this run with" for one model.
 *
 * The selection bar's own verb, so it reads the selection - which now answers on
 * both screens. A tray row's own NAME still hands its file up through
 * `@works-with` instead of going through the selection, because pressing a name
 * is a question about that file and not a decision to act on it. Both land on
 * {@link showWorksWith}, which is where the focus return is remembered - and
 * the dialog normalises the two row shapes itself, because a shelf row's `name`
 * is `modelName`'s `{text, state}` pair and a set member's is a string.
 */
function openWorksWith() {
  showWorksWith(store.selectedRows[0] ?? null);
}

/**
 * Every verb the selection bar emits, and what runs it.
 *
 * One map for two askers: the shelf's own bar, bound with `v-on`, and the
 * Models rail, which is not this view's child and asks through
 * `store.requestVerb`. Both act on `store.selectedRows`, so each confirmation
 * exists once.
 */
const verbListeners = {
  "hide-sets": () => store.toggleHiddenSets(hideableSets.value),
  "hide-models": () => store.toggleHiddenModels(store.selectedModels),
  rename: startRenameSelected,
  "set-base-model": () => (editVerb.value = "base-model"),
  "lookup-base-model": confirmLookupBaseModel,
  "set-kind": () => (editVerb.value = "kind"),
  "set-trigger-words": () => (editVerb.value = "trigger-words"),
  stack: confirmStack,
  unstack: confirmUnstack,
  "make-cover": makeCover,
  "remove-from-stack": confirmRemoveFromStack,
  move: () => openMove(store.selectedRows),
  cut: cutSelected,
  paste: pasteCut,
  "merge-copies": openMergeCopies,
  "open-location": openLocation,
  "set-icon": pickIcon,
  "clear-icons": confirmClearIcons,
  forget: confirmForget,
  delete: confirmDelete,
  "works-with": openWorksWith,
  "new-set": newSetWithCheckpoint,
  "clone-set": (group) => store.cloneSetFromPictures(group),
  "remove-from-set": removeSelectedFromSet,
};

watch(
  () => store.verbRequest,
  (request) => {
    // The runs tab draws none of this, so nothing may open over it.
    if (request && isShelfTab.value) verbListeners[request.verb]?.(...request.args);
  },
);

/** Open the dialog, remembering what to hand focus back to. */
function showWorksWith(model) {
  if (!model) return;
  worksWithInvoker.value =
    document.activeElement === document.body ? null : document.activeElement;
  worksWithModel.value = model;
}

function closeWorksWith() {
  const returnTo = worksWithInvoker.value;
  worksWithModel.value = null;
  worksWithInvoker.value = null;
  // The row or the card the reader was on, then the shelf root - never <body>.
  nextTick(() => restoreFocus(returnTo, rootEl.value));
}

const activeSort = computed(
  () => SORT_LABELS[store.view.sortKey] || SORT_LABELS.added_at,
);

// The badge already says HOW MANY sections deviate; the tooltip says what the
// button is and nothing more, because naming the sections would be a sentence
// that grows with the filter. The GLYPH is the design's funnel; the word stays
// `Show`, which is what this panel is called everywhere else in the product.
const showButtonTitle = computed(() =>
  store.activeCount > 0
    ? `Show: ${store.activeCount} filters active`
    : "Show: what is listed",
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

/* ── The header strip ──────────────────────────────────────────────────────
   The fixed data columns, in the order the rows draw them. `sort` is the
   SORT_KEYS value the heading orders on; every column has one.

   The Name column is not in this list. It is the flexible track - it takes
   whatever the others leave - so it has a heading and a sort but no width
   to drag, and it is written out on its own in the template.

   Every heading is LEFT-aligned, including Size's, whose figures are not: the
   heading is a label naming the column and the figures are a magnitude being
   compared. The grip lives on the column's LEFT edge, so it is held clear of
   the label by sitting in the seam rather than flush against the column - see
   `.shelf-head-grip`. */
const NAME_COLUMN = { key: "name", label: "Name", sort: "name" };

/* A computed and not a constant, for one entry: `DATE_COLUMN` renames itself
   with the axis it is drawn in. The three ahead of it never move. */
const SHELF_COLUMNS = computed(() => [
  { key: "kind", label: "Type", sort: "type" },
  { key: "base", label: "Base", sort: "base_model" },
  { key: "size", label: "Size", sort: "size" },
  DATE_COLUMN.value,
  { key: "sets", label: "Sets", sort: "sets" },
]);

/** How far one arrow-key press moves a column edge. One --space-3. */
const RESIZE_STEP = 8;

/**
 * The Name track's floor, and the only thing standing between the grips and a
 * shelf that scrolls sideways.
 *
 * Not a `min-width` in the stylesheet: a genuinely narrow window is still
 * allowed to squeeze the name - that is what the flexible track IS - and a CSS
 * floor would make those windows overflow instead. This bounds the GESTURE, so
 * the reader cannot do it to themselves. 200px is about 24 characters of the
 * name line, past which the second line's filename is what is being read.
 */
const MIN_NAME_WIDTH = 200;

/** The column drag in flight, or null. Never persisted. */
const resizing = ref(null);

/**
 * The remembered widths, handed to the row rules as the custom properties they
 * already read. Bound on the root rather than per cell so the headings, the
 * rows and a stack's member rows cannot drift apart: there is one declaration
 * of each width and every cell resolves the same variable.
 */
const columnStyle = computed(() =>
  Object.fromEntries(
    Object.entries(store.view.columnWidths).map(([key, px]) => [
      `--shelf-col-${key}`,
      `${px}px`,
    ]),
  ),
);

/** `"ascending"` / `"descending"` / `"none"`, for `aria-sort`. */
function columnSortState(key) {
  if (!key || store.view.sortKey !== key) return "none";
  return store.view.sortDirection === "asc" ? "ascending" : "descending";
}

/**
 * A heading's accessible name: what the column is, how it is sorted now, and
 * what pressing it would do.
 *
 * The direction phrases keep their capitals for the reason `sortButtonTitle`
 * does - "A to Z" lowercased reads as a typo - so the three parts are separate
 * sentences rather than one clause.
 */
function columnSortLabel(column) {
  const now = store.view.sortKey === column.sort;
  const next = now
    ? store.view.sortDirection === "asc"
      ? "desc"
      : "asc"
    : defaultSortDirection(column.sort);
  const state = now
    ? `sorted ${sortDirectionLabel(column.sort, store.view.sortDirection)}`
    : "not sorted";
  return `${column.label}, ${state}. Activate to sort ${sortDirectionLabel(column.sort, next)}.`;
}

/**
 * Sort on a column, or flip the direction if it is already the sorted one.
 *
 * A key the reader has just arrived at starts at its own end, and that is
 * `setView`'s job rather than this one's: the Sort panel writes `sortKey` too,
 * and a rule living in one of the two writers is a rule the other breaks.
 */
function sortByColumn(key) {
  if (store.view.sortKey === key) toggleDirection();
  else store.setView({ sortKey: key });
}

/**
 * Start a column drag.
 *
 * Pointer capture rather than window listeners: the grip keeps receiving moves
 * once the pointer has left it - which it does immediately, because dragging
 * an edge is exactly moving away from where you pressed - and the browser
 * releases the capture for us on pointerup, on cancel, and if the element goes
 * away mid-drag.
 *
 * The primary button on ANY device, not just a mouse: a pen's barrel button
 * reports `button: 2` with `pointerType: "pen"`, and a guard that only tested
 * mice let it start a drag. Touch reports 0 and is unaffected.
 *
 * There is deliberately NO `preventDefault()` here. It is not needed - `.shelf`
 * already sets `user-select: none` and the grip sets `touch-action: none`, so
 * neither a text selection nor a touch scroll can start - and calling it
 * suppresses the compatibility mouse events, which is what focuses the grip on
 * click and what `dblclick` (the only pointer-side way back from a mis-drag)
 * is built on.
 */
function startResize(key, event) {
  if (event.button !== 0) return;
  resizing.value = {
    key,
    startX: event.clientX,
    startWidth: store.view.columnWidths[key],
  };
  event.currentTarget.setPointerCapture?.(event.pointerId);
}

// Not persisted per frame: see `setColumnWidth`. The end of the gesture is
// what writes.
function onResizeMove(event) {
  if (!resizing.value) return;
  // The pointer came back with nothing held down, so the release happened
  // somewhere this grip never heard about - a refused or lost capture, or the
  // strip re-rendering out from under the drag. Without this the drag is stuck
  // for good and the next hover resumes it from the original press.
  if (event.buttons === 0) {
    endResize();
    return;
  }
  // Leftwards WIDENS: the grip is the column's left edge, so the width grows
  // away from the pointer's direction of travel and the edge stays under it.
  const { key, startX, startWidth } = resizing.value;
  store.setColumnWidth(
    key,
    widenable(key, startWidth - (event.clientX - startX)),
    false,
  );
}

/**
 * The requested width, held to what the Name track can actually give up.
 *
 * The ceiling in the store is a sanity bound on a stored blob; THIS is the
 * limit a drag meets, and it is measured rather than guessed because it is a
 * fact about the panel in front of the reader. Name is the flexible track, so
 * every pixel a fixed column takes comes out of it: the room available is
 * whatever Name has beyond `MIN_NAME_WIDTH` right now, and past that the shelf
 * would scroll sideways and rows would slide under a strip whose background
 * stops at the scrollport.
 *
 * Measured per event rather than once at `pointerdown` so the keyboard gets
 * the same limit, and so a window resized mid-drag is answered honestly. It is
 * one `offsetWidth` read on a pointer move, which is a layout the browser has
 * already done by the time the event is dispatched.
 *
 * It is a CEILING and nothing else, so a request to narrow passes through
 * untouched. The ceiling itself never falls below the column's current width,
 * which is what lets an already-cramped panel go on shrinking a column it can
 * no longer widen.
 *
 * A track measuring 0 is not a track with no room, it is one nothing has laid
 * out - the strip is not on screen, or this is jsdom. Unmeasured means
 * unlimited, because the alternative is a grip that silently refuses to move.
 *
 * Also the grip's `aria-valuemax`, so the announced maximum is the one the
 * gesture will actually enforce rather than the store's absolute bound. That
 * binding is as fresh as the last render, which covers every width change and
 * every sort; a window resized with nothing else touched leaves it overstated
 * until the next one. Deliberately not fixed with a resize listener - it would
 * re-render the whole list on every resize frame to keep one attribute exact,
 * and the value it replaces was overstated ALL of the time.
 */
function widenable(key, px) {
  const measured = rootEl.value?.querySelector(
    ".shelf-head-col--label",
  )?.offsetWidth;
  const current = store.view.columnWidths[key];
  const spare = measured ? measured - MIN_NAME_WIDTH : Infinity;
  return Math.min(px, current + Math.max(0, spare));
}

function endResize() {
  if (!resizing.value) return;
  const { key } = resizing.value;
  resizing.value = null;
  store.setColumnWidth(key, store.view.columnWidths[key]);
}

/**
 * The keyboard half of the same gesture, which is the whole reason the grip is
 * a focusable `separator` rather than a decoration on the heading.
 *
 * Left/Right/Home/End are the window-splitter pattern's own keys, and they move
 * the SEPARATOR, not the number: the grip is the column's left edge, so Left
 * widens and Home - the separator as far left as it goes - is the widest the
 * column gets. That is the same mapping the pointer has. `Enter` is
 * the pattern's "restore the default position", and here it is the ONLY way
 * back: a width is remembered for good once it has been dragged, so without a
 * reset a mis-drag is permanent short of clearing the browser's storage. A
 * double-click on the grip does the same thing, which is the pointer-side
 * convention every table with draggable columns already trains.
 */
function onGripKeydown(key, event) {
  const width = {
    ArrowLeft: () => store.view.columnWidths[key] + RESIZE_STEP,
    ArrowRight: () => store.view.columnWidths[key] - RESIZE_STEP,
    Home: () => MAX_COLUMN_WIDTH,
    End: () => MIN_COLUMN_WIDTHS[key],
    Enter: () => DEFAULT_COLUMN_WIDTHS[key],
  }[event.key];
  if (!width) return;
  // Home and End scroll the list, and the list is 1,800 rows long.
  event.preventDefault();
  store.setColumnWidth(key, widenable(key, width()));
}

function resetColumn(key) {
  store.setColumnWidth(key, DEFAULT_COLUMN_WIDTHS[key]);
}

/** What a row's mark borrows a face from: the person or set it is attached to. */
function ringFor(row) {
  return assignmentRing(row.attachments, {
    characters: entityLists.characters,
    sets: entityLists.pictureSets,
  });
}

/**
 * The Sets cell: how many shown set cards hold the model, or a dash.
 *
 * A stack's row is its cover, so the count is the cover's own - the same
 * answer the rail's "In sets" gives for the same file.
 *
 * @param {Object} row - a shelf row.
 * @returns {number|string}
 */
function setsCell(row) {
  // Blank until the sets are read: a dash would say "in none" for every row.
  if (!store.setsKnown) return "";
  return store.setMembership.get(row.id)?.length || "\u2013";
}

/**
 * Everything a row draws from outside its own object, for the list's `v-memo`.
 *
 * The rows are one component, so without it a selection change re-rendered all
 * of them: 1,500 rows cost a quarter of a second per click, and a drag that
 * selects its row paid it before the row would lift. `row` itself is a fresh
 * object whenever `visibleRows` recomputes (`displayRow`), so its own fields
 * need no entry; what belongs here is each other value the row's template or
 * its helpers read. A value left out draws stale until the row next changes.
 *
 * An open run draws its members in the same iteration, each with its own
 * selection, so it is never memoised: the `{}` differs every render.
 *
 * @param {Object} row - a shelf row.
 * @returns {Array}
 */
function rowMemo(row) {
  const key = row.rowKey;
  // The sets themselves rather than `isSelected`, `isCut` and `isFavouriteRow`:
  // a store function is a Pinia action, and its wrapper, paid three times per
  // row on every render, cost more than the rest of this list together.
  return [
    row,
    store.selectedIds.has(row.id),
    store.cutCopies?.ids.has(row.id) ?? false,
    favourites.byKind.model?.has(row.sha256) ?? false,
    key === rovingRowKey.value,
    key === editingRowKey.value && editingName.value,
    key === editingBaseKey.value && editingBase.value,
    misplacedIds.value.has(row.id),
    lockedNote(row),
    setsCell(row),
    // The one thing a bare mark reads from the entity lists, which land after
    // the rows: a set's chosen icon. A face is addressed by id alone.
    ringFor(row).icon,
    store.view.sortKey,
    userPrefs.dateFormat,
    isStackOpen(row.stack_id) && {},
  ];
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
  // shelf knows it is registered - the dialog used to be its only reader.
  //
  // NOT `quiet`: that suppresses the folder store's `loading`, and the folders
  // dialog reads it. Opening the dialog while this first fetch is in flight
  // would show an empty list with no "Reading the registered folders…" state.
  // Unawaited already means it does not hold up the shelf.
  foldersStore.refreshDevices();
  foldersStore.refresh();
  // Where ComfyUI loads each kind from, for the folder marks (#2205).
  foldersStore.loadComfyuiReads();
  // The names, colours and thumbnails behind the assignment rings. Cached
  // and shared with the sidebar, so on a warm cache this repaints the marks
  // without a request; unawaited, because a row whose marks read `#12` for a
  // moment is a better shelf than one that waits for two list reads to draw.
  entityLists.refresh("characters");
  entityLists.refresh("sets");
  // A move is machine-wide and outlives this component, so one may already be
  // running: started before a reload, or from another tab. Adopting it is what
  // puts the progress back rather than leaving the list live over files that
  // are moving under it. Only a `running` job is adopted - a finished one
  // belongs to a receipt that has already been shown.
  moves.adopt();
});

// A credential change (logout, login, share token, restore) empties the store,
// and an empty shelf reads as "this machine has no models". Refetching rather
// than gating the empty state on `loaded`: the view is still on screen and its
// job is to show the shelf, so a blank body would be a second wrong answer.
// The store cannot do this itself: session-reset handlers run BEFORE the new
// credential is installed, whereas this pre-flush watcher runs after. The
// sets go with the rows, and the set grid and the Models rail asked for them
// only as they mounted, so they are read again here too.
watch(
  () => store.loaded,
  (isLoaded) => {
    if (isLoaded) return;
    store.fetchRows();
    store.loadWorkflowSets();
  },
);
</script>

<style scoped>
.shelf {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  /* The positioning context for `.shelf-progress` AND for the floating
     selection pill. Without it either resolves against whatever ancestor
     happens to be positioned - today the grid column, tomorrow anything. */
  position: relative;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  outline: none;
  /* `--shelf-col-kind` / `--shelf-col-base` / `--shelf-col-size` /
     `--shelf-col-date` / `--shelf-col-sets` are NOT declared here. `columnStyle` writes all five
     onto this element from `view.columnWidths`, whose defaults are the
     resolved design's own (ui_kits/app/model-shelf.html, row anatomy; the date
     column is this feature's, sized for the widest day format) - and a
     fallback copy of those figures here would be a second literal of the same
     numbers with nothing keeping them equal, which is how a "cannot disagree"
     comment becomes false. One declaration, in the store.

     FIXED widths, not `auto`: grouping makes one list per group, so `auto`
     tracks would be measured against that group's contents alone and the
     columns would step sideways from one folder to the next - the alignment
     #891 exists to hold. */
  /* What the header strip stands, so the group headings can stick UNDER it
     rather than behind it. A fixed figure rather than a measured one: the
     strip is one line of --text-2xs and never wraps, and 32px is already the
     token for a horizontal band of exactly this kind. */
  --shelf-head-h: var(--rule-h-seam);
  /* Picking rows is the gesture on this panel, and the browser's text selection
     rode along with it: Shift+click extends a text range from the last click and
     a fast double click word-selects, so a multi-select arrived with the list
     highlighted through it (#932). On the PANEL rather than on the row, because
     a drag that starts a row-height too high - on a group heading, the band, the
     empty-folder note - paints the same text just as well. It also clears the
     way for double click to rename: the gesture that opens the field would
     otherwise word-select the name behind it. The desktop
     shell already gets this from `.is-desktop` in `style.css`; this is the same
     rule for the browser build, kept to the shelf rather than made global,
     which is a wider decision than this issue. The rename field opts back in
     below: it is the one place on the panel a name is edited. */
  -webkit-user-select: none;
  user-select: none;
}

.shelf-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  /* `shelfbar` for this bar's own ladder, and the shared `toolbar` name the
     app-wide chrome writes its scoped @container rules against - the same
     pair the grid bar (`selbar toolbar`) and the queue's (`dqbar toolbar`)
     declare, so a shared control mounted here degrades exactly as it does
     there. `shelfbar` carries this bar's own ladder (the rungs at the bottom
     of this block); `toolbar` is what a shared control mounted here would
     write its own rules against, and nothing on the bar does today -
     dropping UndoControl took the one control that did. `container-type:
     inline-size` is also what keeps the bar's width independent of its
     contents. */
  container-type: inline-size;
  container-name: shelfbar toolbar;
  /* The shell's top band, copied from the GRID toolbar
     (`.selection-bar-overlay` in Toolbar.vue), which is the point of truth for
     the box recipe: `height: 36px` + `box-sizing: border-box` (the 1px bottom
     border sits INSIDE the 36) + zero vertical padding, `align-items: center`
     doing the vertical work. This bar shipped at `--bar-height` (48px), so
     switching to /models moved the whole content area down 12px and back -
     which reads as a different app, not a different context. NOT
     `--bar-height`: unifying the shipped 34/36/40/48/56 onto that token is the
     separate, UI/UX-gated item in visual-language.md §5, and a bar that jumped
     there alone would be drift in the other direction. Guardrail:
     Toolbar.test.js asserts all three bars carry the same recipe. */
  height: 36px;
  box-sizing: border-box;
  /* Split inset, same as the queue's and for the same reason. RIGHT is
     --space-3, the grid bar's inset: the app-wide tail ([separator]
     [TbGlobalActions]) is a stable anchor only if its rail toggle lands at
     the identical distance from the edge in every view - a uniform --space-5
     here put them 8px further left than the grid's, so the pair jumped
     sideways on every view switch. LEFT stays --space-5, the shelf's own
     content gutter (`.shelf-group-btn` and the rows inset by --space-5), so
     the count sits flush over the list. */
  padding: 0 var(--space-3) 0 var(--space-5);
  /* Paint the chrome surface the other two bars paint. Unpainted, this strip
     showed `.shelf`'s `background` through it, which is a different hue and
     value from `toolbar` in both themes - the bar read as page, not chrome.
     `toolbar-text` is what `.bar-btn` already uses, so the count now
     inherits the same ink as the buttons beside it. */
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  flex-shrink: 0;
}

/* 0.6, the queue's `.qsub` alpha exactly. It was 0.7 on `on-background`, and
   re-basing it on `toolbar-text` at the old alpha would have invented a third
   strength for the one role in a change whose premise is that these bars
   agree. */
.shelf-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), 0.6);
  font-variant-numeric: tabular-nums;
}

.shelf-spacer {
  flex: 1 1 auto;
}

/* The toolbar separates the count from its controls at --space-4; the controls
   separate from each other at --space-3, which is the gap the grid bar uses.
   Without the cluster every child of .shelf-toolbar sat at the wider gap. */
.shelf-bar-cluster {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* ── The ladder ──────────────────────────────────────────────────────────────

   The bar has been browse-only since #2168: the count, Sort, Show
   (Filter), the sidebar's chips, the name field and the rail toggle. Add and
   Model folders moved to the Models sidebar, so the fold that hid them behind
   a ⋯ went with them. The rungs below were measured on the wider bar this one
   replaced, so they now fire later than they need to, never earlier; remeasure
   at `width: max-content` when a control is next added.

   **The queries are 24px under the widths the classes are named for**:
   `container-type: inline-size` queries the CONTENT box, and this bar's inset
   is `0 var(--space-3) 0 var(--space-5)` = 24px. */

/* The toolbar's name field. A fixed width, so typing never moves the bar. */
.shelf-name-bar {
  width: 180px;
  flex: none;
}

/* Rung 1. The one thing on the bar that REPORTS rather than controls: the
   count. It goes before the name field, a control. */
@container shelfbar (max-width: 1238px) {
  .shelf-fold-1262 {
    display: none;
  }
}

/* Rung 1b. The name field, a shortcut to the one at the top of Show. */
@container shelfbar (max-width: 1007px) {
  .shelf-name-bar {
    display: none;
  }
}

/* Rung 2. `Sort` drops its VALUE and keeps its glyph and chevron, the grid
   Filter trigger's compressed grammar. Never silently: the button carries it
   in `title` and in its accessible name at every width. */
@container shelfbar (max-width: 816px) {
  .bar-btn-value {
    display: none;
  }
}

/* The one filled button in the bar. It is the only control here with a result
   behind it - everything else changes what you are looking at - and that is
   what the fill says. Amber acts, olive selects: the key action's fill is
   `accent` with its `on-accent` label, and a filled control's hover darkens
   (`--hover-shade`) rather than taking the transparent buttons' wash.

   28px, not `.bar-btn`'s 32, and `--radius-sm` rather than the nothing it
   inherited. A filled control is the only kind whose box you can actually see:
   the boxed buttons beside it are transparent at rest, so their 32px shows as a
   hover wash and never as a silhouette, while this one's 32px in a 36px band
   (35 inside the bottom hairline) left 1.5px of clearance and read as a
   bar-height object rather than a control sitting in a bar. The radius was
   simply missing - `--boxed` is what carries `--radius-sm` and this never took
   it, so the one accent button in the app was also the one square-cornered one,
   at a radius (0) that is not on the scale (§6). */
.shelf-toolbar .bar-btn--accent {
  gap: var(--space-2);
  height: var(--control-h);
  border-radius: var(--radius-sm);
  border-color: rgb(var(--v-theme-accent));
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  font-weight: var(--weight-medium);
}

.shelf-toolbar .bar-btn--accent:hover:not(:disabled) {
  background-color: rgb(var(--v-theme-accent));
  background-image: var(--hover-shade);
  color: rgb(var(--v-theme-on-accent));
}

.shelf-toolbar .bar-btn--accent :deep(.v-icon) {
  color: rgb(var(--v-theme-on-accent));
}

/* Group and Sort carry their current VALUE as the label: their glyphs are
   abstract, and their state is the reason the list looks the way it does. It
   ellipsises rather than widening the bar, because a base-model name can be
   long and the tooltip carries the whole of it. */
.bar-btn-value {
  max-width: 12ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The panel below the toolbar: the veil's containing block and the `inert`
   wrapper, and a column so the column strip can sit above the scrollport
   rather than inside it. It does not scroll itself - `.shelf-scroll` does. */
.shelf-body {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.shelf-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* Reserved whether or not the list is long enough to need it, so the strip
     above - which reserves the same gutter - cannot shift sideways relative to
     the rows the moment a filter makes the list short. */
  scrollbar-gutter: stable;
  /* Room under the last row for the pill to float over nothing. Without it the
     bottom-most rows sit permanently behind it and cannot be read or clicked
     at the one moment they matter - while a selection exists. */
  padding-bottom: 56px;
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

/* ── The floating selection pill ───────────────────────────────────────────
   Bottom-centre over the list, the same object the photo grid docks over its
   tiles. The strip takes no pointer events so the rows it crosses stay
   clickable; the pill inside it takes them back. */

/* ── The view switcher ─────────────────────────────────────────────────────
   The shipped segmented track (`.tbm-seg`, `App.css`), sized for this bar per
   the design system's `Segmented`: zero gap between segments,
   `--control-h-sm` segments with a minimum width, and `--elevation-1` on the
   selected one as the cue beyond hue. `--space-1` of inset rather than the spec's `--space-2`, so the
   track is 30px and sits inside the 36px band with room for a focus ring.
   `tbm-seg-btn--on` fills in `primary` and carries no weight change, so
   selecting a segment never resizes the pair (the guardrail in
   `ModelShelf.test.js` holds `.shelf-viewseg--on` to that). */
.shelf-viewswitch {
  gap: 0;
  padding: var(--space-1);
}

.shelf-viewseg {
  flex: none;
  min-width: 56px;
}

.shelf-viewseg--on {
  box-shadow: var(--elevation-1);
}

/* Amber is the attention dot: a run is training or waiting for the GPU. */
.shelf-live-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-left: var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
  vertical-align: middle;
}

/* TrainingRunLive's back arrow, run name, status and Stop. */
.shelf-run-bar {
  display: flex;
  align-items: center;
  flex: 1 1 auto;
  min-width: 0;
}

/* The run bar takes the free width itself: a second spacer split it in two
   and left Stop half way along the toolbar. */
.shelf-run-bar ~ .shelf-spacer {
  display: none;
}

/* Raised so the focus ring is not clipped by the welded sibling. */
.shelf-viewseg:focus-visible {
  position: relative;
  z-index: var(--z-raised);
}

/* ── Banners ───────────────────────────────────────────────────────────────
   One line, one verb, dismissible. Nothing here is broken and nothing needs
   fixing - the models come back the moment the drive does - so it states the
   fact and stops, and deliberately never takes the error surface. */
/* The open folder's drive, under the list, on the toolbar's chrome: one of
   the grounds the drive meter's track is audited on (contrastAudit.js). */
.shelf-status {
  flex-shrink: 0;
  /* Pinned to the foot of the pane: an empty-folder or error state does not
     grow to fill it the way the list's scrollport does. */
  margin-top: auto;
  background: rgb(var(--v-theme-toolbar));
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h);
  padding: 0 var(--space-5);
  border-top: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-toolbar-text), var(--opacity-text-secondary));
}

.shelf-status-bar {
  display: flex;
  width: 120px;
  height: 4px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--drive-meter-track);
}

.shelf-status-bar > span {
  display: block;
  background: rgb(var(--v-theme-surface-success));
}

.shelf-status-bar--full > span {
  background: rgb(var(--v-theme-surface-warning));
}

.shelf-banner {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  background: rgba(var(--v-theme-primary), 0.09);
  font-size: var(--text-xs);
}

.shelf-banner-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shelf-banner-warn {
  color: rgb(var(--v-theme-surface-warning));
}

.shelf-banner b {
  font-weight: var(--weight-semibold);
}

/* ── The column strip ──────────────────────────────────────────────────────
   The same flex metrics as `.shelf-row`, restated rather than shared, because
   the row's padding and gap are what a heading has to line up with and a
   heading that computed its own would be one refactor from drifting. The
   transparent rail is the rows' absence rail: without it every heading would
   sit one rail-width left of the column it names.

   It is a SIBLING of the scrollport, not a sticky child of it, so it needs no
   sticky rung and nothing passes under it: the group headings stick to the top
   of `.shelf-scroll`, which begins below this strip. `overflow: hidden` is not
   about clipping - an element with a scrolling box honours `scrollbar-gutter`,
   which is what reserves the same strip of nothing the scrollport reserves and
   is the whole reason the columns still line up with the rows. */
.shelf-head {
  overflow: hidden;
  scrollbar-gutter: stable;
  display: flex;
  align-items: stretch;
  gap: var(--space-4);
  /* border-box, so the hairline is INSIDE the 32px the group headings offset
     themselves by. There is no universal reset in this app, so a content-box
     strip would stand 33px and leave a 1px sliver of row under it. */
  box-sizing: border-box;
  height: var(--shelf-head-h);
  /* The 4px top and bottom are the room a 3px focus ring needs to be INSIDE
     the strip: `overflow: hidden` is what makes the gutter work, and it clips
     a ring on a full-height cell against the strip's own edge. Everything in
     here is vertically centred, so the only thing 8px of height buys is the
     ring. */
  padding: var(--space-2) var(--space-4) var(--space-2) var(--space-6);
  border-left: var(--rail-w) solid transparent;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  background: rgb(var(--v-theme-background));
}

.shelf-head-ident {
  width: calc(var(--entity-thumb) + var(--space-4));
  flex: none;
}

/* The wrapper that carries the column's width and anchors its grip. Its own
   width classes rather than the rows' `.shelf-col--*`: those are the DATA
   cells, several selectors reach for them by name, and a heading answering to
   the same class is how `.shelf-col--base span` came to find a column name
   instead of a base model. The variable is what the two share. */
.shelf-head-col {
  position: relative;
  flex: none;
}

.shelf-head-col--label {
  flex: 1 1 auto;
  min-width: 0;
}

.shelf-head-col--kind {
  width: var(--shelf-col-kind);
}

.shelf-head-col--base {
  width: var(--shelf-col-base);
}

.shelf-head-col--size {
  width: var(--shelf-col-size);
}

.shelf-head-col--date {
  width: var(--shelf-col-date);
}

.shelf-head-col--sets {
  width: var(--shelf-col-sets);
}

/* The type, the weight, the tracking, the case and the 0.7 ink all come from
   the global `.section-label`, which is what a column name IS - §3 says use it
   rather than re-roll it, and its alpha in particular is a measured value that
   has been wrong here once already. Only the layout is local. */
.shelf-head-cell {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  height: 100%;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  cursor: pointer;
  transition: color var(--dur-1) var(--ease-standard);
}

button.shelf-head-cell:hover {
  color: rgb(var(--v-theme-on-surface));
}

/* The sorted column is named in full ink with an arrow beside it. Both halves
   on purpose: the arrow alone is a 14px glyph at the edge of a quiet strip,
   and the ink alone does not say which way. */
.shelf-head-cell--on {
  color: rgb(var(--v-theme-on-surface));
}

/* Always present, only sometimes visible - the same rule as the row's absence
   rail (§5.1): `v-if` would let the label shift 22px sideways at the instant
   the reader clicks it, and ellipsize its own heading at the narrow end. */
.shelf-head-arrow {
  visibility: hidden;
}

.shelf-head-cell--on .shelf-head-arrow {
  visibility: visible;
}

.shelf-head-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The grip sits on the column's LEFT edge, in the seam before it, because that
   is the only edge of a fixed column that MOVES when the column is resized.
   Name is the flexible track and the fixed columns are anchored to the strip's
   right edge, so a column's right edge is pinned by whatever follows it: a grip
   drawn there stands still while the whole left half of the strip slides under
   the pointer, which is what the reader reads as the drag going the wrong way.
   On the left edge the hairline tracks the pointer exactly. It also puts a seam
   between Name and Kind and none past the last column, which is the set of
   seams that actually exist.

   24px wide, WCAG 2.5.8's floor, centred on the 12px gap's midpoint rather
   than flush to the column: that keeps the grab area off the heading's label,
   which is left-aligned and starts at the column's own edge. 6px of each
   neighbour is all it takes, and the previous column's last 6px are dead
   space - the widest heading is `BASE` at ~30px in an 84px column. */
.shelf-head-grip {
  position: absolute;
  top: 0;
  bottom: 0;
  left: calc(var(--space-6) / -2 - var(--space-4) / 2);
  width: var(--space-6);
  border-radius: var(--radius-sm);
  cursor: col-resize;
  touch-action: none;
}

/* The hairline the reader actually sees, and the ONLY signal that a column can
   be resized - so it is a component-grade 0.4 rather than the `divider` token,
   which measures ~1.2:1 on this canvas and fails 1.4.11's 3:1. Same floor and
   the same reasoning as §11's scrollbar thumb.

   Full height of the grip rather than inset, because the strip's own 4px block
   padding is now the inset: the line stands the same 24px it always did. */
.shelf-head-grip::after {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 1px;
  background: rgba(var(--v-theme-on-background), 0.4);
  transition: background var(--dur-1) var(--ease-standard);
}

/* Colour only, never width: the hairline is `left: 50%` on the seam, so
   growing it would slide the line sideways under the pointer at the moment
   the reader is aiming at it. Full ink, not a hue: hover and a drag in
   progress are neither an action nor a selection. */
.shelf-head-grip:hover::after,
.shelf-head-grip--on::after,
.shelf-head-grip:focus-visible::after {
  background: rgb(var(--v-theme-on-background));
}

/* ── Folder headers ────────────────────────────────────────────────────────
   The header IS the button, so its whole width is the collapse control and the
   drop target; a second element would put a dead strip between the two. */
.shelf-group-heading {
  margin: 0;
  font: inherit;
}

/* Sticky inside the body's own scroller, the same band DuplicateQueue's
   `.mixed-head` ships: an OPAQUE `background` (rows pass underneath it), the
   named `--z-sticky` rung, and one hairline. No elevation: a shadow is for an
   object floating above a surface, and this band is part of the list.

   The 3px inset rail is the TIER, and it is a shadow rather than a border so a
   header that gains one does not move a pixel. */
.shelf-group-btn {
  position: sticky;
  /* 0, because the scrollport it sticks to now BEGINS under the column strip:
     the strip is a sibling above it rather than a sticky child of it, so there
     is nothing here to duck. */
  top: 0;
  z-index: var(--z-sticky);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3) var(--space-4) var(--space-3) var(--space-5);
  border: 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  text-align: left;
  font: inherit;
  cursor: pointer;
  box-shadow: inset 3px 0 0 var(--shelf-rail, transparent);
  transition: background var(--dur-1) var(--ease-standard);
}

.shelf-group-btn:hover {
  background: var(--hover-wash);
}

.shelf-group-chevron {
  flex: none;
  color: rgba(var(--v-theme-on-background), 0.7);
  transition: transform var(--dur-1) var(--ease-standard);
}

.shelf-group-chevron--open {
  transform: rotate(90deg);
}

.shelf-group-mark {
  flex: none;
  color: rgba(var(--v-theme-on-background), 0.7);
}

.shelf-group-label {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* A folder is a PATH, and §3 gives paths the mono face. A base model or a
   feature is a word and keeps the UI face. */
.shelf-group-label--path {
  font-family: var(--font-mono);
}

.shelf-group-count {
  flex: none;
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* ── Rows ──────────────────────────────────────────────────────────────────
   Flex rather than a grid, because the three data columns are fixed widths and
   the name takes the rest: a grid would have to be declared identically on the
   member rows and on the empty-folder row, and one of the three would drift. */
/* The mark at `--space-8`, about the row's own height, where #2168 drew a
   16px letter in a slot sized for 24. The headings
   resolve the same value, so their left edges stay over the names. */
.shelf-head,
.shelf-row {
  --entity-thumb: var(--space-8);
}

.shelf-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4) var(--space-3) var(--space-6);
  /* Always present, always transparent: only its colour and style change, so a
     row that flips into an absence state does not move a pixel (§5.1). */
  border-left: 3px solid transparent;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
  /* Native windowing: the browser skips layout and paint for rows outside the
     viewport, which is what 1,800 rows need and is two lines rather than a
     virtual scroller. The size hint is only the first guess - `auto` makes the
     browser remember each row's real height after it has painted once. */
  content-visibility: auto;
  contain-intrinsic-size: auto calc(var(--entity-thumb) + var(--space-6));
}

.shelf-row:hover {
  background: var(--hover-wash);
}

/* The global ink outline, drawn inside: a flush row in a scroller has no room
   for the gap. */
.shelf-row:focus-visible {
  outline-offset: calc(var(--focus-width) * -1);
}

/* A wash and an inset bar, not a border: a 1px outline on a selected row
   shifts every glyph in it by a pixel, and 200 selected rows would shimmer as
   the list scrolls. The bar is the greyscale half - the wash alone is a hue. */
.shelf-row--selected {
  background: var(--active-wash);
  box-shadow: var(--selection-edge);
}

/* Locked in place: every copy sits in a folder whose files do not leave it.
   The hatch layers UNDER the hover and selection washes rather than being
   replaced by them, so a locked row that is also selected reads as both. The
   hover rule comes last, as `.shelf-row:hover` already outranks selection. */
.shelf-row--locked {
  background: var(--lock-hatch);
}

.shelf-row--locked.shelf-row--selected {
  background:
    linear-gradient(var(--active-wash), var(--active-wash)),
    var(--lock-hatch);
}

.shelf-row--locked:hover {
  background:
    linear-gradient(var(--hover-wash), var(--hover-wash)),
    var(--lock-hatch);
}

/* Cut, waiting for a paste (#2204): the file manager's dim. */
.shelf-row--cut > * {
  opacity: var(--opacity-disabled);
}

/* ── The three kinds of absence ────────────────────────────────────────────
   BROKEN is a fault: the file was registered and is gone. It takes the error
   rail and the error glyph in front of the name.

   OFFLINE is not a fault: the drive is simply not plugged in, nothing is lost,
   and the models come back with it. It takes a DASHED rail and muted ink, and
   deliberately NEVER the error colour - the offline case is the common one for
   anyone keeping adapters on an external disk, and painting it as a failure is
   what trains a reader to ignore both.

   NOT DOWNLOADED is not a fault either: one of PixlStash's own declared engines
   that nothing has needed yet, which is the normal state of about half of them.
   It takes NO rail, muted ink and a download glyph (#926).

   They are told apart in GREYSCALE, which is what makes this a treatment and
   not a hue: solid rail, dashed rail, no rail, plus two different glyphs. */
.shelf-row--broken {
  border-left-color: rgb(var(--v-theme-surface-error));
}

.shelf-row--offline {
  border-left-style: dashed;
  border-left-color: rgba(var(--v-theme-on-background), 0.7);
}

/* Muted ink, not faded ink. 0.7 is the alpha the figure columns already carry
   and the one #836 measured as clearing contrast at this size; 0.6 does not.
   The NAME is what recedes, because on an offline row the row's own content is
   what is out of reach, where a broken row's name is still perfectly true and
   only its file is gone. */
.shelf-row--offline .shelf-row-name {
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* The identity slot: the mark plus 6px either side, which is how far its
   version pill hangs past the corner. */
.shelf-row-ident {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: calc(var(--entity-thumb) + var(--space-4));
  flex: none;
}

/* The box the deck ticks fill: the mark's own, so they peek out from behind
   it. The mark is positioned so it paints over them in DOM order, and the
   ticks take its corner rather than a picture tile's. */
.shelf-row-mark {
  position: relative;
  display: inline-flex;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
}

/* The lock badge: a disc in the row's own ground, cut out of the mark's
   top-start corner and hung as far past it as the version pill hangs past the
   other (half the slot's padding), so it reads as sitting ON the mark. The
   disc follows the row's wash so it never shows as a hole in a selection. */
.shelf-row-lock {
  position: absolute;
  top: calc(var(--space-4) / -2);
  left: calc(var(--space-4) / -2);
  z-index: var(--z-raised);
  display: flex;
  align-items: center;
  justify-content: center;
  width: var(--space-5);
  height: var(--space-5);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-background));
  box-shadow: 0 0 0 1px rgb(var(--v-theme-border));
  color: rgb(var(--v-theme-on-background));
}

.shelf-row--selected .shelf-row-lock {
  background:
    linear-gradient(var(--active-wash), var(--active-wash)),
    rgb(var(--v-theme-background));
}

.shelf-row:hover .shelf-row-lock {
  background:
    linear-gradient(var(--hover-wash), var(--hover-wash)),
    rgb(var(--v-theme-background));
}

.shelf-row-lock .v-icon {
  font-size: var(--text-xs);
}

/* A missing file's glyph, in the mark's place and at its size. */
.shelf-row-gone {
  position: relative;
  margin: auto;
  font-size: var(--entity-thumb);
}

.shelf-row-mark :deep(.stick) {
  border-radius: var(--radius-sm);
}

/* The cover chip is the grid's (App.css), but a row's mark is not a tile: it
   sits on the slot's own corner rather than inset from it, and straddles the
   mark's bottom edge so the top of the face still shows. It drops the grid's
   tracking, takes the hairline padding and starts in the row's own left
   padding, so it covers the mark's bottom-start corner and leaves the version
   pill on the other one. The row's bottom padding (8px) is
   what keeps the 4px overhang out of `content-visibility`'s paint clip.
   Not hoverable, like the grid's: it overlaps the mark, whose own tooltip names
   the assignments, and the chip's tip still describes it to assistive tech. */
.shelf-row-ident .stack-cover-flag {
  left: calc(var(--space-2) * -1);
  bottom: calc(var(--space-2) * -1);
  padding: 0 var(--space-1);
  letter-spacing: normal;
}

.shelf-row-label {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  flex: 1 1 auto;
  min-width: 0;
}

/* The filename takes the whole of the second line. It is what the file is
   actually called - which the name above it may well not be - and it is the
   string that gets pasted into a ComfyUI node, so §3's mono face rather than a
   tooltip. */
.shelf-row-file {
  flex: 0 0 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* A misplaced copy's quiet line (#2205), in the UI face after the filename. */
.shelf-row-nolist {
  margin-left: var(--space-3);
  font-family: var(--font-ui);
  color: rgb(var(--v-theme-surface-warning));
}

.shelf-row-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* Reserved on every state, so the rule appearing under the pointer cannot
     shift the row's baseline by a pixel as the reader scans down it. */
  border-bottom: 1px dashed transparent;
}

/* The editable rule, on hover AND on the tab stop. Hover alone is the defect:
   a keyboard reader would have no sign the name is a field at all, and
   `:focus-within` on the row covers both the row itself and the field it
   opens. Not on `--needs-a-name`, which carries its own accent rule always and
   must not be quieted down to this one while the pointer is over it. */
.shelf-row:hover .shelf-row-name:not(.shelf-row-name--needs-a-name),
.shelf-row:focus-within .shelf-row-name:not(.shelf-row-name--needs-a-name) {
  border-bottom-color: rgba(var(--v-theme-on-background), 0.45);
}

/* An EMPTY FIELD inviting a name, never disabled-looking text - the one
   distinction #897 says decides whether these rows ever get fixed. So: full
   ink and an accent rule that is always there. Italic because rank is style
   and not another step down in contrast. */
.shelf-row-name--needs-a-name {
  font-style: italic;
  font-weight: var(--weight-regular);
  border-bottom-color: rgb(var(--v-theme-accent));
}

/* A readable name we generated. The UI face, because this string is OURS and
   is not in the file - mono would claim it were. Regular weight, so it does
   not carry the authority of a title somebody chose, and the accent rule under
   it says the rest. */
.shelf-row-name--derived {
  font-weight: var(--weight-regular);
  border-bottom-color: rgba(var(--v-theme-accent), 0.7);
}

/* The file's own string, shown because nothing survived the strip. Mono at
   regular weight, at FULL strength: §3 gives the mono face to file paths, and
   this IS one - so the face says what the string is rather than demoting it.
   Rank is never opacity (§5.1), and 37% of rows faded would be a column of
   ghosts. */
.shelf-row-name--from-file {
  font-family: var(--font-mono);
  font-weight: var(--weight-regular);
}

/* The "this is the file's own string" tag. A shape with a word in it, so it
   survives greyscale (§4): the accent is a hint, never the thing carrying the
   meaning. */
.shelf-name-tag {
  flex: none;
  padding: 0 var(--space-2);
  border: 1px solid rgba(var(--v-theme-accent), 0.6);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-accent), 0.14);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  white-space: nowrap;
  /* The surface's own ink on a 14% wash, NOT `on-accent`: that pairing is for a
     solid fill and measures near-invisible over a tint (§4, §11). */
  color: rgb(var(--v-theme-on-background));
}

/* Editing: a real bordered field, in the app's one focus language (§11). Sized
   to the text it replaces so committing does not jump the row. Selectable
   against the panel's `user-select: none`, or the one place a name is genuinely
   edited would be a field whose text cannot be dragged over or double-clicked. */
/* One inline field, two columns. Two class names and not one, because
   `startRename` finds its field with a first-match `querySelector` and a shared
   class would let it land on whichever of the two was drawn first. The base
   field is `BaseModelInput`, whose input is not a root this file's scope
   reaches, hence `:deep()` (see that component's docstring). */
.shelf-row-rename,
.shelf-col--base :deep(.shelf-row-base-edit) {
  min-width: 0;
  -webkit-user-select: text;
  user-select: text;
  flex: 1 1 auto;
  padding: 0 var(--space-2);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-background));
  background: rgba(var(--v-theme-on-background), 0.06);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
}

/* The absence glyph leads the name line, because it changes what everything
   after it means: the name is still true, the file behind it is out of reach.
   A file that is gone draws it in the mark's place instead. */
.shelf-row-loc {
  flex: none;
}

.shelf-row-loc--missing,
.shelf-row-loc--forgotten {
  color: rgb(var(--v-theme-surface-error));
}

/* Muted, never the error colour, and 0.7 like every other muted figure on this
   screen - nothing is wrong with a row that simply has not been fetched. */
.shelf-row-loc--unreachable,
.shelf-row-loc--not_downloaded {
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* One outlined chip vocabulary for every short qualifier on a row: the kind,
   the training step, an unset base. Outlined and not filled, because the
   filled count pill is reserved for picture counts and these are words. */
.shelf-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  flex: none;
  max-width: 100%;
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* The precision, in the app's mono face because it is a machine word read
   character by character (`Q4_K_M`, `FP8`) rather than a phrase - the same
   reason the filename line under it is monospaced. Nothing else changes: it is
   the row's own chip, at the row's own size and border. */
.shelf-chip--quant {
  font-family: var(--font-mono);
}

/* The trigger word is a phrase somebody typed, so it is the one chip with no
   natural length. Capped, and a block so the cap ends in an ellipsis: every
   chip is `flex: none` and the name beside it is what gives way. */
.shelf-chip--trigger {
  display: inline-block;
  max-width: 18ch;
}

.shelf-chip--trigger:not(.shelf-chip--none) {
  font-family: var(--font-mono);
}

/* Not set is a DASHED chip, not a blank cell: a blank under a column that
   promises something reads as a rendering gap rather than as a state, and the
   dash is the greyscale half of "there is nothing here yet". */
.shelf-chip--none {
  border-style: dashed;
  font-style: italic;
}

/* The run's file count, and the control that opens it. A pill because it is a
   count, and a real button because Right/Left on the row is the keyboard path
   and a pointer needs one too. */
.shelf-stack-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  flex: none;
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.shelf-stack-badge:hover {
  background-image: var(--hover-neutral);
}

.shelf-stack-badge .v-icon {
  transition: transform var(--dur-1) var(--ease-standard);
}

.shelf-stack-chevron--open {
  transform: rotate(90deg);
}

/* What the last scan added, in the success treatment: `success` as a
   foreground and a border on the canvas, which is the tier it is for (§4) and
   measures 4.87:1 light / 5.96:1 dark. A word rather than a dot: the shelf is
   a list of 1,800 rows and a dot beside one name says nothing about what is
   different about it. */
.shelf-row-new {
  flex: none;
  padding: 0 var(--space-2);
  border: 1px solid rgba(var(--v-theme-surface-success), 0.5);
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgb(var(--v-theme-surface-success));
}

.shelf-col {
  flex: none;
  font-size: var(--text-xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-background), 0.7);
}

.shelf-col--kind {
  width: var(--shelf-col-kind);
}

.shelf-col--base {
  width: var(--shelf-col-base);
}

/* A guessed value carries its tag UNDER the label, as the name carries its
   filename, so the tag never takes the label's width. */
.shelf-col--base:has(.shelf-base-guess) {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

/* A column item is as wide as its text unless held, and held is what lets
   `.shelf-base-text` ellipsise rather than clip at the column edge. */
.shelf-col--base:has(.shelf-base-guess) .shelf-base-text {
  max-width: 100%;
}

/* Smaller than the name column's tag: it is a note on the value, not a label
   of its own, so it drops the caps and the tracking. */
.shelf-base-guess {
  display: inline-flex;
  align-items: center;
  font-family: inherit;
  font-weight: var(--weight-medium);
  line-height: var(--leading-snug);
  letter-spacing: normal;
  text-transform: none;
  cursor: pointer;
}

.shelf-base-guess-confirm {
  display: none;
  align-items: center;
  gap: var(--space-2);
}

.shelf-base-guess:hover {
  border-color: rgb(var(--v-theme-accent));
}

.shelf-base-guess:hover .shelf-base-guess-word,
.shelf-base-guess:focus-visible .shelf-base-guess-word {
  display: none;
}

.shelf-base-guess:hover .shelf-base-guess-confirm,
.shelf-base-guess:focus-visible .shelf-base-guess-confirm {
  display: inline-flex;
}

.shelf-base-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The field fills the cell it replaces rather than widening the row: every
   other column is fixed, so a field that sized itself would shift the whole
   grid the moment somebody double-clicked one row. Regular weight, because the
   Base column is not the row's title and the name field's semibold would make
   it read as one. */
.shelf-col--base :deep(.shelf-row-base-edit) {
  width: 100%;
  font-weight: var(--weight-regular);
}

/* Right-aligned and tabular, which is what makes a column of sizes scannable:
   the reader is comparing magnitudes, and a ragged right edge is what stops
   them being able to. */
.shelf-col--size {
  width: var(--shelf-col-size);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* Tabular but LEFT-aligned, unlike the size beside it: every day in a column is
   the same width in the same format, so the digits line up either way, and Size
   stays the one right-aligned track - which is what keeps a magnitude readable
   as a magnitude rather than as one more figure in a row of them. */
.shelf-col--date {
  width: var(--shelf-col-date);
  font-variant-numeric: tabular-nums;
}

/* The count of sets a model is in: a magnitude like Size, so it takes the
   same right-aligned tabular figures. */
.shelf-col--sets {
  width: var(--shelf-col-sets);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* The favourite heart after the name: an indicator, not a control. Toggling it
   is the F key, the pill and the row menu. */
.shelf-row-heart {
  flex: none;
  margin-inline-start: var(--space-2);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.shelf-row--broken .shelf-row-name,
.shelf-row--broken .shelf-col {
  opacity: 0.75;
}

/* A run's other steps. Indented past the identity column so the arrow reads as
   belonging to the row above it, and quieter than a cover because it is one
   file of a run rather than the run - but a row like any other: it is picked,
   focused and right-clicked, so it takes the row cursor and the row's own
   selected treatment. */
.shelf-row--member {
  padding-left: var(--space-7);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The run's loss chart: a full-width band, not a grid cell, so the chart takes
   the row's width under the cover and its members. */
.shelf-run-loss-row {
  display: block;
  padding: var(--space-3) var(--space-4) var(--space-3) var(--space-7);
}

.shelf-row--member .shelf-row-name {
  font-weight: var(--weight-regular);
}

/* ── The busy state, scoped to the panel that is busy (#900) ─────────────── */
.shelf-progress {
  position: absolute;
  right: var(--space-4);
  bottom: var(--space-4);
  z-index: var(--z-overlay);
}

.shelf-progress :deep(.progress-overlay) {
  position: static;
}

/* The visible half of `inert`. A veil over the LIST, never over the app: the
   toolbar keeps answering while files are in flight.

   ABOVE everything sticky in this scroller, including the column strip and the
   group headings. Both are opaque, so at or below the veil's rung they stay at
   full brightness over a dimmed list and read as usable when they are not -
   which is the failure the veil exists to prevent. The strip in particular is
   pinned and spans the width, so it is the one that makes the veil look like a
   bug. */
.shelf-dim {
  position: absolute;
  inset: 0;
  z-index: var(--z-floating);
  background: rgba(var(--v-theme-background), 0.55);
}
</style>
