// Pictures resource - /pictures.
//
// The largest resource in the app. Seeded here with the reads whose call sites
// have already migrated; the counts, scores, thumbnails, and export endpoints
// join it as the grid and overlay move over.

import { apiClient, appendShareToken, API_BASE_URL} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * The URL a browser loads a picture's thumbnail from.
 *
 * Not a request function: an `<img src>` bypasses Axios entirely, so the share
 * token has to be appended by hand. It still belongs in this module, because
 * the path is part of the pictures contract and a second spelling of it
 * elsewhere is exactly the drift the api layer exists to prevent.
 *
 * @param {number|string} id
 * @param {Object} [options]
 * @param {number|string} [options.version] - cache-buster, bumped when the
 *   thumbnail is regenerated.
 * @returns {string}
 */
export function pictureThumbnailUrl(id, { version } = {}) {
  const query =
    version === undefined || version === null
      ? ""
      : `?v=${encodeURIComponent(version)}`;
  // An <img src> never reaches the apiClient interceptor, so the API base has
  // to be spelled out here rather than left for axios to prepend.
  return appendShareToken(
    `${API_BASE_URL}/pictures/thumbnails/${id}.webp${query}`,
  );
}

/**
 * Read a picture's thumbnail as image BYTES.
 *
 * The one caller so far is the model shelf's thumbnail verb, and the shape of
 * that verb is why this exists: `POST /models/{id}/icon` takes bytes and stores
 * them content-addressed beside the hub, deliberately with no route that
 * resolves a picture id server-side - the icon is a COPY, so it cannot break
 * when the picture is deleted or the library is switched
 * (`services/model_icons.py`). So choosing a library picture means sending its
 * pixels, and the thumbnail is the right pixels to send: it is already WebP, it
 * is generated on demand rather than having to have been made in advance, and
 * its 384px short edge is both what an icon needs and comfortably inside the
 * store's 2 MB ceiling.
 *
 * **It can still 404**, because "on demand" means generated FROM the file: the
 * route refuses when the source is missing, unreachable or undecodable - an
 * unplugged drive is a state this app models. The caller must have an answer
 * for that; it is not a read that always succeeds.
 *
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string|number} [options.cacheBuster] - forces a fresh read past the
 *   route's one-hour `max-age`. Worth passing when the bytes are about to be
 *   STORED rather than merely shown: an hour-old thumbnail is a fine tile and a
 *   wrong thing to keep.
 * @returns {Promise<Blob>} the WebP bytes.
 */
export async function getPictureThumbnailBlob(id, { cacheBuster } = {}) {
  const query =
    cacheBuster == null ? "" : `?cb=${encodeURIComponent(cacheBuster)}`;
  return unwrap(
    apiClient.get(`/pictures/thumbnails/${id}.webp${query}`, {
      responseType: "blob",
    }),
  );
}

/**
 * Count the pictures matching a filter scope.
 *
 * A single indexed COUNT, deliberately separate from the stream below: the
 * grid uses it to size its placeholder scroll area before any row has loaded.
 *
 * @param {string} [query=""] - pre-encoded filter query string, no leading `?`.
 * @returns {Promise<Object>} the response body, whose `count` is the total.
 */
