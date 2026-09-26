<template>
  <div class="msg" role="region" aria-label="Workflow sets">
    <p class="visually-hidden" role="status">{{ announcement }}</p>

    <p v-if="store.setsLoading && !store.setsLoaded" class="msg__state">
      Reading which models have run together…
    </p>
    <p v-else-if="store.setsError" class="msg__state" role="alert">
      {{ store.setsError }}
    </p>
    <!-- Two empty states, deliberately distinct, for the reason the row list
         keeps three: "you filtered everything out" is one click from fixed and
         "no picture records its models" is not, and stating the second when the
         first is true tells the reader something false about their library. -->
    <div v-else-if="nothingShown && store.activeCount" class="msg__state">
      <p class="msg__lead">No workflow set matches these filters.</p>
      <p>
        {{ store.workflowSets.combinations.length.toLocaleString() }} set{{
          store.workflowSets.combinations.length === 1 ? "" : "s"
        }}
        are being left out by Show.
      </p>
      <AppButton @click="store.resetFilters()">Reset filters</AppButton>
    </div>
    <div v-else-if="nothingShown" class="msg__state">
      <p class="msg__lead">
        No picture in this library records the models it used.
      </p>
      <p>
        A set is read off a recipe, and a recipe arrives with a picture that
        carries its workflow. Until one does, there is nothing here to group —
        which says nothing about the models on the shelf.
      </p>
    </div>

    <div v-else class="msg__scroll">
      <div
        ref="gridEl"
        class="msg__grid"
        role="treegrid"
        aria-label="Workflow sets"
        aria-multiselectable="true"
        :style="{
          '--wf-columns': columns,
          '--wf-column-min': `${COLUMN_MIN}px`,
          '--wf-gap': `${COLUMN_GAP}px`,
        }"
        @keydown="onKeyDown"
      >
        <template v-for="(entry, index) in flatRows" :key="entry.id">
          <!-- The panel is drawn once, in the slot its FIRST member holds; the
               rest of the member entries are index space only. -->
          <ModelSetPanel
            v-if="entry.kind === 'member' && entry.memberIndex === 0"
            :panel-id="PANEL_ID"
            :name="openName"
            :members="openMembers"
            :view="store.view.trayView"
            :columns="trayColumns"
            :column-index="openColumnIndex"
            :cursor-key="cursorKey"
            :gap="COLUMN_GAP"
            :selected-ids="store.selectedIds"
            :selectable-ids="store.setGridModelIds"
            @close="closePanel"
            @view="(value) => store.setView({ trayView: value })"
            @pick="openWorksWith"
            @select="onMemberClick"
            @menu="onMemberMenu"
          />

          <div
            v-else-if="entry.kind === 'card'"
            class="msg__row"
            role="row"
            aria-level="1"
            :aria-expanded="String(store.openSetKey === entry.key)"
            :aria-controls="
              store.openSetKey === entry.key ? PANEL_ID : undefined
            "
            :aria-owns="
              store.openSetKey === entry.key ? memberRowIds : undefined
            "
            :aria-posinset="entry.cardIndex + 1"
            :aria-setsize="store.setGroups.length"
            :aria-selected="
              selectable(entry.headId)
                ? String(store.isSelected(entry.headId))
                : undefined
            "
            :tabindex="index === cursorIndex ? 0 : -1"
            :data-key="entry.key"
            @click="onRowClick(entry, $event)"
            @dblclick="toggle(entry)"
            @contextmenu="onRowMenu(entry, $event)"
          >
            <div class="msg__cell" role="gridcell">
              <ModelSetCard
                :card="entry.card"
                :expanded="store.openSetKey === entry.key"
                :selected="store.isSelected(entry.headId)"
                :panel-id="store.openSetKey === entry.key ? PANEL_ID : ''"
                @toggle="store.toggleSet(entry.key)"
              />
            </div>
          </div>
        </template>
      </div>

      <!-- The models no recipe names, drawn rather than omitted. Outside the
           treegrid: it is not a set, so it is not a row in a grid of sets, and
           putting it in one would make it the last thing the arrow keys reach.
           Dashed and worded so it cannot be read as a verdict. -->
      <section v-if="store.noSetRows.length" class="msg__ghost">
        <h3 class="msg__ghost-title">
          <v-icon size="18">mdi-help-circle-outline</v-icon>
          In no set
          <span class="msg__ghost-count num"
            >{{ store.noSetRows.length.toLocaleString() }}
            {{ store.noSetRows.length === 1 ? "model" : "models" }}</span
          >
        </h3>
        <!-- The exact claim, and no more. "No recipe names them" would be
             wrong: a recipe on this machine may name one from another library,
             or from pictures since deleted. What is true is the narrower thing,
             and it is worded so it cannot be read as a verdict. -->
        <p class="msg__ghost-note">
          No kept picture in this library was made with them, so there is no set
          to draw. That is all it means — nothing here rules out what they work
          with, and a model may well have been used somewhere this library
          cannot see.
        </p>
        <AppButton
          variant="outline"
          size="sm"
          icon-left="format-list-bulleted"
          @click="store.setView({ groupBy: 'none' })"
          >List the {{ store.noSetRows.length.toLocaleString() }}</AppButton
        >
      </section>
    </div>
  </div>
