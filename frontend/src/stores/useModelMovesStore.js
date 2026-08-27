import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import {
  cancelModelMove,
  getModelMoveStatus,
  relocateModelFolder,
  startModelMove,
} from "../api/modelMoves";
import { onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";
import { useModelFoldersStore } from "./useModelFoldersStore";
import { useModelShelfStore } from "./useModelShelfStore";
import { useNoticeStore } from "./useNoticeStore";

/** How often the job is re-read while one is running. */
const POLL_MS = 1000;
/**
 * The longest a run of unreadable statuses may push the next reading out to.
 *
 * A failed read is "status unknown", not "the move stopped" (#1018), so the
 * loop keeps going - but a backend that is down for a minute must not be asked
 * sixty times, so each consecutive failure doubles the wait up to this ceiling.
 * Fifteen seconds is late enough to be cheap and early enough that a move which
 * finished during the outage is still reported while the owner is looking.
 */
const POLL_MAX_MS = 15000;

/**
 * What each per-item status means in a receipt, and whether it is bad news.
 *
 * `moved` and `copied` are the same outcome by two mechanisms - a same-drive
 * `rename()` versus copy-verify-repoint-unlink - and the reader does not care
 * which, so they are counted together. The other three are each their own
 * piece of news and are never merged: `skipped` means the file was already
 * where it was being sent, `cancelled` means the queue stopped before reaching
 * it and nothing happened to it, and `failed` means it was attempted and left
 * its original untouched.
 */
const MOVED_STATUSES = new Set(["moved", "copied"]);

/**
 * Say what a move did, in the order the reader needs it.
 *
 * Landed first, then the three ways an item did not land. A receipt naming only
 * the successes would read as a clean run when a third of the batch failed, and
 * one naming only the failures sends the reader back to re-run a move that
 * mostly worked - which, on 438 GB, is not a cheap mistake.
 *
 * @param {Array<Object>} results - the job's per-item results.
 * @param {boolean} [cancelled=false] - whether a cancel was requested.
 */
export function moveReceipt(results, cancelled = false) {
  const list = Array.isArray(results) ? results : [];
  const tally = (predicate) => list.filter(predicate).length;
  const moved = tally((r) => MOVED_STATUSES.has(r.status));
  const skipped = tally((r) => r.status === "skipped");
  const failed = tally((r) => r.status === "failed");
  const stopped = tally((r) => r.status === "cancelled");

  const files = (n) => `${n.toLocaleString()} ${n === 1 ? "file" : "files"}`;
  const notes = [];
  if (skipped) {
    notes.push(
      `${files(skipped)} ${skipped === 1 ? "was" : "were"} already there.`,
    );
  }
  if (failed) notes.push(`${files(failed)} could not be moved and stayed put.`);
  // A file whose `<stem>_samples/` previews did not travel with it is still
  // `moved` - losing a preview must not cost the weights, so the server does
  // not fail the file for it. The status tallies above therefore cannot see it,
  // and a receipt built from them alone would call the loss a clean move. The
  // import receipt names the same thing (`importReceipt`), and this is its half.
  const withoutSamples = tally(
    (r) => MOVED_STATUSES.has(r.status) && Boolean(r.detail),
  );
  if (withoutSamples) {
    notes.push(
      `${files(withoutSamples)} moved without ${withoutSamples === 1 ? "its" : "their"} training previews.`,
    );
  }
  // Named rather than folded into "not moved": the queue never reached these,
  // so nothing was attempted on them and nothing is half-done.
  if (stopped) {
    notes.push(
      stopped === 1
        ? "1 file was left where it was."
        : `${files(stopped)} were left where they were.`,
    );
  }

  const head = cancelled
    ? moved
      ? `Stopped after moving ${files(moved)}.`
      : "Stopped before anything moved."
    : moved
      ? `Moved ${files(moved)}.`
      : "Nothing moved.";
  return [head, ...notes].join(" ");
}

/**
 * The one model move, and the poll that watches it.
 *
 * A STORE and not dialog state, for the same reason folder scans are: the move
 * outlives whatever started it. The owner drags 400 files onto another drive
 * and navigates away, and the progress still has to be there when they come
 * back, because the server is still copying either way.
 *
 * **One job, machine-wide.** That is the server's rule, not a convenience here:
 * two concurrent moves would race for the same free space that both of them
 * checked before either started. So this store holds a single job rather than a
 * collection, and `busy` is what every entry point checks first.
 *
 * Host paths and folder ids are owner-only, so a session reset drops it whole
 * and abandons the poll. The server thread carries on; this session no longer
 * has standing to watch it.
 */
export const useModelMovesStore = defineStore("modelMoves", () => {
  /** The last status snapshot the server gave us, verbatim. */
  const job = ref(null);
  const starting = ref(false);
  const error = ref("");
  /**
   * The receipt of a finished run that lost files, held until it is dismissed.
   *
   * Held HERE rather than pushed as a notice (#900). A `warning` card clears
   * itself after six seconds, and what it is saying is that some of the
   * owner's models did not arrive - which is the one outcome that must not
   * scroll past while they are looking elsewhere. Living in the store, it goes
   * back into the shelf's corner, where the progress was, every time the shelf
   * is mounted.
   */
  const failure = ref("");
  let pollHandle = null;
  let pollDelay = POLL_MS;
  let epoch = 0;
  // Set the moment a job is started or observed running, cleared when its
  // finish has been reported. Without it a `finished` job read on the first
  // poll after a page load would fire a receipt for a move the reader already
  // saw the receipt for.
  let watching = false;

  const status = computed(() => job.value?.status || "idle");
  const running = computed(() => status.value === "running");
  /** True while a move may not be started: one job, machine-wide. */
  const busy = computed(() => running.value || starting.value);

  const total = computed(() => Number(job.value?.total) || 0);
  const done = computed(() => Number(job.value?.done) || 0);
  const cancelRequested = computed(() => Boolean(job.value?.cancel_requested));

  /**
   * How far along, as a percentage of items decided.
   *
   * Items and not bytes, because `bytes_to_copy` is ZERO for a same-drive move
   * - those are renames - so a byte-based bar would sit at 0% for the whole of
   * the fastest case and then jump. Items are the unit the server actually
   * reports progress in.
   */
  const percent = computed(() =>
    total.value ? Math.round((done.value / total.value) * 100) : 0,
  );

  function stopPolling() {
    pollDelay = POLL_MS;
    if (pollHandle === null) return;
    clearTimeout(pollHandle);
    pollHandle = null;
  }

  /**
   * Watch the job until it reaches a terminal status.
   *
   * A self-scheduling timeout and not an interval: the next reading is booked
   * only once the current one has landed, so a slow status read can never have
   * a second one queued behind it, and an unreadable one can simply wait longer
   * before trying again. The loop ends on a terminal status, a session reset or
   * disposal - never on a failed read, which is what left the tab permanently
   * busy (#1018).
   */
  function startPolling() {
    if (pollHandle !== null) return;
    scheduleNextPoll();
  }

  function scheduleNextPoll() {
    const startedAt = epoch;
    // `pollHandle` is deliberately NOT cleared when the timeout fires: it is
    // what `adopt()` and `startPolling()` test to know a loop already owns this
    // job, and a window where it reads null across the in-flight read would let
    // a second loop start on top of this one. `stopPolling()` clears it.
    pollHandle = setTimeout(async () => {
      let read = false;
      try {
        read = await poll();
      } catch (err) {
        // A rejection out of a timer callback is nobody's to catch, and one
        // thrown before the finish is reported would end the loop still holding
        // `pollHandle` and `watching` - the stuck-busy shape this whole change
        // is about, arriving by another door. A snapshot whose `results` is not
        // a list gets here today. Treated as an unreadable status: said out
        // loud, backed off, still watching.
        console.error("[modelMoves] the move watch threw:", err);
      }
      // A reset while the read was in flight owns the store now; anything this
      // loop does from here would be about somebody else's session.
      if (startedAt !== epoch) return;
      // `watching` is cleared by the reading that consumed a terminal status
      // and reported it, and by nothing else - an unread status leaves it up,
      // and we try again. Testing the job's status instead would end the loop
      // on a failed read whenever the last snapshot was already terminal, which
      // a job that finished before its own POST returned can be.
      if (!watching) return;
      pollDelay = read ? POLL_MS : Math.min(pollDelay * 2, POLL_MAX_MS);
      scheduleNextPoll();
    }, pollDelay);
  }

  /**
   * Take one status reading, and report the finish exactly once.
   *
   * The refresh on completion is BOTH stores: `model_file` rows were repointed,
   * so the shelf's locations are stale, and the folders' file counts and
   * `shelf_bytes` moved with them, so the drive bands are too.
   *
   * @returns {Promise<boolean>} whether a status was actually read.
   */
  async function poll() {
    const startedAt = epoch;
    let snapshot;
    try {
      snapshot = await getModelMoveStatus();
    } catch (err) {
      // A poll that fails is not a move that failed, and it is not a move that
      // stopped either: the server is still copying. Leave the last snapshot up
      // and say only that this reading did not happen.
      console.warn("[modelMoves] could not read the move status:", err);
      return false;
    }
    if (startedAt !== epoch) return false;
    job.value = snapshot;
    if (snapshot?.status === "running") {
      watching = true;
      return true;
    }
    stopPolling();
    if (!watching) return true;
    watching = false;
    const results = snapshot?.results || [];
    const receipt = moveReceipt(results, Boolean(snapshot?.cancel_requested));
    // Two surfaces for two kinds of news: a run that landed says so in passing,
    // a run that lost files holds the panel until it is read.
    if (results.some((r) => r.status === "failed")) failure.value = receipt;
    else useNoticeStore().push({ level: "success", text: receipt });
    try {
      await Promise.all([
        useModelShelfStore().fetchRows(),
        useModelFoldersStore().refresh({ quiet: true }),
      ]);
    } catch (err) {
      // The move itself landed and has been reported; only the repaint behind
      // it did not. Caught here because the caller is usually a timer callback,
      // where a rejection is nobody's to handle and surfaces as an unhandled
      // one. The stale rows are the shelf's own problem and it refetches on the
      // next mount.
      console.warn("[modelMoves] could not refresh after the move:", err);
    }
    return true;
  }

  /**
   * Move registered copies into a folder, and watch the job to its end.
   *
   * The POST plans the whole batch before the first byte, so a refusal here is
   * a 4xx with a reason and NOT a half-done move - which is why the error is
   * surfaced as a notice and the store is left idle rather than showing a
   * failed job.
   *
   * @param {number} destinationFolderId
   * @param {Array<{folder_id: number, relpath: string}>} items
   * @returns {Promise<boolean>} true when the job was accepted.
   */
  async function start(destinationFolderId, items) {
    if (busy.value || !items?.length) return false;
    return begin(
      () => startModelMove(destinationFolderId, items),
      "Could not start that move.",
    );
  }

  /**
   * Move a whole folder PixlStash owns to another host path, files and all.
   *
   * The same job, watched the same way - a relocation IS a move - so it takes
   * the one machine-wide slot, reports through the same progress and ends in
   * the same receipt and refresh.
   *
   * @param {number} folderId - a folder whose `relocatable` is true.
   * @param {string} path - an absolute host path.
   * @returns {Promise<boolean>} true when the job was accepted.
   */
  async function relocate(folderId, path) {
    if (busy.value || !folderId || !path) return false;
    return begin(
      () => relocateModelFolder(folderId, path),
      "Could not start that move.",
    );
  }

  /** Take the one job slot with *request*, and watch whatever it returns. */
  async function begin(request, failureMessage) {
    const notices = useNoticeStore();
    starting.value = true;
    error.value = "";
    // The new run owns the corner from here; the last one's failure has had its
    // chance to be read and must not sit on top of live progress.
    failure.value = "";
    try {
      job.value = await request();
      watching = true;
      startPolling();
      return true;
    } catch (err) {
      error.value = errorDetail(err) || failureMessage;
      notices.push({ level: "error", text: error.value });
      return false;
    } finally {
      starting.value = false;
    }
  }

  /**
   * Ask the queue to stop between files.
   *
   * The job is left running and the poll left alive: the file in flight
   * finishes, and the receipt comes from the same completion path as any other,
   * so "stopped after moving 12" is reported rather than a silent halt.
   */
  async function cancel() {
    if (!running.value) return false;
    try {
      job.value = await cancelModelMove();
      return true;
    } catch (err) {
      useNoticeStore().push({
        level: "error",
        text: errorDetail(err) || "Could not stop the move.",
      });
      return false;
    }
  }

  /** Put the held failure away. The only way out of it, by design. */
  function dismissFailure() {
    failure.value = "";
  }

  /**
   * Pick up a move already running, e.g. one started before a reload.
   *
   * Only adopts a `running` job. A `finished` one belongs to a receipt that has
   * already been shown, and re-reporting it on every mount is how a completed
   * move announces itself forever.
   */
  async function adopt() {
    if (busy.value || pollHandle !== null) return;
    try {
      const snapshot = await getModelMoveStatus();
      if (snapshot?.status !== "running") return;
      job.value = snapshot;
      watching = true;
      startPolling();
    } catch (err) {
      console.warn("[modelMoves] could not check for a running move:", err);
    }
  }

  function resetForSession() {
    epoch += 1;
    stopPolling();
    watching = false;
    job.value = null;
    starting.value = false;
    error.value = "";
    failure.value = "";
  }

  const unsubscribeSessionReset = onSessionReset(resetForSession);
  onScopeDispose(() => {
    unsubscribeSessionReset();
    stopPolling();
  });

  return {
    job,
    status,
    running,
    busy,
    starting,
    error,
    failure,
    total,
    done,
    percent,
    cancelRequested,
    start,
    relocate,
    cancel,
    dismissFailure,
    adopt,
    poll,
    resetForSession,
  };
});
