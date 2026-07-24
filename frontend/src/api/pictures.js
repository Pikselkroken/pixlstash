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
