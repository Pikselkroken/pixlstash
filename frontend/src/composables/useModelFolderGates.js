// What may be done to a registered model folder right now, and why not.
//
// One copy, read by the two places that offer the registry's verbs: the folder
// list in Settings › Models and the shelf's folder-header menu (#1270). Every
// reason here is said beforehand because the server would otherwise answer the
// click with a 403 or a 409.

import { computed } from "vue";

import { DOWNLOADS_KIND } from "../api/modelFolders";
import { useLibrariesStore } from "../stores/useLibrariesStore";
import { useModelFoldersStore } from "../stores/useModelFoldersStore";
import { useModelMovesStore } from "../stores/useModelMovesStore";

/** The one VISIBLE note every tier-blocked control is described by. */
export const REMOTE_NOTE_ID = "mf-remote-note";

export const REMOTE_REASON =
  "Only available on the machine running PixlStash, or over your local network or Tailscale.";

const MOVE_RUNNING_REASON =
  "A move is already running. There is one at a time, machine-wide.";

const SCANNING_REASON = "This folder is being scanned right now.";

const DOCKER_REASON =
  "Adding a folder needs its path on the host, which PixlStash cannot ask for from inside Docker. Add it from the command line for now.";

/**
 * Mark a control blocked without taking it out of the tab order.
 *
 * `aria-disabled`, never the `disabled` attribute: a natively-disabled button
 * cannot be focused, so the reason it points at is unreachable by keyboard,
 * which is the whole failure this pattern exists to avoid (`MixedQueueRow`,
 * `ReviewDecisionBar`). `AppButton` already fades both spellings identically.
 * Only the remote reason has a visible note to point at; the others ride on
 * the control's own tooltip, which opens on keyboard focus.
 */
export function blockedAttrs(reason) {
  if (!reason) return {};
  return reason === REMOTE_REASON
    ? { "aria-disabled": "true", "aria-describedby": REMOTE_NOTE_ID }
    : { "aria-disabled": "true" };
}

export function useModelFolderGates() {
  const store = useModelFoldersStore();
  const librariesStore = useLibrariesStore();
  // The move job is a STORE: a relocation of 438 GB outlives any one view.
  const moves = useModelMovesStore();

  /**
   * Whether this session can reach the §16.3 host-capability tier: the
   * mutators are `LOCAL_OWNER_ONLY` for the same reason `POST /libraries/active`
   * is, so Settings' own signal is reused rather than duplicated.
   */
  const canManage = computed(() => librariesStore.canManage);

  const remoteReason = computed(() => (canManage.value ? "" : REMOTE_REASON));

  /** Docker takes a host path this UI has no way to ask for. */
  const addReason = computed(
    () => remoteReason.value || (librariesStore.inDocker ? DOCKER_REASON : ""),
  );

  function isScanning(folder) {
    return store.scanningIds.has(folder.id);
  }

  /** Only what the owner associated can be disassociated. */
  function canForget(folder) {
    return (
      folder.kind === "user" ||
      folder.kind === "source" ||
      folder.kind === DOWNLOADS_KIND
    );
  }

  function forgetReason(folder) {
    if (remoteReason.value) return remoteReason.value;
    // Forgetting deletes the very `model_file` rows a running move or import
    // is writing, so the server takes the one machine-wide slot for it too and
    // answers a second caller with a 409 (#1017).
    if (moves.busy) return MOVE_RUNNING_REASON;
    // The scan is still writing rows against this folder id.
    return isScanning(folder) ? SCANNING_REASON : "";
  }

  /** Why this folder cannot be moved right now, or "" when it can. */
  function relocateReason(folder) {
    // No button is rendered for these, so this is the guard rather than a
    // label: it is what makes the click path refuse a row the server would 409.
    if (!folder.relocatable) return "This folder cannot be moved.";
    if (remoteReason.value) return remoteReason.value;
    if (moves.busy) return MOVE_RUNNING_REASON;
    return isScanning(folder) ? SCANNING_REASON : "";
  }

  return {
    canManage,
    remoteReason,
    addReason,
    isScanning,
    canForget,
    forgetReason,
    relocateReason,
  };
}
