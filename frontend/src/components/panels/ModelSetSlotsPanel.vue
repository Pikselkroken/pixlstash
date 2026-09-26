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
          <span class="mss__mark">Grouped by you</span>
          <span class="msp__count num">{{ facts }}</span>
          <span class="msp__spacer"></span>
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
            icon-left="image-multiple-outline"
            :disabled="!canFillFromPictures"
            :tooltip="
              canFillFromPictures
                ? 'Add models your pictures used with this checkpoint'
                : fillFromPicturesRefusal
            "
            @click="
              emit('fill', { mode: 'pictures', el: $event.currentTarget })
            "
            >Fill from pictures…</AppButton
          >
          <AppButton
            variant="ghost"
            size="sm"
            icon-left="close"
            icon-only
            tooltip="Close this set"
            @click="emit('close')"
          />
        </div>
      </div>
    </div>

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

    <!-- The merge offer (#1523): lasting, until the owner merges or keeps the
         set separate. A cursor stop ahead of the slots, so Enter merges. -->
    <div
      v-if="set.offer"
      :id="`${panelId}-row-offer`"
      class="mss__offer msp__member"
      :class="{ 'mss__offer--cur': cursorKey === 'offer' }"
      role="row"
      aria-level="2"
      :aria-label="`${offerLead}. ${offerNote} Enter merges.`"
      :tabindex="cursorKey === 'offer' ? 0 : -1"
      data-key="offer"
      data-testid="merge-offer-strip"
      @click="emit('cursor', 'offer')"
    >
      <div class="mss__offercell" role="gridcell">
        <span
          v-if="set.offer.covers.length"
          class="mss__mosaic"
          :class="`mss__mosaic--${set.offer.covers.length}`"
          aria-hidden="true"
        >
          <img
            v-for="cover in set.offer.covers"
            :key="cover.picture_id"
            :src="pictureThumbnailUrl(cover.picture_id, { version: cover.version })"
            alt=""
            loading="lazy"
          />
        </span>
        <span class="mss__offertext" aria-hidden="true"
          ><strong>{{ offerLead }}</strong>
          <span class="mss__offernote">{{ offerNote }}</span></span
        >
        <AppButton
          variant="ghost"
          size="sm"
          tabindex="-1"
          tooltip="Keep this set separate from those pictures. The set's menu can offer the merge again."
          @click.stop="emit('keep-separate')"
          >Keep separate</AppButton
        >
        <AppButton
          variant="primary"
          size="sm"
          icon-left="table-arrow-down"
          tabindex="-1"
          @click.stop="emit('merge')"
          >Merge, add {{ set.offer.models.length }}</AppButton
        >
      </div>
    </div>
    <div
      v-else-if="set.declined?.length"
      class="msp__note mss__kept"
      role="row"
      aria-level="2"
    >
      <div role="gridcell">
        <span>{{
          set.kept_separate
            ? `Kept separate from ${pictureCount(set.kept_separate)}.`
            : "Some models are kept out of this set's merge offer."
        }}</span>
        <AppButton variant="ghost" size="sm" @click="emit('offer-again')"
          >Offer again</AppButton
        >
      </div>
    </div>

    <div class="mss__slots" role="presentation">
      <div
        v-for="{ slot, items } in slots"
        :key="slot.id"
        class="mss__slot"
        role="presentation"
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
                'mss__tile--gone': !item.member.on_shelf,
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
                <span class="mss__tilename" aria-hidden="true">{{
                  item.member.name
                }}</span>
                <span
                  v-if="!item.member.on_shelf"
                  class="mss__gone"
                  aria-hidden="true"
                  >Not on shelf</span
                >
                <span
                  v-else-if="item.member.base_model"
                  class="msp__pill"
                  aria-hidden="true"
                  >{{ item.member.base_model }}</span
                >
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
            <!-- A model the merge would add, drawn where it would land. -->
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
                <span class="mss__ghostcount num" aria-hidden="true">{{
                  ghostCount(item.ghost)
                }}</span>
                <AppButton
                  variant="outline"
                  size="sm"
                  tabindex="-1"
                  :tooltip="`Add ${item.ghost.name} to this set`"
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
 * Every set has the same five slots - Checkpoint (exactly one), Text encoders,
 * VAE, LoRAs, Other - and each ends in a dashed ＋ tile that opens the chooser.
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

import { pictureThumbnailUrl } from "../../api/pictures";
import {
  pictureCount,
  recipeCount,
  setSlots,
} from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";
import BaseModelInput from "../widgets/BaseModelInput.vue";
import ModelMark from "../widgets/ModelMark.vue";

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
  canFillFromPictures: { type: Boolean, default: false },
  /**
   * `{key, name, guess}` while the one-time base-model offer is up, else null.
   * `key` names the offer (set and checkpoint), so a refetch is not a new one.
   */
  baseOffer: { type: Object, default: null },
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
  // The merge offer (#1523): the strip's two verbs, one ghost's Add, the kept
  // line's Offer again, and a click that only moves the cursor.
  "merge",
  "keep-separate",
  "add-ghost",
  "offer-again",
  "cursor",
]);

