<template>
  <!-- A popup beside the New workflow set tile, like the set chooser: one
       short job. v-menu returns focus to nothing we can rely on (the grid
       opens it programmatically), so `close` carries focus back through the
       grid instead. -->
  <v-menu
    :model-value="open"
    :target="target"
    :close-on-content-click="false"
    location="bottom start"
    origin="top start"
    :offset="4"
    @update:model-value="(value) => !value && emit('close')"
  >
    <div
      class="tbm wtp"
      role="dialog"
      aria-label="New workflow set"
      data-testid="workflow-template-picker"
      @keydown.esc.stop.prevent="emit('close')"
    >
      <div class="wtp__search">
        <div class="tbm-input-wrap">
          <v-icon size="16" class="tbm-input-icon">mdi-magnify</v-icon>
          <input
            ref="fieldRef"
            v-model="query"
            class="tbm-input tbm-input--with-icon"
            placeholder="Search templates, tags and file names…"
            aria-label="Search templates, tags and file names"
            autocomplete="off"
            role="combobox"
            aria-expanded="true"
            :aria-controls="listId"
            :aria-activedescendant="cursorOptionId"
            @input="cursor = null"
            @keydown="onKeydown"
          />
        </div>
        <!-- The next stop after the search: the empty set, what the tile made
             before it opened a picker. -->
        <AppButton
          variant="secondary"
          icon-left="plus"
          data-testid="make-your-own"
          @click="emit('make-own')"
          >Make your own</AppButton
        >
      </div>
      <!-- After the search in the DOM, so Tab starts there; drawn first. -->
      <div class="wtp__hd">
        <span class="wtp__title">New workflow set</span>
        <AppButton
          variant="ghost"
          size="sm"
          icon-only
          icon-left="close"
          aria-label="Close"
          @click="emit('close')"
        />
      </div>
      <Segmented
        v-model="kind"
        class="wtp__seg"
        :options="KINDS"
        aria-label="Kind"
        orientation="horizontal"
      />

      <div
        :id="listId"
        class="wtp__list"
        role="listbox"
        aria-label="Workflow templates"
      >
        <p v-if="loading && !rows.length" class="wtp__state">
          Reading the templates…
        </p>
        <p v-else-if="error" class="wtp__state" role="alert">{{ error }}</p>
        <template v-else>
          <template v-for="entry in entries" :key="entry.id">
            <div v-if="entry.kind === 'sec'" class="wtp__sec" role="presentation">
              {{ entry.text }}
            </div>
            <div
              v-else-if="entry.kind === 'div'"
              class="wtp__div"
              role="presentation"
            ></div>
            <button
              v-else-if="entry.kind === 'fold'"
              type="button"
              class="wtp__fold"
              tabindex="-1"
              :aria-expanded="entry.expanded"
              :data-testid="entry.testid"
              @mousedown.prevent
              @click="entry.press"
            >
              <v-icon size="14" aria-hidden="true">{{ entry.icon }}</v-icon>
              {{ entry.text }}
            </button>
            <div
              v-else
              :id="optionId(entry.id)"
              class="wtp__row"
              :class="{
                'wtp__row--cur': cursor === entry.id,
                'wtp__row--open': expanded.has(entry.id),
              }"
              role="option"
              aria-selected="false"
              :aria-expanded="String(expanded.has(entry.id))"
              :aria-label="`${entry.name}, ${entry.have} files on your shelf`"
              data-testid="template-row"
              :data-key="entry.id"
            >
              <button
                type="button"
                class="wtp__chev"
                tabindex="-1"
                :aria-label="`${expanded.has(entry.id) ? 'Hide' : 'Show'} the files of ${entry.name}`"
                @mousedown.prevent
                @click="toggle(entry.id)"
              >
                <v-icon size="14" aria-hidden="true">{{
                  expanded.has(entry.id) ? "mdi-chevron-down" : "mdi-chevron-right"
                }}</v-icon>
              </button>
              <ModelMark :row="entry.mark" />
              <div class="wtp__two">
                <div class="wtp__nm">{{ entry.name }}</div>
                <div class="wtp__sub">
                  {{ entry.sub
                  }}<span v-if="entry.variant.needs_comfyui" class="wtp__flag"
                    ><v-icon size="11" aria-hidden="true"
                      >mdi-alert-outline</v-icon
                    >
                    Needs ComfyUI {{ entry.variant.needs_comfyui }}<Tooltip
                      text="A set is only files, so you can make it now. Running the template's workflow needs this ComfyUI."
                      activator="parent"
                  /></span>
                </div>
              </div>
              <span class="wtp__have">
                <v-icon
                  v-if="entry.variant.complete"
                  size="13"
                  class="wtp__ok"
                  aria-hidden="true"
                  >mdi-check</v-icon
                >{{ entry.have }}
              </span>
              <AppButton
                :variant="entry.primary ? 'primary' : 'secondary'"
                size="sm"
                tabindex="-1"
                :aria-label="entry.ariaLabel"
                data-testid="template-press"
                @mousedown.prevent
                @click="press(entry)"
                >{{ entry.label }}</AppButton
              >
              <table v-if="expanded.has(entry.id)" class="wtp__files">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Slot</th>
                    <th class="wtp__num">Size</th>
                    <th><span class="visually-hidden">On your shelf</span></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="file in entry.variant.files" :key="file.name">
                    <td class="wtp__fn">{{ file.other_quant || file.name }}</td>
                    <td>{{ SLOT_LABEL[file.slot] ?? file.slot }}</td>
                    <td class="wtp__num">
                      {{ file.file_size ? formatModelSize(file.file_size) : "" }}
                    </td>
                    <td>
                      <v-icon
                        v-if="file.on_shelf"
                        size="12"
                        class="wtp__ok"
                        aria-label="On your shelf"
                        >mdi-check</v-icon
                      >
                      <a
                        v-else-if="/^https:\/\//.test(file.url)"
                        class="wtp__dl"
                        :href="file.url"
                        target="_blank"
                        rel="noopener noreferrer"
                        :aria-label="`Download ${file.name}`"
                        >Download</a
                      >
                      <template v-else>Not on your shelf</template>
                    </td>
                  </tr>
                </tbody>
              </table>
              <label
                v-if="expanded.has(entry.id) && entry.row.variants.length > 1"
                class="wtp__variant"
                >Variant
                <select
                  class="tbm-input wtp__select"
                  :value="chosen[entry.id] ?? 0"
                  @change="
                    chosen = { ...chosen, [entry.id]: Number($event.target.value) }
                  "
                >
                  <option
                    v-for="(variant, index) in entry.row.variants"
                    :key="variant.name"
                    :value="index"
                  >
                    {{ variant.title }}
                  </option>
                </select>
              </label>
            </div>
          </template>

          <!-- No match points at the way out, as the design asks. -->
          <p v-if="!shown.length" class="wtp__state">
            {{
              query.trim()
                ? `No template matches "${query.trim()}".`
                : "No template here."
            }}
            <AppButton variant="ghost" size="sm" @click="emit('make-own')"
              >Make your own</AppButton
            >
          </p>
        </template>
      </div>
      <div class="wtp__foot">
        {{ rows.length.toLocaleString() }}
        {{ rows.length === 1 ? "template" : "templates" }}
        <template v-if="version"> · ComfyUI templates {{ version }}</template>
      </div>
    </div>
  </v-menu>
