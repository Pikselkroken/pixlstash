<template>
  <SettingsSection title="Privacy">
    <SettingsTwoCol>
      <SettingsRow
        label="Check for updates"
        sub="Checks daily. Sends only your version and install type, anonymously."
      >
        <v-switch
          v-model="checkForUpdatesModel"
          color="primary"
          density="compact"
          hide-details
        />
      </SettingsRow>

      <!-- Deliberately independent of the update check. The ping goes to its
           own endpoint, so gating it here would invent a dependency the
           architecture does not have. -->
      <SettingsRow
        label="Send an anonymous install ID"
        sub="Lets us tell whether people keep using PixlStash rather than just downloading it. Never derived from anything about your computer."
      >
        <v-switch
          v-model="installIdModel"
          color="primary"
          density="compact"
          hide-details
        />
      </SettingsRow>
    </SettingsTwoCol>

    <SettingsFieldBlock
      label="Your install ID"
      :sub="
        identity && identity.available
          ? 'Stored on this machine, beside your server config. Nothing links a replaced ID to its successor.'
          : 'Could not be stored. Check that the server config directory is writable; the server log has the underlying error.'
      "
    >
      <div class="pv__id-row">
        <code class="pv__id">{{
          identity && identity.available ? identity.install_id : "unavailable"
        }}</code>
        <AppButton
          variant="secondary"
          :disabled="!identity || !identity.available"
          @click="copyId"
        >
          {{ copied ? "Copied" : "Copy" }}
        </AppButton>
        <AppButton
          variant="secondary"
          :loading="recreating"
          :disabled="!identity || !identity.available"
          @click="confirmOpen = true"
        >
          Recreate ID
        </AppButton>
      </div>
      <p v-if="error" class="pv__error">{{ error }}</p>
    </SettingsFieldBlock>

    <p class="pv__note">
      Never sent: your images, your tags, captions or filenames, your search
      queries, or your file paths.
    </p>

    <!-- No @accept: replacing the ID is irreversible, so it only fires from
         its own button. -->
    <AppDialog
      :open="confirmOpen"
      title="Replace your install ID?"
      size="sm"
      @close="confirmOpen = false"
    >
      <p class="pv__confirm-body">
        The current ID is discarded and a new random one takes its place.
        Nothing links the two. Any history recorded under the old ID stops
        updating.
      </p>
      <template #footer>
        <AppButton variant="secondary" @click="confirmOpen = false"
          >Cancel</AppButton
        >
        <AppButton variant="primary" :loading="recreating" @click="doRecreate"
          >Replace</AppButton
        >
      </template>
    </AppDialog>
  </SettingsSection>

  <!-- What is kept of things that are gone (#1309). A ghost is the thumbnail
       and prompt of a destroyed picture, or the name of a model not on the
       shelf. Rendered only once the server has answered; a hubless server
       answers with null counts, so it gets the setting and no purges. -->
  <SettingsSection
    v-if="ghosts"
    title="Ghosts"
    desc="A ghost is what PixlStash keeps of something that is gone: the thumbnail and prompt of a permanently deleted picture, kept with the workflow that made it so it can be made again, or the name of a model that is not on your shelf. Workflows › Ghosts lists the workflows that hold them."
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
      sub="Names and digests of models that are not on your shelf, as workflows remember them, including models from imported pictures you never added. Forgotten, the workflows still group and read as names forgotten."
    >
      <AppButton
        variant="secondary"
        :disabled="!ghosts.model_ghosts || ghostBusy"
        @click="askPurge('model')"
        >{{ PURGES.model.rowLabel(ghosts.model_ghosts) }}</AppButton
      >
    </SettingsRow>
    <p v-if="ghostError" class="pv__error" role="alert">{{ ghostError }}</p>

    <!-- No @accept: a purge is irreversible, so it only fires from its own
         button, like the install-ID replace above. -->
    <AppDialog
      :open="Boolean(purgeKind)"
      :title="purgeKind ? PURGES[purgeKind].title(purgeCount) : ''"
      size="sm"
      @close="purgeKind = null"
    >
      <p v-if="purgeKind" class="pv__confirm-body">
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
import { onMounted, computed, ref, watch } from "vue";
import { VSwitch } from "vuetify/components";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { patchUserConfig } from "../../api/config";
import { getInstallId, recreateInstallId } from "../../api/telemetry";
import {
  getGhostRetention,
  purgeModelGhosts,
  purgePictureGhosts,
  setGhostRetention,
} from "../../api/serverConfig";
import SettingsSection from "./SettingsSection.vue";
import SettingsTwoCol from "./SettingsTwoCol.vue";
import SettingsRow from "./SettingsRow.vue";
import SettingsFieldBlock from "./SettingsFieldBlock.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import Segmented from "../widgets/Segmented.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const prefs = useUserPrefsStore();
const identity = ref(null);
const recreating = ref(false);
const confirmOpen = ref(false);
const copied = ref(false);
const error = ref("");

const checkForUpdatesModel = computed({
  get: () => prefs.checkForUpdates ?? false,
  set: async (value) => {
    const next = Boolean(value);
    const previous = prefs.checkForUpdates;
    error.value = "";
    prefs.checkForUpdates = next;
    try {
      await patchUserConfig({ check_for_updates: next });
    } catch (e) {
      console.error("Failed to persist check_for_updates:", e);
      prefs.checkForUpdates = previous;
      error.value =
        "Could not save the update-check preference. Your previous choice was restored.";
    }
  },
});

const installIdModel = computed({
  get: () => prefs.telemetrySendInstallId,
  set: async (value) => {
    error.value = "";
    const saved = await prefs.saveTelemetry({
      telemetry_send_install_id: Boolean(value),
    });
    if (!saved) {
      error.value =
        "Could not save the install-ID preference. Your previous choice was kept.";
    }
  },
});

async function loadIdentity() {
  try {
    identity.value = await getInstallId();
  } catch (e) {
    console.error("Failed to read the install ID:", e);
    identity.value = { available: false, install_id: null };
  }
}

async function copyId() {
  if (!identity.value?.install_id) return;
  try {
    await navigator.clipboard.writeText(identity.value.install_id);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch (e) {
    // Clipboard access is origin- and permission-gated and fails outright on a
    // non-secure context, so say what happened rather than silently doing
    // nothing to a button the user just pressed.
    console.error("Clipboard write failed:", e);
    error.value = "Could not copy. Select the ID and copy it manually.";
  }
}

async function doRecreate() {
  recreating.value = true;
  error.value = "";
  try {
    identity.value = await recreateInstallId();
  } catch (e) {
    console.error("Failed to recreate the install ID:", e);
    error.value = "Could not create a new ID. The server log has the reason.";
  } finally {
    recreating.value = false;
    confirmOpen.value = false;
  }
}

// ── Ghosts ──────────────────────────────────────────────────────────────────
const workflowsStore = useWorkflowsStore();
const ghosts = ref(null);
const ghostBusy = ref(false);
const ghostError = ref("");
/** `"picture" | "model" | null` — which purge the confirm dialog is asking about. */
const purgeKind = ref(null);

const retentionOptions = [
  { id: "off", label: "Off" },
  { id: "covered", label: "Covered only" },
  { id: "on", label: "On" },
];

// Saving a position destroys nothing already kept (the server says so too), so
// Off must not read as a purge: somebody choosing it for privacy would believe
// the ghosts were gone.
const RETENTION_SUBS = {
  off: "From now on, none: a permanently deleted picture cannot be made again. Ghosts already kept stay until you purge them below.",
  covered:
    "Only while a picture you kept has the same workflow and prompt. Deleting the last one removes the ghosts it covered.",
  on: "Every permanently deleted picture, until you purge them below.",
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

// Fetching the ID creates one if absent, so only do it when the pane is
// actually shown rather than on app start. The ghost counts are re-read on
// every open, since a purge elsewhere changes them.
onMounted(() => {
  if (props.open) {
    loadIdentity();
    loadGhosts();
  }
});
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen && !identity.value) loadIdentity();
    if (isOpen) loadGhosts();
  },
);
</script>

<style scoped>
.pv__id-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.pv__id {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  user-select: all;
}

.pv__error {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
}

.pv__note {
  margin: var(--space-5) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.pv__confirm-body {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.75);
}
</style>
