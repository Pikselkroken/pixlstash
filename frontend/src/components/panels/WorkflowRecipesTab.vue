<template>
  <div class="wfrt">
    <div class="inspector-section wfrt-head">
      <p class="wfrt-title">{{ workflowName }}</p>
      <p class="wfrt-sub">{{ subtitle }}</p>
    </div>

    <p v-if="loading" class="wfrt-note wfrt-quiet">Reading your recipes…</p>
    <p
      v-else-if="!loadError && !recipes.length && !looks.length"
      class="wfrt-note wfrt-quiet"
    >
      No recipes here yet. A picture shows up once PixlStash has read how it was
      made, so a fresh import takes a moment to arrive.
    </p>
    <p v-else-if="loadError" class="wfrt-note wfrt-bad" role="alert">
      {{ loadError }}
    </p>

    <!-- Saved first: they are few, in the owner's order, and the reason to
         come back to this tab. The list below them can run to hundreds. -->
    <div
      v-if="!loading && !loadError && recipes.length"
      class="section-label wfrt-section"
    >
      Saved
    </div>

    <!-- One list, one tab stop. The handle is the control: it is what a
         pointer drags and what Alt+↑/↓ moves, so the row itself needs no
         keyboard behaviour of its own.

         `v-if`, not the band's `v-else`: chaining the two made the band and
         the list alternatives, so the moment there was a used half to label,
         the saved list it labelled disappeared. -->
    <ul
      v-if="!loading && !loadError && recipes.length"
      class="wfrt-list"
    >
      <li
        v-for="(recipe, index) in recipes"
        :key="recipe.id"
        class="wfrt-card"
        :class="{ 'wfrt-card--dragging': draggingId === recipe.id }"
        :aria-label="`${recipe.name || 'Untitled'}, ${recipeDiffs[recipe.id].text}`"
        @dragover.prevent
        @drop.prevent="onDrop(index)"
      >
        <div class="wfrt-top">
          <!-- The handle is the drag source, not the card: a draggable card
               makes the prompt unselectable and turns every press on Run… into
               the start of a drag. The keyboard path is on the same control,
               and it is in the accessible NAME rather than a tooltip, because
               a tooltip needs a hover a keyboard user does not have. -->
          <button
            class="wfrt-handle"
            type="button"
            draggable="true"
            :aria-label="`Reorder ${recipe.name || 'this recipe'}: hold Alt and press the up or down arrow, or drag`"
            @dragstart="onDragStart(recipe, $event)"
            @dragend="draggingId = null"
            @keydown.up.alt.prevent="move(index, -1)"
            @keydown.down.alt.prevent="move(index, 1)"
          >
            <Tooltip
              text="Drag to reorder, or Alt with the arrow keys"
              activator="parent"
              :describe="false"
            />
            <v-icon size="16">mdi-drag-vertical</v-icon>
          </button>

          <!-- The picture the recipe was saved from, and only that one: the
               API credits a COUNT of matching pictures rather than their ids,
               so the design's five-tile strip would be four tiles of
               invention. A square at the head of the row rather than a band
               across the card, because 1/1 is a ratio this app already has
               and a one-picture band needed a new one. -->
          <button
            v-if="recipe.source_picture_id"
            class="wfrt-thumb-btn"
            type="button"
            :aria-label="`Open the picture ${recipe.name || 'this recipe'} was saved from`"
            @click="openPicture(recipe.source_picture_id)"
          >
            <Tooltip text="Open the picture" activator="parent" :describe="false" />
            <img
              class="wfrt-thumb"
              :src="thumbnail(recipe.source_picture_id)"
              alt=""
              loading="lazy"
              decoding="async"
            />
          </button>

          <!-- Escape backs out, as it does in every dialog here; Enter and
               blur both commit, which is what an inline field in a list has
               to do to survive a click somewhere else. -->
          <AppInput
            v-if="renamingId === recipe.id"
            v-model="renameDraft"
            aria-label="Name for this recipe"
            autofocus
            @enter="commitRename(recipe)"
            @blur="commitRename(recipe)"
            @keydown.esc.stop.prevent="cancelRename"
          />
          <span v-else class="wfrt-name">{{ recipe.name || "Untitled" }}</span>

          <v-menu
            v-if="renamingId !== recipe.id"
            :model-value="menuId === recipe.id"
            location="bottom end"
            origin="top end"
            :offset="4"
            @update:model-value="(v) => (menuId = v ? recipe.id : null)"
          >
            <template #activator="{ props: menuProps }">
              <!-- `withRef`, not a second `ref` beside `v-bind`: Vue's
                   mergeProps keeps whichever comes last, so the menu would
                   open unanchored or this ref would stay null. -->
              <AppButton
                v-bind="withRef(menuProps, (el) => registerMenuButton(recipe.id, el))"
                size="sm"
                icon-left="dots-horizontal"
                icon-only
                :tooltip="`More for ${recipe.name || 'this recipe'}`"
                aria-haspopup="menu"
              />
            </template>
            <div
              class="ctx-menu"
              role="menu"
              tabindex="-1"
              @keydown="onMenuKeydown"
            >
              <button
                class="ctx-item"
                type="button"
                role="menuitem"
                @click="startRename(recipe)"
              >
                <v-icon size="16">mdi-pencil</v-icon>
                Rename
              </button>
              <button
                class="ctx-item"
                type="button"
                role="menuitem"
                @click="startExport(recipe)"
              >
                <v-icon size="16">mdi-export</v-icon>
                Export…
              </button>
              <button
                class="ctx-item ctx-item--danger"
                type="button"
                role="menuitem"
                @click="removeRecipe(recipe)"
              >
                <v-icon size="16">mdi-delete</v-icon>
                Delete
              </button>
            </div>
          </v-menu>
        </div>

        <!-- What the recipe changes against its workflow's default recipe
             (#1653 §1.3), then the prompt. The diff comes first: the prompt
             differs on every card, the diff is what tells them apart. -->
        <div class="wfrt-body">
          <p
            :ref="(el) => registerDiff(`r:${recipe.id}`, el)"
            class="wfrt-diff"
            :class="{ 'wfrt-diff--open': expanded[`r:${recipe.id}`] }"
          >
            <span
              v-for="(seg, i) in recipeDiffs[recipe.id].segments"
              :key="i"
              class="wfrt-seg"
            ><span v-if="i" class="wfrt-quiet"> · </span><span
                v-for="(part, j) in seg.parts"
                :key="j"
                :class="{ 'wfrt-quiet': part.quiet }"
              >{{ part.text }}</span></span>
          </p>
          <button
            v-if="hidden[`r:${recipe.id}`] || expanded[`r:${recipe.id}`]"
            class="wfrt-more"
            type="button"
            :aria-expanded="expanded[`r:${recipe.id}`] ? 'true' : 'false'"
            @click="toggleMore(`r:${recipe.id}`)"
          >
            {{ expanded[`r:${recipe.id}`] ? "Fewer" : `+${hidden[`r:${recipe.id}`]} more` }}
          </button>
          <p v-if="recipe.prompt" class="wfrt-prompt">{{ recipe.prompt }}</p>
        </div>

        <div class="wfrt-bot">
          <!-- On the card, not in its ⋯ menu, where nobody found it. Only
               when a parameter differs (the defaults PUT takes parameters;
               LoRAs go in from the Workflow tab's pile), and a verb that
               belongs to one workflow, so not on a multi-selection. -->
          <AppButton
            v-if="!multi && recipeDiffs[recipe.id].params.length"
            :ref="(el) => registerDefaultsButton(recipe.id, el)"
            size="sm"
            variant="ghost"
            data-testid="wfrt-make-defaults"
            :tooltip="`Make ${recipe.name || 'this recipe'}'s settings the workflow's defaults`"
            @click="startMakeDefaults(recipe)"
          >
            Make defaults…
          </AppButton>
          <AppButton size="sm" icon-left="play" @click="run(recipe)">
            Run…
          </AppButton>
        </div>
      </li>
    </ul>

    <!-- The looks the pictures themselves carry: every one they were made
         with, filled in without anybody pressing anything. One cloned into
         Saved stays here too, marked, because a clone is a copy and does not
         take the original out of the list it was found in. -->
    <template v-if="!loading && !loadError && looks.length">
      <div class="section-label wfrt-section">From your pictures</div>
      <ul class="wfrt-list">
        <li v-for="look in looks" :key="look.key" class="wfrt-card">
          <div class="wfrt-top">
            <button
              v-if="look.cover_picture_id"
              class="wfrt-thumb-btn"
              type="button"
              aria-label="Open the newest picture made with this recipe"
              @click="openPicture(look.cover_picture_id)"
            >
              <Tooltip text="Open the picture" activator="parent" :describe="false" />
              <img
                class="wfrt-thumb"
                :src="thumbnail(look.cover_picture_id)"
                alt=""
                loading="lazy"
                decoding="async"
              />
            </button>
            <span v-if="look.saved" class="wfrt-marked">
              <v-icon size="16">mdi-check</v-icon>
              Saved
            </span>
          </div>

          <!-- `/recipes/used` names LoRA files only, so a look's diff is its
               LoRA part and its count; never "Only the prompt" (§1.3). A
               look carries no workflow id, so a multi-selection has no one
               default to compare with and keeps the plain LoRA list. -->
          <div v-if="lookDiffs[look.key]" class="wfrt-body">
            <p
              :ref="(el) => registerDiff(`l:${look.key}`, el)"
              class="wfrt-diff"
              :class="{ 'wfrt-diff--open': expanded[`l:${look.key}`] }"
            >
              <span
                v-for="(seg, i) in lookDiffs[look.key].segments"
                :key="i"
                class="wfrt-seg"
              ><span v-if="i" class="wfrt-quiet"> · </span><span
                  v-for="(part, j) in seg.parts"
                  :key="j"
                  :class="{ 'wfrt-quiet': part.quiet }"
                >{{ part.text }}</span></span>
            </p>
            <button
              v-if="hidden[`l:${look.key}`] || expanded[`l:${look.key}`]"
              class="wfrt-more"
              type="button"
              :aria-expanded="expanded[`l:${look.key}`] ? 'true' : 'false'"
              @click="toggleMore(`l:${look.key}`)"
            >
              {{ expanded[`l:${look.key}`] ? "Fewer" : `+${hidden[`l:${look.key}`]} more` }}
            </button>
            <p v-if="look.prompt" class="wfrt-prompt">{{ look.prompt }}</p>
          </div>

          <template v-else>
            <p v-if="look.prompt" class="wfrt-prompt">{{ look.prompt }}</p>
            <div v-if="look.loras?.length" class="wfrt-chips">
              <span
                v-for="(lora, slot) in look.loras"
                :key="slot"
                class="wfrt-chip"
              >
                <v-icon size="12">mdi-layers-outline</v-icon>
                {{ lora.filename }}
              </span>
            </div>
          </template>

          <div class="wfrt-bot">
            <span class="wfrt-facts">{{
              lookDiffs[look.key] ? "" : picturesLabel(look.pictures)
            }}</span>
            <AppButton
              v-if="!look.saved"
              size="sm"
              icon-left="content-copy"
              :loading="savingLook === look.key"
              :disabled="!look.cover_picture_id"
              tooltip="Clone this recipe into Saved, to name it and keep it at the top"
              @click="keepLook(look)"
            >
              Clone…
            </AppButton>
            <AppButton
              v-if="look.cover_picture_id"
              size="sm"
              icon-left="play"
              tooltip="Run this recipe again, from the picture that made it"
              @click="runLook(look)"
            >
              Run…
            </AppButton>
          </div>
        </li>
      </ul>
    </template>


    <!-- One polite region for the whole list: a reorder made with the
         keyboard, and a refused write that slides the row back, are both
         changes a reader watching the screen can see and a reader who is not
         cannot. -->
    <p class="wfrt-live" role="status" aria-live="polite">{{ liveMessage }}</p>

    <SaveRecipeDialog
      v-if="savingSource"
      :open="Boolean(savingSource)"
      :workflow-id="savingSource.workflowId"
      :prompt="savingSource.prompt"
      :negative="savingSource.negative"
      :loras="savingSource.loras"
      :seed="savingSource.seed"
      :source-picture-id="savingSource.sourcePictureId"
      @close="savingSource = null"
    />

    <ExportRecipeDialog
      v-if="exportId !== null"
      :open="exportId !== null"
      :recipe-id="exportId"
      @close="closeExport"
    />

    <MakeDefaultsDialog
      v-if="makingDefaults"
      :open="Boolean(makingDefaults)"
      :rows="makingDefaults.rows"
      :busy="makingDefaults.busy"
      :error="makingDefaults.error"
      @close="closeMakeDefaults"
      @confirm="confirmMakeDefaults"
    />
  </div>
</template>

<script setup>
/**
 * The Recipes tab of the Workflows grid's inspector (v1.12 F6).
 *
 * One card per saved recipe of the selected workflows (`GET /recipes?
 * workflow_id=`), and beside them every look their pictures were made with.
 *
 * The order is the owner's and is written back whole on every move, because
 * `PUT /recipes/order` refuses a list with an unknown id rather than leaving
 * half an order behind.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import { getPictureRecipe } from "../../api/comfyui";
import { listAdapters } from "../../api/modelShelf";
import {
  deleteSavedRecipe,
  editSavedRecipe,
  listSavedRecipes,
  listUsedLooks,
  reorderSavedRecipes,
} from "../../api/recipes";
import { pictureThumbnailUrl } from "../../api/pictures";
import { getWorkflowCard, setWorkflowDefaults } from "../../api/workflows";
import { useConfirm } from "../../composables/useConfirm";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import { lookLoraDiff, recipeDiff } from "../../utils/recipeDiff";
import { loraKey, promptKey } from "../../utils/recipeKey";
import { resolveRecipeLoras } from "../../utils/recipeLoras";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import { withRef } from "../../utils/withRef";
import ExportRecipeDialog from "../io/ExportRecipeDialog.vue";
import MakeDefaultsDialog from "../io/MakeDefaultsDialog.vue";
import SaveRecipeDialog from "../io/SaveRecipeDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /**
   * The selected workflows' ids (#1623). A selection of several is listed as
   * one union, each recipe once.
   */
  workflowIds: { type: Array, default: () => [] },
  /** What the header names. */
  workflowName: { type: String, default: "" },
});

