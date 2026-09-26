<template>
  <li
    class="eld-row"
    :class="{
      'eld-row--deleted': row.deleted,
      'eld-row--dragging': dragging,
      'eld-row--over': over && !dragging,
    }"
    :data-row="row.id"
    @dragover.prevent="emit('dragover')"
    @dragleave="emit('dragleave')"
    @drop.prevent="emit('drop')"
  >
    <!-- Each loader is a node like the two ends, with the wire from the node
         before it ending in an arrow on its title. In a lane the title bar
         goes to a tooltip, so two lanes stay readable side by side. -->
    <span class="eld-wire" :class="{ 'eld-wire--short': compact }" aria-hidden="true"></span>
    <div class="eld-node">
      <span v-if="!compact" class="eld-node-title">{{ title }}</span>
      <div class="eld-node-body">
        <div class="eld-line">
          <!-- The handle is the drag source AND the keyboard path, and the
               keyboard path is in its name: a tooltip needs a hover a
               keyboard user does not have. The Recipes tab's idiom. -->
          <button
            class="eld-handle"
            type="button"
            data-focus="handle"
            :draggable="canReorder ? 'true' : 'false'"
            :disabled="!canReorder"
            :aria-label="`Reorder ${row.name || 'this LoRA'}: hold Alt and press the up or down arrow, or drag`"
            @dragstart="emit('dragstart', $event)"
            @dragend="emit('dragend')"
            @keydown.up.alt.prevent="emit('move', -1)"
            @keydown.down.alt.prevent="emit('move', 1)"
          >
            <Tooltip
              text="Drag to reorder, or Alt with the arrow keys"
              activator="parent"
              :describe="false"
            />
            <v-icon size="16">mdi-drag-vertical</v-icon>
          </button>

          <span class="eld-pos" aria-hidden="true">{{
            row.deleted ? "—" : position
          }}</span>

          <span v-if="row.isNew && !row.sha256" class="eld-name">
            <AppSelect
              :model-value="row.sha256"
              :label="`LoRA to add, row ${position}`"
              hide-label
              compact
              :options="pickerOptions"
              :disabled="saving"
              @update:model-value="(value) => emit('pick', value)"
            />
          </span>
          <span v-else class="eld-name">
            <Tooltip v-if="compact" :text="title" activator="parent" />
            <v-icon
              v-if="!row.onShelf && !row.isNew"
              size="16"
              class="eld-flag-glyph"
              aria-hidden="true"
              >mdi-alert-outline</v-icon
            >
            <span class="eld-name-text">{{
              row.onShelf || row.isNew ? row.name : row.fileBase
            }}</span>
            <span v-if="row.isNew" class="eld-tag">new</span>
            <span v-if="row.deleted" class="visually-hidden">, deleted</span>
          </span>

          <span
            v-if="row.deleted || !row.hasStrength"
            class="eld-strength eld-quiet"
          >
            {{ row.deleted ? "—" : "wired" }}
          </span>
          <AppInput
            v-else
            class="eld-strength"
            :model-value="row.strengthText"
            :aria-label="`Strength of ${row.name || 'this LoRA'}`"
            type="number"
            min="-10"
            max="10"
            :disabled="!editable || saving"
            :error="invalid"
            @update:model-value="(value) => emit('strength', value)"
            @keydown.stop
          />

          <!-- Delete, not ×: the entry leaves the workflow, and an × in this
               app closes things. A deleted row stays on screen with Restore
               until the dialog is saved or cancelled. -->
          <span class="eld-actions">
            <AppButton
              v-if="row.deleted"
              size="sm"
              data-focus="restore"
              :aria-label="`Restore ${row.name || 'this LoRA'}`"
              :disabled="saving"
              @click="emit('restore')"
            >
              Restore
            </AppButton>
            <template v-else>
              <!-- The keyboard's way across the fork, where drag is the
                   pointer's: the same moves, named. -->
              <v-menu v-if="moveTargets.length && canReorder" location="bottom end">
                <template #activator="{ props: menuProps }">
                  <AppBarButton
                    v-bind="menuProps"
                    icon="dots-vertical"
                    data-focus="menu"
                    :tooltip="`Move ${row.name || 'this LoRA'} to another pass`"
                    aria-haspopup="menu"
                  />
                </template>
                <div class="ctx-menu" role="menu">
                  <button
                    v-for="target in moveTargets"
                    :key="String(target.lane)"
                    class="ctx-item"
                    type="button"
                    role="menuitem"
                    :data-move="String(target.lane)"
                    @click="emit('move-to', target.lane)"
                  >
                    <span class="ctx-label-text">Move to {{ target.label }}</span>
                  </button>
                </div>
              </v-menu>
              <AppBarButton
                icon="delete-outline"
                data-focus="delete"
                :tooltip="`Delete ${row.name || 'this LoRA'}`"
                :disabled="!editable || saving"
                @click="emit('remove')"
              />
            </template>
          </span>
        </div>
        <p
          v-if="!row.onShelf && !row.isNew && !row.deleted"
          class="eld-note eld-flag"
        >
          {{ row.fileBase }} is missing from your model shelf.
        </p>
      </div>
    </div>
  </li>
</template>

<script setup>
/**
 * One loader of the Edit LoRAs chain: handle, position, name, strength,
 * delete. The dialog owns the rows and every gesture; this only draws one and
 * says what was pressed.
 */
import { VIcon, VMenu } from "vuetify/components";

import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import Tooltip from "../widgets/Tooltip.vue";

defineProps({
  row: { type: Object, required: true },
  /** The row's 1-based place on its sampler's path; none when deleted. */
  position: { type: Number, default: null },
  /** The node's class and id: its title bar, or in a lane its tooltip. */
  title: { type: String, default: "" },
  editable: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  canReorder: { type: Boolean, default: false },
  invalid: { type: Boolean, default: false },
  dragging: { type: Boolean, default: false },
  over: { type: Boolean, default: false },
  /** A lane's row: no title bar, a shorter wire. */
  compact: { type: Boolean, default: false },
  pickerOptions: { type: Array, default: () => [] },
  /** `[{lane, label}]`: where the row's menu can send it; empty, no menu. */
  moveTargets: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "dragstart",
  "dragend",
  "dragover",
  "dragleave",
  "drop",
  "move",
  "move-to",
  "pick",
  "strength",
  "remove",
  "restore",
]);
</script>
