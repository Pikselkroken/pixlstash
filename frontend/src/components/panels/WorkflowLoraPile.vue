<template>
  <!-- The LoRAs that change per picture, as a plain list under the default
       recipe's own (#2165): each with how many pictures used it, Show N for
       the pictures, and Add for the ones the default can take. -->
  <ul ref="list" class="wfalso" tabindex="-1" aria-label="Also used">
    <li
      v-for="row in rows"
      :key="row.asset || 'none'"
      class="wfalso-row"
      :data-testid="`wftab-also-row-${row.asset || 'none'}`"
    >
      <span class="wfalso-name" :class="{ 'wfalso-quiet': !row.asset || !row.name }">
        <Tooltip v-if="row.file" :text="row.file" activator="parent" />
        {{ label(row) }}
      </span>
      <span class="wfalso-count">in {{ row.pictures }}</span>
      <AppButton
        v-if="row.asset && row.pictures"
        variant="ghost"
        size="sm"
        :aria-label="`Show the ${pictureCount(row.pictures)} made with ${label(row)}`"
        @click="show(row)"
      >
        Show {{ row.pictures }}
      </AppButton>
      <!-- Only a LoRA the shelf names as one file (`sha256`): the default
           recipe names a LoRA by that digest, and the route takes exactly the
           LoRAs the summary gives one. -->
      <AppButton
        v-if="row.asset && row.sha256"
        variant="ghost"
        size="sm"
        data-testid="wftab-add-default"
        :loading="adding === row.asset"
        :disabled="Boolean(adding) && adding !== row.asset"
        :aria-label="`Add ${label(row)} to the default recipe`"
        tooltip="Add to the workflow's default LoRAs"
        @click="addDefault(row)"
      >
        Add
      </AppButton>
    </li>
  </ul>
</template>

<script setup>
// The Workflow tab's ALSO USED list (#1653, #2165): the LoRAs its pictures used
// that are not in the default recipe. `summary` is `GET
// /workflows/{id}/lora-summary` with the default recipe's LoRAs already taken
// out of `varying` by the tab. Its verbs are Show (the grid, narrowed to one
// LoRA's pictures) and Add (into the default recipe).

import { computed, nextTick, ref, watch } from "vue";

import { pictureCount } from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** `GET …/lora-summary`: `{pictures, varying, without, cover_asset}`. */
  summary: { type: Object, required: true },
  /** The `asset` of the LoRA being added to the default recipe, while it is. */
  adding: { type: String, default: "" },
});

const emit = defineEmits(["show", "add-default"]);

/** The LoRAs used besides the default's, then "No LoRA" when some picture had none. */
const rows = computed(() => [
  ...(props.summary?.varying ?? []),
  ...(props.summary?.without ? [props.summary.without] : []),
]);

function label(row) {
  if (!row) return "";
  if (!row.asset) return "No LoRA";
  return row.name || "A LoRA whose name was forgotten";
}

function show(row) {
  emit("show", row);
}

const list = ref(null);

/** Where the row being added stood, so focus can stay there when it leaves. */
let addedAt = -1;
let addedAsset = "";

function addDefault(row) {
  addedAt = rows.value.indexOf(row);
  addedAsset = row.asset;
  emit("add-default", row);
}

// An added LoRA leaves the list, and its button with it: focus moves to the row
// that took its place (its first button), else the list, rather than falling to
// the page.
watch(rows, async (now) => {
  if (!addedAsset || now.some((row) => row.asset === addedAsset)) return;
  const index = Math.min(addedAt, now.length - 1);
  addedAsset = "";
  await nextTick();
  const next = list.value?.querySelectorAll(".wfalso-row")[index];
  (next?.querySelector("button") ?? list.value)?.focus();
});
</script>

<style scoped>
.wfalso {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

/* Name takes what is left and ellipsises; the count and the verbs keep their
   width, so every row lines up whatever its name. */
.wfalso-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  font-size: var(--text-xs);
}

.wfalso-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wfalso-count {
  flex: none;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfalso-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
