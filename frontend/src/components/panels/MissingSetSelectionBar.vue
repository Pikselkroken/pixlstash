<template>
  <!-- The pill while cards named after a MISSING base model are selected. The
       same floating object as the other two pills. Replace swaps the missing
       file in the workflows that load it; Hide takes the selected cards off
       the grid. No file on the shelf is touched, so nothing here wears the
       error colour. -->
  <div
    v-if="store.selectedMissing.length"
    class="selbar"
    role="toolbar"
    aria-label="Selected sets with a missing model"
  >
    <button class="selbar-count" type="button" @click="store.clearMissingSelection()">
      <Tooltip text="Clear the selection (Esc)" activator="parent" />
      <v-icon size="16">mdi-file-alert-outline</v-icon>
      <span>{{ countLabel }}</span>
      <v-icon size="16" class="selbar-chevron">mdi-close</v-icon>
    </button>
    <span class="selbar-sep"></span>
    <AppBarButton
      shape="round"
      icon="file-replace-outline"
      data-verb="replace-missing"
      aria-label="Replace"
      tooltip="Replace in every workflow that loads it"
      @click="emit('replace')"
    />
    <AppBarButton
      v-if="hide"
      shape="round"
      :icon="hide.icon.replace('mdi-', '')"
      data-verb="hide-sets"
      :aria-label="hide.label"
      :tooltip="hide.label"
      @click="emit('hide-sets')"
    />
  </div>

  <!-- The card's context menu: right-click, the Menu key or Shift+F10 on a
       missing-base card. The shelf's own verb list in its own order, which the
       pill is a shortcut into: Replace leads, Hide follows it, Copy filename
       copies the missing names, and the verbs that write the checkpoint's
       shelf row are shown disabled with the reason, as the file menu shows a
       verb that does not apply, rather than vanishing. -->
  <v-menu
    v-model="contextOpen"
    :target="contextAt"
    location="bottom end"
    origin="top start"
    :offset="2"
  >
    <div
      class="ctx-menu shelf-menu"
      role="menu"
      tabindex="-1"
      @keydown="onMenuKeydown"
    >
      <template v-for="(entry, index) in menu" :key="index">
        <div v-if="entry === SEP" class="ctx-sep"></div>
        <button
          v-else
          class="ctx-item"
          :class="{ 'ctx-item--disabled': !entry.verb, 'ctx-item--danger': entry.danger }"
          type="button"
          role="menuitem"
          :disabled="!entry.verb"
          :data-verb="entry.verb || undefined"
          @click="entry.verb && verb(entry.verb)"
        >
          <Tooltip v-if="!entry.verb" :text="NO_ROW" activator="parent" />
          <v-icon class="ctx-icon">{{ entry.icon }}</v-icon>
          <span class="ctx-label-text">{{
            typeof entry.label === "function" ? entry.label() : entry.label
          }}</span>
          <span v-if="entry.kbd" class="ctx-shortcut">{{ entry.kbd }}</span>
        </button>
      </template>
    </div>
  </v-menu>
</template>

<script setup>
/**
 * The verb surface for selected missing-base cards. Emits only, like
 * `WorkflowSetSelectionBar`: the view owns the Replace dialog.
 */
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { hideSetsVerb } from "../../utils/workflowSets";
import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** Every selected card from pictures, the file pill's included: what Hide takes. */
  sets: { type: Array, default: () => [] },
});

const emit = defineEmits(["replace", "hide-sets"]);

const store = useModelShelfStore();

const SEP = "sep";
const NO_ROW =
  "Its checkpoint is not on your shelf, so there is no file for this. Replace it first.";
const several = () => new Set(store.selectedMissing.flatMap((h) => h.names)).size > 1;
/**
 * The file menu's verbs in its order (`ShelfSelectionBar`'s `VerbMenu`).
 * `verb: null` is a verb that writes the checkpoint's shelf row, which a
 * missing checkpoint does not have.
 */
