<template>
  <!-- The pill while hand-made SETS are selected (#1520). The same floating
       object as the file pill (`ShelfSelectionBar`): count first, then the two
       ways into what the set has done (Show workflows, Show pictures), Rename,
       and `⋯` for the rest. Hide sits before the Clear, and no set verb that
       destroys anything is on the pill itself. None of these touches a file. -->
  <div
    v-if="store.selectedSets.length"
    class="selbar"
    role="toolbar"
    aria-label="Selected workflow sets"
  >
    <span class="selbar-count">{{ countLabel }}</span>
    <span class="selbar-sep"></span>
    <AppBarButton
      shape="round"
      icon="graph-outline"
      data-verb="show-workflows"
      :disabled="!openWorkflowTarget"
      :tooltip="showWorkflowsTitle"
      @click="openWorkflow"
    >
      Show workflows
    </AppBarButton>
    <AppBarButton
      shape="round"
      icon="image-multiple-outline"
      data-verb="show-pictures"
      :disabled="!pictureWorkflows.length"
      :tooltip="showPicturesTitle"
      @click="showPictures"
    >
      Show pictures
    </AppBarButton>
    <AppBarButton
      shape="round"
      icon="pencil-outline"
      data-verb="rename-set"
      :disabled="store.selectedSets.length !== 1"
      :tooltip="
        store.selectedSets.length === 1
          ? 'Rename (F2)'
          : 'Rename one set at a time.'
      "
      @click="emit('rename')"
    >
      Rename
    </AppBarButton>

    <!-- Duplicate, merge, reset, keep separate and delete: the set verbs the
         pill does not carry as words. Same gates as the context menu. -->
    <v-menu
      v-model="moreOpen"
      :close-on-content-click="false"
      location="top end"
      origin="bottom end"
      :offset="8"
    >
      <template #activator="{ props: menuProps }">
        <AppBarButton
          shape="round"
          icon="dots-horizontal"
          v-bind="menuProps"
          data-verb="more"
          aria-label="More actions"
          aria-haspopup="menu"
          :aria-expanded="moreOpen"
          tooltip="More…"
        />
      </template>
      <div
        class="ctx-menu shelf-menu"
        role="menu"
        tabindex="-1"
        @keydown="onMenuKeydown"
      >
        <template v-for="(row, index) in moreRows" :key="index">
          <div v-if="row.sep" class="ctx-sep"></div>
          <button
            v-else
            class="ctx-item"
            :class="{
              'ctx-item--disabled': row.disabled,
              'ctx-item--danger': row.danger,
            }"
            type="button"
            role="menuitem"
            :disabled="row.disabled"
            :data-verb="row.key"
            @click="verb(row.verb, row.payload)"
          >
            <Tooltip v-if="row.title" :text="row.title" activator="parent" />
            <v-icon class="ctx-icon">{{ row.icon }}</v-icon>
            <span class="ctx-label-text">{{ row.label }}</span>
          </button>
        </template>
      </div>
    </v-menu>

    <span class="selbar-sep"></span>

    <AppBarButton
      v-if="hide"
      shape="round"
      :icon="hide.icon.replace('mdi-', '')"
      data-verb="hide-sets"
      :aria-label="hide.label"
      :tooltip="hide.label"
      @click="emit('hide-sets')"
    />
    <AppBarButton
      shape="round"
      icon="close"
      data-verb="clear"
      aria-label="Clear selection (Esc)"
      tooltip="Clear selection (Esc)"
      @click="clear"
    />
  </div>

  <!-- The set's context menu: right-click, the Menu key or Shift+F10 on a
       hand-made card. -->
  <v-menu
    v-model="contextOpen"
    :target="contextAt"
    location="bottom end"
    origin="top start"
    :offset="2"
  >
    <div
      class="ctx-menu shelf-menu"
      role="menu"
      tabindex="-1"
      @keydown="onMenuKeydown"
    >
      <button
        v-if="store.selectedSets.length === 1"
        class="ctx-item"
        type="button"
        role="menuitem"
        @click="verb('rename')"
      >
        <v-icon class="ctx-icon">mdi-pencil-outline</v-icon>
        <span class="ctx-label-text">Rename</span>
        <span class="ctx-shortcut">F2</span>
      </button>
      <button
        v-if="store.selectedSets.length === 1"
        class="ctx-item"
        type="button"
        role="menuitem"
        @click="verb('duplicate')"
      >
        <v-icon class="ctx-icon">mdi-content-duplicate</v-icon>
        <span class="ctx-label-text">Duplicate set</span>
      </button>
      <button
        v-if="hide"
        class="ctx-item"
        type="button"
        role="menuitem"
        data-verb="hide-sets"
        @click="verb('hide-sets')"
      >
        <v-icon class="ctx-icon">{{ hide.icon }}</v-icon>
        <span class="ctx-label-text">{{ hide.label }}</span>
      </button>
      <!-- Merge (#1972): several selected sets, or one set and each other
           set on its checkpoint. Disabled with the reason when it cannot. -->
      <button
        v-if="merge"
        class="ctx-item"
        :class="{ 'ctx-item--disabled': !merge.enabled }"
        type="button"
        role="menuitem"
        data-verb="merge-sets"
        :disabled="!merge.enabled"
        @click="verb('merge', merge)"
      >
        <Tooltip :text="merge.reason" activator="parent" />
        <v-icon class="ctx-icon">mdi-call-merge</v-icon>
        <span class="ctx-label-text">{{ merge.label }}</span>
      </button>
      <template v-else-if="one">
        <button
          v-for="partner in partners"
          :key="partner.id"
          class="ctx-item"
          type="button"
          role="menuitem"
          data-verb="merge-sets"
          @click="
            verb('merge', { into: one, others: [partner] })
          "
        >
          <Tooltip
            :text="`Merges “${handMadeName(partner)}” into this set. No file is touched.`"
            activator="parent"
          />
          <v-icon class="ctx-icon">mdi-call-merge</v-icon>
          <span class="ctx-label-text"
            >Merge with “{{ handMadeName(partner) }}”</span
          >
        </button>
        <button
          v-if="!partners.length"
          class="ctx-item ctx-item--disabled"
          type="button"
          role="menuitem"
          disabled
        >
          <v-icon class="ctx-icon">mdi-call-merge</v-icon>
          <span class="ctx-label-text">{{ noPartner }}</span>
        </button>
      </template>
      <!-- The template offer (#1999), for the one set the menu is about. -->
      <template v-if="offered">
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          @click="verb('merge-offer')"
        >
          <v-icon class="ctx-icon">mdi-plus</v-icon>
          <span class="ctx-label-text">Review models to add…</span>
        </button>
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          @click="verb('keep-separate')"
        >
          <v-icon class="ctx-icon">mdi-call-split</v-icon>
          <span class="ctx-label-text">Keep separate</span>
        </button>
      </template>
      <button
        v-if="one && changed"
        class="ctx-item"
        type="button"
        role="menuitem"
        data-verb="reset-set"
        @click="verb('reset')"
      >
        <v-icon class="ctx-icon">mdi-backup-restore</v-icon>
        <span class="ctx-label-text">Reset to automatic</span>
      </button>
      <button
        class="ctx-item"
        type="button"
        role="menuitem"
        @click="verb('delete')"
      >
        <v-icon class="ctx-icon">mdi-layers-remove</v-icon>
        <span class="ctx-label-text">{{
          store.selectedSets.length === 1 ? "Delete set" : "Delete sets"
        }}</span>
        <span class="ctx-shortcut">Del</span>
      </button>
    </div>
  </v-menu>
</template>

<script setup>
/**
 * The verb surface for selected hand-made workflow sets (#1520).
 *
 * Emits and runs nothing itself, like `ShelfSelectionBar`: the view owns the
 * rename dialog and the store owns the writes and their receipts.
 */
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { VIcon } from "vuetify/components";

import { useContextMenuAt } from "../../composables/useContextMenuAt";
import { useWorkflowPictures } from "../../composables/useWorkflowPictures";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { activityOf, combinationsOfSet } from "../../utils/setActivity";
import {
  handMadeName,
  hideSetsVerb,
  mergePartners,
  mergeVerb,
  canReset,
  setCheckpoint,
} from "../../utils/workflowSets";
import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** Every selected card from pictures and hand-made sets: what Hide takes. */
  sets: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "rename",
  "duplicate",
  "delete",
  "hide-sets",
  // `{into, others}`: the set that stays and the ones merged into it.
  "merge",
  "reset",
  // The template offer (#1999).
  "merge-offer",
  "keep-separate",
]);

