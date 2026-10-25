<template>
  <!-- *Change model set…* (#1850, part 3): one of the owner's model sets fills the
       Workflow tab's model rows, as unsaved changes. It replaces *Clone onto a
       set…*: nothing is written here and no workflow is made, the menu only
       works out which rows a set fills and hands that to the tab. -->
  <!-- A wrapper, so the button can be found from here: `v-menu` hands its
       activator a `ref` of its own, and a second `ref` on the same button is
       the one that loses. -->
  <span ref="root" class="wfset">
  <v-menu
    v-model="open"
    location="bottom end"
    :offset="4"
    @after-enter="onOpened"
  >
    <template #activator="{ props: menuProps }">
      <AppButton
        v-bind="{ ...menuProps, ...$attrs }"
        size="sm"
        variant="ghost"
        aria-haspopup="menu"
        :aria-expanded="open ? 'true' : 'false'"
        :aria-describedby="disabled && disabledReason ? disabledReason : undefined"
        :disabled="disabled"
        tooltip="Fill the model rows from one of your sets, as unsaved changes"
        data-testid="wftab-use-set"
      >
        <!-- A row acts at once, so what the menu does is said before it is
             opened (the tooltip). -->
        Change model set…
      </AppButton>
    </template>
    <div
      ref="surface"
      class="ctx-menu wfset-menu"
      role="menu"
      aria-label="Your sets"
      tabindex="-1"
      data-testid="wftab-use-set-menu"
      @keydown="onMenuKeydown"
    >
      <div class="section-label ctx-label" aria-hidden="true">Your sets</div>
      <!-- Hand-made sets first, then the ones read from the owner's pictures.
           A set that cannot be used stays in the list, with why: one that is
           missing would read as a set the owner has lost. Named outright: the
           two spans would otherwise be read run together. -->
      <button
        v-for="row in rows ?? []"
        :key="row.key"
        class="ctx-item"
        :class="{ 'ctx-item--disabled': row.off }"
        type="button"
        role="menuitem"
        :aria-disabled="row.off ? 'true' : undefined"
        :aria-label="`${row.name}, ${row.meta}`"
        :data-set="row.key"
        @click="choose(row)"
      >
        <span class="ctx-label-text">{{ row.name }}</span>
        <span class="ctx-meta">{{ row.meta }}</span>
      </button>
      <!-- The read is out, failed, or found nothing: a row that says so, never
           an empty surface. Opening the menu again asks again. -->
      <button
        v-if="!rows?.length"
        class="ctx-item ctx-item--disabled"
        type="button"
        role="menuitem"
        aria-disabled="true"
        data-testid="wftab-use-set-unread"
      >
        <span class="ctx-label-text">{{
          failed
            ? "Could not read your sets just now."
            : rows
              ? "You have no sets yet."
              : "Reading your sets…"
        }}</span>
      </button>
    </div>
  </v-menu>
  </span>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { VMenu } from "vuetify/components";

import { fetchWorkflowSets } from "../../api/modelShelf";
import { planSetClones } from "../../api/workflows";
import { useFavouritesStore } from "../../stores/useFavouritesStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { loraBase } from "../../utils/loraChain";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import {
  favouriteSetsFirst,
  handMadeName,
  headName,
  setGroups,
} from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";

// The root is a wrapper round the menu, whose content is teleported: what the
// tab puts on this component belongs on the button.
defineOptions({ inheritAttrs: false });

const props = defineProps({
  /** The workflow whose rows are filled. */
  workflowId: { type: String, required: true },
  /**
   * The tab's model rows, each `{address, filename, kind, pick}`: the loader
   * address the default recipe names the model by, the file its loader loads
   * as saved, the row's kind (`checkpoint`, `vae`, `text_encoder`), and the
   * file waiting on it unsaved, if any.
   *
   * `filename` is what a row is found in a plan by, so it has to be the
   * plan's own key: the plans are read off the saved graph with the owner's
   * replacements applied (`model_fixes`), and name each loader by the file it
   * loads then. That is the replacement on a row that holds one, and the
   * missing file, as the graph spells it, on a row that has none yet. `pick`
   * is only what the row shows now, so a set that holds the saved file can
   * take an earlier pick back off it.
   */
  models: { type: Array, required: true },
  disabled: { type: Boolean, default: false },
  /** The id of the element saying why it is disabled, for `aria-describedby`. */
  disabledReason: { type: String, default: "" },
});