</template>

<script setup>
/**
 * The model shelf's Workflow set axis, as a card grid (#1438).
 *
 * **A card is one base model, and its tray holds the models that have run with
 * it.** One card per checkpoint - or per diffusion file, where a graph loads one
 * instead - and ▸ opens the union of everything it has co-occurred with, as cards
 * or as a comparison list. The card's SHAPE and the tray's are the shipped
 * workflow grid's, because a reader should not have to learn a second card or a
 * second switch; the components are this screen's own, because that grid's card
 * and panel have grown a vocabulary ("Stack of 3 workflows", "Saved recipes", a
 * menu of workflow verbs) that would be wrong words and dead controls on a set.
 * See `ModelSetCard.vue` and `ModelSetPanel.vue`.
 *
 * **A union is not reproducible, and the tray says so.** Two files in one tray
 * may never have run together; the exact combinations live behind `Works with`,
 * which reads the per-recipe evidence rather than the union.
 *
 * **A card stands for ONE model: the base model it is named after.** That is the
 * whole of what makes this screen safe to act on. The shelf's selection is by
 * `model.id` and carries verbs that destroy bytes, so a card standing for its
 * whole SET would put a shared VAE behind a Delete aimed at a checkpoint. It
 * stands for its head instead - the file whose name, kind and mark the card
 * already draws - and the other members of the set are selected one at a time in
 * the tray, where each row is one model. Nothing here ever selects a SET.
 *
 * **A RUN is the one thing a card can stand for besides one file, and it has to
 * be.** `shownModelIds` fans a run out into its members, so a card can be named
 * after step 2 of 6 with nothing on it saying so; the shelf holds runs atomic
 * everywhere (`services/stack_membership`), and letting a card mean one step
 * would let Forget destroy half of one from a screen that draws no runs. The
 * store folds them instead - `setGridRows` pulls a whole run in the moment any
 * step of it is on a card - so such a card selects the run, the bar counts it as
 * one row and states its whole size, and the verbs write all of it.
 *
 * Everything else is the row list's own contract, reached through the same store:
 * click replaces, Ctrl/Cmd+click toggles, Shift+click takes the range in DRAWN
 * order, Space toggles, Shift+arrow extends, F2 renames what the cursor is on,
 * and right-click (and the Menu key, and Shift+F10) opens the shelf's full verb
 * menu after making the row under the pointer the selection. The verb bar and
 * the destructive keys are `ModelShelf.vue`'s, unchanged: this view only feeds
 * them.
 *
 * ONE FLAT LIST, exactly as `WorkflowsView` builds one: the cards and, while a
 * stack is open, its members are one index space, so the cursor crosses the
 * panel boundary with the same `index ± columns` arithmetic. The padding holes
 * are what keep `index % columns` naming the column a row is drawn in.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";

import ModelSetPanel from "../panels/ModelSetPanel.vue";
import AppButton from "../widgets/AppButton.vue";
import ModelSetCard from "../widgets/ModelSetCard.vue";

/**
 * The two numbers the column count is derived from, and the only copy of them.
 *
 * The workflows grid's own figures, because the card is the same card: written
 * onto the grid as custom properties rather than read back out of the sheet, so
 * the arithmetic here and the track the browser paints are the same two numbers
 * by construction.
 */
