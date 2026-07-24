// Pictures resource — /pictures.
//
// The largest resource in the app. Seeded here with the reads whose call sites
// have already migrated; the counts, scores, thumbnails, and export endpoints
// join it as the grid and overlay move over.

import { apiClient } from "../utils/apiClient";

/**
 * Count the pictures matching a filter scope.
 *
 * A single indexed COUNT, deliberately separate from the stream below: the
 * grid uses it to size its placeholder scroll area before any row has loaded.
 *
 * @param {string} [query=""] - pre-encoded filter query string, no leading `?`.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `count` is the total.
 */
export async function getPictureCount(query = "", { baseUrl = "" } = {}) {
  const url = query
    ? `${baseUrl}/pictures/count?${query}`
    : `${baseUrl}/pictures/count`;
  const res = await apiClient.get(url);
  return res.data;
}

/**
 * Fetch one batch of the grid stream.
 *
 * The grid fills itself from several of these in flight at once (first batch,
 * tail batch, then sequential background batches), so the offset/limit pair is
 * the caller's, not a cursor held here.
 *
 * @param {string} query - pre-encoded stream query string, no leading `?`.
 * @param {Object} options
 * @param {number} options.offset
 * @param {number} options.batchLimit
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `pictures` is the batch.
 */
export async function streamPictures(
  query,
  { offset, batchLimit, baseUrl = "" },
) {
  const res = await apiClient.get(
    `${baseUrl}/pictures/stream?${query}&offset=${offset}&batch_limit=${batchLimit}`,
  );
  return res.data;
}

/**
 * Read the likeness groups (near-duplicate clusters) for a filter scope.
 *
 * @param {number|string} threshold - similarity cut-off.
 * @param {string} [query=""] - pre-encoded filter query string.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} the grouped pictures (the response body).
 */
export async function getLikenessGroups(
  threshold,
  query = "",
  { baseUrl = "" } = {},
) {
  const res = await apiClient.get(
    `${baseUrl}/pictures/likeness-groups?threshold=${encodeURIComponent(threshold)}${
      query ? `&${query}` : ""
    }`,
  );
  return res.data;
}

/**
 * Find pictures showing the same face as a given one.
 *
 * Returns ranked references (`picture_id` + score), not pictures: the caller
 * fetches the pictures separately and re-applies this ranking, because the
 * ranking is the result and the id-list read does not preserve order.
 *
 * @param {number|string} sourceFaceId
 * @param {Object} [options]
 * @param {number} [options.topN=500]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} ranked matches (the response body).
 */
