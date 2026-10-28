<template>
  <div class="mf">
    <div class="mf-head">
      <h3 class="mf-title">Model folders</h3>
      <!-- One unit that never wraps inside itself: when it does not fit beside
           the title it drops under it whole, so the pill stays directly above
           the card its dashed link points into. -->
      <div class="mf-head__actions">
        <!-- Shown only while no output root is set, because there is exactly one
           to set: ai-toolkit writes every run under a single folder. Once it is
           registered its row in the list carries the Forget that undoes it. -->
        <AppButton
          v-if="!store.sourceFolder"
          size="sm"
          variant="secondary"
          v-bind="blockedAttrs(addReason)"
          @click="openPicker(SOURCE_KIND)"
        >
          <template #icon="{ size }"><AiToolkitIcon :size="size" /></template>
          Set ai-toolkit folder
        </AppButton>
        <!-- The Downloads folder is a header object, not a row: it lists nothing,
           and what it does is send new models into the default folder below,
           which the dashed link under the pill points at. -->
        <span v-if="store.downloadsFolder" class="mf-dl-wrap">
          <!-- Both verbs are on the host tier, so a remote session gets no menu
               rather than one whose every item fails; the note says why. -->
          <v-menu
            v-model="downloadsMenuOpen"
            :disabled="Boolean(remoteReason)"
            location="bottom end"
            origin="top end"
            :offset="6"
          >
            <template #activator="{ props: menuProps }">
              <button
                v-bind="{ ...menuProps, ...blockedAttrs(remoteReason) }"
                type="button"
                class="mf-dl"
                aria-haspopup="menu"
                :aria-label="`Downloads folder, watching ${store.downloadsFolder.path}`"
              >
                <Tooltip
                  :text="store.downloadsFolder.path"
                  activator="parent"
                />
                <v-icon size="16">mdi-tray-arrow-down</v-icon>
                <span class="mf-dl__name">{{
                  basename(store.downloadsFolder.path)
                }}</span>
                <span class="mf-dl__dot" aria-hidden="true" />
                <span class="mf-dl__state">watching</span>
                <v-icon size="12">mdi-chevron-down</v-icon>
              </button>
            </template>
            <div
              class="ctx-menu"
              role="menu"
              tabindex="-1"
              @keydown="onMenuKeydown"
            >
              <button
                class="ctx-item"
                :class="{ 'ctx-item--disabled': changeDownloadsReason }"
                type="button"
                role="menuitem"
                :aria-disabled="changeDownloadsReason ? 'true' : undefined"
                @click="onChangeDownloads"
              >
                <Tooltip :text="changeDownloadsReason" activator="parent" />
                <v-icon class="ctx-icon">mdi-folder-swap-outline</v-icon>
                <span class="ctx-label-text">Change…</span>
              </button>
              <button
                class="ctx-item"
                :class="{
                  'ctx-item--disabled': forgetReason(store.downloadsFolder),
                }"
                type="button"
                role="menuitem"
                :aria-disabled="
                  forgetReason(store.downloadsFolder) ? 'true' : undefined
                "
                @click="onStopWatching"
              >
                <Tooltip
                  :text="forgetReason(store.downloadsFolder)"
                  activator="parent"
                />
                <v-icon class="ctx-icon">mdi-eye-off-outline</v-icon>
                <span class="ctx-label-text">Stop watching</span>
              </button>
            </div>
          </v-menu>
          <span class="mf-flow" aria-hidden="true" />
        </span>
        <AppButton
          v-else
          size="sm"
          variant="secondary"
          icon-left="tray-arrow-down"
          v-bind="blockedAttrs(addReason)"
          @click="openPicker(DOWNLOADS_KIND)"
        >
          Monitor download folder
        </AppButton>
        <AppButton
          size="sm"
          variant="primary"
          icon-left="folder-plus-outline"
          v-bind="blockedAttrs(addReason)"
          @click="openPicker(null)"
        >
          Add folder
        </AppButton>
        <HelpTip
          v-if="addReason"
          :reason="addReason"
          label="Why adding a folder is unavailable"
        />
      </div>
    </div>

    <p v-if="store.loading && !store.loaded" class="mf-state">
      Reading the registered folders…
    </p>
    <p v-else-if="store.error" class="mf-state" role="alert">
      {{ store.error }}
    </p>

    <template v-else>
      <!-- The default folder is pinned out of the list, so nobody has to match a
           setting's label to a row below it. It appears in one place only. -->
      <section class="mf-default" aria-labelledby="mf-default-label">
        <div class="mf-default__head">
          <span id="mf-default-label" class="section-label"
            >Default folder</span
          >
          <span class="mf-default__from">
            <v-icon v-if="store.downloadsFolder" size="12"
              >mdi-tray-arrow-down</v-icon
            >
            {{
              store.downloadsFolder
                ? "New models from Downloads land here"
                : "New models you add land here"
            }}
          </span>
        </div>
        <div class="mf-row mf-row--default">
          <span class="mf-row__label">
            <FolderPath
              v-if="store.defaultFolder"
              :path="store.defaultFolder.path"
            />
            <span v-else class="mf-row__none">None set</span>
          </span>
          <span class="mf-row__meta">{{
            store.defaultFolder ? folderStats(store.defaultFolder) : ""
          }}</span>
          <v-menu
            v-model="defaultMenuOpen"
            :disabled="Boolean(changeDefaultReason)"
            location="bottom end"
            origin="top end"
            :offset="6"
          >
            <template #activator="{ props: menuProps }">
              <AppButton
                v-bind="{ ...menuProps, ...blockedAttrs(changeDefaultReason) }"
                size="sm"
                variant="secondary"
                aria-haspopup="menu"
                :tooltip="changeDefaultReason"
              >
                Change<v-icon size="16" class="mf-chevron"
                  >mdi-chevron-down</v-icon
                >
              </AppButton>
            </template>
            <div
              class="ctx-menu"
              role="menu"
              tabindex="-1"
              @keydown="onMenuKeydown"
            >
              <button
                v-for="folder in defaultChoices"
                :key="folder.id"
                class="ctx-item"
                type="button"
                role="menuitem"
                @click="onDefaultChange(folder)"
              >
                <span class="ctx-label-text mf-choice">
                  <span>{{ basename(folder.path) }}</span>
                  <span class="mf-choice__path">{{ folder.path }}</span>
                </span>
              </button>
            </div>
          </v-menu>
        </div>
      </section>

      <!-- Where a model downloaded for a workflow goes: the default folder
           until the owner picks another. Pinned like the default, so it too
           appears in one place only. -->
      <section class="mf-default" aria-labelledby="mf-download-label">
        <div class="mf-default__head">
          <span id="mf-download-label" class="section-label"
            >Download folder</span
          >
          <span class="mf-default__from">
            <v-icon size="12">mdi-download</v-icon>
            Models downloaded for a workflow land here
          </span>
        </div>
        <div class="mf-row mf-row--default" data-testid="mf-download-folder">
          <span class="mf-row__label">
            <FolderPath
              v-if="store.downloadFolder"
              :path="store.downloadFolder.path"
            />
            <span v-else class="mf-row__none">None set</span>
            <span
              v-if="store.downloadFolder?.is_default"
              class="mf-chip"
              >Same as default</span
            >
          </span>
          <span class="mf-row__meta">{{
            store.downloadFolder && !store.downloadFolder.is_default
              ? folderStats(store.downloadFolder)
              : ""
          }}</span>
          <v-menu
            v-model="downloadMenuOpen"
            :disabled="Boolean(changeDownloadReason)"
            location="bottom end"
            origin="top end"
            :offset="6"
          >
            <template #activator="{ props: menuProps }">
              <AppButton
                v-bind="{ ...menuProps, ...blockedAttrs(changeDownloadReason) }"
                size="sm"
                variant="secondary"
                aria-haspopup="menu"
                :tooltip="changeDownloadReason"
                data-testid="mf-download-change"
              >
                Change<v-icon size="16" class="mf-chevron"
                  >mdi-chevron-down</v-icon
                >
              </AppButton>
            </template>
            <div
              class="ctx-menu"
              role="menu"
              tabindex="-1"
              @keydown="onMenuKeydown"
            >
              <button
                v-for="folder in downloadChoices"
                :key="folder.id"
                class="ctx-item"
                type="button"
                role="menuitem"
                @click="onDownloadChange(folder)"
              >
                <span class="ctx-label-text mf-choice">
                  <span>{{ basename(folder.path) }}</span>
                  <span class="mf-choice__path">{{ folder.path }}</span>
                </span>
              </button>
            </div>
          </v-menu>
        </div>
      </section>

      <!-- The one scroller. A plain list of rows carrying verbs, not a
           `role="listbox"`: nothing here is selected, and real `<button>`s in a
           `<ul>` give Tab and Enter for free. -->
      <div v-if="listed.length" class="mf-well">
        <ul class="mf-list" role="list">
          <li
            v-for="folder in listed"
            :key="folder.id"
            class="mf-row"
            :aria-busy="isScanning(folder) ? 'true' : undefined"
          >
            <span class="mf-row__label">
              <FolderPath :path="folder.path" />
              <!-- Whose folder it is, resolved by the server: the HuggingFace
                   cache is only PixlStash's while it holds nothing else, and a
                   StabilityMatrix folder is often reached through a symlink. -->
              <span v-if="folder.tool" class="mf-chip">{{ folder.tool }}</span>
            </span>
            <span class="mf-row__meta">{{ folderStats(folder) }}</span>
            <!-- One action per row: Move on a folder PixlStash owns, which has no
                 association to dissolve, and Forget on one the owner added.
                 `relocatable` and not `movable === 'root_only'`: the InsightFace
                 packs say that too, so the server is asked. A row with neither
                 keeps the slot, so the column holds. -->
            <AppButton
              v-if="folder.relocatable"
              icon-only
              variant="ghost"
              icon-left="folder-move-outline"
              :tooltip="
                relocateReason(folder) ||
                `Move ${basename(folder.path)} to a different location. Every file in it is copied, verified and removed from here.`
              "
              :aria-label="`Move ${folder.path} to a different location`"
              v-bind="blockedAttrs(relocateReason(folder))"
              @click="onRelocate(folder)"
            />
            <AppButton
              v-else
              :class="{ 'mf-hidden': !canForget(folder) }"
              icon-only
              variant="ghost"
              icon-left="folder-off-outline"
              :tooltip="
                forgetReason(folder) ||
                `Forget ${basename(folder.path)}. Nothing on disk is deleted.`
              "
              :aria-label="`Forget ${folder.path}`"
              v-bind="blockedAttrs(forgetReason(folder))"
              @click="onForget(folder)"
            />
          </li>
        </ul>
      </div>

      <p v-if="store.loaded && !hasOwnFolders" class="mf-note">
        No folders of your own yet. Add the folder where you keep your LoRAs and
        checkpoints and they will appear on the shelf.
      </p>

      <div class="mf-foot">
        <span>{{ footSummary }}</span>
        <!-- No Rescan: the server scans a folder whenever a model in it
             changes, so this only says that it is doing so. -->
        <span role="status">{{ scanSummary }}</span>
      </div>
    </template>

    <!-- One visible note, not one per button: it is the `aria-describedby`
         target of every control the tier blocks, so the reason a keyboard user
         lands on is rendered rather than living in a tooltip. -->
    <p v-if="!canManage" :id="REMOTE_NOTE_ID" class="mf-note">
      Adding, moving, and forgetting model folders is only available
      on the machine running PixlStash, or over your local network or Tailscale.
      To allow it from anywhere, set <code>allow_remote_host_ops</code> in
      server settings.
    </p>

    <!-- The shipped host-path picker, shared by every verb that needs a host
         path. `registeredPaths` is what stops the 409 rather than reporting it,
         and it is right for Move too - moving into a folder the owner already
         registered would swallow their association into PixlStash's. -->
    <FolderBrowser
      :open="browseOpen"
      :registered-paths="store.registeredPaths"
      already-registered-label="Already a model folder"
      @select="onPicked"
      @close="closeBrowser"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import AppButton from "../widgets/AppButton.vue";