const MENU = [
  { icon: "mdi-file-replace-outline", verb: "replace", label: () => (several() ? "Replace missing models…" : "Replace missing model…") },
  SEP,
  { icon: "mdi-pencil-outline", verb: null, label: "Rename", kbd: "F2" },
  { icon: "mdi-image-outline", verb: null, label: "Set thumbnail…" },
  SEP,
  { icon: "mdi-cube-outline", verb: null, label: "Set base model…" },
  { icon: "mdi-shape-outline", verb: null, label: "Set kind…" },
  { icon: "mdi-account-plus", verb: null, label: "Assign to person" },
  { icon: "mdi-folder-plus", verb: null, label: "Assign to set" },
  { icon: "mdi-layers-outline", verb: null, label: "Stack with selection" },
  { icon: "mdi-folder-move-outline", verb: null, label: "Move to…" },
  SEP,
  { icon: "mdi-folder-open-outline", verb: null, label: "Open in file manager" },
  { icon: "mdi-connection", verb: null, label: "Works with…" },
  { icon: "mdi-layers-plus", verb: null, label: "New workflow set with this checkpoint" },
  { icon: "mdi-content-copy", verb: "copy-filenames", label: () => (several() ? "Copy filenames" : "Copy filename") },
  SEP,
  { icon: "mdi-playlist-remove", verb: null, label: "Remove from shelf" },
  { icon: "mdi-delete-outline", verb: null, label: "Delete", kbd: "Del", danger: true },
];

/** Hide over the selected cards, as the pill's button and the menu's row. */
const hide = computed(() => hideSetsVerb(props.sets));

/**
 * `MENU` with Hide under Replace: the card's eye button, over the selection.
 * The one verb here besides Replace that needs no shelf row, since it hides
 * the card and reaches no file.
 */
const menu = computed(() =>
  hide.value
    ? [
        MENU[0],
        { icon: hide.value.icon, verb: "hide-sets", label: hide.value.label },
        ...MENU.slice(1),
      ]
    : MENU,
);

const contextOpen = ref(false);
const contextAt = ref([0, 0]);

const countLabel = computed(() => {
  // Files, not cards: one set can be missing two.
  const n = new Set(store.selectedMissing.flatMap((head) => head.names)).size;
  return n === 1 ? "1 missing model" : `${n} missing models`;
});

function verb(name) {
  contextOpen.value = false;
  if (name === "copy-filenames") copyFilenames();
  else emit(name);
}

/** The missing files' names, as the file menu's Copy filename copies a row's. */
async function copyFilenames() {
  const names = [...new Set(store.selectedMissing.flatMap((head) => head.names))];
  const notices = useNoticeStore();
  if (!names.length) return;
  try {
    await navigator.clipboard.writeText(names.join("\n"));
    notices.push({
      level: "success",
      text: names.length === 1 ? `Copied ${names[0]}.` : `Copied ${names.length} filenames.`,
    });
  } catch (err) {
    notices.push({
      level: "error",
      text: `Could not reach the clipboard: ${err?.message || err}`,
    });
  }
}

/**
 * Open the card menu at a point, for the grid's right-click and Menu key.
 *
 * Opened from code, so v-menu has no activator to hand focus back to and
 * drops it on <body>. The card that asked is refocused on close, unless a
 * verb moved focus on purpose (the Replace dialog).
 */
let menuTrigger = null;
function openContextMenu(x, y, trigger = document.activeElement) {
  menuTrigger = trigger;
  contextAt.value = [x, y];
  contextOpen.value = true;
}

watch(contextOpen, (open) => {
  if (open || !menuTrigger) return;
  const trigger = menuTrigger;
  menuTrigger = null;
  setTimeout(() => {
    const active = document.activeElement;
    if (!active || active === document.body) trigger.focus?.();
  }, 0);
});

defineExpose({ openContextMenu });
</script>
