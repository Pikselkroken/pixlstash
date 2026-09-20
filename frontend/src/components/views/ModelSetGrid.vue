<template>
  <div class="msg" role="region" aria-label="Workflow sets">
    <p class="visually-hidden" role="status">{{ announcement }}</p>

    <p v-if="store.setsLoading && !store.setsLoaded" class="msg__state">
      Reading which models have run together…
    </p>
    <p v-else-if="store.setsError" class="msg__state" role="alert">
      {{ store.setsError }}
    </p>
    <div v-else-if="nothingAtAll" class="msg__state">
      <p class="msg__lead">No picture in this library records the models it used.</p>
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
          <StackPanel
            v-if="entry.kind === 'member' && entry.memberIndex === 0"
            :panel-id="PANEL_ID"
            :name="openName"
            :members="openMembers"
            :size="openMembers.length"
            noun="combination"
            :note="panelNote"
            :columns="columns"
            :column-index="openColumnIndex"
            :cursor-key="cursorKey"
            @close="closePanel"
          >
            <template #actions>
              <!-- The one gesture that belongs to the FOLD rather than to a
                   card: a reader who distrusts the folding can see every set
                   without hunting for the toolbar control. -->
              <AppButton
                v-if="store.view.fold !== 'none'"
                variant="outline"
                size="sm"
                icon-left="call-split"
                @click="store.setView({ fold: 'none' })"
                >Don’t fold</AppButton
              >
            </template>
            <template #member="{ member }">
              <ModelComboCard :card="member" @pick="openWorksWith" />
            </template>
          </StackPanel>

          <div
            v-else-if="entry.kind === 'card'"
            class="msg__row"
            role="row"
            aria-level="1"
            :aria-expanded="
              entry.card.stack_size > 1
                ? String(store.openSetKey === entry.key)
                : undefined
            "
            :aria-controls="
              store.openSetKey === entry.key ? PANEL_ID : undefined
            "
            :aria-owns="store.openSetKey === entry.key ? memberRowIds : undefined"
            :aria-posinset="entry.cardIndex + 1"
            :aria-setsize="store.setStacks.length"
            :tabindex="index === cursorIndex ? 0 : -1"
            :data-key="entry.key"
            @click="cursorId = entry.id"
            @dblclick="toggle(entry)"
          >
            <div class="msg__cell" role="gridcell">
              <WorkflowCard
                :card="entry.card"
                :expanded="store.openSetKey === entry.key"
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
          In no set — {{ store.noSetRows.length.toLocaleString() }}
          {{ store.noSetRows.length === 1 ? "model" : "models" }}
        </h3>
        <p class="msg__ghost-note">
          No recipe in this library binds them to anything. Nothing follows from
          that except that no picture here has been made with them.
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

    <ModelWorksWithDialog :model="worksWithModel" @close="worksWithModel = null" />
  </div>
</template>

<script setup>
/**
 * The model shelf's Workflow set axis, as a card grid (#1438).
 *
 * **A card is one combination** - the exact files a picture proves ran together
 * - and near-identical combinations fold into a stack the reader can open. The
 * card, the stack badge, the ▸ and the panel under it are the shipped workflow
 * grid's, borrowed rather than rebuilt: the gesture already exists, and a
 * second card component would be the same design maintained twice.
 *
 * **Nothing here is selectable.** The shelf's selection is by `model.id` and
 * carries six verbs, two of which destroy bytes; a card is a SET, so "selected"
 * would have to mean "every model in it", and a Delete aimed at a card would
 * take a shared VAE with it. The grid is for reading, and the row list is where
 * a model is acted on - which is one `Group by` away. The roving cursor is kept
 * because it is how the panel is reached from a keyboard, not because there is
 * anything to pick.
 *
 * ONE FLAT LIST, exactly as `WorkflowsView` builds one: the cards and, while a
 * stack is open, its members are one index space, so the cursor crosses the
 * panel boundary with the same `index ± columns` arithmetic. The padding holes
 * are what keep `index % columns` naming the column a row is drawn in.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { comboCard } from "../../utils/workflowSets";
import ModelWorksWithDialog from "../panels/ModelWorksWithDialog.vue";
import StackPanel from "../panels/StackPanel.vue";
import AppButton from "../widgets/AppButton.vue";
import ModelComboCard from "../widgets/ModelComboCard.vue";
import WorkflowCard from "../widgets/WorkflowCard.vue";

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

const store = useModelShelfStore();

const gridEl = ref(null);
const columns = ref(1);
// The cursor is an entry id, not an index: `flatRows` is rebuilt by a fold
// change, by a stack opening and by one closing, and an index held across any
// of those names a different card - or, when the list shrinks, none, which
// takes the grid's only tab stop with it.
const cursorId = ref("");
const announcement = ref("");
const worksWithModel = ref(null);

const nothingAtAll = computed(
  () => !store.setStacks.length && !store.noSetRows.length,
);

/** The open stack, or null. */
const openStack = computed(
  () =>
    store.setStacks.find((stack) => stack.key === store.openSetKey) ?? null,
);

