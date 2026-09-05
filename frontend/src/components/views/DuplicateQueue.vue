<template>
  <div
    ref="rootEl"
    class="dq"
    role="region"
    tabindex="-1"
    aria-label="Duplicate review queue"
    aria-describedby="dq-key-help"
    data-testid="duplicate-queue"
  >
    <!-- The visible hint strip is a row of glyphs, so it is hidden from
         assistive tech and this sentence carries the model instead. It also
         carries the two keys the strip has no room for, and the one fact that
         makes the whole queue safe to work fast. -->
    <p v-if="store.showingMixed" id="dq-key-help" class="visually-hidden">
      Mixed stacks lists existing stacks whose pictures do not all match. Up and
      Down arrows choose a stack, and Page Up, Page Down, Home and End move
      further. The pictures already marked are the ones this stack does not hold
      together with; the number keys 1 to 9 point at a picture and X marks or
      unmarks it. Enter splits the marked pictures out, or unstacks the whole
      stack when fewer than two would be left, and the button says which. K
      keeps the stack as it is, and is the only action that applies to a
      selection of rows. C compares every picture in the stack side by side, and
      Z zooms. Control Z undoes the last change. Escape returns to the review
      queue, where your place is kept. No picture is ever deleted.
    </p>
    <p v-else id="dq-key-help" class="visually-hidden">
      Up and Down arrows choose a group. Page Up and Page Down move a screenful
      at a time, and Home and End jump to the first and last group. Enter or S
      stacks it. K keeps it separate. Down moves on without deciding. C compares
      every copy field by field. E shows the pictures inside an existing stack,
      below the row, and changes nothing. The number keys 1 to 9 choose the
      cover. X leaves the picture under the cursor out of the stack. Control Z
      undoes the last verdict. Escape returns here from a control, and leaves
      the Decided and Mixed stacks pages. Mixed stacks lists existing stacks
      whose pictures do not all match; opening it keeps your place here. No
      picture is ever deleted, and a stack can be undone.
    </p>

    <!-- One toolbar, not two. The queue's count, the way to the Decided page,
         the tier gate and the size control are all state or controls; the
         keyboard model that used to sit on a second bar is stated on the rows
         themselves (the Enter/S/C chips), in Compare's footer and in the
         description above, which is where a hint belongs (owner call,
         2026-07-29). -->
    <div class="dq-toolbar">
      <div class="dq-tb-left">
        <!-- The count leads, so the ellipsis eats the noun phrase and never
             the number; the full sentence stays in the DOM (a screen reader
             hears it whole at every width) and in the tooltip. -->
        <span
          v-if="store.hasGroups || store.showingMixed"
          class="qtitle"
          :title="headline"
          >{{ headline }}</span
        >
        <!-- SESSION tally, and says so: the durable record is the Decided
             page, which spans every session. -->
        <span
          v-if="store.doneCount && !store.showingDecided && !store.showingMixed"
          class="qsub"
          >{{ store.doneCount.toLocaleString() }} done this session</span
        >
        <!-- The flip side of the queue: review what was already decided and
             clear a decision. -->
        <!-- Folds into the ⋯ at ≤1180 (amendment #4), where it keeps its label
             - icon-only, `mdi-history` says nothing on its own. The exception
             is the way BACK: while the Decided page is showing, this button is
             the visible exit from a sub-page, so it stays on the bar and
             compresses to its arrow, which needs no label. -->
        <button
          v-if="!store.showingMixed"
          type="button"
          class="qdecided"
          :class="{
            'qdecided--on': store.showingDecided,
            'dq-fold-906': pageTogglesFold,
          }"
          :title="decidedToggleLabel"
          :aria-label="decidedToggleLabel"
          :aria-pressed="store.showingDecided ? 'true' : 'false'"
          @click="onToggleDecided"
        >
          <v-icon size="15">{{
            store.showingDecided ? "mdi-arrow-left" : "mdi-history"
          }}</v-icon>
          <span class="qdecided-label">{{ decidedToggleLabel }}</span>
        </button>

        <!-- The THIRD page (design D5), and deliberately not a sidebar row:
             only a destination with a to-do count earns one, and 9 to 26
             stacks is not a to-do count. The same header-toggle pattern as
             Decided, verbatim, so the way in and the way back are already
             learned.

             The count rides on THIS toggle and never on the sidebar badge:
             that badge means "groups to review", and it is the one number in
             the app that has to stay trusted. -->
        <button
          v-if="!store.showingDecided"
          type="button"
          class="qdecided"
          :class="{
            'qdecided--on': store.showingMixed,
            'dq-fold-906': pageTogglesFold,
          }"
          :title="mixedToggleTitle"
          :aria-label="mixedToggleTitle"
          :aria-pressed="store.showingMixed ? 'true' : 'false'"
          data-testid="mixed-toggle"
          @click="onToggleMixed"
        >
          <v-icon size="15">{{
            store.showingMixed ? "mdi-arrow-left" : "mdi-alert-outline"
          }}</v-icon>
          <span class="qdecided-label">{{ mixedToggleLabel }}</span>
          <span
            v-if="!store.showingMixed && store.mixedTotal"
            class="qmixed-count"
            aria-hidden="true"
            >{{ store.mixedTotal.toLocaleString() }}</span
          >
        </button>

        <!-- The ⋯, and it stands where the controls it collapses stood: at the
             end of the toggle run, inside the group it serves (amendment #2's
             principle, amendment #4's measurement). It holds the two page
             toggles and nothing else - the tier gate, the scope pill, the
             count and the app-wide tail all stay on the bar at every width.
             Fold = CSS both ways: a row and its bar button carry the same
             condition, and the container query at ≤1180 flips which of the
             pair
             is visible. The panel opens rightward (`align="start"`), because
             this trigger sits near the bar's left edge. -->
        <TbOverflowMenu
          v-if="pageTogglesFold"
          ref="overflowEl"
          class="dq-overflow"
          align="start"
        >
          <!-- Each row carries the same accessible name its bar button
               carries, spelled out rather than left to the visible text:
               name-from-content would otherwise reduce Mixed stacks to its
               label and count, and the sentence saying what a mixed stack IS
               is the whole reason that button has a tooltip. The count is
               aria-hidden on both forms for the same reason - it is already
               in the sentence. -->
          <template #default="{ close }">
            <button
              type="button"
              class="tbm-action"
              :title="decidedToggleLabel"
              :aria-label="decidedToggleLabel"
              data-testid="decided-row"
              @click="
                onToggleDecided();
                close();
              "
            >
              <v-icon size="18">mdi-history</v-icon>
              <span>{{ decidedToggleLabel }}</span>
            </button>
            <button
              type="button"
              class="tbm-action"
              :title="mixedToggleTitle"
              :aria-label="mixedToggleTitle"
              data-testid="mixed-row"
              @click="
                onToggleMixed();
                close();
              "
            >
              <v-icon size="18">mdi-alert-outline</v-icon>
              <span>{{ mixedToggleLabel }}</span>
              <span
                v-if="store.mixedTotal"
                class="qmixed-count"
                aria-hidden="true"
                >{{ store.mixedTotal.toLocaleString() }}</span
              >
            </button>
          </template>
        </TbOverflowMenu>

        <!-- Separator D-S1: renders at ALL widths (amendment #2). Its left
             flank is always populated - by the toggles above 1180, by the ⋯
             below it, and on an empty queue by whichever of the two is
             showing. The tail's D-S2 stays at every width too. -->
        <span class="dq-tb-sep" aria-hidden="true"></span>

        <!-- Escape inside the popover (including on its threshold slider,
             where the queue's key model stands down for a typing target)
             dismisses it back to the trigger, the standard popover exit. -->
        <div
          ref="tierWrapEl"
          class="dq-tier-wrap"
          @keydown.esc.stop.prevent="closeTierMenu()"
        >
          <!-- The label ellipsizes under pressure and hides at ≤1180 (the
               compressed form is [filter icon][chevron], the grid Filter
               trigger's grammar), so the button carries its own accessible
               name at every width - without it the hidden span would leave
               the name empty (WCAG 4.1.2). -->
          <button
            ref="tierButtonEl"
            type="button"
            class="dq-btn"
            :title="tierLabel"
            :aria-label="tierLabel"
            :aria-expanded="tierMenuOpen"
            aria-haspopup="true"
            @click="toggleTierMenu"
          >
            <v-icon size="16">mdi-filter-outline</v-icon>
            <span class="dq-tier-label">{{ tierLabel }}</span>
            <v-icon size="16">mdi-menu-down</v-icon>
          </button>
          <!-- Two menus behind one button. The tier gate says nothing about a
               decision already made - the server ignores it on the decided
               page entirely - so what a user reviewing decisions wants to
               narrow by is the DECISION (owner call, 2026-07-30). -->
          <DedupVerdictMenu
            v-if="tierMenuOpen && store.showingDecided"
            class="dq-tier-menu"
            :verdicts="store.verdictRows"
            :group-count="store.total"
            @toggle="onVerdictToggle"
          />
          <DedupTierMenu
            v-else-if="tierMenuOpen"
            class="dq-tier-menu"
            :tiers="store.tierRows"
            :group-count="store.openCount"
            :threshold="store.threshold"
            :min-threshold="store.bounds?.min_threshold ?? null"
            :max-threshold="store.bounds?.max_threshold ?? null"
            @threshold="onThresholdChange"
            @toggle="onTierToggle"
          />
        </div>

        <DedupScopePill
          v-if="store.isScoped"
          :label="store.scopeLabel || 'This collection'"
          :icon="store.scopeIcon || 'mdi-folder-multiple-image'"
          @dismiss="onDismissScope"
        />
      </div>

      <div class="dq-tb-right">
        <!-- The same Tiny-to-Huge ladder the grid uses, driving the strip's
             picture height and therefore the row's. Live on drag: unlike the
             grid's, this control changes a list that is already on screen, so
             the user is looking straight at the answer. -->
        <!-- Not on the Mixed stacks page: its rows draw one fixed 64px cover,
             so a size control there would be a control with nothing to buy. -->
        <div v-if="store.hasGroups && !store.showingMixed" class="dq-size">
          <v-icon size="16" aria-hidden="true"
            >mdi-image-size-select-large</v-icon
          >
          <v-slider
            class="dq-size-slider"
            :model-value="store.sizeLevel"
            :min="0"
            :max="maxSizeLevel"
            :step="1"
            density="compact"
            hide-details
            color="primary"
            thumb-color="primary"
            :aria-label="`Thumbnail size: ${sizeLabel}`"
            @update:model-value="store.setSizeLevel($event)"
            @end="onSizeCommitted"
          />
          <span class="dq-size-value">{{ sizeLabel }}</span>
        </div>

        <!-- Compresses with the bar rather than folding: a bulk action with
             an accent fill must stay a visible target. Full label → short
             "Auto-stack N" (≤1040) → icon + count (≤820), the sentence
             surviving as tooltip and accessible name throughout. -->
        <button
          v-if="store.exactCount > 0 && !readOnly && !store.showingMixed"
          type="button"
          class="dq-btn dq-btn--accent"
          :title="autoStackLabel"
          :aria-label="autoStackLabel"
          @click="openAutoStack"
        >
          <v-icon size="16">mdi-flash-outline</v-icon>
          <span class="dq-auto-full">{{ autoStackLabel }}</span>
          <span class="dq-auto-short" aria-hidden="true"
            >Auto-stack {{ store.exactCount.toLocaleString() }}</span
          >
          <span class="dq-auto-count" aria-hidden="true">{{
            store.exactCount.toLocaleString()
          }}</span>
        </button>

        <!-- The app-wide chrome, the same components the grid's toolbar
             mounts: Duplicates replaces the grid (and with it that toolbar),
             but undo/redo, Settings and the stats rail are not the grid's -
             they must not vanish with it. One separator divides the queue's
             own controls from the app-wide cluster. -->
        <span class="dq-tb-sep" aria-hidden="true"></span>
        <!-- No burger in THIS group (amendment #2's principle, kept): a
             burger may only collapse controls from its own visual group, and
             the app-wide tail never folds at any width. The queue's own ⋯
             lives in the left group, where the controls it collapses stood.
             Auto-stack compresses to an icon form and the size slider hides
             at ≤1040, its value persisting in the store. -->
        <UndoControl />
        <TbGlobalActions @open-settings="emit('open-settings')" />
      </div>
    </div>

    <DedupScanBanner :scan="store.scan" />

    <!-- One live region for the whole destination, and deliberately OUTSIDE the
         branches below: a region that unmounts with the last row takes the
         verdict that emptied the queue down with it, so the one announcement a
         user most needs is the one they would never hear. -->
    <span
      class="visually-hidden"
      role="status"
      aria-live="polite"
      data-testid="dedup-announcement"
      >{{ announcement }}</span
    >

    <!-- ── The Mixed stacks page (design D5) ────────────────────────────────
         A third page of this destination, not a route away, which is why the
         queue's window, focus and per-group choices are simply left standing
         behind it and the way back restores them for free.

         The list is bound to the queue's own threshold slider: the same stack
         is mixed at 0.90 and one clean cluster at 0.65, so the header states
         what it was computed at rather than letting the user guess. -->
    <div v-if="store.showingMixed" class="mixed" data-testid="mixed-stacks">
      <div
        v-if="store.mixedLoading && !store.mixedStacks.length"
        class="dq-state"
        role="status"
      >
        Checking which stacks hang together.
      </div>

      <!-- A failed read must never render as "no mixed stacks": that sentence
           is a claim about the library, and nobody asked. -->
      <div v-else-if="store.mixedError" class="dq-state" role="alert">
        Could not check the stacks. Nothing has changed.
        <button type="button" class="qdecided" @click="store.loadMixedStacks()">
          <v-icon size="15">mdi-refresh</v-icon>
          Try again
        </button>
      </div>

      <div v-else-if="store.mixedStacks.length" class="mixed-list">
        <!-- The threshold header. It is sticky INSIDE the list's own scroller,
             because the whole list is a function of one number and a user who
             has scrolled past that number is reading a verdict without its
             premise. The count is the sentence's SUBJECT rather than a separate
             figure beside it: "26 stacks don't hang together at 90% similar" is
             one fact, and splitting it into a numeral and a caption is what
             lets the two drift apart.

             The slider is the shipped threshold control, the same component the
             tier popover mounts, so the label, the step and the number
             formatting cannot differ between the two places the user meets it. -->
        <div class="mixed-head">
          <p class="mixed-lede">
            <b>{{ store.mixedTotal.toLocaleString() }}</b>
            {{ store.mixedTotal === 1 ? "stack doesn't" : "stacks don't" }} hang
            together at <b>{{ thresholdText }}</b> similar<template
              v-if="store.mixedLiveStackCount"
            >
              , of
              <b>{{ store.mixedLiveStackCount.toLocaleString() }}</b> in your
              library</template
            >.<template v-if="store.mixedKeptTotal">
              {{ store.mixedKeptTotal.toLocaleString() }} kept.</template
            >
          </p>
          <DedupThresholdControl
            class="mixed-threshold"
            :threshold="store.threshold"
            :min="store.bounds?.min_threshold ?? null"
            :max="store.bounds?.max_threshold ?? null"
            label="Similar enough at"
            @change="onThresholdChange"
          />
        </div>

        <!-- The bulk-scope statement, and it names the ONE verdict that acts on
             a selection. The primary's outcome differs per row (one stack
             splits, the next dissolves), so a bulk primary could not name what
             it was about to do. -->
        <div v-if="mixedSelectionCount > 1" class="qselbar">
          <span class="qselchip" role="status">
            <v-icon size="14">mdi-checkbox-multiple-marked-outline</v-icon>
            {{ mixedSelectionCount }} rows selected: Keep applies to all
            <button
              type="button"
              class="qselclear"
              title="Clear the selection (Esc)"
              @click="clearMixedSelection()"
            >
              Clear
            </button>
          </span>
        </div>

        <div ref="mixedListEl" class="mlist">
          <MixedQueueRow
            v-for="(stack, i) in store.mixedStacks"
            :key="stack.stack_id"
            :stack="stack"
            :index="i"
            :total="store.mixedStacks.length"
            :focused="i === mixedFocusIndex"
            :selected="isMixedSelected(stack)"
            :selection-count="mixedSelectionCount"
            :bulk-keys="mixedBulkKeysActive"
            :marked-ids="mixedMarksFor(stack)"
            :cursor-index="mixedCursorFor(stack)"
            :thumb-height="store.thumbHeight"
            :busy="String(store.mixedBusyStackId) === String(stack.stack_id)"
            :read-only="readOnly"
            :can-show-queue="store.groupIndexForStack(stack.stack_id) >= 0"
            :revealed="
              String(store.mixedFocusStackId ?? '') === String(stack.stack_id)
            "
            :lock-flash="flashSignature === mixedFlashKey(stack.stack_id)"
            :flash-ids="
              flashSignature === mixedFlashKey(stack.stack_id)
                ? flashIds
                : EMPTY_IDS
            "
            @focus="onMixedRowFocus(i, $event)"
            @resolve="onResolveMixed(stack)"
            @keep="onKeepMixed(stack)"
            @compare="onCompareMixed(i)"
            @show-queue="onShowQueueForStack(stack)"
            @toggle-mark="onToggleMark(stack, $event)"
            @set-cursor="setMixedCursor(stack, $event)"
          />
        </div>
        <button
          v-if="store.hasMoreMixed"
          type="button"
          class="qdecided mixed-more"
          :disabled="store.mixedLoading"
          @click="store.loadMoreMixedStacks()"
        >
          <v-icon size="15">mdi-chevron-down</v-icon>
          Show more
        </button>
      </div>

      <!-- Mirrors the shipped "No decided groups" construction, and carries
           its own way back for the same reason: the header toggle sits on the
           bar, but a user who arrived here from a flagged deck needs the exit
           where they are looking. Most libraries will never have a row here. -->
      <div v-else class="qdone">
        <v-icon size="48">mdi-check-circle-outline</v-icon>
        <h3>No mixed stacks</h3>
        <p>
          Every stack in your library holds together at
          {{ thresholdText }} similarity. Stacks whose pictures stop matching
          each other land here, and lowering the similarity slider is what
          decides how strict that is.
        </p>
        <button type="button" class="qdecided" @click="onToggleMixed">
          <v-icon size="15">mdi-arrow-left</v-icon>
          Back to review
        </button>
      </div>
    </div>

    <div v-else-if="store.loading" class="dq-state" role="status">
      Opening duplicate queue.
    </div>

    <div
      v-else-if="store.error && !store.hasGroups"
      class="dq-state"
      role="alert"
    >
      Could not confirm the duplicate queue. Nothing has been marked clear.
      <button type="button" class="qdecided" @click="store.loadFirstPage()">
        <v-icon size="15">mdi-refresh</v-icon>
        Try again
      </button>
    </div>

    <div v-else-if="store.hasGroups" class="queue">
      <!-- The bulk-scope statement: while ≥2 groups are selected, a verdict on
           any of them takes all of them. The only thing left on a second bar,
           and it appears WITH the selection and goes with it - live state, not
           a standing explanation. -->
      <div v-if="store.selectionCount > 1" class="qselbar">
        <span class="qselchip" role="status">
          <v-icon size="14">mdi-checkbox-multiple-marked-outline</v-icon>
          {{ store.selectionCount }} groups selected -
          {{
            store.showingDecided
              ? "Clear decision applies to all"
              : "Stack and Keep separate apply to all"
          }}
          <button
            type="button"
            class="qselclear"
            title="Clear the selection (Esc)"
            @click="store.clearSelection()"
          >
            Clear
          </button>
        </span>
      </div>

      <div ref="listEl" class="qlist" @scroll.passive="onListScroll">
        <div
          v-if="topSpacer"
          class="qspacer"
          :style="{ height: `${topSpacer}px` }"
          aria-hidden="true"
        ></div>
        <DedupGroupRow
          v-for="entry in windowedGroups"
          :key="entry.group.signature"
          :group="entry.group"
          :index="entry.index"
          :focused="entry.index === store.focusIndex"
          :selected="store.isSelected(entry.group.signature)"
          :selection-count="store.selectionCount"
          :bulk-keys="bulkKeysActive"
          :verdict="store.showingDecided ? entry.group.verdict || '' : ''"
          :decided-at="entry.group.decided_at || ''"
          :collapse-stacks="!store.showingDecided"
          :cover-id="store.coverIdFor(entry.group)"
          :excluded-ids="store.excludedFor(entry.group.signature)"
          :load-thumbnails="entry.loadThumbnails"
          :thumb-height="store.thumbHeight"
          :busy="store.busy"
          :read-only="readOnly"
          :flash-ids="
            flashSignature === entry.group.signature ? flashIds : EMPTY_IDS
          "
          :expanded-stack-id="expandedStackIdFor(entry.group.signature)"
          :expansion-members="expansionMembers"
          :expansion-loading="expansionLoading"
          :expansion-failed="expansionFailed"
          :flagged-stack-ids="store.flaggedStackIds"
          @focus="onRowFocus(entry.index, $event)"
          @stack="onStack(entry.group)"
          @keep-separate="onKeepSeparate(entry.group)"
          @compare="onCompare(entry.index)"
          @set-cover="store.setCover(entry.group.signature, $event)"
          @toggle-excluded="onToggleExcluded(entry.group, $event)"
          @clear-decision="onClearDecision(entry.group)"
          @toggle-expansion="onToggleExpansion(entry.group, $event)"
          @retry-expansion="retryExpansion"
          @show-mixed="onShowMixedForStack($event)"
        />
        <div
          v-if="bottomSpacer"
          class="qspacer"
          :style="{ height: `${bottomSpacer}px` }"
          aria-hidden="true"
        ></div>
        <!-- The track is sized for the whole queue, so a fast drag can land in
             rows that have not arrived yet. Sticky, because at that point the
             end of the list is thousands of pixels below the viewport and a
             message down there would never be seen. -->
        <div v-if="store.loadingMore" class="qmore" role="status">
          <v-icon size="14">mdi-progress-download</v-icon>
          Loading more groups
        </div>
      </div>
    </div>

    <!-- "Queue clear" has to be true when it is shown. A page can be emptied
         faster than the read-ahead refills it, and claiming the work is done
         while the next page is in flight is how the count stops being
         trusted. -->
    <div v-else-if="store.loadingMore" class="dq-state" role="status">
      Loading the next groups.
    </div>

    <!-- A read-only session reaches this destination only by URL (the sidebar
         row is inert), and every /dedup/* route is owner-only, so it asked for
         nothing and holds no rows. It must not fall through to the states
         below: "Confirming whether the queue is clear" never resolves without
         counts, and "Queue clear" would assert a library-wide fact this session
         cannot know. Placed AFTER the row branches on purpose, so a read-only
         render that does have groups still shows them, verdicts disabled. -->
    <div v-else-if="readOnly" class="qdone" data-testid="dedup-read-only">
      <v-icon size="48">mdi-content-duplicate</v-icon>
      <h3>Duplicate review</h3>
      <p>
        Duplicate review is only available in your own library. There, PixlStash
        groups the pictures that are the same shot, strongest match first, and
        each group is one keystroke to stack or keep separate.
      </p>
    </div>

    <div v-else-if="!store.countsLoaded" class="dq-state" role="status">
      Confirming whether the queue is clear.
    </div>

    <div
      v-else-if="scanIncomplete && !store.showingDecided"
      class="qdone"
      role="alert"
    >
      <v-icon size="48">mdi-alert-circle-outline</v-icon>
      <h3>
        {{ store.scan.status === "failed" ? "Scan failed" : "Scan incomplete" }}
      </h3>
      <p>
        Some duplicate comparisons were not completed, so this queue cannot be
        marked clear. Review any available groups and run the scan again.
      </p>
    </div>

    <!-- The empty DECIDED page keeps its own copy and, crucially, its own way
         back: the header toggle lives on the list, which is not rendered here. -->
    <div v-else-if="store.showingDecided" class="qdone">
      <v-icon size="48">mdi-history</v-icon>
      <h3>No decided groups</h3>
      <p>
        Groups you stack or keep separate land here - from any session, not just
        this one - and every decision can be reviewed and cleared until you do.
      </p>
      <button type="button" class="qdecided" @click="onToggleDecided">
        <v-icon size="15">mdi-arrow-left</v-icon>
        Back to review
      </button>
    </div>

    <div v-else class="qdone">
      <v-icon size="48">mdi-check-circle-outline</v-icon>
      <h3>Queue clear</h3>
      <p>
        {{ store.stackedCount.toLocaleString() }}
        {{ store.stackedCount === 1 ? "group" : "groups" }} stacked,
        {{ store.separatedCount.toLocaleString() }} kept separate. Every picture
        is still in your library. Scanning continues in the background, and new
        groups appear here as they are found.
      </p>
      <!-- Always offered: decisions are SERVER state, remembered across
           sessions, so the way to them must not depend on this session's
           tally. An empty Decided page explains itself. -->
      <button type="button" class="qdecided" @click="onToggleDecided">
        <v-icon size="15">mdi-history</v-icon>
        Review decided groups
      </button>

      <!-- The route to the stacks, offered only here: this is the end-of-task
           surface, and the toolbar would put it in front of someone mid-triage.
           Gated on the LIBRARY having a live stack with two or more members,
           never on this session's tally, because a library can hold hundreds of
           stacks that predate the feature.

           It goes to the PLACE, not to the action: All Pictures with the
           stacked filter applied, nothing selected, nothing about to happen. A
           one-click path from a satisfying "Queue clear" screen into a confirm
           for hundreds of deletions is how you get a bad afternoon. A real route
           push, so it is reloadable and Back returns to the queue. -->
      <button
        v-if="hasLiveStacks"
        type="button"
        class="qdecided"
        @click="onReviewStacks"
      >
        <v-icon size="15">mdi-layers-outline</v-icon>
        Review your stacks
      </button>
      <p v-if="hasLiveStacks" class="qdone-hint">
        {{ store.mixedLiveStackCount.toLocaleString() }}
        {{ store.mixedLiveStackCount === 1 ? "stack holds" : "stacks hold" }}
        more than one picture. Every copy is still in your library.
      </p>
    </div>

    <!-- One dialog, two modes. Compare is where the zoom lives, and the zoom is
         the single largest thing the Mixed stacks page gains by being a queue:
         a second dialog would be a second copy of it. What the mode changes is
         what a card MEANS and what its primary click does, nothing else. -->
    <DedupCompareDialog
      ref="compareRef"
      :open="compareOpen"
      :mode="store.showingMixed ? 'mixed' : 'group'"
      :group="store.focusedGroup"
      :collapse-stacks="!store.showingDecided"
      :mixed-stack="mixedFocusedRow"
      :marked-ids="mixedFocusedRow ? mixedMarksFor(mixedFocusedRow) : EMPTY_IDS"
      :primary-label="mixedPlan.label"
      :primary-icon="mixedPlan.icon"
      :cover-id="
        store.focusedGroup ? store.coverIdFor(store.focusedGroup) : null
      "
      :excluded-ids="
        store.focusedGroup
          ? store.excludedFor(store.focusedGroup.signature)
          : []
      "
      :busy="store.busy || store.mixedBusyStackId !== null"
      :read-only="readOnly || store.showingDecided"
      @close="closeCompare"
      @set-cover="
        store.focusedGroup &&
        store.setCover(store.focusedGroup.signature, $event)
      "
      @toggle-excluded="
        store.focusedGroup && onToggleExcluded(store.focusedGroup, $event)
      "
      @stack="onCompareStack"
      @keep-separate="onCompareKeepSeparate"
      @toggle-mark="mixedFocusedRow && onToggleMark(mixedFocusedRow, $event)"
      @resolve="mixedFocusedRow && onResolveMixed(mixedFocusedRow)"
      @keep="mixedFocusedRow && onKeepMixed(mixedFocusedRow)"
    />

    <DedupAutoStackDialog
      :open="autoStackOpen"
      :preview="autoStackPreview"
      :loading="autoStackLoading"
      :preview-failed="autoStackPreviewFailed"
      :busy="store.busy"
      :queue-remaining="store.queueOnlyCount"
      @close="autoStackOpen = false"
      @confirm="confirmAutoStack"
    />

    <ActionReceipt v-if="!readOnly" />
  </div>
</template>

<script setup>
// The duplicate triage queue: the whole "Duplicates" destination.
//
// It replaces the grid rather than floating over it, because duplicates are a
// task with a to-do count rather than a lens on the library. Three design rules
// are load-bearing here and are worth stating where they are implemented:
//
//   * **Never block on a full pass.** The view renders whatever the first page
//     returned and lets `DedupScanBanner` narrate the rest. There is no state in
//     which the user waits on a complete scan before seeing a group.
//   * **Keep one group's worth of pictures in the DOM.** The row list is
//     windowed around the focus, and only the focused row and the one after it
//     decode real thumbnails. Ten groups and ten thousand groups therefore cost
//     the same to render, which is the difference between this and a review
//     page that renders everything and dies.
//   * **Auto-advance.** Every verdict removes its row and the focus lands on
//     the next open group, so a run of `Enter` presses works the queue without
//     a single extra keystroke.
//
// Undo is not reimplemented here. A verdict is recorded server-side like any
// other change, the operation store notices the resulting event and raises the
// standard receipt, and `Ctrl+Z` walks the same shared stack. The queue's only
// undo-specific job is to claim the chord so the app shell does not also
// handle it and undo twice.

import {
  ref,
  computed,
  watch,
  onMounted,
  onBeforeUnmount,
  nextTick,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { useDedupStore } from "../../stores/useDedupStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { isReadOnly } from "../../utils/apiClient";
import {
  candidateId,
  groupUnits,
  serverDetail,
  isLockedRefusal,
  isMixedStackStackable,
  lockedPictureIds,
  lockedSetsSentence,
  mixedStackLockedSets,
  partialStackSentence,
  mixedStackPrimary,
} from "../../utils/dedup";
import { createDedupKeyHandler } from "../../composables/useDedupQueueKeyboard";
import { useDedupRowExpansion } from "../../composables/useDedupRowExpansion";
import { useMixedStackQueue } from "../../composables/useMixedStackQueue";
import {
  MAX_THUMBNAIL_SIZE_LEVEL,
  sizeLabelForLevel,
} from "../../utils/thumbnailSizes";
import { pictureThumbnailUrl } from "../../api/pictures";
import TbGlobalActions from "../panels/TbGlobalActions.vue";
import TbOverflowMenu from "../panels/TbOverflowMenu.vue";
import UndoControl from "../panels/UndoControl.vue";
import DedupGroupRow from "../widgets/DedupGroupRow.vue";
import MixedQueueRow from "../widgets/MixedQueueRow.vue";
import DedupThresholdControl from "../widgets/DedupThresholdControl.vue";
import DedupTierMenu from "../widgets/DedupTierMenu.vue";
import DedupVerdictMenu from "../widgets/DedupVerdictMenu.vue";
import DedupScanBanner from "../widgets/DedupScanBanner.vue";
import DedupScopePill from "../widgets/DedupScopePill.vue";
import DedupCompareDialog from "../widgets/DedupCompareDialog.vue";
import DedupAutoStackDialog from "../widgets/DedupAutoStackDialog.vue";
import ActionReceipt from "../widgets/ActionReceipt.vue";

/**
 * How many rows beyond the anchors stay mounted.
 *
 * The window is anchored to BOTH the keyboard focus and the scroll position:
 * anchoring to the focus alone renders a fixed dozen rows and leaves a mouse
 * user scrolling into blank spacer - a 327-group queue that appears to hold 9.
 * Enough margin that neither a page of arrow presses nor a flick of the wheel
 * lands on an empty viewport; small enough that the mounted row count stays a
 * constant rather than a function of the queue's length.
 */
const WINDOW_BEFORE = 4;
const WINDOW_AFTER = 8;

/**
 * What a row costs beyond its pictures: 8px of padding top and bottom, a 1px
 * border on each edge, and the 8px gap to the next row. Measured, and the
 * reason the estimate is a function of the size level rather than a constant -
 * the whole scroll track is sized from it, so it has to move when the size
 * control does.
 */
const ROW_CHROME_PX = 28;

/**
 * The floor a row cannot go under whatever the pictures do: the info column
 * (title, confidence, one why-pill) and the three verdict buttons both sit
 * beside the strip. Below this the size control stops buying rows per screen
 * and only buys back horizontal space.
 */
const MIN_ROW_CONTENT_PX = 89;

/**
 * What the queue says when `X` is refused at the stack floor.
 *
 * One string for both routes into the refusal (the row's right-click and the
 * key handler), because a rule stated two ways is a rule that drifts.
 */
// "Tiles", not "pictures": one tile can be a whole existing stack, and the
// floor counts tiles. A tile is what the user is looking at and what X acts on.
const STACK_FLOOR_NOTICE =
  "A stack needs at least two items in this row, so this one has to stay in. Keep the group separate instead.";

/**
 * How long the lock chip stays flashed after a refused Stack.
 *
 * Comfortably longer than the animation itself (`--dur-2`, 200ms) so the class
 * is not pulled off mid-run, and short enough that a stale amber chip is never
 * still on screen by the time the user acts again.
 */
const LOCK_FLASH_MS = 1000;

/**
 * A stable empty array for rows that are not flashing.
 *
 * A fresh `[]` in the template would be a new prop identity on every render of
 * every row, which on a queue of twenty rows is twenty needless updates per
 * keystroke.
 */
const EMPTY_IDS = Object.freeze([]);

/**
 * The flash scope key for one Mixed stacks row.
 *
 * The lock flash is scoped by a single ref shared with the queue, and a mixed
 * row has no group signature to be scoped by. Namespacing the stack id keeps
 * the two sets of keys from ever colliding on a signature that happened to
 * look like a number.
 *
 * @param {number|string} stackId
 * @returns {string} empty when there is no row to scope to.
 */
function mixedFlashKey(stackId) {
  return stackId === null || stackId === undefined ? "" : `mixed:${stackId}`;
}

// The settings dialog is App.vue's; the queue only asks for it, the same way
// the grid's toolbar does.
const emit = defineEmits(["open-settings"]);

const route = useRoute();
const router = useRouter();
const store = useDedupStore();
const operationStore = useOperationStore();
const noticeStore = useNoticeStore();

// ── Undo/redo must put the queue back, not just fix the badge ─────────────
// Reverting a stack verdict reopens the group server-side (the op log's
// post-restore hook), but no WebSocket event says "a dedup group returned":
// the undo's pictures_changed echo carries this client's own origin and is
// suppressed like any other echo, and only the COUNTS refresh through the
// sidebar path. So the queue subscribes to the shared operation store's own
// actions and reloads itself after an undo/redo that touched a dedup
// operation - the same reload reopen() performs, through the same store, not
// a new mechanism. Scoped to dedup op types so undoing an unrelated tag edit
// does not yank a triage in progress back to the top. The subscription is
// made in setup, so Pinia removes it when the view unmounts.

const UNDO_REDO_ACTIONS = new Set(["undo", "redo", "undoTo", "undoBatchById"]);
const STACK_UNDO_ACTIONS = new Set(["undo", "undoTo", "undoBatchById"]);

/**
 * The operations an undo/redo action is ABOUT to touch, read before the
 * action runs (afterwards the stack has already moved past them).
 * @param {string} name
 * @param {Array} args
 * @returns {Object[]}
 */
function opsUndoActionTouches(name, args) {
  if (name === "undo") {
    return operationStore.nextUndo ? [operationStore.nextUndo] : [];
  }
  if (name === "redo") {
    return operationStore.nextRedo ? [operationStore.nextRedo] : [];
  }
  if (name === "undoTo") {
    const past = operationStore.past ?? [];
    const index = past.findIndex((op) => op?.id === args?.[0]);
    return index < 0 ? past : past.slice(0, index + 1);
  }
  if (name === "undoBatchById") {
    return (operationStore.operations ?? []).filter(
      (op) => op?.batch_id === args?.[0],
    );
  }
  return [];
}

/**
 * Capture semantic anchors before an undo can insert rows into the queue.
 * A raw scrollTop is not sufficient: rows ahead of the viewport can return,
 * changing the absolute pixel position while the same group should remain at
 * the same place on screen.
 */
function stackUndoViewportSnapshot(touched) {
  const list = listEl.value;
  const top = Number(list?.scrollTop) || 0;
  const topIndex = Math.max(
    store.windowStart,
    Math.floor(top / rowPitchPx.value),
  );
  const anchor = store.groups[topIndex - store.windowStart] ?? null;
  return {
    top,
    topIndex,
    anchorSignature: anchor?.signature ?? null,
    anchorOffset: top - topIndex * rowPitchPx.value,
    focusSignature: store.focusedGroup?.signature ?? null,
    signatures: new Set(store.groups.map((group) => group.signature)),
    targetIds: new Set(
      touched
        .flatMap((operation) => operation?.target_ids ?? [])
        .map((id) => String(id)),
    ),
  };
}

/** Whether an operation-store action actually changed server state. */
function undoActionSucceeded(name, result) {
  if (name === "undoTo") return Number(result) > 0;
  return result !== null && result !== undefined;
}

/**
 * Reconcile a restored stack verdict without losing the reviewer's place.
 * The store preserves the row/focus signatures; this final DOM pass preserves
 * the anchor's intra-row offset and applies `nearest` semantics to the returned
 * group, so it visibly pops back into its old context.
 */
async function reloadStackUndoInPlace(snapshot) {
  store.invalidateScopeCounts();
  await store.reloadWindowAround(snapshot.topIndex, {
    focusSignature: snapshot.focusSignature,
  });
  await nextTick();

  const list = listEl.value;
  if (!list) {
    store.refreshCounts();
    return;
  }

  const anchorLocal = snapshot.anchorSignature
    ? store.groups.findIndex(
        (group) => group.signature === snapshot.anchorSignature,
      )
    : -1;
  if (anchorLocal >= 0) {
    const anchorIndex = store.windowStart + anchorLocal;
    list.scrollTop = Math.max(
      0,
      anchorIndex * rowPitchPx.value + snapshot.anchorOffset,
    );
  } else {
    // Honest degradation when the old anchor no longer belongs to the active
    // lens: keep the old pixel place, clamped by the browser's scrollport.
    list.scrollTop = snapshot.top;
  }
  onListScroll();
  await nextTick();

  const returned = store.groups
    .map((group, local) => ({
      group,
      index: store.windowStart + local,
    }))
    .filter(({ group }) => {
      if (snapshot.signatures.has(group.signature)) return false;
      if (!snapshot.targetIds.size) return true;
      return (group.candidates ?? []).some((candidate) =>
        snapshot.targetIds.has(String(candidateId(candidate))),
      );
    })
    .sort(
      (a, b) =>
        Math.abs(a.index - snapshot.topIndex) -
        Math.abs(b.index - snapshot.topIndex),
    );
  const restored = returned[0];
  if (restored) {
    const top = restored.index * rowPitchPx.value;
    const bottom = top + rowPitchPx.value;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = bottom - list.clientHeight;
    }
    onListScroll();
  }
  store.refreshCounts();
}

operationStore.$onAction(({ name, args, after }) => {
  if (readOnly.value || !UNDO_REDO_ACTIONS.has(name)) return;
  const touched = opsUndoActionTouches(name, args);
  if (!touched.some((op) => String(op?.op_type || "").startsWith("dedup."))) {
    return;
  }
  const preserveStackUndo =
    STACK_UNDO_ACTIONS.has(name) &&
    !store.showingDecided &&
    !store.showingMixed &&
    touched.some((op) => op?.op_type === "dedup.stack");
  const snapshot = preserveStackUndo
    ? stackUndoViewportSnapshot(touched)
    : null;
  after(async (result) => {
    if (snapshot) {
      if (undoActionSucceeded(name, result)) {
        await reloadStackUndoInPlace(snapshot);
      }
      return;
    }
    // Same sequence as reopen(): the group is back in the server's unresolved
    // set, so the list, the per-scope caches and the badge all re-read.
    store.invalidateScopeCounts();
    await store.loadFirstPage();
    store.refreshCounts();
  });
});

const rootEl = ref(null);
const listEl = ref(null);
const mixedListEl = ref(null);
const tierWrapEl = ref(null);
const tierButtonEl = ref(null);
const overflowEl = ref(null);
const tierMenuOpen = ref(false);
const compareOpen = ref(false);
const autoStackOpen = ref(false);
const autoStackLoading = ref(false);
const autoStackPreview = ref(null);
const autoStackPreviewFailed = ref(false);
const announcement = ref("");

// Which thumbnails are currently flashing their lock chip, and on which row.
// Scoped by signature so a refusal on one group cannot light up a same-id
// candidate that also appears in another.
const flashIds = ref([]);
const flashSignature = ref("");
let flashTimer = null;

const readOnly = computed(() => Boolean(isReadOnly.value));
const scanIncomplete = computed(() =>
  ["partial", "failed"].includes(store.scan?.status),
);

// ── The row's stack expansion (D4) ────────────────────────────────────────
// One band in the whole queue, on the focused row, with its members read
// lazily. The composable holds that invariant and the read; the view only
// wires the two gestures to it and narrates them, because the band replaces
// its own `role="status"` loading line with the strip and nothing would
// otherwise announce that the pictures had arrived.
const {
  members: expansionMembers,
  loading: expansionLoading,
  failed: expansionFailed,
  stackIdFor: expandedStackIdFor,
  toggle: toggleExpansion,
  toggleForGroup: toggleExpansionForGroup,
  retry: retryExpansion,
  collapse: collapseExpansion,
  keepOnlyOn: keepExpansionOnFocusedRow,
} = useDedupRowExpansion();

/**
 * The count badge was pressed on one of a row's decks.
 * @param {Object} group
 * @param {number|string} stackId
 */
function onToggleExpansion(group, stackId) {
  announceExpansion(group, stackId, toggleExpansion(group?.signature, stackId));
}

/**
 * `E`: toggle the focused group's expansion.
 * @param {Object} group
 */
function onExpansionKey(group) {
  if (store.showingDecided) {
    announcement.value =
      "Every picture in this decided group is already shown.";
    return;
  }
  const result = toggleExpansionForGroup(group);
  if (!result) {
    // Not a dead key: a group of loose pictures has nothing folded away, and
    // saying so is what stops the user pressing it again.
    announcement.value =
      "This group holds no stack, so there is nothing to open.";
    return;
  }
  announceExpansion(group, result.stackId, result.open);
}

/**
 * Narrate the disclosure.
 * @param {Object} group
 * @param {number|string} stackId
 * @param {boolean} open
 */
function announceExpansion(group, stackId, open) {
  if (!open) {
    announcement.value = "Closed the stack's pictures.";
    return;
  }
  const unit = groupUnits(group).find(
    (candidateUnit) => String(candidateUnit.stackId) === String(stackId),
  );
  announcement.value = `Showing the ${unit?.depth ?? 0} pictures in this stack, below the row. Nothing has changed.`;
}

const maxSizeLevel = MAX_THUMBNAIL_SIZE_LEVEL;
const sizeLabel = computed(() => sizeLabelForLevel(store.sizeLevel));

/**
 * Whether the two page toggles are the kind that folds into the ⋯ at ≤1180.
 * They are, on the review queue, where both are forward navigation. On the
 * Decided and Mixed pages the surviving toggle reads "Back to review" and is
 * the visible way out of a sub-page, so it stays on the bar (amendment #4) -
 * and the ⋯ would then hold nothing, which is why the same flag mounts it.
 */
const pageTogglesFold = computed(
  () => !store.showingDecided && !store.showingMixed,
);

/** What the Decided toggle says: the visible label on a wide bar, the
 * tooltip and accessible name at every width (folded into the ⋯ at ≤1180). */
const decidedToggleLabel = computed(() =>
  store.showingDecided ? "Back to review" : "Decided",
);

/** The auto-stack button's full sentence: the visible label on a wide bar,
 * the tooltip and accessible name always. */
const autoStackLabel = computed(
  () =>
    `Auto-stack ${store.exactCount.toLocaleString()} exact ${
      store.exactCount === 1 ? "match" : "matches"
    }`,
);

/** What the toolbar calls the queue: the count, and which side of it is shown. */
const headline = computed(() => {
  if (store.showingMixed) {
    const n = store.mixedTotal;
    return `${n.toLocaleString()} mixed ${n === 1 ? "stack" : "stacks"}`;
  }
  return store.showingDecided
    ? `${store.total.toLocaleString()} decided ${store.total === 1 ? "group" : "groups"}`
    : `${store.openCount.toLocaleString()} ${store.openCount === 1 ? "group" : "groups"} to review`;
});

// ── The Mixed stacks queue (design D5) ────────────────────────────────────
// The THIRD queue, not a list with buttons on it. Its rows are stacks, its
// tiles are those stacks' members, and its verdicts are split / unstack / keep.
// Everything that makes the review queue workable at speed is inherited: the
// focus model, the multi-selection gestures, the auto-advance, the key handler
// and the action receipt. The composable holds the per-page view state (marks,
// the member cursor, the focus, the selection); the store owns the rows.
const {
  focusIndex: mixedFocusIndex,
  focusedRow: mixedFocusedRow,
  selectionCount: mixedSelectionCount,
  selectedRows: mixedSelectedRows,
  isSelected: isMixedSelected,
  toggleSelected: toggleMixedSelected,
  selectRange: selectMixedRange,
  clearSelection: clearMixedSelection,
  selectAll: selectAllMixed,
  setFocus: setMixedFocus,
  focusNext: mixedFocusNext,
  focusPrev: mixedFocusPrev,
  focusStart: mixedFocusStart,
  focusEnd: mixedFocusEnd,
  marksFor: mixedMarksFor,
  toggleMark: toggleMixedMark,
  cursorFor: mixedCursorFor,
  setCursor: setMixedCursor,
  setCursorToPicture: setMixedCursorToPicture,
  memberIdAtCursor: mixedMemberIdAtCursor,
  unitsFor: mixedUnitsFor,
  forgetRow: forgetMixedRow,
  reset: resetMixedQueue,
} = useMixedStackQueue(store);

/**
 * What the focused row's primary button is about to do.
 *
 * Read here as well as in the row because Compare's footer shows the same
 * button, and the two must never disagree about what Enter does.
 */
const mixedPlan = computed(() =>
  mixedFocusedRow.value
    ? mixedStackPrimary(
        mixedFocusedRow.value,
        mixedMarksFor(mixedFocusedRow.value),
      )
    : {
        action: "unstack",
        label: "",
        icon: "",
        pictureIds: [],
        dissolves: true,
      },
);

/**
 * Whether `K` would genuinely take the whole selection: two or more rows
 * selected AND the keyboard cursor inside it. Only then may every selected row
 * wear the chip, because a chip on a row the key will not hit is a lie.
 */
const mixedBulkKeysActive = computed(() => {
  const row = mixedFocusedRow.value;
  return Boolean(row && mixedSelectionCount.value > 1 && isMixedSelected(row));
});

/**
 * The rows one Keep press acts on: the selection when the pressed row is inside
 * it, that row alone otherwise. The review queue's `verdictTargets` rule,
 * applied to the one verdict here that acts in bulk.
 *
 * @param {Object} stack
 * @returns {Array<Object>}
 */
function mixedVerdictTargets(stack) {
  if (mixedSelectionCount.value > 1 && isMixedSelected(stack)) {
    return mixedSelectedRows.value;
  }
  return stack ? [stack] : [];
}

/**
 * A row click chooses; a modified row click SELECTS. The review queue's own
 * conventions, so nothing new has to be learned on this page.
 *
 * @param {number} index
 * @param {MouseEvent} [event] - absent when a row control re-emits focus.
 */
function onMixedRowFocus(index, event) {
  if (!event) {
    setMixedFocus(index);
    return;
  }
  if (event.shiftKey) {
    selectMixedRange(index);
    return;
  }
  if (event.ctrlKey || event.metaKey) {
    toggleMixedSelected(index);
    return;
  }
  setMixedFocus(index);
  clearMixedSelection();
}

/** Open Compare on a mixed row, focusing it first so the two cannot disagree. */
function onCompareMixed(index) {
  setMixedFocus(index);
  openCompare();
}

/**
 * The threshold the list was computed at, as a percentage.
 *
 * The list's whole verdict is threshold-relative, so the page states the
 * number rather than leaving the user to read the slider and infer it. The
 * SERVER'S echoed value, not the slider's, because they differ for exactly as
 * long as a reload is in flight and that is when the page is most misleading.
 */
const thresholdText = computed(() => {
  const value = Number(store.mixedThreshold ?? store.threshold);
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : "the current";
});

/** The label on the third page's toggle. */
const mixedToggleLabel = computed(() =>
  store.showingMixed ? "Back to review" : "Mixed stacks",
);

/** Its tooltip and accessible name, which carry the count at every width. */
const mixedToggleTitle = computed(() => {
  if (store.showingMixed) return "Back to review";
  const n = store.mixedTotal;
  return n
    ? `Mixed stacks: ${n.toLocaleString()} ${n === 1 ? "stack holds" : "stacks hold"} pictures that don't all match`
    : "Mixed stacks: stacks whose pictures don't all match";
});

/** The row pitch a given picture height implies, before anything is measured. */
function estimatedPitch() {
  return Math.max(store.thumbHeight, MIN_ROW_CONTENT_PX) + ROW_CHROME_PX;
}

/** The real row pitch, measured once two rows exist; the estimate until then. */
const rowPitchPx = ref(estimatedPitch());
/** First row index the scroll position implies, and how many rows fit. */
const scrollIndex = ref(0);
const viewportRows = ref(WINDOW_AFTER);

function measureRowPitch() {
  const list = listEl.value;
  if (!list) return;
  const rows = list.querySelectorAll(".grow");
  // Sampled from a COLLAPSED row. A row with an expansion band open (D4) is
  // taller than every other row in the queue, and writing that one-off height
  // into the pitch would size both spacers, and therefore the whole scroll
  // track: from a state one row is in. The row AFTER the sample may be the
  // expanded one: the pitch is the first row's own height plus the gap, so
  // only the first of the pair has to be collapsed.
  for (let i = 0; i + 1 < rows.length; i += 1) {
    if (rows[i].querySelector(".gexp")) continue;
    const pitch = rows[i + 1].offsetTop - rows[i].offsetTop;
    if (pitch > 0) rowPitchPx.value = pitch;
    break;
  }
  if (list.clientHeight > 0) {
    viewportRows.value = Math.ceil(list.clientHeight / rowPitchPx.value) + 1;
  }
}

/**
 * Fetch the next page when the scroll position is reaching past the rows the
 * client holds.
 *
 * Called on scroll AND whenever the list grows, because the scrollbar is sized
 * for the whole queue: a drag into the reserved-but-unloaded tail produces one
 * scroll event and then nothing, so without the second trigger the chase would
 * stall one page short of where the user is looking.
 */
function maybeLoadMore() {
  // While an End jump/chase is in flight the store drives its own loading;
  // scroll-position math over a window that is about to be replaced would
  // fire pages the rebase then has to discard.
  if (store.endChaseActive) return;
  const windowEnd = store.windowStart + store.groups.length;
  if (
    store.hasMore &&
    scrollIndex.value + viewportRows.value + WINDOW_AFTER >= windowEnd
  ) {
    store.loadMore();
  }
  // The mirror image after an End jump: the scroll reaching up past the
  // window's start backfills the rows above it, page by page, to the top.
  if (
    store.windowStart > 0 &&
    scrollIndex.value - WINDOW_BEFORE < store.windowStart
  ) {
    store.loadPrevious();
  }
}

function onListScroll() {
  const list = listEl.value;
  if (!list) return;
  scrollIndex.value = Math.max(
    0,
    Math.floor(list.scrollTop / rowPitchPx.value),
  );
  // A scroll away from the tail while an End jump is still paging is the user
  // taking their place back: the chase dies here rather than yanking them to
  // the bottom when its last page lands. Slack of one row keeps the pin's own
  // rounding from reading as a user move.
  if (
    store.endChaseActive &&
    scrollIndex.value + viewportRows.value < totalRows.value - 1
  ) {
    store.cancelEndChase();
  }
  // Mouse-wheel users reach the tail without ever moving the keyboard focus,
  // so the scroll position must drive loadMore exactly as the focus does.
  maybeLoadMore();
}

const focusAnchor = computed(() =>
  store.focusIndex < 0 ? 0 : store.focusIndex,
);

/**
 * A rebase (windowStart moving FORWARD: the End jump) relocates the held span
 * wholesale, but `scrollIndex` still reflects the last scroll event - load
 * decisions made from that stale value would immediately backfill the very
 * pages the jump skipped. Snap it to the focus the jump just set; the real
 * scrollbar follows through scrollFocusIntoView in the same breath. Created
 * BEFORE the groups-length watcher below, so it runs first in the flush.
 * Backward moves (upward backfill, Home's reset) keep the user's scroll.
 */
watch(
  () => store.windowStart,
  (now, before) => {
    if (now > before) scrollIndex.value = Math.max(0, focusAnchor.value);
  },
);

/**
 * Whether Enter/S would genuinely take the whole selection: two or more
 * groups selected AND the keyboard cursor inside it. Only then may every
 * selected row wear the Enter/S chips - a chip on a row the key will not hit
 * is a lie.
 */
const bulkKeysActive = computed(() => {
  const focusedGroup = store.focusedGroup;
  return Boolean(
    focusedGroup &&
    store.selectionCount > 1 &&
    store.isSelected(focusedGroup.signature),
  );
});

// Anchored to the SCROLL alone, so the mounted count stays a constant: a
// union with the focus window would mount the whole span between the keyboard
// cursor and a far-away scrollbar. Keyboard moves stay covered because
// scrollFocusIntoView drags the scroll (and thus this window) to the cursor.
//
// Everything here is in ABSOLUTE queue indices, clamped to the span the store
// actually HOLDS ([windowStart, windowStart + groups.length]) - after an End
// jump that span hangs off the queue's tail, and a scroll position outside it
// renders spacer alone while the backfill catches up.
function clampToHeld(index) {
  return Math.max(
    store.windowStart,
    Math.min(store.windowStart + store.groups.length, index),
  );
}
const renderStart = computed(() =>
  clampToHeld(scrollIndex.value - WINDOW_BEFORE),
);
const renderEnd = computed(() =>
  clampToHeld(scrollIndex.value + viewportRows.value + WINDOW_AFTER),
);
/**
 * How many rows the scroll height stands for: every group the queue HAS, not
 * just the pages fetched so far.
 *
 * Sizing the spacers to the loaded rows alone made the scrollbar grow under the
 * user's hand - the thumb shrank and jumped on every page, and the track never
 * meant anything, because "the bottom" moved each time it was reached. The
 * server's total is the only number that does not move as paging proceeds.
 * Once `hasMore` goes false AND the window is anchored at the top, the loaded
 * length is the truth (a total counted under a running scan can lag the rows
 * it already handed out); a window rebased onto the tail always stands for
 * the rows above it too.
 */
const totalRows = computed(() => {
  const held = store.windowStart + store.groups.length;
  if (!store.hasMore && store.windowStart === 0) return store.groups.length;
  return Math.max(held, store.total);
});

// Absolute spacers: the top one covers every row above the render window,
// including rows above windowStart that are not held at all, so the track
// keeps its full height (and the thumb its position) across a tail jump and
// the upward backfill - a prepended page fills spacer, it never moves the
// scroll.
const topSpacer = computed(() => renderStart.value * rowPitchPx.value);
const bottomSpacer = computed(() =>
  Math.max(0, (totalRows.value - renderEnd.value) * rowPitchPx.value),
);

/**
 * The rows that are actually mounted, each carrying its true (absolute)
 * index. Every mounted row may decode thumbnails: the window itself is the
 * budget, and the imgs are `loading="lazy"`, so off-viewport rows inside it
 * cost a request only as they approach.
 */
const windowedGroups = computed(() => {
  const localStart = renderStart.value - store.windowStart;
  const localEnd = renderEnd.value - store.windowStart;
  if (localEnd <= localStart) return [];
  return store.groups.slice(localStart, localEnd).map((group, i) => ({
    group,
    index: renderStart.value + i,
    loadThumbnails: true,
  }));
});

watch(
  () => store.groups.length,
  async () => {
    await nextTick();
    measureRowPitch();
    maybeLoadMore();
  },
);

/**
 * A size change makes every measurement taken at the old size wrong, and the
 * spacers are what the scrollbar is built from. Drop straight to the estimate
 * for the new size so the track is never sized from a stale pitch, re-measure
 * once the rows have laid out, and keep the keyboard cursor on screen: resizing
 * must not cost the user their place in the queue.
 */
watch(
  () => store.thumbHeight,
  async () => {
    rowPitchPx.value = estimatedPitch();
    await nextTick();
    measureRowPitch();
    scrollFocusIntoView();
    maybeLoadMore();
  },
);

/**
 * One End press pins the scroll to the queue's true end at once: the spacers
 * already stand for every unloaded row (the track is sized from the server
 * total), so the track's bottom exists before its rows do. The store keeps
 * paging behind the pin and lands the focus on the real last group when the
 * chase completes - or the chase dies the moment the user moves the focus
 * (store-side) or scrolls away from the tail (onListScroll above).
 */
watch(
  () => store.endChaseActive,
  async (active) => {
    if (!active) return;
    announcement.value =
      "Jumping to the end of the queue. Loading the remaining groups.";
    await nextTick();
    // The chase can be over or cancelled by the time the DOM settles; pinning
    // then would be exactly the late yank cancellation exists to prevent.
    if (!store.endChaseActive) return;
    const list = listEl.value;
    if (!list) return;
    list.scrollTop = Math.max(
      0,
      totalRows.value * rowPitchPx.value - list.clientHeight,
    );
    onListScroll();
  },
);

/**
 * What the filter button says the list is currently showing.
 *
 * On the QUEUE it names the loosest tier that is on, because that is the one
 * that decides how speculative the list is. On the DECIDED page the tier gate
 * is not in force at all (the server ignores it there), so the button names the
 * verdict filter instead - a button reading "Exact only" over a page that is
 * showing every decision would be a plain lie. Both are built from the server's
 * own rows, so a tier or verdict added later names itself here rather than
 * falling through to a wrong label.
 */
const tierLabel = computed(() => {
  if (store.showingDecided) {
    const on = store.verdictRows.filter((verdict) => verdict.enabled);
    if (!on.length || on.length === store.verdictRows.length) {
      return "All decisions";
    }
    return on.map((verdict) => verdict.label).join(" and ");
  }
  const on = store.tierRows.filter((tier) => tier.enabled);
  const loosest = on[on.length - 1];
  if (!loosest || loosest.locked) return "Exact only";
  return `Exact and ${loosest.label.toLowerCase()}`;
});

/**
 * Warm the browser cache for the group after the focused one.
 *
 * Only one group ahead: prefetching further turns the "one group in the DOM"
 * rule back into "load the whole queue", slowly.
 */
function prefetchNextGroup() {
  // focusIndex is absolute; the array holds the window.
  const next = store.groups[store.focusIndex - store.windowStart + 1];
  if (!next || typeof Image === "undefined") return;
  for (const candidate of next.candidates ?? []) {
    const img = new Image();
    // candidateId, not .id - the server calls the field picture_id - and the
    // backend origin, or the warmed URL is not the one the row will render.
    img.src = pictureThumbnailUrl(candidateId(candidate), {
      version: candidate.thumbnail_version,
    });
  }
}

/** Keep the focused row inside the scroll viewport. */
async function scrollFocusIntoView() {
  await nextTick();
  const list = listEl.value;
  if (!list) return;
  const row = list.querySelector(".grow--focus");
  if (!row || typeof row.offsetTop !== "number") {
    // The cursor left the mounted window (a verdict advanced it while the
    // user was scrolled elsewhere). Jump the scroll to its estimated pitch;
    // the scroll event remounts the row and the next pass fine-tunes.
    list.scrollTop = Math.max(0, focusAnchor.value * rowPitchPx.value);
    onListScroll();
    return;
  }
  const top = row.offsetTop;
  const bottom = top + row.offsetHeight;
  if (top < list.scrollTop) list.scrollTop = top;
  else if (bottom > list.scrollTop + list.clientHeight) {
    list.scrollTop = bottom - list.clientHeight;
  }
}

/**
 * Take the DOM focus back off a row control when the keyboard cursor moves.
 *
 * A user who tabs onto a row's Compare button and then presses ArrowDown ends
 * up with the focus ring on one row and the "Keyboard acts here" label on
 * another, which is exactly the ambiguity the focused-row treatment exists to
 * prevent. The cursor wins; the ring comes back to the queue.
 */
function reclaimFocusFromRow() {
  if (typeof document === "undefined") return;
  const list = listEl.value;
  const active = document.activeElement;
  if (!list || !active || active === rootEl.value) return;
  if (list.contains(active)) rootEl.value?.focus?.();
}

watch(
  () => store.focusIndex,
  () => {
    reclaimFocusFromRow();
    scrollFocusIntoView();
    prefetchNextGroup();
  },
);

watch(
  () => store.focusedGroup,
  (group) => {
    // The expansion lives on the focused row and nowhere else (D4), so a
    // cursor move takes it with it. Stated as "keep it only on this row"
    // rather than as a plain collapse, because the badge on an UNFOCUSED row
    // focuses that row before it opens: a blind collapse here would close the
    // band the same click had just opened.
    keepExpansionOnFocusedRow(group?.signature ?? "");
    if (!group) {
      // The queue ran out from under Compare - the last verdict, whichever
      // path gave it (footer buttons, Enter/S), or a reload that emptied the
      // list. Nothing is left to compare, so the dialog closes back to the
      // queue's done state. This is the ONLY way a verdict closes Compare:
      // with groups still open, a verdict advances in place instead.
      if (compareOpen.value) closeCompare();
      return;
    }
    const n = group.candidates?.length ?? 0;
    // Against the whole queue's length, not the held window's: after an End
    // jump "Group 200 of 200" is the truth and "of 20" would be nonsense.
    announcement.value = `Group ${store.focusIndex + 1} of ${totalRows.value}, ${n} pictures.`;
  },
);

/**
 * Bring the row the shortcut landed on into view.
 *
 * The list is a plain scroller of near-identical rows, so a jump that changed
 * nothing visible would read as a dead press. The row also washes itself while
 * it is the revealed one; both are transient and neither is a focus ring, since
 * the DOM focus stays on the queue root that owns the keys.
 */
watch(
  () => [store.showingMixed, store.mixedFocusStackId, store.mixedStacks.length],
  async () => {
    if (!store.showingMixed || !store.mixedFocusStackId) return;
    await nextTick();
    const row = mixedListEl.value?.querySelector?.(
      `[data-testid="mixed-stack-${store.mixedFocusStackId}"]`,
    );
    row?.scrollIntoView?.({ block: "nearest" });
  },
);

/**
 * Keep the Mixed queue's keyboard cursor on screen.
 *
 * The same contract the review queue's `scrollFocusIntoView` holds, and it has
 * to hold here for the same reason: the arrow keys move a cursor the user can
 * only act on if they can see it. `nearest` so a move never scrolls further
 * than it has to, which is what keeps a run of Enter presses from lurching.
 */
watch(
  () => [store.showingMixed, mixedFocusIndex.value],
  async () => {
    if (!store.showingMixed) return;
    await nextTick();
    const row = mixedListEl.value?.querySelector?.(".grow--focus");
    row?.scrollIntoView?.({ block: "nearest" });
  },
);

/** Open Compare on a row, focusing it first so the two can never disagree. */
function onCompare(index) {
  store.setFocus(index);
  openCompare();
}

/**
 * The control that opened Compare, so focus can go back to it.
 *
 * WCAG 2.4.3 expects focus to return to the invoking control, not to the
 * container. Returning it to `rootEl` dumped a keyboard user at the top of the
 * queue and made them walk back to the row they were judging.
 */
const compareInvoker = ref(null);

function openCompare() {
  compareInvoker.value =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  compareOpen.value = true;
}

function closeCompare() {
  compareOpen.value = false;
  const invoker = compareInvoker.value;
  compareInvoker.value = null;
  // The invoker can be gone: deciding from inside the dialog removes its row.
  // Falling back to the container beats dropping focus onto <body>, which
  // strands the keyboard entirely.
  if (invoker?.isConnected) invoker.focus();
  else rootEl.value?.focus?.();
}

/**
 * Give a verdict and narrate it for assistive tech.
 *
 * The visible receipt is the operation store's; this line exists because the
 * receipt is a floating pill a screen reader would otherwise have to find.
 */
async function onStack(group) {
  const targets = store.verdictTargets(group);
  if (targets.length > 1) {
    const pictures = targets.reduce((n, g) => n + store.stackSizeFor(g), 0);
    const result = await store.stack(group);
    if (result?.failed) {
      const sentence = result.uncertain
        ? `The server outcome became uncertain after ${result.completed} of ${result.requested} confirmed stacks. The queue was reloaded from the server before you retry.`
        : `Stacked ${result.completed} of ${result.requested} groups. The remaining groups stayed in the queue.`;
      announcement.value = sentence;
      noticeStore.error(sentence);
      return;
    }
    if (result) {
      announcement.value = `Stacked ${targets.length} groups (${pictures} pictures). One undo reverses them all.`;
      reportPartialStack(result, pictures);
      return;
    }
    const unresolved = targets.filter((target) =>
      store.groups.some((queued) => queued.signature === target.signature),
    );
    const completed = targets.length - unresolved.length;
    if (completed > 0) {
      const detail = serverDetail(store.error);
      const because = detail ? ` ${detail}` : "";
      const sentence = `Stacked ${completed} of ${targets.length} groups.${because} The remaining ${unresolved.length} ${unresolved.length === 1 ? "group is" : "groups are"} still selected, and one undo reverses the completed ${completed === 1 ? "stack" : "stacks"}.`;
      announcement.value = sentence;
      noticeStore.error(sentence);
      flashLockedPictures(
        lockedPictureIds(store.error),
        unresolved[0]?.signature ?? "",
      );
      return;
    }
    reportVerdictFailure("stack those groups", store.error, group);
    return;
  }
  const size = store.stackSizeFor(group);
  const result = await store.stack(group);
  if (result) {
    // The SERVER'S count, never the client estimate: a group that folds in an
    // existing stack moves every member of it, including the ones the group
    // never named, so `stackSizeFor` under-reports by exactly the depth the
    // row could not see.
    const stacked = result.picture_ids?.length ?? size;
    announcement.value = `Stacked ${stacked} pictures. The cover is kept and nothing is deleted.`;
    reportPartialStack(result, stacked);
    return;
  }
  reportVerdictFailure("stack that group", store.error, group);
}

/**
 * Say so when a locked set held some members back.
 *
 * The everyday path never reaches this: the queue already marks a frozen
 * candidate and leaves it out of the request, so `skipped` only fills when the
 * set was locked after the page was loaded. It is a warning rather than an
 * error because the verdict DID land, and the group is gone from the queue, so
 * there is no row left to anchor the explanation to.
 *
 * @param {Object} result - the verdict response.
 * @param {number} stacked - how many pictures went in.
 */
function reportPartialStack(result, stacked) {
  const sentence = partialStackSentence(result?.gesture_skipped, stacked);
  if (!sentence) return;
  announcement.value = sentence;
  noticeStore.warning(sentence);
}

/**
 * Say when `X` was refused rather than letting it read as a dead key.
 *
 * The store holds a stack at two members because the server refuses a
 * one-member stack outright, and a group of two therefore accepts no exclusion
 * at all. Without this the row simply does not change and the user presses the
 * key again; with it the queue names the rule and the way past it.
 *
 * @param {Object} group
 * @param {number} pictureId
 */
function onToggleExcluded(group, pictureId) {
  const outcome = store.toggleExcluded(group, pictureId);
  if (outcome === true) return;
  if (outcome === "locked") {
    // A different refusal from the floor, so a different sentence: this one the
    // user cannot get past by including something else, only by unlocking the
    // set. The chip on the thumbnail carries the how.
    announcement.value =
      "That picture is in a locked set, so it cannot be put into the stack. Unlock the set to include it.";
    flashLockedPictures([pictureId], group?.signature ?? "");
    return;
  }
  announcement.value = STACK_FLOOR_NOTICE;
}

/**
 * Keep a group separate, and offer the only way back.
 *
 * This verdict changes no picture row, so the backend deliberately records no
 * operation for it: there is nothing for undo to restore, and an empty
 * operation row would still consume a Ctrl+Z. No receipt will ever appear for
 * it, so the narration and the escape hatch have to be raised here instead.
 */
async function onKeepSeparate(group) {
  const targets = store.verdictTargets(group);
  const size = targets.reduce((n, g) => n + (g.candidates?.length ?? 0), 0);
  const result = await store.keepSeparate(group);
  if (!result) {
    reportVerdictFailure("record that decision", store.error);
    return;
  }
  if (result.failed) {
    const sentence = result.uncertain
      ? `The server outcome became uncertain after ${result.completed} of ${result.requested} confirmed decisions. The queue was reloaded from the server before you retry.`
      : `Kept ${result.completed} of ${result.requested} groups separate. The remaining groups stayed selected in the queue.`;
    announcement.value = sentence;
    noticeStore.error(sentence);
    return;
  }
  const sentence =
    targets.length > 1
      ? `Kept ${targets.length} groups (${size} pictures) separate. Change your mind under Decided.`
      : `Kept ${size} pictures separate. Change your mind under Decided.`;
  // No sticky notice any more (owner call, 2026-07-29): the Decided page is
  // the standing way back, so the narration can be transient.
  announcement.value = sentence;
  // A backend that records the verdict (batch_id in the response) raises the
  // standard undo receipt through the store; a second toast on top of it
  // would say the same thing twice. Older backends record nothing, so the
  // info notice remains their only visible confirmation.
  if (!result?.batch_id) noticeStore.info(sentence);
}

/**
 * Clear one decided group's verdict from the Decided page.
 *
 * Never touches pictures: a reopened "stacked" group stays stacked until it
 * is unstacked from the Stacks view; the group simply returns to the queue.
 *
 * @param {Object} group
 */
async function onClearDecision(group) {
  const targets = store.verdictTargets(group);
  const signatures = targets.map((g) => g.signature);
  const { cleared, returned } = await store.reopenMany(signatures);
  if (!cleared) {
    noticeStore.error("Could not clear that decision. Nothing changed.");
    return;
  }
  if (cleared < signatures.length) {
    noticeStore.error(
      `Cleared ${cleared} of ${signatures.length} decisions; the rest kept theirs.`,
    );
  }
  if (signatures.length > 1) {
    announcement.value = returned
      ? `Cleared ${cleared} decisions; ${returned} ${returned === 1 ? "group is" : "groups are"} back in the review queue.`
      : `Cleared ${cleared} decisions. The groups return to the queue after the next scan.`;
    return;
  }
  announcement.value = returned
    ? "Decision cleared. The group is back in the review queue."
    : "Decision cleared. The group returns to the queue after the next scan.";
}

/**
 * Say so when a verdict did not land.
 *
 * A failed verdict leaves the row exactly where it was, which on a queue whose
 * whole promise is auto-advance reads as a dead keypress. It has to be a
 * notice, not just a live-region line: the user is looking at the row, not
 * listening. When the server said why, that sentence is carried through
 * verbatim rather than being flattened into this function's generic one.
 *
 * @param {string} what - the attempt, phrased to follow "could not".
 * @param {*} [err] - the rejection, for its `detail`.
 */
function reportVerdictFailure(what, err, group = null) {
  if (Number(err?.response?.status) >= 500) {
    announcement.value = `Could not confirm whether the server completed the request. The queue was reloaded before retry is offered.`;
    noticeStore.error(
      `The outcome is uncertain. The queue was reloaded from the server; check the current row before retrying.`,
    );
    return;
  }
  const detail = serverDetail(err);
  const because = detail ? ` ${detail}` : "";
  announcement.value = `Could not ${what}.${because} The group is still in the queue, so nothing was lost.`;
  noticeStore.error(
    `Could not ${what}.${because} The group is still in the queue, so you can try again.`,
  );
  // The row stays on screen, so the refusal has an anchor: flash the lock chip
  // on the exact pictures the server named. The global sentence says what
  // happened; the flash says WHICH, which no bottom-centre notice can.
  flashLockedPictures(lockedPictureIds(err), group?.signature ?? "");
}

/**
 * Draw the eye to what a refusal named.
 *
 * One shot: the class is dropped again once the animation has run, so a second
 * refusal on the same row flashes again rather than being a no-op. The chip
 * itself is permanent; only the amber is transient.
 *
 * The scope key says WHICH row is answering: a group signature in the queue, or
 * `mixedFlashKey(stackId)` on the Mixed stacks page, whose rows carry no
 * signature and whose anchor is the row's own lock note rather than a
 * per-thumbnail chip. One function for both, because a refusal answered two
 * ways is two behaviours to keep in step.
 *
 * @param {Array<number>} pictureIds - the pictures the refusal named, if any.
 * @param {string} [scopeKey] - the row that is flashing.
 */
function flashLockedPictures(pictureIds, scopeKey = "") {
  if (!pictureIds.length && !scopeKey) return;
  flashSignature.value = scopeKey;
  flashIds.value = pictureIds;
  if (flashTimer) clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    flashIds.value = [];
    flashSignature.value = "";
    flashTimer = null;
  }, LOCK_FLASH_MS);
}

/**
 * A verdict given from inside Compare STAYS in Compare (owner requirement,
 * 2026-07-30): the store's auto-advance lands the focus on the next open
 * group and the dialog, which renders `store.focusedGroup`, flips to it in
 * place - the next decision starts without reopening anything, which is the
 * whole point of comparing a run of groups. The dialog closes only when the
 * verdict emptied the queue (the focusedGroup watcher above), and a FAILED
 * verdict changes nothing: the same group stays on screen with the failure
 * notice over it. The zoom needs no handling here - the dialog resets it on
 * every group signature change.
 */
function onCompareStack() {
  const group = store.focusedGroup;
  if (group) onStack(group);
}

function onCompareKeepSeparate() {
  const group = store.focusedGroup;
  if (group) onKeepSeparate(group);
}

/**
 * Drop the scope and say so.
 *
 * The list underneath is replaced wholesale, so silence here would leave a
 * screen-reader user with a cursor on a group they were never told about.
 */
async function onDismissScope() {
  await store.clearScope();
  announcement.value =
    "Showing duplicates from the whole library, starting at the first group.";
  rootEl.value?.focus?.();
}

function toggleTierMenu() {
  tierMenuOpen.value ? closeTierMenu() : (tierMenuOpen.value = true);
}

/**
 * True from a pointer press inside the tier popover until the next threshold
 * commit: it is what tells a POINTER-committed threshold change apart from a
 * keyboard one, because only the pointer path may hand focus back to the
 * queue (see onThresholdChange).
 */
let thresholdPointerTuning = false;

/**
 * Dismiss the tier popover.
 *
 * By default the focus goes back to the control that opened it (Escape, and
 * any dismissal that is *about the popover*), so the keyboard never has to
 * hunt for where it went. A dismissal caused by a COMMITTED change passes
 * `focusTrigger: false` and hands the focus to the queue instead - the
 * popover session is over and the next keys are for the rows.
 */
function closeTierMenu({ focusTrigger = true } = {}) {
  if (!tierMenuOpen.value) return;
  tierMenuOpen.value = false;
  thresholdPointerTuning = false;
  if (focusTrigger) tierButtonEl.value?.focus?.();
}

/**
 * A pointer press anywhere outside the popover dismisses it; one inside it
 * marks the start of a pointer gesture (a slider drag) for onThresholdChange.
 */
function onDocumentPointerDown(event) {
  if (!tierMenuOpen.value) return;
  if (tierWrapEl.value?.contains?.(event.target)) {
    thresholdPointerTuning = true;
    return;
  }
  tierMenuOpen.value = false;
}

/**
 * Whether a key event belongs to the open tier popover rather than the queue.
 *
 * The popover blocks only the keys pressed INSIDE itself: once a committed
 * change has handed focus back to the queue, the keys must work the rows even
 * while the popover stays open showing its live counts. (The auto-stack
 * dialog, a true modal, still blocks everything - see the isBlocked dep.)
 *
 * @param {KeyboardEvent} [event]
 * @returns {boolean}
 */
function tierMenuOwnsEvent(event) {
  return Boolean(tierWrapEl.value?.contains?.(event?.target));
}

/**
 * The same rule for the ⋯ overflow, and the same shape: only an OPEN panel
 * owns the keys pressed inside it. With the panel closed the trigger is an
 * ordinary toolbar button, so the queue's keys keep working while it holds
 * focus - exactly as they do on the tier trigger beside it. Escape never
 * reaches here: the menu stops it on its own wrap and closes back to the
 * trigger.
 *
 * @param {KeyboardEvent} [event]
 * @returns {boolean}
 */
function overflowOwnsEvent(event) {
  if (!overflowEl.value?.isOpen?.()) return false;
  return Boolean(overflowEl.value?.$el?.contains?.(event?.target));
}

/**
 * What `Escape` means in the queue.
 *
 * A popover first, because that is the thing on top. Otherwise it hands the
 * DOM focus back to the queue itself, which is the way out of a row's buttons
 * without tabbing through the rest of them.
 */
function onEscape() {
  if (tierMenuOpen.value) {
    closeTierMenu();
    return;
  }
  // On the Mixed stacks page the selection is the layer above the page itself,
  // exactly as it is on the review queue below: clearing it must not also cost
  // the user the page they are standing on.
  if (store.showingMixed && mixedSelectionCount.value > 0) {
    clearMixedSelection();
    return;
  }
  // The Mixed stacks page is the next layer out, and leaving it costs nothing:
  // the queue is still standing behind it with its focus intact.
  if (store.showingMixed) {
    onToggleMixed();
    return;
  }
  // The selection is the next thing "on top": clearing it must not also cost
  // the user their place, so the focus stays where it is.
  if (store.selectionCount > 0) {
    store.clearSelection();
    return;
  }
  // The Decided flip is an Escape layer of its own: one press returns to the
  // review queue, exactly as the Back-to-review toggle does (same reload
  // semantics - the queue reopens at its top, which is what toggleDecided has
  // always meant), with the keyboard handed straight back to the list.
  if (store.showingDecided) {
    onToggleDecided();
    return;
  }
  rootEl.value?.focus?.();
}

/**
 * A row click chooses; a modified row click SELECTS.
 *
 * Ctrl (or Cmd) toggles the group in and out of the multi-selection,
 * Shift extends the range from the anchor, and a plain click focuses the row
 * and drops any selection - exactly the grid's own conventions, so nothing
 * new has to be learned here.
 *
 * @param {number} index
 * @param {MouseEvent} [event] - absent when a row control re-emits focus.
 */
function onRowFocus(index, event) {
  // A row control re-emitting focus (a cover click, an exclusion toggle)
  // carries no event: it must move the cursor without costing the selection.
  if (!event) {
    store.setFocus(index);
    return;
  }
  if (event.shiftKey) {
    store.selectRange(index);
    return;
  }
  if (event.ctrlKey || event.metaKey) {
    store.toggleSelected(index);
    return;
  }
  store.setFocus(index);
  store.clearSelection();
}

/**
 * Move the tier gate. The store reloads the queue, so the menu closes: leaving
 * it open over a list that just changed underneath reads as a glitch. The
 * COMMIT ends the popover session, so the focus goes to the queue, not back
 * to the trigger - the user changed the lens and expects Enter/S/arrows to
 * work the rows now, without a click first (Escape, by contrast, still
 * returns to the trigger).
 */
async function onTierToggle(id, on) {
  closeTierMenu({ focusTrigger: false });
  rootEl.value?.focus?.();
  await store.setTierEnabled(id, on);
}

/**
 * Narrow the Decided page to one kind of decision.
 *
 * The menu STAYS open, unlike a tier toggle: with only two verdicts, hiding one
 * is usually followed by hiding or restoring the other, and a popover that shut
 * after every press would make a two-press adjustment a four-press one. The
 * keyboard still goes back to the list, so the rows are workable underneath -
 * the same split the threshold slider already uses.
 */
async function onVerdictToggle(id, on) {
  const changed = await store.setVerdictEnabled(id, on);
  if (!changed) return;
  const row = store.verdictRows.find((verdict) => verdict.id === id);
  const label = row?.label ?? id;
  announcement.value = on
    ? `Showing ${label.toLowerCase()} groups again. ${store.total.toLocaleString()} decided ${store.total === 1 ? "group" : "groups"}.`
    : `Hiding ${label.toLowerCase()} groups. ${store.total.toLocaleString()} decided ${store.total === 1 ? "group" : "groups"} left.`;
}

/**
 * Move the similarity threshold.
 *
 * The popover stays open: a threshold is a value the user tunes and re-reads
 * against the count next to it, unlike a tier switch, which is a decision they
 * make once and then want to see the result of.
 *
 * A POINTER-committed change (drag released - `change` fires once) hands the
 * keyboard back to the queue while the popover stays up with its live count.
 * A KEYBOARD-committed one keeps focus on the slider: every arrow press fires
 * its own `change`, and yanking focus after the first would turn the rest of
 * the tuning into row moves.
 */
async function onThresholdChange(value) {
  const byPointer = thresholdPointerTuning;
  thresholdPointerTuning = false;
  // The mixed list is a function of this number, so a move replaces every row's
  // verdict about itself: which members are strangers, and therefore which the
  // engine marks. An edit made at 90% is not an answer at 65%, so the page's
  // marks and its selection go with the list rather than being replayed against
  // a different question.
  resetMixedQueue();
  await store.setThreshold(value);
  if (byPointer) rootEl.value?.focus?.();
}

/**
 * A pointer-committed size change hands the keyboard back to the queue.
 * Vuetify's `end` fires on drag/track release only, so keyboard sizing keeps
 * focus on the thumb - whose arrow keys the queue's model already leaves
 * alone (`role="slider"` is a typing target).
 */
function onSizeCommitted() {
  rootEl.value?.focus?.();
}

/**
 * Flip to or from the Decided page and hand the keyboard straight to the list
 * that just appeared: focus left on the toggle makes the next Enter flip the
 * page straight back.
 */
function onToggleDecided() {
  collapseExpansion();
  store.toggleDecided();
  rootEl.value?.focus?.();
}

/**
 * Does the LIBRARY hold a stack worth looking at?
 *
 * `mixedLiveStackCount` is the server's own count of live stacks with two or
 * more members. It arrives with the optional Mixed stacks page rather than on
 * ordinary queue startup: after a cold cohesion-cache migration that list can
 * score the whole library, so a shortcut must not make the image grid wait for
 * a page the user never opened. Once loaded it remains a library fact rather
 * than a tally of what this session decided.
 */
const hasLiveStacks = computed(() => store.mixedLiveStackCount > 0);

/**
 * Leave the queue for the stacks themselves.
 *
 * A real route push carrying `stack_state=stacked`, so the destination is
 * reloadable and Back comes back here. It navigates to a PLACE and nothing
 * more: the grid mounts fresh with an empty selection, no dialog opens, and no
 * destructive action is armed. Keep it that way: the shortcut exists so the
 * user can look at what they have, not so a satisfied click can turn into a
 * confirm for hundreds of deletions.
 */
function onReviewStacks() {
  router.push({ path: "/", query: { stack_state: "stacked" } });
}

/**
 * Flip to or from the Mixed stacks page.
 *
 * Unlike the Decided flip this reloads nothing: it is a page of the same
 * destination, so the queue's window, focus and per-group choices are left
 * standing behind it and come straight back. The keyboard goes to the list
 * that just appeared, exactly as it does for Decided.
 */
async function onToggleMixed() {
  if (store.showingMixed) {
    // The selection is scoped to the page and must not survive it: coming back
    // to twelve rows selected by a gesture made minutes ago is a bulk Keep
    // waiting to be pressed by accident. The marks DO survive, keyed on each
    // stack's membership, because they are the work the user was doing.
    clearMixedSelection();
    store.hideMixedStacks();
    announcement.value = `Back in the review queue, at group ${store.focusIndex + 1}.`;
  } else {
    await store.showMixedStacks();
    focusRevealedMixedRow();
    announcement.value = `Showing ${store.mixedTotal.toLocaleString()} mixed ${
      store.mixedTotal === 1 ? "stack" : "stacks"
    } at ${thresholdText.value} similarity. Nothing has changed.`;
  }
  rootEl.value?.focus?.();
}

/**
 * Put the keyboard cursor where the user is looking.
 *
 * On the row the two-way shortcut named, or on the first row. A page whose
 * cursor sat on row 1 while the view had scrolled to row 14 would answer Enter
 * with a change to a stack nobody was reading.
 */
function focusRevealedMixedRow() {
  const wanted = store.mixedFocusStackId;
  const index = wanted
    ? store.mixedStacks.findIndex(
        (row) => String(row.stack_id) === String(wanted),
      )
    : 0;
  setMixedFocus(index < 0 ? 0 : index);
}

/**
 * Half of the two-way shortcut: a flagged deck in the queue to its row here.
 *
 * The queue's focus is deliberately untouched, so the row's own "In the queue"
 * action (and the header toggle) can put the user back exactly where they were.
 *
 * @param {number|string} stackId
 */
async function onShowMixedForStack(stackId) {
  await store.showMixedStacks(stackId);
  focusRevealedMixedRow();
  announcement.value =
    "Showing this stack on the Mixed stacks page. Your place in the review queue is kept.";
  rootEl.value?.focus?.();
}

/**
 * The other half: a row here back to a duplicate group the stack appears in.
 *
 * Offered only when a LOADED group holds it; the queue is paged, and a
 * shortcut that scrolled to a guessed row would be worse than one that is not
 * offered. The store refuses rather than guessing, and the refusal is narrated
 * instead of reading as a dead press.
 *
 * @param {Object} stack
 */
function onShowQueueForStack(stack) {
  if (store.showQueueForStack(stack?.stack_id)) {
    announcement.value = `Back in the review queue, at group ${store.focusIndex + 1}, which holds this stack.`;
    rootEl.value?.focus?.();
    return;
  }
  announcement.value =
    "This stack is not in any of the duplicate groups loaded right now.";
}

/**
 * Split the marked members off, or unstack: the row's primary action.
 *
 * Both outcomes are one call and one operation, so the standard receipt and a
 * single Ctrl+Z cover them; the store raises the receipt off the response's
 * batch id exactly as a verdict does. The row leaves the list on success, so a
 * failure has to be a notice rather than only a live-region line.
 *
 * **What happened is read off the response, never off the prediction.** The
 * button predicts `Split off 2` or `Unstack all 5` so the user knows what they
 * are pressing, but the stack can have changed between the read and the press
 * and the server applies the same floor itself. `stack_dissolved` is the
 * answer, and it is the only thing this narration trusts.
 *
 * @param {Object} stack
 */
async function onResolveMixed(stack) {
  // The row already carries the server's own verdict on whether either outcome
  // can land (`stackable` / `blocked_by_sets`), so the doomed call is never
  // issued: the button is marked `aria-disabled` and the press is answered
  // here, in the same words the 423 would have produced. The row owns the
  // marking, the page owns the guard, which is the ReviewDecisionBar contract.
  if (!isMixedStackStackable(stack)) {
    reportMixedStackRefusal(
      stack,
      lockedSetsSentence(mixedStackLockedSets(stack)),
    );
    return;
  }
  const stackId = stack?.stack_id;
  const result = await store.resolveMixedStack(stack, mixedMarksFor(stack));
  if (!result) {
    // A 400 means the row no longer describes the stack: a marked member has
    // left it since the page was read. The store has already re-read the list,
    // so the row on screen is now the truth and the user is told to look again
    // rather than pressing a button that will fail the same way.
    if (Number(store.error?.response?.status) === 400) {
      forgetMixedRow(stackId);
      const sentence =
        "This stack changed since the page was read, so nothing was done. The list has been re-read; check the marks and try again.";
      announcement.value = sentence;
      noticeStore.warning(sentence);
      return;
    }
    if (isLockedRefusal(store.error)) {
      reportMixedStackRefusal(
        stack,
        serverDetail(store.error),
        lockedPictureIds(store.error),
      );
      return;
    }
    reportMixedStackFailure(serverDetail(store.error));
    return;
  }
  forgetMixedRow(stackId);
  const moved = result.split_picture_ids?.length ?? 0;
  announcement.value = result.stack_dissolved
    ? `Freed ${moved} ${moved === 1 ? "picture" : "pictures"} and removed the stack. Nothing was deleted, and Ctrl+Z restores it.`
    : `Took ${moved} ${moved === 1 ? "picture" : "pictures"} out of the stack. Nothing was deleted, and Ctrl+Z puts ${moved === 1 ? "it" : "them"} back.`;
}

/**
 * Mark or unmark one member as a stranger.
 *
 * The same gesture from three places: the row's tile, the row's `X` key and
 * Compare's card. A locked set freezes the whole stack, so a marked member
 * could never be moved and the mark is refused rather than accepted and then
 * ignored, which is exactly how the review queue answers a cover on a frozen
 * unit.
 *
 * @param {Object} stack
 * @param {number} pictureId
 */
function onToggleMark(stack, pictureId) {
  const outcome = toggleMixedMark(stack, pictureId);
  if (outcome === "locked") {
    announcement.value = `${lockedSetsSentence(mixedStackLockedSets(stack))} Nothing in this stack can be moved until the set is unlocked.`;
    flashLockedPictures([], mixedFlashKey(stack?.stack_id));
    return;
  }
  if (outcome !== true) return;
  const marked = mixedMarksFor(stack).some(
    (id) => String(id) === String(pictureId),
  );
  const plan = mixedStackPrimary(stack, mixedMarksFor(stack));
  announcement.value = marked
    ? `Marked as a stranger. ${plan.label}.`
    : `Back in the stack. ${plan.label}.`;
}

/**
 * The answer to `S` on this page.
 *
 * `S` is Stack in the review queue and a user trained there presses it here
 * meaning Stack, which is the OPPOSITE of what this page's primary does. So the
 * key is claimed (it must never run the primary by accident, and it must never
 * fall through to the app shell) and answered out loud instead of doing
 * nothing, which would read as a broken key.
 *
 * @param {Object} stack
 */
function onMixedStackSynonym(stack) {
  const plan = mixedStackPrimary(stack, mixedMarksFor(stack));
  announcement.value = `S means Stack in the review queue. Here the primary action is ${plan.label}; press Enter.`;
  noticeStore.info(
    `S means Stack in the review queue. Here the primary action is ${plan.label}; press Enter.`,
  );
}

/**
 * Say why a mixed-stack action did not run, and anchor it on the row.
 *
 * One reporter for both refusals, because they are the same refusal met at two
 * moments: the list already knew the stack was frozen, or the lock landed after
 * the page was read and the server said so with a 423. Either way the row is
 * still on screen, which is what makes an anchor possible at all: the notice
 * says WHAT happened and the flashed lock note on the row says WHICH stack,
 * which no bottom-centre notice can.
 *
 * @param {Object} stack - the row that refused.
 * @param {string} sentence - the named reason, already built.
 * @param {Array<number>} [pictureIds] - the pictures a 423 named, if any.
 */
function reportMixedStackRefusal(stack, sentence, pictureIds = []) {
  const because = sentence ? ` ${sentence}` : "";
  announcement.value = `Could not change that stack.${because} Nothing was changed.`;
  noticeStore.error(
    `Could not change that stack.${because} Nothing was changed, so you can try again.`,
  );
  flashLockedPictures(pictureIds, mixedFlashKey(stack?.stack_id));
}

/** Report an operational failure without presenting it as a lock refusal. */
function reportMixedStackFailure(sentence) {
  const because = sentence ? ` ${sentence}` : "";
  announcement.value = `The stack could not be changed.${because}`;
  noticeStore.error(
    `The stack could not be changed.${because} Check the connection or server log, then try again.`,
  );
}

/**
 * Keep one stack, or every selected stack, as it is.
 *
 * Keep is the ONLY verdict on this page that acts in bulk. The primary's
 * outcome differs per row (one stack splits, the next dissolves), so a bulk
 * primary could not name what it was about to do, and a button that cannot name
 * its outcome must not act on twelve rows at once. Keep names the same outcome
 * on every row it touches, so it can.
 *
 * Keep changes no picture, so there is no operation to undo and no receipt will
 * ever arrive for it. The way back is to clear the dismissal, which is offered
 * here on the notice, because the row has already left the list and there is
 * nothing on screen to anchor the offer to.
 *
 * @param {Object} stack
 */
async function onKeepMixed(stack) {
  const targets = mixedVerdictTargets(stack);
  const ids = targets.map((row) => row?.stack_id);
  const results = [];
  for (const row of targets) {
    // Sequential rather than parallel: each Keep removes a row and moves the
    // totals, and the store's list is one array. A burst of concurrent writes
    // over it would race the removals against each other.
    results.push(await store.keepMixed(row));
  }
  const kept = results.filter(Boolean).length;
  if (!kept) {
    const what =
      targets.length > 1
        ? "Could not keep those stacks. They are still listed"
        : "Could not keep that stack. It is still listed";
    announcement.value = `${what}, so nothing was lost.`;
    noticeStore.error(`${what}, so you can try again.`);
    return;
  }
  for (const id of ids) forgetMixedRow(id);
  clearMixedSelection();
  const sentence =
    targets.length > 1
      ? `Kept ${kept} of ${targets.length} stacks as they are. They will be listed again if their pictures change.`
      : "Kept this stack as it is. It will be listed again if its pictures change.";
  announcement.value = sentence;
  noticeStore.info(sentence, {
    action: {
      label: targets.length > 1 ? "Undo keeps" : "Undo keep",
      handler: async () => {
        let cleared = 0;
        for (const id of ids) {
          if (await store.unkeepMixedStack(id)) cleared += 1;
        }
        announcement.value = cleared
          ? `${cleared === 1 ? "The stack is" : `${cleared} stacks are`} listed again.`
          : "Could not clear that Keep. Nothing changed.";
        if (!cleared) noticeStore.error("Could not clear that Keep.");
      },
    },
  });
}

/** Open the bulk dialog on its dry run, so the preview is never stale. */
async function openAutoStack() {
  autoStackOpen.value = true;
  autoStackLoading.value = true;
  autoStackPreviewFailed.value = false;
  const preview = await store.previewAutoStack();
  autoStackPreview.value = preview;
  // A failed dry run must not render as a confident row of zeroes: that reads
  // as "there is nothing to stack" when the truth is "nobody asked".
  autoStackPreviewFailed.value = !preview;
  autoStackLoading.value = false;
}

async function confirmAutoStack() {
  const result = await store.runAutoStack();
  autoStackOpen.value = false;
  if (result?.batch_id) {
    const made = Number(result.groups ?? 0).toLocaleString();
    announcement.value = `Created ${made} stacks. Undo reverses the whole run in one step.`;
    // One unstackable group never aborts the run, so a partial result has to be
    // reported rather than hidden behind a success message.
    const skipped = result.failures?.length ?? 0;
    if (skipped) {
      noticeStore.warning(
        `Created ${made} stacks. ${skipped} ${skipped === 1 ? "group was" : "groups were"} skipped and stayed in the queue.`,
      );
    }
  } else {
    reportVerdictFailure("create those stacks", store.error);
  }
  rootEl.value?.focus?.();
}

const compareRef = ref(null);

/**
 * Select every group in the queue and say what that came to.
 *
 * Ctrl+A pages the rest of the queue in, so it is not instant and it can stop
 * short of the whole thing. Both facts have to be said out loud: a gesture
 * called "select all" that quietly took 500 of 5,000 would put a bulk verdict
 * on a set the user never saw the size of.
 */
async function onSelectAll() {
  const { selected, total, truncated } = await store.selectAll();
  if (!selected) return;
  const count = selected.toLocaleString();
  announcement.value = truncated
    ? `Selected ${count} of ${total.toLocaleString()} groups, the most confident ones. That is as many as one selection can hold.`
    : `Selected all ${count} ${selected === 1 ? "group" : "groups"}.`;
  if (truncated) {
    noticeStore.info(
      `Selected the ${count} most confident groups. That is as many as one selection can hold, so the rest of the queue is untouched.`,
    );
  }
}

/**
 * A dialog the queue did NOT open is on screen - Settings, Share, anything the
 * sidebar raised over us.
 *
 * The handler is bound at the document (see below), so without this the queue
 * would answer keys meant for whatever is on top of it. Compare and the
 * auto-stack dialog are the queue's own and are exempt: the model has branches
 * for both.
 *
 * @returns {boolean}
 */
function foreignDialogOpen() {
  if (typeof document === "undefined") return false;
  if (compareOpen.value || autoStackOpen.value) return false;
  // Two signals, because the app has two kinds of modal: Vuetify's scrim (every
  // AppDialog) and the review overlay, which paints its own. Both are stated on
  // the DOM rather than on listener order, for the reason App.vue records at
  // `handleGlobalKeydown` - a remount silently reorders listeners.
  return (
    document.querySelector(".v-overlay--active .v-overlay__scrim") != null ||
    document.querySelector(".rs-overlay") != null
  );
}

const handleKeydown = createDedupKeyHandler({
  store,
  isCompareOpen: () => compareOpen.value,
  openCompare: () => {
    if (store.focusedGroup) openCompare();
  },
  closeCompare,
  undo: () => operationStore.undo(),
  isReadOnly: () => readOnly.value,
  // The auto-stack dialog is a modal and blocks everything; the tier popover
  // owns only the keys pressed inside itself, so a committed change that
  // handed focus back to the queue leaves the rows workable underneath it.
  // The Mixed stacks page owns the screen while it is up: the queue's rows are
  // not on it, so a verdict key would resolve a group the user cannot see.
  // Escape is exempt by construction (it resolves before this guard), which is
  // what keeps the way back a single press.
  isBlocked: (event) =>
    autoStackOpen.value ||
    store.showingMixed ||
    (tierMenuOpen.value && tierMenuOwnsEvent(event)) ||
    overflowOwnsEvent(event),
  // One row less than the viewport holds, so a page move keeps the row the
  // user was reading on screen as the anchor for the next one.
  pageRows: () => Math.max(1, viewportRows.value - 1),
  onEscape,
  onExclusionRefused: () => {
    announcement.value = STACK_FLOOR_NOTICE;
  },
  // `E` is a READ gesture, so it stays live in a read-only session exactly as
  // `C` does; the band it opens carries no control that could write.
  toggleExpansion: onExpansionKey,
  selectAll: onSelectAll,
  // The blink compare's state lives in the dialog; its KEYS live in the one
  // keyboard model, driven through the dialog's exposed surface.
  zoom: {
    isOpen: () => Boolean(compareRef.value?.isZoomOpen?.()),
    open: () => compareRef.value?.openZoom?.(),
    close: () => compareRef.value?.closeZoom?.(),
    flip: (delta) => compareRef.value?.flipZoom?.(delta),
    to: (index) => compareRef.value?.zoomTo?.(index),
    togglePixels: () => compareRef.value?.toggleZoomPixels?.(),
    step: (direction) => compareRef.value?.stepZoom?.(direction),
    pan: (dx, dy) => Boolean(compareRef.value?.panZoom?.(dx, dy)),
  },
});

/**
 * The Mixed stacks queue's rows, in the shape the shared key handler reads.
 *
 * This is the whole reason `createDedupKeyHandler` was written as a factory
 * taking its dependencies by name: the model (five decline guards, the
 * preventDefault-plus-stopPropagation claim contract, the Escape layering, the
 * Compare-open branch, the auto-repeat guard) is identical on both queues, and a
 * second copy of it would be a second place for those to drift.
 *
 * The three hooks that differ are the three facts that differ: `unitsOf` points
 * the digits at a stack's MEMBERS rather than at a group's units, `signatureOf`
 * keys a row on its stack id, and `onStackSynonym` takes `S` away from the
 * primary because Split is not Stack.
 *
 * `toggleExcluded` always reports success: `onToggleMark` narrates its own
 * refusal (a locked set freezes the whole stack), so returning false here would
 * produce a second announcement of the same event in different words.
 */
const mixedKeyStore = {
  get groups() {
    return store.mixedStacks;
  },
  get focusIndex() {
    return mixedFocusIndex.value;
  },
  get focusedGroup() {
    return mixedFocusedRow.value;
  },
  get busy() {
    return store.mixedBusyStackId !== null;
  },
  focusNext: mixedFocusNext,
  focusPrev: mixedFocusPrev,
  setFocus: setMixedFocus,
  focusStart: mixedFocusStart,
  focusEnd: mixedFocusEnd,
  coverIdFor: (row) => mixedMemberIdAtCursor(row),
  toggleExcluded: (row, pictureId) => {
    onToggleMark(row, pictureId);
    return true;
  },
  setCover: (stackId, pictureId) => setMixedCursorToPicture(stackId, pictureId),
  stack: (row) => onResolveMixed(row),
  keepSeparate: (row) => onKeepMixed(row),
};

/**
 * Ctrl+A on the Mixed stacks page, and it says what the selection can do.
 *
 * Only Keep acts on it, so the announcement names Keep rather than leaving the
 * user to discover that the primary did not follow.
 */
function onSelectAllMixed() {
  const { selected } = selectAllMixed();
  if (!selected) return;
  announcement.value = `Selected all ${selected.toLocaleString()} ${
    selected === 1 ? "stack" : "stacks"
  }. Keep applies to all of them; the primary action still acts on one row.`;
}

const handleMixedKeydown = createDedupKeyHandler({
  store: mixedKeyStore,
  isCompareOpen: () => compareOpen.value,
  openCompare: () => {
    if (mixedFocusedRow.value) openCompare();
  },
  closeCompare,
  undo: () => operationStore.undo(),
  isReadOnly: () => readOnly.value,
  isBlocked: (event) =>
    autoStackOpen.value ||
    (tierMenuOpen.value && tierMenuOwnsEvent(event)) ||
    overflowOwnsEvent(event),
  pageRows: () => Math.max(1, viewportRows.value - 1),
  onEscape,
  // `E` opens a deck in the review queue. Here every member is already on
  // screen, which is the point of the page, so the key is answered rather than
  // left to read as broken.
  toggleExpansion: () => {
    announcement.value =
      "Every picture in this stack is already on the row, so there is nothing to open.";
  },
  selectAll: onSelectAllMixed,
  zoom: {
    isOpen: () => Boolean(compareRef.value?.isZoomOpen?.()),
    open: () => compareRef.value?.openZoom?.(),
    close: () => compareRef.value?.closeZoom?.(),
    flip: (delta) => compareRef.value?.flipZoom?.(delta),
    to: (index) => compareRef.value?.zoomTo?.(index),
    togglePixels: () => compareRef.value?.toggleZoomPixels?.(),
    step: (direction) => compareRef.value?.stepZoom?.(direction),
    pan: (dx, dy) => Boolean(compareRef.value?.panZoom?.(dx, dy)),
  },
  unitsOf: mixedUnitsFor,
  signatureOf: (row) => row?.stack_id,
  onStackSynonym: onMixedStackSynonym,
});

/**
 * The queue's keys, bound at the DOCUMENT for as long as the view is mounted.
 *
 * They used to be bound on the queue root, which meant they only worked while
 * the DOM focus was inside it: one click on a sidebar row and every shortcut
 * went dead, with nothing on screen to say why or how to get them back. The
 * queue is a whole destination, not a widget - while it is the view, the keys
 * are the view's.
 *
 * Two things keep that from being greedy. `isTypingTarget` still declines to a
 * text field wherever it lives, and `foreignDialogOpen` hands the keyboard to
 * any dialog raised over the queue. And it stays honest with the app shell for
 * free: `claim()` calls `stopPropagation`, and this listener sits on the
 * document while `App.vue`'s sits on the window, so a key the queue takes never
 * reaches the shell's Ctrl+Z or its Home/End scrolling.
 */
function onKeydown(event) {
  if (foreignDialogOpen()) return;
  // Two queues, one key owner each, and exactly one of them is on screen. The
  // review queue's handler still declines the Mixed page through its own
  // `isBlocked` as a belt: a verdict key resolving a group the user cannot see
  // is the failure this routing exists to prevent.
  if (store.showingMixed) {
    handleMixedKeydown(event);
    return;
  }
  handleKeydown(event);
}

/**
 * Read the scope out of the URL.
 *
 * The scope lives in the query rather than in a store so a scoped queue is a
 * link that survives a reload. `useViewStore.parseRouteView` deliberately
 * returns `null` for this route (it drives no grid), so opening the queue is
 * this component's own job rather than something the route sync does for it.
 *
 * @returns {Object} the `openQueue` scope argument.
 */
function scopeFromRoute() {
  const query = route.query ?? {};
  return {
    type: query.scope ? String(query.scope) : "global",
    id: query.scope_id ?? null,
    label: query.scope_label ? String(query.scope_label) : "",
    icon: query.scope_icon ? String(query.scope_icon) : "",
  };
}

/**
 * The filter selection the URL carries, or null when it carries none.
 *
 * Each key is applied only when present, so a bare /duplicates URL keeps the
 * server's defaults rather than forcing everything off.
 *
 * @returns {Object|null}
 */
function filtersFromRoute() {
  const query = route.query ?? {};
  const filters = {};
  if (query.near !== undefined) {
    filters.near = query.near === "1" || query.near === "true";
  }
  if (query.embedding !== undefined) {
    filters.embedding = query.embedding === "1" || query.embedding === "true";
  }
  const parsed = Number(query.threshold);
  if (Number.isFinite(parsed)) filters.threshold = parsed;
  if (query.view !== undefined) filters.decided = query.view === "decided";
  // The Decided page's own filter, comma-joined so it stays ONE scalar the
  // mirror below can compare against the live URL without array identity
  // games. The store drops any id the server does not publish.
  if (query.verdict !== undefined) {
    filters.verdicts = String(query.verdict).split(",").filter(Boolean);
  }
  return Object.keys(filters).length ? filters : null;
}

// The filter selection is part of the ADDRESS: a full refresh (or a shared
// link) must restore it. Mirrored with replace(), never push() - tuning the
// tier gate is not a history step the Back button should have to unwind.
// Non-default tier/threshold values are written explicitly (near=0 included):
// "absent" always means "the server's default", never "whatever it was".
const FILTER_QUERY_KEYS = ["near", "embedding", "threshold", "view", "verdict"];

watch(
  () => [
    store.nearEnabled,
    store.embeddingEnabled,
    store.threshold,
    store.showingDecided,
    store.enabledVerdicts.join(","),
    store.policyLoaded,
    store.filtersRestored,
  ],
  () => {
    // filtersRestored is load-bearing, not belt-and-braces. The regression it
    // closes: on a full reload the policy landing flipped policyLoaded one
    // microtask BEFORE openQueue applied the URL's filters, this mirror ran on
    // that flip, saw a pristine default gate, and replaced the URL WITHOUT its
    // filter params. By the time the store adopted the params and the mirror
    // re-ran, `route.query` still showed the old query (the stripping
    // navigation was async and in flight), the `same` check passed, no
    // corrective write happened - and the params were gone for good. The gate
    // keeps the mirror silent until the store has actually adopted the URL.
    if (
      route.name !== "duplicates" ||
      !store.policyLoaded ||
      !store.filtersRestored
    ) {
      return;
    }
    const defaults = store.policyDefaults ?? {};
    const next = { ...route.query };
    for (const key of FILTER_QUERY_KEYS) delete next[key];
    const isDefault =
      store.nearEnabled === Boolean(defaults.near_enabled) &&
      store.embeddingEnabled === Boolean(defaults.embedding_enabled) &&
      (!Number.isFinite(store.threshold) ||
        store.threshold === Number(defaults.threshold));
    if (!isDefault) {
      next.near = store.nearEnabled ? "1" : "0";
      next.embedding = store.embeddingEnabled ? "1" : "0";
      if (Number.isFinite(store.threshold)) {
        next.threshold = String(store.threshold);
      }
    }
    if (store.showingDecided) {
      next.view = "decided";
      // Only a NARROWED selection is written: absent means every decision, the
      // same "absent is the default" rule the tier params follow. The gate is
      // meaningless off the Decided page, so it is never written there.
      const shown = store.enabledVerdicts;
      if (shown.length && shown.length < store.verdictRows.length) {
        next.verdict = shown.join(",");
      }
    }
    const current = route.query ?? {};
    const same = FILTER_QUERY_KEYS.every(
      (key) => (next[key] ?? null) === (current[key] ?? null),
    );
    if (!same) router.replace({ query: next });
  },
);

/**
 * Open the queue for the URL's scope, unless it is already showing it.
 *
 * The already-showing path still refreshes the counts. The badge is the one
 * piece of state that outlives this view, a keep-separate raises no WebSocket
 * event to correct it, and arriving at the destination is exactly the moment
 * its number has to be true.
 */
function syncQueueToRoute() {
  const scope = scopeFromRoute();
  const alreadyShowing =
    String(store.scopeType) === scope.type &&
    String(store.scopeId ?? "") === String(scope.id ?? "");
  if (alreadyShowing && store.groups.length) {
    store.refreshCounts();
    return;
  }
  store.openQueue({ ...scope, filters: filtersFromRoute() });
}

// A scope change is a navigation, not a remount: the component stays mounted
// when the user picks "Find duplicates in..." on a second collection.
//
// An ARRAY of getters, never a getter returning an array: the latter builds a
// fresh array each run, Vue compares it by identity, and the watcher fires on
// EVERY route.query write - including the filter mirror's own replace() above.
// On an empty queue that refire fell through syncQueueToRoute's fast path
// (which requires held rows) into a full openQueue, which force-reset the
// Decided flip the mirror was in the middle of recording: the decided rows
// flashed and were replaced by "Queue clear".
watch([() => route.query.scope, () => route.query.scope_id], () => {
  if (readOnly.value) return;
  syncQueueToRoute();
});

onMounted(() => {
  // Every read this destination makes is owner-only, so a read-only session
  // asks for none of them: the whole body is the explanatory state, and the
  // fetches would only be a row of 403s behind it.
  if (!readOnly.value) syncQueueToRoute();
  // The queue is a keyboard surface. Taking focus on mount is what makes the
  // first Enter work without the user hunting for a click target first.
  rootEl.value?.focus?.();
  nextTick(measureRowPitch);
  if (!readOnly.value) prefetchNextGroup();
  if (typeof document !== "undefined") {
    document.addEventListener("mousedown", onDocumentPointerDown);
    document.addEventListener("keydown", onKeydown);
  }
});

onBeforeUnmount(() => {
  // Leaving the destination mid-jump must stop the paging, not let it keep
  // fetching a queue nobody is looking at.
  store.cancelEndChase();
  if (flashTimer) {
    clearTimeout(flashTimer);
    flashTimer = null;
  }
  if (typeof document === "undefined") return;
  document.removeEventListener("mousedown", onDocumentPointerDown);
  document.removeEventListener("keydown", onKeydown);
});

defineExpose({ windowedGroups, tierLabel });
</script>

<style scoped>
.dq {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  position: relative;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  outline: none;
}

.dq-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  /* The shell's top band. The GRID toolbar (`.selection-bar-overlay` in
     Toolbar.vue) is the point of truth for the band's box recipe, and this
     bar copies it exactly: `height: 36px` + `box-sizing: border-box` (the
     1px bottom border sits INSIDE the 36) + zero vertical padding, with
     `align-items: center` doing the vertical work. The previous recipe here
     (`min-height: 36px` + `var(--space-2)` vertical padding, content-box)
     rendered 41px once the 32px app-wide tail buttons landed - the bars
     visibly stepped. Guardrail: Toolbar.test.js asserts both bars carry the
     same recipe. This is NOT `--bar-height` (48px): that token is the design
     manual's target for the band, and unifying the shipped 34/36/40/48/56
     onto it (or tokenising the shipped 36) is the open, UI/UX-gated
     reconciliation item in visual-language.md §5 - a bar that jumped there
     alone would just be drift in the other direction. */
  height: 36px;
  box-sizing: border-box;
  /* Split inset, each side anchored to what it must align WITH. RIGHT is
     --space-3, the grid bar's inset: the app-wide tail ([sep][Undo][Global])
     is a stable anchor only if its icons land at the identical distance from
     the edge in every view - a uniform --space-5 here put them 8px further
     left than the grid's and the tail jumped on view switches (guardrail in
     Toolbar.test.js pins the right insets equal). LEFT stays --space-5, the
     queue's own content gutter: the count headline sits flush over the
     list's rows (.qlist, .qselbar, .dq-state all inset by --space-5). */
  padding: 0 var(--space-3) 0 var(--space-5);
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  container-type: inline-size;
  /* `dqbar` for this bar's own ladder; the shared `toolbar` name is what the
     shared chrome (UndoControl, TbGlobalActions, the overflow) writes its
     scoped @container rules against, so it degrades identically here and in
     the grid bar (`selbar toolbar`). */
  container-name: dqbar toolbar;
}