export async function faceSearch(
  sourceFaceId,
  { topN = 500, baseUrl = "" } = {},
) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/face-search?source_face_id=${sourceFaceId}&top_n=${topN}`,
  );
  return res.data;
}

/**
 * Find pictures visually similar to one or more source pictures.
 *
 * Several source ids are combined by MINIMUM similarity, i.e. a result must
 * resemble every source, not just one. Ranked like {@link faceSearch}.
 *
 * @param {Array<number|string>} sourcePictureIds
 * @param {Object} [options]
 * @param {number} [options.topN=500]
 * @param {number} [options.threshold=0.05]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} ranked matches (the response body).
 */
export async function likenessSearch(
  sourcePictureIds,
  { topN = 500, threshold = 0.05, baseUrl = "" } = {},
) {
  const params = new URLSearchParams();
  sourcePictureIds.forEach((id) =>
    params.append("source_picture_ids", String(id)),
  );
  params.append("top_n", String(topN));
  params.append("threshold", String(threshold));
  const res = await apiClient.post(
    `${baseUrl}/pictures/likeness-search?${params.toString()}`,
  );
  return res.data;
}

/**
 * Text-search the library.
 *
 * @param {string} text
 * @param {Object} [options]
 * @param {number} [options.threshold=0.1]
 * @param {number} [options.topN=10000]
 * @param {string} [options.query=""] - pre-encoded filter query string to
 *   narrow the search to the current scope.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} the matching pictures (the response body).
 */
export async function searchPictures(
  text,
  { threshold = 0.1, topN = 10000, query = "", baseUrl = "" } = {},
) {
  const res = await apiClient.get(
    `${baseUrl}/pictures/search?query=${encodeURIComponent(
      text,
    )}&threshold=${threshold}&top_n=${topN}${query ? `&${query}` : ""}`,
  );
  return res.data;
}

/**
 * Read one picture's metadata.
 * @param {number|string} id
 * @param {Object} [options]
 * @param {boolean} [options.smartScore=false] - also compute the smart score,
 *   which is markedly more expensive than the plain read.
 * @param {string|number} [options.cacheBuster] - forces a fresh read past any
 *   HTTP cache; pass a changing value only when a stale answer is unacceptable.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the metadata (the response body).
 */
export async function getPictureMetadata(
  id,
  { smartScore = false, cacheBuster, baseUrl = "" } = {},
) {
  const params = new URLSearchParams();
  if (smartScore) params.set("smart_score", "true");
  if (cacheBuster != null) params.set("cb", String(cacheBuster));
  const query = params.toString();
  const res = await apiClient.get(
    query
      ? `${baseUrl}/pictures/${id}/metadata?${query}`
      : `${baseUrl}/pictures/${id}/metadata`,
  );
  return res.data;
}

/**
 * Resolve thumbnail URLs for a batch of pictures.
 * @param {Array<number|string>} ids
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body: picture id → thumbnail record.
 */
export async function getThumbnails(ids, { baseUrl = "" } = {}) {
  const res = await apiClient.post(`${baseUrl}/pictures/thumbnails`, { ids });
  return res.data;
}

/**
 * Soft-delete pictures (move them to the scrapheap).
 *
 * Pictures frozen by a locked set are refused and reported in
 * `skipped_locked`; the caller must keep those tiles rather than assume the
 * whole request applied.
 *
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, including `skipped_locked`.
 */
export async function deletePictures(pictureIds, { baseUrl = "" } = {}) {
  const res = await apiClient.delete(`${baseUrl}/pictures`, {
    data: { picture_ids: pictureIds },
  });
  return res.data;
}

/**
 * Assign, add or remove a project on a set of pictures.
 * @param {Array<number|string>} pictureIds
 * @param {number|string|null} projectId
 * @param {Object} [options]
 * @param {"add"|"remove"} [options.mode] - omitted to SET the project,
 *   replacing whatever was there.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function setPicturesProject(
  pictureIds,
  projectId,
  { mode, baseUrl = "" } = {},
) {
  const body = { picture_ids: pictureIds, project_id: projectId };
  if (mode) body.mode = mode;
  const res = await apiClient.patch(`${baseUrl}/pictures/project`, body);
  return res.data;
}

/**
 * Ask what a permanent scrapheap purge would destroy, before doing it.
 *
 * Callers MUST treat a rejection as "could not verify" and refuse to open the
 * destructive confirm, never as "nothing would be deleted".
 *
 * @param {Array<number|string>|null} [ids=null] - null means the whole heap.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the counts and the protected-file list.
 */
export async function previewScrapheapDelete(ids = null, { baseUrl = "" } = {}) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/scrapheap/delete-preview`,
    { ids },
  );
  return res.data;
}

/**
 * Permanently delete pictures from the scrapheap.
 *
 * Omitting `pictureIds` empties the whole heap. Protected pictures are kept
 * unless `includeProtected` is set, and locked ones are always kept and
 * reported in `skipped_locked`.
 *
 * @param {Object} [options]
 * @param {Array<number|string>} [options.pictureIds]
 * @param {boolean} [options.includeProtected=false]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, including `skipped_locked`.
 */
export async function purgeScrapheap({
  pictureIds,
  includeProtected = false,
  baseUrl = "",
} = {}) {
  const data = { include_protected: includeProtected };
  if (pictureIds) data.picture_ids = pictureIds;
  const res = await apiClient.delete(`${baseUrl}/pictures/scrapheap`, { data });
  return res.data;
}

