<template>
  <div class="wfrt">
    <div class="inspector-section wfrt-head">
      <p class="wfrt-title">{{ stackName }}</p>
      <p class="wfrt-sub">{{ subtitle }}</p>
    </div>

    <p v-if="loading" class="wfrt-note wfrt-quiet">Reading your recipes…</p>
    <p
      v-else-if="!loadError && !recipes.length && !looks.length"
      class="wfrt-note wfrt-quiet"
    >
      No looks here yet. A picture shows up once PixlStash has read how it was
      made, so a fresh import takes a moment to arrive.
    </p>
    <p v-else-if="loadError" class="wfrt-note wfrt-bad" role="alert">
      {{ loadError }}
    </p>

    <div
      v-if="!loading && !loadError && recipes.length && looks.length"
      class="section-label wfrt-section"
    >
      Kept
    </div>

    <!-- One list, one tab stop. The handle is the control: it is what a
         pointer drags and what Alt+↑/↓ moves, so the row itself needs no
         keyboard behaviour of its own.

         `v-if`, not the band's `v-else`: chaining the two made the band and
         the list alternatives, so the moment there was a used half to label,
         the kept half it labelled disappeared. -->
    <ul
      v-if="!loading && !loadError && recipes.length"
      class="wfrt-list"
    >
      <li
        v-for="(recipe, index) in recipes"
        :key="recipe.id"
        class="wfrt-card"
        :class="{ 'wfrt-card--dragging': draggingId === recipe.id }"
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
          <img
            v-if="recipe.source_picture_id"
            class="wfrt-thumb"
            :src="thumbnail(recipe.source_picture_id)"
            alt=""
            loading="lazy"
            decoding="async"
          />

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

        <p v-if="recipe.prompt" class="wfrt-prompt">{{ recipe.prompt }}</p>

        <div v-if="recipe.loras?.length" class="wfrt-chips">
          <!-- Keyed on the position: a recipe may load one file twice (a
               stacked LoRA is a different look), which the server's own
               `lora_key` keeps on purpose, so the filename is not unique. -->
          <span
            v-for="(lora, slot) in recipe.loras"
            :key="slot"
            class="wfrt-chip"
          >
            <v-icon size="12">mdi-layers-outline</v-icon>
            {{ lora.filename }}
            <span class="wfrt-strength">{{ strengthOf(lora) }}</span>
          </span>
        </div>

        <div class="wfrt-bot">
          <span class="wfrt-facts">{{ factsOf(recipe) }}</span>
          <AppButton size="sm" icon-left="play" @click="run(recipe)">
            Run…
          </AppButton>
        </div>
      </li>
    </ul>

    <!-- The looks the pictures themselves carry. A saved recipe is one
         somebody chose to keep; these are the ones they actually ran, and a
         library that has never pressed Save still has hundreds. The server
         leaves out any look a saved recipe already keeps, so the two halves
         never both claim one. -->
    <template v-if="!loading && !loadError && looks.length">
      <div class="section-label wfrt-section">Used in your pictures</div>
      <ul class="wfrt-list">
        <li v-for="look in looks" :key="look.key" class="wfrt-card">
          <div class="wfrt-top">
            <img
              v-if="look.cover_picture_id"
              class="wfrt-thumb"
              :src="thumbnail(look.cover_picture_id)"
              alt=""
              loading="lazy"
              decoding="async"
            />
            <span class="wfrt-name wfrt-quiet">Not kept yet</span>
          </div>

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

          <div class="wfrt-bot">
            <span class="wfrt-facts">{{ picturesLabel(look.pictures) }}</span>
            <AppButton
              size="sm"
              icon-left="bookmark-plus-outline"
              :loading="savingLook === look.key"
              :disabled="!look.cover_picture_id"
              tooltip="Keep this look as a recipe you can name and reorder"
              @click="keepLook(look)"
            >
              Save…
            </AppButton>
            <AppButton
              v-if="look.cover_picture_id"
              size="sm"
              icon-left="play"
              tooltip="Run this look again, from the picture that made it"
              @click="runLook(look)"
            >
              Run…
            </AppButton>
          </div>
        </li>
      </ul>
    </template>

    <div v-if="!loading && !loadError" class="wfrt-hint">
      <v-icon size="16">mdi-bookmark-plus-outline</v-icon>
      <span>
        Keep a look from any picture with <b class="wfrt-strong">Save as
        recipe</b> on its Recipe tab.
      </span>
    </div>

    <!-- One polite region for the whole list: a reorder made with the
         keyboard, and a refused write that slides the row back, are both
         changes a reader watching the screen can see and a reader who is not
         cannot. -->
    <p class="wfrt-live" role="status" aria-live="polite">{{ liveMessage }}</p>

    <SaveRecipeDialog
      v-if="savingSource"
      :open="Boolean(savingSource)"
      :workflow-key="savingSource.workflowKey"
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
  </div>
</template>

<script setup>
/**
 * The Recipes tab of the Workflows grid's inspector (v1.12 F6).
 *
 * One card per saved recipe of the selected card's **stack**: a recipe runs on
 * any workflow in the stack it was saved from, so a selected member shows the
 * stack's recipes and not a list of its own. `GET /recipes?workflow_key=` does
 * that resolution, which is why this passes the selected key and not the
 * stack's.
 *
 * The order is the owner's and is written back whole on every move, because
 * `PUT /recipes/order` refuses a list with an unknown id rather than leaving
 * half an order behind.
 */
