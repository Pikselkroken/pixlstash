<template>
  <div class="tbm shelf-show-panel">
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
        <v-icon size="15">mdi-close-circle-outline</v-icon>
        Reset
      </button>
    </div>

    <!-- Types. Not a `role="tree"`: this is two flat groups, and native
         checkboxes in DOM order give Tab-between / Space-to-toggle for free.
         An ARIA tree here would be a widget contract to maintain for nothing. -->
    <div class="tbm-section">
      <span class="tbm-label">Types</span>
      <label class="tbm-check">
        <input
          ref="adaptersBoxRef"
          type="checkbox"
          :checked="filters.adapters"
          :indeterminate="adaptersIndeterminate"
          @change="
            store.setFilters(
              { adapters: $event.target.checked },
              {
                refetch: true,
              },
            )
          "
        />
        Adapters
      </label>
      <!-- Unchecking the parent GREYS the kinds, it does not clear them: the
           selection survives in the store, so re-checking Adapters restores
           exactly what was picked before. `disabled` is also what takes them
           out of the tab order while they are inert. -->
      <div
        v-if="store.adapterKindOptions.length"
        class="shelf-show-nested shelf-show-nested--kinds"
      >
        <label
          v-for="kind in store.adapterKindOptions"
          :key="kind"
          class="tbm-check"
          :class="{ 'shelf-show-check--off': !filters.adapters }"
        >
          <input
            type="checkbox"
            :disabled="!filters.adapters"
            :checked="filters.adapterKinds.includes(kind)"
            @change="toggleKind(kind, $event.target.checked)"
          />
          <!-- The label, not the stored word - the capability boxes below have
               always done this, and the shelf now spells the algorithm `LoRA`
               on both the row and the group header. A raw `lokr` here was the
               third spelling of one thing on one screen. -->
          {{ adapterKindLabel(kind) }}
        </label>
      </div>
      <label class="tbm-check">
        <input
          type="checkbox"
          :checked="filters.checkpoints"
          @change="
            store.setFilters(
              { checkpoints: $event.target.checked },
              {
                refetch: true,
              },
            )
          "
        />
        Checkpoints
      </label>
      <!-- Support files: the VAEs and text encoders a graph loads beside a
           checkpoint. One box for two `file_kind`s, because they are one
           question to someone deciding what to keep. On by default, and the
           box matters more than the others: before these kinds existed the
           large encoders were counted as checkpoints, so `Checkpoints` was
           answering with a list mostly made of them. -->
      <label
        class="tbm-check"
        title="VAEs and text encoders - the files a generation graph loads beside a checkpoint"
      >
        <input
          type="checkbox"
          :checked="filters.support"
          @change="
            store.setFilters(
              { support: $event.target.checked },
              {
                refetch: true,
              },
            )
          "
        />
        Support files
      </label>
      <!-- `unknown` is a first-class stored value, never promoted to
           checkpoint and never folded into adapters. It gets its own box and
           its own word, and it is ON by default: the leftovers in PixlStash's
           own download folder land here, and off by default is how they stayed
           invisible (#927). -->
      <label
        class="tbm-check"
        title="Files we could not identify as an adapter or a checkpoint"
      >
        <input
          type="checkbox"
          :checked="filters.unclassified"
          @change="
            store.setFilters(
              { unclassified: $event.target.checked },
              {
                refetch: true,
              },
            )
          "
        />
        Unclassified
      </label>
      <!-- Engines: PixlStash's own taggers and scorers, the InsightFace packs
           and the HuggingFace cache. On by default, unlike Unclassified - they
           are the answer to "where did my disk go", and off by default is
           exactly how they stayed invisible while the architecture note said
           they were on the shelf. -->
      <label
        class="tbm-check"
        title="Models PixlStash and its tools downloaded: taggers, scorers, face packs and the HuggingFace cache"
      >
        <input
          type="checkbox"
          :checked="filters.engines"
          :indeterminate="enginesIndeterminate"
          @change="
            store.setFilters(
              { engines: $event.target.checked },
              {
                refetch: true,
              },
            )
          "
        />
        Engines
      </label>
      <!-- Capabilities, nested under Engines exactly as the algorithms nest
           under Adapters, and greyed rather than cleared the same way.
           These match "HAS this capability", not "IS this kind": one model can
           serve several features and is listed under each, so ticking
           `Captioning` keeps Florence-2 - which also detects - in view. -->
      <div v-if="store.capabilityOptions.length" class="shelf-show-nested">
        <label
          v-for="capability in store.capabilityOptions"
          :key="capability"
          class="tbm-check"
          :class="{ 'shelf-show-check--off': !filters.engines }"
        >
          <input
            type="checkbox"
            :disabled="!filters.engines"
            :checked="filters.capabilities.includes(capability)"
            @change="toggleCapability(capability, $event.target.checked)"
          />
          {{ capabilityLabel(capability) }}
        </label>
      </div>
    </div>

    <!-- Copies. Its own section rather than a sixth box under Types, because
         it does not narrow WHAT a row is - it narrows to the rows that are on
         the disk more than once, whatever kind they are. `refetch: false`: the
         count is computed from the `locations` every row already carries, so
         there is nothing to ask the server for. -->
    <div class="tbm-section">
      <span class="tbm-label">Copies</span>
      <label
        class="tbm-check"
        title="Files stored more than once - the same bytes, under one name or two"
      >
        <input
          type="checkbox"
          :checked="filters.duplicatesOnly"
          @change="store.setFilters({ duplicatesOnly: $event.target.checked })"
        />
        Only duplicates
      </label>
    </div>

    <!-- Base model. "Not set" is an option, not an omission: a null base model
         is what most real adapters record, so dropping those rows from a
         filtered view would hide the largest group in the shelf. -->
    <div v-if="store.baseModelOptions.length" class="tbm-section">
      <span class="tbm-label">Base model</span>
      <div class="shelf-show-list">
        <label
          v-for="option in store.baseModelOptions"
          :key="option"
          class="tbm-check"
        >
          <input
            type="checkbox"
            :checked="filters.baseModels.includes(option)"
            @change="toggleBaseModel(option, $event.target.checked)"
          />
          {{ baseModelLabel(option) }}
        </label>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import { BASE_MODEL_UNASSIGNED } from "../../api/modelShelf";
import { adapterKindLabel, capabilityLabel } from "../../utils/modelShelf";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const store = useModelShelfStore();
const filters = store.filters;

const matchLabel = computed(() => {
  const n = store.visibleRows.length;
  return `${n.toLocaleString()} ${n === 1 ? "model" : "models"}`;
});

// Some-but-not-all kinds ticked is a real third state and the checkbox says so
// rather than lying in either direction.
const adaptersIndeterminate = computed(
  () =>
    filters.adapters &&
    filters.adapterKinds.length > 0 &&
    filters.adapterKinds.length < store.adapterKindOptions.length,
);

const enginesIndeterminate = computed(
  () =>
    filters.engines &&
    filters.capabilities.length > 0 &&
    filters.capabilities.length < store.capabilityOptions.length,
);

function toggleKind(kind, checked) {
  const current = filters.adapterKinds;
  const next = checked ? [...current, kind] : current.filter((k) => k !== kind);
  store.setFilters({ adapterKinds: next });
}

function toggleCapability(capability, checked) {
  const current = filters.capabilities;
  const next = checked
    ? [...current, capability]
    : current.filter((c) => c !== capability);
  store.setFilters({ capabilities: next });
}

function toggleBaseModel(option, checked) {
  const current = filters.baseModels;
  const next = checked
    ? [...current, option]
    : current.filter((b) => b !== option);
  store.setFilters({ baseModels: next });
}

function baseModelLabel(option) {
  return option === BASE_MODEL_UNASSIGNED ? "Not set" : option;
}
</script>

<style scoped>
.shelf-show-panel {
  width: 320px;
  max-width: 94vw;
  max-height: min(80vh, 640px);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

/* One checkbox per line: the labels here are single words that must not be
   read as a two-column grid of unrelated pairs. */
.tbm-check {
  display: flex;
  margin-bottom: var(--space-3);
}
.tbm-check:last-child {
  margin-bottom: 0;
}

/* Children step in once from their parent's box, by the one indent step. */
.shelf-show-nested {
  padding-left: var(--indent-step);
  margin-bottom: var(--space-3);
}

/* The fade is legal here and only here: §11 exempts a disabled control from
   the contrast floor, and these are genuinely `disabled`, not merely quiet. */
.shelf-show-check--off {
  opacity: var(--opacity-disabled);
}

.shelf-show-list {
  max-height: 200px;
  overflow-y: auto;
}
</style>