/**
 * Restore pictures from the scrapheap; omit the ids to restore all of them.
 * @param {Array<number|string>} [pictureIds]
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function restoreScrapheap(pictureIds, { baseUrl = "" } = {}) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/scrapheap/restore`,
    pictureIds ? { picture_ids: pictureIds } : undefined,
  );
  return res.data;
}

/**
 * Ask the server host to reveal a picture in its desktop file manager.
 *
 * Fails on a headless or remote server, which has no file manager to open.
 *
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function openPictureLocation(id, { baseUrl = "" } = {}) {
  const res = await apiClient.post(`${baseUrl}/pictures/${id}/open-location`);
  return res.data;
}

/**
 * Queue object detection over a set of pictures.
 *
 * Runs as a background GPU task: this resolves once the work is accepted, and
 * the results arrive over the websocket.
 *
 * @param {Array<number|string>} pictureIds
 * @param {string} prompt
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function detectPictures(pictureIds, prompt, { baseUrl = "" } = {}) {
  const res = await apiClient.post(`${baseUrl}/pictures/detect`, {
    picture_ids: pictureIds,
    prompt,
  });
  return res.data;
}

/**
 * List the installed picture plugins.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `plugins` is the list.
 */
export async function listPicturePlugins({ baseUrl = "" } = {}) {
  const res = await apiClient.get(`${baseUrl}/pictures/plugins`);
  return res.data;
}

/**
 * Run a picture plugin over a set of pictures.
 * @param {string} name - the plugin's name (URL-encoded here).
 * @param {Object} body - `picture_ids`, `parameters`, optional `captions`,
 *   and whether to `stack` the outputs with their sources.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function runPicturePlugin(name, body, { baseUrl = "" } = {}) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/plugins/${encodeURIComponent(name)}`,
    body,
  );
  return res.data;
}

/**
 * Re-run tagging on one picture, replacing its generated tags.
 * @param {number|string} id
 * @param {Object} [body] - `{ model }` to pick a tagger, or empty for the
 *   configured one.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function resetPictureTags(id, body = {}, { baseUrl = "" } = {}) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/${id}/reset_tags`,
    body,
  );
  return res.data;
}

/**
 * Re-run captioning on one picture, replacing its description.
 * @param {number|string} id
 * @param {Object} [body] - `{ model }` to pick a captioner.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function resetPictureDescription(
  id,
  body = {},
  { baseUrl = "" } = {},
) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/${id}/reset_description`,
    body,
  );
  return res.data;
}

/**
 * Remove tags the model could not possibly be right about.
 * @param {Array<number|string>} pictureIds
 * @param {Object} filters - the scope the caller is viewing.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body: `count` and the `removed`
 *   picture/tag pairs, which are what an undo restores.
 */
export async function clearImpossibleTags(
  pictureIds,
  filters,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/impossible-tags/clear`,
    { picture_ids: pictureIds, filters },
  );
  return res.data;
}

/**
 * Put back the picture/tag pairs a previous clear removed.
 * @param {Array<Object>} pairs - as returned by {@link clearImpossibleTags}.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function restoreImpossibleTags(pairs, { baseUrl = "" } = {}) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/impossible-tags/restore`,
    { pairs },
  );
  return res.data;
}

/**
 * Write scores for a signed-in owner.
 * @param {Object} scores - picture id → score.
 * @param {Object} [options]
 * @param {boolean} [options.onlyUnscored=false] - leave already-scored
 *   pictures alone.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function applyScores(
  scores,
  { onlyUnscored = false, baseUrl = "" } = {},
) {
  const res = await apiClient.post(`${baseUrl}/pictures/apply-scores`, {
    scores,
    only_unscored: onlyUnscored,
  });
  return res.data;
}

/**
 * Read the scores collected from this guest session.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `scores` maps picture id
 *   to score.
 */
export async function getGuestScores({ baseUrl = "" } = {}) {
  const res = await apiClient.get(`${baseUrl}/pictures/guest-scores`);
  return res.data;
}

/**
 * Submit scores from a guest (share-token) session.
 * @param {Object} payload - `session_id`, `set_cookie`, and `scores`.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function submitGuestScores(payload, { baseUrl = "" } = {}) {
  const res = await apiClient.post(`${baseUrl}/pictures/guest-scores`, payload);
  return res.data;
}

/**
 * Start a ZIP export of a picture selection or filter scope.
 *
 * Exporting is a background task: this returns a `task_id` to poll with
 * {@link getExportStatus}, which eventually yields a `download_url` for
 * {@link downloadExport}.
 *
 * @param {string} [query=""] - pre-encoded selection/filter query string.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `task_id` drives polling.
 */
