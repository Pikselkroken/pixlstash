// The model shelf's icon store - /models/{id}/icon, /model-icons/{sha256}.
//
// An icon is what a model IS: authored, singular, chosen once. It is not a
// sample, which is what a model produces. The store is content-addressed, so
// forty models given one logo share one file and one cached response.

import { API_BASE_URL, apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * The URL of one stored icon, for an `<img src>`.
 *
 * Addressed by CONTENT hash rather than by model id, which is the point: every
 * row sharing a mark resolves to one URL, so the browser fetches and caches it
 * once however many rows are on screen.
 *
 * @param {string} sha256
 * @returns {string}
 */
export function modelIconUrl(sha256) {
  // An <img src> never reaches the apiClient interceptor, so the API base has
  // to be spelled out here rather than left for axios to prepend.
  return `${API_BASE_URL}/model-icons/${encodeURIComponent(sha256)}`;
}

/**
 * Set a model's icon from image bytes.
 *
 * The single write path for all three ways of choosing one - uploading a file,
 * picking a library picture, promoting a sample - because all three produce the
 * same thing: bytes in the store and a hash in the column. Picking a picture
 * therefore sends the *pixels*, which is what makes the icon a copy rather than
 * a reference into the vault.
 *
 * @param {number} modelId
 * @param {File|Blob} file - PNG, JPEG or WebP. Checked server-side by magic
 *   bytes, not by name or declared type.
 * @returns {Promise<{model_id: number, icon_sha256: string}>}
 */
export async function setModelIcon(modelId, file) {
  const form = new FormData();
  form.append("file", file);
  return unwrap(apiClient.post(`/models/${modelId}/icon`, form));
}

/**
 * Clear the icon on one or more models.
 *
 * The stored file is deliberately left on disk: the store is shared, so another
 * model may name the same hash. The response reports what actually changed, not
 * what was sent.
 *
 * @param {Array<number>} ids
 * @returns {Promise<{cleared: Array<number>}>}
 */
export async function clearModelIcons(ids) {
  return unwrap(apiClient.post("/models/icons/clear", { ids }));
}
