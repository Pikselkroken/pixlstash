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
        :aria-label="`${count} ${count === 1 ? 'LoRA changes' : 'LoRAs change'} per picture. On top: ${label(top)}`"
        data-testid="wftab-pile"
      >
        <span class="wfpile-back" aria-hidden="true"></span>
        <span class="wfpile-back wfpile-back--far" aria-hidden="true"></span>
        <span class="wfpile-top">
          <span class="wfpile-name">{{ label(top) }}</span>
          <span v-if="rest" class="wfpile-more">+{{ rest }}</span>
        </span>
      </button>
    </template>

    <div
      class="tbm wfpile-fan"
      role="dialog"
      aria-label="LoRAs that change per picture"
      data-testid="wftab-fan"
    >
      <div class="wfpile-head">
        <span class="wfpile-title">Changes per picture</span>
        <span class="wfpile-quiet"
          >{{ count }} {{ count === 1 ? "LoRA" : "LoRAs" }} across
          {{ summary.pictures }}
          {{ summary.pictures === 1 ? "picture" : "pictures" }}</span
        >
      </div>
      <ul class="wfpile-rows">
        <li
          v-for="row in rows"
          :key="row.asset || 'none'"
          class="wfpile-row-wrap"
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
            <AppButton
              v-if="row.asset && row.pictures"
              variant="ghost"
              size="sm"
              :aria-label="`Show the ${row.pictures} ${row.pictures === 1 ? 'picture' : 'pictures'} made with ${label(row)}`"
              @click="show(row)"
            >
              Show {{ row.pictures }}
            </AppButton>
            <template v-if="row.asset">
              <AppButton
                v-if="row.promoted"
                variant="ghost"
                size="sm"
                :disabled="busy"
                :aria-label="`Put ${label(row)} back in the pile`"
                @click="confirming = row.asset"
              >
                Put back
              </AppButton>
              <AppButton
                v-else
                variant="ghost"
                size="sm"
                icon-left="arrow-up-bold-outline"
                :disabled="busy"
                :aria-label="`Promote ${label(row)} to a workflow of its own`"
                @click="confirming = row.asset"
              >
                Promote
              </AppButton>
            </template>
          </div>
          <p v-if="row.promoted" class="wfpile-chip-line">
            <span class="wfpile-chip">Promoted</span>
            Its pictures are a workflow of their own in this stack.
          </p>

          <!-- Nothing is written before this is answered: promoting moves
               pictures to another workflow, and saying which is the point. -->
          <div
            v-if="confirming === row.asset"
            class="wfpile-confirm"
            role="group"
            :aria-label="row.promoted ? 'Put back' : 'Promote'"
          >
            <template v-if="row.promoted">
              <p class="wfpile-confirm-title">
                Put {{ label(row) }} back in the pile?
              </p>
              <p class="wfpile-quiet">
                Its {{ pictures(row.pictures) }} go back to the workflow they
                came from, and the workflow made for them goes away.
              </p>
            </template>
            <template v-else>
              <p class="wfpile-confirm-title">
                Promote {{ label(row) }} to a workflow?
              </p>
              <p class="wfpile-quiet">
                Its {{ pictures(row.pictures) }} become
                {{
                  row.members.length > 1
                    ? `a workflow of their own beside each of the ${row.members.length} they were made with`
                    : "a workflow of their own in this stack"
                }},
                <i>{{ workflowName }} + {{ label(row) }}</i>. That workflow always
                loads it, and exports with it. The other
                {{ pictures(summary.pictures - row.pictures) }} stay where they
                are.
              </p>
            </template>
            <div class="wfpile-confirm-actions">
              <AppButton size="sm" @click="confirming = ''">Cancel</AppButton>
              <AppButton
                variant="primary"
                size="sm"
                :disabled="busy"
                data-testid="wftab-fan-confirm"
                @click="answer(row)"
              >
                {{ row.promoted ? "Put back" : "Promote" }}
              </AppButton>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </v-menu>
</template>

<script setup>
// The Workflow tab's pile of LoRAs that change per picture, and the fan it
// opens into (the "Shared LoRAs and a pile" design). It describes the whole
// stack, not the member picked in the rail's header: `summary` is
// `GET /workflows/{key}/lora-summary`.
//
// The one verb here is Promote: a LoRA's pictures become a workflow of their
// own inside the stack, which always loads it and exports with it. The same
// row puts it back. Both are confirmed in place, because both move pictures
// between workflows.

import { computed, ref, watch } from "vue";
import { VMenu } from "vuetify/components";

import { pictureThumbnailUrl } from "../../api/pictures";
import AppButton from "../widgets/AppButton.vue";

const props = defineProps({
  /** `GET …/lora-summary`: `{keys, pictures, varying, without, cover_asset}`. */
  summary: { type: Object, required: true },
  /** `{key: name}` for the stack's members, for "only in …". */
  memberNames: { type: Object, default: () => ({}) },
  /** The workflow a promotion is named after. */
  workflowName: { type: String, default: "" },
  /** A write is out, so neither verb may start another. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["promote", "show"]);

const open = ref(false);
/** The row whose confirmation is showing, by asset, or "". */
const confirming = ref("");

watch(open, (now) => {
  if (!now) confirming.value = "";
});
// A write re-reads the summary; whatever was being confirmed has been answered.
watch(
  () => props.summary,
  () => {
    confirming.value = "";
  },
);

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

function pictures(n) {
  return `${n} ${n === 1 ? "picture" : "pictures"}`;
}

/** Where in the stack a LoRA was used, in words. */
function where(row) {
  const keys = props.summary?.keys ?? [];
  const members = row.members ?? [];
  if (keys.length < 2 || !members.length) return "";
  if (members.length === keys.length) {
    return keys.length === 2 ? "both workflows" : `all ${keys.length} workflows`;
  }
  if (members.length === 1) {
    if (row.promoted) return "its own workflow here";
    return `only in ${props.memberNames[members[0]] || "one workflow"}`;
  }
  return `in ${members.length} of ${keys.length} workflows`;
}

function meta(row) {
  return [
    pictures(row.pictures),
    where(row),
    row.asset && row.asset === props.summary?.cover_asset ? "cover" : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function show(row) {
  open.value = false;
  emit("show", row);
}

function answer(row) {
  emit("promote", row, !row.promoted);
}
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
  z-index: 2;
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

.wfpile:focus-visible .wfpile-top {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.wfpile-back {
  position: absolute;
  z-index: 1;
  left: var(--space-2);
  right: var(--space-2);
  bottom: var(--space-1);
  height: var(--control-h);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.wfpile-back--far {
  z-index: 0;
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

.wfpile-chip-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfpile-chip {
  display: inline-flex;
  align-items: center;
  height: var(--control-h-sm);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-pill);
  color: rgb(var(--v-theme-on-surface));
}

.wfpile-confirm {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

.wfpile-confirm p {
  margin: 0;
}

.wfpile-confirm-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.wfpile-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
  margin-top: var(--space-2);
}
</style>