/**
 * `use`: `{name, models: [{address, filename}]}`, the rows the set fills. A
 * row put back to its saved file is in it under that file, which the tab reads
 * as "no change here any more".
 */
const emit = defineEmits(["use"]);

const favourites = useFavouritesStore();
const tasksStore = useTasksStore();
favourites.ensureLoaded();
const open = ref(false);
const root = ref(null);
/** The button itself, as the page has it. */
const buttonEl = () =>
  root.value?.querySelector('[data-testid="wftab-use-set"]') ?? null;
const surface = ref(null);
/** Every set with its plan, or null while they are unread. */
const sets = ref(null);
const failed = ref(false);
/** Which read is current: an answer to an earlier one is dropped. */
let asked = 0;
/** The menu was opened from outside the tab, so nothing put focus in it. */
let focusOnOpen = false;

/**
 * A set member under the name the shelf reads it by (`headName`): its display
 * name, else one derived from its file, never `….safetensors`.
 */
function shelfNamed(member) {
  return member ? { ...member, name: headName(member) || member.name } : member;
}

/**
 * The shelf's sets, hand-made first, each with the model ids its plan is
 * asked with: `{key, name, checkpointIds, modelIds, loraIds}`.
 */
function setsOf(payload) {
  const handMade = (payload.hand_made ?? []).map((raw) => {
    const set = { ...raw, members: (raw.members ?? []).map(shelfNamed) };
    // On-shelf only, so an off-shelf checkpoint never trips the server's
    // `gone` check.
    const members = set.members.filter((m) => m.on_shelf && m.id != null);
    return {
      key: `hand:${set.id}`,
      name: handMadeName(set),
      // Held by digest while they download: no shelf id to plan with, so the
      // set is used without them, and says so rather than quietly.
      downloading: set.members
        .filter((m) => !m.on_shelf && tasksStore.runningDownload({ sha256: m.sha256 }))
        .map((m) => m.name),
      // Every checkpoint: a two-model graph takes one per base loader (#1690).
      checkpointIds: members
        .filter((m) => m.slot === "checkpoint")
        .map((m) => m.id),
      // The plan uses a set's VAEs and encoders; its LoRAs are only counted.
      modelIds: members
        .filter((m) => m.slot !== "checkpoint" && m.slot !== "lora")
        .map((m) => m.id),
      loraIds: members.filter((m) => m.slot === "lora").map((m) => m.id),
    };
  });
  // A set whose base model is off the shelf has no checkpoint to load. One
  // named after a model this library hides is not on its shelf either, and a
  // hidden member is not filled in: the shelf's own rule for these sets.
  const hiddenModels = new Set(payload.hidden_models ?? []);
  const evidence = setGroups(payload.combinations ?? [])
    .filter(
      (group) => !group.head?.missing && !hiddenModels.has(group.head.id),
    )
    .map((group) => {
      const models = (group.models ?? []).filter(
        (m) => !hiddenModels.has(m.id),
      );
      const others = models.filter((m) => m.id !== group.head.id);
      return {
        key: group.key,
        name: shelfNamed(group.head)?.name || "Unnamed set",
        // The head, then any other checkpoint the pictures ran beside it.
        checkpointIds: [
          group.head.id,
          ...others.filter((m) => m.kind === "checkpoint").map((m) => m.id),
        ],
        modelIds: others.filter((m) => m.kind !== "adapter").map((m) => m.id),
        loraIds: models.filter((m) => m.kind === "adapter").map((m) => m.id),
      };
    });
  // A set hidden in this library is not offered (#1869), whichever kind it
  // is. No "Show them" here, so the payload's own list decides.
  const hidden = new Set(payload.hidden_sets ?? []);
  return [...handMade, ...evidence].filter((set) => !hidden.has(set.key));
}

