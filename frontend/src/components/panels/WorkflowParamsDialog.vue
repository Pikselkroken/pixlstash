<template>
  <!-- Edit parameters (#2165): every setting of the workflow's graph that the
       rail offers, grouped by the node it belongs to. Each row is the same
       editable row as before (WorkflowDefaultRow); its Each run | Pinned switch
       is the pin toggle. Expose a parameter… adds one more. -->
  <AppDialog
    :open="open"
    title="Edit parameters"
    :subtitle="subtitle"
    size="lg"
    data-testid="wftab-params-dialog"
    @close="emit('close')"
  >
    <div class="wfp-tools">
      <AppInput
        v-model="filter"
        class="wfp-filter"
        type="search"
        placeholder="Filter parameters"
        aria-label="Filter parameters"
        data-testid="wfp-filter"
      />
      <AppButton
        size="sm"
        variant="ghost"
        icon-left="plus"
        :loading="exposeBusy"
        data-testid="wftab-expose"
        @click="emit('expose')"
      >
        Expose a parameter…
      </AppButton>
    </div>
    <!-- While a filter is on, how many of the parameters the list shows; the
         Each run count stays the workflow's, which is what the switches change. -->
    <p class="wfp-hint" data-testid="wfp-count">
      <template v-if="needle">{{ shownCount }} of </template>{{ rows.length }}
      {{ rows.length === 1 ? "parameter" : "parameters" }} · {{ eachCount }} set
      each run
    </p>

    <p v-if="reading" class="wfp-note" data-testid="wfp-reading">
      Reading its parameters…
    </p>
    <p v-else-if="!groups.length" class="wfp-note" data-testid="wfp-none">
      {{ rows.length ? "No parameter matches the filter." : "No parameters yet." }}
    </p>
    <section
      v-for="group in reading ? [] : groups"
      :key="group.node || 'other'"
      class="wfp-group"
    >
      <h3 class="section-label wfp-node">{{ group.node || "Other" }}</h3>
      <WorkflowDefaultRow
        v-for="item in group.rows"
        :key="item.row.label"
        :row="item.row"
        :options="item.options"
        :node="item.node"
        :busy="busy === `default:${item.row.label}`"
        @toggle-pin="emit('toggle-pin', item.row)"
        @reset="emit('reset', item.row)"
        @edit="(value) => emit('edit', item.row, value)"
      />
    </section>

    <template #footer>
      <span class="wfp-hint wfp-footer-hint">
        Changes join the workflow's unsaved changes. Run… uses them.
      </span>
      <AppButton variant="primary" data-testid="wftab-params-done" @click="emit('close')">
        Done
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
// The rail's Edit parameters dialog (#2165). `rows` is every parameter the
// tab reads, each with its node and the choices ComfyUI offers for it; the
// filter is local, and every write goes back up as an event.

import { computed, ref } from "vue";

import { parameterTitle } from "../../utils/workflowPins";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import WorkflowDefaultRow from "./WorkflowDefaultRow.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  subtitle: { type: String, default: "" },
  /** `[{row, node, options}]`: a parameter, the node it sets, and its choices. */
  rows: { type: Array, required: true },
  /** The `default:<label>` write in flight, or "". */
  busy: { type: String, default: "" },
  /** The Expose a parameter… write is in flight. */
  exposeBusy: { type: Boolean, default: false },
  /** The read the rows wait for is out: their choices are not known yet. */
  reading: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "toggle-pin", "reset", "edit", "expose"]);

const filter = ref("");

const eachCount = computed(
  () => props.rows.filter((item) => item.row.pinned).length,
);

const needle = computed(() => filter.value.trim().toLowerCase());

/** Grouped by node, in the order the nodes first appear, after the filter. */
const groups = computed(() => {
  const byNode = new Map();
  for (const item of props.rows) {
    const text = [parameterTitle(item.row), item.row.label, item.node]
      .join(" ")
      .toLowerCase();
    if (needle.value && !text.includes(needle.value)) continue;
    const node = item.node || "";
    if (!byNode.has(node)) byNode.set(node, { node, rows: [] });
    byNode.get(node).rows.push(item);
  }
  return [...byNode.values()];
});

/** How many parameters the filter leaves on screen. */
const shownCount = computed(() =>
  groups.value.reduce((sum, group) => sum + group.rows.length, 0),
);
</script>

<style scoped>
.wfp-tools {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.wfp-filter {
  flex: 1;
  min-width: 0;
}

.wfp-hint,
.wfp-note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfp-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.wfp-node {
  margin: 0;
}

.wfp-footer-hint {
  flex: 1;
  min-width: 0;
}
</style>