export async function getPictureCount(query = "") {
  const url = query
    ? `/pictures/count?${query}`
    : `/pictures/count`;
  return unwrap(apiClient.get(url));
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
 * @returns {Promise<Object>} the response body, whose `pictures` is the batch.
 */
export async function streamPictures(
  query,
  { offset, batchLimit },
) {
  return unwrap(apiClient.get(
    `/pictures/stream?${query}&offset=${offset}&batch_limit=${batchLimit}`,
  ));
}

/**
 * Read the likeness groups (near-duplicate clusters) for a filter scope.
 *
 * @param {number|string} threshold - similarity cut-off.
 * @param {string} [query=""] - pre-encoded filter query string.
 * @returns {Promise<Array<Object>>} the grouped pictures (the response body).
 */
export async function getLikenessGroups(
  threshold,
  query = "",
) {
  return unwrap(apiClient.get(
    `/pictures/likeness-groups?threshold=${encodeURIComponent(threshold)}${
      query ? `&${query}` : ""
    }`,
  ));
}

/**
 * Find pictures showing the same face as a given one.
 *
 * Returns ranked references (`picture_id`, `likeness`, `face_id`), not
 * pictures: the caller fetches the pictures separately and re-applies this
 * ranking, because the ranking is the result and the id-list read does not
 * preserve order. `face_id` is the detection that produced the score, which is
 * the row a character assignment writes to.
 *
 * @param {number|string} sourceFaceId
 * @param {Object} [options]
 * @param {number} [options.topN=500]
 * @param {number} [options.threshold] - minimum likeness to include.
 * @returns {Promise<Array<Object>>} ranked matches (the response body).
 */
export async function faceSearch(
  sourceFaceId,
  { topN = 500, threshold } = {},
) {
  const params = new URLSearchParams({
    source_face_id: String(sourceFaceId),
    top_n: String(topN),
  });
  if (threshold != null) params.append("threshold", String(threshold));
  return unwrap(apiClient.post(
    `/pictures/face-search?${params.toString()}`,
  ));
}

/**
 * Find more pictures of a character, using their reference faces as the query.
 *
 * The pictures already assigned to that character are excluded server-side, so
 * the result set is the un-assigned candidates and its length is a count the UI
 * can put on an "assign these" button without over-promising.
 *
 * Ranked like {@link faceSearch}, and each match names the `face_id` that
 * matched so the assignment can target that detection.
 *
 * Each match also carries `reference_likeness`: the winning face's similarity
 * to every one of the person's reference faces, in query order. `likeness` is
 * the maximum of that row, so it says how *well* a candidate matches; the row
 * itself is the only thing that says how *many* references agree, which is what
 * the suggestion panel's second slider cuts on, client-side, so both knobs
 * re-cut the cached list without a round trip.
 *
 * @param {number|string} characterId
 * @param {Object} [options]
 * @param {number} [options.topN=500]
 * @param {number} [options.threshold=0.5] - fetch floor, deliberately looser
 *   than the UI's default cut so the slider can be widened without a refetch.
 * @param {boolean} [options.excludeAssigned=true]
 * @param {boolean} [options.includeReferenceScores=true]
 * @returns {Promise<Array<Object>>} ranked matches (the response body).
 */
export async function characterFaceSearch(
  characterId,
  {
    topN = 500,
    threshold = 0.5,
    excludeAssigned = true,
    includeReferenceScores = true,
  } = {},
) {
  const params = new URLSearchParams({
    source_character_id: String(characterId),
    top_n: String(topN),
    threshold: String(threshold),
  });
  if (excludeAssigned) {
    params.append("exclude_character_id", String(characterId));
  }
  if (includeReferenceScores) {
    params.append("include_reference_scores", "true");
  }
  return unwrap(apiClient.post(
    `/pictures/face-search?${params.toString()}`,
  ));
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
 * @returns {Promise<Array<Object>>} ranked matches (the response body).
 */
export async function likenessSearch(
  sourcePictureIds,
  { topN = 500, threshold = 0.05 } = {},
) {
  const params = new URLSearchParams();
  sourcePictureIds.forEach((id) =>
    params.append("source_picture_ids", String(id)),
  );
  params.append("top_n", String(topN));
  params.append("threshold", String(threshold));
  return unwrap(apiClient.post(
    `/pictures/likeness-search?${params.toString()}`,
  ));
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
 * @returns {Promise<Array<Object>>} the matching pictures (the response body).
 */
export async function searchPictures(
  text,
  { threshold = 0.1, topN = 10000, query = "" } = {},
) {
  return unwrap(apiClient.get(
    `/pictures/search?query=${encodeURIComponent(text)}&threshold=${threshold}&top_n=${topN}${query ? `&${query}` : ""}`,
  ));
}

/**
 * Read one picture's metadata.
 * @param {number|string} id
 * @param {Object} [options]
 * @param {boolean} [options.smartScore=false] - also compute the smart score,
 *   which is markedly more expensive than the plain read.
 * @param {string|number} [options.cacheBuster] - forces a fresh read past any
 *   HTTP cache; pass a changing value only when a stale answer is unacceptable.
 * @returns {Promise<Object>} the metadata (the response body).
 */
export async function getPictureMetadata(
  id,
  { smartScore = false, cacheBuster } = {},
) {
  const params = new URLSearchParams();
  if (smartScore) params.set("smart_score", "true");
  if (cacheBuster != null) params.set("cb", String(cacheBuster));
  const query = params.toString();
  return unwrap(apiClient.get(
    query
      ? `/pictures/${id}/metadata?${query}`
      : `/pictures/${id}/metadata`,
  ));
}

/**
 * List the faces detected in a picture.
 * @param {number|string} id
 * @returns {Promise<Array<Object>|Object>} the response body: either a bare
 *   array of faces or an object nesting them under `faces`, depending on
 *   server version.
 */
export async function listPictureFaces(id) {
  return unwrap(apiClient.get(`/pictures/${id}/faces`));
}

/**
 * List the object detections on a picture.
 * @param {number|string} id
 * @returns {Promise<Array<Object>>} the detection rows (the response body).
 */
export async function listPictureDetections(id) {
  return unwrap(apiClient.get(`/pictures/${id}/detections`));
}

/**
 * Add a hand-drawn face box to a picture.
 * @param {number|string} id
 * @param {Object} body - `{ bbox: [x1, y1, x2, y2], frame_index }`.
 * @returns {Promise<Object>} the response body.
 */
export async function addPictureFace(id, body) {
  return unwrap(apiClient.post(`/pictures/${id}/face`, body));
}

/**
 * Patch one picture's editable fields.
 * @param {number|string} id
 * @param {Object} body - only the keys to change.
 * @returns {Promise<Object>} the updated picture (the response body).
 */
export async function patchPicture(id, body) {
  return unwrap(apiClient.patch(`/pictures/${id}`, body));
}

/**
 * List the ComfyUI models referenced by pictures in the library.
 * @returns {Promise<Array<Object>>} the response body.
 */
export async function listComfyuiModels() {
  return unwrap(apiClient.get(`/pictures/comfyui_models`));
}

/**
 * List the ComfyUI LoRAs referenced by pictures in the library.
 * @returns {Promise<Array<Object>>} the response body.
 */
export async function listComfyuiLoras() {
  return unwrap(apiClient.get(`/pictures/comfyui_loras`));
}

/**
 * Resolve thumbnail URLs for a batch of pictures.
 * @param {Array<number|string>} ids
 * @returns {Promise<Object>} the response body: picture id → thumbnail record.
 */
export async function getThumbnails(ids) {
  return unwrap(apiClient.post(`/pictures/thumbnails`, { ids }));
}

/**
 * Soft-delete pictures (move them to the scrapheap).
 *
 * Pictures frozen by a locked set are refused and reported in
 * `skipped_locked`; the caller must keep those tiles rather than assume the
 * whole request applied.
 *
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<Object>} the response body, including `skipped_locked`.
 */
export async function deletePictures(pictureIds) {
  return unwrap(apiClient.delete(`/pictures`, {
    data: { picture_ids: pictureIds },
  }));
}

/**
 * Rotate pictures in place, by 90° in either direction or by 180°.
 *
 * The file's EXIF orientation tag is rewritten and the bitmap is left alone, so
 * this is not a new picture and nothing is stacked: the same id keeps the same
 * URL and only its bytes move. Owner-only - a share or otherwise scoped token
 * cannot call it at all.
 *
 * The response splits what happened three ways and a caller must read all
 * three: `rotated_picture_ids` (done, thumbnails will regenerate),
 * `unsupported_picture_ids` (the format cannot carry a rotation every renderer
 * agrees on - Filters > Rotate still makes a copy), and `skipped_picture_ids`
 * (a locked set, or the file is missing). `batch_id` groups the whole call as
 * one undo step.
 *
 * @param {Array<number|string>} pictureIds
 * @param {string} direction - `"cw"`, `"ccw"` or `"180"`.
 * @returns {Promise<Object>} the response body.
 */
export async function rotatePictures(pictureIds, direction) {
  return unwrap(apiClient.post(`/pictures/rotate`, {
    picture_ids: pictureIds,
    direction,
  }));
}

/**
 * Assign, add or remove a project on a set of pictures.
 * @param {Array<number|string>} pictureIds
 * @param {number|string|null} projectId
 * @param {Object} [options]
 * @param {"add"|"remove"} [options.mode] - omitted to SET the project,
 *   replacing whatever was there.
 * @returns {Promise<Object>} the response body.
 */
export async function setPicturesProject(
  pictureIds,
  projectId,
  { mode } = {},
) {
  const body = { picture_ids: pictureIds, project_id: projectId };
  if (mode) body.mode = mode;
  return unwrap(apiClient.patch(`/pictures/project`, body));
}

/**
 * Ask what a permanent scrapheap purge would destroy, before doing it.
 *
 * Callers MUST treat a rejection as "could not verify" and refuse to open the
 * destructive confirm, never as "nothing would be deleted".
 *
 * The response also carries `confirm_token`, a single-use, five-minute proof
 * bound to exactly this selection. `purgeScrapheap` will not delete without it,
 * so this call is a required step, not an optional courtesy.
 *
 * @param {Array<number|string>|null} [ids=null] - null means the whole heap.
 * @returns {Promise<Object>} the counts, the protected-file list, and
 *   `confirm_token`.
 */
export async function previewScrapheapDelete(
  ids = null,
) {
  return unwrap(apiClient.post(
    `/pictures/scrapheap/delete-preview`,
    { ids },
  ));
}

/**
 * Permanently delete pictures from the scrapheap.
 *
 * Omitting `pictureIds` empties the whole heap. Protected pictures are kept
 * unless `includeProtected` is set, and locked ones are always kept and
 * reported in `skipped_locked`.
 *
 * `confirmToken` is REQUIRED and comes from `previewScrapheapDelete` for the
 * same selection. The type-to-confirm dialog is a client control that proves
 * nothing to the server, so the server mints its own single-use confirmation
 * with the destruction preview and refuses this call without it (400 when it is
 * missing, 409 when it is spent, expired, or for a different selection).
 *
 * @param {Object} [options]
 * @param {Array<number|string>} [options.pictureIds]
 * @param {boolean} [options.includeProtected=false]
 * @param {string} options.confirmToken - from the matching preview.
 * @returns {Promise<Object>} the response body, including `skipped_locked`.
 */
export async function purgeScrapheap({
  pictureIds,
  includeProtected = false,
  confirmToken,
} = {}) {
  const data = {
    include_protected: includeProtected,
    confirm_token: confirmToken,
  };
  if (pictureIds) data.picture_ids = pictureIds;
  return unwrap(apiClient.delete(`/pictures/scrapheap`, { data }));
}

/**
 * Restore pictures from the scrapheap; omit the ids to restore all of them.
 * @param {Array<number|string>} [pictureIds]
 * @returns {Promise<Object>} the response body.
 */
export async function restoreScrapheap(pictureIds) {
  return unwrap(apiClient.post(
    `/pictures/scrapheap/restore`,
    pictureIds ? { picture_ids: pictureIds } : undefined,
  ));
}

/**
 * Ask the server host to reveal a picture in its desktop file manager.
 *
 * Fails on a headless or remote server, which has no file manager to open.
 *
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function openPictureLocation(id) {
  return unwrap(apiClient.post(`/pictures/${id}/open-location`));
}

/**
 * Queue object detection over a set of pictures.
 *
 * Runs as a background GPU task: this resolves once the work is accepted, and
 * the results arrive over the websocket.
 *
 * @param {Array<number|string>} pictureIds
 * @param {string} prompt
 * @returns {Promise<Object>} the response body.
 */
export async function detectPictures(pictureIds, prompt) {
  return unwrap(apiClient.post(`/pictures/detect`, {
    picture_ids: pictureIds,
    prompt,
  }));
}

/**
 * List the installed picture plugins.
 * @returns {Promise<Object>} the response body, whose `plugins` is the list.
 */
export async function listPicturePlugins() {
  return unwrap(apiClient.get(`/pictures/plugins`));
}

/**
 * Run a picture plugin over a set of pictures.
 * @param {string} name - the plugin's name (URL-encoded here).
 * @param {Object} body - `picture_ids`, `parameters`, optional `captions`,
 *   and whether to `stack` the outputs with their sources.
 * @returns {Promise<Object>} the response body.
 */
export async function runPicturePlugin(name, body) {
  return unwrap(apiClient.post(
    `/pictures/plugins/${encodeURIComponent(name)}`,
    body,
  ));
}

/**
 * Re-run tagging on one picture, replacing its generated tags.
 * @param {number|string} id
 * @param {Object} [body] - `{ model }` to pick a tagger, or empty for the
 *   configured one.
 * @returns {Promise<Object>} the response body.
 */
export async function resetPictureTags(id, body = {}) {
  return unwrap(apiClient.post(
    `/pictures/${id}/reset_tags`,
    body,
  ));
}

/**
 * Re-run captioning on one picture, replacing its description.
 * @param {number|string} id
 * @param {Object} [body] - `{ model }` to pick a captioner.
 * @returns {Promise<Object>} the response body.
 */
export async function resetPictureDescription(
  id,
  body = {},
) {
  return unwrap(apiClient.post(
    `/pictures/${id}/reset_description`,
    body,
  ));
}

/**
 * Re-run tagging on many pictures in one request. The backend marks them and
 * the background tagger batches them; nothing is queued per picture.
 * @param {Array<number|string>} pictureIds
 * @param {Object} [body] - `{ model }` to pick a tagger.
 * @returns {Promise<Object>} the response body: `count` marked.
 */
export async function resetPicturesTags(pictureIds, body = {}) {
  return unwrap(apiClient.post(
    "/pictures/reset_tags",
    { ...body, picture_ids: pictureIds },
  ));
}

/**
 * Re-run captioning on many pictures in one request; see `resetPicturesTags`.
 * @param {Array<number|string>} pictureIds
 * @param {Object} [body] - `{ model }` to pick a captioner.
 * @returns {Promise<Object>} the response body: `count` marked.
 */
export async function resetPicturesDescriptions(pictureIds, body = {}) {
  return unwrap(apiClient.post(
    "/pictures/reset_description",
    { ...body, picture_ids: pictureIds },
  ));
}

/**
 * Remove tags the model could not possibly be right about.
 * @param {Array<number|string>} pictureIds
 * @param {Object} filters - the scope the caller is viewing.
 * @returns {Promise<Object>} the response body: `count` and the `removed`
 *   picture/tag pairs, which are what an undo restores.
 */
export async function clearImpossibleTags(pictureIds, filters) {
  return unwrap(apiClient.post(
    `/pictures/impossible-tags/clear`,
    { picture_ids: pictureIds, filters },
  ));
}

/**
 * Put back the picture/tag pairs a previous clear removed.
 * @param {Array<Object>} pairs - as returned by {@link clearImpossibleTags}.
 * @returns {Promise<Object>} the response body.
 */
export async function restoreImpossibleTags(pairs) {
  return unwrap(apiClient.post(
    `/pictures/impossible-tags/restore`,
    { pairs },
  ));
}

/**
 * Write scores for a signed-in owner.
 * @param {Object} scores - picture id → score.
 * @param {Object} [options]
 * @param {boolean} [options.onlyUnscored=false] - leave already-scored
 *   pictures alone.
 * @returns {Promise<Object>} the response body.
 */
export async function applyScores(
  scores,
  { onlyUnscored = false } = {},
) {
  return unwrap(apiClient.post(`/pictures/apply-scores`, {
    scores,
    only_unscored: onlyUnscored,
  }));
}

/**
 * Read the scores collected from this guest session.
 * @returns {Promise<Object>} the response body, whose `scores` maps picture id
 *   to score.
 */
export async function getGuestScores() {
  return unwrap(apiClient.get(`/pictures/guest-scores`));
}

/**
 * Submit scores from a guest (share-token) session.
 * @param {Object} payload - `session_id`, `set_cookie`, and `scores`.
 * @returns {Promise<Object>} the response body.
 */
export async function submitGuestScores(payload) {
  return unwrap(apiClient.post(`/pictures/guest-scores`, payload));
}

/**
 * Start a ZIP export of a picture selection or filter scope.
 *
 * Exporting is a background task: this returns a `task_id` to poll with
 * {@link getExportStatus}, which eventually yields a `download_url` for
 * {@link downloadExport}.
 *
 * @param {string} [query=""] - pre-encoded selection/filter query string.
 * @returns {Promise<Object>} the response body, whose `task_id` drives polling.
 */
export async function startExport(query = "") {
  return unwrap(apiClient.get(
    query
      ? `/pictures/export?${query}`
      : `/pictures/export`,
  ));
}

/**
 * Start a folder export of a picture selection or filter scope (#291).
 *
 * The local-owner counterpart to {@link startExport}: instead of packaging a
 * ZIP to download, the server writes the pictures straight into a folder on
 * its own disk (`destination`, part of `query`) and opens it in the host
 * file manager once done. Poll the same {@link getExportStatus}; a completed
 * folder export never gets a `download_url`.
 *
 * @param {string} query - pre-encoded selection/filter query string,
 *   including `destination`.
 * @returns {Promise<Object>} the response body, whose `task_id` drives polling.
 */
export async function startFolderExport(query) {
  return unwrap(apiClient.post(`/pictures/export/folder?${query}`));
}

/**
 * Poll a running export.
 * @param {string} taskId
 * @returns {Promise<Object>} the response body: `status`, `processed`,
 *   `total`, and once complete, `download_url`.
 */
export async function getExportStatus(taskId) {
  return unwrap(apiClient.get(`/pictures/export/status`, {
    params: { task_id: taskId },
  }));
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
 * @returns {Promise<{blob: Blob, filename: string}>} the archive and the name
 *   the server gave it (falling back to `pixlstash_export.zip`).
 */
export async function downloadExport(downloadUrl) {
  const res = await apiClient.get(`${downloadUrl}`, {
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
 * Download one picture's original media bytes.
 *
 * @param {number|string} id
 * @param {string} format - file extension without a leading dot.
 * @param {Object} [options]
 * @param {number|string} [options.version] - pixel hash cache-buster.
 * @returns {Promise<Blob>} the picture media.
 */
export async function downloadPicture(
  id,
  format,
  { version } = {},
) {
  const ext = String(format || "").replace(/^\./, "").toLowerCase();
  if (!id || !ext) throw new Error("Picture id and format are required");
  const query =
    version === undefined || version === null
      ? ""
      : `?v=${encodeURIComponent(version)}`;
  return unwrap(apiClient.get(
    `/pictures/${id}.${encodeURIComponent(ext)}${query}`,
    { responseType: "blob" },
  ));
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
  return unwrap(apiClient.get(url, params ? { params } : undefined));
}

/**
 * Discard the guest scores collected in this browser session.
 * @returns {Promise<Object>} the response body.
 */
export async function clearGuestScoreSession() {
  return unwrap(apiClient.delete("/pictures/guest-scores/session"));
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
 * @returns {Promise<Array<Object>>} the pictures (the response body).
 */
export async function listPicturesByIds(ids, { fields } = {}) {
  const params = new URLSearchParams();
  ids.forEach((id) => params.append("id", String(id)));
  if (fields) params.append("fields", fields);
  return unwrap(apiClient.get(`/pictures?${params.toString()}`));
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
  return unwrap(apiClient.get(`/pictures/${pictureId}/anomaly_region`, {
    params: { tag },
  }));
}