import HelpTip from "../widgets/HelpTip.vue";
import Tooltip from "../widgets/Tooltip.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import FolderPath from "./FolderPath.vue";
import {
  DOWNLOADS_KIND,
  SOURCE_KIND,
  forgetModelFolder,
} from "../../api/modelFolders";
import {
  REMOTE_NOTE_ID,
  blockedAttrs,
  useModelFolderGates,
} from "../../composables/useModelFolderGates";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import {
  basename,
  countLabel,
  useModelFoldersStore,
} from "../../stores/useModelFoldersStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { formatModelSize } from "../../utils/modelShelf";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useModelFoldersStore();
const moves = useModelMovesStore();
const {
  canManage,
  remoteReason,
  addReason,
  isScanning,
  canForget,
  forgetReason,
  relocateReason,
} = useModelFolderGates();

const browseOpen = ref(false);
const downloadsMenuOpen = ref(false);
const defaultMenuOpen = ref(false);
const downloadMenuOpen = ref(false);
// Which verb opened the picker. One browser serves them all, so the pick has
// to be told apart: Move empties `relocating` into it, otherwise it registers
// a folder of `addingKind` (null for a plain Add). `replacing` is the Downloads
// folder a Change… swaps out.
const relocating = ref(null);
const addingKind = ref(null);
const replacing = ref(null);