.dq-tb-left,
.dq-tb-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* The shrink chain, and it is the GUARANTEE behind the ladder below
   (amendment #4). Both groups used to sit at the flex default
   (`min-width: auto`) over `white-space: nowrap` content, so neither could
   shrink: the surplus left through the right edge and took the last children
   in DOM order with it, which is exactly Settings and Stats. The left group
   yields first and its text members ellipsize; the right group never shrinks,
   so no content the left group can hold - a scope named by the user, a
   seven-digit count - can push the app-wide tail off the bar.

   NOT `overflow: hidden` on the bar: the tier popover and the ⋯ panel are
   absolutely positioned inside it and would be clipped away. */
.dq-tb-left {
  min-width: 0;
  flex: 0 1 auto;
}

.dq-tb-right {
  margin-left: auto;
  flex: 0 0 auto;
}

/* Divides the queue's identity (what it holds, which side is showing) from the
   controls that change what it holds. */
.dq-tb-sep {
  width: 1px;
  height: 18px;
  /* A 1px flex item is a shrinkable one: once the left group started yielding
     (amendment #4) the rule would give up its single pixel and disappear
     before any label did. */
  flex-shrink: 0;
  background: rgb(var(--v-theme-divider));
}

/* The size control. A fixed track width, because a slider that grows with the
   toolbar makes the same drag mean a different size on every window. */
/* space-3, not space-2: the slider's thumb overhangs both ends of its track, so
   a tighter gap has it colliding with the icon at Tiny and the label at Huge. */
.dq-size {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.dq-size-slider {
  width: 96px;
  flex: 0 0 96px;
}

/* Vuetify's compact slider still reserves a form-control's worth of height,
   which is taller than the whole band it sits in and pushed the toolbar off
   the shell's 36px strip. The track and thumb need none of it. */
.dq-size-slider :deep(.v-input__control) {
  min-height: 0;
  height: 20px;
}

/* Fixed width: the label changes on every notch, and one that resizes with its
   text drags the whole toolbar sideways as the user drags the slider. Wide
   enough for the longest rung ("Very Large"). */
.dq-size-value {
  width: 11ch;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), 0.7);
  white-space: nowrap;
}

