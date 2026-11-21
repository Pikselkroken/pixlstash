<template>
  <!-- Wherever the pre-flight offers it, on or off. Without `assist` (no
       settable prose prompt) nothing at all. -->
  <section v-if="assist" class="pa" aria-label="Photo look" data-testid="pa">
    <div class="pa-head">
      <button
        type="button"
        class="pa-disclose"
        :aria-expanded="String(expanded)"
        :aria-controls="bodyId"
        data-testid="pa-expand"
        @click="expanded = !expanded"
      >
        <v-icon
          class="pa-chev"
          :class="{ 'pa-chev--open': expanded }"
          size="16"
          aria-hidden="true"
          >mdi-chevron-right</v-icon
        >
        <span class="pa-title">Photo look</span>
        <span class="pa-summary" data-testid="pa-summary">{{ summary }}</span>
      </button>
      <AppBarButton
        icon="eye-outline"
        tooltip="Show full prompt"
        :active="showFull"
        :aria-pressed="String(showFull)"
        data-testid="pa-show-full"
        @click="showFull = !showFull"
      />
      <button
        type="button"
        role="switch"
        class="pa-switch"
        :aria-checked="String(model.on.value)"
        aria-label="Add photo look to the prompt"
        :disabled="disabled"
        data-testid="pa-on"
        @click="setOn(!model.on.value)"
      ></button>
      <span class="pa-onword" aria-hidden="true">{{
        model.on.value ? "On" : "Off"
      }}</span>
    </div>

    <div
      v-show="expanded"
      :id="bodyId"
      class="pa-body"
      :class="{ 'pa-body--off': !model.on.value }"
    >
      <div
        v-for="(cat, i) in model.categories.value"
        :key="cat.id"
        class="pa-row"
        :class="{ 'pa-row--odd-last': i === model.categories.value.length - 1 && i % 2 === 0 }"
      >
        <span :id="`${bodyId}-${cat.id}`" class="pa-l">
          {{ cat.label }}
          <RunResetChip
            v-if="!model.isDefault(cat.id)"
            :value="model.defaultLabel(cat.id) || 'nothing'"
            :label="cat.label"
            @reset="changed(() => model.resetRow(cat.id))"
          />
        </span>
        <div class="pa-field" :class="{ 'pa-field--own': own(cat.id) }">
          <BaseModelInput
            :ref="(el) => (fields[cat.id] = el)"
            class="pa-input"
            :model-value="drafts[cat.id] ?? model.fieldText(cat.id)"
            :options="optionsOf(cat)"
            :list-label="cat.label"
            :max-shown="40"
            open-on-focus
            own-text
            hint="Type your own, or pick a look."
            foot-action="Clear all your entries"
            placeholder="Nothing added"
            :disabled="disabled || !model.on.value"
            :aria-labelledby="`${bodyId}-${cat.id}`"
            :aria-describedby="
              own(cat.id) ? `${bodyId}-${cat.id}-own ${bodyId}-keys` : `${bodyId}-keys`
            "
            :data-testid="`pa-field-${cat.id}`"
            @update:model-value="drafts[cat.id] = $event"
            @pick="commit(cat.id, $event)"
            @confirm="commit(cat.id)"
            @blur="commit(cat.id)"
            @remove="changed(() => model.remove(cat.id, $event))"
            @foot-action="changed(() => model.clearAll(cat.id))"
          />
          <!-- A kept text of the owner's own, not a picked preset: a pencil,
               so a narrow field keeps its words. -->
          <span
            v-if="own(cat.id)"
            :id="`${bodyId}-${cat.id}-own`"
            class="pa-own"
            :data-testid="`pa-own-${cat.id}`"
          >
            <Tooltip text="Your own" activator="parent" :describe="false" />
            <v-icon size="14" aria-hidden="true">mdi-pencil-outline</v-icon>
            <span class="visually-hidden">Your own</span>
          </span>
          <!-- Pointer conveniences: the keyboard empties the text or opens the
               list with ArrowDown, so neither is a tab stop. -->
          <button
            type="button"
            tabindex="-1"
            class="pa-fbtn pa-fbtn--clear"
            :aria-label="`Clear ${cat.label}`"
            :disabled="disabled || !model.on.value"
            @mousedown.prevent="commit(cat.id, '')"
          >
            <v-icon size="14" aria-hidden="true">mdi-close</v-icon>
          </button>
          <button
            type="button"
            tabindex="-1"
            class="pa-fbtn"
            :aria-label="`Show the ${cat.label} list`"
            :disabled="disabled || !model.on.value"
            @mousedown.prevent="fields[cat.id]?.open()"
          >
            <v-icon size="16" aria-hidden="true">mdi-chevron-down</v-icon>
          </button>
        </div>
      </div>
      <span :id="`${bodyId}-keys`" class="visually-hidden"
        >With an item highlighted, Delete removes a remembered entry.</span
      >
      <div class="pa-foot">
        <span>{{ wordsLine }}</span>
        <AppButton
          v-if="model.changed.value"
          variant="ghost"
          size="sm"
          :disabled="disabled"
          data-testid="pa-reset"
          @click="changed(() => model.reset())"
          >Reset to defaults</AppButton
        >
      </div>
    </div>

    <!-- What the server will draw from: your words, then each added line,
         marked by "+" and its category in words, never by colour alone. -->
    <div
      v-if="showFull"
      class="pa-full"
      role="region"
      aria-label="Full prompt, read only"
      data-testid="pa-full"
    >
      <div class="pa-line">
        <span class="pa-mark" aria-hidden="true"></span>
        <span class="pa-tag">Your prompt</span>
        <span class="pa-text">{{ words }}</span>
      </div>
      <div
        v-for="part in parts"
        :key="`${part.category}:${part.kind || ''}`"
        class="pa-line pa-line--added"
      >
        <span class="pa-mark" aria-hidden="true">+</span>
        <span class="pa-tag"
          >{{ part.kind === "finish" ? "Look" : labelOf(part.category)
          }}<span class="visually-hidden">, line added by Photo look</span></span
        >
        <span class="pa-text">{{ part.text }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
/**
 * The prompt assistant ("Photo look"): curated camera, light and skin
 * sentences the server adds after the owner's prompt, offered for any
 * workflow with a prose prompt it can set (on by default only where the
 * server says, MiniMax H3 Image first), shared by the Run dialog and the
 * sheet step. Drawn from the server's `categories`, however many.
 *
 * Collapsed it is one line: what is added, a "Show full prompt" eye and the
 * On/Off switch, so typing a prompt and pressing Run needs nothing else.
 * Expanded, each category is a typeable field (`BaseModelInput`) offering the
 * shipped presets and the texts typed there before. Empty adds nothing.
 *
 * The host owns the state (`usePromptAssist`, passed as `model`) because its
 * request body needs it; this only draws it and says `change` whenever what
 * would be sent may have moved, so the host can ask its pre-flight again. The
 * prompt box is never written to: the full-prompt view is the pre-flight's
 * `parts` after `words`.
 */
import { computed, reactive, ref, useId } from "vue";
import { VIcon } from "vuetify/components";

import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import BaseModelInput from "../widgets/BaseModelInput.vue";
import Tooltip from "../widgets/Tooltip.vue";
import RunResetChip from "./RunResetChip.vue";

const props = defineProps({
  /** The pre-flight's `prompt.assist`, or null. */
  assist: { type: Object, default: null },
  /** `usePromptAssist(...)` for this surface. */
  model: { type: Object, required: true },
  /** The owner's own words, first in the full prompt. */
  words: { type: String, default: "" },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["change"]);

const bodyId = `pa-${useId()}`;
const expanded = ref(false);
const showFull = ref(false);
// What each field shows while it is being typed in, until it is kept.
const drafts = reactive({});
const fields = {};

function changed(act) {
  act();
  emit("change");
}

function setOn(on) {
  changed(() => props.model.setOn(on));
}

/** Keep what a field says (Enter, leaving it, or a pick from its list). */
function commit(id, text = drafts[id]) {
  delete drafts[id];
  if (text === undefined || text === props.model.fieldText(id)) return;
  changed(() => props.model.commit(id, text));
}

function optionsOf(cat) {
  return [
    ...cat.options.map((opt) => ({
      value: opt.label,
      detail: opt.text,
      section: "Presets",
    })),
    ...props.model.entries(cat.id).map((text) => ({
      value: text,
      section: "Your entries",
      removable: true,
    })),
  ];
}

/** The row holds the owner's own kept text (not while it is being typed). */
const own = (id) => drafts[id] === undefined && Boolean(props.model.current(id).text);

const labelOf = (id) =>
  props.model.categories.value.find((cat) => cat.id === id)?.label || id;

const parts = computed(() => (props.model.on.value ? props.assist?.parts || [] : []));

const summary = computed(() => {
  if (!props.model.on.value) {
    // "camera, light and skin": each category's first word.
    const words = props.model.categories.value.map((cat) =>
      cat.label.split(" ")[0].toLowerCase(),
    );
    const list =
      words.length > 1 ? `${words.slice(0, -1).join(", ")} and ${words.at(-1)}` : words[0];
    return `Off. Turn on to add ${list} lines.`;
  }
  const shown = props.model.categories.value
    .map((cat) => props.model.fieldText(cat.id))
    .filter(Boolean);
  return shown.length ? shown.join(" · ") : "Nothing added";
});

const wordsLine = computed(() => {
  const count = parts.value
    .map((part) => part.text.split(/\s+/).filter(Boolean).length)
    .reduce((a, b) => a + b, 0);
  return count
    ? `Adds ${count} words after your prompt.`
    : "Nothing added to your prompt.";
});
</script>

<style scoped>
.pa {
  container-type: inline-size;
  background: var(--group-surface);
  border: 1px solid var(--group-border);
  border-radius: var(--radius-md);
}

.pa-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
}

.pa-disclose {
  flex: 1;
  min-width: 0;
  min-height: var(--control-h-bar);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  text-align: left;
  color: rgb(var(--v-theme-on-surface));
}

.pa-disclose:hover {
  background: var(--hover-wash);
}

.pa-chev {
  flex: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  transition: transform var(--dur-2) var(--ease-standard);
}

.pa-chev--open {
  transform: rotate(90deg);
}

.pa-title {
  flex: none;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.pa-summary {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The Workflow tab's switch, on tokens. The word beside it and the thumb's
   side carry the state as well as the olive. */
.pa-switch {
  position: relative;
  flex: none;
  width: var(--space-7);
  height: var(--gutter-glyph);
  padding: 0;
  border: 0;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-input-background));
  box-shadow: inset 0 0 0 1px rgb(var(--v-theme-border));
  cursor: pointer;
}

.pa-switch::after {
  content: "";
  position: absolute;
  top: var(--space-1);
  left: var(--space-1);
  width: var(--space-4);
  height: var(--space-4);
  border-radius: 50%;
  background: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  transition: left var(--dur-1) var(--ease-standard);
}

.pa-switch[aria-checked="true"] {
  background: rgb(var(--v-theme-primary));
  box-shadow: none;
}

.pa-switch[aria-checked="true"]::after {
  left: calc(var(--space-7) - var(--space-4) - var(--space-1));
  background: rgb(var(--v-theme-on-primary));
}

.pa-switch:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}

