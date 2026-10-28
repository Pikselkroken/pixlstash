<template>
  <!-- The selection half of the grid action pill. It is a run of controls, not
       a surface: GridActionPill owns the background, the seam, the motion and
       the bottom-edge anchor. The face count is folded into the menu trigger's
       label rather than standing on its own, so the half opens with exactly one
       number (merged-grid-action-pill.md §6.3). -->
  <div class="selection-bar" :class="{ 'selection-bar--compact': compact }">
      <div
        v-if="
          selectedCount > 0 &&
          !isScrapheapView &&
          pluginOptions.length &&
          !isReadOnly
        "
        class="plugin-run-controls"
        @keydown.esc="handlePluginMenuEsc"
      >
        <v-menu
          v-model="pluginMenuOpen"
          :close-on-content-click="false"
          location-strategy="connected"
          location="bottom end"
          origin="top end"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <div
              v-bind="menuProps"
              class="hidden-panel-activator"
              aria-hidden="true"
            ></div>
          </template>
          <div class="plugin-menu-panel">
            <div class="plugin-menu-header">Apply Filters</div>
            <div class="plugin-menu-body">
              <label class="plugin-menu-label">Filters</label>
              <select v-model="selectedPluginName" class="plugin-run-select">
                <option
                  v-for="plugin in pluginOptions"
                  :key="plugin.name"
                  :value="plugin.name"
                >
                  {{ plugin.display_name || plugin.name }}
                </option>
              </select>

              <PluginParametersUI
                v-model="pluginParameters"
                :plugin="activePluginSchema"
                :show-description="true"
                tone="auto"
                input-class="plugin-run-select"
                label-class="plugin-menu-label"
              />

              <label class="plugin-menu-checkbox-row">
                <input v-model="stackFilterOutputs" type="checkbox" />
                <span>Stack new images with the originals</span>
              </label>

              <div class="plugin-menu-actions">
                <AppButton
                  variant="primary"
                  icon-left="play"
                  :disabled="!selectedPluginName || !selectedImageIds.length"
                  @click="runSelectedPlugin"
                  >Run</AppButton
                >
              </div>
            </div>
          </div>
        </v-menu>
      </div>
      <!--
        The verbs live here, not in the toolbar (#2160): the count, Favourite,
        Tag (T), Add to set (S), Export, Train LoRA, then ⋯ for the rest, a
        rule, and Scrapheap and Clear (Esc).

        ⋯ opens SelectionMenu, which mirrors the right-click context menu for
        every selection-scoped action (Export and Train LoRA are the pill's
        own and in neither menu, as they were in neither before), so a keyboard user reaches the same
        actions as a right-click on the same selection. The context menu
        additionally offers three single-image actions (Share image, Find
        similar faces, Remove all shares) that are deliberately context-only:
        they act on a specific right-clicked image and its per-image face /
        share state, which the selection-scoped dropdown has no single target
        for. Multi-select parity is asserted by e2e/specs/menu-parity.spec.js.
      -->
      <div
        class="selection-ctx-bar"
        :class="{ 'selection-ctx-bar--active': selectedCount > 0 }"
      >
        <!-- The group's first child is its count, so entering the group
             announces it without a live region of its own. Never truncated
             by the ladder: a count is the blast radius of everything else. -->
        <span class="selection-count"
          ><Tooltip
            v-if="countTitle"
            :text="countTitle"
            activator="parent"
          />{{ selectionCountLabel }}</span
        >
        <span class="pill-divider" aria-hidden="true"></span>
        <!-- Favourite leads the verbs (#1578's pill): the glyph says which
             way it will go, filled when every selected picture already is
             one. A verb, so it stays ink; red marks the state, on the tile. -->
        <AppBarButton
          v-if="selectedCount > 0 && !isScrapheapView && !isReadOnly"
          shape="round"
          :icon="favouriteOn ? 'heart' : 'heart-outline'"
          data-verb="favourite"
          :aria-label="favouriteLabel"
          :aria-pressed="favouriteOn ? 'true' : 'false'"
          aria-keyshortcuts="H"
          :tooltip="`${favouriteLabel} (H)`"
          @click="emit('favourite')"
        />
        <AppBarButton
          v-if="showTagControls"
          class="pill-verb"
          shape="round"
          icon="tag-outline"
          data-verb="tag"
          aria-label="Tag"
          aria-keyshortcuts="T"
          tooltip="Tag selected (T)"
          @click="openTagInput()"
        >
          <span class="pill-verb-label">Tag</span>
          <kbd class="fm-kbd pill-verb-kbd" aria-hidden="true">T</kbd>
        </AppBarButton>
        <Tooltip v-if="showTagControls" :text="addToSetTitle">
          <template #activator="{ props: tipProps }">
            <AddToEntityControl
              v-bind="withRef(tipProps, (el) => (addToSetRef = el))"
              class="pill-ate"
              type="set"
              label="Add to set"
              key-hint="S"
              float-menu
              allow-create
              :subject-ids="selectedImageIds"
              :disabled="!!groupingLockReason"
              :locked-set-ids="lockedSetsStore.lockedSetIds"
              @added="$emit('added-to-set', $event)"
              @create="$emit('create-set', $event)"
            />
          </template>
        </Tooltip>
        <!-- Export: the zip or folder export of the selection. A whole view
             is Ctrl+A, then this. -->
        <v-menu
          v-if="selectedCount > 0"
          v-model="exportStore.exportMenuOpen"
          :close-on-content-click="false"
          location="top"
          origin="bottom center"
          :offset="8"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <AppBarButton
              v-bind="menuProps"
              class="pill-verb tb-export-btn"
              shape="round"
              data-verb="export"
              icon="tray-arrow-down"
              :open="exportStore.exportMenuOpen"
              :tooltip="exportTitle"
              :aria-label="exportTitle"
            >
              <span class="pill-verb-label">Export</span>
            </AppBarButton>
          </template>
          <TbExportPanel
            @confirm-export="emit('confirm-export-zip')"
            @confirm-export-folder="emit('confirm-export-folder', $event)"
          />
        </v-menu>
        <!-- Train LoRA: a run trains on one set or one character, so this is
             live only in such a view (the dialog cannot take a loose
             selection); elsewhere it stays, dimmed, and says why. The
             sidebar's set and person menus offer it too. -->
        <AppBarButton
          v-if="selectedCount > 0 && !isReadOnly && !isScrapheapView"
          class="pill-verb tb-train-btn"
          shape="round"
          data-verb="train"
          icon="school-outline"
          :disabled="!canTrain"
          :open="trainingStore.trainDialogOpen"
          aria-label="Train LoRA"
          :tooltip="
            canTrain
              ? `Train a LoRA on ${trainScope.name || 'this view'}`
              : 'Open a set or person to train a LoRA on it.'
          "
          @click="openTrainDialog()"
        >
          <span class="pill-verb-label">Train LoRA</span>
        </AppBarButton>
        <!-- Upscale… (#1868): the keepers picked after a run, upscaled and
             stacked on top of themselves. Pictures only: a video has no
             picture loader to go behind. -->
        <AppBarButton
          v-if="canUpscale"
          class="upscale-btn"
          shape="round"
          icon="arrow-expand-all"
          data-testid="selection-upscale"
          :tooltip="`Upscale the ${selectedCount === 1 ? 'selected picture' : `${selectedCount} selected pictures`}`"
          @click="upscale.openFor(props.selectedImageIds)"
        >
          <span class="bar-btn-apply-label">Upscale…</span>
        </AppBarButton>
        <AppBarButton
          v-if="
            selectedCount > 0 &&
            !isScrapheapView &&
            !isReadOnly &&
            impossibleSources.length > 0
          "
          class="clear-impossible-btn"
          shape="round"
          icon="tag-off-outline"
          :loading="clearingImpossible"
          :tooltip="`Strip the impossible tags from the ${selectedCount} selected picture(s)`"
          @click="$emit('clear-impossible-tags')"
        >
          <span class="clear-impossible-label">{{
            clearingImpossible ? "Clearing…" : "Clear impossible tags"
          }}</span>
        </AppBarButton>
        <v-menu
          v-model="selectionMenuOpen"
          :close-on-content-click="false"
          location="top end"
          origin="bottom end"
          transition="scale-transition"
        >
          <template #activator="{ props: menuProps }">
            <!-- A menu button with neither aria-haspopup nor aria-expanded
                 gives a screen-reader user no signal that it opens anything. -->
            <AppBarButton
              v-bind="menuProps"
              class="selection-menu-trigger"
              shape="round"
              data-verb="more"
              icon="dots-horizontal"
              :disabled="!hasSelection"
              :tooltip="triggerTitle"
              aria-haspopup="menu"
              :aria-expanded="selectionMenuOpen ? 'true' : 'false'"
            />
          </template>
          <SelectionMenu
            ref="selectionMenuRef"
            :open="selectionMenuOpen"
            :selected-count="selectedCount"
            :selected-image-ids="selectedImageIds"
            :is-read-only="isReadOnly"
            :is-scrapheap-view="isScrapheapView"
            :grouping-lock-reason="props.groupingLockReason"
            :tagger-plugins="props.taggerPlugins"
            :captioner-plugins="props.captionerPlugins"
            :comfyui-configured="props.comfyuiConfigured"
            :has-plugin-options="pluginOptions.length > 0"
            :selected-sort="props.selectedSort"
            :selected-group-name="props.selectedGroupName"
            :stack-edit="props.stackEdit"
            :keep-cover-only-stack-count="props.keepCoverOnlyStackCount"
            :keep-cover-only-lock-reason="props.keepCoverOnlyLockReason"
            :rotate-block-reason="props.rotateBlockReason"
            :compare-block-reason="props.compareBlockReason"
            @close="selectionMenuOpen = false"
            @set-project="$emit('set-project', $event)"
            @add-to-character="$emit('add-to-character', $event)"
            @remove-from-character="$emit('remove-from-character', $event)"
            @added-to-set="$emit('added-to-set', $event)"
            @remove-from-stack="$emit('remove-from-stack')"
            @create-stack="$emit('create-stack')"
            @dissolve-stacks="$emit('dissolve-stacks')"
            @stack-move="$emit('stack-move', $event)"
            @open-stack="$emit('open-stack')"
            @create-stacks-from-groups="$emit('create-stacks-from-groups')"
            @open-tag-input="openTagInput()"
            @auto-tag="$emit('auto-tag', $event)"
            @generate-description="$emit('generate-description', $event)"
            @open-plugin-panel="openPluginPanel()"
            @make-more="$emit('make-more')"
            @run-workflow="$emit('run-workflow')"
            @edit-pictures="$emit('edit-pictures')"
            @compare-pictures="$emit('compare-pictures')"
            @reverse-image-search="$emit('reverse-image-search')"
            @segment="$emit('segment')"
            @rotate-left="$emit('rotate-left')"
            @rotate-right="$emit('rotate-right')"
            @remove-from-group="$emit('remove-from-group')"
            @keep-cover-only="$emit('keep-cover-only')"
            @delete-selected="$emit('delete-selected')"
            @move-to-library="$emit('move-to-library', $event)"
          />
        </v-menu>
        <span class="pill-divider" aria-hidden="true"></span>
        <!-- The destructive verb sits past the rule, in the danger ink, and
             keeps a group gap from Clear: two identical 32px round buttons
             side by side, one of them destructive, is the adjacency this pill
             can least afford. -->
        <AppBarButton
          class="delete-btn"
          shape="round"
          data-verb="scrapheap"
          icon="delete"
          danger
          :disabled="!hasSelection || isReadOnly"
          :tooltip="deleteTitle"
          @click="$emit('delete-selected')"
        />
        <AppBarButton
          icon="close"
          shape="round"
          data-verb="clear"
          :disabled="!hasSelection"
          :tooltip="clearTitle"
          :aria-keyshortcuts="ownsEscape ? 'Escape' : undefined"
          @click="$emit('clear-selection')"
        />
    </div>
    <!-- /selection-ctx-bar -->
  </div>
</template>

<script setup>
import { withRef } from "../../utils/withRef.js";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { isReadOnly } from "../../utils/apiClient";
import { useGenStackPrefsStore } from "../../stores/useGenStackPrefsStore";
import { useUpscaleStore } from "../../stores/useUpscaleStore";
import { useExportStore } from "../../stores/useExportStore";
import { useTrainingStore } from "../../stores/useTrainingStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import {
  ALL_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
} from "../../stores/useViewStore";
import SelectionMenu from "./SelectionMenu.vue";
import TbExportPanel from "./TbExportPanel.vue";
import PluginParametersUI from "../widgets/PluginParametersUI.vue";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { isEditableElement } from "../../utils/dom.js";
import { favouriteVerb } from "../../stores/useFavouritesStore";

const props = defineProps({
  selectedCount: Number,
  selectedExpandedCount: { type: Number, default: 0 },
  selectedFaceCount: { type: Number, default: 0 },
  selectedGroupName: String,
  selectedSort: { type: String, default: "" },
  /**
   * Esc reaches THIS half. Esc peels one layer per press (open menu → the
   * selection → the search), so while anything is selected the keycap belongs
   * here and the search half must not also claim it.
   */
  ownsEscape: { type: Boolean, default: true },
  /**
   * The search half shares the pill: the verbs drop their words for their
   * glyphs, so the two halves fit side by side without wrapping
   * (merged-grid-action-pill.md §7). Each keeps its name in aria-label.
   */
  compact: { type: Boolean, default: false },
  scrapheapPicturesId: { type: String, required: true },
  selectedImageIds: { type: Array, default: () => [] },
  selectedMediaSupport: {
    type: Object,
    default: () => ({ hasImages: false, hasVideos: false }),
  },
  comfyuiConfigured: { type: Boolean, default: false },
  // What the selection is as far as stacks go (see EditStackMenu's `target`).
  stackEdit: { type: Object, default: null },
  // Forwarded straight to SelectionMenu: Keep cover only lives in the overflow
  // only, never as a top-level pill button.
  keepCoverOnlyStackCount: { type: Number, default: 0 },
  keepCoverOnlyLockReason: { type: String, default: null },
  // Forwarded straight to SelectionMenu, like the two above: the rotate pair
  // lives in the overflow only, never as a top-level pill button.
  rotateBlockReason: { type: String, default: null },
  compareBlockReason: { type: String, default: null },
  groupingLockReason: { type: String, default: null },
  availablePlugins: { type: Array, default: () => [] },
  taggerPlugins: { type: Array, default: () => [] },
  captionerPlugins: { type: Array, default: () => [] },
  allGridImages: { type: Array, default: () => [] },
  selectedCharacter: String,
  impossibleSources: { type: Array, default: () => [] },
  clearingImpossible: { type: Boolean, default: false },
});

const emit = defineEmits([
  "clear-selection",
  "favourite",
  "added-to-set",
  "create-set",
  "remove-from-group",
  "keep-cover-only",
  "delete-selected",
  "move-to-library",
  "set-project",
  "add-to-character",
  "remove-from-character",
  "create-stack",
  "remove-from-stack",
  "dissolve-stacks",
  "stack-move",
  "open-stack",
  "create-stacks-from-groups",
  "run-plugin",
  "tags-applied",
  "auto-tag",
  "generate-description",
  "reverse-image-search",
  "segment",
  "rotate-left",
  "rotate-right",
  "selection-menu-open",
  "clear-impossible-tags",
  "make-more",
  "run-workflow",
  "edit-pictures",
  "compare-pictures",
  "confirm-export-zip",
  "confirm-export-folder",
]);

const upscale = useUpscaleStore();
const exportStore = useExportStore();
const trainingStore = useTrainingStore();
const selectionStore = useSelectionStore();
const entityNames = useEntityNamesStore();
const lockedSetsStore = useLockedSetsStore();

// ── Train LoRA: single-set / single-character scope gating ───────────────────
// A run trains on one set or one character (a character's reference set is
// chosen inside the dialog). Enable the button only when the current view is
// exactly that; otherwise disable with a teaching tooltip.
const TRAIN_SPECIAL_CHARACTER_IDS = new Set([
  ALL_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
]);

const trainScope = computed(() => {
  const sel = selectionStore;
  const setIds = Array.isArray(sel.selectedSetIds) ? sel.selectedSetIds : [];
  const charIds = Array.isArray(sel.selectedCharacterIds)
    ? sel.selectedCharacterIds
    : [];
  // Single set: one set selected, no multi-set overlap. A character next to a
  // set is ignored here, as the grid ignores it (useGridFetch sends only the
  // set_id), so the scope is the whole set the view shows.
  const singleSetId =
    sel.selectedSet != null && sel.selectedSet !== "" && setIds.length <= 1
      ? Number(sel.selectedSet)
      : null;
  if (singleSetId != null && Number.isFinite(singleSetId)) {
    return {
      type: "set",
      id: singleSetId,
      name: entityNames.setNames[singleSetId] || sel.lastSelectedSetLabel,
    };
  }
  // Single character: a real character id (not ALL/UNASSIGNED/SCRAPHEAP), not a
  // multi-character view, and no set selected.
  const charId = sel.selectedCharacter;
  const isSpecial =
    charId == null || TRAIN_SPECIAL_CHARACTER_IDS.has(String(charId));
  if (!isSpecial && charIds.length <= 1 && !setIds.length) {
    const numId = Number(charId);
    if (Number.isFinite(numId)) {
      return {
        type: "character",
        id: numId,
        name:
          entityNames.characterNames[numId] ||
          sel.lastSelectedCharacterLabel,
      };
    }
  }
  return null;
});

const canTrain = computed(() => trainScope.value != null && !isReadOnly.value);

// Open the Train a LoRA dialog on the set or character in view.
function openTrainDialog() {
  const scope = trainScope.value;
  if (!scope) return;
  trainingStore.openForScope({
    type: scope.type,
    id: scope.id,
    name: scope.name,
  });
}

const canUpscale = computed(
  () =>
    props.selectedCount > 0 &&
    props.comfyuiConfigured &&
    !isScrapheapView.value &&
    !isReadOnly.value &&
    props.selectedMediaSupport?.hasImages === true &&
    props.selectedMediaSupport?.hasVideos !== true,
);

const isScrapheapView = computed(() => {
  const scrapheapId = String(
    props.scrapheapPicturesId || "SCRAPHEAP",
  ).toUpperCase();
  const selected = String(props.selectedCharacter || "").toUpperCase();
  return selected === scrapheapId;
});

const hasSelection = computed(
  () => props.selectedCount > 0 || props.selectedFaceCount > 0,
);

// Unfavourite only when every selected picture already is one; a mixed
// selection reads Favourite and makes them all favourites.
const favouriteOn = computed(() => {
  const ids = new Set((props.selectedImageIds ?? []).map(String));
  const rows = (props.allGridImages ?? []).filter(
    (img) => img?.id != null && ids.has(String(img.id)),
  );
  return rows.length > 0 && rows.every((img) => img.favourite);
});
const favouriteLabel = computed(() => favouriteVerb(favouriteOn.value));

// One number opens the half. Pictures and faces are different units, so when
// both are live they are stated as two, never summed.
const selectionCountLabel = computed(() => {
  if (props.selectedCount > 0 && props.selectedFaceCount > 0) {
    return `${props.selectedCount} selected · ${props.selectedFaceCount} faces`;
  }
  if (props.selectedCount > 0) return `${props.selectedCount} selected`;
  return `${props.selectedFaceCount} faces selected`;
});

// Stacks count once in the label; the hover says what they hold.
const countTitle = computed(() =>
  props.selectedExpandedCount > props.selectedCount
    ? `${props.selectedExpandedCount} total including stacks`
    : undefined,
);

const triggerTitle = computed(() =>
  hasSelection.value
    ? `More actions for ${selectionCountLabel.value}`
    : "Select images to apply actions",
);

const addToSetTitle = computed(
  () => props.groupingLockReason || "Add to set (S)",
);

// The live label names what lands in the zip before the panel is open (whose
// count is only refreshed once it is).
const exportTitle = computed(() => {
  const n = props.selectedCount;
  return `Export ${n} picture${n === 1 ? "" : "s"}`;
});

const clearTitle = computed(() =>
  props.ownsEscape ? "Clear selection (Esc)" : "Clear selection",
);

// "Delete" mis-set the expectation in the more alarming direction: outside the
// scrapheap the action moves pictures there, records to the operation log and
// raises an undoable receipt. Inside it, it is the irreversible one.
const deleteTitle = computed(() => {
  const n = props.selectedCount;
  return isScrapheapView.value
    ? `Delete ${n} forever (Del)`
    : `Move ${n} to Scrapheap (Del)`;
});

const pluginOptions = computed(() => {
  if (!Array.isArray(props.availablePlugins)) return [];
  const hasImages = props.selectedMediaSupport?.hasImages === true;
  const hasVideos = props.selectedMediaSupport?.hasVideos === true;
  return props.availablePlugins.filter((plugin) => {
    if (!plugin || !plugin.name) return false;
    const supportsImages = plugin.supports_images !== false;
    const supportsVideos = plugin.supports_videos === true;
    if (hasImages && !supportsImages) return false;
    if (hasVideos && !supportsVideos) return false;
    return true;
  });
});

const selectedPluginName = ref("");
const pluginMenuOpen = ref(false);
const selectionMenuOpen = ref(false);
const selectionMenuRef = ref(null);

function handleSelectionMenuHotkey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (isEditableElement(event.target)) return;
  if (isEditableElement(document.activeElement)) return;
  // Down while menu is open and focus is outside panel → focus first item
  if (event.key === "ArrowDown" && selectionMenuOpen.value) {
    if (selectionMenuRef.value?.containsFocus?.()) return;
    event.preventDefault();
    nextTick(() => selectionMenuRef.value?.focusFirst());
    return;
  }
  if (event.key !== "s" && event.key !== "S") return;
  if (!showTagControls.value || props.groupingLockReason) return;
  // The lightbox owns its own S (its sidebar), and an open menu or dialog its
  // own keys.
  // (A tooltip is an overlay too, so only menus and dialogs count.)
  if (
    document.querySelector(
      ".image-overlay, .v-menu.v-overlay--active, .v-dialog.v-overlay--active",
    )
  ) {
    return;
  }
  if (openAddToSet()) event.preventDefault();
}

// S is Add to set (#2160). The picker opens from a real click on its own
// trigger, the same way T opens the tag panel. True when it opened.
const addToSetRef = ref(null);
function openAddToSet() {
  const root = addToSetRef.value?.$el ?? addToSetRef.value;
  const trigger = root?.querySelector?.(".ate-btn");
  if (root?.classList?.contains("open") || !trigger || trigger.disabled) {
    return false;
  }
  trigger.click();
  return true;
}

onMounted(() => window.addEventListener("keydown", handleSelectionMenuHotkey));
onUnmounted(() => {
  window.removeEventListener("keydown", handleSelectionMenuHotkey);
});

// The grid's Esc ladder stands aside while a surface opened from the pill is up,
// so the Esc that closes it does not also clear the selection under it. The
// Export panel and the Train LoRA dialog joined the ⋯ menu in this with #2160.
// Immediate: the pill mounts with the selection, and the Train LoRA dialog can
// already be up then (the sidebar opens it too).
watch(
  () =>
    selectionMenuOpen.value ||
    exportStore.exportMenuOpen ||
    trainingStore.trainDialogOpen,
  (open) => emit("selection-menu-open", open),
  { immediate: true },
);

const pluginParameters = ref({});

// Remembered "stack outputs with originals" prefs (persisted in localStorage).
const genStackPrefs = useGenStackPrefsStore();
const stackFilterOutputs = computed({
  get: () => genStackPrefs.stackFilterOutputs,
  set: (val) => genStackPrefs.setStackFilterOutputs(val),
});

const activePluginSchema = computed(() => {
  if (!selectedPluginName.value) return null;
  return (
    pluginOptions.value.find(
      (plugin) => String(plugin.name) === String(selectedPluginName.value),
    ) || null
  );
});

watch(
  pluginOptions,
  (plugins) => {
    if (!Array.isArray(plugins) || !plugins.length) {
      selectedPluginName.value = "";
      return;
    }
    if (!selectedPluginName.value) {
      selectedPluginName.value = String(plugins[0].name);
      return;
    }
    const stillExists = plugins.some(
      (plugin) => String(plugin.name) === String(selectedPluginName.value),
    );
    if (!stillExists) {
      selectedPluginName.value = String(plugins[0].name);
    }
  },
  { immediate: true },
);

watch(selectedPluginName, () => {
  pluginParameters.value = {};
});

watch(pluginMenuOpen, (isOpen) => {
  if (!isOpen) return;
  if (!selectedPluginName.value && pluginOptions.value.length) {
    selectedPluginName.value = String(pluginOptions.value[0].name);
  }
  pluginParameters.value = {};
});

// The plugin-run-controls div uses v-if. If the
// menu is open when the v-if condition transitions to false (e.g. selection
// cleared by ESC before Vuetify can emit update:modelValue), the VMenu
// unmounts without resetting the ref, leaving it true. The watcher below
// resets the ref whenever the hosting condition goes false so the panel
// does not auto-reopen when the condition becomes true again.
const showPluginControls = computed(
  () =>
    props.selectedCount > 0 &&
    !isScrapheapView.value &&
    pluginOptions.value.length > 0 &&
    !isReadOnly.value,
);
watch(showPluginControls, (shown) => {
  if (!shown) pluginMenuOpen.value = false;
});

function runSelectedPlugin() {
  if (!selectedPluginName.value) return;
  emit("run-plugin", {
    pluginName: selectedPluginName.value,
    pictureIds: props.selectedImageIds,
    parameters: pluginParameters.value || {},
    stack: stackFilterOutputs.value,
  });
  pluginMenuOpen.value = false;
}

function handlePluginMenuEsc(event) {
  if (!pluginMenuOpen.value) return;
  event.preventDefault();
  event.stopPropagation();
  if (typeof event.stopImmediatePropagation === "function") {
    event.stopImmediatePropagation();
  }
  pluginMenuOpen.value = false;
}

// ── Tag ───────────────────────────────────────────────────────────────────────
// Tagging happens in the grid's rail (RailTags): T and the Tag verb open it and
// put the caret in its tag field.
const sidebarStore = useSidebarStore();
const showTagControls = computed(
  () => props.selectedCount > 0 && !isScrapheapView.value && !isReadOnly.value,
);

function openTagInput() {
  if (showTagControls.value) sidebarStore.requestTagField();
}

function openPluginPanel() {
  if (pluginMenuOpen.value) return;
  // Ensure a plugin is selected (watcher is immediate, but guard anyway)
  if (!selectedPluginName.value && pluginOptions.value.length) {
    selectedPluginName.value = String(pluginOptions.value[0].name);
  }
  // nextTick lets any in-progress Vue render cycle complete (e.g. a context
  // menu closing on the same tick) before we open the overlay.
  nextTick(() => {
    pluginMenuOpen.value = true;
  });
}

defineExpose({ openTagInput, openPluginPanel });
</script>

<style scoped>
/* No box of its own: the pill surface, the seam, the motion and the bottom-edge
   anchor all live in GridActionPill. `display: contents` lets these controls sit
   directly in the pill's flex run, so the half adds no nesting to the layout. */
.selection-bar {
  display: contents;
}

.selection-ctx-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* 8px here plus the run's own 8px gap = the --space-5 group gap that keeps the
   destructive control off Clear's elbow. */
.delete-btn {
  margin-right: var(--space-3);
}

/* The count opens the half as text, not as a control (#2160): ⋯ is the menu. */
.selection-count {
  padding: 0 var(--space-2);
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* The rules inside the half: after the count, and before the destructive pair.
   `divider`, not the seam's `border`: these part runs of one context, the seam
   parts two. */
.pill-divider {
  width: 1px;
  height: var(--rule-h);
  background: rgb(var(--v-theme-divider));
  flex-shrink: 0;
}

.pill-verb-kbd {
  margin-left: var(--space-1);
}

/* The set picker's own trigger, dressed as the round bar verbs beside it
   (AppBarButton, shape round). Its `:where()` skin leaves the specificity for
   exactly this. */
.pill-ate :deep(.ate-btn) {
  height: var(--control-h-bar);
  padding: 0 var(--space-3);
  gap: var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  background: transparent;
  color: inherit;
  font-size: var(--text-base);
  font-weight: var(--weight-regular);
  white-space: nowrap;
}
.pill-ate :deep(.ate-btn:hover:not(:disabled)) {
  background: var(--hover-wash);
}
.pill-ate.open :deep(.ate-btn) {
  background: var(--hover-wash);
  border-color: rgb(var(--v-theme-border));
}
.pill-ate :deep(.ate-chevron) {
  display: none;
}

/* Hidden panel activators - zero-size but remain in DOM for menu positioning */
.hidden-panel-activator {
  display: block;
  width: 0;
  min-width: 0;
  height: 0;
  padding: 0;
  margin: 0;
  border: none;
  background: none;
  overflow: hidden;
  pointer-events: none;
}

.plugin-run-controls {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

.plugin-menu-panel {
  width: 420px;
  max-width: min(92vw, 560px);
  background: rgba(var(--v-theme-surface), 0.96);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-3);
}

.plugin-menu-header {
  font-size: var(--text-base);
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  padding: 10px var(--space-4);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}

.plugin-menu-body {
  padding: 10px var(--space-4);
}

.plugin-menu-label {
  display: block;
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: var(--space-2);
  opacity: 0.9;
}

.plugin-menu-checkbox-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-4);
  font-size: var(--text-base);
  cursor: pointer;
}