/**
 * `defaults-changed(workflowId, detail)`: this tab wrote a workflow's defaults.
 * The Workflow tab keeps its own copy of the detail and builds its whole-set
 * PUTs from it, so it has to take this answer or its next write drops ours.
 */
const emit = defineEmits(["defaults-changed"]);

const { confirm } = useConfirm();
const notices = useNoticeStore();
const runDialog = useRunDialogStore();
const workflows = useWorkflowsStore();

const recipes = ref([]);
const loading = ref(false);
const loadError = ref("");
const draggingId = ref(null);
const menuId = ref(null);
const renamingId = ref(null);
const renameDraft = ref("");
const exportId = ref(null);
/** Every recipe the workflows' own pictures carry; `saved` is the server's. */
const looks = ref([]);
/** The recipe whose cover picture is being read, so its Clone… can show it. */
const savingLook = ref("");
/** What the Save dialog is filled from, or null. */
const savingSource = ref(null);

/** The one sentence the live region is saying, or "". */
const liveMessage = ref("");

/** Say something to a reader who is not watching the list. */
function say(message) {
  liveMessage.value = message;
}

/** Bumped per read, so a slower earlier one cannot write over a later one. */
let loadToken = 0;

/**
 * What one read may ask about, matching `MAX_UNION_KEYS` in `routes/recipes.py`.
 *
 * Spelled here as well because the refusal has to be a sentence rather than a
 * 422: the server's cap is the contract, this is the screen keeping to it.
 */
