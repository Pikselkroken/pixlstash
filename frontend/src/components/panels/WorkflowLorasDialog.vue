<template>
  <!-- Edit LoRAs (#2165): the default recipe's LoRAs as the rail's own editable
       list. Every strength, removal and put-back is an unsaved change at once,
       so there is no Apply and no Cancel: Done closes it. Adding a LoRA or
       reordering the chain is the chain editor's, which writes at once and is
       one step away from here. -->
  <AppDialog
    :open="open"
    title="Edit LoRAs"
    :subtitle="subtitle"
    size="md"
    data-testid="wftab-loras-dialog"
    @close="emit('close')"
  >
    <p v-if="!rows.length" class="wfl-note wfl-quiet">
      This workflow has no LoRA in its default recipe.
    </p>
    <ol
      v-else
      ref="list"
      class="wfl-list"
      aria-label="LoRAs, in the order the chain applies them"
    >
      <li
        v-for="lora in rows"
        :key="lora.id"
        class="wfl-row"
        :class="{ 'wfl-row--gone': lora.removed }"
        :data-slot="lora.slotKey || undefined"
        data-testid="wfl-row"
      >
        <div class="wfl-main">
          <span class="wfl-name">
            <Tooltip v-if="lora.file" :text="lora.file" activator="parent" />
            <s v-if="lora.removed">{{ lora.label }}</s>
            <template v-else>{{ lora.label }}</template>
          </span>
          <span v-if="lora.removed" class="wfl-mark" data-testid="wftab-lora-removed"
            >Removed</span
          >
          <span
            v-else-if="lora.strengthEdited"
            class="wfl-mark"
            data-testid="wftab-lora-edited"
            >Edited</span
          >
          <span
            v-if="lora.strengthEdited && !lora.removed"
            class="wfl-quiet wfl-saved"
            data-testid="wftab-lora-saved"
            >Saved: {{ lora.savedStrengthText }}</span
          >
        </div>
        <div class="wfl-ctl">
          <template v-if="lora.removed">
            <AppButton
              size="sm"
              variant="ghost"
              data-testid="wftab-lora-put-back"
              :disabled="!canEdit"
              :aria-label="`Put ${lora.label} back`"
              @click="remove(lora, false)"
            >
              Put back
            </AppButton>
          </template>
          <template v-else>
            <!-- A component, so an answer landing while a number is half typed
                 does not put the row's own back. -->
            <WorkflowStrengthInput
              v-if="lora.canStrength"
              class="wfl-strength"
              step="0.05"
              min="-10"
              max="10"
              :value="lora.strengthValue"
              :disabled="!canEdit"
              :aria-label="`Strength of ${lora.label}`"
              data-testid="wftab-lora-strength"
              @change="(raw, field) => emit('strength', lora, raw, field)"
            />
            <span v-else-if="lora.strengthText" class="wfl-quiet">{{
              lora.strengthText
            }}</span>
            <AppButton
              v-if="lora.strengthEdited"
              variant="ghost"
              size="sm"
              icon-only
              icon-left="undo"
              data-testid="wftab-reset-strength"
              :disabled="!canEdit"
              :tooltip="`Put the strength of ${lora.label} back to ${lora.savedStrengthText}`"
              @click="emit('reset-strength', lora)"
            />
            <AppButton
              v-if="lora.slot"
              variant="ghost"
              size="sm"
              icon-only
              icon-left="close"
              data-testid="wftab-lora-remove"
              :disabled="!canEdit"
              :tooltip="`Remove ${lora.label}`"
              @click="remove(lora, true)"
            />
          </template>
        </div>
      </li>
    </ol>

    <AppButton
      size="sm"
      icon-left="plus"
      data-testid="wftab-lora-chain"
      :disabled="chainNoGraph"
      @click="emit('chain')"
    >
      Add a LoRA or reorder…
    </AppButton>

    <template #footer>
      <span class="wfl-hint">
        Changes join the workflow's unsaved changes. Run… uses them.
      </span>
      <AppButton variant="primary" data-testid="wftab-loras-done" @click="emit('close')">
        Done
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
// The rail's Edit LoRAs dialog (#2165). Its rows are the tab's `loraRows`; every
// change goes back up as an event, so the tab's own write path (the unsaved
// changes call) does the work and this stays a view of it.

import { nextTick, ref } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import Tooltip from "../widgets/Tooltip.vue";
import WorkflowStrengthInput from "./WorkflowStrengthInput.vue";

defineProps({
  open: { type: Boolean, default: false },
  /** The workflow's name, under the title. */
  subtitle: { type: String, default: "" },
  /** The tab's `loraRows`: the default recipe's LoRAs as the rows read them. */
  rows: { type: Array, required: true },
  /** Whether the workflow's unsaved changes can be written just now. */
  canEdit: { type: Boolean, default: false },
  /** No graph in PixlStash: the chain cannot be read or changed at all. */
  chainNoGraph: { type: Boolean, default: false },
});

const emit = defineEmits([
  "close",
  "strength",
  "remove",
  "reset-strength",
  "chain",
]);

const list = ref(null);

/**
 * Remove a loader, or put it back. The control pressed leaves with the row's
 * state, so focus goes to the one that took its place: Put back after ×, × after
 * Put back.
 */
async function remove(lora, removed) {
  emit("remove", lora, removed);
  await nextTick();
  const row = [...(list.value?.querySelectorAll("[data-slot]") ?? [])].find(
    (entry) => entry.dataset.slot === lora.slotKey,
  );
  row
    ?.querySelector(
      `[data-testid="${removed ? "wftab-lora-put-back" : "wftab-lora-remove"}"]`,
    )
    ?.focus();
}
</script>

<style scoped>
.wfl-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.wfl-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
}

.wfl-main {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
  min-width: 0;
  flex: 1;
  font-size: var(--text-sm);
}

.wfl-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wfl-mark {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
}

.wfl-saved,
.wfl-quiet {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfl-ctl {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.wfl-strength {
  /* Wide enough for "0.45" beside the number spinner (48px clipped it). */
  width: var(--space-9);
  height: var(--control-h-sm);
  padding: 0 var(--space-2);
  /* AppInput's ground and border. The field stays WorkflowStrengthInput,
     which keeps a half-typed number through a re-render; AppInput would not. */
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  color: inherit;
  font: inherit;
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.wfl-note {
  margin: 0;
  font-size: var(--text-sm);
}

.wfl-hint {
  flex: 1;
  min-width: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
