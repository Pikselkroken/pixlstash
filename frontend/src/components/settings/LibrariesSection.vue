<script setup>
/**
 * Settings › Libraries.
 *
 * Lists the libraries this installation knows about and switches between them.
 * Adding and removing libraries is deliberately not here: those point the server
 * at folders on disk and are command-line operations in this release, which is
 * why the pane has to *teach* the CLI rather than just mention it. That panel is
 * the only discovery path for multi-library, so it carries real copy.
 *
 * Switching closes one library and opens another, so it ends in a full page
 * reload rather than a store refresh: picture ids do not mean the same thing in
 * another library, and every open view describes the old one.
 */
import { computed, ref, watch } from "vue";
import { VProgressCircular } from "vuetify/components";

import { listLibraries, setActiveLibrary } from "../../api/libraries";
import { useConfirm } from "../../composables/useConfirm";
import { copyText } from "../../utils/clipboard";
import AppButton from "../widgets/AppButton.vue";
import SettingsSection from "./SettingsSection.vue";

const props = defineProps({
  // The dialog re-fetches whenever it opens, so a library attached from the
  // terminal shows up without a restart.
  open: { type: Boolean, default: false },
});

const { confirm } = useConfirm();

const libraries = ref([]);
const canManage = ref(false);
const cliHint = ref("");
const inDocker = ref(false);
const loading = ref(false);
const loadError = ref("");
const switchingTo = ref("");
const switchError = ref("");
const copied = ref(false);

const activeLibrary = computed(() =>
  libraries.value.find((library) => library.is_active),
);

const hasOnlyOneLibrary = computed(() => libraries.value.length <= 1);

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const body = await listLibraries();
    libraries.value = body?.libraries ?? [];
    canManage.value = Boolean(body?.can_manage);
    cliHint.value = body?.cli_hint ?? "";
    inDocker.value = Boolean(body?.in_docker);
  } catch (error) {
    loadError.value =
      error?.response?.data?.detail || "Could not read the list of libraries.";
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) load();
  },
  { immediate: true },
);

async function switchTo(library) {
  switchError.value = "";
  const ok = await confirm({
    title: `Switch to ${library.name}?`,
    message:
      "PixlStash will reload. Work in progress finishes or is cancelled first.",
    confirmLabel: "Switch and reload",
  });
  if (!ok) return;

  switchingTo.value = library.uuid;
  try {
    await setActiveLibrary(library.uuid);
    // The whole app describes the library that is closing, so reload rather
    // than trying to reconcile stores against a different library.
    window.location.reload();
  } catch (error) {
    // The server stays on the library it was already using when a switch
    // fails, so the honest thing is to say so and leave the user where they are.
    switchError.value =
      error?.response?.data?.detail ||
      `Could not switch to ${library.name}. PixlStash is still using ${
        activeLibrary.value?.name ?? "the current library"
      }.`;
    switchingTo.value = "";
  }
}

async function copyHint() {
  if (!cliHint.value) return;
  await copyText(cliHint.value);
  copied.value = true;
  window.setTimeout(() => {
    copied.value = false;
  }, 2000);
}
</script>

