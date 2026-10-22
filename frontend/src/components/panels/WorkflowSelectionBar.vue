<template>
  <!-- The floating pill, the SAME object the model shelf and the photo grid
       dock over their own lists: bottom-centre, panel surface, pill radius,
       elevation-4. The shelf's argument is borrowed whole (#904, #1455) —
       nine labelled buttons were a sentence to re-read on every selection, a
       row of icons is a chord you learn once, and the words are never gone:
       hover has them and right-click has all of them. The two destinations
       that REPLACE the picture grid now dock the same pill, so a reader who
       learned the chord on one has learned it on the other.

       The last verb is the only one that destroys anything, which is why it
       is the only one in the error colour. -->
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
    <!-- The way out to the pictures (#1797): the picture grid's own
         "pictures" glyph, first because it reads the selection rather than
         changing it. -->
    <AppBarButton
      shape="round"
      icon="image-multiple-outline"
      data-verb="show-pictures"
      :aria-label="showPicturesLabel"
      :disabled="!hasPictures"
      :tooltip="showPicturesTitle"
      @click="emit('show-pictures')"
    />

    <AppBarButton
      shape="round"
      icon="play"
      data-verb="run"
      aria-label="Run this workflow"
      :disabled="!runnable || busy"
      :loading="running('run')"
      :tooltip="runTitle"
      @click="emit('run')"
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

    <!-- The one destructive verb, so the one verb in the error colour, and
         always in the same last place before `⋯`: a destructive
         button that moves with the selection is how a reader presses the wrong
         one. -->
    <AppBarButton
      shape="round"
      danger
      icon="delete-outline"
      data-verb="delete"
      aria-label="Delete workflow"
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
      <VerbMenu />
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
    <VerbMenu />
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
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { useComfyuiOpen } from "../../composables/useComfyuiOpen";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { formatKeyHint, selectAllKeyHint } from "../../utils/shortcutHints";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { outputNoun } from "../../utils/workflowCard";

const emit = defineEmits([
  "select-all",
  "menu-closed",
  "open-cover",
  "show-pictures",
  "run",
  "open-comfyui",
  "rename",
  "hide",
  "export",
  "duplicate",
  "use-set",
  "delete",
]);

const store = useWorkflowsStore();

// The keycap beside "Select all shown", so the chord is taught where the
// button is — the same job the `Esc` cap next to Clear does. Read once at
// setup: the platform cannot change under a mounted component.
const selectAllHint = formatKeyHint(selectAllKeyHint());

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
 * it.
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
 * `{id, index, total, noun}`, read off the tile's own dataset by the host,
 * with what its own card calls its output: in a mixed selection the tile may
 * be a video while the selection as a whole is "pictures".
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
 * nowhere. This pays that debt on every way out of the menu, Escape included
 * (WCAG 2.4.3).
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
  comfyuiSubOpen.value = false;
  if (wasOpen) emit("menu-closed");
});

const cards = computed(() => store.selectedCards);
const single = computed(() => store.selectedKeys.length === 1);

/**
 * What the selection's output is called: "video" when every selected workflow
 * saves video, "picture" otherwise — a mixed selection included, since that
 * is the library's word for anything in it.
 */
const noun = computed(() =>
  cards.value.length > 0 &&
  cards.value.every((card) => outputNoun(card) === "video")
    ? "video"
    : "picture",
);

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
  return `${n.toLocaleString()} ${n === 1 ? "workflow" : "workflows"} selected`;
});

// ── The gates ─────────────────────────────────────────────────────────────
//
// Each one is ONE computed carrying its refusal sentence, never a boolean
// beside a message kept in step with it by hand: written as two, the shelf's
// Stack tooltip named the wrong rule for half of its refusals and sent the
// reader to fix something that was not the problem.

/** `POST /workflows/run` takes one workflow, and the Run popup shows one run. */
const runnable = computed(() => Boolean(store.runnableCard));
const runTitle = computed(() =>
  runnable.value ? "Run this workflow" : "Select one workflow to run it",
);

// *Open in ComfyUI*, the Workflow tab's two open verbs under one row. Absent
// with no ComfyUI to open, as the tab's button is; refused for several cards,
// or a ComfyUI without the node that loads the link.
const { canOpenComfyui, comfyuiLacksNode, comfyuiOpensACopy } =
  useComfyuiOpen();
