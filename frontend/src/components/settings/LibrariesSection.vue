<script setup>
/**
 * Settings › Libraries.
 *
 * The whole lifecycle: list, add, rename, switch, stop using. It used to be a
 * list and a lesson in the CLI, because adding a library points the server at a
 * folder on disk and that was a terminal job. The routes exist now, so the CLI
 * panel stays but is demoted to reference - the same things from a terminal,
 * for a script or a cron job, or anyone who prefers one.
 *
 * **Nothing in this pane can remove a picture or a folder.** `Stop using this`
 * deregisters; the files stay where they are and the row is kept, so the share
 * links pointing at that library revive when the folder is added again. The
 * copy says so at the moment of the decision rather than in a help page.
 *
 * The active library has no `Stop using this` - switch away first. That refusal
 * is the registry's, not this dialog's; the item is hidden because offering a
 * gesture that always fails is worse than not offering it.
 *
 * Switching closes one library and opens another, so it ends in a full page
 * reload rather than a store refresh: picture ids do not mean the same thing in
 * another library, and every open view describes the old one.
 */
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { VCard, VDialog, VMenu, VProgressCircular } from "vuetify/components";

import {
  LIBRARIES_DOCUMENTATION_URL,
  detachLibrary,
  renameLibrary,
} from "../../api/libraries";
import { useConfirm } from "../../composables/useConfirm";
import {
  useLibrariesStore,
  useLibrarySwitchStore,
} from "../../stores/useLibrariesStore";
import { useFolderMappingStore } from "../../stores/useFolderMappingStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { copyText } from "../../utils/clipboard";
import { errorDetail } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import LibraryLayoutDialog from "./LibraryLayoutDialog.vue";
import SettingsSection from "./SettingsSection.vue";

const props = defineProps({
  // The dialog re-fetches whenever it opens, so a library attached from the
  // terminal shows up without a restart.
  open: { type: Boolean, default: false },
});

// `Choose a layout…` opens LibraryLayoutDialog over this pane. The layout is a
// property of the *open* library - the routes are `/server-config/...`, which
// is whichever library is active - so the item is on the active row only.
const layoutDialogOpen = ref(false);

const { confirm } = useConfirm();
const librariesStore = useLibrariesStore();
const switchStore = useLibrarySwitchStore();
const noticeStore = useNoticeStore();
const mappingStore = useFolderMappingStore();
const {
  libraries,
  canManage,
  cliHint,
  inDocker,
  loading,
  loadError,
  hasLoadedSuccessfully,
  activeLibrary,
} = storeToRefs(librariesStore);
const { targetLibrary, overlayOpen } = storeToRefs(switchStore);
const copiedCommand = ref("");
let copyResetTimer = 0;
// The four commands carry an absolute interpreter path each, so inline they
// dominated a panel whose actual job is listing and switching libraries. They
// are reference material consulted once, not part of that flow, so they live
// behind a button.
const commandsOpen = ref(false);
const expandedPaths = ref(new Set());
const openMenuUuid = ref("");
/** The library being renamed, or null. Held rather than looked up by uuid so a
    refresh mid-edit cannot swap the dialog's subject under the typing. */
const renaming = ref(null);
const renameValue = ref("");
const renameError = ref("");
const renameBusy = ref(false);
const renameInput = ref(null);
const busyUuid = ref("");

const showOneLibraryPrimer = computed(
  () => hasLoadedSuccessfully.value && libraries.value.length === 1,
);

const cliCommands = computed(() => {
  if (!cliHint.value) return [];
  const base = cliHint.value.replace(/\s+list\s*$/, "");
  return [
    { verb: "list", syntax: "list", description: "Show what is attached." },
    {
      verb: "create",
      syntax: "create <folder>",
      description: "Start a new, empty library.",
    },
    {
      verb: "attach",
      syntax: "attach <folder>",
      description: "Register a library that already exists on disk.",
    },
    {
      verb: "rename",
      syntax: "rename <name> <new name>",
      description: "Change a library's label. Nothing on disk is renamed.",
    },
    {
      verb: "detach",
      syntax: "detach <name>",
      description:
        "Forget one. No files are removed and nothing inside the folder changes.",
    },
    {
      verb: "backup",
      syntax: "backup <name> <destination>",
      description:
        "Write the library and the hub to one archive, even while it is open. Read it back with restore.",
    },
  ].map((item) => ({ ...item, command: `${base} ${item.syntax}` }));
});