</template>

<script setup>
/**
 * New workflow set's picker (#1999, #2001): ComfyUI's templates, the ones
 * whose files are all on the shelf first.
 *
 * **Three sections.** *Ready*: every file on the shelf and no set covering it,
 * newest arrival first, one press from a set. *Already in a set*, folded to one
 * line: those rows open their set rather than make a second. *Templates*: the
 * rest, most files on the shelf first, whose button makes the set from what is
 * there and says how many are missing. A row is one head file; templates that
 * share it are variants, chosen in the open row.
 *
 * **Keyboard**, the set chooser's: focus stays in the search, the arrows move
 * the cursor (the inset ring), Right opens a row and Left closes it, Enter
 * presses the cursor row's button, Escape closes. Each button is named for its
 * model. The pointer only washes a row.
 *
 * Nothing here makes anything on its own: `make`, `make-own` and `open-set`
 * are the caller's to act on.
 */
import { computed, nextTick, ref, useId, watch } from "vue";
import { VIcon } from "vuetify/components";

import { formatModelSize } from "../../utils/modelShelf";
import { templateName } from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";
import ModelMark from "../widgets/ModelMark.vue";
import Segmented from "../widgets/Segmented.vue";
import Tooltip from "../widgets/Tooltip.vue";

/** How many partial rows draw before "Show more", without a search. */
const PARTIAL_DEPTH = 30;