/** Every folder but the ones the header and the pinned cards already show. */
const listed = computed(() =>
  store.folders.filter(
    (folder) =>
      folder.kind !== DOWNLOADS_KIND &&
      !folder.is_default &&
      !folder.is_download_target,
  ),
);

/** The folders that can take over as the default, minus the one that is. */
const defaultChoices = computed(() =>
  store.defaultCandidates.filter((folder) => !folder.is_default),
);

/** The folders downloads can go to instead, minus the one they go to. */
const downloadChoices = computed(() =>
  store.defaultCandidates.filter((folder) => !folder.is_download_target),
);

/** `user` and `source` rows only; `managed` and `foreign` are PixlStash's own. */
const hasOwnFolders = computed(() =>
  store.folders.some(
    (folder) => folder.kind === "user" || folder.kind === "source",
  ),
);

const footSummary = computed(() => {
  const shown = store.folders.filter((f) => f.kind !== DOWNLOADS_KIND);
  const bytes = shown.reduce(
    (sum, f) => sum + (Number(f.present_bytes) || 0),
    0,
  );
  const count = `${shown.length} ${shown.length === 1 ? "folder" : "folders"}`;
  return bytes > 0 ? `${count} · ${formatModelSize(bytes)}` : count;
});

const scanSummary = computed(() => {
  const n = store.scanningIds.size;
  if (!n) return "Kept up to date as models are added or removed";
  return `Scanning ${n} ${n === 1 ? "folder" : "folders"}…`;
});

