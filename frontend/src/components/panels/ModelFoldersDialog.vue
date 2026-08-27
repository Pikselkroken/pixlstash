<template>
  <AppDialog
    :open="open"
    title="Model folders"
    :width="720"
    @close="emit('close')"
  >
    <template #header-right>
      <!-- Shown only while no output root is set, because there is exactly one
           to set: ai-toolkit writes every run under a single folder. Once it is
           registered this button has nothing left to do, and the row it created
           carries the Forget that undoes it. A second button rather than a
           split menu, so the ordinary verb keeps its own shape and its own
           position whichever state this is in. -->
      <AppButton
        v-if="!store.sourceFolder"
        size="sm"
        variant="secondary"
        v-bind="blockedAttrs(addReason, REMOTE_NOTE_ID)"
        @click="onAddSource"
      >
        <template #icon="{ size }"><AiToolkitIcon :size="size" /></template>
        Set ai-toolkit folder
      </AppButton>
      <AppButton
        size="sm"
        variant="primary_green"
        icon-left="folder-plus-outline"
        v-bind="blockedAttrs(addReason, REMOTE_NOTE_ID)"
        @click="onAdd"
      >
        Add folder
      </AppButton>
      <HelpTip :reason="addReason" label="Why adding a folder is unavailable" />
    </template>

    <p class="mf-intro">
      PixlStash lists models from these folders. Adding one does not move or
      copy anything.
    </p>

    <p v-if="store.loading && !store.loaded" class="mf-state">
      Reading the registered folders…
    </p>
    <p v-else-if="store.error" class="mf-state" role="alert">
      {{ store.error }}
    </p>

    <!-- A plain list of rows carrying verbs, not a `role="listbox"`: nothing
         here is selected, and interactive controls inside a `role="option"`
         are unreachable to a screen reader. Real `<button>`s in a `<ul>` give
         Tab and Enter for free. -->
    <ul v-else class="mf-list" role="list">
      <li
        v-for="folder in store.folders"
        :key="folder.id"
        class="ps-row mf-row"
        :aria-busy="isScanning(folder) ? 'true' : undefined"
      >
        <span class="mf-row__glyph">
          <v-icon size="16">{{
            KIND_ICON[folder.kind] || KIND_ICON.user
          }}</v-icon>
        </span>

        <span class="mf-row__body">
          <span class="mf-row__pathline">
            <!-- The path is the row's identity, so it is mono at full strength
                 (§3) and never faded. It truncates from the LEFT because the
                 folder name is the part that identifies it; the U+200E keeps
                 the leading slash of a POSIX path on the left where it
                 belongs. Click or Enter expands it, which is the only route a
                 keyboard has to the hidden head. -->
            <button
              type="button"
              class="mf-row__path"
              :class="{ 'mf-row__path--expanded': expanded.has(folder.id) }"
              :title="folder.path"
              :aria-expanded="expanded.has(folder.id)"
              :aria-label="`${expanded.has(folder.id) ? 'Collapse' : 'Show'} the full path: ${folder.path}`"
              @click="toggleExpanded(folder.id)"
            >
              {{ LRM }}{{ folder.path }}
            </button>
            <span v-if="folder.kind === MANAGED_KIND" class="mf-chip"
              >Managed</span
            >
            <span v-else-if="folder.kind === SOURCE_KIND" class="mf-chip"
              >ai-toolkit</span
            >
            <span v-else-if="folder.kind === 'foreign'" class="mf-chip"
              >PixlStash</span
            >
          </span>

          <span class="mf-row__meta">
            <span>{{ countLabel(folder.file_count) }}</span>
            <!-- Size sits beside the count because the count alone is
                 misleading on the folders PixlStash declares: the HuggingFace
                 cache is a handful of repos and 116 GB, and "26 models" reads
                 as small. Omitted at zero rather than shown as "0 B", which
                 would claim a measurement on a folder that has none. -->
            <span v-if="folder.present_bytes > 0">{{
              formatModelSize(folder.present_bytes)
            }}</span>
            <span :title="folder.last_checked || ''">{{
              scannedLabel(folder)
            }}</span>
          </span>

          <!-- Visible, in the row, never a tooltip: it explains why this row
               has no forget control, and an explanation for a missing control
               has to sit where the missing control would be. -->
          <span v-if="folder.kind === MANAGED_KIND" class="mf-row__note">
            PixlStash keeps its own models here, so this folder stays.
          </span>
        </span>

        <!-- Three slots, always three, whatever the row's kind. A slot that
             collapsed on one row would move every other row's buttons
             sideways, so an absent action is hidden with `visibility` and
             keeps its box, exactly as §5.1 requires of the glyph gutter. -->
        <span class="mf-row__actions">
          <AppButton
            :class="{ 'mf-hidden': !canScan(folder) }"
            icon-only
            variant="ghost"
            icon-left="refresh"
            :loading="isScanning(folder)"
            :title="scanTitle(folder)"
            :aria-label="`${scanVerb(folder)} ${folder.path}`"
            v-bind="blockedAttrs(remoteReason, REMOTE_NOTE_ID)"
            @click="onScan(folder)"
          />

          <!-- Slot two is Forget on a folder the owner associated and Move on
               one PixlStash owns, which has no association to dissolve. One
               element either way, so the column holds. `relocatable` and not
               `movable === 'root_only'`: the InsightFace packs say that too and
               have no relocate route yet, so the server is asked. -->
          <AppButton
            v-if="folder.relocatable"
            icon-only
            variant="ghost"
            icon-left="folder-move-outline"
            :title="`Move ${basename(folder.path)} to a different location. Every file in it is copied, verified and removed from here.`"
            :aria-label="`Move ${folder.path} to a different location`"
            v-bind="blockedAttrs(relocateReason(folder), REMOTE_NOTE_ID)"
            @click="onRelocate(folder)"
          />
          <AppButton
            v-else
            :class="{ 'mf-hidden': !canForget(folder) }"
            icon-only
            variant="ghost"
            icon-left="folder-off-outline"
            :title="`Forget ${basename(folder.path)}. Nothing on disk is deleted.`"
            :aria-label="`Forget ${folder.path}`"
            v-bind="blockedAttrs(forgetReason(folder), REMOTE_NOTE_ID)"
            @click="onForget(folder)"
          />

          <HelpTip
            :reason="rowReason(folder)"
            :label="`Why some actions are unavailable for ${basename(folder.path)}`"
          />
        </span>
      </li>
    </ul>

    <p v-if="store.loaded && !hasOwnFolders" class="mf-note">
      No folders of your own yet. Add the folder where you keep your LoRAs and
      checkpoints and they will appear on the shelf.
    </p>

    <!-- One visible note, not one per button: it is the `aria-describedby`
         target of every control the tier blocks, so the reason a keyboard user
         lands on is rendered rather than living in a tooltip. -->
    <p v-if="!canManage" :id="REMOTE_NOTE_ID" class="mf-note">
      Adding, scanning, moving, and forgetting model folders is only available
      on the machine running PixlStash, or over your local network or Tailscale.
      To allow it from anywhere, set <code>allow_remote_host_ops</code> in
      server settings.
    </p>

    <template #footer>
      <AppButton variant="secondary" key-hint="esc" @click="emit('close')">
        Done
      </AppButton>
    </template>
  </AppDialog>

  <!-- The shipped host-path picker, reused whole, by both verbs that need a
       host path: Add takes the folder as it is, Move empties one into it.
       `registeredPaths` is what stops the 409 rather than reporting it, and it
       is right for Move too - moving into a folder the owner already registered
       would swallow their association into PixlStash's. -->
  <FolderBrowser
    :open="browseOpen"
    :registered-paths="store.registeredPaths"
    already-registered-label="Already a model folder"
    @select="onPicked"
    @close="closeBrowser"
  />
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import HelpTip from "../widgets/HelpTip.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import { MANAGED_KIND, SOURCE_KIND } from "../../api/modelFolders";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import {
  basename,
  countLabel,
  useModelFoldersStore,
} from "../../stores/useModelFoldersStore";
import { relativeDate } from "../../utils/snapshots";
import { FOLDER_TIERS, formatModelSize } from "../../utils/modelShelf";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "source-added"]);

