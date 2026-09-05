<script setup>
/**
 * PixlStash Views - this library's sets, people and projects as folders of
 * LINKS to the real files, so they can be opened in a file manager and pointed
 * at other tools.
 *
 * The copy is load-bearing and deliberate: nothing here duplicates a picture,
 * no original moves, and deleting the whole folder loses nothing. Views are
 * *additional* to the folders the owner already keeps - if this ever reads as a
 * replacement for their tree, the feature has lost the point it exists for.
 *
 * Saving IS rebuilding. The tree is a full re-derive and costs a fraction of a
 * second even for a large library, so there is one button rather than a save
 * and a separate rebuild, and no "keep it up to date" mode to go stale.
 *
 * Gated behind `isReadOnly === false` at the tab level in UserSettingsDialog.
 */
import { computed, ref, watch } from "vue";
import { VIcon, VSwitch } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsInfoCard from "./SettingsInfoCard.vue";
import SettingsRow from "./SettingsRow.vue";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { getViewsSettings, setViewsSettings } from "../../api/serverConfig";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const KIND_LABELS = {
  people: "People",
  sets: "Sets",
  projects: "Projects",
};

// Views is on the same LOCAL_OWNER_ONLY tier as the library-management routes
// beside it (backend §16.3), so the pane already has the answer: `can_manage`
// comes back with the registry listing. Reading it rather than discovering the
// same fact from a 403 keeps this pane speaking with one voice, and spares a
// remote owner a request that was always going to fail.
const libraries = useLibrariesStore();

const loading = ref(false);
/** Set once the settings have been read, so the watcher does not re-fetch. */
const loaded = ref(false);
const saving = ref(false);
const error = ref("");
/** The route refused us even though `can_manage` said yes. Display only. */
const refused = ref(false);
/**
 * The gate: the registry's own answer, which is known before any request. Kept
 * separate from `refused` on purpose - a gate that also consulted `refused`
 * could never retry, because clearing `refused` happens inside the fetch the
 * gate was blocking.
 */
const blocked = computed(
  () => libraries.hasLoadedSuccessfully && !libraries.canManage,
);
const unavailable = computed(() => blocked.value || refused.value);
const browseOpen = ref(false);

const root = ref(null);
const availableKinds = ref([]);
/** Which kinds the switches are currently showing, keyed by kind. */
const enabled = ref({});
const lastPublish = ref(null);

const isOn = computed(() => Boolean(root.value));
const chosenKinds = computed(() =>
  availableKinds.value.filter((kind) => enabled.value[kind]),
);

/**
 * Toggling a kind republishes straight away, like every other switch in this
 * dialog. A "save later" model would need a dirty indicator this pane does not
 * have, and its failure mode is the quiet one: a user unticks Sets, closes the
 * dialog, and nothing has changed on disk or in the settings.
 */
function toggleKind(kind, value) {
  enabled.value[kind] = value;
  save(root.value);
}

function applySettings(body) {
  root.value = body.views_root || null;
  availableKinds.value = body.available_kinds || [];
  const on = new Set(body.kinds || []);
  enabled.value = Object.fromEntries(
    availableKinds.value.map((kind) => [kind, on.has(kind)]),
  );
  if (body.last_publish) lastPublish.value = body.last_publish;
}

// Vuetify dialogs stay mounted after the first open, so onMounted would fire
// only once - fetch on the open transition instead (the house pattern).
// `unavailable` is watched alongside it because the registry read that answers
// it is in flight when the pane opens: a pane that only watched `open` would
// stay blank for a local owner whose `can_manage` landed a moment later.
watch(
  [() => props.open, blocked],
  async ([isOpen, cannot]) => {
    if (!isOpen || cannot || loaded.value) return;
    loading.value = true;
    error.value = "";
    // Cleared per attempt, not only set on failure: a session that was refused
    // once - a remote owner who then reached the machine, or a policy change -
    // would otherwise stay stuck showing the locality notice for ever.
    refused.value = false;
    try {
      applySettings(await getViewsSettings());
      loaded.value = true;
    } catch (err) {
      // The backstop for the locality rule: `can_manage` said yes and the route
      // refused anyway. A remote owner gets the pane's sentence rather than a
      // raw permission error on controls they cannot use.
      refused.value = err?.response?.status === 403;
      error.value = refused.value
        ? ""
        : errorDetail(err) || err?.message || "Could not read the views settings.";
    } finally {
      loading.value = false;
    }
  },
  { immediate: true },
);

