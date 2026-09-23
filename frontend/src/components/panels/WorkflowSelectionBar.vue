<template>
  <!-- The floating pill, the SAME object the model shelf and the photo grid
       dock over their own lists: bottom-centre, panel surface, pill radius,
       elevation-4. The shelf's argument is borrowed whole (#904, #1455) —
       nine labelled buttons were a sentence to re-read on every selection, a
       row of icons is a chord you learn once, and the words are never gone:
       hover has them and right-click has all of them. The two destinations
       that REPLACE the picture grid now dock the same pill, so a reader who
       learned the chord on one has learned it on the other.

       The last verb is the only one that touches a file on disk, which is why
       it is the only one in the error colour. -->
  <div
    v-if="store.selectedKeys.length"
    class="selbar"
    role="toolbar"
    aria-label="Selected workflows"
  >
    <!-- The count is a control, not a caption: it is where "I meant all of
         them" and "never mind" live, which is why it carries a chevron. -->
    <v-menu
      v-model="countMenuOpen"
      location="top"
      origin="bottom center"
      :offset="8"
    >
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          class="selbar-count"
          type="button"
          aria-haspopup="menu"
          :aria-expanded="countMenuOpen"
        >
          <Tooltip :text="countTitle" activator="parent" />
          <v-icon size="16">mdi-sitemap-outline</v-icon>
          <span>{{ store.selectedKeys.length.toLocaleString() }}</span>
          <v-icon size="16" class="selbar-chevron">mdi-menu-down</v-icon>
        </button>
      </template>
      <div
        class="ctx-menu wf-menu"
        role="menu"
        tabindex="-1"
        @keydown="onMenuKeydown"
      >
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          @click="onVerb('select-all')"
        >
          <v-icon class="ctx-icon">mdi-select-all</v-icon>
          <span class="ctx-label-text">Select all shown</span>
          <span class="ctx-shortcut">{{ selectAllHint }}</span>
        </button>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          @click="store.clearSelection()"
        >
          <v-icon class="ctx-icon">mdi-close</v-icon>
          <span class="ctx-label-text">Clear selection</span>
          <span class="ctx-shortcut">Esc</span>
        </button>
      </div>
    </v-menu>

    <span class="selbar-sep"></span>

    <!-- Every verb below carries BOTH `aria-label` and a tooltip, and they say
         different things on purpose: the label is the verb and never changes,
         so a screen reader hears a stable name; the tooltip is the REFUSAL and
         changes with the selection. A tooltip alone is not an accessible name
         a reader can rely on. -->
    <AppBarButton
      shape="round"
      icon="play"
      data-verb="run"
      :aria-label="stackWhole ? runTitle : 'Run this workflow'"
      :disabled="!runnable || busy"
      :loading="running('run')"
      :tooltip="runTitle"
      @click="emit('run')"
    />

    <AppBarButton
      shape="round"
      icon="layers-outline"
      data-verb="stack"
      aria-label="Stack these together"
      :disabled="!stackable || busy"
      :loading="running('stack')"
      :tooltip="stackTitle"
      @click="emit('stack')"
    />

    <AppBarButton
      shape="round"
      icon="layers-off-outline"
      data-verb="unstack"
      aria-label="Unstack all"
      :disabled="!unstackable || busy"
      :loading="running('unstack')"
      :tooltip="unstackTitle"
      @click="emit('unstack')"
    />

    <!-- Rename rides along DISABLED rather than disappearing, which is the
         shelf's rule and this screen's now: a row of buttons that reflows as
         the selection grows is a row you have to re-read, and a disabled
         button with its reason in the tooltip teaches where the verb lives. -->
    <AppBarButton
      shape="round"
      icon="pencil-outline"
      data-verb="rename"
      aria-label="Rename"
      :disabled="!single || busy"
      :loading="running('rename')"
      :tooltip="renameTitle"
      @click="emit('rename')"
    />

    <AppBarButton
      shape="round"
      :icon="allHidden ? 'eye-outline' : 'eye-off-outline'"
      data-verb="hide"
      :aria-label="hideLabel"
      :disabled="busy"
      :loading="running('hide')"
      :tooltip="hideTitle"
      @click="emit('hide', allHidden)"
    />

    <span class="selbar-sep"></span>

    <!-- The one verb that touches a file on disk, so the one verb in the error
         colour, and always in the same last place before `⋯`: a destructive
         button that moves with the selection is how a reader presses the wrong
         one. -->
    <AppBarButton
      shape="round"
      danger
      icon="delete-outline"
      data-verb="delete"
      aria-label="Delete the workflow file"
      :disabled="!deletable || busy"
      :loading="running('delete')"
      :tooltip="deleteTitle"
      @click="emit('delete')"
    />

    <v-menu
      v-model="moreMenuOpen"
      location="top end"
      origin="bottom end"
      :offset="8"
    >
      <template #activator="{ props: menuProps }">
        <AppBarButton
          shape="round"
          icon="dots-horizontal"
          v-bind="menuProps"
          data-verb="more"
          aria-label="More actions"
          aria-haspopup="menu"
          :aria-expanded="moreMenuOpen"
          tooltip="More…"
        />
      </template>
      <VerbMenu v-bind="verbHandlers" />
    </v-menu>
  </div>

  <!-- The card context menu: the FULL inventory, and the thing every other
       surface here is a shortcut into. Anchored to the pointer rather than to
       the card, which is what a context menu is; the view opens it through
       `openContextMenu` after it has made the card the selection. -->
  <v-menu
    v-model="contextOpen"
    :target="contextAt"
    location="bottom end"
    origin="top start"
    :offset="2"
  >
    <VerbMenu v-bind="verbHandlers" />
  </v-menu>