.dq-tier-wrap {
  position: relative;
  /* Part of the tier button's shrink chain: without this the flex default
     (min-width: auto) refuses to shrink and the label wraps instead.
     `display: flex` is the other half - as a block the wrap could shrink to
     nothing while the inline-flex button inside kept its full width and drew
     straight over its neighbours (amendment #4). */
  display: flex;
  min-width: 0;
}

/* The icons flank the ellipsis, never feed it. */
.dq-btn .v-icon {
  flex-shrink: 0;
}

.dq-tier-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dq-tier-menu {
  position: absolute;
  top: calc(100% + var(--space-2));
  left: 0;
  z-index: var(--z-dropdown);
}

.dq-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  /* Structural no-wrap: under width pressure the LABEL ellipsizes on one
     line; the 27px button and the 36px band never grow. */
  white-space: nowrap;
  min-width: 0;
  height: 27px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  color: inherit;
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  transition: background var(--dur-1) var(--ease-standard);
}

.dq-btn:hover {
  background: var(--hover-wash);
}

.dq-btn--accent {
  background: rgb(var(--v-theme-accent));
  border-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}

.queue {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

/* The count leads the toolbar: it is the queue's to-do number, and the one
   thing the user checks on arrival and after every verdict. */
.qtitle {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  /* The FIRST thing to give between two rungs, and it gives from the RIGHT:
     the count leads the string, so the ellipsis eats "groups to review" and
     never the number (amendment #4). The full sentence stays in the DOM for a
     screen reader and in the tooltip. The weight buys the order - the give is
     shared out in proportion to `flex-shrink × width`, and a clipped tail on a
     sentence reads better than "Mixed stac…" on a button. */
  min-width: 0;
  flex-shrink: 6;
  overflow: hidden;
  text-overflow: ellipsis;
}

.qsub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), 0.6);
  white-space: nowrap;
}