const COLUMN_MIN = 240;
const COLUMN_GAP = 12;

const PANEL_ID = "msg-set-panel";

const emit = defineEmits(["works-with", "menu", "rename"]);

const store = useModelShelfStore();

const gridEl = ref(null);
const columns = ref(1);
// The cursor is an entry id, not an index: `flatRows` is rebuilt by a fold
// change, by a stack opening and by one closing, and an index held across any
// of those names a different card - or, when the list shrinks, none, which
// takes the grid's only tab stop with it.
const cursorId = ref("");
const announcement = ref("");

const nothingShown = computed(
  () => !store.setGroups.length && !store.noSetRows.length,
);

/** The open group, or null. */
const openGroup = computed(
  () => store.setGroups.find((group) => group.key === store.openSetKey) ?? null,
);

/**
 * The open group's models, head first, each flagged if it is the one the set is
 * named after - which is what the tray marks rather than re-deriving.
 */
const openMembers = computed(() => {
  const group = openGroup.value;
  if (!group) return [];
  return group.models.map((model) => ({
    ...model,
    head: model.id === group.head?.id,
  }));
});

const openName = computed(() => openGroup.value?.card.name || "Set");

/**
 * How many columns the TRAY draws its member cards in.
 *
 * One column in List, whatever the grid above is doing: the rows are full-width,
 * and the flat list's column arithmetic has to agree with what is painted or Down
 * lands on the wrong row.
 */
const trayColumns = computed(() =>
  store.view.trayView === "list" ? 1 : columns.value,
);

/**
 * The cards, with the open stack's members spliced in at its row's end.
 *
 * `{kind: "card"|"member"|"hole", id, key, ...}`. `hole` pads the member block
 * out to whole rows, so every card after the panel keeps naming the column it
 * is drawn in.
 */
const flatRows = computed(() => {
  const cards = store.setGroups.map((group, cardIndex) => ({
    kind: "card",
    id: `card:${group.key}`,
    key: group.key,
    card: group.card,
    // The model the card IS, which is what a selection or a verb is aimed at.
    headId: group.head?.id ?? null,
    cardIndex,
  }));
  const openKey = store.openSetKey;
  if (!openKey) return cards;
  const at = cards.findIndex((entry) => entry.key === openKey);
  if (at < 0) return cards;
  const cols = Math.max(1, columns.value);
  const rowEnd = (Math.floor(at / cols) + 1) * cols;
  // The stack's OWN row is padded to whole first, not just the member block: a
  // stack in the last, incomplete row would otherwise splice the block in at a
  // length that is not a multiple of `cols`, and from there the column
  // arithmetic stops describing the screen.
  const head = cards.slice(0, rowEnd);
  while (head.length < rowEnd) {
    head.push({ kind: "hole", id: `hole:head:${head.length}` });
  }
  const block = openMembers.value.map((member, memberIndex) => ({
    kind: "member",
    id: `member:${member.id}`,
    key: String(member.id),
    modelId: member.id,
    memberIndex,
  }));
  // The TRAY's own column count, which is 1 in List: the padding has to make the
  // member block a whole number of the rows that are actually painted, or the
  // cursor's `index ± columns` walks out of the tray.
  const trayCols = Math.max(1, trayColumns.value);
  const padded = Math.ceil(Math.max(block.length, 1) / trayCols) * trayCols;
  while (block.length < padded) {
    block.push({ kind: "hole", id: `hole:block:${block.length}` });
  }
  return [...head, ...block, ...cards.slice(rowEnd)];
});

/** The open card's column, for the panel's notch. */
const openColumnIndex = computed(() => {
  const at = flatRows.value.findIndex(
    (entry) => entry.kind === "card" && entry.key === store.openSetKey,
  );
  return at < 0 ? 0 : at % Math.max(1, columns.value);
});