/** Why the default cannot be changed here, or "" when it can. */
const changeDefaultReason = computed(
  () =>
    remoteReason.value ||
    (defaultChoices.value.length
      ? ""
      : "No other folder can be the default yet. Add one first."),
);

/** Why the download folder cannot be changed here, or "" when it can. */
const changeDownloadReason = computed(
  () =>
    remoteReason.value ||
    (downloadChoices.value.length
      ? ""
      : "No other folder can take downloads yet. Add one first."),
);

/** A swap is a forget then an add, so either refusal blocks it. */
const changeDownloadsReason = computed(
  () =>
    addReason.value ||
    (store.downloadsFolder ? forgetReason(store.downloadsFolder) : ""),
);

/** "5 files · 811.2 MB"; the size is left off at zero rather than claiming 0 B. */
function folderStats(folder) {
  const count = countLabel(folder.file_count);
  return folder.present_bytes > 0
    ? `${count} · ${formatModelSize(folder.present_bytes)}`
    : count;
}

function openPicker(kind) {
  if (addReason.value) return;
  relocating.value = null;
  addingKind.value = kind;
  browseOpen.value = true;
}

function onChangeDownloads() {
  downloadsMenuOpen.value = false;
  if (changeDownloadsReason.value) return;
  openPicker(DOWNLOADS_KIND);
  replacing.value = store.downloadsFolder;
}

function onStopWatching() {
  downloadsMenuOpen.value = false;
  onForget(store.downloadsFolder);
}

function onDefaultChange(folder) {
  defaultMenuOpen.value = false;
  if (remoteReason.value || folder.is_default) return;
  store.setDefault(folder);
}

function onDownloadChange(folder) {
  downloadMenuOpen.value = false;
  if (remoteReason.value || folder.is_download_target) return;
  store.setDownloadTarget(folder);
}

function onRelocate(folder) {
  if (relocateReason(folder)) return;
  relocating.value = folder;
  browseOpen.value = true;
}

/**
 * Swap the Downloads folder for another. The server holds one at most, so the
 * old one is forgotten first; it was never scanned, so nothing leaves the
 * shelf. If the new one is then refused, the old one is put back rather than
 * leaving nothing watched.
 */