const comfyuiOpenable = computed(
  () => runnable.value && !comfyuiLacksNode.value,
);
const comfyuiTitle = computed(() => {
  if (!runnable.value) return "Select one workflow to open it in ComfyUI";
  if (comfyuiLacksNode.value) {
    return (
      "Opening a workflow needs the ComfyUI-PixlStash node in ComfyUI. " +
      "Install or update it, then restart ComfyUI. Already installed? " +
      "Restart ComfyUI, then leave Workflows and come back so PixlStash " +
      "checks again"
    );
  }
  return "";
});
const adaptedTitle = computed(() =>
  comfyuiOpensACopy.value
    ? "Your ComfyUI-PixlStash node is older, so saving there does not save " +
      "back here. Update it to save back"
    : "With PixlStash's savers and loaders, so what you queue there comes back here",
);
const comfyuiSubOpen = ref(false);
// Opened leftward when the menu sits too near the window's right edge for it.
const comfyuiSubFlip = ref(false);
watch(moreMenuOpen, (open) => {
  if (!open) comfyuiSubOpen.value = false;
});

/** Open the submenu beside `wrap`, its first row focused when `focus`. */
async function openComfyuiSub(wrap, focus = false) {
  comfyuiSubFlip.value =
    wrap.getBoundingClientRect().right + 200 > window.innerWidth;
  comfyuiSubOpen.value = true;
  if (!focus) return;
  await nextTick();
  wrap.querySelector(".ctx-submenu .ctx-item")?.focus();
}

/** Back out of the submenu to its row. */
function closeComfyuiSub(wrap) {
  comfyuiSubOpen.value = false;
  wrap.querySelector(":scope > .ctx-item")?.focus();
}

/** The refusal of a verb that acts on exactly one workflow. */
function oneOnly(verb) {
  return `Select one workflow to ${verb} it`;
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
    const one = picture.noun ?? noun.value;
    return {
      id: picture.id,
      label:
        picture.total > 1
          ? `Open ${one} ${picture.index} of ${picture.total}`
          : `Open this ${one}`,
      title: `Open the ${one} you right-clicked`,
    };
  }
  return {
    id: coverPictureId.value,
    label: `Open cover ${noun.value}`,
    title: runnable.value
      ? coverPictureId.value != null
        ? `Open the first of this workflow's ${noun.value}s`
        : `This workflow has no ${noun.value} to open`
      : `Select one workflow to open its cover ${noun.value}`,
  };
});

const coverOpenable = computed(() => openTarget.value.id != null);

/**
 * The pictures of every selected workflow, OR'd, on All Pictures (#1797).
 * The row says it in full; the pill has no room for words, so its tooltip
 * carries the count of workflows.
 */
const showPicturesLabel = computed(() =>
  cards.value.length > 1
    ? `Show all ${noun.value}s using these ${cards.value.length.toLocaleString()} workflows`
    : `Show all ${noun.value}s using this workflow`,
);
// Nothing to show, so nothing to press: the view would be cleared for an
// empty grid (the F7 figure is plain text on such a card for the same reason).
const hasPictures = computed(() =>
  cards.value.some((card) => (card.picture_count ?? 0) > 0),
);
const showPicturesTitle = computed(() => {
  if (!hasPictures.value) {
    return cards.value.length > 1
      ? `These workflows have no ${noun.value}s to show`
      : `This workflow has no ${noun.value}s to show`;
  }
  return cards.value.length > 1
    ? `Show the ${noun.value}s of these ${cards.value.length.toLocaleString()} workflows`
    : `Show the ${noun.value}s of this workflow`;
});

const renameTitle = computed(() =>
  single.value ? "Give this workflow a name" : oneOnly("rename"),
);

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
 * Only a MANUAL workflow can be deleted: it is a record of its own.
 *
 * `manual` is exactly the fact `DELETE /workflows/{id}` tests: an automatic
 * workflow is what the library's pictures group into, not a record, and the
 * route 409s on one ("hide it instead"). All of them, not the subset that
 * can: a press that deleted four of six would be a partial destruction nobody
 * asked for. The caller still counts refusals, because the gate is not a
 * promise the write succeeds.
 */