.pa-onword {
  min-width: 3ch;
  font-size: var(--text-sm);
}

.pa-body {
  display: grid;
  gap: var(--space-4);
  padding: 0 var(--space-4) var(--space-4);
}

/* However many categories the server sends: two columns, a lone last one
   spanning both; three where there is room. */
@container (min-width: 400px) {
  .pa-body {
    grid-template-columns: 1fr 1fr;
  }

  .pa-row--odd-last {
    grid-column: 1 / -1;
  }
}

@container (min-width: 600px) {
  .pa-body {
    grid-template-columns: repeat(3, 1fr);
  }

  .pa-row--odd-last {
    grid-column: auto;
  }
}

.pa-body--off .pa-row {
  opacity: var(--opacity-disabled);
}

.pa-row {
  display: grid;
  gap: var(--space-2);
  min-width: 0;
}

.pa-l {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--space-5);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.pa-field {
  position: relative;
}

/* `:deep()`: BaseModelInput has two roots. RecentInput's box, with room on
   the right for the clear and chevron buttons. */
.pa-field :deep(.pa-input) {
  display: block;
  width: 100%;
  height: var(--control-h);
  padding: 0 calc(2 * var(--control-h-sm) + var(--space-2)) 0 var(--space-3);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  outline: none;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
}