const store = useModelFoldersStore();
const librariesStore = useLibrariesStore();
// The move job is a STORE and not dialog state: a relocation of 438 GB outlives
// this dialog, and the shelf's own progress bar watches the same job.
const moves = useModelMovesStore();

/** LEFT-TO-RIGHT MARK. Without it the `direction: rtl` truncation resolves a
 *  POSIX path's leading slash against the RTL paragraph and renders
 *  `/home/x/loras` as `home/x/loras/`. */
const LRM = "‎";

/** The one VISIBLE note every tier-blocked control is described by. */
const REMOTE_NOTE_ID = "mf-remote-note";

const MOVE_RUNNING_REASON =
  "A move is already running. There is one at a time, machine-wide.";

const REMOTE_REASON =
  "Only available on the machine running PixlStash, or over your local network or Tailscale.";

const DOCKER_REASON =
  "Adding a folder needs its path on the host, which PixlStash cannot ask for from inside Docker. Add it from the command line for now.";

// One folder family, so the column reads as "which kind of folder" rather than
// as four unrelated marks - and ONE copy of it, shared with the shelf's folder
// headers (#899), which state the same tier about the same registry. `managed`
// is not locked: it holds no association to dissolve, but it is scannable and
// relocatable, which is why it keeps the home glyph rather than the lock.
const KIND_ICON = Object.fromEntries(
  Object.entries(FOLDER_TIERS).map(([kind, tier]) => [kind, tier.icon]),
);

