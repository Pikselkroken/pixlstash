<template>
  <AppDialog
    :open="open"
    title="Expose a parameter"
    size="sm"
    @close="close"
    @accept="accept"
  >
    <p v-if="pending" class="xpd-note xpd-quiet">Reading its nodes…</p>
    <p v-else-if="failed" class="xpd-note xpd-quiet" data-testid="xpd-failed">
      {{ failed }}
    </p>
    <p v-else-if="!nodes.length" class="xpd-note xpd-quiet" data-testid="xpd-none">
      Every setting PixlStash can set on this workflow is a parameter already.
    </p>
    <template v-else>
      <p class="xpd-note xpd-quiet">
        Pick a setting of one of this workflow's nodes. It joins the
        parameters, and the Run form asks for it each run.
      </p>
      <AppSelect
        v-model="nodeId"
        label="Node"
        :options="nodeOptions"
        data-testid="xpd-node"
      />
      <AppSelect
        v-model="inputName"
        label="Parameter"
        :options="inputOptions"
        data-testid="xpd-input"
      />
      <!-- Asked only where ComfyUI did not say: the value in the graph is then
           the one clue, and `7` may be a number or a piece of text. -->
      <AppSelect
        v-if="input && !input.kind"
        v-model="kind"
        label="Type"
        :options="KINDS"
        data-testid="xpd-kind"
      />
      <p v-if="input" class="xpd-note" data-testid="xpd-now">
        <span class="xpd-quiet">It is now</span>
        <span class="num">{{ String(input.value) }}</span>
        <span v-if="input.options" class="xpd-quiet">
          · one of {{ input.options.length }} choices
        </span>
      </p>
      <p v-if="refusal" class="xpd-note xpd-bad" role="alert">{{ refusal }}</p>
    </template>

    <template #footer>
      <AppButton @click="close">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="plus"
        :disabled="value === undefined"
        data-testid="xpd-expose"
        @click="accept"
      >
        Expose
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * Expose a parameter: pick a node of the workflow's graph, then one of its
 * settings, and it becomes a row of the Workflow tab's Parameters panel.
 *
 * `nodes` is `GET …/form-inputs` less what is a parameter already, so the
 * lists hold only what can be added. The dialog writes nothing: it hands the
 * chosen address and the value it holds now to the tab, which owns the
 * workflow's whole-set writes.
 */
import { computed, nextTick, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppSelect from "../widgets/AppSelect.vue";

const KINDS = [
  { label: "Number", value: "number" },
  { label: "Text", value: "text" },
  { label: "On or off", value: "boolean" },
];

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `[{node_id, title, class_type, inputs: [{slot_label, input_name, value, kind, options}]}]`. */
  nodes: { type: Array, default: () => [] },
  pending: { type: Boolean, default: false },
  /** Why the nodes could not be read, or "". */
  failed: { type: String, default: "" },
});

const emit = defineEmits(["close", "expose"]);

const nodeId = ref("");
const inputName = ref("");
const kind = ref("text");
let invoker = null;

const node = computed(() =>
  props.nodes.find((entry) => entry.node_id === nodeId.value),
);
const input = computed(() =>
  node.value?.inputs.find((entry) => entry.input_name === inputName.value),
);

/** Two nodes of one name are told apart by ComfyUI's own node number. */
const nodeOptions = computed(() => {
  const count = {};
  for (const entry of props.nodes) {
    count[entry.title] = (count[entry.title] || 0) + 1;
  }
  return props.nodes.map((entry) => ({
    label:
      count[entry.title] > 1 ? `${entry.title} #${entry.node_id}` : entry.title,
    value: entry.node_id,
  }));
});
const inputOptions = computed(() =>
  (node.value?.inputs ?? []).map((entry) => entry.input_name),
);

/** What a value in the graph looks like, where ComfyUI names no type. */
function guessed(value) {
  if (typeof value === "boolean") return "boolean";
  return typeof value === "number" ? "number" : "text";
}

// A selection always names something that is there: the first node, and the
// first setting of whichever node is chosen.
watch(
  () => [props.open, props.nodes],
  () => {
    if (!node.value) nodeId.value = props.nodes[0]?.node_id ?? "";
  },
  { immediate: true },
);
watch(
  node,
  () => {
    if (!input.value) inputName.value = node.value?.inputs[0]?.input_name ?? "";
  },
  { immediate: true },
);
watch(
  input,
  (entry) => {
    if (entry) kind.value = guessed(entry.value);
  },
  { immediate: true },
);
watch(
  () => props.open,
  (open) => {
    if (open) invoker = document.activeElement;
  },
  { immediate: true },
);

/**
 * The value the parameter starts at, as the type it is: the graph's own where
 * ComfyUI typed it, else read as the type picked here. `undefined` when it
 * cannot be read as that type.
 */
const value = computed(() => {
  const entry = input.value;
  if (!entry) return undefined;
  if (entry.kind || kind.value === guessed(entry.value)) return entry.value;
  const text = String(entry.value).trim();
  if (kind.value === "text") return String(entry.value);
  if (kind.value === "number") {
    const number = Number(text);
    return text && Number.isFinite(number) ? number : undefined;
  }
  if (text === "true" || text === "1") return true;
  return text === "false" || text === "0" ? false : undefined;
});

const refusal = computed(() => {
  if (!input.value || value.value !== undefined) return "";
  return kind.value === "number"
    ? `${String(input.value.value)} is not a number.`
    : `${String(input.value.value)} is neither on nor off.`;
});

function accept() {
  if (value.value === undefined) return;
  emit("expose", {
    slot_label: input.value.slot_label,
    input_name: input.value.input_name,
    value: value.value,
  });
  restoreFocus();
}

function close() {
  emit("close");
  restoreFocus();
}

/** Vuetify hands focus back only to an activator, and this has none. */
function restoreFocus() {
  const back = invoker;
  invoker = null;
  nextTick(() => back?.isConnected && back.focus?.());
}
</script>

<style scoped>
.xpd-note {
  margin: 0;
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

.xpd-note > span + span {
  margin-left: var(--space-2);
}

.xpd-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.xpd-bad {
  color: rgb(var(--v-theme-surface-error));
}
</style>
