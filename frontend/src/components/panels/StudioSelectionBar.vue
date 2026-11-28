<template>
  <!-- The selection pill every surface that selects wears (`.selbar`, App.css):
       the count, which opens Select all and Clear; the verbs as round icon
       buttons, their words in the tooltip and the right-click menu; the one
       that destroys last, in the error colour, before Clear. The host floats
       it over the end of its list (`.selbar-float`). -->
  <div
    v-if="count"
    class="selbar"
    role="toolbar"
    :aria-label="`Selected ${plural}`"
    :data-testid="testid"
  >
    <v-menu v-model="countOpen" location="top" origin="bottom center" :offset="8">
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          class="selbar-count"
          type="button"
          aria-haspopup="menu"
          :aria-expanded="countOpen"
          data-verb="count"
        >
          <span>{{ count.toLocaleString() }} {{ count === 1 ? noun : plural }}</span>
          <v-icon size="16" class="selbar-chevron">mdi-menu-down</v-icon>
        </button>
      </template>
      <div class="ctx-menu" role="menu" tabindex="-1" @keydown="onMenuKeydown">
        <button
          v-if="selectAll"
          class="ctx-item"
          type="button"
          role="menuitem"
          data-verb="select-all"
          @click="emit('select-all')"
        >
          <v-icon class="ctx-icon">mdi-select-all</v-icon>
          <span class="ctx-label-text">Select all shown</span>
          <span class="ctx-shortcut">Ctrl+A</span>
        </button>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          data-verb="clear-menu"
          @click="emit('clear')"
        >
          <v-icon class="ctx-icon">mdi-selection-off</v-icon>
          <span class="ctx-label-text">Clear selection</span>
          <span class="ctx-shortcut">Esc</span>
        </button>
      </div>
    </v-menu>

    <span class="selbar-sep"></span>

    <AppBarButton
      v-for="verb in safe"
      :key="verb.key"
      shape="round"
      :icon="verb.icon"
      :data-verb="verb.key"
      :aria-label="verb.label"
      :disabled="!!verb.reason"
      :tooltip="verb.reason || verb.label"
      @click="emit('verb', verb.key)"
    />

    <!-- Project: the grid's own picker, in a popover, as the shelf's Assign. -->
    <v-menu
      v-if="project"
      v-model="projectOpen"
      :close-on-content-click="false"
      location="top"
      origin="bottom center"
      :offset="8"
    >
      <template #activator="{ props: menuProps }">
        <AppBarButton
          shape="round"
          icon="folder-move-outline"
          v-bind="menuProps"
          data-verb="project"
          aria-label="Project"
          tooltip="Put in a project, or take out of one"
        />
      </template>
      <div class="ctx-menu">
        <AddToEntityControl
          type="project"
          placement="right"
          :subject-ids="project.subjectIds"
          :membership="project.membership"
          @selected="emit('project', $event)"
        />
      </div>
    </v-menu>

    <span class="selbar-sep"></span>

    <AppBarButton
      v-for="verb in danger"
      :key="verb.key"
      shape="round"
      danger
      :icon="verb.icon"
      :data-verb="verb.key"
      :aria-label="verb.label"
      :disabled="!!verb.reason"
      :tooltip="verb.reason || verb.label"
      @click="emit('verb', verb.key)"
    />
    <AppBarButton
      shape="round"
      icon="selection-off"
      data-verb="clear"
      aria-label="Clear selection"
      tooltip="Clear selection (Esc)"
      @click="emit('clear')"
    />
  </div>
</template>

<script setup>
/**
 * The Studio's selection pill (#2819): the grid's cards and an item's
 * pictures. The verbs are the host's, `{ key, icon, label, danger?, reason? }`
 * (`icon` without its `mdi-`), the same list its right-click menu shows.
 */
import { computed, ref } from "vue";
import { VIcon, VMenu } from "vuetify/components";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import { onMenuKeydown } from "../../utils/menuKeyboard";

const props = defineProps({
  count: { type: Number, default: 0 },
  noun: { type: String, required: true },
  plural: { type: String, required: true },
  verbs: { type: Array, default: () => [] },
  /** `{ subjectIds, membership }` for the Project popover, or null. */
  project: { type: Object, default: null },
  selectAll: { type: Boolean, default: true },
  testid: { type: String, default: "studio-selbar" },
});
const emit = defineEmits(["verb", "clear", "select-all", "project"]);

const countOpen = ref(false);
const projectOpen = ref(false);
const safe = computed(() => props.verbs.filter((v) => !v.danger));
const danger = computed(() => props.verbs.filter((v) => v.danger));
</script>