export async function startExport(query = "", { baseUrl = "" } = {}) {
  const res = await apiClient.get(
    query ? `${baseUrl}/pictures/export?${query}` : `${baseUrl}/pictures/export`,
  );
  return res.data;
}

/**
 * Poll a running export.
 * @param {string} taskId
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body: `status`, `processed`,
 *   `total`, and once complete, `download_url`.
 */
export async function getExportStatus(taskId, { baseUrl = "" } = {}) {
  const res = await apiClient.get(`${baseUrl}/pictures/export/status`, {
    params: { task_id: taskId },
  });
  return res.data;
}

/**
 * Download a finished export.
 *
 * This is the one read whose response METADATA matters: the server names the
 * ZIP in `Content-Disposition`, and a body-only return would silently rename
 * every download to the fallback. The header is parsed here so the envelope
 * still does not escape this layer.
 *
 * @param {string} downloadUrl - the path from {@link getExportStatus}.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<{blob: Blob, filename: string}>} the archive and the name
 *   the server gave it (falling back to `pixlstash_export.zip`).
 */
export async function downloadExport(downloadUrl, { baseUrl = "" } = {}) {
  const res = await apiClient.get(`${baseUrl}${downloadUrl}`, {
    responseType: "blob",
  });
  let filename = "pixlstash_export.zip";
  const disposition = res.headers["content-disposition"];
  if (disposition) {
    const match = disposition.match(/filename="?([^";]+)"?/);
    if (match) filename = match[1];
  }
  return { blob: res.data, filename };
}

/**
 * Read library statistics for a filtered scope.
 *
 * The stats endpoint is deliberately sectioned: the caller asks for the parts
 * it is about to render via `include`, because the heavy sections
 * (co-occurrences, confidence histograms) cost far more than the summary.
 *
 * `query` is the caller's already-encoded filter string, which the sidebar
 * shares with the grid so both describe the same scope; `params` carries the
 * per-call section selectors on top of it.
 *
 * @param {string} [query=""] - pre-encoded filter query string, no leading `?`.
 * @param {Object} [params] - extra query params (`include`, `only_penalised`,
 *   `confidence_tag`, ...), encoded by Axios.
 * @returns {Promise<Object>} the statistics (the response body).
 */
export async function getPictureStats(query = "", params) {
  const url = query ? `/pictures/stats?${query}` : "/pictures/stats";
  const res = await apiClient.get(url, params ? { params } : undefined);
  return res.data;
}

/**
 * Discard the guest scores collected in this browser session.
 * @returns {Promise<Object>} the response body.
 */
export async function clearGuestScoreSession() {
  const res = await apiClient.delete("/pictures/guest-scores/session");
  return res.data;
}

/**
 * Fetch a specific set of pictures by id.
 *
 * The ids go out as a repeated `id` query param, and the server is free to
 * return them in any order (and to omit ones the caller may not see), so
 * callers that need the requested order re-index the result themselves.
 *
 * @param {Array<number|string>} ids
 * @param {Object} [options]
 * @param {string} [options.fields] - projection, e.g. `"grid"`; omitted for
 *   the full record.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} the pictures (the response body).
 */
export async function listPicturesByIds(ids, { fields, baseUrl = "" } = {}) {
  const params = new URLSearchParams();
  ids.forEach((id) => params.append("id", String(id)));
  if (fields) params.append("fields", fields);
  const res = await apiClient.get(`${baseUrl}/pictures?${params.toString()}`);
  return res.data;
}

/**
 * Read the region of a picture that explains an anomalous tag.
 *
 * Rejects with the raw Axios error for a tag outside the tagger vocabulary
 * (404/422) or an unavailable model (503), so the caller can cache the miss
 * rather than retry.
 *
 * @param {number|string} pictureId
 * @param {string} tag
 * @returns {Promise<Object>} the region (the response body).
 */
export async function getAnomalyRegion(pictureId, tag) {
  const res = await apiClient.get(`/pictures/${pictureId}/anomaly_region`, {
    params: { tag },
  });
  return res.data;
}