const deletable = computed(
  () => cards.value.length > 0 && cards.value.every((card) => card.manual),
);

const deleteTitle = computed(() => {
  if (!deletable.value) {
    return (
      "Only a manual workflow can be deleted. One PixlStash groups from " +
      `your ${noun.value}s is hidden instead`
    );
  }
  const n = store.selectedKeys.length;
  return n === 1
    ? `Delete this workflow. Its ${noun.value}s stay, on their automatic workflow`
    : `Delete these ${n.toLocaleString()} workflows. Their ${noun.value}s stay, on their automatic workflows`;
});

const exportTitle = computed(() =>
  single.value
    ? "Save it as a file to share: prompts and seeds taken out"
    : oneOnly("export"),
);

const duplicateTitle = computed(() =>
  single.value
    ? "Make a new manual workflow from this one"
    : oneOnly("duplicate"),
);

/**
 * *Use a set…* is the Workflow tab's: it fills that tab's model rows, and
 * nothing is written until the tab's bar saves them (#1850).
 */
const useSetTitle = computed(() =>
  single.value
    ? "Fill its model rows from one of your sets, as unsaved changes"
    : "Select one workflow to use a set on it",
);

/** Fire a verb and shut whichever surface it was pressed on. */
function onVerb(verb) {
  countMenuOpen.value = false;
  contextOpen.value = false;
  moreMenuOpen.value = false;
  comfyuiSubOpen.value = false;
  if (verb === "hide") emit("hide", allHidden.value);
  else if (verb === "open-comfyui") emit("open-comfyui", false);
  else if (verb === "open-comfyui-original") emit("open-comfyui", true);
  else emit(verb);
}

/**
 * The verb list itself, as a render function rather than a second `.vue` file.
 *
 * It is drawn twice — once under `⋯` and once at the pointer — from one array,
 * and that is the whole reason the context menu lives in this component and
 * not in the view: every tooltip here is a refusal sentence, and a second copy
 * of them would drift.
 *
 * The order is the design's: what running the card does, then what changes
 * the card, then the two that make something new, then the one that destroys.
 */
