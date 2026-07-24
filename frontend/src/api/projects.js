// Projects resource — /projects.
//
// Seeded with the read used by the review-scope pickers; the membership and
// picture-assignment endpoints join it as their call sites migrate.

import { apiClient } from "../utils/apiClient";

/**
 * List projects.
 * @param {Object} [params] - optional query params.
 * @returns {Promise<Array<Object>>} the project list (the response body).
 */
export async function listProjects(params) {
  const res = await apiClient.get("/projects", params ? { params } : undefined);
  return res.data;
}