const copyAnnouncement = computed(() =>
  copiedCommand.value ? `Copied ${copiedCommand.value}` : "",
);

const detachCommand = computed(() =>
  cliHint.value
    ? cliHint.value.replace(/\s+list\s*$/, " detach <name>")
    : "",
);

// Which shell these commands are written for, when it is not "any of them".
// The Windows desktop declares a PowerShell command - a leading `&` call
// operator - because no single string runs in both cmd.exe and PowerShell
// (issue #1058), and a command pasted into the wrong one fails with an error
// that names neither. Read off the command we are about to show rather than
// from a new API field: the hint IS the deployment's own answer, so the two
// can never disagree.
const needsPowerShell = computed(() => cliHint.value?.startsWith("& ") ?? false);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) librariesStore.refresh();
  },
  { immediate: true },
);

async function switchTo(library, event) {
  // DOM Event.currentTarget is cleared as soon as synchronous dispatch ends.
  // Save the invoking control before the confirmation awaits so failure can
  // restore focus to the exact Switch button.
  const trigger = event?.currentTarget ?? null;
  const shareCount = Number(activeLibrary.value?.active_share_links ?? 0);
  const ok = await confirm({
    title: `Switch to ${library.name}?`,
    message:
      "PixlStash will reload. Work in progress finishes or is cancelled first.",
    warning:
      shareCount > 0
        ? `${shareCount} share ${shareCount === 1 ? "link points" : "links point"} at ${activeLibrary.value?.name}. ${shareCount === 1 ? "It stops" : "They stop"} working until you switch back.`
        : "",
    confirmLabel: "Switch and reload",
  });
  if (!ok) return;
  await switchStore.begin(
    library,
    activeLibrary.value,
    trigger,
  );
}

async function startRename(library) {
  openMenuUuid.value = "";
  renaming.value = library;
  renameValue.value = library.name;
  renameError.value = "";
  await nextTick();
  renameInput.value?.focus();
  renameInput.value?.select();
}

async function commitRename() {
  const library = renaming.value;
  const name = renameValue.value.trim();
  if (!library || renameBusy.value) return;
  if (!name || name === library.name) {
    renaming.value = null;
    return;
  }
  renameBusy.value = true;
  renameError.value = "";
  try {
    await renameLibrary(library.uuid, name);
    renaming.value = null;
    await librariesStore.refresh();
  } catch (error) {
    // Stay open on the name that was refused. The server's reason names the
    // library already holding it, which is the one thing that tells the owner
    // what to type instead.
    renameError.value = errorDetail(error) || "Could not rename that library.";
  } finally {
    renameBusy.value = false;
  }
}

async function stopUsing(library) {
  openMenuUuid.value = "";
  const shareCount = Number(library.active_share_links ?? 0);
  const ok = await confirm({
    title: `Stop using "${library.name}"?`,
    message:
      `PixlStash forgets it. Every picture and folder inside ${library.path ?? "that folder"} ` +
      "stays exactly where it is. Its tags, scores and people live in that " +
      "folder too, so adding it again later brings them back.",
    warning:
      shareCount > 0
        ? `${shareCount} share ${shareCount === 1 ? "link" : "links"} ` +
          `${shareCount === 1 ? "points" : "point"} at it. ` +
          `${shareCount === 1 ? "It stops" : "They stop"} working until you add ` +
          "the folder again, and then works again - nothing is revoked."
        : "",
    confirmLabel: "Forget it",
  });
  if (!ok) return;

  busyUuid.value = library.uuid;
  try {
    await detachLibrary(library.uuid);
    await librariesStore.refresh();
    noticeStore.success(`Forgot "${library.name}". No files were removed.`, {
      key: "libraries-detached",
    });
  } catch (error) {
    noticeStore.error(
      errorDetail(error) || `Could not stop using ${library.name}.`,
      { key: "libraries-detach" },
    );
  } finally {
    busyUuid.value = "";
  }
}

function togglePath(libraryUuid) {
  const next = new Set(expandedPaths.value);
  if (next.has(libraryUuid)) next.delete(libraryUuid);
  else next.add(libraryUuid);
  expandedPaths.value = next;
}

