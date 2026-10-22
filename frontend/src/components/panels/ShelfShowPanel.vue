<template>
  <div class="shelf-show" @keydown.left.capture="onLeft">
    <div
      ref="rootRef"
      class="tbm shelf-show-root"
      role="group"
      aria-label="Show"
    >
      <span class="tbm-caret tbm-caret--end"></span>
      <div class="tbm-header">
        <v-icon size="18" class="tbm-header-icon">mdi-eye-outline</v-icon>
        <span class="tbm-title">Show</span>
        <span class="tbm-spacer"></span>
        <span class="tbm-count">{{ matchLabel }}</span>
        <button
          class="tbm-ghost"
          type="button"
          :disabled="!store.activeCount"
          @click="store.resetFilters()"
        >
          <v-icon size="16">mdi-close-circle-outline</v-icon>
          Reset
        </button>
      </div>

      <!-- First, above the kinds: a name is what most people come here to
           find, and the field the toolbar copy hands over to when it folds. -->
      <div class="tbm-section">
        <NameFilterField
          ref="nameRef"
          :model-value="filters.name"
          placeholder="Filter models by name…"
          label="Filter models by name"
          @update:model-value="(name) => store.setFilters({ name })"
          @keydown.down.prevent="rowRefs[rowOrder[0]]?.focus()"
        />
      </div>

      <!-- Above the kinds, with the name: a narrowing, never a re-sort, and not
           restored on startup like the other one-off questions below. The count
           is the favourites the shelf holds, so the box says whether it does
           anything before it is ticked. -->
      <div class="tbm-section">
        <label class="tbm-check fm-check" data-testid="shelf-show-favourites">
          <input
            type="checkbox"
            :checked="filters.favouritesOnly"
            @change="
              store.setFilters({ favouritesOnly: $event.target.checked })
            "
          />
          <span class="fm-check-label">Favourites only</span>
          <span class="fm-n">{{ store.favouriteModelCount }}</span>
        </label>
      </div>

      <div
        v-for="section in sections"
        :key="section.kinds[0]"
        class="tbm-section"
      >
        <span v-if="section.label" class="tbm-label">{{ section.label }}</span>
        <button
          v-for="kind in section.kinds"
          :key="kind"
          :ref="(el) => (rowRefs[kind] = el)"
          class="fm-row"
          type="button"
          :data-kind="kind"
          :aria-expanded="sub === kind ? 'true' : 'false'"
          :aria-controls="sub === kind ? submenuId : undefined"
          @click="toggle(kind)"
          @keydown.right.prevent="openKind(kind)"
          @keydown.enter.prevent="openKind(kind)"
          @keydown.down.prevent="stepRow(kind, 1)"
          @keydown.up.prevent="stepRow(kind, -1)"
        >
          <v-icon size="16" class="fm-row-lead">{{ KINDS[kind].icon }}</v-icon>
          <span class="fm-row-label">{{ KINDS[kind].label }}</span>
          <span class="fm-n">{{ rowValue(kind) }}</span>
          <v-icon size="16" class="fm-row-trail">mdi-chevron-right</v-icon>
        </button>
      </div>
    </div>

    <div
      v-if="sub"
      :id="submenuId"
      ref="subRef"
      class="shelf-show-sub-slot"
      :style="{ marginTop: `${subTop}px` }"
    >
      <!-- The five blocks. All on by default, so the filter is turning one
           off. Native checkboxes in DOM order give Tab-between and
           Space-to-toggle for free. -->
      <div
        v-if="sub === 'type'"
        class="tbm fm-sub"
        role="group"
        aria-label="Type"
        data-testid="shelf-show-sub-type"
      >
        <div class="tbm-header">
          <span class="tbm-title">Type</span>
        </div>
        <div class="tbm-section">
          <label
            v-for="type in TYPES"
            :key="type.key"
            class="tbm-check fm-check"
          >
            <Tooltip v-if="type.tip" :text="type.tip" activator="parent" />
            <input
              type="checkbox"
              :checked="filters[type.key]"
              :indeterminate="indeterminate[type.key]?.value"
              @change="
                store.setFilters(
                  { [type.key]: $event.target.checked },
                  { refetch: true },
                )
              "
            />
            <span class="fm-check-label">{{ type.label }}</span>
          </label>
        </div>
      </div>

      <!-- The adapter algorithms and the engine capabilities: one list each,
           hanging off a block in Type. Turning that block off GREYS the boxes,
           it does not clear them: the selection survives in the store, so
           turning the block back on restores exactly what was picked. -->
      <div
        v-else-if="NESTED[sub]"
        class="tbm fm-sub"
        role="group"
        tabindex="-1"
        :aria-label="KINDS[sub].label"
        :aria-describedby="
          filters[NESTED[sub].parent] ? undefined : `${submenuId}-off`
        "
        :data-testid="`shelf-show-sub-${sub}`"
      >
        <div class="tbm-header">
          <span class="tbm-title">{{ KINDS[sub].label }}</span>
          <span class="tbm-spacer"></span>
          <button
            class="tbm-ghost"
            type="button"
            :disabled="!filters[NESTED[sub].field].length"
            @click="store.setFilters({ [NESTED[sub].field]: [] })"
          >
            Clear
          </button>
        </div>
        <div class="tbm-section">
          <label
            v-for="option in nestedOptions(sub)"
            :key="option"
            class="tbm-check fm-check"
            :class="{ 'shelf-show-check--off': !filters[NESTED[sub].parent] }"
          >
            <input
              type="checkbox"
              :disabled="!filters[NESTED[sub].parent]"
              :checked="filters[NESTED[sub].field].includes(option)"
              @change="
                toggleIn(NESTED[sub].field, option, $event.target.checked)
              "
            />
            <!-- The label, not the stored word: the shelf spells an algorithm
                 `LoRA` on the row and on the group header, and a capability in
                 the screen's words rather than the database's. -->
            <span class="fm-check-label">{{ NESTED[sub].label(option) }}</span>
          </label>
        </div>
        <div
          v-if="!filters[NESTED[sub].parent]"
          :id="`${submenuId}-off`"
          class="tbm-footer"
        >
          {{ NESTED[sub].offNote }}
        </div>
      </div>

      <!-- Base model: the one list that grows with the library, so it is the
           grid's Checkpoint menu - a field over a list that scrolls. -->
      <FilterChecklistMenu
        v-else-if="sub === 'baseModel'"
        title="Base model"
        placeholder="Filter base models…"
        :items="baseModelItems"
        :checked="filters.baseModels"
        :clearable="filters.baseModels.length > 0"
        empty-text="No base model matches."
        data-testid="shelf-show-sub-baseModel"
        @toggle="(option, on) => toggleIn('baseModels', option, on)"
        @clear="store.setFilters({ baseModels: [] })"
      />

      <!-- Copies and Hidden models: a pick-one list each, like every flag in
           the other Filters menus. No refetch for either: every row already
           carries its `locations` and says whether it is hidden. -->
      <div
        v-else-if="FLAGS[sub]"
        class="tbm fm-sub"
        role="group"
        :aria-label="KINDS[sub].label"
        :data-testid="`shelf-show-sub-${sub}`"
      >
        <div class="tbm-header">
          <span class="tbm-title">{{ KINDS[sub].label }}</span>
        </div>
        <div class="tbm-section">
          <OptionRows
            :options="flagOptions(sub)"
            :model-value="filters[FLAGS[sub].field]"
            :aria-label="KINDS[sub].label"
            @update:model-value="
              (on) => store.setFilters({ [FLAGS[sub].field]: on })
            "
          >
            <template #meta="{ option }">
              <span class="fm-n">{{ option.count }}</span>
            </template>
          </OptionRows>
        </div>
        <div class="tbm-footer">{{ FLAGS[sub].footer }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The Models shelf's Show menu: the cascade the picture grid's `FilterMenu`
 * and the Workflows screen's `WorkflowFilterMenu` use. A root list of filter
 * kinds, each opening one submenu beside it, so the root never grows with the
 * library and never scrolls; the one list that does grow (Base model) scrolls
 * inside its own submenu.
 *
 * The menu hangs from the END of its button, so the submenu opens on the
 * root's left: opening it on the right would widen the menu away from its
 * anchor and slide the root out from under the pointer.
 */
import { computed, nextTick, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import Tooltip from "../widgets/Tooltip.vue";
import OptionRows from "../widgets/OptionRows.vue";
import FilterChecklistMenu from "./FilterChecklistMenu.vue";
import NameFilterField from "../widgets/NameFilterField.vue";
import { BASE_MODEL_UNASSIGNED } from "../../api/modelShelf";
import { useCascadeMenu } from "../../composables/useCascadeMenu";
import { adapterKindLabel, capabilityLabel } from "../../utils/modelShelf";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const props = defineProps({
  // The v-menu keeps its content mounted, so a closed menu says so here and
  // reopens on the root alone.
  open: { type: Boolean, default: false },
  // Bumped by the view to put focus in the name field.
  focusNameRequest: { type: Number, default: 0 },
});

const store = useModelShelfStore();
const filters = store.filters;

const KINDS = {
  type: { label: "Type", icon: "mdi-shape-outline" },
  adapterKind: { label: "Adapter kind", icon: "mdi-puzzle-outline" },
  // The glyphs the shelf's Group menu gives the same two axes.
  capability: { label: "Engine feature", icon: "mdi-star-four-points-outline" },
  baseModel: { label: "Base model", icon: "mdi-cube-outline" },
  copies: { label: "Copies", icon: "mdi-content-copy" },
  // The Workflows menu's glyph for the same flag.
  hidden: { label: "Hidden models", icon: "mdi-eye-off-outline" },
};

const TYPES = [
  { key: "adapters", label: "Adapters" },
  { key: "checkpoints", label: "Checkpoints" },
  {
    key: "support",
    label: "Support files",
    tip: "VAEs, text encoders, ControlNets and upscalers - the files a generation graph loads beside a checkpoint",
  },
  {
    // `unknown` is a first-class stored value, never promoted to checkpoint
    // and never folded into adapters, so it has its own box and its own word.
    key: "unclassified",
    label: "Unclassified",
    tip: "Files we could not identify as an adapter or a checkpoint",
  },
  {
    key: "engines",
    label: "Engines",
    tip: "Models PixlStash and its tools downloaded: taggers, scorers, face packs and the HuggingFace cache",
  },
];

// The two lists that narrow one block of Type. Capabilities match "HAS this
// capability", not "IS this kind": one model can serve several features and is
// listed under each.
const NESTED = {
  adapterKind: {
    field: "adapterKinds",
    parent: "adapters",
    label: adapterKindLabel,
    offNote: "Adapters are turned off in Type.",
  },
  capability: {
    field: "capabilities",
    parent: "engines",
    label: capabilityLabel,
    offNote: "Engines are turned off in Type.",
  },
};

// The two flags: neither narrows WHAT a model is.
const FLAGS = {
  copies: {
    field: "duplicatesOnly",
    footer:
      "A duplicate is a file stored more than once: the same bytes, under one name or two.",
  },
  // The models this library leaves off its shelf and out of every menu that
  // offers one. Shown, they are drawn again, marked, and the context menu's
  // Hide row reads "Show model again" - the one way back.
  hidden: {
    field: "showHidden",
    footer: "Models you hid in this library. Right-click one to show it again.",
  },
};

function flagOptions(kind) {
  if (kind === "copies") {
    return [
      { id: false, label: "All" },
      { id: true, label: "Only duplicates" },
    ];
  }
  // Both rows carry how many this library hides, so the list says whether
  // there is anything to show before Show is picked.
  const count = store.hiddenModelIds.size.toLocaleString();
  return [
    { id: false, label: "Hide", count },
    { id: true, label: "Show", count },
  ];
}

function nestedOptions(kind) {
  return kind === "adapterKind"
    ? store.adapterKindOptions
    : store.capabilityOptions;
}

// A list with nothing in it has no row: there is no adapter kind to pick on a
// shelf with no adapters.
const sections = computed(() => [
  {
    label: "Model",
    kinds: [
      "type",
      ...(store.adapterKindOptions.length ? ["adapterKind"] : []),
      ...(store.capabilityOptions.length ? ["capability"] : []),
      ...(store.baseModelOptions.length ? ["baseModel"] : []),
    ],
  },
  // Last and unlabelled, where the grid's menu puts Problems: the two flags.
  { kinds: ["copies", "hidden"] },
]);
const rowOrder = computed(() => sections.value.flatMap((s) => s.kinds));

const { sub, subTop, rootRef, subRef, rowRefs, openKind, toggle, onLeft } =
  useCascadeMenu(
    () => props.open,
    // The chosen radio, else the first box or field that can take focus; never
    // the header. A greyed list has neither, so the list itself takes it: left
    // on the row, a keyboard never reaches the line saying why it is greyed.
    (submenu) =>
      (
        submenu?.querySelector('[role="radio"][tabindex="0"]') ??
        submenu?.querySelector("input:not(:disabled)") ??
        submenu?.querySelector('[tabindex="-1"]')
      )?.focus(),
  );
const submenuId = "shelf-show-submenu";
const nameRef = ref(null);
// The view's search key asks for the name field. Immediate, because the first
// ask is what opens the menu, and the menu renders this panel only then.
watch(
  () => props.focusNameRequest,
  (n) => n && nextTick(() => nameRef.value?.focus()),
  { immediate: true },
);

// A refetch can empty a list while its submenu is open; the row goes, and the
// submenu must not outlive it.
watch(rowOrder, (kinds) => {
  if (sub.value && !kinds.includes(sub.value)) sub.value = null;
});

const matchLabel = computed(() => {
  const n = store.visibleRows.length;
  return `${n.toLocaleString()} ${n === 1 ? "model" : "models"}`;
});

// Some-but-not-all ticked is a real third state, and the block's box says so
// rather than lying in either direction.
function someOf(parent, field, options) {
  return computed(
    () =>
      filters[parent] &&
      filters[field].length > 0 &&
      filters[field].length < options().length,
  );
}
const indeterminate = {
  adapters: someOf("adapters", "adapterKinds", () => store.adapterKindOptions),
  engines: someOf("engines", "capabilities", () => store.capabilityOptions),
};

/**
 * What a root row says about its filter: nothing while it is at rest, else
 * how many are picked. Type counts the other way, because its rest state is
 * every block on - which is also why the header says Reset where the other
 * Filters menus say Clear all: clearing every box here would empty the shelf.
 */
function rowValue(kind) {
  if (kind === "type") {
    const on = TYPES.filter((type) => filters[type.key]).length;
    return on === TYPES.length ? "" : `${on} of ${TYPES.length}`;
  }
  if (FLAGS[kind]) return filters[FLAGS[kind].field] ? "1" : "";
  if (kind === "baseModel") return filters.baseModels.length || "";
  // Picks under a block that is off narrow nothing, so they are not counted.
  const { field, parent } = NESTED[kind];
  return (filters[parent] && filters[field].length) || "";
}

const baseModelItems = computed(() =>
  store.baseModelOptions.map((option) => ({
    value: option,
    // "Not set" is an option, not an omission: a null base model is what most
    // real adapters record. The wire sentinel never reaches the UI.
    label: option === BASE_MODEL_UNASSIGNED ? "Not set" : option,
  })),
);

function toggleIn(field, option, checked) {
  const current = filters[field];
  store.setFilters({
    [field]: checked
      ? [...current, option]
      : current.filter((o) => o !== option),
  });
}

function stepRow(kind, step) {
  const order = rowOrder.value;
  const at = order.indexOf(kind);
  rowRefs[order[(at + step + order.length) % order.length]]?.focus();
}
</script>

<style scoped>
.shelf-show {
  display: flex;
  /* The submenu on the root's left; see the note at the top of the script. */
  flex-direction: row-reverse;
  /* A phone has no room for two menus side by side: the submenu wraps below. */
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--space-2);
}
.shelf-show-root {
  width: var(--filter-menu-w);
  max-width: 94vw;
}
.shelf-show-sub-slot {
  max-height: min(80vh, 760px);
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* The fade is legal here and only here: §11 exempts a disabled control from
   the contrast floor, and these are genuinely `disabled`, not merely quiet. */
.shelf-show-check--off {
  opacity: var(--opacity-disabled);
}
</style>