/* The Decided toggle: same chrome as the toolbar buttons, pressed state
   while the flip side is showing. */
.qdecided {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  /* Same shrink chain as the tier button: between two rungs the label
     ellipsizes on one line rather than the button overlapping its
     neighbour. The glyph and the count never feed the ellipsis. */
  min-width: 0;
  padding: var(--space-1) var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  font-family: var(--font-ui);
  color: inherit;
  white-space: nowrap;
  transition: background var(--dur-1) var(--ease-standard);
}

.qdecided .v-icon {
  flex-shrink: 0;
}

.qdecided-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.qdecided:hover {
  background: var(--hover-wash);
}

.qdecided--on {
  background: var(--active-wash);
  border-color: rgba(var(--v-theme-accent), 0.5);
}

/* The third page's count, on ITS toggle and nowhere else. It is deliberately
   not the sidebar badge's shape: that badge means "groups to review", the one
   number in this app that has to stay trusted, and a second count wearing the
   same pill would be read as part of it. */
.qmixed-count {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  min-width: var(--badge-size);
  min-height: var(--badge-size);
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.1);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

/* Appears with the selection and goes with it, so the queue has one bar in
   its resting state and a second only while a bulk gesture is live. */
.qselbar {
  display: flex;
  align-items: center;
  padding: var(--space-2) var(--space-5) 0;
}