/** The open stack's combinations, as the panel's cards. */
const openMembers = computed(() => {
  const stack = openStack.value;
  if (!stack) return [];
  const [seed] = stack.members;
  return stack.members.map((member, index) => comboCard(member, seed, index));
});

const openName = computed(() => openStack.value?.card.name || "Set");

const panelNote = computed(() =>
  openMembers.value.length > 1
    ? "each one is a set of files a picture proves ran together"
    : "",
);

/**
 * The cards, with the open stack's members spliced in at its row's end.
 *
 * `{kind: "card"|"member"|"hole", id, key, ...}`. `hole` pads the member block
 * out to whole rows, so every card after the panel keeps naming the column it
 * is drawn in.
 */
const flatRows = computed(() => {
  const cards = store.setStacks.map((stack, cardIndex) => ({
    kind: "card",
    id: `card:${stack.key}`,
    key: stack.key,
    card: stack.card,
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
  const block = openMembers.value.map((card, memberIndex) => ({
    kind: "member",
    id: `member:${card.key}`,
    key: card.key,
    memberIndex,
  }));
  const padded = Math.ceil(Math.max(block.length, 1) / cols) * cols;
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
  openMembers.value.map((card) => `${PANEL_ID}-row-${card.key}`).join(" "),
);

// ── Columns from the real container width ─────────────────────────────────

function measure() {
  const width = gridEl.value?.clientWidth ?? 0;
  columns.value = Math.max(
    1,
    Math.floor((width + COLUMN_GAP) / (COLUMN_MIN + COLUMN_GAP)),
  );
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
      announcement.value = `${openName.value} opened, ${openMembers.value.length} combinations`;
      return;
    }
    announcement.value = `${previous ? "Set" : ""} closed`.trim();
  },
);

function toggle(entry) {
  if (entry?.kind === "card" && entry.card.stack_size > 1) {
    store.toggleSet(entry.key);
  }
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

/** One file inside a combination: what else has this run with? */
function openWorksWith(file) {
  worksWithModel.value = file;
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
  const selector = entry.kind === "member" ? ".stack-panel__member" : ".msg__row";
  return Array.from(gridEl.value?.querySelectorAll(selector) ?? []).find(
    (element) => element.dataset.key === entry.key,
  );
}

/** Put the cursor on whichever real row `index` names, if there is one. */
function moveCursor(index) {
  const entry = flatRows.value[index];
  if (!entry || entry.kind === "hole") return;
  cursorId.value = entry.id;
  nextTick(() => rowElement(entry)?.focus());
}

function onKeyDown(event) {
  const entry = flatRows.value[cursorIndex.value];
  const cols = Math.max(1, columns.value);
  switch (event.key) {
    case "ArrowRight":
      event.preventDefault();
      moveCursor(firstStop(cursorIndex.value + 1, 1));
      return;
    case "ArrowLeft":
      event.preventDefault();
      moveCursor(firstStop(cursorIndex.value - 1, -1));
      return;
    case "ArrowDown":
      event.preventDefault();
      moveCursor(verticalStop(cursorIndex.value + cols, cols, 1));
      return;
    case "ArrowUp":
      event.preventDefault();
      moveCursor(verticalStop(cursorIndex.value - cols, cols, -1));
      return;
    case "Enter":
      event.preventDefault();
      // A stack opens; a card with nothing to fold opens ⓘ, which is the one
      // escape hatch every card on this grid has.
      if (entry?.kind === "card" && entry.card.stack_size > 1) {
        store.toggleSet(entry.key);
      } else {
        rowElement(entry)?.querySelector(".wf-card__info")?.click();
      }
      return;
    case "Escape":
      if (store.openSetKey) {
        event.preventDefault();
        closePanel();
      }
      return;
    default:
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

.msg__ghost-title {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.msg__ghost-note {
  margin: 0;
  max-width: 68ch;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