<template>
  <div class="libraries-pane">
    <SettingsSection
      first
      title="Libraries"
      desc="A library is a folder holding your pictures and their database. PixlStash keeps one open at a time."
    >
      <div v-if="loading" class="libraries-loading">
        <v-progress-circular indeterminate size="20" width="2" />
        <span>Reading the list of libraries…</span>
      </div>

      <p v-else-if="loadError" class="libraries-error" role="alert">
        {{ loadError }}
      </p>

      <ul v-else class="libraries-list">
        <li
          v-for="library in libraries"
          :key="library.uuid"
          class="library-row"
          :class="{ 'library-row--active': library.is_active }"
        >
          <div class="library-row__text">
            <div class="library-row__name">
              {{ library.name }}
              <span v-if="library.is_active" class="library-chip">Active</span>
              <span
                v-else-if="!library.is_reachable"
                class="library-chip library-chip--warn"
                >Not found</span
              >
            </div>
            <!-- Present only for a local session: the server omits the path
                 for a remote caller so it never leaks host layout. -->
            <div
              v-if="library.path"
              class="library-row__path"
              :title="library.path"
            >
              {{ library.path }}
            </div>
            <div v-if="!library.is_reachable" class="library-row__help">
              Reconnect the drive, then reopen this tab.
            </div>
          </div>

          <div class="library-row__action">
            <AppButton
              v-if="!library.is_active"
              size="sm"
              variant="secondary"
              :disabled="!canManage || !library.is_reachable"
              :loading="switchingTo === library.uuid"
              @click="switchTo(library)"
            >
              Switch
            </AppButton>
          </div>
        </li>
      </ul>

      <p v-if="switchError" class="libraries-error" role="alert">
        {{ switchError }}
      </p>

      <!-- Visible text, not a tooltip: a disabled control has to explain
           itself somewhere a keyboard or screen-reader user will reach. -->
      <p v-if="!loading && !canManage" class="libraries-note">
        Switching libraries is only available on the machine running PixlStash,
        or over your local network or Tailscale. To allow it from anywhere, set
        <code>allow_remote_host_ops</code> in server settings.
      </p>
    </SettingsSection>

    <SettingsSection title="Adding and removing libraries">
      <p class="libraries-note">
        Libraries are added and removed from the command line in this release,
        because it points PixlStash at folders on your computer.
      </p>

      <div v-if="cliHint" class="libraries-cli">
        <code class="libraries-cli__command">{{ cliHint }}</code>
        <AppButton
          size="sm"
          variant="ghost"
          icon-left="content-copy"
          @click="copyHint"
        >
          {{ copied ? "Copied" : "Copy" }}
        </AppButton>
      </div>
      <p v-else class="libraries-note">
        Run it on the machine hosting PixlStash to see the exact command.
      </p>

      <dl class="libraries-verbs">
        <div>
          <dt>list</dt>
          <dd>Show what is attached.</dd>
        </div>
        <div>
          <dt>create</dt>
          <dd>Start a new, empty library.</dd>
        </div>
        <div>
          <dt>attach</dt>
          <dd>Register a library that already exists on disk.</dd>
        </div>
        <div>
          <dt>detach</dt>
          <dd>
            Forget one. <strong>No files are removed</strong> and nothing inside
            the folder changes.
          </dd>
        </div>
      </dl>

      <p class="libraries-note">
        Run it on the machine hosting PixlStash, signed in as the user that owns
        it.
        <template v-if="inDocker">
          Paths shown here are paths inside the container.
        </template>
      </p>

      <p v-if="hasOnlyOneLibrary" class="libraries-note">
        You have one library. Attach another to keep separate sets of pictures,
        such as client work and experiments, and switch between them here.
      </p>
    </SettingsSection>
  </div>
</template>

<style scoped>
.libraries-pane {
  display: flex;
  flex-direction: column;
}

.libraries-loading {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.6);
  padding: var(--space-3) 0;
}

.libraries-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.library-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  margin-bottom: var(--space-2);
}

.library-row--active {
  border-color: rgb(var(--v-theme-accent));
}

.library-row__text {
  min-width: 0;
}

.library-row__name {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: var(--weight-semibold);
  font-size: var(--text-sm);
}

/* Truncate from the left so the identifying tail of the path stays readable:
   the last segments are what tell two libraries apart. */
.library-row__path {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: var(--space-1);
  direction: rtl;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.library-row__help {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: var(--space-1);
}

.library-chip {
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--hover-wash);
  color: rgb(var(--v-theme-accent));
}

.library-chip--warn {
  color: rgb(var(--v-theme-warning));
}

.library-row__action {
  flex-shrink: 0;
}

.libraries-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-snug);
  margin: var(--space-2) 0 0;
}

.libraries-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  line-height: var(--leading-snug);
  margin: var(--space-2) 0 0;
}

.libraries-cli {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.libraries-cli__command {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  padding: var(--space-2) var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: var(--hover-wash);
}

.libraries-verbs {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.libraries-verbs > div {
  display: flex;
  gap: var(--space-3);
  margin-bottom: var(--space-1);
}

.libraries-verbs dt {
  flex-shrink: 0;
  width: 56px;
  font-family: var(--font-mono);
  font-weight: var(--weight-semibold);
}

.libraries-verbs dd {
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
</style>
