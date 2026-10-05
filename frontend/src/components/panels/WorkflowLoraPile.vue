<template>
  <!-- The LoRAs that change per picture, as one element: the cover's LoRA
       on top, "+N" for the rest and cards layered behind it, so the rail row
       is one line however many there are. Opening it fans the pile out over
       the grid, where each LoRA has room for a strip of its own pictures. -->
  <v-menu
    v-model="open"
    location="start"
    origin="end"
    :offset="12"
    :close-on-content-click="false"
  >
    <template #activator="{ props: menuProps }">
      <button
        v-bind="menuProps"
        type="button"
        class="wfpile"
        :class="{ 'wfpile--one': rest === 0, 'wfpile--two': rest === 1 }"
        :aria-expanded="open"
        :aria-label="`${count} ${count === 1 ? 'LoRA' : 'LoRAs'} also used, not in the default recipe. On top: ${label(top)}`"
        data-testid="wftab-pile"
      >
        <!-- Back to front: the cards stack by DOM order, not z-index. -->
        <span class="wfpile-back wfpile-back--far" aria-hidden="true"></span>
        <span class="wfpile-back" aria-hidden="true"></span>
        <span class="wfpile-top">
          <span class="wfpile-name">{{ label(top) }}</span>
          <span v-if="rest" class="wfpile-more">+{{ rest }}</span>
        </span>
      </button>
    </template>

    <div
      ref="fan"
      class="tbm wfpile-fan"
      tabindex="-1"
      role="dialog"
      aria-label="LoRAs also used, not in the default recipe"
      data-testid="wftab-fan"
    >
      <div class="wfpile-head">
        <span class="wfpile-title">Also used</span>
        <span class="wfpile-quiet"
          >{{ count }} {{ count === 1 ? "LoRA" : "LoRAs" }} across
          {{ pictureCount(summary.pictures) }}</span
        >
      </div>
      <ul class="wfpile-rows">
        <li
          v-for="row in rows"
          :key="row.asset || 'none'"
          class="wfpile-row-wrap"
          :class="{ 'wfpile-row-wrap--none': !row.asset }"
          :data-testid="`wftab-fan-row-${row.asset || 'none'}`"
        >
          <div class="wfpile-row">
            <div class="wfpile-strip" aria-hidden="true">
              <img
                v-for="id in row.picture_ids"
                :key="id"
                :src="pictureThumbnailUrl(id)"
                alt=""
                loading="lazy"
              />
            </div>
            <div class="wfpile-text">
              <div
                class="wfpile-lname"
                :class="{ 'wfpile-quiet': !row.asset || !row.name }"
              >
                {{ label(row) }}
              </div>
              <div class="wfpile-meta">{{ meta(row) }}</div>
            </div>
            <!-- Only a LoRA the shelf names as one file (`sha256`): the default
                 recipe names a LoRA by that digest, and the route takes
                 exactly the LoRAs the summary gives one. -->
            <AppButton
              v-if="row.asset && row.sha256"
              variant="ghost"
              size="sm"
              data-testid="wftab-add-default"
              :loading="adding === row.asset"
              :disabled="Boolean(adding) && adding !== row.asset"
              :aria-label="`Add ${label(row)} to the default recipe`"
              tooltip="Add to the workflow's default LoRAs"
              @click="addDefault(row)"
            >
              Add to default
            </AppButton>
            <AppButton
              v-if="row.asset && row.pictures"
              variant="ghost"
              size="sm"
              :aria-label="`Show the ${pictureCount(row.pictures)} made with ${label(row)}`"
              @click="show(row)"
            >
              Show {{ row.pictures }}
            </AppButton>
          </div>
        </li>
      </ul>
    </div>
  </v-menu>
</template>

<script setup>
// The Workflow tab's ALSO USED pile: the LoRAs its pictures used that are not
// in the default recipe, and the fan it opens into (#1653). It describes the
// whole workflow: `summary` is `GET /workflows/{id}/lora-summary` with the
// default recipe's LoRAs already taken out of `varying` by the tab. Its one
// verb is Show: the grid, narrowed to one LoRA's pictures.

import { computed, nextTick, ref, watch } from "vue";
import { VMenu } from "vuetify/components";

import { pictureThumbnailUrl } from "../../api/pictures";
import { pictureCount } from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";

const props = defineProps({
  /** `GET …/lora-summary`: `{pictures, varying, without, cover_asset}`. */
  summary: { type: Object, required: true },
  /** The `asset` of the LoRA being added to the default recipe, while it is. */
  adding: { type: String, default: "" },
});

const emit = defineEmits(["show", "add-default"]);