const MAX_UNION_KEYS = 100;

const subtitle = computed(() => {
  const found = looks.value.length;
  const kept = recipes.value.length;
  // One count per section, in the sections' order, so each reads as the size
  // of the list under its own label: a saved recipe need not have one below it
  // (saved from the Run popup, never run), and two may mark one. Nothing
  // is counted before a read has answered, because 0 would be a claim.
  const parts = [];
  if (!loading.value && !loadError.value) {
    if (kept) parts.push(`${kept} saved`);
    if (found || !kept) {
      parts.push(`${found} recipe${found === 1 ? "" : "s"} from your pictures`);
    }
  }
  return parts.join(" · ");
});

/** "31 pictures", the way the saved half writes it. */
function picturesLabel(count) {
  return `${count} ${count === 1 ? "picture" : "pictures"}`;
}

function thumbnail(pictureId) {
  return pictureThumbnailUrl(pictureId);
}

// ── The diff line (#1653 §1.3) ──────────────────────────────────────────────

/** Several workflows selected: each card is diffed against its own. */
const multi = computed(() => props.workflowIds.filter(Boolean).length > 1);

/**
 * Workflow details by id, `{state: "loading" | "ready" | "failed", body}`:
 * the default recipe a card is compared with is `body.card.default_recipe`.
 */