const KINDS = [
  { id: "all", label: "All" },
  { id: "image", label: "Image" },
  { id: "edit", label: "Edit" },
  { id: "video", label: "Video" },
  { id: "audio", label: "Audio" },
];

const KIND_LABEL = {
  image: "Image",
  edit: "Image edit",
  video: "Video",
  audio: "Audio",
  "3d": "3D",
  llm: "Text",
};

const SLOT_LABEL = {
  checkpoint: "Checkpoint",
  text_encoder: "Text encoder",
  vae: "VAE",
  lora: "LoRA",
  other: "Other",
};

const props = defineProps({
  open: { type: Boolean, default: false },
  /** What v-menu anchors to: the New tile. */
  target: { type: [Object, Array], default: undefined },
  /** `GET /models/workflow-templates` rows: `{key, variants}`. */
  rows: { type: Array, default: () => [] },
  version: { type: String, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: "" },
});

const emit = defineEmits(["close", "make", "make-own", "open-set"]);

const listId = `wtp-${useId()}`;
const fieldRef = ref(null);
const query = ref("");
const kind = ref("all");
/** The keyboard cursor, a row key; null until an arrow puts it down. */
const cursor = ref(null);
/** Open rows, by key. */
const expanded = ref(new Set());
/** The variant chosen per row, by key; the default is the first. */
const chosen = ref({});
const coveredOpen = ref(false);
const partialDepth = ref(PARTIAL_DEPTH);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    query.value = "";
    kind.value = "all";
    cursor.value = null;
    expanded.value = new Set();
    chosen.value = {};
    coveredOpen.value = false;
    partialDepth.value = PARTIAL_DEPTH;
    // The field mounts with the menu, and takes focus only once the menu's
    // entry transition lets it: a focus() before that is silently dropped,
    // so it is checked rather than assumed.
    for (let tries = 0; tries < 30 && props.open; tries += 1) {
      await nextTick();
      fieldRef.value?.focus();
      if (fieldRef.value && document.activeElement === fieldRef.value) return;
      await new Promise((resolve) => setTimeout(resolve, 16));
    }
  },
  { immediate: true },
);

function variantOf(row) {
  return row.variants[chosen.value[row.key] ?? 0] ?? row.variants[0];
}

function haystack(row) {
  return row.variants
    .flatMap((v) => [
      v.title,
      ...(v.tags ?? []),
      ...(v.models ?? []),
      ...v.files.map((f) => f.name),
    ])
    .join("\n")
    .toLowerCase();
}

/** Every row the search and the kind leave, with its chosen variant. */
const shown = computed(() => {
  const needle = query.value.trim().toLowerCase();
  return props.rows
    .filter(
      (row) =>
        (kind.value === "all" ||
          row.variants.some((v) => v.kind === kind.value)) &&
        (!needle || haystack(row).includes(needle)),
    )
    .map((row) => ({ key: row.key, row, variant: variantOf(row) }));
});

const ready = computed(() =>
  shown.value
    .filter((item) => item.variant.complete && !item.variant.covered_by)
    .sort(
      (a, b) =>
        String(b.variant.head_arrived_at ?? "").localeCompare(
          String(a.variant.head_arrived_at ?? ""),
        ) || b.variant.usage - a.variant.usage,
    ),
);

const covered = computed(() =>
  shown.value.filter((item) => item.variant.covered_by),
);

const partial = computed(() =>
  shown.value
    .filter((item) => !item.variant.complete && !item.variant.covered_by)
    .sort(
      (a, b) =>
        b.variant.on_shelf - a.variant.on_shelf ||
        b.variant.usage - a.variant.usage,
    ),
);

const partialShown = computed(() =>
  query.value.trim()
    ? partial.value
    : partial.value.slice(0, partialDepth.value),
);

const coveredLine = computed(() => {
  const first = covered.value[0]?.variant;
  if (covered.value.length === 1) {
    return `1 already in a set: ${templateName(first)}, in ${first.covered_by.name || "an untitled set"}`;
  }
  return `${covered.value.length} already in a set`;
});