const store = useModelShelfStore();

/** Hide over the selected cards, as the pill's button and the menu's row. */
const hide = computed(() => hideSetsVerb(props.sets));

const { contextOpen, contextAt, openContextMenu } = useContextMenuAt();
const moreOpen = ref(false);

/** "2 selected", the count the pill leads with. */
const countLabel = computed(() => `${store.selectedSets.length} selected`);

/** The one set the menu is about, or null with several selected. */
const one = computed(() =>
  store.selectedSets.length === 1 ? store.selectedSets[0] : null,
);

/** Merge over several selected sets, into the one last clicked. */
const merge = computed(() =>
  mergeVerb(store.selectedSets, store.mergeTarget),
);

/** The other sets on the one selected set's checkpoint. */
const partners = computed(() =>
  one.value ? mergePartners(one.value, store.shownHandMadeSets) : [],
);

const noPartner = computed(() => {
  const checkpoint = setCheckpoint(one.value);
  return checkpoint
    ? `No other set uses ${checkpoint.name}`
    : "Merge needs a checkpoint";
});

/** The one selected set carries a template offer (#1999). */
const offered = computed(() => Boolean(one.value?.offer));

/** The one selected set carries changes Reset to automatic would undo. */
const changed = computed(() => Boolean(one.value) && canReset(one.value));