const details = ref({});

/** Read one detail per distinct workflow, without holding the cards back. */
async function readDetails(token, ids) {
  details.value = Object.fromEntries(ids.map((id) => [id, { state: "loading" }]));
  await Promise.all(
    ids.map(async (id) => {
      try {
        const body = await getWorkflowCard(id);
        if (token === loadToken) details.value[id] = { state: "ready", body };
      } catch (err) {
        console.warn(`Could not read workflow ${id} to compare its recipes:`, err);
        if (token === loadToken) details.value[id] = { state: "failed" };
      }
    }),
  );
}

/** A segment in the quiet ink: a state, a count, the workflow's name. */
function quiet(text) {
  return { parts: [{ text, quiet: true }], text };
}

function finish(segments, params = []) {
  return { segments, params, text: segments.map((seg) => seg.text).join(" · ") };
}

/** The default a workflow's detail holds, or a quiet segment saying why not. */
function defaultOf(workflowId) {
  const entry = details.value[workflowId];
  if (!entry || entry.state === "loading") return { wait: quiet("Comparing with the default…") };
  const recipe = entry.body?.card?.default_recipe;
  if (entry.state === "failed" || !recipe) {
    return { wait: quiet("Could not compare with the default.") };
  }
  return { recipe, name: entry.body.card.name };
}

const recipeDiffs = computed(() => {
  const out = {};
  for (const recipe of recipes.value) {
    if (!recipe.workflow_id) {
      out[recipe.id] = finish([quiet("Not compared: not filed on a workflow")]);
      continue;
    }
    const base = defaultOf(recipe.workflow_id);
    if (base.wait) {
      out[recipe.id] = finish([base.wait]);
      continue;
    }
    const diff = recipeDiff(recipe, base.recipe);
    const segments = diff.segments.length ? diff.segments : [quiet("Only the prompt")];
    if (multi.value && base.name) segments.push(quiet(`on ${base.name}`));
    out[recipe.id] = finish(segments, diff.params);
  }
  return out;
});

/** Looks by key; absent on a multi-selection, which keeps the LoRA list. */
const lookDiffs = computed(() => {
  const out = {};
  const keys = props.workflowIds.filter(Boolean);
  if (keys.length !== 1) return out;
  const base = defaultOf(keys[0]);
  for (const look of looks.value) {
    const lora = base.wait ? [base.wait] : lookLoraDiff(look, base.recipe);
    if (!lora) continue;
    const segments = lora.length ? lora : [{ parts: [{ text: "Default LoRAs" }], text: "Default LoRAs" }];
    out[look.key] = finish([...segments, quiet(picturesLabel(look.pictures))]);
  }
  return out;
});