/* The bulk-scope chip: accent-washed so it reads as state, not decoration -
   while it shows, a verdict on any selected row takes the whole selection. */
.qselchip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border: 1px solid rgba(var(--v-theme-accent), 0.5);
  border-radius: var(--radius-pill);
  background: var(--active-wash);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
}

.qselclear {
  padding: 0;
  font: inherit;
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-accent));
}

.qselclear:focus-visible {
  outline: none;
  border-radius: var(--radius-sm);
  box-shadow: var(--focus-ring);
}

.qlist {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: 0 var(--space-5) var(--space-4);
  overflow-y: auto;
  min-height: 0;
  flex: 1;
  scrollbar-gutter: stable;
}

.qspacer {
  flex: 0 0 auto;
}

/* Pinned to the foot of the scrollport, not to the end of the content. */
.qmore {
  position: sticky;
  bottom: 0;
  align-self: center;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-divider));
  box-shadow: var(--elevation-1);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.dq-state {
  padding: var(--space-6) var(--space-5);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.6);
}

/* ── The Mixed stacks page ──────────────────────────────────────────────── */

.mixed {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.mixed-list {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  /* The queue's own content gutter, so the two pages of one destination line
     up down their left edge. */
  padding: 0 var(--space-5) var(--space-4);
  overflow-y: auto;
  scrollbar-gutter: stable;
}

/* The threshold header. STICKY inside the list's own scroller, because every
   row on this page is a verdict relative to one number and a user who has
   scrolled that number off the screen is reading the verdict without its
   premise. It is the list's own band rather than a toolbar row: the number it
   states and the rows it states it about scroll in the same box.

   `--z-sticky` is the named rung for exactly this ("sticky headers/toolbars
   inside a scroll container"), and the background is opaque `background` rather
   than a wash, because rows pass underneath it. */
.mixed-head {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  display: flex;
  align-items: center;
  gap: var(--space-5);
  flex-wrap: wrap;
  padding: var(--space-4) 0 var(--space-3);
  background: rgb(var(--v-theme-background));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

/* The count is the sentence's SUBJECT, not a figure beside a caption: "26
   stacks don't hang together at 90% similar" is one fact, and splitting it into
   two elements is what lets the two drift apart. The numerals carry the weight
   and the tabular figures; the words around them do not. */
.mixed-lede {
  flex: 1;
  min-width: 0;
  margin: 0;
  max-width: 78ch;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-background), 0.7);
}

