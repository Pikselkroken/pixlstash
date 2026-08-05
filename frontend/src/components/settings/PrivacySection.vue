<template>
  <SettingsSection title="Privacy">
    <SettingsTwoCol>
      <SettingsRow
        label="Check for updates"
        sub="Checks once a day and shows a sidebar notice when a new version is out. Sends only your app version and install type."
      >
        <v-switch
          v-model="checkForUpdatesModel"
          color="accent"
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
          color="accent"
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
        <v-btn
          size="small"
          variant="tonal"
          :disabled="!identity || !identity.available"
          @click="copyId"
        >
          {{ copied ? "Copied" : "Copy" }}
        </v-btn>
        <v-btn
          size="small"
          variant="tonal"
          :loading="recreating"
          :disabled="!identity || !identity.available"
          @click="confirmOpen = true"
        >
          Recreate ID
        </v-btn>
      </div>
      <p v-if="error" class="pv__error">{{ error }}</p>
    </SettingsFieldBlock>

    <p class="pv__note">
      Never sent: your images, your tags, captions or filenames, your search
      queries, or your file paths.
    </p>

    <v-dialog v-model="confirmOpen" max-width="420">
      <v-card class="pv__confirm">
        <h3 class="pv__confirm-title">Replace your install ID?</h3>
        <p class="pv__confirm-body">
          The current ID is discarded and a new random one takes its place.
          Nothing links the two. Any history recorded under the old ID stops
          updating.
        </p>
        <div class="pv__confirm-actions">
          <v-btn variant="tonal" @click="confirmOpen = false">Cancel</v-btn>
          <v-btn variant="tonal" :loading="recreating" @click="doRecreate"
            >Replace</v-btn
          >
        </div>
      </v-card>
    </v-dialog>
  </SettingsSection>
</template>

<script setup>
import { onMounted, computed, ref, watch } from "vue";
import { VBtn, VCard, VDialog, VSwitch } from "vuetify/components";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { patchUserConfig } from "../../api/config";
import { getInstallId, recreateInstallId } from "../../api/telemetry";
import SettingsSection from "./SettingsSection.vue";
import SettingsTwoCol from "./SettingsTwoCol.vue";
import SettingsRow from "./SettingsRow.vue";
import SettingsFieldBlock from "./SettingsFieldBlock.vue";

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

// Fetching the ID creates one if absent, so only do it when the pane is
// actually shown rather than on app start.
onMounted(() => {
  if (props.open) loadIdentity();
});
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen && !identity.value) loadIdentity();
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
  color: rgb(var(--v-theme-error));
}

.pv__note {
  margin: var(--space-5) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.pv__confirm {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.pv__confirm-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.pv__confirm-body {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.pv__confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}
</style>