async function copyCommand(command) {
  if (await copyText(command)) {
    copiedCommand.value = command;
    window.clearTimeout(copyResetTimer);
    copyResetTimer = window.setTimeout(() => {
      if (copiedCommand.value === command) copiedCommand.value = "";
    }, 2000);
    return;
  }
  // The clipboard refuses on an insecure context and the execCommand fallback
  // can be denied too. Claiming "Copied" then leaves the user pasting whatever
  // was on the clipboard before, and this command is the one thing on the
  // screen nobody can retype from memory. The shared notice surface owns the
  // failure, as it does for the identical copy in OverlayDescriptionPanel.
  noticeStore.error(
    "Couldn't copy the command. Select the text and press Ctrl+C, or Command-C on a Mac.",
    { key: "libraries-cli-copy" },
  );
}

// Reopening the dialog otherwise shows "Copied" - and announces it - for a
// button nobody pressed this time.
watch(commandsOpen, (isOpen) => {
  if (!isOpen) copiedCommand.value = "";
});

onUnmounted(() => window.clearTimeout(copyResetTimer));
</script>

<template>
  <div class="libraries-pane">
    <SettingsSection
      first
      title="Libraries"
      desc="A library is a folder holding your pictures and their database. PixlStash keeps one open at a time."
    >
      <div v-if="canManage" class="libraries-toolbar">
        <AppButton size="sm" variant="primary" @click="mappingStore.openWizard()">
          + Add a library…
        </AppButton>
      </div>

      <div v-if="loading" class="libraries-loading" role="status" aria-live="polite">
        <v-progress-circular indeterminate size="20" width="2" />
        <span>Reading the list of libraries…</span>
      </div>

      <div v-else-if="loadError" class="libraries-error" role="alert">
        <span>{{ loadError }}</span>
        <AppButton size="sm" variant="secondary" @click="librariesStore.refresh()">
          Retry
        </AppButton>
      </div>

      <ul v-else class="libraries-list">
        <li
          v-for="library in libraries"
          :key="library.uuid"
          class="library-row"
          :class="{ 'library-row--active': library.is_active }"
        >
          <div class="library-row__text">
            <div class="library-row__name">
              <span class="library-row__label" :title="library.name">
                {{ library.name }}
              </span>
              <span v-if="library.is_active" class="library-chip">Active</span>
              <span
                v-else-if="!library.is_reachable"
                class="library-chip library-chip--warn"
                >Not found</span
              >
            </div>
            <!-- Present only for a local session: the server omits the path
                 for a remote caller so it never leaks host layout. -->
            <button
              v-if="library.path"
              type="button"
              class="library-row__path"
              :class="{
                'library-row__path--expanded': expandedPaths.has(library.uuid),
              }"
              :title="library.path"
              :aria-label="`${expandedPaths.has(library.uuid) ? 'Collapse' : 'Show'} full folder path for ${library.name}: ${library.path}`"
              :aria-expanded="expandedPaths.has(library.uuid)"
              @click="togglePath(library.uuid)"
            >
              {{ library.path }}
            </button>
            <div v-if="!library.is_reachable" class="library-row__help">
              Reconnect its storage, then reopen this tab.
              <template v-if="cliHint">
                If it is no longer needed, run
                <code>{{ detachCommand }}</code>.
              </template>
              <template v-else>
                The documentation explains how to detach it from the host.
              </template>
              Detaching removes only this entry; no files are removed.
            </div>
          </div>

          <div class="library-row__action">
            <AppButton
              v-if="!library.is_active"
              size="sm"
              variant="secondary"
              :disabled="
                !canManage || !library.is_reachable || busyUuid === library.uuid
              "
              :loading="overlayOpen && targetLibrary?.uuid === library.uuid"
              @click="switchTo(library, $event)"
            >
              Switch
            </AppButton>

            <!-- The management verbs are all on the locality tier, so a remote
                 session gets no menu at all rather than one whose every item
                 fails. The visible note under the list explains why. -->
            <v-menu
              v-if="canManage"
              :model-value="openMenuUuid === library.uuid"
              location="bottom end"
              origin="top end"
              :offset="6"
              @update:model-value="
                (isOpen) => (openMenuUuid = isOpen ? library.uuid : '')
              "
            >
              <template #activator="{ props: menuProps }">
                <button
                  v-bind="menuProps"
                  type="button"
                  class="library-row__more"
                  :disabled="busyUuid === library.uuid"
                  aria-haspopup="menu"
                  :aria-expanded="openMenuUuid === library.uuid"
                  :aria-label="`More actions for ${library.name}`"
                >
                  ⋯
                </button>
              </template>
              <ul class="library-menu" role="menu">
                <li v-if="!library.is_active" role="none">
                  <button
                    type="button"
                    role="menuitem"
                    class="library-menu__item"
                    :disabled="!library.is_reachable"
                    @click="
                      openMenuUuid = '';
                      switchTo(library, $event);
                    "
                  >
                    Open this library
                  </button>
                </li>
                <li role="none">
                  <button
                    type="button"
                    role="menuitem"
                    class="library-menu__item"
                    @click="startRename(library)"
                  >
                    Rename…
                  </button>
                </li>
                <!-- Only on the active library: the layout routes address the
                     open one, so offering this on a row that is not open would
                     silently edit a different library's folders. -->
                <li v-if="library.is_active" role="none">
                  <button
                    type="button"
                    role="menuitem"
                    class="library-menu__item"
                    @click="
                      openMenuUuid = '';
                      layoutDialogOpen = true;
                    "
                  >
                    Choose a layout…
                  </button>
                </li>
                <!-- Absent on the active library on purpose: detaching it is
                     refused by the registry, and an item that can only fail is
                     worse than no item. -->
                <li v-if="!library.is_active" role="none">
                  <button
                    type="button"
                    role="menuitem"
                    class="library-menu__item library-menu__item--separated"
                    @click="stopUsing(library)"
                  >
                    Stop using this…
                  </button>
                </li>
              </ul>
            </v-menu>
          </div>
        </li>
      </ul>

      <!-- Visible text, not a tooltip: a disabled control has to explain
           itself somewhere a keyboard or screen-reader user will reach. -->
      <p v-if="!loading && !canManage" class="libraries-note">
        Adding, renaming, switching and removing libraries is only available on
        the machine running PixlStash, or over your local network or Tailscale,
        because it points the server at folders on that machine. To allow it
        from anywhere, set <code>allow_remote_host_ops</code> in server
        settings.
      </p>
    </SettingsSection>

    <SettingsSection title="The same things from a terminal">
      <p class="libraries-note">
        Everything above can also be done from the command line, on the machine
        hosting PixlStash.
      </p>

      <AppButton
        v-if="cliCommands.length"
        size="sm"
        variant="secondary"
        icon-left="console"
        @click="commandsOpen = true"
      >
        Show the commands
      </AppButton>
      <p v-else class="libraries-note">
        Open the documentation on the machine hosting PixlStash for the command
        appropriate to that installation. Host paths and command details are
        not shown to remote sessions.
      </p>

      <!-- Guidance and the documentation link stay in the section rather than
           moving into the dialog: they are exactly what a remote session needs,
           and that session never gets a dialog to open. -->
      <p class="libraries-note">
        <a
          :href="LIBRARIES_DOCUMENTATION_URL"
          target="_blank"
          rel="noopener noreferrer"
        >Open the PixlStash command-line documentation</a>.
      </p>

      <p class="libraries-note">
        Run it on the machine hosting PixlStash, signed in as the user that owns
        it.
        <template v-if="needsPowerShell">
          Use PowerShell (for example, in Windows Terminal); these commands do not run in
          the older Command Prompt.
        </template>
        <template v-if="inDocker">
          Paths shown here are paths inside the container.
        </template>
      </p>

      <p v-if="showOneLibraryPrimer" class="libraries-note">
        You have one library. Add another to keep separate sets of pictures -
        client work and experiments, say - and switch between them here.
      </p>
    </SettingsSection>

    <v-dialog v-model="commandsOpen" max-width="640">
      <v-card class="libraries-cli-dialog">
        <h3 class="libraries-cli-dialog__title">The same things from a terminal</h3>

        <ul class="libraries-cli-list">
          <li v-for="item in cliCommands" :key="item.verb" class="libraries-cli">
            <div class="libraries-cli__text">
              <code class="libraries-cli__command">{{ item.command }}</code>
              <span>{{ item.description }}</span>
            </div>
            <AppButton
              class="libraries-cli__copy"
              size="sm"
              variant="ghost"
              icon-left="content-copy"
              :title="`Copy ${item.syntax} command`"
              @click="copyCommand(item.command)"
            >
              {{ copiedCommand === item.command ? "Copied" : "Copy" }}
            </AppButton>
          </li>
        </ul>

        <div class="libraries-cli-dialog__actions">
          <AppButton size="sm" variant="secondary" @click="commandsOpen = false">
            Close
          </AppButton>
        </div>
      </v-card>
    </v-dialog>

    <AppDialog
      :open="Boolean(renaming)"
      :title="`Rename ${renaming?.name ?? ''}`"
      subtitle="Changes the label only. Nothing on disk is renamed."
      :width="440"
      @close="renaming = null"
      @accept="commitRename"
    >
      <AppInput
        ref="renameInput"
        v-model="renameValue"
        class="libraries-rename__field"
        label="Name"
        :disabled="renameBusy"
        @enter="commitRename"
      />
      <p v-if="renameError" class="libraries-error libraries-rename__error" role="alert">
        {{ renameError }}
      </p>
      <template #footer>
        <AppButton size="sm" variant="secondary" @click="renaming = null">
          Cancel
        </AppButton>
        <AppButton
          class="libraries-rename__commit"
          size="sm"
          variant="primary"
          :loading="renameBusy"
          :disabled="!renameValue.trim()"
          @click="commitRename"
        >
          Rename
        </AppButton>
      </template>
    </AppDialog>

    <LibraryLayoutDialog
      :open="layoutDialogOpen"
      @close="layoutDialogOpen = false"
    />

    <p class="visually-hidden" role="status" aria-live="polite">
      {{ copyAnnouncement }}
    </p>
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
  color: rgba(var(--v-theme-on-surface), 0.65);
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
  background: rgba(var(--v-theme-accent), 0.08);
}