/** Where the cursor is now. Always a real row: it falls back to the first. */
const cursorIndex = computed(() => {
  const at = flatRows.value.findIndex((entry) => entry.id === cursorId.value);
  return at >= 0 ? at : (firstStop(0, 1) ?? 0);
});

/** The cursor's key when it is inside the panel, else "". */
const cursorKey = computed(() => {
  const entry = flatRows.value[cursorIndex.value];
  return entry?.kind === "member" ? entry.key : "";
});

/**
 * The member rows' DOM ids, for the open card's `aria-owns`.
 *
 * The panel is a full-width band after the LAST card of the open card's row, so
 * in DOM order the member rows follow whichever card ends that row. `aria-owns`
 * re-parents them in the accessibility tree without moving a pixel, which is
 * what makes the treegrid true.
 */
const memberRowIds = computed(() =>
  openMembers.value.map((member) => `${PANEL_ID}-row-${member.id}`).join(" "),
);

// ── Columns from the real container width ─────────────────────────────────

function measure() {
  const width = gridEl.value?.clientWidth ?? 0;
  const next = Math.max(
    1,
    Math.floor((width + COLUMN_GAP) / (COLUMN_MIN + COLUMN_GAP)),
  );
  // Written only when it CHANGED. `columns` decides the height, the height can
  // decide whether the scroller has a scrollbar, and the scrollbar decides
  // `clientWidth` - so an unconditional write at a boundary width feeds the
  // observer its own output and can oscillate (#1479 review). The guard breaks
  // the loop: the same count re-observed is not a change.
  if (next !== columns.value) columns.value = next;
}

let observer = null;
watch(gridEl, (element) => {
  observer?.disconnect();
  observer = null;
  if (!element || typeof ResizeObserver === "undefined") return;
  observer = new ResizeObserver(measure);
  observer.observe(element);
  measure();
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = null;
});

// The grid is mounted by the axis, so this is where the read is asked for: the
// store's own guard makes the repeat free.
store.loadWorkflowSets();

/**
 * A stack opening or closing announces itself: every row below it moves, and a
 * reader who is not on that card hears nothing otherwise.
 */
watch(
  () => store.openSetKey,
  (key, previous) => {
    if (key) {
      announcement.value = `${openName.value} opened, ${openMembers.value.length} models`;
      return;
    }
    announcement.value = `${previous ? "Set" : ""} closed`.trim();
  },
);

function toggle(entry) {
  if (entry?.kind === "card") store.toggleSet(entry.key);
}

/**
 * Close the panel and put the cursor back on the card that opened it.
 *
 * Esc and the panel's own Close come through here. ▸ does not: that is a click
 * on a card, and the cursor belongs where the reader left it.
 */
function closePanel() {
  const key = store.openSetKey;
  if (!key) return;
  store.toggleSet(key);
  const at = flatRows.value.findIndex(
    (entry) => entry.kind === "card" && entry.key === key,
  );
  if (at >= 0) moveCursor(at);
}

/**
 * One file inside a combination: what else has this run with?
 *
 * Emitted rather than answered here. `ModelShelf.vue` mounts the one dialog for
 * both entry points - this grid and the row list's context menu - because two
 * mounted copies with a ref each is two dialogs that can disagree about which
 * is open, and this component is the shelf's own child.
 */
function openWorksWith(file) {
  if (file) emit("works-with", file);
}

// ── Selection ─────────────────────────────────────────────────────────────

/**
 * The model one flat entry stands for: a card's base model, a tray row's file.
 *
 * `null` for a hole, and for the rare card whose base model is not a row on this
 * shelf - a combination can name a file from a block this session never fetched,
 * and there is no row to rename, move or delete for one of those.
 */
function modelIdOf(entry) {
  if (entry?.kind === "card") return entry.headId;
  if (entry?.kind === "member") return entry.modelId;
  return null;
}

/**
 * Is there a shelf row behind this id for a verb to write?
 *
 * A row that answers `false` still takes the cursor from a click - it is a place
 * on the screen - but it takes no selection, carries no `aria-selected` and
 * opens no verb menu, because there is nothing for a verb to write.
 */
function selectable(id) {
  return id != null && store.setGridModelIds.has(id);
}

