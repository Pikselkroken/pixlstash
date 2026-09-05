import { apiClient } from "../utils/apiClient";

/**
 * Picture import resource - the async streaming-staging session (#459).
 *
 * This module is the SINGLE seam between the async-import UI and the backend.
 * Every network call the import flow makes lives here, so the UI (the two-phase
 * dialog, the task-store run, the StatsSidebar row) never talks to `apiClient`
 * directly. It predates the rest of `src/api/` and was written to the same
 * rules, so it moved here unchanged rather than being rewritten.
 *
 * TWO PHASES (mirrors the v1.8.0 design, docs/design/v1.8.0-preview.html) mapped
 * onto the finalised streaming-staging session contract:
 *
 *   Phase A "staging / unsafe":  open a staging session, then STREAM the files
 *     into it batch by batch while the tab stays open. Closing/refreshing the
 *     tab mid-stream aborts the upload - this is the window the beforeunload
 *     guard protects. `openStagingSession()` starts it; `stageFiles()` streams a
 *     batch; `cancelStaging()` discards a not-yet-committed session.
 *
 *   Safe transition:  once every byte is staged, `commitStaging()` hands off to a
 *     background `PictureImportTask` on the server's task runner and returns the
 *     task id. From here the tab is free - refreshing is harmless.
 *
 *   Phase B "importing / safe":  poll `fetchStagingStatus()` BY `stagingId`
 *     (NOT task id) until `stage` is `completed`/`failed`/`cancelled`, driving
 *     the task row from `processed`/`total` and summarising with the five
 *     disjoint buckets `imported_count` / `duplicate_count` /
 *     `scrapheaped_count` / `failed_count` / `cancelled_count`, which sum to
 *     `total`. `scrapheaped_picture_ids` carries the restore offer (see below).
 *
 * THE SCRAPHEAP BUCKET: a file whose content matches a SOFT-DELETED picture is
 * neither imported nor an ordinary duplicate. Importing it again would put a
 * second copy of every scrapheaped picture back on disk, which a bulk "Keep
 * cover only" cleanup makes a predictable way to undo the cleanup and double the
 * bytes. Restoring it automatically would be the opposite surprise: the user
 * scrapheapped it deliberately. So the import reports it, and the UI offers
 * `POST /pictures/scrapheap/restore` with `scrapheaped_picture_ids`, the
 * shipped restore route, which already clears `deleted_at` and re-folds stack
 * positions. There is deliberately no second restore path here.
 *
 * GRID REFRESH - there is deliberately NO per-file `results[]` payload. On
 * completion the backend broadcasts `CHANGED_PICTURES` + `PICTURE_IMPORTED` over
 * the WebSocket (uniform origin-aware envelope, integration_architecture.md §8),
 * which the grid already consumes (`useGridRealtimeSync`) to insert the new
 * pictures. The import UI relies on that broadcast, not a results array.
 *
 * ENDPOINTS (final, all under /api/v1, mutating routes OWNER_ONLY):
 *   POST   {backendUrl}/pictures/import/staging                   → { staging_id, safe_threshold }
 *   POST   {backendUrl}/pictures/import/staging/{id}/files         multipart `file` (repeatable)
 *                                                                 → { staging_id, staged, received[], skipped[] }
 *   POST   {backendUrl}/pictures/import/staging/{id}/commit        → { staging_id, task_id, staged_count }
 *                                                                   400 empty/face-worker-down, 409 committed, 507 disk
 *   DELETE {backendUrl}/pictures/import/staging/{id}               (pre-commit only) → { stage:"cancelled", … }
 *   GET    {backendUrl}/pictures/import/staging/{id}/status        → { stage, staged, total, processed, task_id,
 *                                                                       imported_count, duplicate_count, scrapheaped_count,
 *                                                                       scrapheaped_picture_ids[], failed_count,
 *                                                                       cancelled_count, error }
 *
 * NOTE: the staging file endpoint accepts media, `.zip` archives (extracted
 * server-side) and `.txt` caption sidecars, and the client streams whatever the
 * import file-collection allows (`isSupportedImportFile`). That collection now
 * mirrors the server's own media allowlist (`IMPORT_MEDIA_EXTENSIONS` in
 * `utils/media.js` ↔ `STAGING_ALLOWED_MEDIA_EXTS` in the route), because
 * "stream it and let the backend decide" charged the whole upload before
 * refusing: a file the name already disqualifies came back in `skipped[]` after
 * its last byte, and a session that staged nothing 400s on commit. What still
 * reaches `skipped[]` is what only the bytes can settle - a corrupt image, a
 * bad zip - never an extension.
 */