/** Diff lines by `r:<id>` / `l:<key>`, for measuring the 2-line clamp. */
const diffEls = new Map();
/** How many segments the clamp hides, by the same key. */
const hidden = ref({});
/** Lines the owner expanded. */
const expanded = ref({});

/**
 * Re-measures when a line's box changes size without its content changing:
 * the rail animates its width open from zero, so a count taken on mount can be
 * of a line still wrapping at a fraction of its width.
 */
const diffObserver = new ResizeObserver(() => measure());
onBeforeUnmount(() => diffObserver.disconnect());

function registerDiff(key, el) {
  const previous = diffEls.get(key);
  if (previous === el) return;
  if (previous) diffObserver.unobserve(previous);
  if (el) {
    diffEls.set(key, el);
    diffObserver.observe(el);
  } else {
    diffEls.delete(key);
  }
}

/**
 * Count the segments the clamp cuts, after each render of the lines. The
 * line is its segments' offset parent, so a segment ending below the line's
 * box is (at least partly) hidden.
 */
function measure() {
  const next = {};
  for (const [key, el] of diffEls) {
    if (expanded.value[key]) {
      next[key] = hidden.value[key] || 0;
      continue;
    }
    const limit = el.clientHeight;
    next[key] = [...el.querySelectorAll(".wfrt-seg")].filter(
      (seg) => seg.offsetTop + seg.offsetHeight > limit + 1,
    ).length;
  }
  hidden.value = next;
}

watch([recipeDiffs, lookDiffs], measure, { flush: "post" });

function toggleMore(key) {
  expanded.value = { ...expanded.value, [key]: !expanded.value[key] };
}

/**
 * The looks as the list keys them.
 *
 * A look has no id, and its prompt and LoRAs are what make it one, so the key
 * both halves match on is also what keys the list.
 */
function keyedLooks(used) {
  return used.map((look) => ({
    ...look,
    key: `${promptKey(look.prompt)}\u0000${loraKey(look.loras)}`,
  }));
}

/**
 * Read the looks again, for their marks, without blanking the tab.
 *
 * **The server is the only source of `saved`.** Its key and the client's
 * mirror differ at the edges (whitespace a LoRA name was written with), so a
 * mark worked out here could disagree with the one the next load shows.
 */
async function refreshLooks(token) {
  try {
    const used = await listUsedLooks(props.workflowIds.filter(Boolean));
    if (token === loadToken) looks.value = keyedLooks(used);
  } catch (err) {
    // The recipe is gone either way; only a mark below may be stale until
    // the tab is next read.
    console.warn("Could not re-read the used recipes after a delete:", err);
  }
}

async function load() {
  const token = (loadToken += 1);
  const mine = () => token === loadToken;
  loadError.value = "";
  const keys = props.workflowIds.filter(Boolean);
  details.value = {};
  if (!keys.length) {
    recipes.value = [];
    looks.value = [];
    return;
  }
  // **Refused here rather than by the server.** One shift-click down a long
  // grid is a single gesture and can name more workflows than the route takes;
  // the read would 422 and the catch below would wipe both halves behind
  // "Could not read your saved recipes", which is neither true nor useful.
  if (keys.length > MAX_UNION_KEYS) {
    recipes.value = [];
    looks.value = [];
    loadError.value = `Too many workflows selected to read their recipes together — pick ${MAX_UNION_KEYS} or fewer.`;
    return;
  }
  loading.value = true;
  try {
    // Both halves together: a look's `saved` flag is about the recipes
    // in the first, so reading them apart could mark a look against a list
    // of saved recipes that is not the one on screen.
    const [saved, used] = await Promise.all([
      listSavedRecipes(keys),
      listUsedLooks(keys),
    ]);
    if (!mine()) return;
    recipes.value = saved;
    looks.value = keyedLooks(used);
    // The defaults the cards are diffed against: the selected workflow's
    // (its looks need it too), and each saved recipe's own.
    const ids = new Set(keys.length === 1 ? keys : []);
    for (const row of saved) if (row.workflow_id) ids.add(row.workflow_id);
    void readDetails(token, [...ids]);
  } catch (err) {
    if (mine()) {
      recipes.value = [];
      looks.value = [];
      loadError.value = errorMessage(err, "Could not read your saved recipes.");
    }
  } finally {
    if (mine()) loading.value = false;
  }
}

// The selection, and a save made anywhere else. The second is most often the
// Run popup opened from this very tab, which leaves the list behind the dialog
// one recipe out of date on a screen the selection never moved off.
watch(
  [
    () => props.workflowIds,
    () => workflows.recipesEpoch,
  ],
  () => load(),
  { immediate: true },
);

// ── Order ───────────────────────────────────────────────────────────────────

function onDragStart(recipe, event) {
  draggingId.value = recipe.id;
  // Firefox starts no drag at all without payload, and the effect is what
  // makes the cursor say "move" rather than "copy".
  event.dataTransfer?.setData("text/plain", String(recipe.id));
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
}