const VerbMenu = () => {
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
  const item = (icon, label, { verb, disabled, title, kbd, danger } = {}) =>
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
        onClick: disabled
          ? (event) => event.preventDefault()
          : () => onVerb(verb),
      },
      [
        title ? h(Tooltip, { text: title, activator: "parent" }) : null,
        // An mdi name, or a glyph of its own (the PixlStash and ComfyUI marks).
        typeof icon === "string"
          ? h("i", { class: `v-icon mdi ${icon} ctx-icon`, "aria-hidden": "true" })
          : icon,
        h("span", { class: "ctx-label-text" }, label),
        kbd ? h("span", { class: "ctx-shortcut" }, kbd) : null,
      ],
    );
  const sep = () => h("div", { class: "ctx-sep" });

  /**
   * *Open in ComfyUI* and its submenu: Adapted for PixlStash, Original.
   *
   * An enabled row stops its own click: a `v-menu` closes on any click in its
   * content, and this one opens something rather than doing it. A refused
   * row's click closes the menu, as every other refused row's does. Right arrow,
   * Enter and Space open the submenu on its first row; Left goes back.
   */
  const comfyuiRow = () => {
    if (!canOpenComfyui.value) return null;
    const disabled = !comfyuiOpenable.value;
    const open = comfyuiSubOpen.value && !disabled;
    const subKeydown = (event) => {
      const wrap = event.currentTarget.parentElement;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        closeComfyuiSub(wrap);
      } else {
        onMenuKeydown(event);
      }
      // The parent menu's own roving cursor must not move as well.
      if (["ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.stopPropagation();
      }
    };
    return h(
      "div",
      {
        class: ["ctx-submenu-wrap", comfyuiSubFlip.value && "ctx-flip-sub"],
        onMouseenter: (event) => {
          if (!disabled) openComfyuiSub(event.currentTarget);
        },
        // Not while a row in it holds focus: a pointer crossing the row after
        // the keyboard opened it would take the focused row away with it.
        onMouseleave: (event) => {
          if (!event.currentTarget.contains(document.activeElement)) {
            comfyuiSubOpen.value = false;
          }
        },
        onFocusout: (event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) {
            comfyuiSubOpen.value = false;
          }
        },
      },
      [
        h(
          "button",
          {
            class: ["ctx-item", disabled && "ctx-item--disabled"],
            type: "button",
            role: "menuitem",
            "data-verb": "open-comfyui",
            "aria-haspopup": "menu",
            "aria-expanded": String(open),
            "aria-disabled": disabled ? "true" : undefined,
            onClick: (event) => {
              event.preventDefault();
              if (disabled) return;
              // Opens, never toggles: the pointer that clicks has already
              // opened it on the way in.
              event.stopPropagation();
              openComfyuiSub(event.currentTarget.parentElement);
            },
            onKeydown: (event) => {
              if (disabled || !["ArrowRight", "Enter", " "].includes(event.key)) {
                return;
              }
              event.preventDefault();
              event.stopPropagation();
              openComfyuiSub(event.currentTarget.parentElement, true);
            },
          },
          [
            comfyuiTitle.value
              ? h(Tooltip, { text: comfyuiTitle.value, activator: "parent" })
              : null,
            h(ComfyuiIcon, { class: "ctx-icon" }),
            h("span", { class: "ctx-label-text" }, "Open in ComfyUI"),
            h("i", {
              class: "v-icon mdi mdi-chevron-right ctx-arrow",
              "aria-hidden": "true",
            }),
          ],
        ),
        open
          ? h(
              "div",
              {
                class: "ctx-submenu",
                role: "menu",
                "aria-label": "Open in ComfyUI",
                onKeydown: subKeydown,
              },
              [
                item(
                  h("img", {
                    src: "/Logo.png",
                    alt: "",
                    class: "ctx-icon wf-menu-logo",
                  }),
                  "Adapted for PixlStash",
                  {
                    verb: "open-comfyui",
                    title: adaptedTitle.value,
                  },
                ),
                item(h(ComfyuiIcon, { class: "ctx-icon" }), "Original", {
                  verb: "open-comfyui-original",
                  title: "With its own savers and loaders instead of PixlStash's",
                }),
              ],
            )
          : null,
      ],
    );
  };

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
      item("mdi-image-outline", openTarget.value.label, {
        verb: "open-cover",
        disabled: !coverOpenable.value,
        title: openTarget.value.title,
      }),
      item("mdi-play", "Run…", {
        verb: "run",
        disabled: !runnable.value,
        title: runTitle.value,
      }),
      comfyuiRow(),
      item("mdi-image-multiple-outline", showPicturesLabel.value, {
        verb: "show-pictures",
        disabled: !hasPictures.value,
        title: showPicturesTitle.value,
      }),
      sep(),
      item("mdi-pencil-outline", "Rename", {
        verb: "rename",
        disabled: !single.value,
        title: renameTitle.value,
        kbd: "F2",
      }),
      item(
        allHidden.value ? "mdi-eye-outline" : "mdi-eye-off-outline",
        allHidden.value ? "Unhide" : "Hide",
        {
          verb: "hide",
          title: hideTitle.value,
        },
      ),
      // With the rows that change the card, not the ones that make a new one:
      // it fills the Workflow tab's model rows and writes nothing.
      item("mdi-swap-horizontal", "Use a set…", {
        verb: "use-set",
        disabled: !single.value,
        title: useSetTitle.value,
      }),
      sep(),
      // The two that make something rather than changing a card. Both
      // single-only: an export is one save dialog and a duplicate is one new
      // card, and doing either forty times is a queue feature wearing a menu
      // row's clothes.
      item("mdi-tray-arrow-down", "Export…", {
        verb: "export",
        disabled: !single.value,
        title: exportTitle.value,
      }),
      item("mdi-content-duplicate", "Duplicate", {
        verb: "duplicate",
        disabled: !single.value,
        title: duplicateTitle.value,
      }),
      sep(),
      item("mdi-delete-outline", "Delete…", {
        verb: "delete",
        disabled: !deletable.value,
        title: deleteTitle.value,
        kbd: "Del",
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
  comfyuiOpenable,
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

/* The PixlStash mark is the brand in colour, so it takes none of the dimming
   a monochrome row glyph does; the slot sizes it as the title bar's is. */
.wf-menu .wf-menu-logo {
  object-fit: contain;
  opacity: 1;
}
</style>