.library-row__text {
  flex: 1;
  min-width: 0;
}

.library-row__name {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: var(--weight-semibold);
  font-size: var(--text-sm);
  min-width: 0;
}

.library-row__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Truncate from the left so the identifying tail of the path stays readable:
   the last segments are what tell two libraries apart. */
.library-row__path {
  display: block;
  width: 100%;
  padding: 0;
  font-size: var(--text-xs);
  font-family: var(--font-ui);
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin-top: var(--space-1);
  direction: rtl;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.library-row__path:hover {
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
}

.library-row__path--expanded {
  direction: ltr;
  overflow: visible;
  overflow-wrap: anywhere;
  white-space: normal;
}

.library-row__help {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin-top: var(--space-1);
}

.library-chip {
  flex-shrink: 0;
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-accent));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-accent), 0.12);
  color: rgb(var(--v-theme-on-surface));
}

.library-chip--warn {
  border-color: rgb(var(--v-theme-warning));
  background: rgba(var(--v-theme-warning), 0.12);
  color: rgb(var(--v-theme-on-surface));
}

.library-row__action {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.libraries-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--space-3);
}

.library-row__more {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
  line-height: 1;
  cursor: pointer;
}

.library-row__more:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.library-row__more:disabled {
  opacity: 0.5;
  cursor: default;
}