/**
 * The drawn occurrences, in order, which is what a Shift-range spans.
 *
 * Read off `flatRows`, so a range that crosses an open tray takes the cards
 * before it, the tray's own rows, and the cards after - the order on screen and
 * not the order the payload arrived in. Occurrences stay distinct: a set's
 * head is drawn twice while its tray is open, and a Shift-range starts from the
 * occurrence the reader clicked. The store deduplicates model ids only after it
 * has sliced that occurrence range.
 */
const orderedEntries = computed(() =>
  flatRows.value
    .map((entry) => ({ id: modelIdOf(entry), occurrence: entry.id }))
    .filter((entry) => selectable(entry.id)),
);

/** Click, Ctrl+click, Shift+click - the row list's own three gestures. */
function select(id, event) {
  if (!selectable(id)) return;
  const ctrl = Boolean(event?.ctrlKey || event?.metaKey);
  const entry = flatRows.value[cursorIndex.value];
  store.selectFromClick(
    id,
    { ctrl, shift: event?.shiftKey },
    orderedEntries.value,
    entry?.id,
  );
}

/**
 * A card was clicked: it takes the cursor, and it takes the selection.
 *
 * The cursor moves either way - a click inside the card is still a statement
 * about where the reader is - but a click the card's own ▸ owns stops there.
 */
function onRowClick(entry, event) {
  cursorId.value = entry.id;
  if (targetOwnsTheGesture(event)) return;
  selectEntry(entry, event);
}

/**
 * Right-click a card or a tray row: the shelf's full verb inventory, at the
 * pointer.
 *
 * The file-manager rule the row list already follows: right-clicking something
 * that is NOT selected selects it and acts on it alone; right-clicking one of
 * forty selected models leaves the forty alone. `ModelShelf.vue` owns the menu,
 * because there is one `ShelfSelectionBar` and two views feeding it.
 */
function openMenu(id, x, y) {
  if (!selectable(id)) return false;
  if (!store.isSelected(id)) select(id, {});
  emit("menu", { x, y });
  return true;
}

function onRowMenu(entry, event) {
  cursorId.value = entry.id;
  if (openMenu(modelIdOf(entry), event.clientX, event.clientY)) {
    event.preventDefault();
  }
}

/** A tray row was clicked. The tray reports the member; the cursor follows it. */
function onMemberClick({ member, event }) {
  cursorId.value = `member:${member.id}`;
  selectEntry(flatRows.value[cursorIndex.value], event);
}

function selectEntry(entry, event) {
  const id = modelIdOf(entry);
  if (!selectable(id)) return;
  const ctrl = Boolean(event?.ctrlKey || event?.metaKey);
  store.selectFromClick(
    id,
    { ctrl, shift: event?.shiftKey },
    orderedEntries.value,
    entry?.id,
  );
}

function onMemberMenu({ member, event }) {
  cursorId.value = `member:${member.id}`;
  if (openMenu(member.id, event.clientX, event.clientY)) {
    event.preventDefault();
  }
}

// ── The roving cursor ─────────────────────────────────────────────────────

/** First index at or after `index` that is a real row, travelling in `step`. */
function firstStop(index, step) {
  for (let i = index; i >= 0 && i < flatRows.value.length; i += step) {
    const kind = flatRows.value[i]?.kind;
    if (kind === "card" || kind === "member") return i;
  }
  return null;
}

/**
 * The row above or below `index`, keeping its column.
 *
 * Steps by WHOLE ROWS over the padding holes, never one index at a time: a
 * ragged last member row leaves holes in the middle of the index space, and a
 * linear scan walks out of the column the reader is travelling down.
 */
function verticalStop(index, cols, direction) {
  const total = flatRows.value.length;
  for (let i = index; i >= 0 && i < total; i += direction * cols) {
    const kind = flatRows.value[i]?.kind;
    if (kind === "card" || kind === "member") return i;
  }
  return firstStop(Math.min(Math.max(index, 0), total - 1), direction);
}

/**
 * The DOM row for one flat entry.
 *
 * Scoped by kind, not by key: a stack's seed holds the same key twice, once in
 * the grid and once in the panel. A key is arbitrary text, so it is compared
 * rather than spliced into a selector.
 */
