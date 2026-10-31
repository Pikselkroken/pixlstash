<template>
  <!-- The tray a HAND-MADE set opens in (#1520): the same band, caret and
       header as the evidence tray (`ModelSetPanel`), with the fixed slots in
       place of the union. `rowgroup` for the reason that panel gives: it sits
       inside the grid's treegrid, where only rows and rowgroups may. -->
  <div
    :id="panelId"
    class="msp mss"
    role="rowgroup"
    :style="notchStyle"
    data-testid="model-set-slots-panel"
  >
    <span
      class="tbm-caret"
      :class="columns > 1 ? 'msp__notch' : 'tbm-caret--start'"
      aria-hidden="true"
    ></span>

    <div class="msp__header" role="row" aria-level="2">
      <div role="gridcell">
        <div class="msp__bar">
          <span class="msp__name">{{ name }}</span>
          <span class="mss__mark">{{
            set.automatic ? "Automatic" : "Custom"
          }}</span>
          <span class="msp__count num">{{ facts }}</span>
          <!-- One unit, so a narrow bar wraps the actions together to the
               right of the next line rather than stranding Close on the left. -->
          <span class="mss__actions">
            <!-- Only while there is something to undo: an automatic set
                 already holds exactly what its pictures ran with. -->
            <AppButton
              v-if="canReset(set)"
              variant="ghost"
              size="sm"
              icon-left="backup-restore"
              data-testid="set-reset"
              tooltip="Take out what you added and restore what you removed. Your name stays."
              @click="emit('reset')"
              >Reset to automatic</AppButton
            >
            <AppButton
              variant="ghost"
              size="sm"
              icon-left="table-arrow-down"
              :disabled="!canFillFromSet"
              :tooltip="
                canFillFromSet
                  ? 'Add models from your other sets'
                  : 'None of your other sets has anything to add.'
              "
              @click="emit('fill', { mode: 'set', el: $event.currentTarget })"
              >Fill from a set…</AppButton
            >
            <AppButton
              variant="ghost"
              size="sm"
              icon-left="close"
              icon-only
              tooltip="Close this set"
              @click="emit('close')"
            />
          </span>
        </div>
      </div>
    </div>

    <!-- PixlStash's check of this set's pictures and the owner's verdicts, from
         its on-shelf members. No plain sentence: a hand-made set claims nothing
         about co-occurrence. -->
    <SetVerdictNotes
      ref="notes"
      :check="check"
      :members="verdictMembers"
      :save-set="saveSet"
      :save-member="saveMember"
    />

    <div
      v-if="incomplete"
      class="msp__note mss__warn"
      role="row"
      aria-level="2"
    >
      <div role="gridcell">
        <v-icon size="16">mdi-alert-outline</v-icon>
        <span
          >Incomplete: no checkpoint. Add one in the Checkpoint slot so this set
          can seed a workflow.</span
        >
      </div>
    </div>

    <!-- Offered once, when a checkpoint with no base model goes in: the
         suggestions in this set follow it. "Not now" leaves Set base model on
         the shelf menu. -->
    <div
      v-if="baseOffer"
      class="msp__note mss__base"
      role="row"
      aria-level="2"
      data-testid="base-model-offer"
    >
      <div role="gridcell">
        <v-icon size="16">mdi-cube-outline</v-icon>
        <span class="mss__basetext"
          ><strong>{{ baseOffer.name }}</strong> has no base model. Suggestions
          in this set follow it.</span
        >
        <!-- The field is the shelf's own base-model combobox, drawn with the
             app's input style: bare, it rendered as nothing a reader could see
             to type into. -->
        <BaseModelInput
          ref="baseInputEl"
          v-model="baseValue"
          class="tbm-input mss__baseinput"
          placeholder="Type a base model, e.g. SDXL 1.0"
          aria-label="Base model"
          :aria-describedby="baseHint ? `${panelId}-base-hint` : undefined"
          @confirm="submitBase"
          @cancel="emit('dismiss-base')"
        />
        <!-- Never disabled: a Set that greys out with nothing saying why is a
             dead end. Pressed with the field empty it says what is missing and
             puts the cursor where to fix it. -->
        <AppButton variant="primary" size="sm" @click="submitBase">{{
          baseValue.trim() ? `Set ${baseValue.trim()}` : "Set"
        }}</AppButton>
        <AppButton variant="ghost" size="sm" @click="emit('dismiss-base')"
          >Not now</AppButton
        >
        <span
          v-if="baseHint"
          :id="`${panelId}-base-hint`"
          class="mss__basehint"
          role="status"
          >{{ baseHint }}</span
        >
      </div>
    </div>

    <!-- What the set follows, and what the owner changed on top (#1972). -->
    <div
      v-if="followLine"
      class="msp__note mss__follow"
      role="row"
      aria-level="2"
      data-testid="set-follows"
    >
      <div role="gridcell">
        <v-icon size="16">mdi-image-multiple-outline</v-icon>
        <span>{{ followLine }}</span>
      </div>
    </div>

    <!-- The template offer (#1999): lasting, until the owner adds the files
         or stops asking. A cursor stop ahead of the slots, so Enter adds. In
         the card's words, so the card and the tray ask one question. -->
    <div
      v-if="set.offer"
      :id="`${panelId}-row-offer`"
      class="mss__offer msp__member"
      :class="{ 'mss__offer--cur': cursorKey === 'offer' }"
      role="row"
      aria-level="2"
      :aria-label="`${offerLead} ${offerNote}${set.offer.models.length ? ` Enter adds ${set.offer.models.length === 1 ? 'it' : 'them'}.` : ''}`"
      :tabindex="cursorKey === 'offer' ? 0 : -1"
      data-key="offer"
      data-testid="merge-offer-strip"
      @click="emit('cursor', 'offer')"
    >
      <div class="mss__offercell" role="gridcell">
        <div class="mss__offertext">
          <span aria-hidden="true"
            ><strong>{{ offerLead }}</strong>
            <span class="mss__offernote">{{ offerNote }}</span></span
          >
          <!-- A template's files the shelf lacks (#1999), downloaded in the
               app as a run's missing model is: the committed template list
               is the download's own source. A read-only session cannot
               download, so it keeps the https link. -->
          <span
            v-if="set.offer.downloads?.length"
            class="mss__offernote"
            data-testid="offer-downloads"
            >Not on your shelf:
            <template v-for="(file, i) in set.offer.downloads" :key="file.sha256"
              >{{ i ? ", " : ""
              }}<template v-if="/^https:\/\//.test(file.url) && !isReadOnly"
                >{{ file.name
                }}<ModelDownloadButton :file="file.name" @click.stop /></template
              ><a
                v-else-if="/^https:\/\//.test(file.url)"
                class="mss__dl"
                :href="file.url"
                target="_blank"
                rel="noopener noreferrer"
                :aria-label="`Download ${file.name}`"
                @click.stop
                >{{ file.name }}</a
              ><template v-else>{{ file.name }}</template></template
            ></span
          >
        </div>
        <AppButton
          variant="ghost"
          size="sm"
          tabindex="-1"
          :tooltip="`Keep this set separate from ${offerThose}. Reset to automatic suggests them again.`"
          @click.stop="emit('keep-separate')"
          >Stop asking</AppButton
        >
        <AppButton
          v-if="set.offer.models.length"
          variant="primary"
          size="sm"
          icon-left="plus"
          tabindex="-1"
          @click.stop="emit('merge')"
          >{{
            set.offer.models.length === 1
              ? "Add to set"
              : `Add all ${set.offer.models.length}`
          }}</AppButton
        >
      </div>
    </div>

    <div class="mss__slots" role="presentation">
      <div
        v-for="{ slot, items } in slots"
        :key="slot.id"
        class="mss__slot"
        role="presentation"
        :data-slot="slot.id"
      >
        <div class="mss__label" role="presentation">
          <span class="mss__slotname">{{ slot.label }}</span>
          <span class="mss__hint">{{ slot.hint }}</span>
        </div>
        <div class="mss__tiles" role="presentation">
          <template v-for="item in items" :key="item.key">
            <div
              v-if="item.member"
              :id="`${panelId}-row-${item.key}`"
              class="mss__tile msp__member"
              :class="{
                'mss__tile--gone': !item.member.on_shelf && !coming(item.member),
                'mss__tile--on': isSelected(item.member),
              }"
              role="row"
              aria-level="2"
              :aria-label="tileName(item.member, slot)"
              :aria-selected="
                selectable(item.member)
                  ? String(isSelected(item.member))
                  : undefined
              "
              :tabindex="cursorKey === item.key ? 0 : -1"
              :data-key="item.key"
              @click="emit('select', { member: item.member, event: $event })"
              @dblclick="item.member.on_shelf && emit('pick', item.member)"
              @contextmenu="
                emit('menu', { member: item.member, event: $event })
              "
            >
              <span class="mss__cell" role="gridcell">
                <ModelMark
                  class="mss__markicon"
                  :row="markOf(item.member).row"
                  :ring="markOf(item.member).ring"
                  :style="markOf(item.member).style"
                  aria-hidden="true"
                />
                <span class="mss__tilename" aria-hidden="true"
                  ><Tooltip
                    :text="
                      modelFileTitle(
                        marks.get(item.member.id)?.row ?? item.member,
                      )
                    "
                    activator="parent"
                  />{{ item.member.name }}</span
                >
                <span
                  v-if="quantBadge(item.member.quant)"
                  class="mss__quant"
                  aria-hidden="true"
                  ><Tooltip
                    :text="quantBadge(item.member.quant).title"
                    activator="parent"
                  />{{ quantBadge(item.member.quant).label }}</span
                >
                <!-- Held by its digest while it downloads: on its way, not
                     gone, and on the shelf the moment it lands. -->
                <span
                  v-if="coming(item.member)"
                  class="mss__coming"
                  aria-hidden="true"
                  ><DownloadingMark
                    :percent="coming(item.member).percent"
                  />Downloading</span
                >
                <span
                  v-else-if="!item.member.on_shelf"
                  class="mss__gone"
                  aria-hidden="true"
                  >Not on shelf</span
                >
                <span
                  v-if="item.member.new"
                  class="mss__origin mss__origin--new"
                  aria-hidden="true"
                  >New</span
                >
                <span
                  v-else-if="item.member.origin === 'added'"
                  class="mss__origin"
                  aria-hidden="true"
                  >Added by you</span
                >
                <span
                  v-else-if="item.member.base_model"
                  class="msp__pill"
                  aria-hidden="true"
                  >{{ item.member.base_model }}</span
                >
                <!-- Off the tab order like the remove button beside it: the
                     grid's cursor owns Tab. -->
                <VerdictIcon
                  v-if="memberVerdict(item.member)"
                  interactive
                  tabindex="-1"
                  :data-verdict-member="item.member.id"
                  :verdict="memberVerdict(item.member)"
                  :label="memberVerdictLabel(item.member)"
                  @click="notes?.reopenMember(item.member.id)"
                />
                <AppButton
                  class="mss__remove"
                  variant="ghost"
                  size="sm"
                  icon-left="close"
                  icon-only
                  tabindex="-1"
                  tooltip="Remove from set (the file stays on the shelf)"
                  @click.stop="emit('remove', item.member)"
                />
              </span>
            </div>
            <!-- A model the owner removed (#1972), or one the template offer
                 would add (#1999), drawn where it would be. -->
            <div
              v-else-if="item.ghost"
              :id="`${panelId}-row-${item.key}`"
              class="mss__tile mss__tile--ghost msp__member"
              role="row"
              aria-level="2"
              :aria-label="ghostName(item.ghost, slot)"
              :tabindex="cursorKey === item.key ? 0 : -1"
              :data-key="item.key"
              @click="emit('cursor', item.key)"
            >
              <span class="mss__cell" role="gridcell">
                <ModelMark
                  class="mss__markicon mss__ghostmark"
                  :row="{ display_name: item.ghost.name }"
                  aria-hidden="true"
                />
                <span class="mss__tilename" aria-hidden="true">{{
                  item.ghost.name
                }}</span>
                <span
                  v-if="quantBadge(item.ghost.quant)"
                  class="mss__quant"
                  aria-hidden="true"
                  ><Tooltip
                    :text="quantBadge(item.ghost.quant).title"
                    activator="parent"
                  />{{ quantBadge(item.ghost.quant).label }}</span
                >
                <span class="mss__ghostcount num" aria-hidden="true">{{
                  ghostCount(item.ghost)
                }}</span>
                <AppButton
                  v-if="item.ghost.removed"
                  variant="outline"
                  size="sm"
                  tabindex="-1"
                  :tooltip="`Put ${withQuant(item.ghost)} back in this set`"
                  @click.stop="emit('restore', item.ghost)"
                  >Restore</AppButton
                >
                <AppButton
                  v-else
                  variant="outline"
                  size="sm"
                  tabindex="-1"
                  :tooltip="`Add ${withQuant(item.ghost)} to this set`"
                  @click.stop="emit('add-ghost', item.ghost)"
                  >Add</AppButton
                >
              </span>
            </div>
            <div
              v-else
              :id="`${panelId}-row-${item.key}`"
              class="mss__tile mss__tile--add msp__member"
              role="row"
              aria-level="2"
              :aria-label="`${slot.add}…`"
              :tabindex="cursorKey === item.key ? 0 : -1"
              :data-key="item.key"
              @click="
                emit('add', { slotId: slot.id, el: $event.currentTarget })
              "
            >
              <span class="mss__cell" role="gridcell">
                <v-icon size="16" aria-hidden="true">mdi-plus</v-icon>
                <span aria-hidden="true">{{ slot.add }}</span>
              </span>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * One open hand-made workflow set (#1520): a MENU of models, slot by slot.
 *
 * Every set has the same five slots - Checkpoint (one or more: a two-model
 * workflow loads two), Text encoders, VAE, LoRAs, Other - and each ends in a
 * dashed ＋ tile that opens the chooser.
 * A member whose file has left the shelf stays, struck through and marked "Not
 * on shelf": it is kept by hash and comes back to life when the file does.
 *
 * **A member tile is one file**, so it selects like one and its menu is the
 * shelf's own (with Remove from set added); the ✕ on the tile is the pointer's
 * short way to Remove from set. Nothing here deletes a file on its own - Delete
 * reaches the shelf's delete, which warns.
 *
 * The rows are cursor stops of the grid's roving index; this component only
 * draws them and reports gestures, exactly as `ModelSetPanel` does.
 */
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { useTasksStore } from "../../stores/useTasksStore";
import { isReadOnly } from "../../utils/apiClient";
import { modelFileTitle, quantBadge } from "../../utils/modelShelf";
import { memberVerdictOf, memberVerdictText } from "../../utils/setVerdicts";
import {
  pictureCount,
  recipeCount,
  offerQuestion,
  setChanges,
  canReset,
  setCheckpoint,
  setSlots,
} from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";
import BaseModelInput from "../widgets/BaseModelInput.vue";
import DownloadingMark from "../widgets/DownloadingMark.vue";
import ModelDownloadButton from "../widgets/ModelDownloadButton.vue";
import ModelMark from "../widgets/ModelMark.vue";
import Tooltip from "../widgets/Tooltip.vue";
import VerdictIcon from "../widgets/VerdictIcon.vue";
import SetVerdictNotes from "./SetVerdictNotes.vue";

const props = defineProps({
  panelId: { type: String, required: true },
  /** The hand-made set, as `GET /models/workflow-sets` serves it. */
  set: { type: Object, required: true },
  name: { type: String, default: "" },
  columns: { type: Number, default: 1 },
  columnIndex: { type: Number, default: 0 },
  cursorKey: { type: String, default: "" },
  gap: { type: Number, default: 12 },
  selectedIds: { type: Object, default: () => new Set() },
  selectableIds: { type: Object, default: () => new Set() },
  /**
   * `model.id` → `{row, ring, style}`, the shelf's own mark, so a tile wears the
   * model's icon and the face and ring of whoever it is assigned to.
   */
  marks: { type: Object, default: () => new Map() },
  canFillFromSet: { type: Boolean, default: false },
  /**
   * `{key, name, guess}` while the one-time base-model offer is up, else null.
   * `key` names the offer (set and checkpoint), so a refetch is not a new one.
   */
  baseOffer: { type: Object, default: null },
  /** This set's `set_checks` entry, or null when none was served. */
  check: { type: Object, default: null },
  /** `(verdict) => Promise`: record the set's verdict; rejects on failure. */
  saveSet: { type: Function, default: async () => {} },
  /** `(modelId, verdict) => Promise`: the same for one member. */
  saveMember: { type: Function, default: async () => {} },
});

const emit = defineEmits([
  "close",
  "select",
  "menu",
  "remove",
  "add",
  "fill",
  "pick",
  "set-base",
  "dismiss-base",
  // A removed model's Restore, Reset to automatic (#1972), the template
  // offer's Add all, Stop asking and one ghost's Add (#1999), and a click
  // that only moves the cursor.
  "restore",
  "reset",
  "merge",
  "keep-separate",
  "add-ghost",
  "cursor",
]);

const slots = computed(() => setSlots(props.set));

const notes = ref(null);

/** The on-shelf members, by the names the notes use. */
const verdictMembers = computed(() =>
  (props.set.members ?? []).filter((m) => m.on_shelf && m.id != null),
);

function memberVerdict(member) {
  return memberVerdictOf(props.check, member.id);
}

function memberVerdictLabel(member) {
  return `Your verdict: ${memberVerdictText(memberVerdict(member), member.name)}`;
}

/** The offer's field, starting from the shelf's own guess when it has one. */
const baseValue = ref("");
const baseHint = ref("");
const baseInputEl = ref(null);
// Reset only when a DIFFERENT offer appears. The parent's offer is a computed
// that returns a fresh object on every refetch while it is up, and resetting on
// identity wiped whatever the reader was typing into the field.
watch(
  // The offer's identity alone: a refetch that refines the guess mid-offer
  // must not wipe what is being typed either.
  () => props.baseOffer?.key ?? "",
  () => {
    baseValue.value = props.baseOffer?.guess ?? "";
    baseHint.value = "";
  },
  { immediate: true },
);
watch(baseValue, () => {
  if (baseValue.value.trim()) baseHint.value = "";
});

function submitBase() {
  const value = baseValue.value.trim();
  if (value) {
    emit("set-base", value);
    return;
  }
  baseHint.value = "Type or pick a base model first.";
  // The combobox's root IS the input, so its element takes the focus.
  (baseInputEl.value?.$el ?? baseInputEl.value)?.focus?.();
}

const incomplete = computed(
  () => !(props.set.members ?? []).some((m) => m.slot === "checkpoint"),
);

/** What the owner changed over what the pictures ran with. */
const changes = computed(() => setChanges(props.set));
const changeCount = computed(
  () => changes.value.added + changes.value.removed,
);

const facts = computed(() => {
  const count = (props.set.members ?? []).length;
  const pictures = Number(props.set.picture_count) || 0;
  const recipes = Number(props.set.recipes) || 0;
  const n = changeCount.value;
  return [
    count === 1 ? "1 model" : `${count} models`,
    recipes ? recipeCount(recipes) : null,
    pictures ? pictureCount(pictures) : "no pictures yet",
    n ? `${n} ${n === 1 ? "change" : "changes"} from automatic` : null,
    madeLabel(props.set.created_at),
  ]
    .filter(Boolean)
    .join(" · ");
});

/**
 * What the set follows, said once: a set with a checkpoint on the shelf moves
 * with the pictures made with it, and the owner's changes stay on top.
 */
const followLine = computed(() => {
  const checkpoint = setCheckpoint(props.set);
  if (!checkpoint?.on_shelf) return "";
  const { added, removed } = changes.value;
  const lead = `Follows the pictures made with ${checkpoint.name}: a model they start using joins this set.`;
  if (!added && !removed) return lead;
  const parts = [
    added ? `${added} added` : null,
    removed ? `${removed} removed` : null,
  ].filter(Boolean);
  return `${lead} Your changes stay on top of it: ${parts.join(", ")}.`;
});

/** "made today", "made 3 Sep" - when the set was made, in the reader's words. */
function madeLabel(iso) {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return "";
  const today = new Date();
  if (at.toDateString() === today.toDateString()) return "made today";
  return `made ${at.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`;
}

function isSelected(member) {
  return member.id != null && props.selectedIds.has(member.id);
}

function selectable(member) {
  return member.on_shelf && props.selectableIds.has(member.id);
}

/**
 * What a tile's mark draws: the shelf's own for a model on the shelf, and for
 * one that has left it a plain generated mark from its kept name, with no ring
 * - there is no row left to say who it is assigned to.
 */
function markOf(member) {
  const mark = member.id != null ? props.marks.get(member.id) : null;
  if (mark) return mark;
  return {
    row: {
      display_name: member.name,
      filename: member.filename,
      base_model: member.base_model,
    },
    ring: null,
    style: {},
  };
}

/** The strip's first line: the card's own statement of the offer. */
const offerLead = computed(() => {
  const question = offerQuestion(props.set);
  return question ? `${question.who}${question.rest}` : "";
});

/** What the template offer is from, for the strip and its Stop asking. */
const offerThose = computed(() =>
  props.set.offer ? `the ${props.set.offer.template.title} template` : "",
);

const offerNote = computed(() => {
  const n = props.set.offer?.models.length ?? 0;
  const others = props.set.offer?.others ?? [];
  return [
    n
      ? n === 1
        ? "The one on your shelf is dashed below."
        : `The ${n} on your shelf are dashed below.`
      : "",
    others.length
      ? `Other templates built around it: ${others.map((o) => o.title).join(", ")}.`
      : "",
  ]
    .filter(Boolean)
    .join(" ");
});

/**
 * A ghost's note: "Removed by you · 4 recipes", what the pictures still use a
 * removed model in, or "template" for one the template offer would add.
 */
function ghostCount(ghost) {
  if (!ghost.removed) return "template";
  return `Removed by you · ${
    ghost.picture_count
      ? pictureCount(ghost.picture_count)
      : recipeCount(ghost.recipes)
  }`;
}

function ghostName(ghost, slot) {
  if (!ghost.removed) {
    return `${withQuant(ghost)}, ${slot.label}, offered by ${offerThose.value}. Enter adds it, Delete keeps it out.`;
  }
  return `${withQuant(ghost)}, ${slot.label}, removed by you: used in ${ghostCount(ghost)}. Enter restores it.`;
}

/** A name with the precision `modelName` took out of it, for a spoken label. */
function withQuant(model) {
  return [model.name, quantBadge(model.quant)?.label].filter(Boolean).join(" ");
}

const tasksStore = useTasksStore();

/** The download an off-shelf member is, by its digest, or null. */
function coming(member) {
  return member.on_shelf
    ? null
    : tasksStore.runningDownload({ sha256: member.sha256 });
}

function tileName(member, slot) {
  return [
    withQuant(member),
    slot.label,
    member.on_shelf
      ? member.base_model
      : coming(member)
        ? "still downloading"
        : "not on shelf, kept by its hash",
    member.new
      ? "new: the pictures started using it"
      : member.origin === "added"
        ? "added by you"
        : null,
  ]
    .filter(Boolean)
    .join(", ");
}

// The evidence tray's notch arithmetic, restated for the same reason it is
// restated there: a few lines, and sharing them would couple two components.
const notchStyle = computed(() => {
  const style = { "--wf-columns": props.columns, "--wf-gap": `${props.gap}px` };
  if (props.columns > 1) {
    style["--notch"] = `${((props.columnIndex + 0.5) / props.columns) * 100}%`;
  }
  return style;
});
</script>

<style scoped>
/* The band itself is `ModelSetPanel`'s, taken by class rather than copied:
   `.msp*` below are that component's scoped names, so the few this panel needs
   are restated here. */
.msp {
  position: relative;
  grid-column: 1 / -1;
  margin-bottom: var(--space-4);
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-panel));
}

.msp__notch {
  left: var(--notch);
  transform: translateX(-50%) rotate(45deg);
}

.msp__bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--bar-height);
  padding: 0 var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  flex-wrap: wrap;
}

.msp__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.msp__count {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.msp__note > [role="gridcell"] {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: var(--space-4) var(--space-4) 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
}

.mss__warn > [role="gridcell"] {
  background: rgba(var(--v-theme-surface-warning), 0.14);
}

.mss__warn .v-icon {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-warning));
}