async function replaceDownloads(previous, path) {
  try {
    await forgetModelFolder(previous.id);
  } catch (err) {
    useNoticeStore().push({
      level: "error",
      text: errorDetail(err) || "Could not change the Downloads folder.",
    });
    return;
  }
  if (await store.add({ path, kind: DOWNLOADS_KIND })) return;
  await store.add({ path: previous.path, kind: DOWNLOADS_KIND });
}

async function onPicked(path) {
  const folder = relocating.value;
  if (!folder) {
    if (addReason.value) return;
    const kind = addingKind.value || "user";
    const previous = replacing.value;
    if (previous && kind === DOWNLOADS_KIND) {
      await replaceDownloads(previous, path);
      return;
    }
    await store.add({ path, kind });
    return;
  }
  if (relocateReason(folder)) return;
  // The job outlives this pane; the shelf's own progress bar watches it.
  await moves.relocate(folder.id, path);
}

function closeBrowser() {
  browseOpen.value = false;
  relocating.value = null;
  addingKind.value = null;
  replacing.value = null;
}

function onForget(folder) {
  if (!folder || forgetReason(folder)) return;
  store.forget(folder);
}

// The registry is host data, re-read when Settings opens so its rows are
// current. A scan running when it closes keeps polling in the store.
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) store.refresh();
  },
  { immediate: true },
);
</script>

<style scoped>
/* The pane fills Settings' content box and the list is the one thing in it
   that scrolls, so Settings itself never grows a scrollbar for this pane. */
.mf {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.mf-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.mf-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.mf-head__actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.mf-dl-wrap {
  position: relative;
}

.mf-dl {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 28px;
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  transition: background var(--dur-1) var(--ease-standard);
}

.mf-dl:hover {
  background: var(--hover-wash);
}

.mf-dl__name {
  max-width: 16ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mf-dl__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgb(var(--v-theme-success));
}

.mf-dl__state {
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

/* The dashed link from the Downloads pill down into the default card. */
.mf-flow {
  position: absolute;
  left: 50%;
  top: 100%;
  height: var(--space-4);
  border-left: 1px dashed rgba(var(--v-theme-on-surface), 0.7);
  pointer-events: none;
}

.mf-state {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
  padding: var(--space-5) 0;
}

.mf-default {
  flex-shrink: 0;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  padding-top: var(--space-3);
}

.mf-default__head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-3) 0 var(--space-4);
}

.mf-default__from {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
}

/* Shrinks to the folders it holds and scrolls only once it outgrows the pane. */
.mf-well {
  flex: 0 1 auto;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-background));
}

.mf-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

/* One line per folder: path and owner chip, then count and size in a
   right-aligned column that lines up down the list, then its one action. */
.mf-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 10em 28px;
  column-gap: var(--space-3);
  align-items: center;
  min-height: 40px;
  padding: var(--space-1) var(--space-2) var(--space-1) var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  transition: background var(--dur-1) var(--ease-standard);
}

.mf-list .mf-row:last-child {
  border-bottom: 0;
}

.mf-list .mf-row:hover {
  background: var(--hover-wash);
}

.mf-row--default {
  grid-template-columns: minmax(0, 1fr) 10em auto;
  border-bottom: 0;
}

.mf-row__label {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.mf-row__none {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.mf-row__meta {
  justify-self: end;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* A permanent, never-changing label is not attention, so it takes the neutral
   border rather than the accent §4 keeps scarce. */
.mf-chip {
  flex-shrink: 0;
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgb(var(--v-theme-on-surface));
  white-space: nowrap;
}

.mf-chevron {
  margin-left: var(--space-1);
}

.mf-choice {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.mf-choice__path {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

/* `visibility`, never `v-if`: the box is kept so the column holds, and only
   this also drops the control from the tab order and the accessibility tree. */
.mf-hidden {
  visibility: hidden;
}

.mf-foot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-variant-numeric: tabular-nums;
}

.mf-note {
  flex-shrink: 0;
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.7);
  max-width: 70ch;
}

.mf-note code {
  font-family: var(--font-mono);
}

/* Below 480px Settings stacks its rail on top and the pane is narrow: the
   count column gives way so the path keeps its room. */
@media (max-width: 480px) {
  .mf-row,
  .mf-row--default {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .mf-row__meta {
    display: none;
  }
}
</style>