/** Whether `files` names `file`, under whatever folder and case. */
function names(files, file) {
  const base = loraBase(file);
  return (files ?? []).some((other) => loraBase(other) === base);
}

/**
 * Whether the set holds the very file a row's loader loads, so taking the set
 * leaves that loader as saved. `swaps` lists only the files that change, so
 * this is read off the rest of the plan: a base model the plan paired with a
 * set checkpoint (not among `unpaired_bases`), or a VAE or text encoder the
 * set has a model for (`takes`, which lists them changed or not).
 */
function keepsSaved(plan, model) {
  return model.kind === "checkpoint"
    ? !names(plan.unpaired_bases, model.filename)
    : names(Object.keys(plan.takes ?? {}), model.filename);
}

/**
 * The model rows a set changes: each row's address with the file it takes.
 * Two spellings of one file are the same file (`loraBase`, as the tab compares
 * them), the graph's own spelling first.
 *
 * A row takes what the plan's `swaps` (the graph's file -> the set's) give
 * it, or its own saved file where the set holds exactly that. Either is a
 * change only when it is not what the row shows now: so a set that agrees
 * with the saved graph does nothing to an untouched row, and takes an earlier
 * unsaved pick back off one that has it.
 */
function fillOf(plan) {
  const swaps = plan.swaps ?? {};
  const files = Object.keys(swaps);
  return props.models.flatMap((model) => {
    const base = loraBase(model.filename);
    const from =
      model.filename in swaps
        ? model.filename
        : files.find((file) => loraBase(file) === base);
    let takes;
    if (from !== undefined) takes = swaps[from];
    else if (model.pick && keepsSaved(plan, model)) takes = model.filename;
    else return [];
    if (loraBase(takes) === loraBase(model.pick || model.filename)) return [];
    return [{ address: model.address, filename: takes }];
  });
}

/**
 * The sets in the order they are drawn: hand-made first, a favourite leading
 * inside each group. Null while the sets are unread.
 */
const rankedSets = computed(
  () =>
    sets.value &&
    favouriteSetsFirst(sets.value, (key) => favourites.isFavourite("set", key)),
);

/** The rows as drawn, or null while the sets are unread. */
const rows = computed(
  () =>
    rankedSets.value?.map((set) => {
      const fill = fillOf(set.plan);
      const wontLoad = set.plan.fit === "wont_load";
      const meta = wontLoad
        ? // The server's own words, as sent.
          set.plan.reason || "will not load here"
        : !fill.length
          ? "changes nothing here"
          : [
              `fills ${fill.length} ${fill.length === 1 ? "row" : "rows"}`,
              // The change set has no way to add a loader.
              set.loraIds.length ? "its LoRAs are not added" : "",
            ]
              .filter(Boolean)
              .join(" · ");
      const coming = set.downloading ?? [];
      return {
        key: set.key,
        name: set.name,
        fill,
        meta: coming.length
          ? `${meta} · ${comingWhat(coming)} still downloading, left out`
          : meta,
        off: wontLoad || !fill.length,
        coming,
      };
    }) ?? null,
);

/** Every set on the shelf, and one plan request for all of them. */
async function read() {
  const mine = (asked += 1);
  const workflowId = props.workflowId;
  sets.value = null;
  failed.value = false;
  try {
    const found = setsOf(await fetchWorkflowSets());
    if (mine !== asked) return;
    const answer = found.length
      ? await planSetClones(
          workflowId,
          found.map((set) => ({
            key: set.key,
            checkpoint_ids: set.checkpointIds,
            model_ids: set.modelIds,
          })),
        )
      : null;
    if (mine !== asked) return;
    const plans = new Map((answer?.plans ?? []).map((plan) => [plan.key, plan]));
    sets.value = found
      .filter((set) => plans.has(set.key))
      .map((set) => ({ ...set, plan: plans.get(set.key) }));
  } catch (err) {
    if (mine !== asked) return;
    console.warn(`[workflows] could not read the sets for ${workflowId}`, err);
    failed.value = true;
  }
}