.pa-field :deep(.pa-input:focus-visible) {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
  box-shadow: var(--focus-glow);
}

/* "Nothing added" is a state, not a hint: read at the text floor. */
.pa-field :deep(.pa-input::placeholder) {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* Room for the pencil as well as the two buttons. */
.pa-field--own :deep(.pa-input) {
  padding-right: calc(3 * var(--control-h-sm) + var(--space-2));
}

.pa-own {
  position: absolute;
  top: 50%;
  right: calc(2 * var(--control-h-sm) + var(--space-1));
  transform: translateY(-50%);
  display: inline-grid;
  place-items: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.pa-fbtn {
  position: absolute;
  top: 50%;
  right: var(--space-1);
  transform: translateY(-50%);
  display: inline-grid;
  place-items: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.pa-fbtn:hover:not(:disabled) {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.pa-fbtn--clear {
  right: calc(var(--control-h-sm) + var(--space-1));
  visibility: hidden;
}

.pa-field:hover .pa-fbtn--clear,
.pa-field:focus-within .pa-fbtn--clear {
  visibility: visible;
}

@media (hover: none), (pointer: coarse) {
  .pa-fbtn--clear {
    visibility: visible;
  }
}

.pa-foot {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--group-border);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.pa-full {
  display: grid;
  gap: var(--space-2);
  margin: 0 var(--space-4) var(--space-4);
  padding: var(--space-3);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid var(--group-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.pa-line {
  display: grid;
  grid-template-columns: var(--gutter-glyph) 6.5rem minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-1) var(--space-2);
  border-left: var(--rail-w) solid transparent;
  overflow-wrap: anywhere;
}

/* Four cues, none of them a hue: the "+", the word, the rail and the wash. */
.pa-line--added {
  border-left-color: rgba(var(--v-theme-on-surface), 0.55);
  background: var(--group-surface);
}

.pa-mark {
  font-family: var(--font-mono);
  font-weight: var(--weight-semibold);
}

.pa-tag {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
}

@container (max-width: 399px) {
  /* Narrow: the summary drops under the title, two lines at most. */
  .pa-disclose {
    flex-wrap: wrap;
    row-gap: 0;
  }

  .pa-summary {
    flex-basis: 100%;
    padding-left: calc(var(--gutter-glyph) + var(--space-3));
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .pa-line {
    grid-template-columns: var(--gutter-glyph) minmax(0, 1fr);
  }

  .pa-text {
    grid-column: 2;
  }
}
</style>