function onDrop(toIndex) {
  const from = recipes.value.findIndex((row) => row.id === draggingId.value);
  draggingId.value = null;
  if (from < 0 || from === toIndex) return;
  void place(from, toIndex);
}

function move(index, delta) {
  const to = index + delta;
  if (to < 0 || to >= recipes.value.length) return;
  void place(index, to);
}

/**
 * Move one recipe and write the whole order back.
 *
 * The list is moved first so the rail does not stall on the round trip, and
 * put back if the write is refused: the server's order is the one that counts
 * and a tab left showing a move that did not land is the worse of the two.
 */
async function place(from, to) {
  const token = loadToken;
  const before = recipes.value.slice();
  const next = recipes.value.slice();
  const [row] = next.splice(from, 1);
  next.splice(to, 0, row);
  recipes.value = next;
  say(`${row.name || "Recipe"} moved to ${to + 1} of ${next.length}.`);
  try {
    await reorderSavedRecipes(next.map((entry) => entry.id));
  } catch (err) {
    // Only if this is still the same card's list. Selecting another workflow
    // mid-write and then being refused would otherwise render the previous
    // card's recipes under the new card's header.
    if (token !== loadToken) return;
    recipes.value = before;
    const reason = errorMessage(err, "Could not save that order.");
    notices.push({ level: "error", text: reason });
    say(`Order not saved. ${reason}`);
  }
}

// ── The ⋯ menu ──────────────────────────────────────────────────────────────

function startRename(recipe) {
  menuId.value = null;
  renamingId.value = recipe.id;
  renameDraft.value = recipe.name || "";
}

async function commitRename(recipe) {
  if (renamingId.value !== recipe.id) return;
  const name = renameDraft.value.trim();
  renamingId.value = null;
  if (!name || name === recipe.name) return;
  try {
    const saved = await editSavedRecipe(recipe.id, { name });
    recipe.name = saved?.name ?? name;
  } catch (err) {
    notices.push({
      level: "error",
      text: errorMessage(err, "Could not rename that recipe."),
    });
  }
}

function cancelRename() {
  renamingId.value = null;
  renameDraft.value = "";
}

/**
 * The ⋯ buttons, by recipe id, so focus can be put back where it came from.
 *
 * A dialog opened from a menu item leaves nothing to return to: the item
 * unmounts with the menu, and focus falls to the document body. The row's own
 * menu button is the nearest thing that is still there.
 */
const menuButtons = new Map();

function registerMenuButton(id, el) {
  if (el) menuButtons.set(id, el);
  else menuButtons.delete(id);
}

function focusMenuButton(id) {
  menuButtons.get(id)?.$el?.focus?.();
}

/** Each saved card's *Make defaults…*, which its dialog hands focus back to. */
const defaultsButtons = new Map();

function registerDefaultsButton(id, el) {
  if (el) defaultsButtons.set(id, el);
  else defaultsButtons.delete(id);
}

function startExport(recipe) {
  menuId.value = null;
  exportId.value = recipe.id;
}

function closeExport() {
  const id = exportId.value;
  exportId.value = null;
  nextTick(() => focusMenuButton(id));
}

// ── Make these the defaults (#1653 §1.2) ────────────────────────────────────

/** The open dialog's `{recipe, rows, busy, error}`, or null. */
const makingDefaults = ref(null);

function startMakeDefaults(recipe) {
  menuId.value = null;
  makingDefaults.value = {
    recipe,
    rows: recipeDiffs.value[recipe.id].params,
    busy: false,
    error: "",
  };
}

function closeMakeDefaults() {
  const id = makingDefaults.value?.recipe.id;
  makingDefaults.value = null;
  // Its button, or the card's ⋯ when the write took the last difference and
  // the button with it.
  nextTick(() => {
    const button = defaultsButtons.get(id)?.$el;
    if (button?.isConnected) button.focus();
    else focusMenuButton(id);
  });
}

/** A parameter's address, as the whole-set PUT keys it. */
function addressKey(row) {
  return `${row.slot_label}\u0000${row.input_name}`;
}

/** The workflow's edited parameters as the server has them NOW, keyed. */
async function editedNow(workflowId) {
  const body = await getWorkflowCard(workflowId);
  const values = body?.card?.default_recipe?.values || [];
  return new Map(
    values
      .filter((row) => row.provenance === "edited")
      .map(({ slot_label, input_name, value }) => [
        addressKey({ slot_label, input_name }),
        { slot_label, input_name, value },
      ]),
  );
}

/**
 * Change some addresses of a workflow's edited set, leaving every other edit
 * as the server has it at this moment. `changes` maps an address to its new
 * row, or to null to drop the edit (back to computed). The set is read fresh
 * each time rather than taken from a detail read earlier: the PUT replaces the
 * whole set, and the Workflow tab may have edited it since.
 */
