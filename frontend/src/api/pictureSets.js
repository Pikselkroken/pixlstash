// Picture sets resource — /picture_sets.
//
// Seeded with the read used by the review-scope pickers; the membership and
// locked-member endpoints join it as their call sites migrate.

import { apiClient } from "../utils/apiClient";

/**
 * List picture sets.
 *
 * The list includes the internal `reference_pictures` set; callers that show
 * user-facing sets filter it out themselves.
 *
 * @param {Object} [params] - optional query params.
 * @returns {Promise<Array<Object>>} the picture-set list (the response body).
 */
export async function listPictureSets(params) {
  const res = await apiClient.get(
    "/picture_sets",
    params ? { params } : undefined,
  );
  return res.data;
}