export const IMPORT_ENDPOINTS = {
  openStaging: (backendUrl) => `${backendUrl}/pictures/import/staging`,
  stageFiles: (backendUrl, stagingId) =>
    `${backendUrl}/pictures/import/staging/${stagingId}/files`,
  commit: (backendUrl, stagingId) =>
    `${backendUrl}/pictures/import/staging/${stagingId}/commit`,
  cancel: (backendUrl, stagingId) =>
    `${backendUrl}/pictures/import/staging/${stagingId}`,
  status: (backendUrl, stagingId) =>
    `${backendUrl}/pictures/import/staging/${stagingId}/status`,
};

/**
 * @typedef {Object} StagingSession
 * @property {string} stagingId    Opaque session id used on every later call.
 * @property {number} safeThreshold Declared file count for the safe-window hint (0 if none).
 * @property {Object} raw
 */

/**
 * Phase A start - open a staging session (start of the unsafe window).
 *
 * `projectId` / `setId` / `characterId` are the drop-target association: when
 * files are dropped onto a project / set / character row, the backend associates
 * every imported picture with that target server-side on commit (there is no
 * per-file results[] to associate client-side). They are independent - a drop
 * targets one, but the session accepts each optionally.
 *
 * @param {Object} args
 * @param {string} args.backendUrl
 * @param {number|null} [args.projectId]    Project every imported picture joins on commit.
 * @param {number|null} [args.setId]        Picture set to add every imported picture to.
 * @param {number|null} [args.characterId]  Character to associate every imported picture with.
 * @param {number} [args.totalFiles]        Declared total (progress/safe-threshold hint).
 * @returns {Promise<StagingSession>}
 */
export async function openStagingSession({
  backendUrl,
  projectId = null,
  setId = null,
  characterId = null,
  totalFiles,
}) {
  const res = await apiClient.post(IMPORT_ENDPOINTS.openStaging(backendUrl), {
    project_id: projectId ?? null,
    set_id: setId ?? null,
    character_id: characterId ?? null,
    total_files: totalFiles ?? null,
  });
  const d = res?.data ?? {};
  return {
    stagingId: d.staging_id ?? null,
    safeThreshold: d.safe_threshold ?? 0,
    raw: d,
  };
}

/**
 * @typedef {Object} StagedBatch
 * @property {number} staged     Total files staged in this session so far.
 * @property {string[]} received Original filenames accepted in this request.
 * @property {string[]} skipped  Filenames rejected (unsupported/empty) in this request.
 * @property {Object} raw
 */

/**
 * Phase A - stream one batch of files into the session. Throws on network/HTTP
 * failure so the caller's retry/abort logic can react.
 *
 * @param {Object} args
 * @param {string} args.backendUrl
 * @param {string} args.stagingId
 * @param {File[]} args.files
 * @param {AbortSignal} [args.signal]
 * @param {number} [args.timeoutMs]
 * @param {(e: ProgressEvent) => void} [args.onUploadProgress]
 * @returns {Promise<StagedBatch>}
 */
export async function stageFiles({
  backendUrl,
  stagingId,
  files,
  signal,
  timeoutMs,
  onUploadProgress,
}) {
  const formData = new FormData();
  files.forEach((file) => formData.append("file", file));
  const res = await apiClient.post(
    IMPORT_ENDPOINTS.stageFiles(backendUrl, stagingId),
    formData,
    {
      signal,
      timeout: timeoutMs,
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress,
    },
  );
  const d = res?.data ?? {};
  return {
    staged: d.staged ?? 0,
    received: Array.isArray(d.received) ? d.received : [],
    skipped: Array.isArray(d.skipped) ? d.skipped : [],
    raw: d,
  };
}