const browseOpen = ref(false);
const expanded = ref(new Set());
// Which verb opened the picker. One browser serves all three, so the pick has
// to be told apart: Add registers the folder, Move empties another one into it,
// and Set ai-toolkit folder registers it as the output root instead.
const relocating = ref(null);
const addingSource = ref(false);

/**
 * Whether this session can reach the §16.3 host-capability tier.
 *
 * The same signal Settings uses, from the same response: the mutators here are
 * `LOCAL_OWNER_ONLY` for the same reason `POST /libraries/active` is, so a
 * second source of truth would only be a second thing to drift.
 */
const canManage = computed(() => librariesStore.canManage);

/** Docker takes a host path this UI has no way to ask for (`FolderEditor`'s job). */
const inDocker = computed(() => librariesStore.inDocker);

const remoteReason = computed(() => (canManage.value ? "" : REMOTE_REASON));

const addReason = computed(
  () => remoteReason.value || (inDocker.value ? DOCKER_REASON : ""),
);

/** `user` and `source` rows only; `managed` and `foreign` are PixlStash's own. */
const hasOwnFolders = computed(() =>
  store.folders.some(
    (folder) => folder.kind === "user" || folder.kind === "source",
  ),
);

function isScanning(folder) {
  return store.scanningIds.has(folder.id);
}

/** A source folder is taken from, never catalogued, so its scan is a no-op. */
function canScan(folder) {
  return folder.kind === "user" || folder.kind === MANAGED_KIND;
}

/** Only what the owner associated can be disassociated. */
function canForget(folder) {
  return folder.kind === "user" || folder.kind === "source";
}

function scanVerb(folder) {
  return folder.last_checked ? "Rescan" : "Scan";
}

function scanTitle(folder) {
  return `${scanVerb(folder)}: look for models added or removed since the last scan.`;
}

function scannedLabel(folder) {
  if (!folder.last_checked) return "Never scanned";
  return `Scanned ${relativeDate(folder.last_checked)}`;
}

