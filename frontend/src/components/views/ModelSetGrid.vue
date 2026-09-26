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
    <div
      v-else-if="nothingShown && store.activeCount && !hasHandMade"
      class="msg__state"
    >
      <p class="msg__lead">No workflow set matches these filters.</p>
      <p>
        {{ store.workflowSets.combinations.length.toLocaleString() }} set{{
          store.workflowSets.combinations.length === 1 ? "" : "s"
        }}
        are being left out by Show.
      </p>
      <AppButton @click="store.resetFilters()">Reset filters</AppButton>
    </div>
    <div v-else class="msg__scroll">
      <!-- No evidence to draw, said above the (empty) grid. The toolbar's New
           set button still works: making a set by hand is exactly what an
           owner with no recorded pictures can still do (#1520, #1575). -->
      <div
        v-if="nothingShown && !hasHandMade"
        class="msg__state msg__state--inline"
      >
        <p class="msg__lead">
          No picture in this library records the models it used.
        </p>
        <p>
          A set is read off a recipe, and a recipe arrives with a picture that
          carries its workflow. Until one does, there is nothing here to group —
          which says nothing about the models on the shelf. You can still make a
          set by hand with <strong>New set</strong> in the toolbar.
        </p>
      </div>
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
          <ModelSetSlotsPanel
            v-if="entry.kind === 'slot' && entry.first && openHand"
            :panel-id="PANEL_ID"
            :set="openHand.set"
            :name="openName"
            :columns="columns"
            :column-index="openColumnIndex"
            :cursor-key="cursorKey"
            :gap="COLUMN_GAP"
            :selected-ids="store.selectedIds"
            :selectable-ids="store.setGridModelIds"
            :marks="marksById"
            :can-fill-from-set="fillSetItems.length > 0"
            :can-fill-from-pictures="fillPictureItems.length > 0"
            :base-offer="baseOffer"
            @set-base="setCheckpointBase"
            @dismiss-base="store.checkpointAdded = null"
            @close="closePanel"
            @select="onSlotClick"
            @menu="onSlotMenu"
            @remove="removeMember"
            @add="({ slotId, el }) => openChooser('slot', el, slotId)"
            @fill="({ mode, el }) => openChooser(mode, el)"
            @pick="openWorksWith"
            @merge="mergeOffer()"
            @keep-separate="keepSeparate()"
            @add-ghost="(ghost) => mergeOffer([ghost])"
            @offer-again="store.offerMergeAgain(openHand.set)"
            @cursor="(key) => moveToKey(key)"
          />

          <ModelSetPanel
            v-else-if="entry.kind === 'member' && entry.memberIndex === 0"
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
            :marks="marksById"
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
            :aria-setsize="cardCount"
            :aria-selected="
              entry.hand
                ? String(store.selectedSetIds.has(entry.setId))
                : selectable(entry.headId)
                  ? String(cardSelected(entry))
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
                :selected="
                  entry.hand
                    ? store.selectedSetIds.has(entry.setId)
                    : cardSelected(entry)
                "
                :panel-id="store.openSetKey === entry.key ? PANEL_ID : ''"
                @toggle="store.toggleSet(entry.key)"
                @offer="openOffer(entry.setId)"
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

    <WorkflowSetChooser
      :open="Boolean(chooser)"
      :target="chooser?.target"
      :title="chooserTitle"
      :note="chooserNote"
      :sections="chooserSections"
      :filterable="chooser?.mode === 'slot'"
      :query="chooserQuery"
      :pick="chooser?.mode === 'slot'"
      :placeholder="chooserPlaceholder"
      :empty-text="chooserEmpty"
      @update:query="(value) => (chooserQuery = value)"
      @add="addChosen"
      @close="closeChooser"
    />
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
 * the tray, where each row is one model. An EVIDENCE card never selects a set.
 *
 * **A hand-made card is the exception, and it is safe for the same reason**
 * (#1520): it selects its SET into a separate selection (`selectedSetIds`)
 * whose verbs - Rename, Delete set - touch no file, and taking a set drops any
 * selected files and vice versa, so no pill ever holds both vocabularies.
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

import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { assignmentRing } from "../../utils/modelShelf";
import {
  fillFromPictures,
  fillFromSets,
  handMadeName,
  SET_SLOTS,
  setCheckpoint,
  setSlots,
  slotSuggestions,
} from "../../utils/workflowSets";

import ModelSetPanel from "../panels/ModelSetPanel.vue";
import ModelSetSlotsPanel from "../panels/ModelSetSlotsPanel.vue";
import WorkflowSetChooser from "../panels/WorkflowSetChooser.vue";
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

const emit = defineEmits([
  "works-with",
  "menu",
  "rename",
  "set-menu",
  "rename-set",
]);

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

const hasHandMade = computed(() => store.handMadeGroups.length > 0);

/** Every card on the grid, hand-made first: what `aria-setsize` counts. */
const cardCount = computed(
  () => store.handMadeGroups.length + store.setGroups.length,
);

/** `model.id` → `icon_sha256`, so a hand-made card and tile wear real marks. */
const iconsById = computed(
  () =>
    new Map(
      store.rows
        .filter((row) => row.icon_sha256)
        .map((row) => [row.id, row.icon_sha256]),
    ),
);

const entityLists = useEntityListsStore();

/**
 * The shelf's own mark for every on-shelf model in the OPEN tray, by id:
 * `{row, ring, style}`, exactly what a shelf row hands `ModelMark`.
 *
 * So a LoRA assigned to a person wears that person's face and ring here as it
 * does in the row list, instead of a generated initials square - the tray is
 * where a whole set's models get assigned, and it could not show who they
 * already belong to. Only the open tray's members, not the whole shelf: a ring
 * is a lookup per model and a closed tray draws none.
 */
const marksById = computed(() => {
  const ids = new Set(
    (openHand.value?.models ?? openGroup.value?.models ?? []).map((m) => m.id),
  );
  const marks = new Map();
  for (const row of store.rows) {
    if (!ids.has(row.id)) continue;
    const ring = assignmentRing(row.attachments, {
      characters: entityLists.characters,
      sets: entityLists.pictureSets,
    });
    marks.set(row.id, {
      row,
      ring,
      style: ring.hue ? { "--mmark-ring": ring.hue } : {},
    });
  }
  return marks;
});

/** The open hand-made group, or null. */
const openHand = computed(
  () =>
    store.handMadeGroups.find((group) => group.key === store.openSetKey) ??
    null,
);

/** The open evidence group, or null. */
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

const openName = computed(
  () =>
    (openHand.value && handMadeName(openHand.value.set)) ||
    openGroup.value?.card.name ||
    "Set",
);

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
  const hand = store.handMadeGroups.map((group, cardIndex) => ({
    kind: "card",
    hand: true,
    id: `card:${group.key}`,
    key: group.key,
    // A hand-made card IS its set: selecting it selects the set, never a file.
    setId: group.set.id,
    card: {
      ...group.card,
      markIcon: iconsById.value.get(group.card.markModel?.id) ?? null,
    },
    headId: null,
    cardIndex,
  }));
  const evidence = store.setGroups.map((group, cardIndex) => ({
    kind: "card",
    id: `card:${group.key}`,
    key: group.key,
    card: group.card,
    // The model the card IS, which is what a selection or a verb is aimed at.
    headId: group.head?.id ?? null,
    cardIndex: hand.length + cardIndex,
  }));
  const cards = [...hand, ...evidence];
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
  if (openHand.value) {
    // A hand-made tray's stops are its tiles, slot by slot. Padded to whole
    // GRID rows, so every card after it keeps naming its column; inside the
    // tray the arrows walk slots rather than columns (see `onKeyDown`).
    // The merge offer's strip (#1523) is the tray's first stop, a slot row
    // of its own above the Checkpoint slot, so Up and Down step through it.
    const offer = openHand.value.set.offer
      ? [
          {
            kind: "slot",
            id: "slot:offer",
            key: "offer",
            slotId: "",
            slotIndex: -1,
            member: null,
            offer: true,
            first: true,
          },
        ]
      : [];
    const tiles = [
      ...offer,
      ...setSlots(openHand.value.set).flatMap(({ slot, items }, slotIndex) =>
        items.map((item, itemIndex) => ({
          kind: "slot",
          id: `slot:${item.key}`,
          key: item.key,
          slotId: slot.id,
          slotIndex,
          member: item.member,
          ghost: item.ghost ?? null,
          first: !offer.length && slotIndex === 0 && itemIndex === 0,
        })),
      ),
    ];
    const padded = Math.ceil(Math.max(tiles.length, 1) / cols) * cols;
    while (tiles.length < padded) {
      tiles.push({ kind: "hole", id: `hole:block:${tiles.length}` });
    }
    return [...head, ...tiles, ...cards.slice(rowEnd)];
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
  if (at >= 0) return at;
  return firstStop(0, 1) ?? 0;
});

/** The cursor's key when it is inside the panel, else "". */
const cursorKey = computed(() => {
  const entry = flatRows.value[cursorIndex.value];
  return entry?.kind === "member" || entry?.kind === "slot" ? entry.key : "";
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
      const count = openHand.value
        ? (openHand.value.set.members ?? []).length
        : openMembers.value.length;
      announcement.value = `${openName.value} opened, ${count} models`;
      // A set opened from outside the grid - just made, from the toolbar or from
      // a checkpoint's menu - takes the cursor, and the view scrolls to it.
      const at = flatRows.value.findIndex(
        (entry) => entry.kind === "card" && entry.key === key,
      );
      const current = flatRows.value[cursorIndex.value];
      if (
        at >= 0 &&
        key.startsWith("hand:") &&
        current?.key !== key &&
        current?.kind !== "slot"
      ) {
        moveCursor(at);
        nextTick(() =>
          rowElement(flatRows.value[at])?.scrollIntoView?.({
            block: "nearest",
          }),
        );
      }
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
  if (entry?.kind === "slot" && entry.member?.on_shelf) return entry.member.id;
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

/** A gesture aimed at whatever the cursor is on. */
function selectCursor(event) {
  selectEntry(flatRows.value[cursorIndex.value], event);
}

/**
 * The models whose selection was made from a TRAY row rather than their card.
 *
 * Selection is by model id, and a set's head is drawn twice while its tray is
 * open - as the card and as the tray's first row - so without this, picking the
 * checkpoint in the tray lit the card too, and read as having picked the whole
 * set. So while the open tray shows a model that was picked there, the card
 * named after it stays unlit; close the tray and the card is the only mark left,
 * so it lights again rather than leave a live selection drawn nowhere.
 *
 * Kept only for selections this grid made: a change from anywhere else (Select
 * all, the bar, the row list) clears it, and the cards follow the selection again.
 */
const trayPicked = ref(new Set());
let ownSelection = null;

watch(
  () => store.selectedIds,
  (ids) => {
    if (ids !== ownSelection) trayPicked.value = new Set();
  },
);

/** The models the open tray is drawing, each with its own selection mark. */
const trayModelIds = computed(
  () =>
    new Set(
      // Either tray: a hand-made set's on-shelf members are rows the reader
      // can see just as an evidence tray's are (#1520).
      [...openMembers.value, ...(openHand.value?.models ?? [])].map(
        (member) => member.id,
      ),
    ),
);

/** Is this card's head selected by a tray row the reader can see? */
function shownInTray(headId) {
  return trayPicked.value.has(headId) && trayModelIds.value.has(headId);
}

/** A card is drawn selected: its head is, and not by way of a visible tray row. */
function cardSelected(entry) {
  return store.isSelected(entry.headId) && !shownInTray(entry.headId);
}

/**
 * The card heads a Shift-range from the anchor to `occurrence` passes over, or
 * null when the store will not take it as a range (no anchor on this screen).
 */
function rangeCardHeads(occurrence) {
  const order = orderedEntries.value;
  const from = order.findIndex(
    (item) => item.occurrence === store.anchorOccurrence,
  );
  const to = order.findIndex((item) => item.occurrence === occurrence);
  if (from < 0 || to < 0) return null;
  return new Set(
    order
      .slice(Math.min(from, to), Math.max(from, to) + 1)
      .filter((item) => item.occurrence.startsWith("card:"))
      .map((item) => item.id),
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
  if (entry.hand) {
    selectSetEntry(entry, event);
    return;
  }
  selectEntry(entry, event);
}

// ── Hand-made sets (#1520) ────────────────────────────────────────────────

/** The hand-made set ids in drawn order, which is what a Shift-range spans. */
const orderedSetIds = computed(() =>
  flatRows.value.filter((entry) => entry.hand).map((entry) => entry.setId),
);

function selectSetEntry(entry, event = {}) {
  store.selectSet(
    entry.setId,
    {
      ctrl: Boolean(event.ctrlKey || event.metaKey),
      shift: Boolean(event.shiftKey),
    },
    orderedSetIds.value,
  );
}

/** Right-click, Menu or Shift+F10 on a hand-made card: the set's own menu. */
function openSetMenu(entry, x, y) {
  if (!store.selectedSetIds.has(entry.setId)) selectSetEntry(entry);
  emit("set-menu", { x, y, el: rowElement(entry) });
}

/** Make an empty set and open it. Its tray says what to add first. */
async function createSet() {
  await store.createHandMadeSet({});
}

/** Delete the selected sets, or the one under the cursor. No file is touched. */
function deleteSets(entry) {
  if (entry?.hand && !store.selectedSetIds.has(entry.setId)) {
    selectSetEntry(entry);
  }
  return store.deleteHandMadeSets(store.selectedSets);
}

function onSlotClick({ member, event }) {
  cursorId.value = `slot:m:${member.sha256}`;
  if (!member.on_shelf) return;
  selectEntry(flatRows.value[cursorIndex.value], event);
}

function onSlotMenu({ member, event }) {
  cursorId.value = `slot:m:${member.sha256}`;
  if (
    member.on_shelf &&
    openMenu(flatRows.value[cursorIndex.value], event.clientX, event.clientY)
  ) {
    event.preventDefault();
  }
}

/** Remove from set - the tile's ✕, and the member menu's verb. */
function removeMember(member) {
  if (openHand.value) {
    store.removeFromHandMadeSet(openHand.value.set, [member.sha256]);
  }
}

// The chooser: one popup for a slot's ＋ and both Fill buttons.
const chooser = ref(null);
const chooserQuery = ref("");

const fillPictureItems = computed(() =>
  openHand.value
    ? fillFromPictures(
        openHand.value.set,
        store.workflowSets.combinations,
        store.rows,
      )
    : [],
);

const fillSetItems = computed(() =>
  openHand.value ? fillFromSets(openHand.value.set, store.handMadeSets) : [],
);

const chooserSlot = computed(() =>
  SET_SLOTS.find((slot) => slot.id === chooser.value?.slotId),
);

const chooserSections = computed(() => {
  const mode = chooser.value?.mode;
  const set = openHand.value?.set;
  if (!mode || !set) return [];
  if (mode === "pictures") {
    return [
      {
        id: "pictures",
        label: "",
        items: fillPictureItems.value.map((item) => ({
          ...item,
          checked: true,
        })),
      },
    ];
  }
  if (mode === "set") {
    return [
      {
        id: "sets",
        label: "",
        items: fillSetItems.value.map((item) => ({ ...item, checked: true })),
      },
    ];
  }
  return slotSuggestions({
    set,
    slotId: chooser.value.slotId,
    rows: store.rows,
    sets: store.handMadeSets,
    combinations: store.workflowSets.combinations,
    query: chooserQuery.value,
  }).map((section) => ({
    ...section,
    items: section.items.map((row) => ({
      id: row.id,
      name: row.display_name || row.filename,
      detail: row.base_model_canonical || row.base_model || "",
    })),
  }));
});

const chooserTitle = computed(() => {
  const name = openHand.value ? handMadeName(openHand.value.set) : "";
  if (chooser.value?.mode === "pictures") {
    const checkpoint = setCheckpoint(openHand.value?.set);
    return `Fill "${name}" from pictures of ${checkpoint?.name ?? "its checkpoint"}`;
  }
  if (chooser.value?.mode === "set") return `Fill "${name}" from a set`;
  return chooserSlot.value ? `${chooserSlot.value.add} to "${name}"` : "";
});

const chooserNote = computed(() => {
  if (chooser.value?.mode === "set") {
    const checkpoint = setCheckpoint(openHand.value?.set);
    return checkpoint?.base_model
      ? `From your other ${checkpoint.base_model} sets.`
      : "From your other sets. Add a checkpoint to narrow this to its base model.";
  }
  if (chooser.value?.mode === "slot" && !setCheckpoint(openHand.value?.set)) {
    return "Suggestions follow the checkpoint, so every model is listed until one is chosen.";
  }
  return "";
});

const chooserPlaceholder = computed(() =>
  chooserSlot.value ? `Filter ${chooserSlot.value.noun}` : "Filter…",
);

const chooserEmpty = computed(() =>
  chooser.value?.mode === "slot"
    ? `No ${chooserSlot.value?.noun ?? "models"} on the shelf to add.`
    : "Nothing to add: the set already holds all of them.",
);

/**
 * Open the chooser beside the control that asked for it.
 *
 * @param {"slot"|"pictures"|"set"} mode
 * @param {Element} el - the ＋ tile or fill button, which is also where focus
 *   goes back to.
 * @param {string} [slotId]
 */
function openChooser(mode, el, slotId = "") {
  if (!openHand.value) return;
  chooserQuery.value = "";
  const box = el?.getBoundingClientRect?.();
  chooser.value = {
    mode,
    slotId,
    target: box ? [box.left, box.bottom] : [0, 0],
    returnTo: slotId ? `slot:add:${slotId}` : cursorId.value,
    // Decided by what the TRIGGER is, not by where the cursor was: a Fill
    // button opened while the cursor rested on a ＋ tile is still a button.
    triggerIsTile: Boolean(slotId),
    // The control that asked - a ＋ tile or a Fill button in the tray's bar.
    // A Fill button is not a cursor stop, so the cursor alone cannot return
    // focus to it.
    trigger: el ?? null,
  };
}

/**
 * The one-time "has no base model" offer for the open set, or null.
 *
 * Up while the checkpoint the store just recorded going into THIS set is still
 * its checkpoint and still has no base model. The guess is the shelf's own
 * fuzzy identification, which the shelf shows as a guess and never stores as
 * the base model - here it only pre-fills the field.
 */
const baseOffer = computed(() => {
  const added = store.checkpointAdded;
  const set = openHand.value?.set;
  if (!added || !set || added.setId !== set.id) return null;
  const checkpoint = setCheckpoint(set);
  if (!checkpoint || checkpoint.id !== added.modelId || checkpoint.base_model) {
    return null;
  }
  const row = store.rows.find((candidate) => candidate.id === checkpoint.id);
  const fuzzy = String(row?.base_model_source ?? "").endsWith("_fuzzy");
  return {
    key: `${set.id}:${checkpoint.id}`,
    name: checkpoint.name,
    guess: fuzzy ? (row?.base_model_canonical ?? "") : "",
  };
});

// Once means once: leaving the set's tray takes the offer away for good.
watch(
  () => store.openSetKey,
  (key) => {
    const added = store.checkpointAdded;
    if (added && key !== `hand:${added.setId}`) store.checkpointAdded = null;
  },
);

/** Set base model, from the offer: the shelf's own write, then the sets again. */
async function setCheckpointBase(value) {
  const modelId = store.checkpointAdded?.modelId;
  store.checkpointAdded = null;
  if (modelId == null) return;
  if (await store.editModelIds([modelId], { base_model: value })) {
    await store.loadWorkflowSets({ force: true });
  }
}

// ── The merge offer (#1523) ───────────────────────────────────────────────

/** Put the cursor on a tray stop by its key, if it is drawn. */
function moveToKey(key) {
  const at = flatRows.value.findIndex(
    (entry) => entry.kind === "slot" && entry.key === key,
  );
  if (at >= 0) moveCursor(at);
  return at >= 0;
}

/**
 * Open a set's tray with the cursor on its offer strip: the card's lozenge,
 * and Merge with the pictures' set… in the set's menu.
 */
async function openOffer(setId) {
  const key = `hand:${setId}`;
  if (store.openSetKey !== key) store.toggleSet(key);
  await nextTick();
  moveToKey("offer");
}

/**
 * After a merge or a Keep separate the strip, or the ghost, is gone: the
 * cursor goes where the reader expects - `key` if it is drawn now, else the
 * tray's first stop - and never to <body>.
 */
function settleCursor(key) {
  if (key && moveToKey(key)) return;
  if (flatRows.value.some((entry) => entry.id === cursorId.value)) return;
  const first = flatRows.value.findIndex((entry) => entry.kind === "slot");
  if (first >= 0) moveCursor(first);
}

/** Merge, add all - or one ghost's Add, which lands the cursor on its tile. */
async function mergeOffer(models) {
  const set = openHand.value?.set;
  const adding = models ?? set?.offer?.models ?? [];
  if (!set || !adding.length) return;
  await store.addToHandMadeSet(
    set,
    adding.map((model) => ({ model_id: model.id, slot: model.slot })),
    { joined: true },
  );
  settleCursor(adding.length === 1 ? `m:${adding[0].sha256}` : "");
}

/** Keep separate: the whole offer, or one ghost (Delete on it). */
async function keepSeparate(models) {
  const set = openHand.value?.set;
  if (!set?.offer) return;
  await store.keepOutOfHandMadeSet(set, models);
  settleCursor("");
}

/** Exposed so the shelf can open a new set with Fill from pictures ready. */
function openFill(mode) {
  const button = gridEl.value?.querySelector?.(
    "[data-testid='model-set-slots-panel'] .msp__bar",
  );
  openChooser(mode, button);
}

function closeChooser() {
  const { returnTo, trigger, triggerIsTile } = chooser.value ?? {};
  chooser.value = null;
  // A Fill button still on screen takes its focus back directly. A ＋ tile
  // goes through the cursor, which also keeps the grid's roving tab stop on
  // it - and a tile that vanished (the Checkpoint ＋, once filled) is handled
  // by whoever filled it.
  if (trigger?.isConnected && !triggerIsTile) {
    trigger.focus?.();
    return;
  }
  // Programmatic overlays drop focus to <body>; put it back on the tile, or,
  // when that is gone too, on the open set's card - never nowhere.
  const at = flatRows.value.findIndex((entry) => entry.id === returnTo);
  if (at >= 0) {
    moveCursor(at);
    return;
  }
  const card = flatRows.value.findIndex(
    (entry) => entry.kind === "card" && entry.key === store.openSetKey,
  );
  moveCursor(card >= 0 ? card : (firstStop(0, 1) ?? 0));
}

/** Models a slot pick is still adding, so a quick second click is not a second add. */
const picking = new Set();

async function addChosen(ids) {
  const set = openHand.value?.set;
  const mode = chooser.value?.mode;
  if (!set || !mode) return;
  if (mode === "slot") {
    await pickIntoSlot(set, chooser.value.slotId, ids);
    return;
  }
  const items =
    mode === "pictures" ? fillPictureItems.value : fillSetItems.value;
  const wanted = new Set(ids);
  const members = items
    .filter((item) => wanted.has(item.id))
    .map((item) => ({ model_id: item.id, slot: item.slot }));
  closeChooser();
  await store.addToHandMadeSet(set, members);
}

/**
 * One click in a slot's popup adds that model (#1520 feedback: a tick and an
 * Add button was two decisions for one). The Checkpoint slot holds one, so its
 * popup closes and the cursor lands on the checkpoint just added; any other
 * slot stays open, the added model drops out of the list, and the next one is
 * one more click.
 */
async function pickIntoSlot(set, slotId, ids) {
  const fresh = ids.filter((id) => !picking.has(id));
  if (!fresh.length) return;
  fresh.forEach((id) => picking.add(id));
  const single = slotId === "checkpoint";
  if (single) closeChooser();
  try {
    await store.addToHandMadeSet(
      set,
      fresh.map((id) => ({ model_id: id, slot: slotId })),
    );
  } finally {
    fresh.forEach((id) => picking.delete(id));
  }
  if (single) {
    const sha = store.rows.find((row) => row.id === fresh[0])?.sha256;
    const at = flatRows.value.findIndex(
      (entry) => entry.id === `slot:m:${sha}`,
    );
    if (at >= 0) moveCursor(at);
  }
}

defineExpose({ openFill, openOffer });

/**
 * Right-click a card or a tray row: the shelf's full verb inventory, at the
 * pointer.
 *
 * The file-manager rule the row list already follows: right-clicking something
 * that is NOT selected selects it and acts on it alone; right-clicking one of
 * forty selected models leaves the forty alone. `ModelShelf.vue` owns the menu,
 * because there is one `ShelfSelectionBar` and two views feeding it.
 *
 * Takes the ENTRY, not a model id: a set's head is drawn twice while its tray
 * is open, and the occurrence decides whether its card lights.
 */
function openMenu(entry, x, y) {
  const id = modelIdOf(entry);
  if (!selectable(id)) return false;
  if (!store.isSelected(id)) selectEntry(entry, {});
  emit("menu", { x, y });
  return true;
}

function onRowMenu(entry, event) {
  cursorId.value = entry.id;
  if (entry.hand) {
    event.preventDefault();
    openSetMenu(entry, event.clientX, event.clientY);
    return;
  }
  if (openMenu(entry, event.clientX, event.clientY)) {
    event.preventDefault();
  }
}

/** A tray row was clicked. The tray reports the member; the cursor follows it. */
function onMemberClick({ member, event }) {
  cursorId.value = `member:${member.id}`;
  selectEntry(flatRows.value[cursorIndex.value], event);
}

/** Click, Ctrl+click, Shift+click - the row list's own three gestures. */
function selectEntry(entry, event) {
  const id = modelIdOf(entry);
  if (!selectable(id)) return;
  const ctrl = Boolean(event?.ctrlKey || event?.metaKey);
  const shift = Boolean(event?.shiftKey);
  // Ctrl or Space on a card drawn unlit adds it, as it looks: the model is
  // already selected from the tray, so the toggle would REMOVE it instead.
  if (
    ctrl &&
    entry.kind === "card" &&
    shownInTray(id) &&
    store.isSelected(id)
  ) {
    const next = new Set(trayPicked.value);
    next.delete(id);
    trayPicked.value = next;
    store.anchorOccurrence = entry.id;
    store.anchorId = id;
    return;
  }
  // Read before the store moves the anchor. Ctrl wins over Shift, as it does there.
  const rangeHeads = shift && !ctrl ? rangeCardHeads(entry.id) : null;
  const before = store.selectedIds;
  store.selectFromClick(id, { ctrl, shift }, orderedEntries.value, entry.id);
  const after = store.selectedIds;
  const fromTray = entry.kind === "member" || entry.kind === "slot";
  if (rangeHeads) {
    // A range lights the cards it passed over, and no card it did not.
    trayPicked.value = new Set([...after].filter((m) => !rangeHeads.has(m)));
  } else if (ctrl) {
    // What a card adds was not selected before, so it was never tray-picked.
    const next = new Set([...trayPicked.value].filter((m) => after.has(m)));
    if (fromTray) {
      for (const m of after) if (!before.has(m)) next.add(m);
    }
    trayPicked.value = next;
  } else {
    trayPicked.value = fromTray ? new Set(after) : new Set();
  }
  ownSelection = after;
}

function onMemberMenu({ member, event }) {
  cursorId.value = `member:${member.id}`;
  const entry = flatRows.value.find((row) => row.id === cursorId.value);
  if (openMenu(entry, event.clientX, event.clientY)) {
    event.preventDefault();
  }
}

// ── The roving cursor ─────────────────────────────────────────────────────

/** Is this flat entry somewhere the cursor can rest? Holes are not. */
function isStop(entry) {
  return ["card", "member", "slot"].includes(entry?.kind);
}

/**
 * Up or Down inside a hand-made tray: the first tile of the slot above or below,
 * or out of the tray - Up to its card, Down to the row after it.
 */
function slotStop(index, direction) {
  const rows = flatRows.value;
  const here = rows[index];
  for (let i = index; i >= 0 && i < rows.length; i += direction) {
    const entry = rows[i];
    if (entry?.kind !== "slot") {
      if (direction < 0) {
        return rows.findIndex(
          (row) => row.kind === "card" && row.key === store.openSetKey,
        );
      }
      return firstStop(i, 1);
    }
    if (entry.slotIndex !== here.slotIndex) {
      if (direction > 0) return i;
      // The FIRST tile of the slot above, not its last.
      let first = i;
      while (
        rows[first - 1]?.kind === "slot" &&
        rows[first - 1].slotIndex === entry.slotIndex
      ) {
        first -= 1;
      }
      return first;
    }
  }
  return null;
}

/** First index at or after `index` that is a real row, travelling in `step`. */
function firstStop(index, step) {
  for (let i = index; i >= 0 && i < flatRows.value.length; i += step) {
    if (isStop(flatRows.value[i])) return i;
  }
  return null;
}

/**
 * Where the open tray's rows begin and end in `flatRows`.
 *
 * `null` with no tray open. In List the block is exactly the member entries -
 * the padding only tops a block up to a whole number of TRAY rows, and a
 * one-column tray is already whole - so scanning for them bounds it exactly.
 */
const trayBounds = computed(() => {
  const rows = flatRows.value;
  // A hand-made set's tray is `slot` entries (#1520); an evidence tray's are
  // `member` entries. Only one tray is open at a time.
  const first = rows.findIndex(
    (entry) => entry.kind === "member" || entry.kind === "slot",
  );
  if (first < 0) return null;
  const kind = rows[first].kind;
  let last = first;
  while (last + 1 < rows.length && rows[last + 1].kind === kind) last += 1;
  return { first, last, kind };
});

/**
 * Where a vertical step from the cursor lands.
 *
 * **Two grids of different widths, so a crossing is not arithmetic.** A List
 * tray is one column whatever the card grid above it is doing, so the column a
 * reader is travelling down has no counterpart on the other side. Stepping by
 * the outer count carried that column offset into the tray: from the third card
 * of a row, Down landed on the tray's third row rather than its first, and with
 * a short tray it stepped over the tray entirely.
 *
 * So a crossing lands at the EDGE it arrives at - the tray's first row coming
 * down, its last coming up - and inside the tray the step is one row, which is
 * what the tray is drawn as.
 */
function verticalTarget(direction) {
  const from = cursorIndex.value;
  const cols = Math.max(1, columns.value);
  const tray = trayBounds.value;
  // A slots tray is never drawn in the grid's columns either: its tiles wrap
  // per slot. Inside it, Up and Down are `slotStop`'s (see `onHandKey`), so
  // only the crossing is decided here.
  if (tray && (tray.kind === "slot" || trayColumns.value === 1)) {
    if (flatRows.value[from]?.kind === "member") {
      return firstStop(from + direction, direction);
    }
    const target = from + direction * cols;
    if (direction > 0 && from < tray.first && target >= tray.first) {
      return tray.first;
    }
    if (direction < 0 && from > tray.last && target <= tray.last) {
      return tray.last;
    }
  }
  return verticalStop(from + direction * cols, cols, direction);
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
    if (isStop(flatRows.value[i])) return i;
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
  const selector =
    entry.kind === "member" || entry.kind === "slot"
      ? ".msp__member"
      : ".msg__row";
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
  if (!isStop(entry)) return;
  cursorId.value = entry.id;
  // Into a hand-made tray, the cursor is on FILES: a set selection left behind
  // would turn the Delete a reader aims at a member into a set delete.
  if (entry.kind === "slot") store.clearSetSelection();
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
      ? rows.findIndex((e) => e.kind === "member" || e.kind === "slot")
      : rows.findIndex((e) => e.kind === "card" && e.key === store.openSetKey),
  );
  return true;
}

/**
 * The keys a hand-made set and its tray answer differently.
 *
 * @returns {boolean} true when the press was handled here.
 */
function onHandKey(event, entry) {
  const plain = !event.ctrlKey && !event.metaKey && !event.altKey;
  // Not on a held key: each repeat would make another empty set.
  if (
    plain &&
    !event.shiftKey &&
    !event.repeat &&
    (event.key === "n" || event.key === "N")
  ) {
    event.preventDefault();
    createSet();
    return true;
  }
  if (entry?.kind === "slot") {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const to = slotStop(
        cursorIndex.value,
        event.key === "ArrowDown" ? 1 : -1,
      );
      if (to != null && to >= 0) moveCursor(to, event.shiftKey);
      return true;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (entry.offer) {
        mergeOffer();
      } else if (entry.ghost) {
        mergeOffer([entry.ghost]);
      } else if (!entry.member) {
        openChooser("slot", rowElement(entry), entry.slotId);
      } else if (entry.member.on_shelf) {
        openWorksWith(entry.member);
      }
      return true;
    }
    if (
      (event.key === "Delete" || event.key === "Backspace") &&
      (entry.ghost || entry.offer)
    ) {
      // Keep one model out (a ghost) or the whole offer (the strip). Stopped,
      // so the shelf's file Delete never sees a press aimed at a row that
      // holds no file.
      event.preventDefault();
      event.stopPropagation();
      if (!event.repeat) keepSeparate(entry.ghost ? [entry.ghost] : undefined);
      return true;
    }
    if (event.key === "Backspace" && entry.member) {
      // Remove from set, the keyboard's ✕. Delete stays the shelf's file
      // delete, which warns - the design keeps that meaning on a member.
      event.preventDefault();
      removeMember(entry.member);
      return true;
    }
    return false;
  }
  if (!entry?.hand) return false;
  if (event.key === "Delete" || event.key === "Backspace") {
    event.preventDefault();
    event.stopPropagation();
    // Not on a held key: one press, one delete, as N is one press, one set.
    if (!event.repeat) deleteSets(entry);
    return true;
  }
  if (event.key === "F2") {
    event.preventDefault();
    selectSetEntry(entry);
    emit("rename-set");
    return true;
  }
  if (event.key === " ") {
    event.preventDefault();
    selectSetEntry(entry, { ctrlKey: true });
    return true;
  }
  if (isMenuKey(event)) {
    event.preventDefault();
    const box = rowElement(entry)?.getBoundingClientRect?.();
    openSetMenu(entry, box ? box.left + 24 : 0, box ? box.bottom : 0);
    return true;
  }
  return false;
}

function onKeyDown(event) {
  if (headerStep(event)) return;
  if (targetOwnsTheGesture(event)) return;
  const entry = flatRows.value[cursorIndex.value];
  const extend = event.shiftKey;
  if (onHandKey(event, entry)) return;
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
      moveCursor(verticalTarget(1), extend);
      return;
    case "ArrowUp":
      event.preventDefault();
      moveCursor(verticalTarget(-1), extend);
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
      selectCursor({ ctrlKey: true });
      return;
    case "F2":
      // The rename key, which the verb menu advertises with an `F2` keycap - so
      // it has to answer here or the cap is a lie on this screen. It renames the
      // model under the CURSOR, as it does on a row: the press makes that one
      // the selection first, rather than renaming whichever of forty the bar
      // happens to hold.
      if (!selectable(modelIdOf(entry))) return;
      event.preventDefault();
      selectCursor({});
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
        if (openMenu(entry, x, y)) event.preventDefault();
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

.msg__state--inline {
  padding: 0 0 var(--space-4);
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