function rowElement(entry) {
  if (!entry) return undefined;
  const selector = entry.kind === "member" ? ".msp__member" : ".msg__row";
  return Array.from(gridEl.value?.querySelectorAll(selector) ?? []).find(
    (element) => element.dataset.key === entry.key,
  );
}

/**
 * Put the cursor on whichever real row `index` names, if there is one.
 *
 * `extend` is Shift+arrow: the cursor moves AND the range grows, which is the
 * keyboard's Shift+click. Without it the arrows only move, which is the roving
 * contract - a reader can walk a grid of cards without arming a verb against
 * every one they pass.
 */
function moveCursor(index, extend = false) {
  const entry = flatRows.value[index];
  if (!entry || entry.kind === "hole") return;
  cursorId.value = entry.id;
  if (extend) selectEntry(entry, { shiftKey: true });
  nextTick(() => rowElement(entry)?.focus());
}

/**
 * What an event inside the grid must NOT be treated as a grid gesture.
 *
 * `onKeyDown` is on the grid, so it sees every press inside it, including the
 * ones aimed at the panel's own buttons - *Don't fold* and Close, which are real
 * focusable controls. Unguarded, Enter on *Don't fold* was `preventDefault`ed
 * and closed the stack instead of pressing the button, while Space still
 * activated it: the same button answering two keys differently (#1479 review).
 *
 * **A CLICK asks the same question and needs the same answer.** The card's ▸ is
 * a button inside a row that now selects, so its click bubbles out of the button
 * and into the row: pressing "show me what is in this set" would replace the
 * reader's selection with that card and arm a model they never pointed at for a
 * verb with no undo. A disclosure control is navigation, not a pick.
 *
 * The row itself is the tab stop and is a plain div, so a gesture the grid IS
 * meant to own never matches this.
 */
function targetOwnsTheGesture(event) {
  return Boolean(
    event.target?.closest?.(
      "button, a[href], input, select, textarea, [role='button']",
    ),
  );
}

/**
 * The context-menu key, which a row owes as much as it owes the right button.
 *
 * Two spellings, because two platforms spell it differently and a browser
 * reports whichever the keyboard sent: the dedicated Menu key, and Shift+F10.
 * The row list's own test, restated rather than imported for two lines.
 */
function isMenuKey(event) {
  return event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey);
}

/**
 * Up/Down pressed in the open tray's header bar (the Grid/List switch, Close).
 *
 * The bar sits between the card and its members, so Down enters the tray at its
 * first member and Up returns to the card, wherever the cursor last was: a
 * mouse click on the switch does not move it. Returns true when handled.
 */
function headerStep(event) {
  const down = event.key === "ArrowDown";
  if (!down && event.key !== "ArrowUp") return false;
  if (!event.target?.closest?.(".msp__header")) return false;
  event.preventDefault();
  const rows = flatRows.value;
  moveCursor(
    down
      ? rows.findIndex((e) => e.kind === "member")
      : rows.findIndex((e) => e.kind === "card" && e.key === store.openSetKey),
  );
  return true;
}