.library-menu {
  list-style: none;
  margin: 0;
  padding: var(--space-2);
  min-width: 200px;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
}

.library-menu__item {
  display: block;
  width: 100%;
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
  cursor: pointer;
}

.library-menu__item:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.library-menu__item:disabled {
  opacity: 0.5;
  cursor: default;
}

/* The destructive-sounding one is set apart, the way the artboard sets it
   apart: it is the only item in this menu whose consequence is not obvious
   from its name. */
.library-menu__item--separated {
  margin-top: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-border));
  border-radius: 0;
}

.libraries-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  line-height: var(--leading-snug);
  margin: var(--space-2) 0 0;
}

.libraries-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-error));
  background: rgb(var(--v-theme-error));
  line-height: var(--leading-snug);
  margin: var(--space-2) 0 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
}

.libraries-cli-dialog {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.libraries-cli-dialog__title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.libraries-cli-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

.libraries-cli-list {
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
}

.libraries-cli {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.libraries-cli__text {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: var(--space-1);
  color: rgba(var(--v-theme-on-surface), 0.65);
  font-size: var(--text-xs);
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
  background: rgb(var(--v-theme-background));
}

.libraries-cli__copy {
  min-width: 92px;
}

.libraries-note a {
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
  text-underline-offset: 0.15em;
}

.libraries-note a:hover {
  text-decoration-thickness: 2px;
}

@media (max-width: 799px) {
  .library-row,
  .libraries-cli {
    align-items: stretch;
    flex-direction: column;
  }

  .library-row__action,
  .libraries-cli__copy {
    align-self: flex-end;
  }

  .libraries-cli__command {
    white-space: normal;
    overflow-wrap: anywhere;
  }
}
</style>