async function save(nextRoot) {
  saving.value = true;
  error.value = "";
  try {
    applySettings(await setViewsSettings(nextRoot, nextRoot ? chosenKinds.value : []));
  } catch (err) {
    // The server left the settings untouched, so the refused folder is not the
    // recorded one - re-read rather than leave the pane showing what was tried.
    error.value = errorDetail(err) || err?.message || "Could not publish the views.";
    try {
      applySettings(await getViewsSettings());
    } catch {
      // The read failing too is already covered by the error above.
    }
  } finally {
    saving.value = false;
  }
}

function chooseFolder(path) {
  browseOpen.value = false;
  if (path) save(path);
}

const publishedSummary = computed(() => {
  const report = lastPublish.value;
  if (!report) return "";
  const kind = report.link_mode === "hardlink" ? "hard links" : "symbolic links";
  return `${report.links} ${kind} in ${report.folders} folders.`;
});
</script>

<template>
  <div class="views-pane">
    <SettingsSection
      title="PixlStash Views"
      desc="Your sets, people and projects, as folders you can open in your file manager and point other tools at. They are links, not copies: nothing is duplicated, no original moves, and deleting the whole folder loses nothing."
      first
    >
      <!-- The same locality rule as the library controls above, and
           deliberately the same wording, because it is the same answer. -->
      <SettingsInfoCard v-if="unavailable">
        Publishing views is only available on the machine running PixlStash, or
        over your local network or Tailscale, because it writes folders on that
        machine.
      </SettingsInfoCard>
      <template v-else>
      <SettingsInfoCard>
        Views are <strong>additional</strong> to the folders you already keep.
        PixlStash never moves, renames or copies a picture to build this tree, and
        one picture in three projects appears in three view folders while its one
        real file stays exactly where you put it.
      </SettingsInfoCard>

      <SettingsRow
        label="Views folder"
        :sub="root || 'Not published. Choose a folder outside your library to turn views on.'"
      >
        <div class="views-actions">
          <AppButton
            variant="secondary"
            size="sm"
            icon-left="mdi-folder-outline"
            :disabled="loading || saving"
            @click="browseOpen = true"
          >
            {{ isOn ? "Change…" : "Choose…" }}
          </AppButton>
          <AppButton
            v-if="isOn"
            variant="ghost"
            size="sm"
            :disabled="loading"
            :loading="saving"
            @click="save(null)"
          >
            Turn off
          </AppButton>
        </div>
      </SettingsRow>

      <div v-if="error" class="settings-error">{{ error }}</div>
      </template>
    </SettingsSection>

    <SettingsSection
      v-if="isOn && !unavailable"
      title="What gets published"
      desc="Saving rebuilds the tree. It is a full re-derive and takes a moment, so there is nothing to keep in sync by hand."
    >
      <v-switch
        v-for="kind in availableKinds"
        :key="kind"
        v-model="enabled[kind]"
        color="accent"
        density="compact"
        hide-details
        :disabled="saving"
        :label="KIND_LABELS[kind] || kind"
        @update:model-value="(value) => toggleKind(kind, value)"
      />
      <div class="views-publish">
        <AppButton
          variant="primary"
          size="sm"
          icon-left="mdi-refresh"
          :loading="saving"
          @click="save(root)"
        >
          Rebuild now
        </AppButton>
        <span v-if="publishedSummary" class="views-summary">{{ publishedSummary }}</span>
      </div>
      <SettingsInfoCard v-if="lastPublish?.skipped_unlinkable?.length">
        <v-icon size="15">mdi-alert-outline</v-icon>
        These folders are incomplete - at least one picture could not be linked
        into each of them:
        {{ lastPublish.skipped_unlinkable.join(", ") }}. A hard link cannot span
        two drives, so this is what a library split across disks looks like when
        symbolic links are unavailable - on Windows they need administrator
        rights or Developer Mode.
      </SettingsInfoCard>
      <SettingsInfoCard v-if="lastPublish?.kept_by_owner?.length">
        <v-icon size="15">mdi-alert-outline</v-icon>
        These are yours, not links, so the rebuild left them exactly where they
        are: {{ lastPublish.kept_by_owner.join(", ") }}. PixlStash never deletes
        a file whose only copy is in a view folder, but it will not keep one up
        to date either - move it somewhere you control.
      </SettingsInfoCard>
      <SettingsInfoCard v-if="lastPublish?.skipped_missing">
        {{ lastPublish.skipped_missing }} pictures were skipped because their
        file is not on disk where the library records it.
      </SettingsInfoCard>
    </SettingsSection>

    <FolderBrowser
      :open="browseOpen"
      :allow-create-folder="true"
      :initial-path="root"
      @select="chooseFolder"
      @close="browseOpen = false"
    />
  </div>
</template>

<style scoped>
.views-actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.views-publish {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  margin-top: var(--space-3);
}

.views-summary {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.settings-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  margin-top: var(--space-2);
}
</style>