/**
 * @typedef {Object} CommitResult
 * @property {string|null} taskId     Background PictureImportTask id.
 * @property {number} stagedCount     Files handed off to the import.
 * @property {Object} raw
 */

/**
 * Safe handoff - commit the session, enqueuing the background import.
 * Throws on 400 (empty / face worker down), 409 (already committed), 507 (disk).
 *
 * @param {Object} args
 * @param {string} args.backendUrl
 * @param {string} args.stagingId
 * @returns {Promise<CommitResult>}
 */
export async function commitStaging({ backendUrl, stagingId }) {
  const res = await apiClient.post(
    IMPORT_ENDPOINTS.commit(backendUrl, stagingId),
  );
  const d = res?.data ?? {};
  return {
    taskId: d.task_id ?? null,
    stagedCount: d.staged_count ?? 0,
    raw: d,
  };
}

/**
 * Cancel a not-yet-committed session (Phase A only), discarding its files.
 *
 * @param {Object} args
 * @param {string} args.backendUrl
 * @param {string} args.stagingId
 * @returns {Promise<{stage: string, raw: Object}>}
 */
export async function cancelStaging({ backendUrl, stagingId }) {
  const res = await apiClient.delete(
    IMPORT_ENDPOINTS.cancel(backendUrl, stagingId),
  );
  const d = res?.data ?? {};
  return { stage: d.stage ?? "cancelled", raw: d };
}

/**
 * @typedef {Object} StagingStatus
 * @property {string} stage           staging | importing | completed | failed | cancelled.
 * @property {number} staged          Files staged into the session.
 * @property {number} total           Total the import is working through.
 * @property {number} processed       Files processed so far (Phase B).
 * @property {string|null} taskId
 * @property {number|null} importedCount   New pictures imported (on completion).
 * @property {number|null} duplicateCount  Skipped: content already live in the vault.
 * @property {number|null} scrapheapedCount Skipped: content matches a picture in the
 *   Scrapheap. Counted per FILE. Not imported again (that would double the bytes on
 *   disk) and not restored either: restoring is offered, because the user
 *   scrapheapped those pictures on purpose.
 * @property {number[]} scrapheapedPictureIds Distinct scrapheaped pictures behind
 *   `scrapheapedCount`, per PICTURE. Feed straight to `restoreScrapheap()`.
 * @property {number|null} failedCount     Failed files (on completion).
 * @property {number|null} cancelledCount  Staged files never reached (cancelled run).
 * @property {string|null} error
 * @property {Object} raw
 *
 * The five counts are disjoint and sum to `total`; none is derived by subtracting
 * the others, so no summary line can overstate what happened.
 */

/**
 * Phase B - poll a session's stage/progress by staging id.
 *
 * @param {Object} args
 * @param {string} args.backendUrl
 * @param {string} args.stagingId
 * @returns {Promise<StagingStatus>}
 */
export async function fetchStagingStatus({ backendUrl, stagingId }) {
  const res = await apiClient.get(
    IMPORT_ENDPOINTS.status(backendUrl, stagingId),
  );
  const d = res?.data ?? {};
  return {
    stage: d.stage || "staging",
    staged: d.staged ?? 0,
    total: d.total ?? 0,
    processed: d.processed ?? 0,
    taskId: d.task_id ?? null,
    importedCount: d.imported_count ?? null,
    duplicateCount: d.duplicate_count ?? null,
    scrapheapedCount: d.scrapheaped_count ?? null,
    scrapheapedPictureIds: Array.isArray(d.scrapheaped_picture_ids)
      ? d.scrapheaped_picture_ids
      : [],
    failedCount: d.failed_count ?? null,
    cancelledCount: d.cancelled_count ?? null,
    error: d.error ?? null,
    raw: d,
  };
}