/**
 * The `⋯` rows: the set verbs the pill has no word for, in the context menu's
 * order and under its gates, Delete last.
 */
const moreRows = computed(() => {
  const rows = [];
  rows.push({
    key: "duplicate-set",
    verb: "duplicate",
    icon: "mdi-content-duplicate",
    label: "Duplicate set",
    disabled: !one.value,
    title: one.value
      ? "Duplicate set: a copy with the same models. No file is touched."
      : "Duplicate one set at a time.",
  });
  if (merge.value) {
    rows.push({
      key: "merge-sets",
      verb: "merge",
      payload: merge.value,
      icon: "mdi-call-merge",
      label: merge.value.label,
      disabled: !merge.value.enabled,
      title: merge.value.reason,
    });
  } else if (one.value) {
    for (const partner of partners.value) {
      rows.push({
        key: "merge-sets",
        verb: "merge",
        payload: { into: one.value, others: [partner] },
        icon: "mdi-call-merge",
        label: `Merge with “${handMadeName(partner)}”`,
        title: `Merges “${handMadeName(partner)}” into this set. No file is touched.`,
      });
    }
    if (!partners.value.length) {
      rows.push({
        key: "merge-sets",
        icon: "mdi-call-merge",
        label: noPartner.value,
        disabled: true,
      });
    }
  }
  if (offered.value) {
    rows.push({
      key: "merge-offer",
      verb: "merge-offer",
      icon: "mdi-plus",
      label: "Review models to add…",
    });
    rows.push({
      key: "keep-separate",
      verb: "keep-separate",
      icon: "mdi-call-split",
      label: "Keep separate",
    });
  }
  if (one.value && changed.value) {
    rows.push({
      key: "reset-set",
      verb: "reset",
      icon: "mdi-backup-restore",
      label: "Reset to automatic",
    });
  }
  rows.push({ sep: true });
  rows.push({
    key: "delete-set",
    verb: "delete",
    icon: "mdi-layers-remove",
    label: store.selectedSets.length === 1 ? "Delete set" : "Delete sets",
    danger: true,
    title: "Delete set (Del). No file is touched, and it can be undone.",
  });
  return rows;
});

/**
 * What the selected sets have run in, as the Workflows screen counts it: one
 * entry per workflow, most pictures first. Several sets read their union, each
 * combination once, so a picture is not counted twice.
 */
const pictureWorkflows = computed(() => {
  const combinations = new Set(
    groups.value.flatMap((group) =>
      combinationsOfSet(group, store.workflowSets.combinations),
    ),
  );
  return activityOf([...combinations]).workflows;
});

/** The sets' hand-made groups, which carry the combinations a set covers. */
const groups = computed(() =>
  store.selectedSets
    .map((set) => store.handMadeGroups.find((g) => g.key === `hand:${set.id}`))
    .filter(Boolean),
);

/** The workflow Show workflows opens: one set's most-pictured, else nothing. */
const openWorkflowTarget = computed(() =>
  store.selectedSets.length === 1 ? (pictureWorkflows.value[0] ?? null) : null,
);

const showWorkflowsTitle = computed(() => {
  if (store.selectedSets.length !== 1)
    return "Select one set to open its workflow.";
  if (!openWorkflowTarget.value)
    return "This set has not run in any workflow yet.";
  return `Open ${openWorkflowTarget.value.name || "its workflow"}, the one its pictures came from most.`;
});

const showPicturesTitle = computed(() =>
  pictureWorkflows.value.length
    ? "These are the pictures of the workflows it ran in."
    : "No workflow has run with this set, so it has no pictures yet.",
);

const router = useRouter();
const { showWorkflowsPictures } = useWorkflowPictures();

function openWorkflow() {
  const target = openWorkflowTarget.value;
  if (!target) return;
  router.push({ name: "workflows", query: { workflow: target.id } });
}

function showPictures() {
  showWorkflowsPictures(pictureWorkflows.value);
}

function clear() {
  store.clearSetSelection();
}

function verb(name, payload) {
  contextOpen.value = false;
  moreOpen.value = false;
  emit(name, payload);
}

defineExpose({ openContextMenu });
</script>
