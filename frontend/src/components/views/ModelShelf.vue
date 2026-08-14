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
      Every adapter and checkpoint PixlStash has found on this machine. Group
      and Sort choose the order and whether the list is cut into groups; Show
      chooses which kinds are listed and which base models. A ring around a
      model's mark says who it is assigned to. A name in italics has not been
      given one. A row that stands for a training run says how many files it
      holds; Right and Left open and close it. Right-click a row for everything
      that can be done to it. Escape clears the selection.
    </p>

    <!-- One announcement for a resort, because the rows reorder silently: the
         two buttons' own names change, but a reader who is not on them hears
         nothing. Group collapse gets none, because `aria-expanded` on the
         header already says it and a second announcer double-speaks. -->
    <p class="visually-hidden" role="status">{{ sortAnnouncement }}</p>

    <!-- The toolbar changes the VIEW. The two things on it that are not view
         controls are the ones with no selection to hang on — Add, which makes a
         row that does not exist yet, and the stack sweep, which proposes over
         the whole shelf — so they sit together on the left, apart from the view
         controls, and both open something before they write anything. Every
         other verb lives on the row or in the selection pill (#904). -->
    <div class="shelf-toolbar">
      <span class="shelf-title">Models</span>
      <span class="shelf-sub">{{ countLabel }}</span>

      <!-- The one accented, labelled button in the bar, because it is the only
           thing here with a result behind it. Three ways in, one menu: a
           folder, a loose file, or a training run somebody else's tool wrote. -->
      <v-menu
        v-model="addMenuOpen"
        location="bottom start"
        origin="top start"
        :offset="8"
        transition="scale-transition"
      >
        <template #activator="{ props: menuProps }">
          <button
            ref="addBtnRef"
            v-bind="menuProps"
            class="bar-btn bar-btn--accent"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="addMenuOpen"
            :aria-busy="adding || undefined"
            title="Add models to the shelf"
          >
            <!-- A copy of a 6 GB checkpoint is not instant, and this button is
                 the only thing on screen that knows one is running. -->
            <v-icon v-if="adding" size="19" class="mdi-spin"
              >mdi-loading</v-icon
            >
            <v-icon v-else size="19">mdi-plus</v-icon>
            <span>Add</span>
            <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
          </button>
        </template>
        <div class="shelf-menu" role="menu">
          <button
            class="shelf-mi"
            type="button"
            role="menuitem"
            @click="openFolders()"
          >
            <v-icon size="16">mdi-folder-plus-outline</v-icon>
            <span>Add folder…</span>
          </button>
          <button
            class="shelf-mi"
            type="button"
            role="menuitem"
            @click="openAddFile"
          >
            <v-icon size="16">mdi-file-plus-outline</v-icon>
            <span>Add file…</span>
          </button>
          <!-- Shown only once an ai-toolkit output root is registered. Hidden
               rather than disabled, unlike the selection pill's verbs: those
               are about a selection the reader just made and owe an
               explanation, and this is about a folder they have not set up,
               which the folders dialog is the place to say. -->
          <template v-if="hasSourceFolder">
            <span class="shelf-mi-sep"></span>
            <button
              class="shelf-mi"
              type="button"
              role="menuitem"
              @click="openImport"
            >
              <v-icon size="16">mdi-import</v-icon>
              <span>Import from ai-toolkit</span>
            </button>
          </template>
        </div>
      </v-menu>

      <!-- The sweep, and the shelf's one verb with no selection to act on: it
           proposes over every row. Icon-only beside Add rather than among the
           view controls, because it is a verb and they are not — and it opens a
           dry run, so nothing is written by the press itself. -->
      <button
        ref="stacksBtnRef"
        class="bar-btn bar-btn--boxed"
        :class="{ 'bar-btn--open': stacksOpen }"
        type="button"
        title="Stack training runs — review proposed stacks"
        aria-label="Stack training runs"
        @click="stacksOpen = true"
      >
        <v-icon size="19">mdi-layers-plus</v-icon>
      </button>

      <span class="shelf-spacer"></span>

      <!-- The bar's own cluster gap. `.shelf-toolbar` separates the title from
           its controls at --space-4; the controls separate from each other at
           --space-3, which is what every other bar in the app uses. -->
      <div class="shelf-bar-cluster">
        <!-- Group and Sort carry their current VALUE as the label, because
             their glyphs are abstract and their state is the reason the list
             looks the way it does. Filter keeps the universal funnel and says
             the rest with a count. -->
        <v-menu
          v-model="groupMenuOpen"
          :close-on-content-click="false"
          location="bottom end"
          origin="top end"
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--boxed"
              :class="{ 'bar-btn--open': groupMenuOpen }"
              type="button"
              aria-haspopup="dialog"
              :aria-expanded="groupMenuOpen"
              :title="groupButtonTitle"
            >
              <v-icon size="19">{{ activeGroup.icon }}</v-icon>
              <span class="bar-btn-value">{{ activeGroup.label }}</span>
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <ShelfSortPanel section="group" />
        </v-menu>

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
                <v-icon size="19">{{ activeSort.icon }}</v-icon>
                <span class="bar-btn-value">{{ activeSort.label }}</span>
                <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
              </button>
            </div>
          </template>
          <ShelfSortPanel section="sort" />
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
             of that is hand-rolled.

             The badge counts ACTIVE FILTERS, never results: it is the answer to
             "why is this list short", and the result counts are already on the
             group headers. -->
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="bar-btn bar-btn--boxed"
              :class="{
                'bar-btn--active': store.activeCount > 0 && !showMenuOpen,
                'bar-btn--open': showMenuOpen,
              }"
              type="button"
              :title="showButtonTitle"
            >
              <span class="bar-icon-badge-wrap">
                <v-icon size="19">mdi-filter-outline</v-icon>
                <span v-if="store.activeCount > 0" class="bar-filter-badge">{{
                  store.activeCount
                }}</span>
              </span>
              <v-icon size="18" class="bar-btn-chevron">mdi-menu-down</v-icon>
            </button>
          </template>
          <ShelfShowPanel />
        </v-menu>
      </div>
    </div>

    <!-- The file picker the Set icon verb drives. A real <input type=file>
         rather than a drop zone or a dialog: it is the platform's own chooser,
         it is keyboard-accessible for free, and picking a file is the whole
         interaction. Hidden rather than styled, because the verb beside it is
         already the affordance. -->
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
      <!-- An unplugged drive states its scope ONCE, here, rather than through
           300 rows each carrying the same mark. The rows still take the offline
           treatment — that is what tells one row from its neighbour — but the
           REASON is a fact about the mount and belongs to the mount.

           One line, one verb, dismissible: nothing here is broken and nothing
           needs fixing, so the reader who has read it once gets to put it
           away. It comes back when the shelf is refetched, because a drive that
           is still unplugged is still worth saying once. -->
      <p
        v-if="offlineNote && !offlineDismissed"
        class="shelf-banner"
        :title="offlineMountPaths"
      >
        <v-icon size="16">mdi-power-plug-off-outline</v-icon>
        <span class="shelf-banner-text">{{ offlineNote }}</span>
        <span class="shelf-spacer"></span>
        <button
          class="shelf-banner-dismiss"
          type="button"
          title="Dismiss"
          aria-label="Dismiss the offline notice"
          @click="offlineDismissed = true"
        >
          <v-icon size="15">mdi-close</v-icon>
        </button>
      </p>
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
          @click="openFolders()"
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

      <!-- The row itself is not a focus stop unless it holds the roving one:
           1,800 tab stops would be a trap, and a row's verbs are on its context
           menu rather than inside it. Group headers stay stops too, so Tab
           still moves group to group. -->
      <template v-else>
        <!-- The key to the meters, said ONCE for the view rather than once per
             band: it is the same three segments every time, and repeating it
             down the list would cost more room than the meters themselves.
             Drawn only when a measured meter is actually on screen — an
             unmeasured band renders no meter at all, so a shelf of offline
             drives would otherwise key a picture nobody can see.

             No ARIA and no `aria-describedby` back from the bands: each
             heading already states its figures in words, so this is redundant
             to a screen reader, and wiring it up would re-read all three
             labels on every heading. -->
        <p v-if="showsBandLegend" class="shelf-keys">
          <span v-for="item in BAND_LEGEND" :key="item.key" class="shelf-key">
            <span
              class="shelf-key-swatch"
              :class="`shelf-band-seg--${item.key}`"
            ></span>
            <span>{{ item.label }}</span>
          </span>
          <span class="shelf-key">
            <v-icon size="14">mdi-cursor-move</v-icon>
            <span>drag a selection onto a drive or folder to move it</span>
          </span>
        </p>
        <div v-for="group in shownGroups" :key="group.key" class="shelf-group">
          <!-- The drive band: the OUTER of the two levels the plan allows, and
               the second one is spent here rather than on stacks, which nest
               inside a row and not inside a header. Drawn on the first group of
               each band, never as a wrapper element, so the sticky folder
               headers below keep scrolling under it in one flow. -->
          <!-- And the drop target for a move (#894). `dragover` carries no
               `.prevent` here either — calling preventDefault() is what ACCEPTS
               a drop, so a band with no room simply never calls it and the
               browser draws its own refusal cursor over a band already in the
               error treatment. -->
          <h3
            v-if="group.bandStart"
            class="shelf-band"
            :class="{
              'shelf-band--unknown': !group.band.measured,
              'shelf-band--drop': bandDropState(group.band) === 'drop',
              'shelf-band--reject': bandDropState(group.band) === 'reject',
            }"
            @dragover="onBandDragOver(group.band, $event)"
            @dragleave="onBandDragLeave(group.band)"
            @drop="onBandDrop(group.band, $event)"
          >
            <v-icon size="15" class="shelf-band-icon">mdi-harddisk</v-icon>
            <span class="shelf-band-name">{{ group.band.label }}</span>
            <span v-if="group.band.mountPoint" class="shelf-band-path">{{
              group.band.mountPoint
            }}</span>
            <!-- Three segments carving up one track, not fills stacked on top
                 of each other. The shelf's share is a PART of what is used, so
                 `other` is the REST of the used space: laid end to end the
                 three are the drive, and no boundary is ambiguous. Overlaying
                 them was the original shape and it meant a reader could see a
                 boundary without being able to tell which of the two questions
                 — "how full is this disk" and "how much of that is us" — it
                 answered (#893).

                 `aria-hidden`, and no `role="img"`: `.shelf-band-figures`
                 below already renders the identical string as visible text in
                 this same heading, so labelling the meter made every band
                 announce its figures twice. `role="meter"` would be worse —
                 it carries one `aria-valuenow` and this is three numbers. -->
            <span
              v-if="usage(group.band)"
              class="shelf-band-meter"
              :class="{ 'shelf-band-meter--low': usage(group.band).lowFree }"
              aria-hidden="true"
            >
              <span
                class="shelf-band-seg shelf-band-seg--shelf"
                :style="{ width: `${meter(group.band).shelfPct}%` }"
              ></span>
              <span
                class="shelf-band-seg shelf-band-seg--other"
                :style="{ width: `${meter(group.band).otherPct}%` }"
              ></span>
              <!-- The ghost: what a drop would add, carved out of the free
                   segment rather than laid over it, so the four still sum to
                   the drive. Hatched, never a solid, because a projection is
                   provisional and a measurement is not — and the two must not
                   be one reading apart. -->
              <span
                v-if="projection(group.band)"
                class="shelf-band-seg shelf-band-seg--ghost"
                :class="{
                  'shelf-band-seg--ghost-reject': !projection(group.band).fits,
                }"
                :style="{ width: `${projection(group.band).addedPct}%` }"
              ></span>
              <span
                class="shelf-band-seg shelf-band-seg--free"
                :style="{ width: `${meter(group.band).freePct}%` }"
              ></span>
            </span>
            <span
              class="shelf-band-figures"
              :class="{
                'shelf-band-figures--low':
                  !projection(group.band) && usage(group.band)?.lowFree,
                'shelf-band-figures--reject':
                  bandDropState(group.band) === 'reject',
              }"
            >
              <!-- The non-colour half of the low and reject states. Colour is
                   additive here, never the carrier: the distinction has to
                   survive greyscale, and the glyph and the words ("Only", "will
                   not fit") both do. A drop that fits gets the tray glyph
                   rather than none, so the label under a hatched meter is
                   marked as being about the drag and not about the disk. -->
              <v-icon v-if="projection(group.band)" size="16">{{
                projection(group.band).fits
                  ? "mdi-tray-arrow-down"
                  : "mdi-alert-circle-outline"
              }}</v-icon>
              <v-icon v-else-if="usage(group.band)?.lowFree" size="16"
                >mdi-alert-outline</v-icon
              >
              <span>{{ meterLabel(group.band) }}</span>
            </span>
          </h3>

          <!-- The header IS the button, so its whole width is the drop target
               and the collapse control. A heading as well as a button, so a
               screen reader can jump group to group by heading. -->
          <h3 v-if="grouped" class="shelf-group-heading">
            <!-- A folder header is also the drop target for a drag, which is
                 why the drag handlers sit on the button and not on a wrapper:
                 the button already spans the header's full width, and a second
                 element would put a dead strip between the two. `dragover`
                 does NOT carry `.prevent` — calling preventDefault() is what
                 ACCEPTS a drop, so it happens inside the handler and only for
                 a payload this target takes (#757). -->
            <button
              class="shelf-group-btn"
              :class="[
                `shelf-group-btn--${group.tier || 'plain'}`,
                {
                  'shelf-group-btn--drop':
                    dropTargetKey === group.key && dropFits(group.band),
                  'shelf-group-btn--offline': group.offline,
                  'shelf-group-btn--nested': group.nested,
                },
              ]"
              :style="groupStyle(group)"
              type="button"
              :aria-expanded="!store.isCollapsed(group.key)"
              :aria-label="groupLabel(group)"
              @click="store.toggleGroup(group.key)"
              @dragover="onGroupDragOver(group, $event)"
              @dragleave="onGroupDragLeave(group)"
              @drop="onGroupDrop(group, $event)"
            >
              <v-icon
                size="16"
                class="shelf-group-chevron"
                :class="{
                  'shelf-group-chevron--open': !store.isCollapsed(group.key),
                }"
                >mdi-chevron-right</v-icon
              >
              <!-- Under `Folder` the glyph is the TIER's — one mdi folder
                   family, never a hand-drawn box — and an unreachable folder
                   wears the disconnected mark instead, which is the shape half
                   of the offline treatment. -->
              <v-icon size="16" class="shelf-group-mark">{{
                group.icon || GROUP_BY_LABELS[store.view.groupBy].icon
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
              <span v-if="group.chip" class="shelf-chip">{{ group.chip }}</span>
              <!-- Only where no band names the drive already: under `Drive,
                   then folder` the band above IS this chip, and repeating it on
                   every folder under it is noise rather than a signal. -->
              <span v-if="!group.band && group.drive" class="shelf-chip">
                <v-icon size="12">mdi-harddisk</v-icon>
                {{ group.drive.label }}
              </span>
              <span v-if="group.offline" class="shelf-chip">Offline</span>
              <span class="shelf-spacer"></span>
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
                 grouping needs one strip per group — and the resolved design
                 has no visible header strip, because the kind is a chip, the
                 base is a word and the size is right-aligned, which is what
                 makes the columns readable without being named. The names stay
                 for the reader who cannot see that. -->
            <li class="visually-hidden" role="row">
              <span role="columnheader">Model</span>
              <span role="columnheader">Name</span>
              <span role="columnheader">Kind</span>
              <span role="columnheader">Base</span>
              <span role="columnheader">Size</span>
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
                class="shelf-row"
                :class="{
                  'shelf-row--selected': store.isSelected(row.id),
                  'shelf-row--offline': row.locState === 'unreachable',
                  'shelf-row--broken': BROKEN_STATES.has(row.locState),
                }"
                role="row"
                aria-level="1"
                :aria-expanded="
                  row.memberCount > 1 ? isStackOpen(row.stack_id) : undefined
                "
                :aria-selected="store.isSelected(row.id)"
                aria-keyshortcuts="F2"
                :tabindex="row.rowKey === rovingRowKey ? 0 : -1"
                :data-row-key="row.rowKey"
                :draggable="canDrag(row) && editingRowKey !== row.rowKey"
                @click="pickRow(row, $event)"
                @contextmenu.prevent="openRowMenu(row, $event)"
                @keydown="onRowKeydown(row, $event)"
                @focus="focusedRowKey = row.rowKey"
                @dragstart="onRowDragStart(row, $event)"
                @dragend="clearDropState()"
              >
                <!-- The identity slot, and the assignment (#904). The RING is
                     the assignment: its hue is the entity's own and its style
                     is hashed off the entity, so the pair survives greyscale
                     where the hue alone would not, and the mark's own label
                     names every attachment out loud. That is what replaced the
                     `Assigned to` column — one mark, two axes, no track that is
                     empty on most rows. -->
                <span role="gridcell" class="shelf-row-ident">
                  <!-- Deck ticks behind the mark say "this is more than one
                       file" before the count is read, exactly as they do on a
                       picture tile. Count-only, so the component reuses cleanly
                       here even though a model has no thumbnail. -->
                  <StackEdgeTicks
                    v-if="row.memberCount > 1"
                    :count="row.memberCount"
                  />
                  <!-- The hue is bound as a custom property rather than a
                       class, because it is per-entity DATA and there is no
                       bounded set of them to name. Bound only when there IS
                       one: an unassigned ring has no hue, and a custom
                       property set to an empty string is a different thing
                       from an unset one — `var(--mmark-ring, transparent)`
                       would resolve to nothing rather than to its fallback,
                       and an invalid-at-computed-value-time `border` takes the
                       whole shorthand down with it, including the 2px. -->
                  <ModelMark
                    :row="row"
                    :ring="ringFor(row)"
                    :style="ringStyle(row)"
                  />
                </span>
                <span role="gridcell" class="shelf-row-label">
                  <!-- The absence glyph leads the line, because it changes what
                       everything after it means: the name is still true, the
                       file behind it is not there. -->
                  <v-icon
                    v-if="row.locState !== 'present'"
                    size="14"
                    class="shelf-row-loc"
                    :class="`shelf-row-loc--${row.locState}`"
                    :title="LOC_TITLE[row.locState]"
                    >{{ LOC_ICON[row.locState] }}</v-icon
                  >
                  <!-- The name is a FIELD, and it has four states, because
                       naming is the commonest fix on this shelf and the reader
                       has to be able to tell "somebody chose this" from "we
                       guessed" from "there is nothing here" without opening
                       anything. Rendering all four as one string is what made
                       an unnamed row look inert, and an inert row never gets
                       named (#897). The tag beside the name is the carrier;
                       the type and the accent are hints on top of it, so the
                       distinction survives greyscale. -->
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
                    <span
                      v-if="NAME_TAG[row.name.state]"
                      class="shelf-name-tag"
                      :class="`shelf-name-tag--${row.name.state}`"
                      :title="NAME_TAG[row.name.state].title"
                      >{{ NAME_TAG[row.name.state].label }}</span
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
                    :title="`${row.memberCount} files in this run`"
                    @click.stop="toggleStack(row.stack_id)"
                  >
                    {{ row.memberCount }}
                    <v-icon
                      size="13"
                      :class="{
                        'shelf-stack-chevron--open': isStackOpen(row.stack_id),
                      }"
                      >mdi-chevron-right</v-icon
                    >
                  </button>
                  <!-- The step, on any row that is not a stack cover.
                       `deriveModelName` strips the trailing step from the
                       filename on the stated grounds that "the step is parsed
                       into its own field" — and that field was never rendered
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
                  <!-- What the scan you just ran brought in. The SUCCESS
                       treatment, because an arrival is a good outcome — and
                       nothing else on a row is green, so it reads without a
                       key. Cleared by the next fetch, so it is never a stale
                       mark from three refreshes ago. -->
                  <span v-if="row.isNew" class="shelf-row-new">New</span>
                  <!-- The filename, on its own line under the name. It is what
                       the file is actually called, which the name above it may
                       well not be, and it is the string the reader pastes into
                       a ComfyUI node — so it is monospaced and it is always
                       there rather than living in a tooltip. -->
                  <span class="shelf-row-file">
                    {{ row.filename
                    }}<template v-if="LOC_NOTE[row.locState]">
                      · {{ LOC_NOTE[row.locState] }}</template
                    >
                  </span>
                </span>
                <span role="gridcell" class="shelf-col shelf-col--kind">
                  <span class="shelf-chip" :title="kindLabel(row)">{{
                    kindLabel(row)
                  }}</span>
                </span>
                <!-- Base is a COLUMN, not a phrase on a metadata line: it is
                     the field a reader scans a shelf for, and it can only be
                     scanned if it aligns. -->
                <span role="gridcell" class="shelf-col shelf-col--base">
                  <span v-if="row.base_model">{{ row.base_model }}</span>
                  <span v-else class="shelf-chip shelf-chip--none"
                    >not set</span
                  >
                </span>
                <span role="gridcell" class="shelf-col shelf-col--size">{{
                  row.file_size ? formatModelSize(row.file_size) : ""
                }}</span>
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
                  class="shelf-row shelf-row--member"
                  role="row"
                  aria-level="2"
                >
                  <span role="gridcell" class="shelf-row-ident">
                    <v-icon size="14">mdi-subdirectory-arrow-right</v-icon>
                  </span>
                  <span role="gridcell" class="shelf-row-label">
                    <span class="shelf-row-name">{{
                      memberLabel(member)
                    }}</span>
                    <span class="shelf-row-file">{{ member.filename }}</span>
                  </span>
                  <!-- A step of a run has no kind or base of its own — those
                       are the run's, one row up — but a grid row still owes a
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
                </li>
              </template>
            </template>
          </ul>
        </div>
      </template>
    </div>

    <!-- The pill floats bottom-centre OVER the list, exactly like the photo
         grid's: the list is what the selection was made in, and a docked strip
         between the toolbar and the rows pushed the whole list down every time
         a row was clicked. This wrapper is the float; the pill owns its own
         shape. `pointer-events` is off on the strip and back on for the pill,
         so the rows underneath it stay clickable. -->
    <div class="shelf-selbar-float">
      <ShelfSelectionBar
        ref="selBarRef"
        @rename="startRenameSelected"
        @set-base-model="editVerb = 'base-model'"
        @set-kind="editVerb = 'kind'"
        @stack="confirmStack"
        @move="openMove(store.selectedRows)"
        @set-icon="pickIcon"
        @clear-icons="confirmClearIcons"
        @forget="confirmForget"
      />
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
        :count="moves.failure ? null : moves.done"
        :total="moves.failure ? null : moves.total"
        :abort-label="moveProgressAction"
        @abort="moves.failure ? dismissMoveFailure() : moves.cancel()"
      />
    </div>
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
import FolderBrowser from "../editors/FolderBrowser.vue";
import ModelMark from "../widgets/ModelMark.vue";
import ProgressOverlay from "../widgets/ProgressOverlay.vue";
import StackEdgeTicks from "../widgets/StackEdgeTicks.vue";
import { useConfirm } from "../../composables/useConfirm";
import { addModelFile } from "../../api/modelFiles";
import { createStack } from "../../api/modelStacks";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { isModelFileDrag, setInternalDragPayload } from "../../utils/media";
import {
  assignmentRing,
  bandGroups,
  bandKeyFor,
  bandProjection,
  bandUsage,
  capabilityLabel,
  withEmptyFolders,
  withFolderSignals,
  formatModelSize,
  GROUP_BY_LABELS,
  movableCopies,
  SORT_LABELS,
  stackReceipt,
  trainingStep,
  sortDirectionLabel,
} from "../../utils/modelShelf";

const store = useModelShelfStore();
const entityLists = useEntityListsStore();
const foldersStore = useModelFoldersStore();
const moves = useModelMovesStore();
const rootEl = ref(null);
const showMenuOpen = ref(false);
const sortMenuOpen = ref(false);
const foldersOpen = ref(false);
const addMenuOpen = ref(false);
const groupMenuOpen = ref(false);
// One button behind every dialog the toolbar's left half opens, so focus has
// one place to come back to however the reader got there. The menu item that
// opened it is gone by then — it unmounts with the menu.
const addBtnRef = ref(null);
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
/** The band the pointer is currently over, for its meter's projection (#894). */
const dropBandKey = ref("");
/**
 * The dragged bytes, by the folder they are in NOW.
 *
 * Kept for the length of the drag because `dataTransfer`'s DATA is unreadable
 * during `dragover` — only `types` is — and the projection has to be drawn
 * while the pointer is still down. The drag always starts in this component, so
 * this is a hand-off between two of its own handlers and not a guess.
 */
const dragBytesByFolder = shallowRef(new Map());
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
 * fall to `<body>` — the next Tab restarts at the top of the document, which is
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
  const { items, bytesByFolderId } = movableCopies(
    store.selectedRows,
    foldersById.value,
  );
  if (!items.length) {
    event.preventDefault();
    return;
  }
  dragBytesByFolder.value = bytesByFolderId;
  event.dataTransfer.effectAllowed = "move";
  setInternalDragPayload(event.dataTransfer, { type: "model-files", items });
}

/** Everything the pointer was saying, cleared however the drag ended. */
function clearDropState() {
  dropTargetKey.value = "";
  dropBandKey.value = "";
  dragBytesByFolder.value = new Map();
}

/**
 * The bytes this drag would ADD to a drive.
 *
 * Copies already on it are excluded, because a move within one drive is a
 * rename: the server reports `bytes_to_copy` of zero for exactly that case, and
 * a meter projecting 438 GB onto the disk those bytes are already sitting on
 * would refuse a move that costs nothing.
 */
function bytesLandingOn(band) {
  if (!band) return 0;
  let bytes = 0;
  for (const [folderId, size] of dragBytesByFolder.value) {
    if (bandKeyFor(folderId, foldersStore.deviceByFolderId) !== band.key) {
      bytes += size;
    }
  }
  return bytes;
}

/**
 * The projection for the band under the pointer, and only for that one.
 *
 * A computed rather than a per-band call, so the arithmetic runs once per drag
 * position however many times the template asks for it — the heading reads it
 * for its own state, for the ghost segment, for the glyph and for the label.
 */
const dropProjection = computed(() => {
  if (!dropBandKey.value) return null;
  const group = shownGroups.value.find(
    (item) => item.bandStart && item.band?.key === dropBandKey.value,
  );
  if (!group) return null;
  return bandProjection(group.band, bytesLandingOn(group.band));
});

/** The projection if this band is the one being dragged over, else null. */
function projection(band) {
  return band && band.key === dropBandKey.value ? dropProjection.value : null;
}

/**
 * Whether a drop aimed at this drive has room for it.
 *
 * A drive we could not measure answers **true**: "we cannot say" must not read
 * as "does not fit", and refusing a drop on an unplugged-then-replugged disk
 * because its capacity never came back would be a refusal with no cause the
 * reader can see. The server still checks before it copies.
 */
function dropFits(band) {
  const projected = bandProjection(band, bytesLandingOn(band));
  return projected ? projected.fits : true;
}

/**
 * What this band is saying to the drag right now: nothing, that it takes the
 * drop, or that it refuses it.
 *
 * Keyed on the pointer and the fit rather than on the projection, so a drive
 * whose capacity we could not read still highlights as a target. It has no
 * ghost to draw and no outcome to state, but it does accept the drop — and a
 * target that accepts without saying so is the one gap a projection-gated
 * highlight would open.
 */
function bandDropState(band) {
  if (!band || band.key !== dropBandKey.value) return "";
  return dropFits(band) ? "drop" : "reject";
}

/**
 * The folder a drop on the BAND resolves to: the first on that drive a move may
 * be sent to, in the order the headers are drawn.
 *
 * A band is a disk and a move needs a folder, so one of them has to be chosen.
 * Choosing the first is safe because a drop does not move on release — the
 * dialog states the destination and its select corrects it — and it is kinder
 * than refusing a drive holding two eligible folders, which would be a refusal
 * the reject treatment does not mean.
 */
function bandDropFolderId(band) {
  const group = (band?.groups || []).find((item) => isDropTarget(item));
  return group ? Number(group.folderId) : null;
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
  // Recorded BEFORE the fit is judged, so a refused header still has something
  // for `dragleave` to clear and the band above it keeps projecting for exactly
  // as long as the pointer is there. The highlight is what the fit gates.
  dropTargetKey.value = group.key;
  dropBandKey.value = group.band?.key || "";
  // A folder on a full drive cannot take the files either — the refusal belongs
  // to the disk, not to the header, which is why the check lives here as well
  // and the band above is where it is drawn.
  if (!dropFits(group.band)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
}

function onGroupDragLeave(group) {
  // Only when the pointer really left: moving header to header inside one band
  // fires this AFTER `dragover` on the new one, so the key has already moved on
  // and clearing here would blink the projection off between two targets.
  if (dropTargetKey.value !== group.key) return;
  dropTargetKey.value = "";
  dropBandKey.value = "";
}

function onGroupDrop(group, event) {
  const fits = dropFits(group.band);
  clearDropState();
  if (!isModelFileDrag(event.dataTransfer) || !isDropTarget(group)) return;
  if (!fits) return;
  event.preventDefault();
  openMove(store.selectedRows, Number(group.folderId));
}

/**
 * The meter is the drop target (#894).
 *
 * It is where "which disk has room" stops being informational, and it is the
 * honest place to refuse: a drop that will not fit is refused while the pointer
 * is still down, next to the projection saying why, rather than as a message
 * after the release. The band is still marked as the target while it refuses —
 * `preventDefault()` is simply not called, so the browser draws its own "no
 * drop here" cursor over a band already in the error treatment.
 */
function onBandDragOver(band, event) {
  if (!isModelFileDrag(event.dataTransfer)) return;
  // Nothing on this drive takes a drop at all. No projection either: it would
  // promise an outcome for a gesture that has no destination to resolve to.
  if (bandDropFolderId(band) === null) return;
  dropTargetKey.value = "";
  dropBandKey.value = band.key;
  if (!dropFits(band)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
}

function onBandDragLeave(band) {
  // `dropTargetKey` set means the pointer moved from the band DOWN onto one of
  // its own folder headers, which is still inside this band's projection.
  if (dropBandKey.value === band.key && !dropTargetKey.value) {
    dropBandKey.value = "";
  }
}

function onBandDrop(band, event) {
  const folderId = bandDropFolderId(band);
  const fits = dropFits(band);
  clearDropState();
  if (!isModelFileDrag(event.dataTransfer) || folderId === null) return;
  if (!fits) return;
  event.preventDefault();
  openMove(store.selectedRows, folderId);
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

/**
 * Group the selection into one run, the bar's manual counterpart to the sweep.
 *
 * A confirmation and not a dry run, unlike the toolbar's proposals dialog: the
 * reader assembled this group themselves and is looking at it, so there is
 * nothing to show them they have not already chosen. It is still a prompt,
 * because there is no way back — nothing unstacks a model shelf run — and every
 * verb afterwards acts on the whole run rather than the file that was clicked.
 *
 * The bar refuses anything the route would, so a failure here is the shelf
 * having changed underneath (409) rather than a gesture that should not have
 * been offered; it is reported and nothing local is guessed at.
 */
async function confirmStack() {
  const ids = store.selectedModelIds;
  if (ids.length < 2) return;
  const ok = await confirm({
    title: `Group ${ids.length} files into one run?`,
    message:
      "They become one row on the shelf — the bare final file, or the highest " +
      "step, stands for the run — and every verb then acts on all of them.",
    warning: "Nothing unstacks a run afterwards.",
    confirmLabel: "Group them",
  });
  if (!ok) return;
  const notices = useNoticeStore();
  try {
    await createStack(ids);
    await store.fetchRows();
    notices.push({ level: "success", text: stackReceipt(1, 0) });
  } catch (err) {
    notices.push({
      level: "error",
      text: errorDetail(err) || "Those files could not be grouped.",
    });
  }
}

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
  // `training_step` from the API when the row carries one, and the filename
  // only as the fallback it always was. The column is what the scanner parsed;
  // re-deriving it here made the shelf's answer depend on which of two parsers
  // ran, and they are only equal by convention.
  const step =
    member.training_step ?? trainingStep(member.filename ?? "") ?? null;
  return step === null ? "Final" : `Step ${step.toLocaleString()}`;
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

// ── Import from ai-toolkit (shelf plan F6) ──────────────────────────────────

const importOpen = ref(false);

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
  (addBtnRef.value?.isConnected ? addBtnRef.value : rootEl.value)?.focus();
}

// ── Add file (shelf plan F6's remainder) ────────────────────────────────────
//
// The loose-file path: one adapter that belongs to no training run and does not
// deserve a registered folder of its own. It lands in the managed store — the
// ruled default destination — and the server registers it as it copies, so the
// row is on the shelf when the call returns and no rescan is needed.
//
// No confirmation and no destination picker. A copy into PixlStash's own store
// writes nothing the owner had, removes nothing, and is undone by forgetting the
// row; asking twice would be ceremony around the least dangerous shelf verb
// there is. Choosing another destination is what a drag onto a folder already
// does, and it does it better, with the folder in front of you.

const addFileOpen = ref(false);
const adding = ref(false);

function openAddFile() {
  if (adding.value) return;
  addFileOpen.value = true;
}

async function closeAddFile() {
  addFileOpen.value = false;
  await nextTick();
  addBtnRef.value?.focus();
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
  (returnTo?.isConnected ? returnTo : addBtnRef.value)?.focus();
}

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

// What the file line says after the filename, per absence state. On the line
// rather than only in a tooltip, because the reader's next question after "the
// name is fine" is "so where is the file", and a tooltip is not an answer you
// can scan a column for.
const LOC_NOTE = {
  present: "",
  missing: "file is not where it was",
  unreachable: "out of reach",
  forgotten: "every registered copy forgotten",
};

const LOC_TITLE = {
  present: "",
  missing: "The file is not where it was",
  // Not "the drive is unplugged", however common that is: a subdirectory the
  // scan could not list lands here too, and naming a cause we did not observe
  // is the same overclaim the muted glyph exists to avoid.
  unreachable: "Out of reach: this location could not be read",
  forgotten: "Every registered copy has been forgotten",
};

// The two states that mean SOMETHING IS WRONG, as against `unreachable`, which
// means nothing is. Both are a registered file that is not there: `missing` was
// looked for in a readable folder and not found, `forgotten` has no registered
// copy left at all. They share the row treatment because they share the fact.
const BROKEN_STATES = new Set(["missing", "forgotten"]);

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
  // The keyboard half of the pencil. F2 is the rename key everywhere a list has
  // one, and it keeps the affordance off the tab order: the shelf's dialect is
  // that the ROW is the control, so a focusable pencil per row would be 1,800
  // new tab stops for the gesture one key already covers.
  if (event.key === "F2") {
    event.preventDefault();
    startRename(row);
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
 * What the two "nobody has named this" states say out loud.
 *
 * Words, not colour: the accent on the from-file chip is a hint and the label
 * is what carries the meaning, so the pair still reads in greyscale. They are
 * different news — one string is the file's own and one is ours — and the whole
 * point of #897 is that a reader can tell which without opening the row.
 */
const NAME_TAG = {
  derived: {
    label: "derived",
    title:
      "PixlStash made this name from the file. Nobody has named this model.",
  },
  "from-file": {
    label: "from filename",
    title: "This is the file's own name. Nobody has named this model.",
  },
};

// Inline rename. One row at a time, held by row key: the field is what makes
// the dashed rule and the pencil honest — an affordance that opened a dialog
// would be advertising a field the row does not have.
const editingRowKey = ref("");
const editingName = ref("");
let editingRow = null;

/** Put the field on a row, seeded with the GIVEN name, not the shown one. */
function startRename(row) {
  editingRow = row;
  editingRowKey.value = row.rowKey;
  // Seeded from `display_name`, so opening the field on a derived row offers an
  // empty box: the derived string is a guess and pre-filling it would turn one
  // Enter into somebody having chosen it.
  editingName.value = row.display_name || "";
  nextTick(() => {
    const el = rootEl.value?.querySelector(".shelf-row-rename");
    el?.focus();
    el?.select();
  });
}

/**
 * The pill's Rename, which is the inline field and not a dialog.
 *
 * The row is where a name is edited — the dashed rule under it is what says so
 * — so the pill's button opens that field rather than a second, contradictory
 * way to do the same thing. Gated on one row by the pill; this finds the DRAWN
 * row for it, because a model with copies in two folders is two draws and the
 * field belongs to whichever one is on screen.
 */
function startRenameSelected() {
  const id = store.selectedRows[0]?.id;
  if (id == null) return;
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
 * acts on all forty. Without that, the commonest gesture in a bulk edit —
 * select, then right-click one of them — would silently drop the other 39.
 */
function openRowMenu(row, event) {
  focusedRowKey.value = row.rowKey;
  if (!store.isSelected(row.id)) {
    store.selectFromClick(row.id, {}, orderedRowIds.value);
  }
  selBarRef.value?.openContextMenu(event.clientX, event.clientY);
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
  endRename();
  if (next === String(row.display_name || "").trim()) return;
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

/**
 * The ring one row's mark wears (#892, redrawn for #904).
 *
 * The lists are read from the shared entity store rather than fetched per row:
 * `attachments` comes back on the list read already, so the whole shelf costs
 * the two list reads the sidebar makes anyway, not one lookup per attachment.
 */
function ringFor(row) {
  return assignmentRing(row.attachments, {
    characters: entityLists.characters,
    sets: entityLists.pictureSets,
  });
}

/**
 * The ring's hue, as an inline custom property, or nothing at all.
 *
 * `{}` and not `{ "--mmark-ring": "" }` for the unassigned ring: the dashed
 * grey treatment is drawn by `.mmark--none`, which needs the pseudo-element's
 * `border` shorthand to have applied first, and a custom property that is set
 * but empty makes `var(--mmark-ring, transparent)` resolve to nothing rather
 * than to its fallback. That is invalid at computed-value time, which drops the
 * whole shorthand — the 2px width with it.
 */
function ringStyle(row) {
  const { hue } = ringFor(row);
  return hue ? { "--mmark-ring": hue } : {};
}

/**
 * Cells per row, so the rows, the column names and the empty-folder row's
 * `aria-colspan` cannot drift apart. A grid where one row has a different cell
 * count is a grid a reader is lied to about.
 */
const COLUMN_COUNT = 5;

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

/** The folders every copy of which is unreachable, for the headers' rails. */
const offlineFolderIds = computed(
  () => new Set(store.offlineMounts.map((mount) => mount.folderId)),
);

/**
 * The groups as drawn: banded by drive under `Folder` + `Drive, then folder`,
 * and the store's own order on every other axis, each folder group carrying
 * what its header states about the folder (#899).
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
  const arranged =
    store.view.folderLayout === "drive"
      ? bandGroups(groups, foldersStore.deviceByFolderId)
      : groups;
  // Last, so it decorates what is actually drawn under either layout. The
  // offline set comes off the SHELF store rather than the registry: "wholly out
  // of reach" is a fact about the copies, and the registry only knows a path.
  return withFolderSignals(arranged, {
    folders: foldersStore.folders,
    deviceByFolderId: foldersStore.deviceByFolderId,
    offlineFolderIds: offlineFolderIds.value,
  });
});

/**
 * The rail, and the one step of nesting.
 *
 * Both ride machinery that is already there: `.ps-row`'s rail is always present
 * and always transparent (§5.1), so only its colour changes and a header that
 * gains a drive does not move a pixel; `--depth` is the shared row system's own
 * indent and is what every nested row in the sidebar uses.
 */
function groupStyle(group) {
  const style = {};
  // Not on an offline header: that rail is muted and dashed, and a drive hue
  // on it would say the disk is there.
  if (group.drive && !group.offline) style.borderLeftColor = group.drive.rail;
  if (group.nested) style["--depth"] = 1;
  return style;
}

/**
 * What a folder header says out loud.
 *
 * The same three facts the chips and the rail carry, because a rail has no
 * accessible name and a hue has none either: the tier, the drive and whether
 * the folder can be reached at all.
 */
function groupLabel(group) {
  return [
    group.label,
    group.chip,
    group.drive?.label,
    group.offline ? "offline" : "",
    modelCount(group.rows.length),
  ]
    .filter(Boolean)
    .join(", ");
}

function usage(band) {
  return bandUsage(band);
}

/**
 * The figures the meter draws: the projection while a drag is over this band,
 * the measurement otherwise.
 *
 * One object either way, because `bandProjection` returns a REPLACEMENT for
 * `bandUsage`'s — its `freePct` is already reduced by the ghost — so the three
 * measured segments need no branch of their own and the row still sums to 100.
 */
function meter(band) {
  return projection(band) || bandUsage(band);
}

/**
 * The meter's key, in the segments' own left-to-right order.
 *
 * The wording borrows `meterLabel`'s, so the key and the figures under it are
 * visibly the same vocabulary. Not "PixlStash" and not "used by other apps":
 * the shelf knows which bytes are its own and knows nothing whatever about
 * what put the rest there.
 */
const BAND_LEGEND = [
  { key: "shelf", label: "On the shelf" },
  { key: "other", label: "Other files" },
  { key: "free", label: "Free" },
];

const showsBandLegend = computed(() =>
  shownGroups.value.some((group) => group.bandStart && usage(group.band)),
);

/**
 * What a band's meter says in words.
 *
 * Free space leads, because it is the number that decides whether the next
 * checkpoint fits. A drive we could not measure says so rather than reporting
 * zero, which would draw an empty meter for a drive that may well be full.
 */
function meterLabel(band) {
  const projected = projection(band);
  if (projected) return projectionLabel(projected);
  const use = bandUsage(band);
  if (!use) return "Capacity unknown";
  const free = formatModelSize(band.freeBytes);
  const total = formatModelSize(band.totalBytes);
  const shelf = formatModelSize(band.shelfBytes);
  // One word for the low state, and it states the fact and stops. Nothing is
  // broken and there is nothing to click, so this is not the error voice and
  // gets no action — the same register as the offline banner.
  const lead = use.lowFree ? "Only " : "";
  return `${lead}${free} free of ${total} · ${shelf} on the shelf`;
}

/**
 * What a drop on this drive would do, said in words.
 *
 * The hatch says "provisional" and the colour says "refused", and neither is
 * readable aloud or in greyscale — this is the half that is. It states the
 * OUTCOME rather than the new total: the reader is deciding whether to release
 * the pointer, and "8.1 GB short" answers that where "1.9 TB used" does not.
 */
function projectionLabel(projected) {
  if (!projected.addedBytes) return "Already on this drive · nothing to copy";
  const added = formatModelSize(projected.addedBytes);
  if (!projected.fits) {
    const short = formatModelSize(-projected.freeAfter);
    return `${added} will not fit · ${short} short`;
  }
  return `${added} fits · ${formatModelSize(projected.freeAfter)} free after`;
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
 * fixing — the models come back the moment the drive does — so the line states
 * the fact and stops.
 */
const offlineNote = computed(() => {
  const mounts = store.offlineMounts;
  if (!mounts.length) return "";
  const models = modelCount(
    mounts.reduce((total, mount) => total + mount.count, 0),
  );
  if (mounts.length === 1) {
    return `${mounts[0].path} is offline — ${models} on it cannot be read.`;
  }
  const folders = `${mounts.length.toLocaleString()} model folders`;
  return `${folders} are offline — ${models} on them cannot be read.`;
});

/** The offline paths, for the banner's tooltip. */
const offlineMountPaths = computed(() =>
  store.offlineMounts.map((mount) => mount.path).join("\n"),
);

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

const activeSort = computed(
  () => SORT_LABELS[store.view.sortKey] || SORT_LABELS.added_at,
);

const activeGroup = computed(
  () => GROUP_BY_LABELS[store.view.groupBy] || GROUP_BY_LABELS.none,
);

/**
 * What the Group button says on hover.
 *
 * The layout is a sub-choice of Folder rather than a fourth axis, so it rides
 * in the tooltip beside the axis it belongs to instead of widening the label:
 * "Folder" is what the reader picked and "by drive" is how it is drawn.
 */
const groupButtonTitle = computed(() => {
  const axis = `Group: ${activeGroup.value.label}`;
  if (store.view.groupBy !== "folder") return axis;
  return store.view.folderLayout === "drive"
    ? `${axis} · by drive`
    : `${axis} · flat`;
});

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

/**
 * The always-present anchor of the metadata line, whatever else is null.
 *
 * A model that serves several features lists them ALL, because a single label
 * answers "what breaks if I delete this" wrongly for exactly the rows a reader
 * is most likely to be deciding about: Florence-2 captions and detects, and the
 * CLIP the embedder loads is both the search encoder and the aesthetic scorer's
 * backbone. `capabilities` arrives primary-first, so the first word is the one
 * `row.kind` holds and the column still reads as one thing at a glance.
 */
function kindLabel(row) {
  if (row.file_kind === "checkpoint") return "Checkpoint";
  if (row.file_kind === "unknown") return "Unclassified";
  const capabilities = Array.isArray(row.capabilities) ? row.capabilities : [];
  if (capabilities.length) return capabilities.map(capabilityLabel).join(", ");
  const kind = String(row.kind || "").toLowerCase();
  return ALGO_LABEL[kind] || kind || "Adapter";
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
  // The names, colours and thumbnails behind the assignment rings. Cached
  // and shared with the sidebar, so on a warm cache this repaints the marks
  // without a request; unawaited, because a row whose marks read `#12` for a
  // moment is a better shelf than one that waits for two list reads to draw.
  entityLists.refresh("characters");
  entityLists.refresh("sets");
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
/* The spinner keeps spinning under reduced motion, slower. The global reset in
   design-tokens.css zeroes every element's animation, and @mdi/font puts this
   one on ::before, where the reset lands — a frozen mdi-loading reads as a
   rendering fault rather than as "working". Same fix as `LoginScreen`. */
@media (prefers-reduced-motion: reduce) {
  .bar-btn .mdi-spin::before {
    animation-duration: 2s !important;
    animation-iteration-count: infinite !important;
  }
}

.shelf {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  /* The positioning context for `.shelf-progress` AND for the floating
     selection pill. Without it either resolves against whatever ancestor
     happens to be positioned — today the grid column, tomorrow anything. */
  position: relative;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  outline: none;
  /* The three data columns, stated once. FIXED widths, not `auto`: grouping
     makes one list per group, so `auto` tracks would be measured against that
     group's contents alone and the columns would step sideways from one folder
     to the next — which is the alignment #891 exists to hold. The figures are
     the resolved design's own (ui_kits/app/model-shelf.html, row anatomy). */
  --shelf-col-kind: 64px;
  --shelf-col-base: 84px;
  --shelf-col-size: 74px;
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

/* The one filled button in the bar. It is the only control here with a result
   behind it — everything else changes what you are looking at — and that is
   what the fill says. `on-primary` and not the surface ink: this is a solid
   accent fill, which is the pairing that measures (§4). */
.shelf-toolbar .bar-btn--accent {
  gap: var(--space-2);
  border-color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-weight: var(--weight-medium);
}

.shelf-toolbar .bar-btn--accent :deep(.v-icon) {
  color: rgb(var(--v-theme-on-primary));
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

.shelf-body {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* Room under the last row for the pill to float over nothing. Without it the
     bottom-most rows sit permanently behind it and cannot be read or clicked
     at the one moment they matter — while a selection exists. */
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
.shelf-selbar-float {
  position: absolute;
  left: 0;
  right: 0;
  bottom: var(--space-5);
  display: flex;
  justify-content: center;
  pointer-events: none;
  z-index: var(--z-sticky);
}

.shelf-selbar-float > :deep(*) {
  pointer-events: auto;
}

/* ── Banners ───────────────────────────────────────────────────────────────
   One line, one verb, dismissible. Nothing here is broken and nothing needs
   fixing — the models come back the moment the drive does — so it states the
   fact and stops, and deliberately never takes the error surface. */
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

.shelf-banner-dismiss {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex: none;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-background), 0.7);
  cursor: pointer;
}

.shelf-banner-dismiss:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-background));
}

/* ── Drive bands ───────────────────────────────────────────────────────────
   The OUTER of the two levels the plan allows, drawn only under `Folder` +
   `Drive, then folder`. Deliberately NOT sticky: two sticky levels need
   stacking arithmetic (the inner offset becomes the outer's measured height,
   which no token knows), and the band is a label with a meter rather than
   something the reader needs pinned while they scan a folder. */
.shelf-band {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-3) var(--space-4);
  background: rgb(var(--v-theme-surface));
  border-top: 1px solid rgb(var(--v-theme-border));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-sm);
  font-weight: var(--weight-regular);
  color: rgb(var(--v-theme-on-background));
}

/* The glyph says "this is a disk", which is what lets the label be a bare
   volume name rather than a path the reader has to parse to know what it is. */
.shelf-band-icon {
  flex: none;
  color: rgba(var(--v-theme-on-background), 0.7);
}

.shelf-band-name {
  font-weight: var(--weight-semibold);
}

/* The mount point beside the name, in the mono face §3 gives to paths: the
   volume label answers "which disk" and the path answers "which one is that",
   and on a machine with two Samsung 990s only the second one does. */
.shelf-band-path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* Rank is size and weight, never opacity: a header must not be dimmer than the
   rows it heads. The unknown case loses the meter, not the contrast. */
.shelf-band--unknown .shelf-band-figures {
  font-style: italic;
}

/* The drop affordance, in the same inset ring the folder header below uses:
   the shelf has one drop treatment and a second dialect on the outer level
   would read as a different kind of target rather than the same one. */
.shelf-band--drop {
  background: rgba(var(--v-theme-primary), 0.12);
  box-shadow: inset 0 0 0 2px rgba(var(--v-theme-primary), 0.65);
}

/* The refusal, while the pointer is still down. `no-drop` is the same cursor
   `.not-droppable` uses in the sidebar, and it is the third carrier after the
   hue and the hatch — the state has to survive greyscale and forced-colors. */
.shelf-band--reject {
  background: rgba(var(--v-theme-error), 0.1);
  box-shadow: inset 0 0 0 2px rgba(var(--v-theme-error), 0.65);
  cursor: no-drop;
}

/* One track, three segments laid end to end. They sum to exactly 100% by
   construction in `bandUsage`, so the row needs no arithmetic here and no
   sliver of bare track can open at the right-hand end.

   The ROUNDING lives on the track and nowhere else, which is the point of
   `overflow: hidden`: the outer ends curve because the track clips them, while
   every inner boundary stays square. A radius on a segment would put a curve
   mid-stack where the data has no end, and a rounded edge reads as "the bar
   stops here" when the next segment carries straight on (#893). */
.shelf-band-meter {
  display: flex;
  width: 190px;
  height: 10px;
  flex: none;
  padding: 1px;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: var(--band-meter-free);
  overflow: hidden;
}

.shelf-band-seg {
  height: 100%;
  /* Never rounded, never shrunk: a segment is the width it was given, and
     flex would otherwise take space back off the small ones. */
  border-radius: 0;
  flex: none;
}

/* The three fills. Separated by LUMINANCE, not hue, so the meter survives
   greyscale and deuteranopia: the ramp runs .0085 → .1426 → .7687 in light and
   .1426 → .5962 → .0073 in dark, and no pair depends on colour vision.
   Measurements and the per-theme reasoning live in style.css. */
.shelf-band-seg--shelf {
  background: rgb(var(--v-theme-primary));
}

.shelf-band-seg--other {
  background: var(--band-meter-other);
}

.shelf-band-seg--free {
  background: var(--band-meter-free);
}

/* The projection. HATCHED, never a solid: the other three segments are things
   that were measured and this one is a thing that has not happened, and a
   fourth flat colour would have said "this is also on the disk". The same 45°
   texture the sidebar's `.not-droppable` and the grid's ghosted tiles use.

   Both stops carry a visible alpha rather than one of them being transparent:
   a hatch that let the free track show through would read as a lighter free
   segment on a nearly-empty drive rather than as a texture. */
.shelf-band-seg--ghost {
  background: repeating-linear-gradient(
    45deg,
    rgba(var(--v-theme-primary), 0.9) 0 var(--space-1),
    rgba(var(--v-theme-primary), 0.4) var(--space-1) var(--space-2)
  );
}

/* Does not fit. The segment is clamped to the free space it is drawing into —
   a bar cannot run past its own track — so the hue and the hatch are what say
   the drop was refused, and the label says by how much. */
.shelf-band-seg--ghost-reject {
  background: repeating-linear-gradient(
    45deg,
    rgba(var(--v-theme-error), 0.9) 0 var(--space-1),
    rgba(var(--v-theme-error), 0.4) var(--space-1) var(--space-2)
  );
}

/* Track as well as segment, so a sub-pixel seam between two segments cannot
   show the neutral track through an amber bar. */
.shelf-band-meter--low,
.shelf-band-meter--low .shelf-band-seg--free {
  background: var(--band-meter-free-low);
}

/* The figures do NOT take the warning hue: they are small body text, and light
   `warning` measures 3.09:1 on the canvas — the 3:1 UI floor, not the 4.5:1
   body floor this size needs. Weight carries the rank instead, the same way
   `--unknown` above ranks by style. */
.shelf-band-figures--low,
.shelf-band-figures--reject {
  font-weight: var(--weight-semibold);
}

/* The glyph is non-text at 16px, so it may carry the hue: 3.09 light, 6.72
   dark, both over the 3:1 UI floor. */
.shelf-band-figures--low .v-icon {
  color: rgb(var(--v-theme-warning));
}

.shelf-band-figures--reject .v-icon {
  color: rgb(var(--v-theme-error));
}

.shelf-band-figures {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  color: rgba(var(--v-theme-on-background), 0.7);
}

/* The key, once for the view. Wraps rather than scrolls: four short pairs at a
   narrow width belong on two lines, not behind a scrollbar. */
.shelf-keys {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-5);
  margin: 0;
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), 0.7);
}

.shelf-key {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

/* 10px squares rather than a scaled-down meter: the key names the three fills
   and does not restate their proportions, which are per-drive. */
.shelf-key-swatch {
  width: 10px;
  height: 10px;
  flex: none;
  border-radius: 2px;
}

/* Free is the ABSENCE of a fill, so its swatch is an outline. Filled, it would
   be a fourth colour in a three-colour key. */
.shelf-keys .shelf-band-seg--free {
  background: transparent;
  border: 1px solid rgb(var(--v-theme-border));
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

.shelf-group-btn--nested {
  padding-left: var(--space-7);
}

/* Three tiers, three rails, and the glyph beside each is the shape half: the
   hue groups the folders on one disk and the tier's mdi folder gives it a
   form, but neither survives greyscale on its own — which is what the chip
   beside the label is for. */
.shelf-group-btn--registered {
  --shelf-rail: rgb(var(--v-theme-primary));
}

.shelf-group-btn--managed,
.shelf-group-btn--builtin {
  --shelf-rail: rgb(var(--v-theme-info));
}

/* An unplugged drive: muted ink and a muted rail, and deliberately NEVER the
   error colour. Nothing is lost and the models come back with the drive, so
   painting it as a failure is what trains a reader to ignore both. */
.shelf-group-btn--offline {
  --shelf-rail: rgba(var(--v-theme-on-background), 0.5);
  background: rgba(var(--v-theme-on-background), 0.04);
  color: rgba(var(--v-theme-on-background), 0.7);
}

.shelf-group-btn--offline .shelf-group-mark {
  opacity: 0.5;
}

/* The drop affordance keeps the tier rail beside it rather than replacing it:
   which folder this is does not stop being true while a drag is over it. */
.shelf-group-btn--drop {
  background: rgba(var(--v-theme-primary), 0.12);
  box-shadow:
    inset 3px 0 0 var(--shelf-rail, transparent),
    inset 0 0 0 2px rgba(var(--v-theme-primary), 0.65);
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
     virtual scroller. The size hint is only the first guess — `auto` makes the
     browser remember each row's real height after it has painted once. */
  content-visibility: auto;
  contain-intrinsic-size: auto calc(var(--entity-thumb) + var(--space-6));
}

.shelf-row:hover {
  background: var(--hover-wash);
}

.shelf-row:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}

/* A wash and an inset bar, not a border: a 1px outline on a selected row
   shifts every glyph in it by a pixel, and 200 selected rows would shimmer as
   the list scrolls. The bar is the greyscale half — the wash alone is a hue. */
.shelf-row--selected {
  background: rgba(var(--v-theme-primary), 0.12);
  box-shadow: inset 3px 0 0 rgb(var(--v-theme-primary));
}

/* ── The two kinds of absence ──────────────────────────────────────────────
   BROKEN is a fault: the file was registered and is gone. It takes the error
   rail and the error glyph in front of the name.

   OFFLINE is not a fault: the drive is simply not plugged in, nothing is lost,
   and the models come back with it. It takes a DASHED rail and muted ink, and
   deliberately NEVER the error colour — the offline case is the common one for
   anyone keeping adapters on an external disk, and painting it as a failure is
   what trains a reader to ignore both.

   They are told apart in GREYSCALE, which is what makes this a treatment and
   not a hue: solid rail, dashed rail, no rail, plus two different glyphs. */
.shelf-row--broken {
  border-left-color: rgb(var(--v-theme-error));
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

/* The identity slot, sized to hold the ring's widest treatment: the mark is
   24px and a `thick`/`double` ring stands 6px off it on every side. Anything
   narrower clips the ring on the two rows that most need it read. */
.shelf-row-ident {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: calc(var(--entity-thumb) + var(--space-4));
  flex: none;
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
   actually called — which the name above it may well not be — and it is the
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

/* An EMPTY FIELD inviting a name, never disabled-looking text — the one
   distinction #897 says decides whether these rows ever get fixed. So: full
   ink and an accent rule that is always there. Italic because rank is style
   and not another step down in contrast. */
.shelf-row-name--needs-a-name {
  font-style: italic;
  font-weight: var(--weight-regular);
  border-bottom-color: rgb(var(--v-theme-accent));
}

/* A readable name we generated. The UI face, because this string is OURS and
   is not in the file — mono would claim it were. Regular weight, so it does
   not carry the authority of a title somebody chose; the tag beside it and the
   accent rule under it say the rest. */
.shelf-row-name--derived {
  font-weight: var(--weight-regular);
  border-bottom-color: rgba(var(--v-theme-accent), 0.7);
}

/* The file's own string, shown because nothing survived the strip. Mono at
   regular weight, at FULL strength: §3 gives the mono face to file paths, and
   this IS one — so the face says what the string is rather than demoting it.
   Rank is never opacity (§5.1), and 37% of rows faded would be a column of
   ghosts. */
.shelf-row-name--from-file {
  font-family: var(--font-mono);
  font-weight: var(--weight-regular);
}

/* The two "nobody has named this" tags. Both are shapes with words in them, so
   which is which survives greyscale (§4): the accent is a hint on the
   from-file one, never the thing carrying the meaning. */
.shelf-name-tag {
  flex: none;
  padding: 0 var(--space-2);
  border: 1px solid rgba(var(--v-theme-on-background), 0.35);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  white-space: nowrap;
  /* 0.7 and not 0.6: at 11px the lower alpha misses the contrast floor (#836). */
  color: rgba(var(--v-theme-on-background), 0.7);
}

.shelf-name-tag--from-file {
  border-color: rgba(var(--v-theme-accent), 0.6);
  background: rgba(var(--v-theme-accent), 0.14);
  /* The surface's own ink on a 14% wash, NOT `on-accent`: that pairing is for a
     solid fill and measures near-invisible over a tint (§4, §11). */
  color: rgb(var(--v-theme-on-background));
}

/* Editing: a real bordered field, in the app's one focus language (§11). Sized
   to the text it replaces so committing does not jump the row. */
.shelf-row-rename {
  min-width: 0;
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

.shelf-row-rename:focus {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* The absence glyph leads the name line, because it changes what everything
   after it means: the name is still true, the file behind it is not there. */
.shelf-row-loc {
  flex: none;
}

.shelf-row-loc--missing,
.shelf-row-loc--forgotten {
  color: rgb(var(--v-theme-error));
}

.shelf-row-loc--unreachable {
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
  border-color: rgb(var(--v-theme-primary));
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
  border: 1px solid rgba(var(--v-theme-success), 0.5);
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgb(var(--v-theme-success));
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

/* Right-aligned and tabular, which is what makes a column of sizes scannable:
   the reader is comparing magnitudes, and a ragged right edge is what stops
   them being able to. */
.shelf-col--size {
  width: var(--shelf-col-size);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.shelf-row--broken .shelf-row-name,
.shelf-row--broken .shelf-col {
  opacity: 0.75;
}

/* A run's other steps. Indented past the identity column so the arrow reads as
   belonging to the row above it, and quieter than a row because it is detail
   rather than something to act on — the members are not selectable, stacks
   being atomic here. */
.shelf-row--member {
  cursor: default;
  padding-left: var(--space-7);
  color: rgb(var(--v-theme-on-surface-variant));
}

.shelf-row--member .shelf-row-name {
  font-weight: var(--weight-regular);
}

.shelf-empty-folder {
  padding: var(--space-3) var(--space-4) var(--space-3) var(--space-6);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-xs);
  font-style: italic;
  color: rgba(var(--v-theme-on-background), 0.7);
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
   toolbar keeps answering while files are in flight. */
.shelf-dim {
  position: absolute;
  inset: 0;
  z-index: var(--z-sticky);
  background: rgba(var(--v-theme-background), 0.55);
}
</style>