import { computed, nextTick, ref, watch } from "vue";
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
import { useConfirm } from "../../composables/useConfirm";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import { loraKey, promptKey } from "../../utils/recipeKey";
import { resolveRecipeLoras } from "../../utils/recipeLoras";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import { withRef } from "../../utils/withRef";
import ExportRecipeDialog from "../io/ExportRecipeDialog.vue";
import SaveRecipeDialog from "../io/SaveRecipeDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /**
   * The selected cards' keys. Every one's whole stack is listed, as one union.
   *
   * A selection of several is the union of their stacks, counted once: two
   * members of one stack resolve to the same keys, and the server folds them
   * before it reads any picture.
   */
  workflowKeys: { type: Array, default: () => [] },
  /** What the header names - the stack, when a member is selected. */
  stackName: { type: String, default: "" },
  /** How many workflows that stack holds; 1 or 0 means it is not a stack. */
  stackSize: { type: Number, default: 0 },
});

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
/** The looks the stack's own pictures carry, that nobody has saved. */
const looks = ref([]);
/** The look whose cover picture is being read, so its Save… can show it. */
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
  const kept = recipes.value.length;
  const parts = [`${kept} saved recipe${kept === 1 ? "" : "s"}`];
  // The used half is most of what a tab shows before anybody saves anything,
  // so a header counting only the kept ones reads as "0" over a full list.
  if (looks.value.length) parts.push(`${looks.value.length} more used`);
  if (props.stackSize > 1) {
    parts.push(`runs on any of its ${props.stackSize} workflows`);
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

function strengthOf(lora) {
  const value = Number(lora?.strength);
  return Number.isFinite(value) ? value.toFixed(2) : "1.00";
}

/**
 * "31 pictures · steps 12" - the credit, then what the recipe changed.
 *
 * An override is addressed `slot/input`, and the input name is the part a
 * person recognises; the slot is a node label that means nothing outside the
 * graph. Two are named and the rest are counted, because this line has one
 * row of a 288px rail to live in.
 */
function factsOf(recipe) {
  const parts = [`${recipe.pictures ?? 0} picture${recipe.pictures === 1 ? "" : "s"}`];
  const entries = Object.entries(recipe.overrides || {});
  for (const [addressed, value] of entries.slice(0, 2)) {
    parts.push(`${addressed.split("/").pop()} ${value}`);
  }
  if (entries.length > 2) parts.push(`+${entries.length - 2} more`);
  return parts.join(" · ");
}

async function load() {
  const token = (loadToken += 1);
  const mine = () => token === loadToken;
  loadError.value = "";
  const keys = props.workflowKeys.filter(Boolean);
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
    // Both halves together: the server leaves a look out of the second when a
    // recipe in the first keeps it, so reading them apart could show one look
    // twice for as long as the slower read was out.
    const [saved, used] = await Promise.all([
      listSavedRecipes(keys),
      listUsedLooks(keys),
    ]);
    if (!mine()) return;
    recipes.value = saved;
    looks.value = used.map((look) => ({
      ...look,
      // A look has no id, and its prompt and LoRAs are what make it one, so
      // the key both halves match on is also what keys the list.
      key: `${promptKey(look.prompt)}\u0000${loraKey(look.loras)}`,
    }));
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
  [() => props.workflowKeys, () => workflows.recipesEpoch],
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

function startExport(recipe) {
  menuId.value = null;
  exportId.value = recipe.id;
}

function closeExport() {
  const id = exportId.value;
  exportId.value = null;
  nextTick(() => focusMenuButton(id));
}

async function removeRecipe(recipe) {
  menuId.value = null;
  const ok = await confirm({
    title: "Delete this recipe?",
    message: `“${recipe.name || "Untitled"}” goes for good. The pictures it made are untouched.`,
    confirmLabel: "Delete",
    danger: true,
  });
  if (!ok) {
    focusMenuButton(recipe.id);
    return;
  }
  try {
    await deleteSavedRecipe(recipe.id);
    // The row goes locally and the epoch is NOT bumped: this tab is the writer
    // here, so announcing it would only make the tab re-read what it just did.
    // The epoch is for a save made on a surface this one cannot see.
    recipes.value = recipes.value.filter((row) => row.id !== recipe.id);
    say(`${recipe.name || "Recipe"} deleted.`);
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
    // look's, so the look would stay in the used half AND the new recipe
    // would be credited 0 - the one thing both halves promise cannot happen.
    // The picture is read for the two things the columns do not hold: the
    // LoRA strengths, and the seed.
    if (!recipe?.workflow_key) {
      // The card this look belongs to is what the recipe is filed under, and
      // `_picture_workflow_key` can legitimately answer with nothing. Guessing
      // from the selection would file it on a workflow that never made it.
      notices.push({
        level: "error",
        text: "That picture is not filed on a workflow, so there is nowhere to keep its look.",
      });
      return;
    }
    savingSource.value = {
      workflowKey: recipe.workflow_key,
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
  runDialog.openRun({ kind: "picture", pictureIds: [look.cover_picture_id] });
}

function run(recipe) {
  runDialog.openRun({
    kind: "card",
    workflowKey: recipe.workflow_key,
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

.wfrt-strength {
  font-family: var(--font-mono);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfrt-bot {
  display: flex;
  align-items: center;
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

/* The hint points at the gesture that fills this tab, and stays on screen
   whether or not the tab is empty: the second recipe is saved the same way
   the first one was. */
.wfrt-hint {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfrt-strong {
  color: rgb(var(--v-theme-on-surface));
}

/* The band between the two halves. The first only wears one once there is a
   second: a tab showing kept recipes alone needs no word for "the rest". */
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