async function writeDefaults(workflowId, changes, onlyWhere = null) {
  const edited = await editedNow(workflowId);
  let changed = 0;
  for (const [key, row] of changes) {
    // `onlyWhere` (Undo): change an address only while it still holds the
    // value this tab left there; one edited since belongs to whoever did it.
    if (onlyWhere && !sameEdit(edited.get(key), onlyWhere.get(key))) continue;
    changed += 1;
    if (row) edited.set(key, row);
    else edited.delete(key);
  }
  if (!changed) return 0;
  const body = await setWorkflowDefaults(workflowId, [...edited.values()]);
  if (details.value[workflowId]) details.value[workflowId] = { state: "ready", body };
  emit("defaults-changed", workflowId, body);
  return changed;
}

/** Whether two edited rows (or their absence) hold one value. */
function sameEdit(a, b) {
  if (!a || !b) return !a && !b;
  return String(a.value) === String(b.value);
}

/**
 * Take the ticked parameters into the default recipe.
 *
 * The PUT replaces the whole edited set, so the workflow's existing edits go
 * back with it, the ticked rows replacing any at the same address. Undo puts
 * the previous set back, which also returns a formerly computed value to
 * computed.
 */
async function confirmMakeDefaults(rows) {
  const job = makingDefaults.value;
  if (!job || job.busy) return;
  const workflowId = job.recipe.workflow_id;
  const taken = new Map(
    rows.map((row) => [
      addressKey(row),
      { slot_label: row.slot_label, input_name: row.input_name, value: row.to },
    ]),
  );
  // What Undo puts back: each taken address as it was just before, an edit or
  // nothing (computed). Only these addresses, and only those still holding
  // what this wrote: an edit made elsewhere since is not ours to revert.
  let previous;
  job.busy = true;
  job.error = "";
  try {
    const before = await editedNow(workflowId);
    previous = new Map([...taken.keys()].map((key) => [key, before.get(key) || null]));
    await writeDefaults(workflowId, taken);
  } catch (err) {
    job.busy = false;
    job.error = errorMessage(err, "Could not change the defaults.");
    return;
  }
  closeMakeDefaults();
  const name = details.value[workflowId]?.body?.card?.name || "this workflow";
  notices.push({
    level: "success",
    timeout: 8000,
    text:
      rows.length === 1
        ? `${rows[0].label} is now a default of ${name}.`
        : `${rows.length} values are now defaults of ${name}.`,
    action: {
      label: "Undo",
      handler: async () => {
        try {
          const reverted = await writeDefaults(workflowId, previous, taken);
          if (!reverted) {
            notices.push({
              level: "info",
              text: "Nothing to undo: those values have been changed since.",
            });
          }
        } catch (err) {
          notices.push({
            level: "error",
            text: errorMessage(err, "Could not undo that change to the defaults."),
          });
        }
      },
    },
  });
}

async function removeRecipe(recipe) {
  menuId.value = null;
  const ok = await confirm({
    title: "Delete this recipe?",
    message: `“${recipe.name || "Untitled"}” goes for good. The pictures it made are untouched, and the recipe they carry stays in the list below.`,
    confirmLabel: "Delete",
    danger: true,
  });
  if (!ok) {
    focusMenuButton(recipe.id);
    return;
  }
  const token = loadToken;
  try {
    await deleteSavedRecipe(recipe.id);
    // Only if this is still the same card's list, as in `place()`: another
    // selection made mid-write has its own saved and used recipes.
    if (token !== loadToken) return;
    // The epoch is NOT bumped: this tab is the writer, so announcing it would
    // only make the whole tab re-read what it just did. The looks are re-read
    // on their own, because whether the look keeps its mark (another saved recipe
    // differing only in a strength) is the server's answer.
    recipes.value = recipes.value.filter((row) => row.id !== recipe.id);
    say(`“${recipe.name || "Untitled"}” deleted.`);
    void refreshLooks(token);
  } catch (err) {
    focusMenuButton(recipe.id);
    notices.push({
      level: "error",
      text: errorMessage(err, "Could not delete that recipe."),
    });
  }
}

/**
 * Run this recipe.
 *
 * The popup is opened on the recipe itself, not on the card: the run's source
 * is `saved_recipe_id`, so the row's own prompt, LoRAs and settings are what
 * runs unless the owner changes them in the form.
 */
/**
 * Keep a look the pictures already carry.
 *
 * The cover picture is read for the two things a picture row does not store —
 * the LoRA strengths and the seed — so the recipe holds the look as it was
 * actually run, not a flattened copy of it. The shelf is asked at the same
 * time, because a saved LoRA is found again by its digest.
 */