function forgetReason(folder) {
  if (remoteReason.value) return remoteReason.value;
  // Same guard and same reason as `relocateReason` below: forgetting deletes
  // the very `model_file` rows a running move or import is writing, so the
  // server takes the one machine-wide slot for it too and answers a second
  // caller with a 409 (#1017). Saying so beforehand beats a red error toast on
  // a button that looked enabled.
  if (moves.busy) return MOVE_RUNNING_REASON;
  // The scanner thread is still writing rows against this folder id.
  return isScanning(folder) ? "This folder is being scanned right now." : "";
}

/** Why this folder cannot be moved right now, or "" when it can. */
function relocateReason(folder) {
  // No button is rendered for these, so this is the guard rather than a label:
  // it is what makes the click path refuse a row the server would 409.
  if (!folder.relocatable) return "This folder cannot be moved.";
  if (remoteReason.value) return remoteReason.value;
  // The server takes one move at a time, machine-wide, and answers a second
  // with a 409. Saying so beforehand beats reporting it afterwards.
  if (moves.busy) return MOVE_RUNNING_REASON;
  return isScanning(folder) ? "This folder is being scanned right now." : "";
}

/** Everything the row's help indicator has to explain, in one sentence each. */
function rowReason(folder) {
  const reasons = [];
  if (remoteReason.value) reasons.push(remoteReason.value);
  else if (isScanning(folder) && (canForget(folder) || folder.relocatable)) {
    reasons.push("This folder is being scanned right now.");
  }
  // `canForget` as well as `relocatable`: a running move blocks both verbs now,
  // and an ordinary `user` folder is forgettable without being relocatable -
  // gating this on `relocatable` alone left exactly those rows with a blocked
  // Forget and no note saying why.
  if (
    !remoteReason.value &&
    (folder.relocatable || canForget(folder)) &&
    moves.busy
  ) {
    reasons.push(MOVE_RUNNING_REASON);
  }
  return reasons.join(" ");
}

/**
 * Mark a control blocked without taking it out of the tab order.
 *
 * `aria-disabled`, never the `disabled` attribute: a natively-disabled button
 * cannot be focused, so the reason it points at is unreachable by keyboard,
 * which is the whole failure this pattern exists to avoid (`MixedQueueRow`,
 * `ReviewDecisionBar`). `AppButton` already fades both spellings identically.
 */
function blockedAttrs(reason, describedBy) {
  if (!reason) return {};
  return { "aria-disabled": "true", "aria-describedby": describedBy };
}

function toggleExpanded(id) {
  const next = new Set(expanded.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expanded.value = next;
}

function onAdd() {
  if (addReason.value) return;
  relocating.value = null;
  addingSource.value = false;
  browseOpen.value = true;
}

/** The same picker, registering what it returns as the ai-toolkit output root. */
function onAddSource() {
  if (addReason.value) return;
  relocating.value = null;
  addingSource.value = true;
  browseOpen.value = true;
}

function onRelocate(folder) {
  if (relocateReason(folder)) return;
  relocating.value = folder;
  browseOpen.value = true;
}

async function onPicked(path) {
  const folder = relocating.value;
  if (!folder) {
    if (addReason.value) return;
    const asSource = addingSource.value;
    // Awaited so the registry has the folder before anything acts on it: the
    // shelf answers `source-added` by showing the runs, and it can only read
    // them once `sourceFolder` resolves.
    store.add({ path, kind: asSource ? SOURCE_KIND : "user" }).then((added) => {
      if (added && asSource) emit("source-added");
    });
    return;
  }
  if (relocateReason(folder)) return;
  // Closed on start, not kept open with a second progress bar in it: the job
  // outlives this dialog and the shelf behind it already watches it, so staying
  // would only put a copy of that progress on top of the original.
  if (await moves.relocate(folder.id, path)) emit("close");
}

function closeBrowser() {
  browseOpen.value = false;
  relocating.value = null;
  addingSource.value = false;
}

function onScan(folder) {
  if (remoteReason.value || isScanning(folder)) return;
  store.scan(folder.id);
}

function onForget(folder) {
  if (forgetReason(folder)) return;
  store.forget(folder);
}

// The registry is host data the dialog is the only reader of, so it is fetched
// when the dialog opens rather than at startup. A scan started here keeps
// polling in the store after the dialog closes.
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    expanded.value = new Set();
    store.refresh();
  },
  { immediate: true },
);
</script>