/** "flux1-dev.safetensors", or "2 of its models". */
function comingWhat(names) {
  return names.length === 1 ? names[0] : `${names.length} of its models`;
}

function choose(row) {
  if (row.off) return;
  open.value = false;
  emit("use", { name: row.name, models: row.fill });
  if (row.coming.length) {
    useNoticeStore().push({
      level: "warning",
      text: `${comingWhat(row.coming)} ${row.coming.length === 1 ? "is" : "are"} still downloading, so "${row.name}" was used without ${row.coming.length === 1 ? "it" : "them"}. Use the set again once the download has finished.`,
      key: `use-set-downloading-${row.key}`,
    });
  }
}

/** Put the keyboard on the button. */
function focus() {
  buttonEl()?.focus();
}

/**
 * Open the menu from somewhere else (the grid's own *Change model set…* row). The
 * button may be scrolled out of the rail, and the menu hangs off it.
 */
function openMenu() {
  if (props.disabled || open.value) return;
  buttonEl()?.scrollIntoView?.({ block: "nearest" });
  focusOnOpen = true;
  open.value = true;
}

/**
 * A `v-menu` puts focus in its content only for its own activator's arrow
 * keys, so one opened from outside leaves the keyboard where it was, with
 * Escape and the arrows going nowhere near it. Focus goes to the surface once
 * the menu is drawn (after its transition, so after whatever the surface that
 * asked for it does with focus as it closes), unless it is on a row already.
 */
function onOpened() {
  const el = surface.value;
  if (focusOnOpen && el && !el.contains(document.activeElement)) el.focus();
  focusOnOpen = false;
}

// Read on open, never on mount: the sets are the whole shelf's and the plans
// cost a graph walk each. A closed menu takes the row that had focus with it,
// and focus would fall to the page: it goes back to the button, unless the
// click that closed the menu landed on another control.
watch(open, async (now) => {
  if (now) {
    void read();
    return;
  }
  focusOnOpen = false;
  await nextTick();
  const active = document.activeElement;
  if (!active || active === document.body || active.closest(".wfset-menu")) {
    focus();
  }
});

// Another workflow: the rows on show are about the one before. The next open
// reads again, which is also what drops an answer still out for this one.
watch(
  () => props.workflowId,
  () => {
    open.value = false;
  },
);

defineExpose({ open: openMenu, focus });
</script>

<style scoped>
/* Wider than the 288px rail's button, and a width of its own rather than
   its content's: the sets are read after the menu has opened and been placed,
   and a surface that then grew with a long row ran off the window's edge. */
.wfset {
  display: inline-flex;
}

.wfset-menu {
  width: 320px;
}

/* A row is a set's name over what taking it would do. On two lines, and the
   second one wraps: a set that will not load says why in the server's own
   sentence, which no single line of a menu this wide holds. */
.wfset-menu .ctx-item[data-set] {
  flex-direction: column;
  align-items: flex-start;
  gap: 0;
  padding-block: var(--space-2);
  white-space: normal;
}

.wfset-menu .ctx-item[data-set] .ctx-label-text {
  flex: none;
  max-width: 100%;
}

.wfset-menu .ctx-item[data-set] .ctx-meta {
  margin-left: 0;
}

/* A set that cannot be used says why, and the reason has to stay readable:
   the name fades as a disabled row's does, the reason keeps its own ink. */
.wfset-menu .ctx-item--disabled {
  opacity: 1;
}

.wfset-menu .ctx-item--disabled .ctx-label-text {
  opacity: var(--opacity-disabled);
}
</style>
