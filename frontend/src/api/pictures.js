// Pictures resource — /pictures.
//
// The largest resource in the app. Seeded here with the reads whose call sites
// have already migrated; the counts, scores, thumbnails, and export endpoints
// join it as the grid and overlay move over.

import { apiClient } from "../utils/apiClient";

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