.mixed-lede b {
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-background));
}

/* The slider caps itself at `--stats-panel-w`; this only stops it stretching
   into the sentence's line when the band is wide. */
.mixed-threshold {
  flex: 0 1 var(--stats-panel-w);
}

/* The rows, at the queue's own rhythm: this is the third QUEUE, so its rows sit
   in the same column with the same gap as the review queue's, and a user moving
   between the two pages meets one list shape rather than two. */
.mlist {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-3);
  container-type: inline-size;
  container-name: mixedlist;
}

.mixed-more {
  align-self: center;
  margin-top: var(--space-4);
}

.mixed-more:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}

.qdone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  flex: 1;
  padding: var(--space-9) var(--space-6);
  text-align: center;
  color: rgba(var(--v-theme-on-background), 0.75);
}

.qdone h3 {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-background));
}

.qdone p {
  margin: 0;
  max-width: 52ch;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

/* The one-line caption under the stacks route. Quieter than the summary above
   it: it explains a control rather than reporting the outcome. Qualified by
   `.qdone` so it outranks the `.qdone p` size above rather than losing to it. */
.qdone .qdone-hint {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), 0.6);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* ── The collapse ladder (docs/design/toolbar-responsive-decisions.md:
   amendment #4 measured it, #7 re-placed the rungs, #8 re-ordered them by
   what each one COSTS). ─────────────────────────────────────────────────

   Measured against the full bar (1408px, typical content), one item at a
   time - this is the table the order is derived from, not an argument:

     48 done this session            122px   a readout
     the size value (Very Large)      84px   a readout the slider already shows
     Auto-stack's sentence            91px   words; the tooltip keeps them
     the tier label                  137px   words; the tooltip keeps them
     both page toggles → the ⋯       232px   one extra click
     the SIZE CONTROL                212px   a control, outright

   **Cheapest loss first, and the size control is never spent.** It was being
   dropped fourth, which is what #8 fixes: it is the one thing on this bar
   with no fold destination and no tooltip standing in for it, and the queue's
   whole business is looking at pictures - a control that changes the thing
   you are looking at is not what you sell to buy 128px. Spending Auto-stack's
   sentence instead (91px, and the button still shows its count) reaches a
   NARROWER floor than the old ladder did while dropping the slider, because
   the old order spent the expensive things first.

   Every rung fires at the width the configuration above it needs:

     full                1408px
     after rung 1        1202px
     after rung 2        1111px
     after rung 3         974px
     after rung 4         906px
     after rung 5         706px   ← the floor, with the size control still on

   Below 706px the shrink chain (amendment #4: `.dq-tb-left { min-width: 0;
   flex: 0 1 auto }`) ellipsizes the count headline, which carries the bar to
   ~380px with nothing leaving it - narrower than the 586px floor the ladder
   reached by dropping the slider.

   Tuned to typical content on purpose: the same chain answers a pathological
   scope name or a seven-digit count. A ladder placed at the worst case would
   collapse the bar for everyone to protect a case the chain already holds.

   The queries are 24px under the widths the rungs are named for:
   `container-type: inline-size` queries the CONTENT box and this bar's inset
   is `0 var(--space-3) 0 var(--space-5)`. Re-measure by stepping the
   container, never by reasoning about it - both times this ladder was wrong,
   a number had been argued rather than read. ───────────────────────────── */
.dq-auto-short,
.dq-auto-count {
  display: none;
}

/* The ⋯ trigger appears with the first control that folds into it, and only
   this bar knows that width (the component's own default is hidden). */
.dq-overflow {
  display: none;
}

/* Auto-stack's full label shares the tier label's latent wrap; same cure. */
.dq-auto-full {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Rung 1. The two readouts that report rather than control: the session tally
   (the durable record is the Decided page) and the slider's word for a
   position the slider already shows. Buys 206px. */
@container dqbar (max-width: 1384px) {
  .qsub {
    display: none;
  }
  .dq-size-value {
    display: none;
  }
}

/* Rung 2. Auto-stack drops its sentence for "Auto-stack N" - the cheapest
   word on the bar, because the count it keeps IS the sentence's subject and
   the full form survives as the tooltip and the accessible name. Buys 91px,
   and spending it here is what keeps the tier label and both toggle labels
   alive for another 200-400px. */
@container dqbar (max-width: 1178px) {
  .dq-auto-full {
    display: none;
  }
  .dq-auto-short {
    display: inline;
  }
}

/* Rung 3. The tier trigger compresses to [filter icon][chevron], the grid
   Filter trigger's shipped grammar, with its title and aria-label carrying
   the name. Buys 137px. */
@container dqbar (max-width: 1087px) {
  .dq-tier-label {
    display: none;
  }
}

/* Rung 4. Auto-stack keeps its flash and its count and drops the verb. Buys
   68px. */
@container dqbar (max-width: 950px) {
  .dq-auto-short {
    display: none;
  }
  .dq-auto-count {
    display: inline;
  }
}

/* Rung 5, and the last: the page toggles fold into the ⋯, which appears in
   their place. The biggest single bite on the bar, taken when the bar has
   actually run out rather than 400px before. A toggle showing "Back to
   review" never carries the fold class, so the way out of a sub-page stays on
   the bar and compresses to its arrow, which says what it does without a
   label. Buys 200px net of the ⋯ itself. */
@container dqbar (max-width: 882px) {
  .dq-fold-906 {
    display: none;
  }
  .dq-overflow {
    display: flex;
  }
  .qdecided-label {
    display: none;
  }
}

/* The floor's density step. Below the shared ladder's last rung the bar buys
   its remaining width from the runs' own gaps rather than from another
   control, so the worst case (a scope in force AND exact matches to
   auto-stack) still fits with nothing leaving the bar.

   The groups only: `.dq-toolbar` IS the query's container, and a container
   query styles a container's DESCENDANTS, never the container itself - a rule
   for the bar's own gap or inset here would be dead. Its 36px band and its
   insets are pinned to the grid bar's anyway (guardrail in Toolbar.test.js),
   so they are not this ladder's to spend. */
@container toolbar (max-width: 420px) {
  .dq-tb-left,
  .dq-tb-right {
    gap: var(--space-2);
  }
}
</style>