const fan = ref(null);

const open = ref(false);

const varying = computed(() => props.summary?.varying ?? []);
const count = computed(() => varying.value.length);

/** The cover's LoRA on top, as the design stacks it; the most used otherwise. */
const top = computed(
  () =>
    varying.value.find((row) => row.asset === props.summary?.cover_asset) ??
    varying.value[0] ??
    null,
);
const rest = computed(() => Math.max(count.value - 1, 0));

/** The fan's rows: the top of the pile first, then the rest, then "No LoRA". */
const rows = computed(() => [
  ...(top.value ? [top.value] : []),
  ...varying.value.filter((row) => row !== top.value),
  ...(props.summary?.without ? [props.summary.without] : []),
]);

function label(row) {
  if (!row) return "";
  if (!row.asset) return "No LoRA";
  return row.name || "A LoRA whose name was forgotten";
}

function meta(row) {
  return [
    pictureCount(row.pictures),
    row.asset && row.asset === props.summary?.cover_asset ? "cover" : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function show(row) {
  open.value = false;
  emit("show", row);
}

/** Where the row being added stood, so focus can stay there when it leaves. */
let addedAt = -1;
let addedAsset = "";

function addDefault(row) {
  addedAt = rows.value.indexOf(row);
  addedAsset = row.asset;
  emit("add-default", row);
}

// A failed add leaves its row where it was: forget it, or a later change that
// takes that row out for another reason would move focus unasked.
watch(
  () => props.adding,
  (now, before) => {
    if (before && before === addedAsset && !now) {
      nextTick(() => {
        if (rows.value.some((row) => row.asset === addedAsset)) addedAsset = "";
      });
    }
  },
);

// An added LoRA leaves the pile, and its button with it: focus moves to the
// row that took its place (its first button), else the fan itself, rather
// than falling to the page.
watch(rows, async (now) => {
  if (!addedAsset || now.some((row) => row.asset === addedAsset)) return;
  const index = Math.min(addedAt, now.length - 1);
  addedAsset = "";
  await nextTick();
  const next = fan.value?.querySelectorAll(".wfpile-row-wrap")[index];
  (next?.querySelector("button") ?? fan.value)?.focus();
});
</script>

<style scoped>
/* The pile: one value field, with two edges of cards peeking out below it so
   it reads as more than one. */
.wfpile {
  position: relative;
  display: block;
  width: 100%;
  min-width: 0;
  padding: 0 0 var(--space-2);
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.wfpile-top {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-h);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-surface));
  font-size: var(--text-sm);
}

.wfpile:hover .wfpile-top {
  border-color: rgb(var(--v-theme-border));
}

.wfpile:focus-visible {
  outline: none;
}

/* The global ink ring, on the top card rather than the button's box (which
   includes the cards peeking out below it). */
.wfpile:focus-visible .wfpile-top {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
}

.wfpile-back {
  position: absolute;
  left: var(--space-2);
  right: var(--space-2);
  bottom: var(--space-1);
  height: var(--control-h);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.wfpile-back--far {
  left: var(--space-3);
  right: var(--space-3);
  bottom: 0;
}

.wfpile--one .wfpile-back,
.wfpile--two .wfpile-back--far {
  display: none;
}

.wfpile-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wfpile-more {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The fan, over the grid: the rail is too narrow for picture strips. */
.wfpile-fan {
  width: min(440px, calc(100vw - 2 * var(--space-5)));
  max-height: 70vh;
  overflow-y: auto;
}

.wfpile-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5) var(--space-3);
}

.wfpile-title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.wfpile-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfpile-head .wfpile-quiet,
.wfpile-meta {
  font-size: var(--text-xs);
}

.wfpile-rows {
  margin: 0;
  padding: 0 0 var(--space-3);
  list-style: none;
}

.wfpile-row-wrap {
  padding: var(--space-3) var(--space-5);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

/* "No LoRA" is not one of the LoRAs, so it stands apart below them rather
   than reading as the next row of a list that ran out of room. */
.wfpile-row-wrap--none {
  margin-top: var(--space-4);
  border-top-color: rgb(var(--v-theme-border));
}

.wfpile-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.wfpile-strip {
  display: flex;
  flex-shrink: 0;
  gap: var(--space-1);
  width: calc(3 * var(--control-h-bar) + 2 * var(--space-1));
}

.wfpile-strip img {
  width: var(--control-h-bar);
  height: var(--control-h-bar);
  border-radius: var(--radius-sm);
  object-fit: cover;
}

.wfpile-text {
  flex: 1;
  min-width: 0;
}

.wfpile-lname {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

</style>
