<template>
  <AppInspector
    v-model="tab"
    class="wftab"
    label="Inspector"
    :open="sidebarStore.statsOpen"
    :tabs="tabs"
  >
    <p v-if="!card && !multiple" class="wftab-empty">
      Pick a workflow to see what it is made of.
    </p>

    <!-- Several selected: the tab says so and offers the two things that can
         be done to a set of workflows. Run… is still in the footer below,
         disabled, because a control that vanishes teaches nothing. -->
    <template v-else-if="multiple">
      <div class="inspector-section">
        <span class="section-label">Selected</span>
        <p class="wftab-title">
          {{ store.selectedKeys.length }} workflows selected
        </p>
        <div class="wftab-actions">
          <AppButton
            size="sm"
            icon-left="layers-outline"
            :loading="busy === 'stack'"
            @click="stackSelected"
          >
            Stack together
          </AppButton>
          <AppButton
            size="sm"
            icon-left="eye-off-outline"
            :loading="busy === 'hide'"
            @click="hideSelected"
          >
            Hide
          </AppButton>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="inspector-section wftab-head">
        <p class="wftab-title">{{ card.name }}</p>
        <p class="wftab-sub">{{ subtitle }}</p>
      </div>

      <div class="inspector-section">
        <span class="section-label">Models</span>
        <div class="wftab-field">
          <span class="wftab-label">Checkpoint</span>
          <span class="wftab-value">{{ checkpointLabel }}</span>
        </div>
        <div class="wftab-field">
          <span class="wftab-label">VAE</span>
          <span class="wftab-value">{{ vaeLabel }}</span>
        </div>

        <!-- Two lines per slot, as drawn: what is in it and how strong, then
             the switch that decides whether the slot is part of the workflow
             or part of the look. The switch re-keys the card, so it is a
             full-width control of its own and not a chip in the first line. -->
        <template v-if="loraSlots.length">
          <span class="wftab-label wftab-label--group">LoRA slots</span>
          <div v-for="slot in loraSlots" :key="slot.id" class="wftab-slot">
            <div class="wftab-slot-line">
              <span class="wftab-chip" :class="{ 'wftab-chip--empty': !slot.name }">
                <Tooltip v-if="slot.name" :text="slot.name" activator="parent" />
                <v-icon size="16">mdi-layers-outline</v-icon>
                {{ slot.name || "recipe LoRA" }}
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
        <p v-else class="wftab-note wftab-quiet">This workflow has no LoRA slot.</p>
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
            The most used values among its pictures rated 4★ and up. Edit one
            to make it yours.
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

    <!-- The footer is the last thing in the body and sticks to its bottom, so
         Run… is where the design puts it without a second scroll container. -->
    <div v-if="card || multiple" class="wftab-foot">
      <AppButton
        variant="primary"
        icon-left="play"
        block
        :aria-disabled="multiple ? 'true' : undefined"
        :aria-describedby="multiple ? 'wftab-run-reason' : undefined"
        @click="run"
      >
        Run…
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
      <p v-if="multiple" id="wftab-run-reason" class="wftab-note wftab-quiet">
        Run one workflow at a time
      </p>
    </div>
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
// member selected inside its panel shows that member here, which is the only
// way to read a member's own defaults.

import { computed, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import {
  getWorkflowCard,
  patchWorkflowCard,
  setWorkflowDefaults,
  setWorkflowPins,
  setWorkflowSlots,
  stackWorkflows,
} from "../../api/workflows";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useWorkflowRunStore } from "../../stores/useWorkflowRunStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { errorMessage } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import AppInspector from "../widgets/AppInspector.vue";
import Segmented from "../widgets/Segmented.vue";
import Tooltip from "../widgets/Tooltip.vue";
import WorkflowDefaultRow from "./WorkflowDefaultRow.vue";

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
const sidebarStore = useSidebarStore();
const notices = useNoticeStore();
const runStore = useWorkflowRunStore();

const tab = ref("workflow");
const tabs = [
  { value: "workflow", label: "Workflow", icon: "mdi-sitemap-outline" },
];

/** The detail of the selected card, and whether its read is still out. */
const detail = ref(null);
const detailPending = ref(false);
const detailFailed = ref(false);
/** One write at a time, named so only the control that fired it shows it. */
const busy = ref("");
const menuOpen = ref(false);
const notesDraft = ref("");

const multiple = computed(() => store.selectedKeys.length > 1);