/** The rows the arrows walk, in the order they are drawn. */
const walk = computed(() => [
  ...ready.value,
  ...(coveredOpen.value ? covered.value : []),
  ...partialShown.value,
]);

const cursorOptionId = computed(() =>
  cursor.value ? optionId(cursor.value) : undefined,
);

function optionId(key) {
  return `${listId}-${key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
}

function toggle(key, open = !expanded.value.has(key)) {
  const next = new Set(expanded.value);
  if (open) next.add(key);
  else next.delete(key);
  expanded.value = next;
}

/** What a row's button does, by what the row is. */
function press(item) {
  if (item.variant.covered_by) emit("open-set", item.variant.covered_by.id);
  else emit("make", item.variant);
}

function onKeydown(event) {
  const list = walk.value;
  const at = list.findIndex((item) => item.key === cursor.value);
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!list.length) return;
    const step = event.key === "ArrowDown" ? 1 : -1;
    const next =
      at < 0 ? (step > 0 ? 0 : list.length - 1) : at + step;
    cursor.value = list[Math.min(Math.max(next, 0), list.length - 1)].key;
    nextTick(() =>
      document
        .getElementById(cursorOptionId.value)
        ?.scrollIntoView?.({ block: "nearest" }),
    );
  } else if (event.key === "ArrowRight" && at >= 0) {
    event.preventDefault();
    toggle(cursor.value, true);
  } else if (event.key === "ArrowLeft" && at >= 0) {
    event.preventDefault();
    toggle(cursor.value, false);
  } else if (event.key === "Enter" && at >= 0) {
    event.preventDefault();
    press(list[at]);
  }
}

/** "arrived today", "arrived yesterday", "arrived 3 days ago", or "". */
function arrivedText(iso) {
  if (!iso) return "";
  const days = Math.floor((Date.now() - Date.parse(iso)) / 86_400_000);
  if (!Number.isFinite(days)) return "";
  if (days <= 0) return "arrived today";
  if (days === 1) return "arrived yesterday";
  return `arrived ${days} days ago`;
}

/**
 * What the template does, which tells two rows of one model apart: the part of
 * its title after the colon ("Z-Image-Turbo: Text to Image"), else its kind.
 */
function taskText(variant) {
  const [, task] = String(variant.title ?? "").split(/:\s+/, 2);
  return task || KIND_LABEL[variant.kind] || variant.kind;
}

/** "fp8 for bf16", from the head's other-quant match, or "". */
function quantText(variant) {
  const swapped = variant.files.find((f) => f.other_quant);
  if (!swapped) return "";
  return `${swapped.other_quant} for ${swapped.name}`;
}

function buttonLabel(variant) {
  if (variant.covered_by) {
    return `In ${variant.covered_by.name || "an untitled set"}`;
  }
  const missing = variant.files.length - variant.on_shelf;
  return missing ? `Make set · ${missing} missing` : "Make set";
}

/** One template row as drawn: its words, its mark and its button. */
function rowEntry(item) {
  const { row, variant } = item;
  const name = templateName(variant);
  const head =
    variant.files.find((f) => f.slot === "checkpoint") ?? variant.files[0];
  return {
    kind: "row",
    id: item.key,
    row,
    variant,
    name,
    sub: [
      taskText(variant),
      variant.complete
        ? ""
        : `${variant.on_shelf} of ${variant.files.length} on your shelf`,
      quantText(variant),
      arrivedText(variant.head_arrived_at),
    ]
      .filter(Boolean)
      .join(" · "),
    mark: { display_name: name, filename: head?.name ?? "" },
    have: `${variant.on_shelf} of ${variant.files.length}`,
    label: buttonLabel(variant),
    ariaLabel: variant.covered_by
      ? `Open ${variant.covered_by.name || "the set"}, made from ${name}`
      : `Make set from ${name}`,
    primary: variant.complete && !variant.covered_by,
  };
}

/** The list top to bottom: section labels, rows, the fold, Show more. */
const entries = computed(() => {
  const out = [];
  if (ready.value.length) {
    out.push({
      kind: "sec",
      id: "sec:ready",
      text: "Ready · every file is on your shelf",
    });
    out.push(...ready.value.map(rowEntry));
  }
  if (covered.value.length) {
    out.push({
      kind: "fold",
      id: "fold:covered",
      testid: "covered-fold",
      expanded: String(coveredOpen.value),
      icon: coveredOpen.value ? "mdi-chevron-down" : "mdi-chevron-right",
      text: coveredLine.value,
      press: () => (coveredOpen.value = !coveredOpen.value),
    });
    if (coveredOpen.value) out.push(...covered.value.map(rowEntry));
  }
  if (partial.value.length) {
    out.push({ kind: "div", id: "div:templates" });
    out.push({ kind: "sec", id: "sec:templates", text: "Templates" });
    out.push(...partialShown.value.map(rowEntry));
    const more = partial.value.length - partialShown.value.length;
    if (more) {
      out.push({
        kind: "fold",
        id: "fold:more",
        testid: "show-more",
        expanded: undefined,
        icon: "mdi-chevron-down",
        text: `Show ${more} more`,
        press: () => (partialDepth.value = partial.value.length),
      });
    }
  }
  return out;
});
</script>

<style scoped>
.wtp {
  box-sizing: border-box;
  width: min(var(--dialog-w-lg), calc(100vw - var(--space-6) * 2));
  display: flex;
  flex-direction: column;
}

.wtp__hd {
  order: -1;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5) var(--space-3);
}

.wtp__title {
  flex: 1;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.wtp__search {
  display: flex;
  gap: var(--space-3);
  margin: 0 var(--space-5) var(--space-3);
}

.wtp__search > .tbm-input-wrap {
  flex: 1;
  min-width: 0;
}

.wtp__seg {
  align-self: flex-start;
  margin: 0 var(--space-5) var(--space-3);
}

.wtp__list {
  max-height: min(60vh, calc((var(--control-h-bar) + var(--space-4)) * 10));
  overscroll-behavior: contain;
  overflow-y: auto;
  padding-bottom: var(--space-3);
}

.wtp__state {
  margin: 0;
  padding: var(--space-4) var(--space-5);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.wtp__sec {
  padding: var(--space-3) var(--space-5) var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.wtp__div {
  margin-top: var(--space-2);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

/* Chevron, mark, name over the line under it, how many are here, the button.
   An open row's table and variant choice span from the name column on. */
.wtp__row {
  display: grid;
  grid-template-columns: var(--space-5) var(--entity-thumb) minmax(0, 1fr) auto auto;
  align-items: center;
  column-gap: var(--space-4);
  padding: var(--space-3) var(--space-5);
  font-size: var(--text-sm);
  transition: background var(--dur-1) var(--ease-standard);
}

.wtp__row:hover,
.wtp__row--cur,
.wtp__row--open {
  background: var(--hover-wash);
}

.wtp__row--cur {
  box-shadow: var(--focus-ring-inset);
}

.wtp__row--open {
  padding-bottom: var(--space-4);
}

.wtp__chev {
  display: inline-flex;
  padding: 0;
  border: 0;
  background: none;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  cursor: pointer;
}

.wtp__two {
  min-width: 0;
  line-height: var(--leading-tight);
}

.wtp__nm {
  overflow: hidden;
  font-weight: var(--weight-semibold);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wtp__sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.wtp__flag {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--tag-h-xs);
  margin-left: var(--space-2);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-surface-warning));
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-surface-warning));
  white-space: nowrap;
}

.wtp__have {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  white-space: nowrap;
}

.wtp__ok {
  color: rgb(var(--v-theme-surface-success));
}

.wtp__files {
  grid-column: 3 / -1;
  width: 100%;
  margin-top: var(--space-3);
  border-collapse: collapse;
  font-size: var(--text-xs);
}

.wtp__files th,
.wtp__files td {
  height: var(--control-h-sm);
  padding: 0 var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-divider));
  text-align: left;
  white-space: nowrap;
}

.wtp__files th {
  border-top: none;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.wtp__fn {
  max-width: 0;
  width: 100%;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  text-overflow: ellipsis;
}

.wtp__num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.wtp__dl {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.wtp__variant {
  grid-column: 3 / -1;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
  font-size: var(--text-xs);
}

.wtp__select {
  width: auto;
  height: var(--control-h-sm);
}

.wtp__fold {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3) var(--space-5);
  border: 0;
  background: none;
  font: inherit;
  font-size: var(--text-xs);
  text-align: left;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
  cursor: pointer;
}

.wtp__fold:hover {
  background: var(--hover-wash);
}

.wtp__foot {
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
</style>
