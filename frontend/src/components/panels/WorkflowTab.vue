<template>
  <AppInspector
    v-model="tab"
    class="wftab"
    label="Inspector"
    :open="sidebarStore.workflowInspectorOpen"
    :tabs="tabs"
  >
    <!-- The task manager, last tab and on its own: what the app is working on
         is not part of a workflow, so it replaces the body rather than sitting
         under it. Here so that a run started from this screen can be watched
         from this screen. -->
    <!-- A stack selected whole reads as one workflow, its cover, with the
         other members a pick away: expanding the stack just to read one of
         them was the only way before. Outside the body below so a member's
         read, or its failure, never takes the picker (or its focus) away. -->
    <div
      v-if="tab === 'workflow' && stackCover"
      class="inspector-section wftab-head"
    >
      <AppSelect
        :model-value="selectedKey"
        class="wftab-pick"
        label="Workflow in this stack"
        hide-label
        :options="stackOptions"
        data-testid="wftab-stack-pick"
        @update:model-value="store.pickStackMember"
      />
    </div>

    <TasksPanel v-if="tab === 'tasks'" />

    <p v-else-if="!card && !multiple && !stackCover" class="wftab-empty">
      Pick a workflow to see what it is made of.
    </p>

    <!-- Before the multiple-selection body: a selection of several has a
         Recipes answer of its own, the union of their stacks, which is what
         the owner is asking for by selecting them. -->
    <WorkflowRecipesTab
      v-else-if="showingRecipes"
      :workflow-keys="recipeKeys"
      :stack-name="recipesStack.name"
      :stack-size="recipesStack.size"
    />

    <!-- Several selected: the tab says so, and the VERBS ARE THE PILL'S.
         They were here first, as two buttons of this tab's own; #1455 gave
         the grid a selection bar that offers the same two and eight more, and
         two surfaces deriving the same verb independently disagreed within a
         week — this Hide was hardcoded `Hide` while the pill's is a toggle
         reading Unhide on an all-hidden selection, and this Stack said "Stack
         together" where the pill says "Fuse into one stack" for a selection
         that already holds one. Two live controls on one screen, one of them
         saying the wrong thing about what it is about to do.

         So one owner. The pill is always on screen when this block is (both
         are gated on a selection, and it is docked over the grid this rail
         sits beside), so nothing became unreachable — and the reader is told
         where the verbs are rather than left to find them. -->
    <template v-else-if="multiple">
      <div class="inspector-section">
        <span class="section-label">Selected</span>
        <p class="wftab-title">
          {{ store.selectedKeys.length }} workflows selected
        </p>
        <p class="wftab-note wftab-quiet">
          What you can do with them is on the bar at the bottom of the grid, or
          under a right-click on any of them.
        </p>
      </div>
    </template>

    <!-- A stack member the grid does not list, while its read is out. -->
    <p v-else-if="!card" class="wftab-empty">
      {{
        detailFailed
          ? "Could not read this workflow just now."
          : "Reading this workflow…"
      }}
    </p>

    <template v-else>
      <div class="inspector-section wftab-head">
        <p v-if="!stackCover" class="wftab-title">{{ card.name }}</p>
        <p class="wftab-sub">
          {{ subtitlePrefix
          }}<button
            v-if="card.picture_count"
            class="wftab-pictures"
            type="button"
            data-testid="wftab-show-pictures"
            :aria-label="`Show all ${picturesLabel}`"
            @click="showPictures(card)"
          >
            {{ picturesLabel }}</button
          ><template v-else>{{ picturesLabel }}</template>
        </p>
      </div>

      <div class="inspector-section">
        <span class="section-label">Models</span>
        <div class="wftab-field">
          <span class="wftab-label">Checkpoint</span>
          <!-- Missing: ComfyUI does not have the file (the run pre-flight's
               answer), or - when ComfyUI cannot be asked - the card has no
               name for it. Always with the FILE it is missing, as its name
               without folders; the whole recorded value is the tooltip. -->
          <div v-if="detail && checkpointIsMissing" class="wftab-missing">
            <p class="wftab-warn">
              <v-icon size="16">mdi-alert-outline</v-icon>
              Checkpoint missing
            </p>
            <p
              v-if="missingCheckpointFile"
              class="wftab-note wftab-quiet"
              data-testid="wftab-missing-file"
            >
              <span class="wftab-file"
                ><Tooltip :text="missingCheckpointFile" activator="parent" />{{
                  fileName(missingCheckpointFile)
                }}</span
              >{{ preflightAnswered ? " is not installed in ComfyUI." : "" }}
            </p>
            <p v-else class="wftab-note wftab-quiet">
              No file name was kept for it anywhere.
            </p>
            <!-- The fix: another shelf model in its place. The card keeps its
                 pictures, and what the replacement makes is filed on it. -->
            <AppSelect
              v-if="replaceOptions.length"
              model-value=""
              label="Replace with a model from your shelf"
              hide-label
              :options="replaceOptions"
              :disabled="busy === 'model-fix'"
              data-testid="wftab-replace-model"
              @update:model-value="replaceCheckpoint"
            />
            <!-- The replacement has gone missing too: say what it was, and
                 keep the way back reachable. Choosing another above replaces
                 the original, never the replacement. -->
            <p
              v-if="replacementMissing"
              class="wftab-note wftab-quiet"
              data-testid="wftab-fix-missing"
            >
              Replaced by {{ fileName(checkpointFix.now) }}, which is missing
              too.
              <AppButton
                variant="ghost"
                size="sm"
                icon-only
                icon-left="undo"
                :tooltip="`Undo: load ${fileName(checkpointFix.was)} again`"
                :disabled="busy === 'model-fix'"
                @click="replaceCheckpoint(null)"
              />
            </p>
          </div>
          <!-- Replaced by the owner: what it loads now, flagged, and the
               original a hover away, because this is not the workflow as its
               pictures were made. -->
          <div
            v-else-if="checkpointFix"
            class="wftab-fixed"
            data-testid="wftab-fixed-model"
          >
            <span class="wftab-value">
              <v-icon size="16" class="wftab-fixed-flag" aria-hidden="true"
                >mdi-alert-outline</v-icon
              >
              <Tooltip
                :text="`Replaced. This workflow originally used ${fileName(checkpointFix.was)}`"
                activator="parent"
              />
              {{ fileName(checkpointFix.now) }}
              <span class="visually-hidden"
                >, replaced. This workflow originally used
                {{ fileName(checkpointFix.was) }}</span
              >
            </span>
            <AppButton
              variant="ghost"
              size="sm"
              icon-only
              icon-left="undo"
              data-testid="wftab-undo-fix"
              :tooltip="`Undo: load ${fileName(checkpointFix.was)} again`"
              :disabled="busy === 'model-fix'"
              @click="replaceCheckpoint(null)"
            />
          </div>
          <span v-else-if="checkpointLabel" class="wftab-value">{{
            checkpointLabel
          }}</span>
          <!-- The card has no name for it, but the graph a run submits does:
               that is the file, installed as far as anybody knows. -->
          <span v-else-if="graphBaseModel" class="wftab-value"
            ><Tooltip :text="graphBaseModel" activator="parent" />{{
              fileName(graphBaseModel)
            }}</span
          >
          <!-- The graph a run would submit was read and loads no base model:
               an upscaler, say. A fact, so it is said as one. -->
          <span v-else-if="graphLoadsNone" class="wftab-value wftab-quiet">
            None in this workflow
          </span>
          <!-- A recipe-less card's rows are what its file gave up, so an
               empty one is "not read", never a claim that it has none. -->
          <span v-else-if="checkpointIsUnread" class="wftab-value wftab-quiet">
            Not read from its file
          </span>
          <!-- Only when nothing anywhere names a base model: not the card, not
               the graph a run would submit, not the pre-flight. -->
          <span v-else class="wftab-value wftab-quiet">Not recorded</span>
        </div>
        <div class="wftab-field">
          <span class="wftab-label">VAE</span>
          <span class="wftab-value">{{ vaeLabel }}</span>
        </div>

        <!-- The chain, in the order it applies (#1478). "Edit LoRAs…" is
             here even with no loader at all: an entry point that only exists
             for workflows that already have LoRAs is how adding the first one
             stays unreachable. -->
        <div class="wftab-loras-head">
          <span class="wftab-label">LoRAs</span>
          <AppButton
            size="sm"
            data-testid="wftab-edit-loras"
            :disabled="chainNoGraph"
            :aria-describedby="chainNoGraph ? 'wftab-chain-reason' : undefined"
            @click="openEditLoras(selectedKey)"
          >
            Edit LoRAs…
          </AppButton>
        </div>
        <p v-if="chainPending" class="wftab-note wftab-quiet">
          Reading its LoRAs…
        </p>
        <p
          v-else-if="chainNoGraph"
          id="wftab-chain-reason"
          class="wftab-note wftab-quiet"
        >
          PixlStash has no graph for this workflow, so its LoRAs cannot be read
          or edited.
        </p>
        <p v-else-if="chainFailed" class="wftab-note wftab-quiet">
          Could not read its LoRAs just now.
        </p>
        <template v-else-if="chain">
          <ol
            v-if="chainLoaders.length"
            class="wftab-chain"
            aria-label="LoRAs, in the order the chain applies them"
          >
            <li
              v-for="loader in chainLoaders"
              :key="loader.node_id"
              class="wftab-chain-row"
            >
              <v-icon
                v-if="!loader.on_shelf"
                size="16"
                class="wftab-chain-flag"
                aria-hidden="true"
                >mdi-alert-outline</v-icon
              >
              <span class="wftab-chain-name">{{ loader.label }}</span>
              <span v-if="!loader.on_shelf" class="visually-hidden"
                >, not on your model shelf</span
              >
              <span class="wftab-chain-strength">{{ loader.strengthText }}</span>
            </li>
          </ol>
          <p
            v-if="chainLoaders.length"
            class="wftab-note wftab-quiet"
            data-testid="wftab-shelf-line"
          >
            In the order the chain applies them. {{ shelfLine }}
          </p>
          <p v-else class="wftab-note wftab-quiet">
            No LoRA loader. Editing adds the first one.
          </p>
        </template>

        <!-- Two lines per slot, as drawn: what is in it and how strong, then
             the switch that decides whether the slot is part of the workflow
             or part of the look. The switch re-keys the card, so it is a
             full-width control of its own and not a chip in the first line. -->
        <template v-if="loraSlots.length">
          <span class="wftab-label wftab-label--group">LoRA slots</span>
          <div v-for="slot in loraSlots" :key="slot.id" class="wftab-slot">
            <div class="wftab-slot-line">
              <span
                class="wftab-chip"
                :class="{ 'wftab-chip--empty': !slot.name }"
              >
                <Tooltip
                  v-if="slot.name"
                  :text="slot.name"
                  activator="parent"
                />
                <v-icon size="16">mdi-layers-outline</v-icon>
                {{ slot.chipText || "recipe LoRA" }}
              </span>
              <!-- No strength field. The card payload carries no strength
                   (`_describe_slots` reads filenames, and `FEATURED_NAMES`
                   has no `strength_model`), and an empty bordered box reads
                   as a control that is broken rather than as "not recorded".
                   It arrives with the data. -->
            </div>
            <!-- Disabled without a `slot_label`: the field is nullable
                 (`WorkflowSlotModel`), and the mark is written by label, so
                 an enabled switch here would be a control that answers a
                 click with nothing at all. -->
            <Segmented
              :options="MARK_OPTIONS"
              :model-value="slot.mark"
              full
              :disabled="!slot.label || Boolean(busy)"
              :aria-label="`Is ${slot.name || 'this recipe LoRA slot'} part of the workflow?`"
              @update:model-value="(mark) => flipMark(slot, mark)"
            />
            <p v-if="!slot.label" class="wftab-note wftab-quiet">
              This slot has no recorded address, so it cannot be marked.
            </p>
          </div>
        </template>
        <!-- Only while the chain has nothing to say: once it is read, "No LoRA
             loader" above is the same fact in the words that lead to Edit. -->
        <p v-else-if="!chain" class="wftab-note wftab-quiet">
          This workflow has no LoRA slot.
        </p>
      </div>

      <div class="inspector-section">
        <span class="section-label">
          Defaults
          <span class="wftab-legend"
            ><v-icon size="14">mdi-pin</v-icon> = shown in Run</span
          >
        </span>
        <p v-if="detailPending" class="wftab-note wftab-quiet">
          Reading its defaults…
        </p>
        <p v-else-if="detailFailed" class="wftab-note wftab-quiet">
          Could not read its defaults just now.
        </p>
        <template v-else-if="defaults.length">
          <p class="wftab-note wftab-quiet">
            The most used values among its pictures rated 4★ and up. Edit one to
            make it yours.
          </p>
          <WorkflowDefaultRow
            v-for="row in pinnedDefaults"
            :key="row.label"
            :row="row"
            :busy="busy === `default:${row.label}`"
            @toggle-pin="togglePin(row)"
            @reset="resetDefault(row)"
          />
          <!-- The whole set, pinned rows included, because "All N" is a count
               of the card's parameters and not of what is left over. -->
          <details v-if="unpinnedDefaults.length" class="wftab-disclose">
            <summary>All {{ defaults.length }} parameters</summary>
            <WorkflowDefaultRow
              v-for="row in unpinnedDefaults"
              :key="row.label"
              :row="row"
              :busy="busy === `default:${row.label}`"
              @toggle-pin="togglePin(row)"
              @reset="resetDefault(row)"
            />
          </details>
        </template>
        <p
          v-else-if="editorOnly"
          class="wftab-note wftab-quiet"
          data-testid="wftab-editor-only"
        >
          Parameters are not available yet. This is a ComfyUI editor file:
          open it in ComfyUI and use <strong>Convert for PixlStash</strong> to
          hand PixlStash the graph it runs.
        </p>
        <p v-else class="wftab-note wftab-quiet">
          Nothing this workflow made records a setting yet, so it has no
          defaults to start from.
        </p>
      </div>

      <div class="inspector-section">
        <!-- The box is drawn only once THIS card's notes have arrived.
             `notesDraft` holds the last card read, so a box shown while the
             read is out is the previous workflow's text, and blurring it
             writes that text onto this one. -->
        <details class="wftab-disclose">
          <summary>Notes</summary>
          <textarea
            v-if="detail"
            v-model="notesDraft"
            class="wftab-notes"
            rows="4"
            aria-label="Notes about this workflow"
            @blur="saveNotes"
          ></textarea>
          <p v-else class="wftab-note wftab-quiet">
            {{
              detailFailed
                ? "Could not read this workflow's notes just now."
                : "Reading its notes…"
            }}
          </p>
        </details>
        <details class="wftab-disclose">
          <summary>
            Node names <span class="wftab-quiet">for ComfyUI users</span>
          </summary>
          <dl class="inspector-kv">
            <div v-for="slot in nodeNames" :key="slot.id">
              <dt>{{ slot.kind }}</dt>
              <dd class="wftab-mono">{{ slot.label || "—" }}</dd>
            </div>
          </dl>
        </details>
      </div>
    </template>

    <!-- The inspector's footer slot sits below the scrolling body, so Run…
         stays where the design puts it and never scrolls. -->
    <!-- The Workflow tab's, and only its: Recipes runs a recipe from its own
         row and Tasks is the app's business, so neither wants this footer. -->
    <template #footer>
      <div
        v-if="tab === 'workflow' && (card || multiple || stackCover)"
        class="wftab-foot"
      >
        <AppButton
          variant="primary"
          icon-left="play"
          block
          :aria-disabled="runTarget ? undefined : 'true'"
          :aria-describedby="runDescribedBy"
          @click="run"
        >
          Run…
        </AppButton>
        <!-- Beside Run…, as the ComfyUI mark: the ComfyUI-PixlStash node reads
             `?pixlstash_workflow=` and loads the graph, so without the node
             ComfyUI opens on whatever it had last. It opens what Run… runs
             (`runTarget`), and is refused rather than hidden otherwise, for
             Run…'s reason or a ComfyUI without the node. The tooltip is its
             accessible name. -->
        <AppButton
          v-if="canOpenComfyui"
          icon-only
          tooltip="Open in ComfyUI"
          data-testid="wftab-open-comfyui"
          :aria-disabled="runTarget && !comfyuiLacksNode ? undefined : 'true'"
          :aria-describedby="openDescribedBy"
          @click="openInComfyui"
        >
          <template #icon="{ size }"><ComfyuiIcon :size="size" /></template>
        </AppButton>
        <v-menu
          v-if="card"
          v-model="menuOpen"
          location="top end"
          origin="bottom end"
          :offset="8"
        >
          <template #activator="{ props: menuProps }">
            <AppButton
              v-bind="menuProps"
              icon-left="dots-horizontal"
              icon-only
              tooltip="More"
              aria-haspopup="menu"
              :aria-expanded="menuOpen"
            />
          </template>
          <div class="tbm">
            <div class="tbm-section">
              <button class="wftab-item" type="button" @click="toggleHidden">
                <v-icon size="16">{{
                  detail?.hidden ? "mdi-eye-outline" : "mdi-eye-off-outline"
                }}</v-icon>
                {{ detail?.hidden ? "Unhide" : "Hide" }}
              </button>
            </div>
          </div>
        </v-menu>
        <p
          v-if="multiple && canOpenComfyui"
          id="wftab-open-reason"
          class="wftab-note wftab-quiet"
        >
          Open one workflow, or one whole stack, at a time
        </p>
        <p
          v-else-if="comfyuiLacksNode && canOpenComfyui"
          id="wftab-open-node-reason"
          class="wftab-note wftab-quiet"
        >
          Opening a workflow needs the ComfyUI-PixlStash node in ComfyUI.
          Install or update it, then restart ComfyUI.
        </p>
        <p v-if="multiple" id="wftab-run-reason" class="wftab-note wftab-quiet">
          Run one workflow, or one whole stack, at a time
        </p>
      </div>
    </template>

    <!-- Keyed to the card it was OPENED on, not to the selection: a save
         selects the new card, and the dialog must not re-read the chain of
         the card it just wrote under the owner's feet. -->
    <EditLorasDialog
      v-if="editKey"
      :open="Boolean(editKey)"
      :workflow-key="editKey"
      :card-name="editName"
      :picture-count="editPictures"
      :drop-lora="editDrop"
      @close="closeEditLoras"
    />
  </AppInspector>
</template>

<script setup>
// The Workflow tab of the Workflows grid's inspector (implementation plan §F3).
//
// Built as a sibling of the shipped shelf's own inspector rather than a
// reshaping of it, because every step of this feature had to leave the shelf
// on `/workflows` working; F1b (#1404) then deleted the shelf, its store and
// that inspector together, and this is the rail on `/workflows`.
//
// What the rail shows follows the SELECTION, not the open stack: a stack
// member selected inside its panel shows that member here, and a stack
// selected whole shows its cover with a picker for the other members.

import { computed, onBeforeUnmount, ref, toRaw, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon, VMenu } from "vuetify/components";

import {
  getLoraChain,
  getWorkflowCard,
  patchWorkflowCard,
  preflightWorkflowRun,
  readModelSwap,
  setWorkflowDefaults,
  setWorkflowModelFix,
  setWorkflowPins,
  setWorkflowSlots,
  workflowCoverUrl,
} from "../../api/workflows";
import { getPixlstashNode } from "../../api/comfyui";
import { useWorkflowPictures } from "../../composables/useWorkflowPictures";
import { useFilterStore } from "../../stores/useFilterStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import { EDIT_LORAS, loraStem } from "../../utils/loraChain";
import { quantBadge } from "../../utils/modelShelf";
import {
  checkpointMissing,
  checkpointModel,
  checkpointUnread,
  modelDisplayName,
  stackMemberOptions,
} from "../../utils/workflowCard";
import AppButton from "../widgets/AppButton.vue";
import AppInspector from "../widgets/AppInspector.vue";
import AppSelect from "../widgets/AppSelect.vue";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import EditLorasDialog from "../io/EditLorasDialog.vue";
import Segmented from "../widgets/Segmented.vue";
import TasksPanel, { tasksTabFor } from "./TasksPanel.vue";
import Tooltip from "../widgets/Tooltip.vue";
import WorkflowDefaultRow from "./WorkflowDefaultRow.vue";
import WorkflowRecipesTab from "./WorkflowRecipesTab.vue";

/** B1's vocabulary, in the design's words. */
const MARK_OPTIONS = [
  { id: "structural", label: "Workflow" },
  { id: "recipe", label: "Recipe" },
];

/**
 * What a card shows before "All N parameters" when nobody has pinned on it.
 *
 * The design's own choice: the numbers a person changes between runs are up
 * top and the sampler and scheduler are not. `null` pins from the server mean
 * "no choice made", which is deliberately not the same as an empty list.
 */
const DEFAULT_PINS = ["steps", "cfg", "guidance", "width", "height"];

const store = useWorkflowsStore();
const { showPictures } = useWorkflowPictures();
const sidebarStore = useSidebarStore();
const notices = useNoticeStore();
const filterStore = useFilterStore();
/**
 * The desktop shell's bridge, when there is one. Its window opens only `https:`
 * outside the app and ComfyUI is plain `http:`, so on the desktop the link goes
 * through `desktop:openComfyui` instead of `window.open`.
 */
const desktop = typeof window !== "undefined" ? window.pixlstashDesktop : null;

/** Whether there is a ComfyUI to open, and a way to open it from here. */
const canOpenComfyui = computed(
  () => Boolean(filterStore.comfyuiUrl) && (!desktop || !!desktop.openComfyui),
);

/**
 * ComfyUI answered that it lacks the ComfyUI-PixlStash node's
 * `open_workflow.js`, so a link would open on whatever it had last. Only a
 * definite no refuses: an unreachable ComfyUI, or a failed ask, leaves the
 * button to try.
 */
const comfyuiLacksNode = ref(false);
let nodeCheck = 0;
watch(
  () => canOpenComfyui.value && filterStore.comfyuiUrl,
  async (url) => {
    const check = ++nodeCheck;
    comfyuiLacksNode.value = false;
    if (!url) return;
    try {
      const { can_open_workflows: canOpen } = await getPixlstashNode();
      if (check === nodeCheck) comfyuiLacksNode.value = canOpen === false;
    } catch (err) {
      console.warn("[workflows] could not ask ComfyUI for the PixlStash node", err);
    }
  },
  { immediate: true },
);
const runDialog = useRunDialogStore();
const tasksStore = useTasksStore();

const route = useRoute();
const router = useRouter();

const tab = ref("workflow");
// A deep link to the Tasks tab, from a notice or a banner (`showTasksTab`).
// This rail is the one a run is usually started from, so it is the one the
// toast's *Show* has to land on.
watch(
  () => sidebarStore.tasksTabRequest,
  () => {
    tab.value = "tasks";
  },
);

// `?tab=recipes`, from the lightbox banner naming the recipe a picture matches
// (#1480). The card itself is selected by `?topology=`, which `WorkflowsView`
// honours; this is the other half, and **it opens the rail too** - landing on
// a selected card with the rail shut is the same dead end the link was for.
//
// Honoured on the value rather than once per visit: the query is a one-shot
// instruction the URL goes on carrying, so a watcher that re-fired on every
// route change would take the tab back off whatever the reader had chosen.
watch(
  () => route.query?.tab,
  (wanted) => {
    if (wanted !== "recipes") return;
    tab.value = "recipes";
    sidebarStore.openWorkflowInspector();
  },
  { immediate: true },
);

// Tasks last, and never anything but last: it is the app's business, not this
// workflow's.
/**
 * Recipes first, as the design bands them; Workflow is still the tab a
 * selection opens on, because it is the one that says what the card IS; and
 * Tasks last, and never anything but last, because it is the app's business
 * and not this workflow's.
 *
 * Recipes answers for a selection of several too — the union of their stacks —
 * so it is never disabled. The Workflow tab is the one with nothing to say
 * about several cards at once.
 */
const tabs = computed(() => [
  { value: "recipes", label: "Recipes", icon: "mdi-bookmark-outline" },
  { value: "workflow", label: "Workflow", icon: "mdi-sitemap-outline" },
  tasksTabFor(tasksStore),
]);

/** The detail of the selected card, and whether its read is still out. */
const detail = ref(null);
const detailPending = ref(false);
const detailFailed = ref(false);
/** One write at a time, named so only the control that fired it shows it. */
const busy = ref("");
const menuOpen = ref(false);
const notesDraft = ref("");

/**
 * The cover of a stack selected WHOLE, or null.
 *
 * Several keys, but one card on screen: the rail shows it as one workflow,
 * the cover by default, with a picker for the other members. The pick is the
 * store's (`stackPickKey`), because the grid's Run follows it too.
 */
const stackCover = computed(() => store.stackCover);

/** Several cards selected that are not one whole stack. */
const multiple = computed(
  () => store.selectedKeys.length > 1 && !stackCover.value,
);

/** The stack's members, from the cover's own card, which lists them in order. */
const stackOptions = computed(() => {
  const cover = stackCover.value;
  if (!cover) return [];
  // One line each: the rail's picker stays a native select, so the two-line
  // chip rows the Run popup draws are left out here.
  const rows = stackMemberOptions(cover.members).map(({ value, label }) => ({
    value,
    label,
  }));
  return rows.length ? rows : [{ value: cover.key, label: cover.name }];
});

/**
 * The one card the body reads: the single selection, or the stack member
 * picked. Every read and write below goes through this.
 */
const selectedKey = computed(() =>
  store.selectedKeys.length === 1
    ? store.selectedKeys[0]
    : store.stackPickKey,
);

/**
 * The selected card, wherever it sits.
 *
 * A member is never in `cards` — the grid lists one card per stack — so the
 * fetched member lists are searched too. Without that, selecting a row inside
 * an open stack panel emptied the rail.
 */
const card = computed(() => {
  const key = selectedKey.value;
  if (!key) return null;
  const top = store.cards.find((entry) => entry.key === key);
  if (top) return top;
  for (const group of Object.values(store.members)) {
    const found = group.find((entry) => entry.key === key);
    if (found) return found;
  }
  // Last: the card the detail read brought back. The grid lists one card per
  // stack and leaves out hidden cards and one-offs, so a selected card is not
  // always a listed one — and a mark flip that merged this card into somebody
  // else's stack lands exactly there, which used to empty the rail on the one
  // gesture this tab exists for.
  if (detail.value?.card?.key === key) return detail.value.card;
  return null;
});

/**
 * Whether the Recipes list is the body on screen.
 *
 * Not `tab === "recipes"` alone: with nothing selected the empty sentence is
 * the body, and gating the footer on the tab would take Run… off a screen
 * still showing the Workflow body — Run… has to stay on screen and refuse
 * rather than vanish.
 */
const showingRecipes = computed(
  () => tab.value === "recipes" && recipeKeys.value.length > 0,
);

/** Every selected card, because a selection's recipes are their union. */
const recipeKeys = computed(() =>
  store.selectedKeys.length > 1
    ? [...store.selectedKeys]
    : card.value
      ? [card.value.key]
      : [],
);

/**
 * What heads the Recipes tab: the stack, when the selected card is a member.
 *
 * `stack_size` rather than `member_keys.length`, which leaves the card's own
 * key out and would call a stack of six "five workflows".
 */
const recipesStack = computed(() => {
  // A stack selected whole heads its Recipes as the stack, not as a count.
  if (stackCover.value) {
    return {
      name: stackCover.value.name || "",
      size: Number(stackCover.value.stack_size) || 0,
    };
  }
  if (multiple.value) {
    const count = store.selectedKeys.length;
    return { name: `${count} workflows selected`, size: 0 };
  }
  const stack = parentStack.value || card.value;
  return {
    name: stack?.name || "",
    size: Number(stack?.stack_size) || 0,
  };
});

/** The stack this card sits in, when it is a member of one. */
const parentStack = computed(() => {
  const key = selectedKey.value;
  if (!key || !card.value || card.value.key === store.openStackKey) return null;
  return (
    store.cards.find(
      (entry) => entry.key !== key && (entry.member_keys ?? []).includes(key),
    ) ?? null
  );
});

// Plain numbers, as `WorkflowsView`'s own subtitle writes them: the shelf's
// grouped spelling went with the shelf in F1b, and this screen never used it.
// Split from its prefix so the figure alone is F7's *Show all N pictures*
// link, and the words that place the card stay text.
const picturesLabel = computed(() => {
  const count = card.value?.picture_count ?? 0;
  return `${count} ${count === 1 ? "picture" : "pictures"}`;
});

const subtitlePrefix = computed(() => {
  if (stackCover.value) {
    return `A stack of ${stackCover.value.stack_size} workflows · `;
  }
  if (parentStack.value) return `In the ${parentStack.value.name} stack · `;
  if ((card.value?.stack_size ?? 1) > 1) return "Showing the cover · ";
  return "";
});

// All three read the MODEL SHELF's name where it has one, the same preference
// the card's name row was built from, so the panel and the row do not describe
// one model twice (`modelDisplayName`).
//
// **And all three carry the precision**, because the server has taken it out
// of `name`: without the badge, two quant builds of one model read identically
// here, which is the failure `derive_model_name`'s own docstring names. It is
// appended to the value rather than given a chip of its own - these are
// label/value rows, not a chip row, and `· FP8` is how the shelf's own file
// line already says a second fact about one file.
function withQuant(text, model) {
  const badge = quantBadge(model?.quant);
  // FP8's shared compact label loses E4M3 versus E5M2. Other badges already
  // carry their full identity in their label, and keep this detail row concise.
  const detail = badge?.label === "FP8" ? badge.title : badge?.label;
  return detail ? `${text} · ${detail}` : text;
}

// The base model, as the card's own row picks it: a Flux or SD3 graph has a
// `unet` and no `checkpoint`, and reading "checkpoint" alone called that
// missing.
const checkpointLabel = computed(() => {
  const found = card.value ? checkpointModel(card.value) : null;
  const name = modelDisplayName(found);
  return name ? withQuant(name, found) : "";
});

const checkpointIsUnread = computed(() =>
  card.value ? checkpointUnread(card.value) : false,
);

/** ComfyUI's folders for a base model, as `missing_models` names them. */
const BASE_MODEL_FOLDERS = new Set(["checkpoints", "diffusion_models"]);

/** What the pre-flight reports for a name the hub forgot: names no file. */
const FORGOTTEN_MODEL = "(forgotten model)";

/** How long a selection has to settle before ComfyUI is asked about it. */
const PREFLIGHT_SETTLE_MS = 250;

/**
 * The base-model files the run pre-flight says ComfyUI does not have.
 *
 * The card cannot answer this: it names what the recipe recorded, and only
 * ComfyUI's own model list says whether that file is installed. So the tab
 * asks the same question Run… asks, once per card. `preflightAnswered` says
 * whether it has: an unreachable ComfyUI leaves the card's own answer.
 */
const missingBaseFiles = ref([]);
const preflightAnswered = ref(false);
let installedCheck = 0;
// A rail that has closed asks nothing: an ask still settling is superseded.
onBeforeUnmount(() => {
  installedCheck += 1;
});

/**
 * The base-model file the graph a run would submit names, for a card that
 * has no name of its own for it (`graph_base_models` on the detail).
 */
const graphBaseModel = computed(
  () => detail.value?.graph_base_models?.[0] || "",
);

/** The graph was read and names no base model (`[]`, not `null`). */
const graphLoadsNone = computed(
  () =>
    Array.isArray(detail.value?.graph_base_models) &&
    detail.value.graph_base_models.length === 0,
);

/**
 * Whether the base model will not load.
 *
 * Once ComfyUI has answered, its answer: a checkpoint the card cannot name
 * but ComfyUI has is not missing. Until then, and when it cannot be asked, the
 * card's own: a base-model slot with no name. A graph with no such slot (an
 * upscaler) is never missing one.
 */
const checkpointIsMissing = computed(() =>
  preflightAnswered.value
    ? missingBaseFiles.value.length > 0
    : Boolean(card.value && checkpointMissing(card.value)),
);

/** The file that is missing, as recorded (folders included), or "". */
const missingCheckpointFile = computed(
  () =>
    missingBaseFiles.value.find((file) => file !== FORGOTTEN_MODEL) ||
    graphBaseModel.value ||
    checkpointLabel.value,
);

/** The owner's replacement for this card's base model, or null. */
const checkpointFix = computed(
  () => (detail.value?.model_fixes ?? []).find((fix) => fix.base_model) ?? null,
);

/**
 * Whether the pre-flight says the replacement itself is missing, rather than
 * some other base model of the graph.
 */
const replacementMissing = computed(
  () =>
    Boolean(checkpointFix.value) &&
    missingBaseFiles.value.some(
      (file) =>
        fileName(file).toLowerCase() ===
        fileName(checkpointFix.value.now).toLowerCase(),
    ),
);

/** The shelf models a missing base model can be replaced with. */
const replaceCandidates = ref([]);

const replaceOptions = computed(() =>
  replaceCandidates.value.length
    ? [
        { value: "", label: "Replace with…" },
        ...replaceCandidates.value.map((model) => ({
          value: model.filename,
          label: model.display_name || model.filename,
        })),
      ]
    : [],
);

/**
 * Replace the missing base model with `now`, or undo the replacement (`null`).
 *
 * The card keeps its key unless a re-key moved it, so the answer's key is
 * followed; the grid is re-read because pictures already made with the
 * replacement join this card. The pre-flight is asked again: it is what says
 * whether the base model now loads.
 */
function replaceCheckpoint(now) {
  const key = selectedKey.value;
  const was = now === null ? checkpointFix.value?.was : missingCheckpointFile.value;
  if (!key || !was || now === "") return;
  const unmoved = selectionMark();
  return queueWrite("model-fix", async () => {
    try {
      const body = await setWorkflowModelFix(key, { was, now });
      store.forgetMembers();
      await store.fetchCards();
      if (!unmoved()) return;
      const moved = body?.card?.key;
      if (moved && moved !== key) {
        store.select(moved);
        return;
      }
      detail.value = body;
      void checkInstalled(key);
    } catch (err) {
      fail(
        err,
        now === null
          ? "Could not undo that replacement."
          : "Could not replace that model.",
      );
    }
  });
}

/** A recorded model value as a person looks for it: the file, no folders. */
function fileName(value) {
  return String(value).split(/[\\/]/).pop();
}

const vaeLabel = computed(() => {
  const found = (card.value?.models ?? []).find(
    (model) => model.kind === "vae",
  );
  const name = modelDisplayName(found);
  return name ? withQuant(name, found) : "From the checkpoint";
});

const loraSlots = computed(() =>
  (card.value?.loras ?? []).map((lora, index) => ({
    id: lora.slot_label || `lora-${index}`,
    label: lora.slot_label,
    name: lora.name,
    // The chip shows this and its tooltip shows `name`, so the precision goes
    // on the chip: the tooltip is the raw-ish string a reader copies.
    chipText: lora.name ? withQuant(lora.name, lora) : null,
    mark: lora.mark,
  })),
);

// ── The LoRA chain (#1478) ─────────────────────────────────────────────────

/** `GET …/lora-chain` for the selected card, and the state of that read. */
const chain = ref(null);
const chainPending = ref(false);
const chainFailed = ref(false);
/** 409: the card has no graph, so there is no chain to read or edit. */
const chainNoGraph = ref(false);

/** The card Edit LoRAs… is open on, or "" when it is shut. */
const editKey = ref("");
const editName = ref("");
const editPictures = ref(0);
/** A LoRA to open with its loader already deleted (Save-as-recipe's hand-over). */
const editDrop = ref("");

/**
 * The loaders as the inspector lists them: the shelf's name, and a strength.
 * A forked chain's lanes are listed after its trunk, so every loader shows.
 */
const chainLoaders = computed(() =>
  [
    ...(chain.value?.loaders ?? []),
    ...(chain.value?.lanes ?? []).flatMap((lane) => lane.loaders ?? []),
  ].map((loader) => {
    const strength = Number(loader.strength);
    return {
      node_id: String(loader.node_id),
      label: loader.on_shelf
        ? loader.name || loraStem(loader.filename)
        : String(loader.filename || loader.name || "").split(/[\\/]/).pop(),
      on_shelf: Boolean(loader.on_shelf),
      strengthText:
        loader.strength === null || loader.strength === undefined
          ? "—"
          : Number.isFinite(strength)
            ? strength.toFixed(2)
            : "—",
    };
  }),
);

/** "3 of 4 are on your model shelf." */
const shelfLine = computed(() => {
  const total = chainLoaders.value.length;
  const known = chainLoaders.value.filter((loader) => loader.on_shelf).length;
  return `${known} of ${total} ${total === 1 ? "is" : "are"} on your model shelf.`;
});

/**
 * Read the selected card's chain.
 *
 * Its own read rather than a field on the card: the chain is typed from the
 * owner's ComfyUI (`object_info`), which the grid must not wait on. A 409 is a
 * card with no graph, said as such; anything else is "could not read it just
 * now", and Edit LoRAs… stays offered because the dialog reads again.
 */
async function loadChain(key) {
  chain.value = null;
  chainFailed.value = false;
  chainNoGraph.value = false;
  if (!key) {
    chainPending.value = false;
    return;
  }
  chainPending.value = true;
  try {
    const body = await getLoraChain(key);
    if (selectedKey.value !== key) return;
    chain.value = body;
  } catch (err) {
    if (selectedKey.value !== key) return;
    if (err?.response?.status === 409) {
      chainNoGraph.value = true;
    } else {
      console.warn(`[workflows] could not read the LoRA chain of ${key}`, err);
      chainFailed.value = true;
    }
  } finally {
    if (selectedKey.value === key) chainPending.value = false;
  }
}

/** Open Edit LoRAs… on `key`, with `drop` already struck through if given. */
function openEditLoras(key, drop = "") {
  if (!key) return;
  const shown = card.value?.key === key ? card.value : null;
  editName.value = shown?.name || "";
  editPictures.value = Number(shown?.picture_count) || 0;
  editDrop.value = drop;
  editKey.value = key;
}

function closeEditLoras() {
  editKey.value = "";
  editDrop.value = "";
}

// The name and count arrive with the card when the dialog was opened from a
// link before the grid or the detail read had landed.
watch(card, (next) => {
  if (!editKey.value || next?.key !== editKey.value) return;
  if (!editName.value) editName.value = next.name || "";
  if (!editPictures.value) editPictures.value = Number(next.picture_count) || 0;
});

// `?card=<key>&edit=loras&drop_lora=<file>`, from Save-as-recipe's "The
// workflow" (#1478): select that card, open the rail on its Workflow tab, and
// open Edit LoRAs… with that entry already deleted.
//
// By KEY rather than `?topology=`, because a topology can hold several cards
// and the hand-over names exactly one. `edit` and `drop_lora` are one-shot:
// they are taken back off the URL once honoured, so a reload or a Back does
// not reopen a dialog the owner has since cancelled. `card` stays, as
// `topology` does, and is honoured once per value.
let honouredCard = null;

watch(
  () => [route.query?.card, route.query?.edit, route.query?.drop_lora],
  ([wanted, edit, drop]) => {
    if (typeof wanted !== "string" || !wanted) {
      honouredCard = null;
      return;
    }
    if (honouredCard !== wanted || edit) {
      honouredCard = wanted;
      store.select(wanted);
      tab.value = "workflow";
      sidebarStore.openWorkflowInspector();
    }
    if (edit === EDIT_LORAS) {
      openEditLoras(wanted, typeof drop === "string" ? drop : "");
    }
    if (edit !== undefined || drop !== undefined) {
      const rest = Object.fromEntries(
        Object.entries(route.query || {}).filter(
          ([name]) => name !== "edit" && name !== "drop_lora",
        ),
      );
      void router?.replace?.({ query: rest });
    }
  },
  { immediate: true },
);

/** Every slot the card names, for the ComfyUI-users disclosure. */
const nodeNames = computed(() =>
  [...(card.value?.models ?? []), ...(card.value?.loras ?? [])].map(
    (slot, index) => ({
      id: slot.slot_label || `slot-${index}`,
      kind: slot.kind,
      label: slot.slot_label,
    }),
  ),
);

/**
 * The card's defaults, each with whether it is pinned.
 *
 * The detail read carries the pins; a card with none (`null`, not `[]`) gets
 * the default set above, which is what the server means by "the default pins
 * apply again".
 */
const defaults = computed(() => {
  const rows = detail.value?.card?.defaults ?? [];
  const stored = detail.value?.pins;
  const pinned = Array.isArray(stored)
    ? new Set(stored.map((pin) => `${pin.slot_label}\u0000${pin.input_name}`))
    : null;
  return rows.map((row) => ({
    ...row,
    pinned: pinned
      ? pinned.has(`${row.slot_label}\u0000${row.input_name}`)
      : DEFAULT_PINS.includes(row.input_name),
  }));
});

/**
 * A workflow file with no recipe: an editor-format file ComfyUI has not
 * converted yet (#1530). An API file always files a recipe, and a converted
 * editor file files its converted graph, so `variant_count: 0` on an imported
 * card is exactly the editor file PixlStash cannot run or parameterise.
 */
const editorOnly = computed(
  () => Boolean(card.value?.imported) && card.value?.variant_count === 0,
);

const pinnedDefaults = computed(() =>
  defaults.value.filter((row) => row.pinned),
);
const unpinnedDefaults = computed(() =>
  defaults.value.filter((row) => !row.pinned),
);

/** Read one card's detail, and say which of the three states it is in. */
async function loadDetail(key) {
  detail.value = null;
  detailFailed.value = false;
  if (!key) {
    detailPending.value = false;
    return;
  }
  detailPending.value = true;
  try {
    const body = await getWorkflowCard(key);
    // Another card may have been selected while this was on the wire.
    if (selectedKey.value !== key) return;
    detail.value = body;
    notesDraft.value = body.notes ?? "";
  } catch (err) {
    console.warn(`[workflows] could not read the card ${key}`, err);
    if (selectedKey.value !== key) return;
    detailFailed.value = true;
  } finally {
    if (selectedKey.value === key) detailPending.value = false;
  }
}

function fail(err, fallback) {
  console.warn("[workflows] a workflow write failed", err);
  notices.push({ level: "error", text: errorMessage(err, fallback) });
}

/**
 * Run a write after whichever is already out, never beside it.
 *
 * `defaults`, `pins`, the notes and the card all come back on one `detail`,
 * so two writes in flight means the slower answer discards the faster one's
 * change. **Queued rather than refused**, because the gesture that starts
 * the second write is routinely the one that ends the first: clicking a pin
 * is what blurs the notes box, so a refusal would drop that click and leave
 * the pin looking dead.
 *
 * The chain is never broken by a failure — each link catches its own — and
 * `busy` names only the link that is running, so the control that fired it
 * is the one that shows it.
 */
let writes = Promise.resolve();

function queueWrite(token, work) {
  writes = writes.then(async () => {
    busy.value = token;
    try {
      await work();
    } finally {
      busy.value = "";
    }
  });
  return writes;
}

/**
 * Whether a write that has come back still belongs on screen.
 *
 * Every handler awaits, and the selection can move while it does — clicking
 * another card is exactly what blurs the notes box and fires its save. An
 * answer for the card that has gone must not be written into the rail
 * showing the one that arrived.
 */
function stillOn(key) {
  return selectedKey.value === key;
}

/**
 * A mark over the selection itself, for the writes that re-read the grid.
 *
 * `stillOn` is not enough there: a stack member's `selectedKey` is derived
 * from the grid, so a flip or a Hide that takes the member out of its stack
 * empties it even though the reader never moved. By identity, of the
 * selection and of the stack pick: every gesture that selects, or picks
 * another member, writes a new one, so a reader who moved on meanwhile —
 * even only to another member of the same stack — is told apart.
 */
function selectionMark() {
  const keys = toRaw(store.selectedKeys);
  const pick = store.stackPick;
  return () => toRaw(store.selectedKeys) === keys && store.stackPick === pick;
}

/**
 * Flip a LoRA slot between the workflow and the look.
 *
 * **This re-keys the card**, and can split it into several or merge it into
 * another, so the answer's `key` is followed rather than the key that was
 * sent: staying on the old one leaves the rail reading a card that no longer
 * exists. The grid is re-read for the same reason.
 */
function flipMark(slot, mark) {
  const key = selectedKey.value;
  if (!key || !slot.label || slot.mark === mark) return;
  const unmoved = selectionMark();
  return queueWrite(`slot:${slot.label}`, async () => {
    try {
      const moved = await setWorkflowSlots(key, { [slot.label]: mark });
      // Every card of the topology may have been re-keyed, so the cached
      // stack members are about workflows the hub no longer has.
      store.forgetMembers();
      await store.fetchCards();
      if (!unmoved()) return;
      // `select` moves `selectedKey`, which the watcher below turns into the
      // detail read. Calling `loadDetail` here as well fetched the same card
      // twice; when the flip did not move it the watcher does not fire, so
      // that case reads explicitly. A stack member the flip took out of its
      // stack is selected on its own, the card the reader was looking at.
      if (selectedKey.value === moved.key) await loadDetail(moved.key);
      else store.select(moved.key);
    } catch (err) {
      fail(err, "Could not change that LoRA slot.");
    }
  });
}

/**
 * The defaults a whole-set write for `key` may be built from, or null.
 *
 * Read when the queued write RUNS, not when it was queued, so an earlier write
 * it waited behind is in them. By then the rail may have moved on: `defaults`
 * is then another card's (or nothing, mid-read), and a whole-set PUT built
 * from them would replace `key`'s own overrides. Refused and said instead.
 */
function defaultsFor(key) {
  if (stillOn(key) && detail.value) return defaults.value;
  console.warn(`[workflows] a write to ${key} dropped: the selection moved`);
  // Not "you selected another": a LoRA flip re-keys the card and moves the
  // selection itself.
  notices.push({
    level: "error",
    text: "That change was not saved: the workflow changed before it could be.",
  });
  return null;
}

/**
 * The edited defaults except one address, as a whole-set write sends them.
 *
 * Every whole-set writer starts here: the route replaces the set, so what is
 * not sent is cleared.
 */
function editedExcept(rows, slotLabel, inputName) {
  return rows
    .filter(
      (entry) =>
        entry.provenance === "edited" &&
        !(entry.slot_label === slotLabel && entry.input_name === inputName),
    )
    .map((entry) => ({
      slot_label: entry.slot_label,
      input_name: entry.input_name,
      value: entry.value,
    }));
}

/**
 * Put a value back to what the pictures say.
 *
 * The route replaces the whole override set, so this sends every other edited
 * value back untouched; sending only the survivors of a filter is the same
 * request and is what makes "reset one" possible at all.
 */
function resetDefault(row) {
  const key = selectedKey.value;
  if (!key) return;
  return queueWrite(`default:${row.label}`, async () => {
    const rows = defaultsFor(key);
    if (!rows) return;
    try {
      const kept = editedExcept(rows, row.slot_label, row.input_name);
      const body = await setWorkflowDefaults(key, kept);
      if (stillOn(key)) detail.value = body;
    } catch (err) {
      fail(err, "Could not reset that value.");
    }
  });
}

/** Pin or unpin one parameter. Whole-set, like the defaults. */
function togglePin(row) {
  const key = selectedKey.value;
  if (!key) return;
  return queueWrite(`default:${row.label}`, async () => {
    const rows = defaultsFor(key);
    if (!rows) return;
    try {
      const pins = rows
        .filter((entry) =>
          entry.slot_label === row.slot_label &&
          entry.input_name === row.input_name
            ? !entry.pinned
            : entry.pinned,
        )
        .map((entry) => ({
          slot_label: entry.slot_label,
          input_name: entry.input_name,
        }));
      const body = await setWorkflowPins(key, pins);
      if (stillOn(key)) {
        detail.value = { ...detail.value, pins: body.pins ?? pins };
      }
    } catch (err) {
      fail(err, "Could not change that pin.");
    }
  });
}

/**
 * Save the notes, on blur.
 *
 * Queued like every other write, and it has to be: this PATCH answers with
 * the whole detail, pins included, so landing beside a pin write puts the
 * pre-toggle pins back on screen while the server holds the new ones. Blur
 * is exactly when that happens — clicking the pin is what blurs this box.
 *
 * The draft is compared at QUEUE time, not at run time: the comparison is
 * "did the person change anything", and by the time an earlier write has
 * finished `detail` may already carry what they typed.
 */
function saveNotes() {
  const key = selectedKey.value;
  // `detail` null is a card whose notes have not arrived; the box is not
  // drawn then, and the draft belongs to whatever was read last.
  if (
    !key ||
    !detail.value ||
    notesDraft.value === (detail.value.notes ?? "")
  ) {
    return;
  }
  const notes = notesDraft.value || null;
  return queueWrite("notes", async () => {
    try {
      const body = await patchWorkflowCard(key, { notes });
      if (stillOn(key)) detail.value = body;
    } catch (err) {
      fail(err, "Could not save those notes.");
    }
  });
}

function toggleHidden() {
  const key = selectedKey.value;
  if (!key || !detail.value) return;
  menuOpen.value = false;
  const hiding = !detail.value.hidden;
  const unmoved = selectionMark();
  return queueWrite("hidden", async () => {
    try {
      const body = await patchWorkflowCard(key, { hidden: hiding });
      if (stillOn(key)) detail.value = body;
      // A hidden card leaves the grid, so the rail would have nothing to
      // draw were it not for the detail fallback in `card` — which is also
      // what keeps Unhide reachable from here.
      store.forgetMembers();
      await store.fetchCards();
      // A stack member hidden from the rail leaves its stack, and the stack
      // stops being selected whole: stay on the card that was hidden.
      if (unmoved() && selectedKey.value !== key) store.select(key);
    } catch (err) {
      fail(
        err,
        hiding ? "Could not hide that workflow." : "Could not unhide it.",
      );
    }
  });
}

/**
 * What Run… runs: the card on screen, which for a stack selected whole is the
 * member picked at the top. Nothing while several cards are selected.
 */
const runTarget = computed(() => (multiple.value ? null : card.value));

/**
 * Why Run… refuses, when it is a reason worth a sentence: a stack member
 * whose read is still out refuses too, but only for as long as the read.
 */
const runDescribedBy = computed(() =>
  multiple.value ? "wftab-run-reason" : undefined,
);

/** Why Open in ComfyUI refuses, when it does. */
const openDescribedBy = computed(() => {
  if (multiple.value) return "wftab-open-reason";
  return comfyuiLacksNode.value ? "wftab-open-node-reason" : undefined;
});

/**
 * Run… opens the Run popup on THIS card (v1.12 F5).
 *
 * No picture behind it, so the popup shows the card's cover, an empty prompt
 * and a set picker for where the output is filed - which is the one thing a
 * card-sourced run has to be told and a picture-sourced one already knows.
 *
 * Refused outright while several cards are selected: the button stays on
 * screen and `aria-disabled` says why, rather than disappearing and leaving
 * nothing to explain. A stack selected whole is one card (`runTarget`).
 */
function run() {
  const target = runTarget.value;
  if (!target) return;
  runDialog.openRun({
    kind: "card",
    workflowKey: target.key,
    name: target.name,
    // Through the helper: a raw `covers` entry is API-relative and an
    // `<img src>` resolves it against the page origin instead.
    coverUrl: target.covers?.[0] ? workflowCoverUrl(target.covers[0]) : "",
    emptyPrompt: true,
  });
}

/**
 * Open ComfyUI on what Run… runs (`runTarget`), in a new tab.
 *
 * ComfyUI takes no workflow from a URL, so the key rides along as
 * `?pixlstash_workflow=` and the ComfyUI-PixlStash node fetches the graph
 * (`GET /workflows/{key}/graph`) and loads it. Synchronous on purpose: a
 * `window.open` after an await is what popup blockers refuse.
 */
function openInComfyui() {
  const target = runTarget.value;
  if (!target || !filterStore.comfyuiUrl || comfyuiLacksNode.value) return;
  let url;
  try {
    url = new URL(filterStore.comfyuiUrl);
  } catch (err) {
    console.warn(`[workflows] bad ComfyUI address ${filterStore.comfyuiUrl}`, err);
    url = null;
  }
  if (!url || !/^https?:$/.test(url.protocol)) {
    notices.push({
      level: "error",
      text: "The ComfyUI address in Settings is not a web address.",
    });
    return;
  }
  // `0.0.0.0` is ComfyUI listening everywhere: fine for the server, and a
  // browser cannot navigate to it. The machine this page came from is the one.
  if (["0.0.0.0", "[::]"].includes(url.hostname)) {
    url.hostname = window.location.hostname;
  }
  url.searchParams.set("pixlstash_workflow", target.key);
  if (desktop?.openComfyui) {
    desktop
      .openComfyui(url.toString())
      .then((opened) => {
        if (!opened) throw new Error("the shell refused the link");
      })
      .catch((err) => {
        console.warn("[workflows] the desktop shell would not open ComfyUI", err);
        notices.push({ level: "error", text: "Could not open ComfyUI." });
      });
    return;
  }
  window.open(url.toString(), "_blank", "noopener,noreferrer");
}

watch(
  selectedKey,
  (key) => {
    void loadDetail(key);
    void loadChain(key);
  },
  { immediate: true },
);

/**
 * Ask the run pre-flight whether the base model loads, once per card./**
 * Ask the run pre-flight whether the base model loads, once per card.
 *
 * Keyed on the card alone: every write answers with a new `detail` too, and a
 * pin toggle is no reason to ask ComfyUI again. Each ask is a fresh
 * `object_info` read, so it waits for the selection to settle - arrowing across
 * the grid asks once, not per card - and a superseded answer is dropped. A
 * failed or unanswerable check leaves the card's own answer standing.
 */
watch(
  () => (detail.value ? selectedKey.value : null),
  (key) => checkInstalled(key),
  { immediate: true },
);

async function checkInstalled(key) {
  const check = ++installedCheck;
  missingBaseFiles.value = [];
  preflightAnswered.value = false;
  replaceCandidates.value = [];
  if (!key) return;
  await new Promise((resolve) => setTimeout(resolve, PREFLIGHT_SETTLE_MS));
  if (check !== installedCheck) return;
  try {
    const answer = await preflightWorkflowRun({
      workflow_key: key,
      values: [],
    });
    if (check !== installedCheck || !stillOn(key)) return;
    preflightAnswered.value = true;
    missingBaseFiles.value = (answer?.groups ?? [])
      .flatMap((group) => group.reasons ?? [])
      .filter((reason) => reason.code === "missing_models")
      .flatMap((reason) => reason.models ?? [])
      .filter((model) => BASE_MODEL_FOLDERS.has(model.folder))
      .map((model) => String(model.file));
  } catch (err) {
    console.warn(`[workflows] could not pre-flight ${key}`, err);
    return;
  }
  // Only a file ComfyUI named can be replaced: a forgotten name is no file.
  if (!missingBaseFiles.value.some((file) => file !== FORGOTTEN_MODEL)) return;
  try {
    const swap = await readModelSwap(key);
    if (check !== installedCheck || !stillOn(key)) return;
    replaceCandidates.value = swap?.checkpoints ?? [];
  } catch (err) {
    console.warn(`[workflows] could not read replacements for ${key}`, err);
  }
}
</script>

<style scoped>
.wftab-empty,
.wftab-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

.wftab-empty {
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
}

.wftab-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wftab-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}

