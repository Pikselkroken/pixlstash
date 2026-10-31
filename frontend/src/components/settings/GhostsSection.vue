<template>
  <!-- What is kept of things that are gone (#1309). A ghost is the thumbnail
       and prompt of a destroyed picture, or the name of a model not on the
       shelf. Rendered only once the server has answered; a hubless server
       answers with null counts, so it gets the setting and no purges. -->
  <SettingsSection
    v-if="ghosts"
    title="Ghosts"
    desc="Leave a trace of pictures you deleted. Workflows, recipes and thumbnails. Allows recreation of deleted pictures at a fraction of the size."
  >
    <SettingsRow
      label="Keep picture ghosts"
      :sub="RETENTION_SUBS[ghosts.workflow_ghost_retention] || ''"
    >
      <Segmented
        :options="retentionOptions"
        :model-value="ghosts.workflow_ghost_retention"
        aria-label="Keep picture ghosts"
        @update:model-value="saveRetention"
      />
    </SettingsRow>

    <!-- The two purges side by side: Recovery holds three sections and
         has no height for a row each. -->
    <SettingsTwoCol>
      <SettingsRow
        v-if="ghosts.picture_ghosts != null"
        label="Picture ghosts"
        sub="Thumbnails and prompts kept for pictures deleted from this library."
      >
        <AppButton
          variant="secondary"
          :disabled="!ghosts.picture_ghosts || ghostBusy"
          @click="askPurge('picture')"
          >{{ PURGES.picture.rowLabel(ghosts.picture_ghosts) }}</AppButton
        >
      </SettingsRow>

      <SettingsRow
        v-if="ghosts.model_ghosts != null"
        label="Model ghosts"
        sub="Names and file hashes of models that are not on your shelf, including models from imported pictures you never added."
      >
        <AppButton
          variant="secondary"
          :disabled="!ghosts.model_ghosts || ghostBusy"
          @click="askPurge('model')"
          >{{ PURGES.model.rowLabel(ghosts.model_ghosts) }}</AppButton
        >
      </SettingsRow>
    </SettingsTwoCol>
    <p v-if="ghostError" class="gh__error" role="alert">{{ ghostError }}</p>

    <!-- No @accept: a purge is irreversible, so it only fires from its own
         button, like the install-ID replace under Privacy. -->
    <AppDialog
      :open="Boolean(purgeKind)"
      :title="purgeKind ? PURGES[purgeKind].title(purgeCount) : ''"
      size="sm"
      @close="purgeKind = null"
    >
      <p v-if="purgeKind" class="gh__confirm-body">
        {{ PURGES[purgeKind].body }}
      </p>
      <template #footer>
        <AppButton variant="secondary" @click="purgeKind = null"
          >Cancel</AppButton
        >
        <AppButton
          v-if="purgeKind"
          variant="danger"
          :loading="ghostBusy"
          @click="doPurge"
          >{{ PURGES[purgeKind].confirm(purgeCount) }}</AppButton
        >
      </template>
    </AppDialog>
  </SettingsSection>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import {
  getGhostRetention,
  purgeModelGhosts,
  purgePictureGhosts,
  setGhostRetention,
} from "../../api/serverConfig";
import SettingsSection from "./SettingsSection.vue";
import SettingsTwoCol from "./SettingsTwoCol.vue";
import SettingsRow from "./SettingsRow.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import Segmented from "../widgets/Segmented.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const workflowsStore = useWorkflowsStore();
const ghosts = ref(null);
const ghostBusy = ref(false);
const ghostError = ref("");
/** `"picture" | "model" | null` — which purge the confirm dialog is asking about. */
const purgeKind = ref(null);

const retentionOptions = [
  { id: "off", label: "Never" },
  { id: "covered", label: "Partially" },
  { id: "on", label: "Always" },
];

// Saving a position destroys nothing already kept (the server says so too), so
// Never must not read as a purge: somebody choosing it for privacy would believe
// the ghosts were gone.
const RETENTION_SUBS = {
  off: "From now on, none: a permanently deleted picture cannot be made again. Ghosts already kept stay until purged.",
  covered:
    "Only while you still have pictures with the same workflow and prompt. Deleting the last one clears the ghosts.",
  on: "Every permanently deleted picture, until you purge them.",
};

const plural = (n, one, many) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

// One verb per purge, the same on the row, the title and the confirm button:
// a picture ghost is purged, a model name is forgotten.
const PURGES = {
  picture: {
    rowLabel: (n) => (n ? `Purge ${n.toLocaleString()}` : "None kept"),
    title: (n) => `Purge ${plural(n, "picture ghost", "picture ghosts")}?`,
    confirm: (n) => `Purge ${plural(n, "ghost", "ghosts")}`,
    body: "Their thumbnails and prompts are destroyed, and the pictures they came from can no longer be made again. The workflows stay. This cannot be undone.",
    run: async () => {
      await purgePictureGhosts();
    },
  },
  model: {
    rowLabel: (n) => (n ? `Forget ${n.toLocaleString()}` : "None kept"),
    title: (n) => `Forget ${plural(n, "model name", "model names")}?`,
    confirm: (n) => `Forget ${plural(n, "name", "names")}`,
    body: "Workflows stop saying which models they used, for every model that is not on your shelf, including models you never added. They still group, and read as names forgotten. This cannot be undone.",
    run: async (count) => {
      await purgeModelGhosts(count);
    },
  },
};

const purgeCount = computed(() =>
  purgeKind.value === "model"
    ? ghosts.value?.model_ghosts || 0
    : ghosts.value?.picture_ghosts || 0,
);

async function loadGhosts() {
  try {
    ghosts.value = await getGhostRetention();
  } catch (e) {
    // Owner-only route: a session that cannot read it simply has no section.
    // One that could read it before keeps what it had, or a failed re-read
    // after a purge would take the section away with its own error message.
    console.warn("Could not read the ghost retention setting:", e);
  }
}

// The last position the server confirmed, and the latest one asked for while a
// save was in flight. The control is never disabled for a save: a natively
// disabled radio drops keyboard focus on the first arrow press. So a pick made
// mid-save is queued, and only the newest queued pick is sent next.
let confirmedRetention = null;
let pendingRetention = null;
let savingRetention = false;

async function saveRetention(position) {
  ghostError.value = "";
  if (!savingRetention) {
    confirmedRetention = ghosts.value.workflow_ghost_retention;
  }
  ghosts.value = { ...ghosts.value, workflow_ghost_retention: position };
  pendingRetention = position;
  if (savingRetention) return;
  savingRetention = true;
  try {
    while (pendingRetention !== null) {
      const next = pendingRetention;
      pendingRetention = null;
      try {
        const body = await setGhostRetention(next);
        confirmedRetention = body.workflow_ghost_retention;
        // A newer pick is already on screen; the next pass saves it.
        if (pendingRetention === null) ghosts.value = body;
      } catch (e) {
        console.error("Failed to save workflow_ghost_retention:", e);
        pendingRetention = null;
        ghosts.value = {
          ...ghosts.value,
          workflow_ghost_retention: confirmedRetention,
        };
        ghostError.value =
          "Could not save which ghosts to keep. Your previous choice was kept.";
      }
    }
  } finally {
    savingRetention = false;
  }
}

function askPurge(kind) {
  ghostError.value = "";
  purgeKind.value = kind;
}

async function doPurge() {
  const kind = purgeKind.value;
  if (!kind) return;
  ghostBusy.value = true;
  try {
    await PURGES[kind].run(purgeCount.value);
    // The Workflows grid holds the names and picture counts this just
    // changed, and keeps a stack's members for the whole session.
    workflowsStore.invalidate();
  } catch (e) {
    console.error(`Failed to purge ${kind} ghosts:`, e);
    ghostError.value =
      e?.response?.status === 409
        ? "The count changed since it was shown, so nothing was purged. Check the new count and try again."
        : "Could not purge. The server log has the reason.";
  } finally {
    // Re-read either way: the counts are the server's to say, and a purge
    // that failed half-way must not leave a stale number on the button.
    await loadGhosts();
    ghostBusy.value = false;
    purgeKind.value = null;
  }
}

// The counts are re-read on every open, since a purge elsewhere changes them.
onMounted(() => {
  if (props.open) loadGhosts();
});
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) loadGhosts();
  },
);
</script>

<style scoped>
.gh__error {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
}

.gh__confirm-body {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.75);
}
</style>