const selectedKey = computed(() =>
  store.selectedKeys.length === 1 ? store.selectedKeys[0] : null,
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

const subtitle = computed(() => {
  // Plain numbers, as `WorkflowsView`'s own subtitle writes them: the shelf's
  // grouped spelling went with the shelf in F1b, and this screen never used it.
  const count = card.value?.picture_count ?? 0;
  const pictures = `${count} ${count === 1 ? "picture" : "pictures"}`;
  if (parentStack.value) {
    return `In the ${parentStack.value.name} stack · ${pictures}`;
  }
  if ((card.value?.stack_size ?? 1) > 1) {
    return `Showing the cover · ${pictures}`;
  }
  return pictures;
});

const checkpointLabel = computed(() => {
  const models = card.value?.models ?? [];
  const found = models.find((model) => model.kind === "checkpoint");
  return found?.name || "Not recorded";
});

const vaeLabel = computed(() => {
  const found = (card.value?.models ?? []).find((model) => model.kind === "vae");
  return found?.name || "From the checkpoint";
});

const loraSlots = computed(() =>
  (card.value?.loras ?? []).map((lora, index) => ({
    id: lora.slot_label || `lora-${index}`,
    label: lora.slot_label,
    name: lora.name,
    mark: lora.mark,
  })),
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

const pinnedDefaults = computed(() => defaults.value.filter((row) => row.pinned));
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
  return queueWrite(`slot:${slot.label}`, async () => {
    try {
      const moved = await setWorkflowSlots(key, { [slot.label]: mark });
      // Every card of the topology may have been re-keyed, so the cached
      // stack members are about workflows the hub no longer has.
      store.forgetMembers();
      await store.fetchCards();
      if (!stillOn(key)) return;
      // `select` moves `selectedKey`, which the watcher below turns into the
      // detail read. Calling `loadDetail` here as well fetched the same card
      // twice; when the flip did not move it the watcher does not fire, so
      // that case reads explicitly.
      if (moved.key === key) await loadDetail(key);
      else store.select(moved.key);
    } catch (err) {
      fail(err, "Could not change that LoRA slot.");
    }
  });
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
    try {
      const kept = defaults.value
        .filter(
          (entry) =>
            entry.provenance === "edited" &&
            !(
              entry.slot_label === row.slot_label &&
              entry.input_name === row.input_name
            ),
        )
        .map((entry) => ({
          slot_label: entry.slot_label,
          input_name: entry.input_name,
          value: entry.value,
        }));
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
    try {
      const pins = defaults.value
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
  if (!key || !detail.value || notesDraft.value === (detail.value.notes ?? "")) {
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
  return queueWrite("hidden", async () => {
    try {
      const body = await patchWorkflowCard(key, { hidden: hiding });
      if (stillOn(key)) detail.value = body;
      // A hidden card leaves the grid, so the rail would have nothing to
      // draw were it not for the detail fallback in `card` — which is also
      // what keeps Unhide reachable from here.
      store.forgetMembers();
      await store.fetchCards();
    } catch (err) {
      fail(
        err,
        hiding ? "Could not hide that workflow." : "Could not unhide it.",
      );
    }
  });
}

function stackSelected() {
  const keys = [...store.selectedKeys];
  return queueWrite("stack", async () => {
    try {
      await stackWorkflows(keys);
      store.forgetMembers();
      await store.fetchCards();
      store.clearSelection();
    } catch (err) {
      fail(err, "Could not stack those workflows.");
    }
  });
}

/**
 * Hide every selected workflow.
 *
 * **One at a time, and every one attempted.** There is no bulk-hide route,
 * and each PATCH runs a whole `read_grid()` inside `_read_detail` — whose own
 * docstring says the grid read is paid once per gesture rather than per card
 * — so firing N of them at once maximises both grid reads and single-writer
 * contention on the hub. A plain loop that threw on the first refusal was
 * worse again: it left the ones before it hidden on the server, still drawn
 * and still selected, under one sentence saying none of it worked. So the
 * loop runs to the end and the message names how many actually went.
 */
function hideSelected() {
  const keys = [...store.selectedKeys];
  return queueWrite("hide", async () => {
    let refused = 0;
    let firstError = null;
    for (const key of keys) {
      try {
        await patchWorkflowCard(key, { hidden: true });
      } catch (err) {
        refused += 1;
        firstError = firstError ?? err;
      }
    }
    store.forgetMembers();
    await store.fetchCards();
    if (!refused) {
      store.clearSelection();
      return;
    }
    console.warn("[workflows] some cards would not hide", firstError);
    notices.push({
      level: "error",
      text:
        refused === keys.length
          ? "None of those workflows could be hidden."
          : `${keys.length - refused} of ${keys.length} workflows were hidden; the rest could not be.`,
    });
  });
}

/**
 * Run the selected workflow.
 *
 * Until F5 lands the Run popup, this opens the shipped run panel, which is
 * the app's only way to start a run today. Refused outright while several are
 * selected: the button stays on screen and `aria-disabled` says why, rather
 * than disappearing and leaving nothing to explain.
 */
function run() {
  if (multiple.value) return;
  runStore.openFor("toolbar");
}

watch(selectedKey, (key) => loadDetail(key), { immediate: true });
</script>

<style scoped>
/* The body fills the rail so the footer's `margin-top: auto` reaches the
   bottom of a short panel, and `sticky` keeps it there on a long one. */
.wftab :deep(.inspector-body) {
  flex: 1;
}

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

.wftab-head {
  gap: var(--space-2);
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
  position: sticky;
  bottom: 0;
  margin-top: auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) 0;
  border-top: 1px solid rgb(var(--v-theme-divider));
  background: rgb(var(--v-theme-surface));
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