</template>

<script setup>
// The Workflows grid's verb layer (#1455), which is `ShelfSelectionBar.vue`
// borrowed whole and re-gated.
//
// **Three surfaces, one set of gates.** The pill, its `⋯` menu and the card
// context menu offer the same verbs under the same refusals, so they are one
// component rather than three that have to be kept in step — and the `⋯` menu
// and the context menu are two mounts of ONE render function, so their parity
// is construction rather than convention. `menu-parity.spec.js` exists because
// the picture grid's pair are two components; this pair cannot diverge.
//
// It carries no verb logic of its own: every button emits and
// `WorkflowsView.vue` runs the confirmation, the dialog and the store call.
// That keeps the confirmations in one place instead of half here and half
// there, and is what lets this component be mounted in a test with nothing but
// a store.

import { computed, h, nextTick, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { isStack } from "../../utils/workflowCard";
import { formatKeyHint, selectAllKeyHint } from "../../utils/shortcutHints";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";

const emit = defineEmits([
  "select-all",
  "menu-closed",
  "open-cover",
  "run",
  "stack",
  "unstack",
  "rename",
  "make-cover",
  "hide",
  "export",
  "duplicate",
  "delete",
]);

const store = useWorkflowsStore();

// The keycap beside "Select all shown", so the chord is taught where the
// button is — the same job the `Esc` cap next to Clear does. Read once at
// setup: the platform cannot change under a mounted component.
const selectAllHint = formatKeyHint(selectAllKeyHint());

/** Fire a verb and shut whichever surface it was pressed on. */
function onVerb(verb) {
  countMenuOpen.value = false;
  emit(verb);
}

const countMenuOpen = ref(false);
const moreMenuOpen = ref(false);
const contextOpen = ref(false);
/** `[x, y]` in client coordinates — what v-menu's `target` takes. */
const contextAt = ref([0, 0]);

/**
 * Open the context menu at a pointer.
 *
 * Called by the view, which has already made the right-clicked card the
 * selection — the file-manager rule: right-clicking a card that is not
 * selected selects it, right-clicking one that is leaves the selection alone.
 *
 * **Closed first and reopened on the next tick**, even when it is already
 * open: a right button press fires `contextmenu` but no `click`, and
 * Vuetify's click-outside closes on `click`, so right-clicking a second card
 * would otherwise leave the menu where it was while the selection moved under
 * it. `StackPanel.openMenu` learned this first.
 */
/**
 * The picture the right-click landed on, or null for a click that missed one.
 *
 * **Belongs to the open context menu and to nothing else.** A right-click on
 * a card's second thumbnail means "this one", and a menu that offered only
 * the cover would answer a question nobody asked — but `⋯` on the pill has no
 * pointer and no target, so it means the cover. Cleared when the menu closes
 * (below), or the next `⋯` would silently inherit whichever tile was last
 * right-clicked and open a picture off a card the reader may have moved on
 * from.
 *
 * `{id, index, total}`, read off the tile's own dataset by the host.
 */
const contextPicture = ref(null);

async function openContextMenu(x, y, picture = null) {
  contextOpen.value = false;
  await nextTick();
  contextAt.value = [x, y];
  contextPicture.value = picture;
  contextOpen.value = true;
}

/**
 * The menu closing hands focus back, because it has nowhere of its own to
 * hand it to.
 *
 * A context menu's activator is a PAIR OF COORDINATES, not an element, so
 * when Vuetify closes it there is nothing for the browser to restore focus
 * to and it lands on `document.body` — outside the grid, with the roving
 * cursor's row still marked and no longer focused, so the next arrow key goes
 * nowhere. `WorkflowsView.followMove` already says exactly this about the
 * member-move path and restores focus there; this is the same debt on every
 * other way out of the menu, Escape included (WCAG 2.4.3).
 *
 * Emitted rather than fixed here: only the view knows where the cursor is.
 */
/** Shut the card menu. The view uses it; so does anything that must not leave
 * a stale pointer target behind. */
function closeContextMenu() {
  contextOpen.value = false;
}

watch(contextOpen, (open, wasOpen) => {
  if (open) return;
  contextPicture.value = null;
  if (wasOpen) emit("menu-closed");
});

const cards = computed(() => store.selectedCards);
const single = computed(() => store.selectedKeys.length === 1);

/**
 * A bulk write is out, so nothing else may be started.
 *
 * Every verb here writes once per selected card, serially, and each write
 * costs a whole grid read on the server — so a selection of forty is forty
 * round trips with the pill still live. A second press part-way through does
 * not merely duplicate work: it re-issues writes against state the first press
 * has already changed, and the 404s that come back are counted as refusals, so
 * the reader is told an operation FAILED immediately after it succeeded.
 *
 * The store owns the flag (`verbBusy`) rather than this component, because the
 * rail's Workflow tab fires two of the same verbs and the two surfaces have to
 * lock together.
 */
const busy = computed(() => Boolean(store.verbBusy));

/** Is `verb` the one currently running? Drives the spinner, nothing else. */
const running = (verb) => store.verbBusy === verb;

const countTitle = computed(() => {
  const n = store.selectedKeys.length;
  if (stackWhole.value) {
    return `A stack of ${n.toLocaleString()} workflows selected`;
  }
  return `${n.toLocaleString()} ${n === 1 ? "workflow" : "workflows"} selected`;
});

// ── The gates ─────────────────────────────────────────────────────────────
//
// Each one is ONE computed carrying its refusal sentence, never a boolean
// beside a message kept in step with it by hand: written as two, the shelf's
// Stack tooltip named the wrong rule for half of its refusals and sent the
// reader to fix something that was not the problem.

/**
 * `POST /workflows/run` takes one card, and the Run popup shows one run. A
 * stack selected whole runs its cover (`store.runnableCard`).
 */
const runnable = computed(() => Boolean(store.runnableCard));
const runTitle = computed(() => {
  if (!runnable.value) {
    return "Select one workflow, or one whole stack, to run it";
  }
  if (single.value) return "Run this workflow";
  return (
    `Run ${store.runnableCard.name}, this stack's cover. ` +
    "Pick another member in the Run popup"
  );
});

/**
 * A stack selected whole: several keys, one card on screen. Run and Open
 * cover picture take its cover; the verbs that would have to pick one of its
 * workflows refuse with that rule rather than "Select one workflow".
 */
const stackWhole = computed(() => !single.value && runnable.value);

/** The refusal of a verb that acts on exactly one workflow. */
function oneOnly(verb) {
  return stackWhole.value
    ? `A stack is several workflows. Open it and pick one to ${verb}`
    : `Select one workflow to ${verb} it`;
}

/**
 * The picture the selected card's cover draws, or null.
 *
 * One card, and one whose cover names its picture (`picture_id`). A card whose
 * pictures have not arrived, or one served before that field existed, has
 * nothing to open — and this menu is the only surface where that refusal can
 * be read, so it says which of the two it is.
 */
const coverPictureId = computed(
  () => store.runnableCard?.covers?.[0]?.picture_id ?? null,
);

/**
 * The picture this menu's open row would open, and how to name it.
 *
 * The tile under the pointer wins over the cover: right-clicking the third
 * thumbnail and being offered the first is the menu answering a different
 * question from the one the gesture asked. With no tile under the pointer —
 * a right-click on the card's name row, or the pill's `⋯`, which has no
 * pointer at all — it is the cover, which is what "this card's picture"
 * means when nothing narrower was pointed at.
 */
const openTarget = computed(() => {
  const picture = contextPicture.value;
  if (picture?.id != null) {
    return {
      id: picture.id,
      label:
        picture.total > 1
          ? `Open picture ${picture.index} of ${picture.total}`
          : "Open this picture",
      title: "Open the picture you right-clicked",
    };
  }
  return {
    id: coverPictureId.value,
    label: "Open cover picture",
    title: runnable.value
      ? coverPictureId.value != null
        ? "Open the first of this workflow's pictures"
        : "This workflow has no picture to open"
      : "Select one workflow to open its cover picture",
  };
});

const coverOpenable = computed(() => openTarget.value.id != null);

const renameTitle = computed(() =>
  single.value ? "Give this workflow a name" : oneOnly("rename"),
);

const stackRefusal = computed(() =>
  store.selectedKeys.length < 2
    ? "Select two or more workflows to group them"
    : "",
);
const stackable = computed(() => !stackRefusal.value);

/** Whether pressing Stack would fuse existing stacks rather than build one. */
const stackFuses = computed(() =>
  cards.value.some((card) => card.stack_id != null),
);

const stackTitle = computed(() => {
  if (stackRefusal.value) return stackRefusal.value;
  const n = store.selectedKeys.length.toLocaleString();
  // The verb says which of the two things it is about to do: the route
  // absorbs a selected card's whole stack, so stacking something already
  // stacked MERGES runs rather than collapsing loose cards, and the reader is
  // entitled to know which.
  return stackFuses.value
    ? `Fuse these ${n} into one stack`
    : `Group these ${n} workflows into one stack`;
});

/**
 * Why this selection cannot be taken apart, or `""` when it can.
 *
 * **`stack_id` null is not "not a stack".** The server withholds it for a
 * stack whose membership the grid drew only PART of — a hidden member, or one
 * counted as a one-off — deliberately, so that `POST /workflows/stacks/{id}/
 * unstack` cannot be attempted and refused (`useWorkflowsStore.reorderMembers`
 * says the same of the reorder route). Such a card still draws as a stack: ▸,
 * the layered count, a "differs by" row. Gating on the id alone therefore told
 * a reader looking at a stack that nothing in their selection was one, which
 * is a sentence they can see is false — so the two refusals are separated and
 * the second names the way out.
 */
const unstackRefusal = computed(() => {
  if (store.selectedStackIds.length) return "";
  if (cards.value.some((card) => isStack(card))) {
    return (
      "PixlStash has only drawn part of this stack, so it cannot take it " +
      "apart. Let the hidden workflows or the one-offs in first"
    );
  }
  return "Nothing in this selection is part of a stack";
});
const unstackable = computed(() => !unstackRefusal.value);

const unstackTitle = computed(() => {
  if (unstackRefusal.value) return unstackRefusal.value;
  const n = store.selectedStackIds.length;
  return n === 1
    ? "Break this stack up, leaving its workflows in the grid"
    : `Break these ${n.toLocaleString()} stacks up, leaving their workflows in the grid`;
});

/**
 * Is every selected card already hidden?
 *
 * `every`, not `some`: a mixed selection is hidden, because the gesture a
 * reader makes on "these and that one already hidden" is to get them all out
 * of the grid. It reads as Unhide only when there is nothing left to hide.
 *
 * A selection whose cards the store cannot resolve — the grid was re-read
 * under it — is never `allHidden`: `cards` is then empty and `every` is
 * vacuously true, which would turn the button into Unhide over a selection
 * nobody has hidden.
 */
const allHidden = computed(
  () => cards.value.length > 0 && cards.value.every((card) => card.hidden),
);

const hideLabel = computed(() =>
  allHidden.value ? "Unhide these workflows" : "Hide these workflows",
);

const hideTitle = computed(() => {
  const n = store.selectedKeys.length;
  if (allHidden.value) {
    return n === 1
      ? "Put this workflow back in the grid"
      : `Put these ${n.toLocaleString()} workflows back in the grid`;
  }
  return n === 1
    ? "Take this workflow out of the grid. Nothing is deleted"
    : `Take these ${n.toLocaleString()} workflows out of the grid. Nothing is deleted`;
});

/**
 * Only a card that came from a FILE on this machine has one to delete.
 *
 * All of them, not the subset that can: the route is the one verb here that
 * touches bytes, and a press that silently deleted four of six files would be
 * a partial destruction nobody asked for.
 *
 * `imported` is "the hub holds a file ROW for this card"
 * (`workflow_card_reads.py`), which is the fact the route's 409 branch tests,
 * so the button is not offered where it could only come back refused for
 * THAT reason. It is not a promise the delete will succeed: `trash_user_workflow`
 * resolves inside the user's workflow folder and 404s when the file is no
 * longer on disk, which the row survives — somebody deleting it outside
 * PixlStash leaves an `imported` card whose delete refuses. That is why the
 * caller counts refusals and names them rather than assuming the gate was
 * enough.
 */
const deletable = computed(
  () => cards.value.length > 0 && cards.value.every((card) => card.imported),
);

const deleteTitle = computed(() => {
  if (!deletable.value) {
    return (
      "Only a workflow that came from a file on this machine has a file to " +
      "delete. One the library knows from its pictures is hidden instead"
    );
  }
  const n = store.selectedKeys.length;
  return n === 1
    ? "Send this workflow's file to the trash. The card and its pictures stay"
    : `Send these ${n.toLocaleString()} workflow files to the trash. The cards and their pictures stay`;
});

const exportTitle = computed(() =>
  single.value
    ? "Save a shareable copy: prompts, seeds and recipe LoRAs taken out"
    : oneOnly("export"),
);

const duplicateTitle = computed(() =>
  single.value
    ? "Write a copy into your workflow folder, to change in ComfyUI"
    : oneOnly("copy"),
);

/**
 * Why this selection cannot become its stack's cover, or `""` when it can.
 *
 * One card, and one that is a MEMBER of the open stack rather than its cover:
 * the cover is what the grid draws for the whole stack, and this is the owner
 * saying the order chose the wrong one. Listed always and disabled with its
 * reason on anything else — this is where a reader who has never opened a
 * stack finds out that opening one is a gesture, which a hidden item could
 * never tell them.
 */
const coverRefusal = computed(() => {
  if (!single.value) return "Open a stack and pick one workflow inside it";
  const key = store.selectedKeys[0];
  const members = store.openMembers;
  const at = members.findIndex((card) => card.key === key);
  if (at < 0) return "Open a stack and pick one workflow inside it";
  if (at === 0) return "This workflow already stands for its stack";
  if (!store.openStackId) return "This stack has no recorded order to change";
  return "";
});
const coverable = computed(() => !coverRefusal.value);
const coverTitle = computed(
  () => coverRefusal.value || "Draw the stack from this workflow instead",
);

/**
 * Everything `VerbMenu` needs, in one object, so the two mounts of it cannot
 * drift apart. Passed with `v-bind` rather than listed twice in the template.
 */
const verbHandlers = computed(() => ({
  single: single.value,
  coverOpenable: coverOpenable.value,
  coverOpenLabel: openTarget.value.label,
  coverOpenTitle: openTarget.value.title,
  runnable: runnable.value,
  runTitle: runTitle.value,
  stackable: stackable.value,
  stackTitle: stackTitle.value,
  stackLabel: stackFuses.value ? "Fuse into one stack" : "Stack together",
  unstackable: unstackable.value,
  unstackTitle: unstackTitle.value,
  renameTitle: renameTitle.value,
  coverable: coverable.value,
  coverTitle: coverTitle.value,
  hideLabel: allHidden.value ? "Unhide" : "Hide",
  hideTitle: hideTitle.value,
  exportTitle: exportTitle.value,
  duplicateTitle: duplicateTitle.value,
  deletable: deletable.value,
  deleteTitle: deleteTitle.value,
  onVerb: (verb) => {
    contextOpen.value = false;
    moreMenuOpen.value = false;
    if (verb === "hide") emit("hide", allHidden.value);
    else emit(verb);
  },
}));

/**
 * The verb list itself, as a render function rather than a second `.vue` file.
 *
 * It is drawn twice — once under `⋯` and once at the pointer — from one array,
 * and that is the whole reason the context menu lives in this component and
 * not in the view: every tooltip here is a refusal sentence, and a second copy
 * of them would drift.
 *
 * The order is the design's: what running the card does, then what grouping
 * does, then what naming does, then the two that make a file, then the one
 * that destroys one.
 */
const VerbMenu = (props) => {
  /**
   * One row.
   *
   * **A refused row carries `aria-disabled`, never the native attribute.** A
   * natively `disabled` button fires no pointer events, so a tooltip bound to
   * it with `activator: "parent"` never opens — and the whole argument for
   * keeping a verb on screen rather than hiding it is that the REASON is one
   * hover away. Disabled natively, this menu offered a grey row and no way at
   * all to find out why, which is worse than not offering it. `aria-disabled`
   * announces the state, the class paints it, the click is dropped below, and
   * `utils/menuKeyboard.js` already skips `.ctx-item--disabled` so the roving
   * cursor passes over it exactly as it did.
   *
   * The pill's icon buttons above keep the native attribute: they are the
   * shared `AppBarButton`, whose disabled shape is the app's, and changing it
   * is a change to every toolbar in the product rather than to this screen.
   * That is why the full inventory — the one surface that can always state its
   * refusal — is a right-click away from every one of them.
   */
  const item = (icon, label, { verb, on, disabled, title, kbd, danger } = {}) =>
    h(
      "button",
      {
        class: [
          "ctx-item",
          disabled && "ctx-item--disabled",
          danger && "ctx-item--danger",
        ],
        type: "button",
        role: "menuitem",
        // **The row's identity, and what parity is asserted on.** Not the
        // LABEL: the open row names the picture the pointer was over, so the
        // context menu says "Open picture 2 of 3" where `⋯` says "Open cover
        // picture" — the same verb, correctly telling the reader which
        // picture it is about to open. Comparing rendered strings would call
        // that a divergence, which is the opposite of what #403 was about.
        "data-verb": verb,
        "aria-disabled": disabled ? "true" : undefined,
        onClick: disabled ? (event) => event.preventDefault() : on,
      },
      [
        title ? h(Tooltip, { text: title, activator: "parent" }) : null,
        h("i", { class: `v-icon mdi ${icon} ctx-icon`, "aria-hidden": "true" }),
        h("span", { class: "ctx-label-text" }, label),
        kbd ? h("span", { class: "ctx-shortcut" }, kbd) : null,
      ],
    );
  const sep = () => h("div", { class: "ctx-sep" });

  // `data-testid`, not a second class: the count menu shares `.wf-menu` for
  // its sizing, and a selector that cannot tell the two apart makes the parity
  // assertion compare a count menu with a verb menu.
  return h(
    "div",
    {
      class: "ctx-menu wf-menu",
      role: "menu",
      // `tabindex` and the keydown are what the COUNT menu above already
      // carries: a `v-menu`'s activator focuses the content's first focusable
      // child, and `onMenuKeydown` is what moves between rows from there.
      // Without them the ten-row inventory was arrow-key-inert while the
      // two-row menu beside it was not — and this is the surface a keyboard
      // reader is sent to.
      tabindex: "-1",
      "data-testid": "wf-verbs",
      onKeydown: onMenuKeydown,
    },
    [
      // **The cover click's only keyboard route.** The tiles sit at
      // `tabindex="-1"` because the grid owns Tab, so without this row a new
      // action on a shipped surface could be performed by pointer alone
      // (WCAG 2.1.1) — and this menu calls itself the full inventory, which
      // was not true while it left one verb out.
      item("mdi-image-outline", props.coverOpenLabel, {
        verb: "open-cover",
        on: () => props.onVerb("open-cover"),
        disabled: !props.coverOpenable,
        title: props.coverOpenTitle,
      }),
      item("mdi-play", "Run…", {
        verb: "run",
        on: () => props.onVerb("run"),
        disabled: !props.runnable,
        title: props.runTitle,
      }),
      sep(),
      item("mdi-layers-outline", props.stackLabel, {
        verb: "stack",
        on: () => props.onVerb("stack"),
        disabled: !props.stackable,
        title: props.stackTitle,
      }),
      item("mdi-layers-off-outline", "Unstack all", {
        verb: "unstack",
        on: () => props.onVerb("unstack"),
        disabled: !props.unstackable,
        title: props.unstackTitle,
      }),
      item("mdi-arrow-collapse-up", "Make it the cover", {
        verb: "make-cover",
        on: () => props.onVerb("make-cover"),
        disabled: !props.coverable,
        title: props.coverTitle,
      }),
      sep(),
      item("mdi-pencil-outline", "Rename", {
        verb: "rename",
        on: () => props.onVerb("rename"),
        disabled: !props.single,
        title: props.renameTitle,
        kbd: "F2",
      }),
      item(
        props.hideLabel === "Unhide"
          ? "mdi-eye-outline"
          : "mdi-eye-off-outline",
        props.hideLabel,
        {
          verb: "hide",
          on: () => props.onVerb("hide"),
          title: props.hideTitle,
        },
      ),
      sep(),
      // The two that write a file rather than changing a card. Both single-only:
      // an export is one save dialog and a duplicate is one new card, and doing
      // either forty times is a queue feature wearing a menu row's clothes.
      item("mdi-tray-arrow-down", "Export…", {
        verb: "export",
        on: () => props.onVerb("export"),
        disabled: !props.single,
        title: props.exportTitle,
      }),
      item("mdi-content-duplicate", "Duplicate", {
        verb: "duplicate",
        on: () => props.onVerb("duplicate"),
        disabled: !props.single,
        title: props.duplicateTitle,
      }),
      sep(),
      item("mdi-delete-outline", "Delete file…", {
        verb: "delete",
        on: () => props.onVerb("delete"),
        disabled: !props.deletable,
        title: props.deleteTitle,
        danger: true,
      }),
    ],
  );
};

// The opener the view calls, and the gates the suite asserts directly rather
// than through a handful of `title` strings. Last in the file because
// `defineExpose` evaluates its argument where it stands.
defineExpose({
  openContextMenu,
  closeContextMenu,
  coverPictureId,
  openTarget,
  coverOpenable,
  runnable,
  stackable,
  stackFuses,
  unstackable,
  coverable,
  allHidden,
  deletable,
});
</script>

<style>
/* The rows are the global `.ctx-*` menu (styles/context-menu.css). What is
   left here is sizing, and it is global because the verb menu is a render
   function, which scoped CSS cannot reach. */
.wf-menu {
  min-width: 210px;
}
</style>
