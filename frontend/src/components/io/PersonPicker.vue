<template>
  <!-- Fixed: the popup was opened ON this person, so there is nothing to pick
       and the tile is a statement, not a control. -->
  <div v-if="fixed" class="personpick" role="group" :aria-label="ariaLabel || undefined">
    <span
      v-for="o in options"
      :key="String(o.id)"
      class="persontile persontile--on"
      data-testid="person-fixed"
    >
      <span class="persontile__face">
        <img v-if="faceOf(o)" :src="faceOf(o)" alt="" @error="broken.add(o.id)" />
        <v-icon v-else aria-hidden="true">mdi-account-outline</v-icon>
      </span>
      <span class="persontile__name">{{ o.label }}</span>
    </span>
  </div>
  <div
    v-else
    role="radiogroup"
    class="personpick"
    :aria-label="ariaLabel || undefined"
    @keydown="onKeydown"
  >
    <button
      v-for="o in options"
      :key="String(o.id)"
      type="button"
      role="radio"
      class="persontile"
      :class="{ 'persontile--on': o.id === modelValue }"
      :aria-checked="o.id === modelValue ? 'true' : 'false'"
      :tabindex="o.id === tabStop ? 0 : -1"
      :disabled="disabled"
      :data-person="String(o.id)"
      @click="select(o)"
    >
      <span class="persontile__face">
        <img
          v-if="faceOf(o)"
          :src="faceOf(o)"
          alt=""
          loading="lazy"
          @error="broken.add(o.id)"
        />
        <v-icon v-else aria-hidden="true">{{
          o.id === NO_ONE ? "mdi-account-off-outline" : "mdi-account-outline"
        }}</v-icon>
      </span>
      <span class="persontile__name">{{ o.label }}</span>
    </button>
  </div>
</template>

<script setup>
/**
 * Pick the person a run is of, by face: one tile per person, and "No one".
 *
 * A radiogroup like OptionRows, and it follows that component's rule: NO
 * FILL. The chosen tile is an olive ring round the face and a medium-weight
 * name; hover is the ink wash, which follows the pointer and not the value.
 */
import { computed, reactive } from "vue";
import { VIcon } from "vuetify/components";
import { characterThumbnailUrl } from "../../api/characters";
import { arrowStep, tabStopId } from "../../utils/radioGroup.js";

/** The id of the "No one" tile: no person is a choice, not an absence. */
const NO_ONE = null;

const props = defineProps({
  /** `[{ id, name }]`: the people offered, in the order to draw them. */
  people: { type: Array, required: true },
  /** The chosen person's id, or null for no one. */
  modelValue: { type: [Number, String, null], default: null },
  /** Draw the people as given, chosen, with nothing to press. */
  fixed: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  ariaLabel: { type: String, default: "" },
});

const emit = defineEmits(["update:modelValue"]);

const options = computed(() => [
  ...(props.fixed ? [] : [{ id: NO_ONE, label: "No one" }]),
  ...props.people.map((person) => ({ id: person.id, label: person.name })),
]);

/** People whose thumbnail would not load: they keep their tile, with a glyph. */
const broken = reactive(new Set());

function faceOf(option) {
  if (option.id === NO_ONE || broken.has(option.id)) return "";
  return characterThumbnailUrl(option.id);
}

const tabStop = computed(() => tabStopId(options.value, props.modelValue));

function select(option) {
  if (props.disabled) return;
  if (option.id !== props.modelValue) emit("update:modelValue", option.id);
}

function onKeydown(event) {
  if (props.disabled) return;
  const id = arrowStep(event, options.value, props.modelValue);
  if (id !== undefined && id !== props.modelValue) {
    emit("update:modelValue", id);
  }
}
</script>

<style scoped>
.personpick {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.persontile {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  /* One face and its ring, plus the tile's own padding either side. */
  width: calc(var(--space-9) + var(--space-3));
  padding: var(--space-3) var(--space-2);
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-xs);
  font-weight: var(--weight-regular);
}

button.persontile {
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

button.persontile:not(:disabled):hover {
  background: var(--hover-wash);
}

button.persontile:disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

/* Weight is the second cue, so the answer survives without colour. */
.persontile--on {
  font-weight: var(--weight-medium);
}

.persontile__face {
  display: grid;
  place-items: center;
  width: var(--space-8);
  height: var(--space-8);
  border-radius: var(--radius-pill);
  overflow: hidden;
  background: var(--hover-wash);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  /* Room for the ring, held whether or not it is drawn, so choosing a tile
     moves nothing. */
  outline: var(--focus-width) solid transparent;
  outline-offset: var(--space-1);
}

/* One olive mark per tile: the ring. */
.persontile--on .persontile__face {
  outline-color: var(--active-bar);
}

.persontile__face img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.persontile__name {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