.mss__base > [role="gridcell"] {
  flex-wrap: wrap;
  align-items: center;
  background: rgba(var(--v-theme-surface-info), 0.14);
}

.mss__base .v-icon {
  flex-shrink: 0;
  color: rgb(var(--v-theme-surface-info));
}

.mss__basetext {
  flex: 1 1 16rem;
}

/* `:deep()`: `BaseModelInput` is not a root this file's scope reaches. */
.mss__base :deep(.mss__baseinput) {
  flex: 0 1 14rem;
  width: auto;
}

.mss__basehint {
  flex-basis: 100%;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-warning));
}

.msp__pill {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  flex-shrink: 0;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
}

/* The hand-made mark: the dashed pill the card's cover badge also wears. */
.mss__actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-left: auto;
}

.mss__mark {
  flex-shrink: 0;
  padding: 0 var(--space-2);
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  font-size: var(--text-2xs);
  line-height: var(--tag-h-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.mss__slots {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-4);
}

/* Label column and tiles, the design's two-column slot row. */
.mss__slot {
  display: grid;
  grid-template-columns: minmax(96px, 12%) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.mss__label {
  display: flex;
  flex-direction: column;
  padding-top: var(--space-2);
}

.mss__slotname {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.mss__hint {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.mss__tiles {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.mss__tile {
  display: inline-flex;
  max-width: 100%;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  cursor: pointer;
}

.mss__tile:hover {
  background: var(--hover-wash);
}

.mss__tile--on {
  background: var(--active-wash);
  box-shadow: var(--selection-ring);
}

.mss__cell {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  min-height: var(--control-h);
  padding: 0 var(--space-1) 0 var(--space-2);
}

.mss__tilename {
  overflow: hidden;
  font-size: var(--text-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Kept by hash, file gone: dashed and struck, never hidden. */
.mss__tile--gone {
  border-style: dashed;
  background: transparent;
}

.mss__tile--gone .mss__tilename {
  text-decoration: line-through;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.mss__gone {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgb(var(--v-theme-surface-warning));
}

.mss__coming {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.mss__tile--add {
  border-style: dashed;
  background: transparent;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  font-size: var(--text-sm);
}

.mss__tile--add .mss__cell {
  padding: 0 var(--space-3);
}

.mss__remove {
  flex-shrink: 0;
}

.mss__follow > [role="gridcell"] {
  align-items: center;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

/* The template offer strip (#1999): dashed like the ghosts it announces. */
.mss__offer {
  margin: var(--space-4) var(--space-4) 0;
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  cursor: default;
}

.mss__offer--cur,
.mss__offer:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
  box-shadow: var(--focus-glow);
}

.mss__offercell {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-2) var(--space-2) var(--space-3);
  font-size: var(--text-sm);
}

.mss__offertext {
  flex: 1;
  min-width: 0;
}

.mss__offernote {
  display: block;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.mss__dl {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
}

/* Where a member came from, when it is not simply the pictures: added by the
   owner, or new from the pictures. Small text takes primary, not the amber the
   card's dot wears (design-tokens.css, Badge). */
.mss__origin {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.mss__origin--new {
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-primary));
}

/* A ghost: a model the owner removed (#1972). Dashed like a ＋ tile, full ink,
   and never struck through - struck is "Not on shelf", which this is not. */
.mss__tile--ghost {
  border-style: dashed;
  background: transparent;
  cursor: default;
}

.mss__ghostmark {
  opacity: var(--opacity-disabled);
}

/* The precision the name to its left has lost: what tells a set's BF16 file
   from the FP8 one it is offered. Mono, as the shelf row's quant chip is; no
   border, since the tile already carries a bordered base-model pill. */
.mss__quant {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.mss__ghostcount {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
</style>
