import { computed, ref, watch } from "vue";
import { listAdapters } from "../api/modelShelf";
import { errorDetail } from "../utils/apiError";

// Swapping a LoRA from the model shelf into a run (#1310): the one
// implementation behind the run panel, "Generate variants" and "Edit with
// ComfyUI". It used to be three copies, and the bugs a review found were
// found three times - so the rules live here once.
//
// Four rules the callers rely on:
//
//   * A slot is a node AND a field. A stacker carries several LoRAs on one node
//     (`lora_name_1`, `lora_name_2`), so a choice keyed on the node alone could
//     not tell them apart, and the run would swap every one of them.
//   * The choice belongs to the graph it was made for. Whenever the slots change
//     (another workflow, another picture, another mode) it goes back to "keep
//     the workflow's own", so a LoRA picked for one run never rides silently
//     into the next - Generate variants opens with focus on Generate.
//   * Nothing is sent where there is no slot: the backend refuses the whole run
//     rather than ignoring the field.
//   * Except where a loader can be added (#1376): a graph with no slot asks the
//     backend where one would go, and offers the shelf only once it can show
//     that. Only then does a run send `insert_lora_loader`.

/** The key a slot is chosen by: field and node, since neither alone is unique. */
export function slotKey(slot) {
  return `${slot.field}@${slot.node_id}`;
}

/**
 * How a slot reads in a menu: the node, what it loads now (only the owner-only
 * reads carry that), and the field when a node has more than the one.
 */
export function slotLabel(slot) {
  const loads = slot.value || slot.class_type || "";
  const field = slot.field && slot.field !== "lora_name" ? ` · ${slot.field}` : "";
  return `#${slot.node_id} ${loads}${field}`.trim();
}

/**
 * What adding a loader would do, in one sentence the owner reads before a run:
 * where it goes and every input it takes over.
 */
export function insertionSummary(plan) {
  if (!plan?.model) return "";
  const node = (n) => `#${n.node_id} ${n.class_type || ""}`.trim();
  const from =
    plan.clip && plan.clip.node_id !== plan.model.node_id
      ? `${node(plan.model)} and ${node(plan.clip)}`
      : node(plan.model);
  const rewires = (plan.rewires || [])
    .map((r) => `${node(r)} (${r.field})`)
    .join(", ");
  const onlyModel = plan.clip ? "" : " It loads the model only: nothing here reads a CLIP.";
  return `A LoRA loader is added after ${from}, feeding ${rewires}.${onlyModel}`;
}

/**
 * @param {import("vue").Ref<Array<Object>>} slots - the `lora_slots` of whatever
 *   this surface would run now; `[]` when it has none.
 * @param {import("vue").Ref<{key: string, load: () => Promise<Object>}|null>} [insertionSource]
 *   - for a graph with no slot, how to ask where a loader would go:
 *   `load` resolves with `{plan, reason}`, and `key` names the graph so a
 *   change of graph is seen even when both have no slot. `null` offers none.
 */
export function useLoraSwap(slots, insertionSource = ref(null)) {
  /** The shelf adapter to swap in, "" for the workflow's own. */
  const adapterSha = ref("");
  /** Which slot it goes into, as `slotKey`; the first until chosen. */
  const chosenSlot = ref("");
  const adapters = ref([]);
  const adaptersError = ref("");
  /** `{plan, reason}` for a graph with no slot, `null` until known. */
  const insertion = ref(null);
  const insertionLoading = ref(false);

  /** The graph has no slot, but a loader can be added and has been shown. */
  const canInsert = computed(
    () => !(slots.value || []).length && Boolean(insertion.value?.plan),
  );

  // Sorted by name, and the base model beside it: a select is scanned by eye,
  // and an SDXL LoRA put into a Flux graph is the easy mistake to make. A row
  // whose hash the shelf has not read yet cannot be asked for, so it is left
  // out rather than offered and refused.
  const adapterOptions = computed(() => [
    {
      value: "",
      label: canInsert.value ? "No LoRA" : "Keep the workflow's own",
    },
    ...adapters.value
      .filter((adapter) => adapter?.sha256)
      .map((adapter) => {
        const name =
          adapter.display_name || adapter.filename || adapter.sha256.slice(0, 12);
        return {
          value: adapter.sha256,
          label: adapter.base_model ? `${name} · ${adapter.base_model}` : name,
        };
      })
      .sort((a, b) => a.label.localeCompare(b.label)),
  ]);

  const slotOptions = computed(() =>
    (slots.value || []).map((slot) => ({
      value: slotKey(slot),
      label: slotLabel(slot),
    })),
  );

  // Once per component lifetime, and again after a failure.
  // ponytail: the whole shelf in one select; a search field if it grows unwieldy.
  let adaptersLoaded = false;
  async function loadAdapters() {
    if (adaptersLoaded) return;
    adaptersLoaded = true;
    adaptersError.value = "";
    try {
      adapters.value = await listAdapters();
    } catch (err) {
      adaptersLoaded = false;
      adaptersError.value = `Your LoRAs could not be read: ${
        errorDetail(err) || err?.message || String(err)
      }`;
    }
  }

  /** Back to the workflow's own LoRA, in the first slot. */
  function resetChoice() {
    adapterSha.value = "";
    chosenSlot.value = slots.value?.length ? slotKey(slots.value[0]) : "";
  }

  // A request for a graph that is no longer shown must not land.
  let insertionRequest = 0;
  async function loadInsertion() {
    const request = ++insertionRequest;
    insertion.value = null;
    insertionLoading.value = false;
    const source = insertionSource.value;
    if ((slots.value || []).length || !source) return;
    insertionLoading.value = true;
    let result;
    try {
      result = await source.load();
    } catch (err) {
      result = {
        plan: null,
        reason: `The check failed: ${errorDetail(err) || err?.message || String(err)}`,
      };
    }
    if (request !== insertionRequest) return;
    insertionLoading.value = false;
    insertion.value = result || null;
    if (insertion.value?.plan) loadAdapters();
  }

  // Keyed on the slots' identity and the graph's key, not the array: a computed
  // that hands back a fresh `[]` on every read must not wipe a choice the user
  // just made, and two workflows that both have no slot are still two graphs.
  watch(
    () =>
      `${(slots.value || []).map(slotKey).join("|")}#${insertionSource.value?.key ?? ""}`,
    () => {
      resetChoice();
      if (slots.value?.length) loadAdapters();
      loadInsertion();
    },
    { immediate: true },
  );

  /**
   * The LoRA half of a run body, and nothing when no swap was asked for. The
   * slot is named only where there is a choice to make.
   */
  function body() {
    const list = slots.value || [];
    if (!adapterSha.value) return {};
    if (!list.length) {
      return canInsert.value
        ? { adapter_sha256: adapterSha.value, insert_lora_loader: true }
        : {};
    }
    if (list.length === 1) return { adapter_sha256: adapterSha.value };
    const slot = list.find((s) => slotKey(s) === chosenSlot.value) || list[0];
    return {
      adapter_sha256: adapterSha.value,
      lora_node_id: slot.node_id,
      lora_field: slot.field,
    };
  }

  return {
    adapterSha,
    chosenSlot,
    adapters,
    adaptersError,
    adapterOptions,
    slotOptions,
    insertion,
    insertionLoading,
    canInsert,
    insertionText: computed(() => insertionSummary(insertion.value?.plan)),
    loadAdapters,
    resetChoice,
    body,
  };
}