.plugin-menu-checkbox-row input {
  cursor: pointer;
}

.plugin-menu-actions {
  margin-top: var(--space-4);
  display: flex;
  justify-content: flex-end;
}

.plugin-run-select {
  height: var(--control-h-bar);
  width: 100%;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-primary), 0.4);
  background: rgba(var(--v-theme-background), 0.7);
  color: rgb(var(--v-theme-on-background));
  padding: 0 var(--space-3);
}

.bar-btn-apply-label {
  white-space: nowrap;
  font-size: var(--text-base);
  flex-shrink: 1;
}

:deep(.bar-btn-chevron) {
  flex-shrink: 0;
}

/* The ladder drops the label of the conditional action first and NEVER the
   count: losing a selection count is worse than losing any word beside it
   (merged-grid-action-pill.md §7). The old rule hid both at 660px together. */
@container selbar (max-width: 1100px) {
  .clear-impossible-label,
  .pill-verb-kbd,
  .pill-ate :deep(.ate-kbd) {
    display: none;
  }
}

/* Then the verbs fall back to their glyphs, each named by its aria-label, so
   the pill never wraps (merged-grid-action-pill.md §5). They do at any width
   while the search half shares the pill. */
.selection-bar--compact .pill-verb-label,
.selection-bar--compact .pill-verb-kbd,
.selection-bar--compact .pill-ate :deep(.ate-label),
.selection-bar--compact .pill-ate :deep(.ate-kbd) {
  display: none;
}
@container selbar (max-width: 900px) {
  .pill-verb-label,
  .pill-ate :deep(.ate-label) {
    display: none;
  }
}

@media (hover: none) and (pointer: coarse) {
  .bar-btn {
    height: var(--bar-height);
  }
  .bar-btn--icon {
    width: var(--bar-height);
  }
}
</style>