async function keepLook(look) {
  if (!look.cover_picture_id || savingLook.value) return;
  savingLook.value = look.key;
  try {
    const [recipe, shelf] = await Promise.all([
      getPictureRecipe(look.cover_picture_id, { preflight: false }),
      listAdapters().catch((err) => {
        // The names still save; only the digests are lost, and the dialog
        // says which rows a run will ignore.
        console.warn("Could not read the model shelf's LoRAs:", err);
        return [];
      }),
    ]);
    // **The key comes from the LOOK, never from the re-read.** The look was
    // keyed on the stored `comfyui_*` columns; the live extraction can differ
    // from them (a prompt the column never got, a LoRA spelled another way).
    // Saving the re-read's version would make a recipe whose key is not this
    // look's, so the look would stay unmarked below AND the new recipe
    // would be credited 0 - the one thing both halves promise cannot happen.
    // The picture is read for the two things the columns do not hold: the
    // LoRA strengths, and the seed.
    if (!recipe?.workflow_id) {
      // The workflow this look belongs to is what the recipe is filed under,
      // and the recipe read can legitimately answer with none. Guessing
      // from the selection would file it on a workflow that never made it.
      notices.push({
        level: "error",
        text: "That picture is not filed on a workflow, so there is nowhere to keep its look.",
      });
      return;
    }
    savingSource.value = {
      workflowId: recipe.workflow_id,
      prompt: look.prompt,
      negative: recipe?.negative_prompt || "",
      loras: resolveRecipeLoras(
        look.loras.map((row) => row.filename),
        recipe?.model_slots || [],
        shelf,
      ),
      seed: recipe?.seed_text || "",
      sourcePictureId: look.cover_picture_id,
    };
  } catch (err) {
    notices.push({
      level: "error",
      text: errorMessage(err, "Could not read that picture's recipe."),
    });
  } finally {
    savingLook.value = "";
  }
}

/**
 * Run a look that is not a recipe yet.
 *
 * Opened on the cover picture rather than on a card: there is no
 * `saved_recipe_id` to name, and "run what made this picture" is exactly what
 * the look is. The popup fills itself from that picture's own recipe.
 */
function runLook(look) {
  if (!look.cover_picture_id) return;
  // The prompt the row shows, as the popup's starting text: the picture's own
  // re-read can come back without one, and the box would open empty under a
  // row that plainly has a prompt. It stays an editable prefill.
  runDialog.openRun({
    kind: "picture",
    pictureIds: [look.cover_picture_id],
    prompt: look.prompt,
  });
}

/** Open a recipe's picture in the lightbox, by way of the Workflows view. */
function openPicture(pictureId) {
  workflows.requestOpenPicture(pictureId);
}

function run(recipe) {
  runDialog.openRun({
    kind: "card",
    workflowId: recipe.workflow_id,
    savedRecipe: recipe,
    name: recipe.name,
  });
}
</script>

<style scoped>
.wfrt {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.wfrt-head {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.wfrt-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}

.wfrt-sub,
.wfrt-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

.wfrt-quiet,
.wfrt-sub {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfrt-bad {
  color: rgb(var(--v-theme-surface-error));
}

.wfrt-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.wfrt-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
}

.wfrt-card--dragging {
  opacity: var(--opacity-disabled);
}

.wfrt-top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.wfrt-handle {
  display: inline-flex;
  align-items: center;
  border: 0;
  background: none;
  padding: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: grab;
}

.wfrt-name {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* `cover` so a portrait picture crops rather than letterboxes. */
.wfrt-thumb {
  display: block;
  flex-shrink: 0;
  width: var(--space-8);
  aspect-ratio: 1 / 1;
  object-fit: cover;
  border-radius: var(--radius-sm);
}

.wfrt-prompt {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.wfrt-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

/* `ChipRow`'s `.chip-row__chip`, spelled the same: the dense chip is
   `--tag-h-xs` tall on the input surface, and `--text-2xs` is its size only
   because the height is there to sit it in. This row wraps where `ChipRow`
   clips to one line with a "+N", which is the whole reason it is not that
   component - a recipe's LoRAs are the look and none of them is surplus. */
.wfrt-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  box-sizing: border-box;
  max-width: 100%;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-regular);
  line-height: var(--leading-snug);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The diff line, then +N more, then the prompt: one group, the in-group step. */
.wfrt-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
}

/* Full ink, above the secondary prompt: it is what tells cards apart. Relative
   so the segments measure their offsets against it (the clamp count). */
.wfrt-diff {
  position: relative;
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  font-weight: var(--weight-regular);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-surface));
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.wfrt-diff--open {
  display: block;
  -webkit-line-clamp: unset;
}

/* `.wftab-link`'s inline text button; the pointer target grown to 24px the
   `RunResetChip` way, without padding. */
.wfrt-more {
  position: relative;
  padding: 0;
  border: 0;
  background: none;
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.wfrt-more::after {
  content: "";
  position: absolute;
  inset: -4px 0;
}

.wfrt-more:hover {
  text-decoration-thickness: 2px;
}

.wfrt-bot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-2);
}

.wfrt-facts {
  flex: 1;
  min-width: 0;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* A bare button around the thumbnail: the picture IS the control. */
.wfrt-thumb-btn {
  display: block;
  flex-shrink: 0;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  cursor: pointer;
}

.wfrt-marked {
  flex: 1;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The band over each half: Saved, then From your pictures. */
.wfrt-section {
  margin-top: var(--space-2);
}

/* Heard, never seen: `display: none` would take it out of the accessibility
   tree along with the layout, so it is clipped instead. */
.wfrt-live {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

</style>