.wftab-sub {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
/* Underlined, not just coloured: it sits inside a line of secondary text, and
   a hue change alone would not say which words are the control. */
.wftab-pictures {
  /* `padding: 0; font: inherit` is the shipped inline-button reset
     (`EmptyScrapHeap.vue`): the global one drops the border and background
     but a <button> still inherits neither the UA padding nor its typeface,
     and this one sits mid-sentence in a line the template glues together
     whitespace-free. */
  padding: 0;
  font: inherit;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
}
.wftab-pictures:hover {
  color: rgb(var(--v-theme-primary));
}

.wftab-head {
  gap: var(--space-2);
}

.wftab-pick :deep(.app-select__field) {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.wftab-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

/* The 96px label column is local to this tab: no other pane pairs a label
   with a value field at this width, so it is not a token. */
.wftab-field {
  display: grid;
  grid-template-columns: 96px 1fr;
  align-items: center;
  gap: var(--space-3);
}

.wftab-label {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wftab-label--group {
  margin-top: var(--space-2);
}

.wftab-value {
  display: flex;
  align-items: center;
  min-height: var(--control-h);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wftab-loras-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

/* The chain as read: one line per loader, name then strength, in the order
   it applies. Plain rows, not chips: the chips below are the slot marks, and
   a second set of chips would read as a second set of controls. */
.wftab-chain {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wftab-chain-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-h-sm);
  font-size: var(--text-sm);
}

.wftab-chain-row + .wftab-chain-row {
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.wftab-chain-flag {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

.wftab-chain-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wftab-chain-strength {
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wftab-missing {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

/* A replaced base model: the value field as every other row draws it, with
   an icon-only Undo beside it. */
.wftab-fixed {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.wftab-fixed > .wftab-value {
  flex: 1;
  min-width: 0;
  gap: var(--space-2);
}

.wftab-fixed-flag {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

/* The hue drawn as text, so the surface variant: the fill is 2.1:1 on the
   light canvas. */
.wftab-warn {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-warning));
}

.wftab-slot {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.wftab-slot + .wftab-slot {
  padding-top: var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.wftab-slot-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.wftab-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1;
  min-width: 0;
  height: var(--control-h);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  font-size: var(--text-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wftab-chip--empty {
  border-style: dashed;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wftab-legend {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  margin-left: auto;
  text-transform: none;
  letter-spacing: 0;
  font-weight: var(--weight-regular);
}

.wftab-disclose > summary {
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
  cursor: pointer;
}

.wftab-notes {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: inherit;
  font: inherit;
  font-size: var(--text-sm);
  resize: vertical;
}

.wftab-mono {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  overflow-wrap: anywhere;
}

.wftab-foot {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  /* The body's inline padding, so the buttons line up with the content while
     the hairline spans the whole rail. */
  padding: var(--space-3);
  /* `border`, not `divider`: divider all but vanishes on the sidebar tone. */
  border-top: 1px solid rgb(var(--v-theme-border));
}

.wftab-foot > :first-child {
  flex: 1;
}

.wftab-foot > .wftab-note {
  flex-basis: 100%;
}

.wftab-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-height: var(--control-h-bar);
  padding: 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: inherit;
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
}

.wftab-item:hover {
  background: var(--hover-wash);
}
</style>
