import { computed, ref, watch } from "vue";
import { listAdapters } from "../api/modelShelf";
import { errorDetail } from "../utils/apiError";

// Swapping a LoRA from the model shelf into a run (#1310): the one
// implementation behind the run panel, "Generate variants" and "Edit with
// ComfyUI". It used to be three copies, and the bugs a review found were
// found three times - so the rules live here once.
//
// Three rules the callers rely on:
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
 * @param {import("vue").Ref<Array<Object>>} slots - the `lora_slots` of whatever
 *   this surface would run now; `[]` when it has none.
 */
export function useLoraSwap(slots) {
  /** The shelf adapter to swap in, "" for the workflow's own. */
  const adapterSha = ref("");
  /** Which slot it goes into, as `slotKey`; the first until chosen. */
  const chosenSlot = ref("");
  const adapters = ref([]);
  const adaptersError = ref("");

  // Sorted by name, and the base model beside it: a select is scanned by eye,
  // and an SDXL LoRA put into a Flux graph is the easy mistake to make. A row
  // whose hash the shelf has not read yet cannot be asked for, so it is left
  // out rather than offered and refused.
  const adapterOptions = computed(() => [
    { value: "", label: "Keep the workflow's own" },
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

  // Keyed on the slots' identity, not the array: a computed that hands back a
  // fresh `[]` on every read must not wipe a choice the user just made.
  watch(
    () => (slots.value || []).map(slotKey).join("|"),
    () => {
      resetChoice();
      if (slots.value?.length) loadAdapters();
    },
    { immediate: true },
  );

  /**
   * The LoRA half of a run body, and nothing when no swap was asked for. The
   * slot is named only where there is a choice to make.
   */
  function body() {
    const list = slots.value || [];
    if (!list.length || !adapterSha.value) return {};
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
    loadAdapters,
    resetChoice,
    body,
  };
}