const slots = computed(() => setSlots(props.set));

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

const fillFromPicturesRefusal = computed(() =>
  incomplete.value
    ? "Add a checkpoint first: this reads what your pictures used with it."
    : "No picture used anything else with this checkpoint.",
);

const facts = computed(() => {
  const count = (props.set.members ?? []).length;
  const pictures = Number(props.set.picture_count) || 0;
  return [
    madeLabel(props.set.created_at),
    pictures ? pictureCount(pictures) : "no picture yet",
    count === 1 ? "1 model" : `${count} models`,
  ]
    .filter(Boolean)
    .join(" · ");
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

/** The strip's first line: which pictures, of which file. */
const offerLead = computed(() => {
  const offer = props.set.offer;
  if (!offer) return "";
  const who = offer.picture_count
    ? `${pictureCount(offer.picture_count)} more`
    : recipeCount(offer.recipes);
  const n = offer.models.length;
  return `${who} of ${offer.head_name} used the ${n === 1 ? "dashed model" : `${n} dashed models`} below`;
});

const offerNote = computed(() => {
  const n = props.set.offer?.models.length ?? 0;
  return `Adding ${n === 1 ? "it" : `all ${n}`} brings them into this set.`;
});

/** "812 pictures", or the recipes when none of its pictures is here. */
function ghostCount(ghost) {
  return ghost.picture_count
    ? pictureCount(ghost.picture_count)
    : recipeCount(ghost.recipes);
}

function ghostName(ghost, slot) {
  return `${ghost.name}, ${slot.label}, offered: used in ${ghostCount(ghost)}. Enter adds it, Delete keeps it out.`;
}

function tileName(member, slot) {
  return [
    member.name,
    slot.label,
    member.on_shelf ? member.base_model : "not on shelf, kept by its hash",
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

.msp__spacer {
  flex: 1;
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

.mss__baseinput {
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

/* The merge offer strip: dashed like the ghosts it announces. */
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

/* The pictures' set's own covers, the card mosaic in miniature. */
.mss__mosaic {
  display: grid;
  flex: none;
  gap: 1px;
  width: var(--bar-height);
  height: var(--control-h-bar);
  overflow: hidden;
  border-radius: var(--radius-sm);
}

.mss__mosaic--2 {
  grid-template-columns: 1fr 1fr;
}

.mss__mosaic--3 {
  grid-template-columns: 2fr 1fr;
  grid-template-rows: 1fr 1fr;
}

.mss__mosaic--3 img:first-child {
  grid-row: 1 / 3;
}

.mss__mosaic img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.mss__kept > [role="gridcell"] {
  align-items: center;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

/* A ghost: a model the merge would add. Dashed like a ＋ tile, full ink, and
   never struck through - struck is "Not on shelf", which this is not. */
.mss__tile--ghost {
  border-style: dashed;
  background: transparent;
  cursor: default;
}

.mss__ghostmark {
  opacity: var(--opacity-disabled);
}

.mss__ghostcount {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
</style>