<style scoped>
.mf-intro {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-bottom: var(--space-5);
  max-width: 60ch;
}

.mf-state {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
  padding: var(--space-5) 0;
}

.mf-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

/* The shared row system (`SideBar.global.css`, visual-language.md §5.1) via the
   neutral `.ps-row` alias, so this surface consumes those rules rather than
   keeping a second copy. No disclosure gutter: this list is flat and never
   nests, and a permanently blank glyph box reads as a missing icon. */
.mf-row {
  display: grid;
  grid-template-columns: var(--entity-thumb) minmax(0, 1fr) auto;
  align-items: center;
  column-gap: var(--space-3);
  padding-top: var(--space-3);
  padding-bottom: var(--space-3);
  transition: background var(--dur-1) var(--ease-standard);
}

.mf-row:hover {
  background: var(--hover-wash);
}

.mf-row__glyph {
  display: inline-flex;
  justify-content: center;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.mf-row__body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.mf-row__pathline {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

/* Mono at regular weight and FULL strength: §3 gives the mono face to file
   paths, and this path is the row's label rather than its metadata. Rank is
   never opacity (§5.1). */
.mf-row__path {
  min-width: 0;
  font-family: var(--font-mono);
  font-size: var(--text-base);
  font-weight: var(--weight-regular);
  color: rgb(var(--v-theme-on-surface));
  text-align: left;
  direction: rtl;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border-radius: var(--radius-sm);
}

.mf-row__path--expanded {
  direction: ltr;
  white-space: normal;
  overflow-wrap: anywhere;
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
}

/* 0.7, not 0.6: at 12px the lower alpha measures 4.07:1 on the light canvas
   and misses the 4.5:1 floor. */
.mf-row__meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-variant-numeric: tabular-nums;
}

.mf-row__note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  max-width: 60ch;
}

.mf-row__actions {
  justify-self: end;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: nowrap;
}

/* No slot may shrink, or the reservation is not a reservation. `flex-wrap` is
   nowrap for the same reason: a wrapped action group makes rows different
   heights, which is precisely what reserving the boxes is protecting. */
.mf-row__actions > * {
  flex: 0 0 auto;
}

/* `visibility`, never `display: none` and never `v-if`: all three keep the box,
   only this one also drops the control from the tab order and the
   accessibility tree, which is what an absent action needs. */
.mf-hidden {
  visibility: hidden;
}

.mf-note {
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-top: var(--space-5);
  max-width: 70ch;
}

.mf-note code {
  font-family: var(--font-mono);
}

/* Measured, not guessed. At a 320px viewport Vuetify gives the dialog
   `calc(100% - 48px)` = 272px, minus the 1px borders and two `--space-6` of
   body padding = 222px of content. The fixed part (24px glyph + two 8px column
   gaps + a 124px action group) is 164px, leaving the path 58px, about five
   characters of mono. So the actions restack below 480px, where the path gets
   190px instead. The buttons never shrink: 23px is the wrong direction for a
   touch target, and the group was never what did not fit. */
@media (max-width: 480px) {
  .mf-row {
    grid-template-columns: var(--entity-thumb) minmax(0, 1fr);
    row-gap: var(--space-3);
  }

  .mf-row__actions {
    grid-column: 2;
    justify-self: end;
  }
}
</style>