function onKeyDown(event) {
  if (headerStep(event)) return;
  if (targetOwnsTheGesture(event)) return;
  const entry = flatRows.value[cursorIndex.value];
  const cols = Math.max(1, columns.value);
  const extend = event.shiftKey;
  switch (event.key) {
    case "ArrowRight":
      event.preventDefault();
      moveCursor(firstStop(cursorIndex.value + 1, 1), extend);
      return;
    case "ArrowLeft":
      event.preventDefault();
      moveCursor(firstStop(cursorIndex.value - 1, -1), extend);
      return;
    case "ArrowDown":
      event.preventDefault();
      moveCursor(
        entry?.kind === "member" && store.view.trayView === "list"
          ? firstStop(cursorIndex.value + 1, 1)
          : verticalStop(cursorIndex.value + cols, cols, 1),
        extend,
      );
      return;
    case "ArrowUp":
      event.preventDefault();
      moveCursor(
        entry?.kind === "member" && store.view.trayView === "list"
          ? firstStop(cursorIndex.value - 1, -1)
          : verticalStop(cursorIndex.value - cols, cols, -1),
        extend,
      );
      return;
    case " ":
      // Space toggles, exactly as it does on a row: the keyboard's Ctrl+click,
      // so a reader can build a selection without a pointer. Enter is NOT this
      // key here - it already opens a tray and asks a model what it has run
      // with, and those are the gestures this grid is read with.
      //
      // Refused before it is swallowed: a row with no shelf model behind it has
      // no answer to Space, and `preventDefault` on a press nothing then handles
      // takes the page's own scroll away for nothing.
      if (!selectable(modelIdOf(entry))) return;
      event.preventDefault();
      select(modelIdOf(entry), { ctrlKey: true });
      return;
    case "F2":
      // The rename key, which the verb menu advertises with an `F2` keycap - so
      // it has to answer here or the cap is a lie on this screen. It renames the
      // model under the CURSOR, as it does on a row: the press makes that one
      // the selection first, rather than renaming whichever of forty the bar
      // happens to hold.
      if (!selectable(modelIdOf(entry))) return;
      event.preventDefault();
      select(modelIdOf(entry), {});
      emit("rename");
      return;
    case "Enter":
      event.preventDefault();
      // A card opens its tray; a member in that tray answers "what else has this
      // model run with". **Now that a tray row IS one model, the keyboard reaches
      // every one of them** - the earlier shape had a row per combination and
      // could only offer the file it was named after, which was a real gap
      // (#1479 review). The member cards' own name buttons stay at
      // `tabindex="-1"` because the grid owns Tab; this is how a keyboard gets
      // there.
      if (entry?.kind === "card") {
        store.toggleSet(entry.key);
      } else if (entry?.kind === "member") {
        openWorksWith(openMembers.value[entry.memberIndex]);
      }
      return;
    case "Escape":
      // An open tray first, and the press is stopped so it cannot ALSO reach
      // the shelf's window listener and clear the selection on the way out:
      // one Escape, one thing undone. With no tray open it is let through, and
      // clearing the selection is what it means.
      if (store.openSetKey) {
        event.preventDefault();
        event.stopPropagation();
        closePanel();
      }
      return;
    default:
      if (isMenuKey(event)) {
        // Over the row's own box, since there is no pointer to anchor to - the
        // same offset the row list opens its menu at.
        const box = rowElement(entry)?.getBoundingClientRect?.();
        const x = box ? box.left + 24 : 0;
        const y = box ? box.bottom : 0;
        if (openMenu(modelIdOf(entry), x, y)) event.preventDefault();
      }
  }
}
</script>

<style scoped>
.msg {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
}

/* The scroller carries the padding so the grid below is the bare track:
   `measure()` reads the grid's `clientWidth`, which INCLUDES its own padding,
   and a padded grid would be measured wider than the space the columns have. */
.msg__scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-4);
}

.msg__grid {
  display: grid;
  grid-template-columns: repeat(var(--wf-columns), minmax(0, 1fr));
  gap: var(--wf-gap);
  align-items: start;
}

.msg__cell {
  border-radius: var(--radius-md);
}

.msg__state {
  margin: 0;
  padding: var(--space-6) var(--space-5);
  max-width: 68ch;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.msg__lead {
  margin: 0 0 var(--space-3);
  font-size: var(--text-md);
  color: rgb(var(--v-theme-on-surface));
}

/* Dashed, because it is the one card that stands for an absence. Not in the
   grid's own track: a set it is not, and it must not be the last thing the
   arrow keys walk into. */
.msg__ghost {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-3);
  margin-top: var(--space-5);
  padding: var(--space-4) var(--space-5);
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
}

/* The shipped section-label recipe - `--text-2xs`, semibold, `--tracking-label`,
   uppercase, secondary ink - and not `--text-sm` body weight. This heading names
   a group exactly as the Works with dialog's own headings do, and this PR was
   using two recipes for one job (#1479 design note). The count stays in
   sentence case: a figure is not a label. */
.msg__ghost-title {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.msg__ghost-count {
  letter-spacing: normal;
  text-transform: none;
}

.msg__ghost-note {
  margin: 0;
  max-width: 68ch;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
