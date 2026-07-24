// Projects resource — /projects.
//
// A picture belongs to at most one project, which is why membership is read as
// assignments plus an explicit unassigned list rather than a set per project.

import { apiClient } from "../utils/apiClient";

/**
 * Build a projects route, optionally under an explicit backend base.
 * @param {string} [path=""] - the route below `/projects`.
 * @param {string} [baseUrl=""]
 * @returns {string}
 */
function projectsUrl(path = "", baseUrl = "") {
  return `${baseUrl}/projects${path}`;
}

/**
 * List projects.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @param {Object} [options.params] - optional query params.
 * @returns {Promise<Array<Object>>} the project list (the response body).
 */
export async function listProjects({ baseUrl = "", params } = {}) {
  const res = await apiClient.get(
    projectsUrl("", baseUrl),
    params ? { params } : undefined,
  );
  return res.data;
}

/**
 * Create a project.
 * @param {Object} body - `{ name, description }`.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the created project (the response body).
 */
export async function createProject(body, { baseUrl = "" } = {}) {
  const res = await apiClient.post(projectsUrl("", baseUrl), body);
  return res.data;
}

/**
 * Replace a project's editable fields.
 *
 * This is a PUT, not a PATCH: the editor always sends the whole
 * `{ name, description }` pair, so an omitted description clears it.
 *
 * @param {number|string} id
 * @param {Object} body - `{ name, description }`.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the updated project (the response body).
 */
export async function updateProject(id, body, { baseUrl = "" } = {}) {
  const res = await apiClient.put(projectsUrl(`/${id}`, baseUrl), body);
  return res.data;
}

/**
 * Delete a project. Its pictures survive and become unassigned.
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function deleteProject(id, { baseUrl = "" } = {}) {
  const res = await apiClient.delete(projectsUrl(`/${id}`, baseUrl));
  return res.data;
}

/**
 * Read a project's summary counts.
 *
 * `"UNASSIGNED"` is accepted as the id for the pictures in no project.
 *
 * @param {number|string} id
 * @param {Object} [params] - optional scope params such as `apply_tag_filter`.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `image_count` is the
 *   number of pictures in scope.
 */
export async function getProjectSummary(id, params, { baseUrl = "" } = {}) {
  const res = await apiClient.get(
    projectsUrl(`/${id}/summary`, baseUrl),
    params ? { params } : undefined,
  );
  return res.data;
}

/**
 * Ask which project each of the given pictures belongs to.
 *
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body: `project_assignments`
 *   (project id → picture ids) and `unassigned_picture_ids`.
 */
export async function getProjectMembership(pictureIds, { baseUrl = "" } = {}) {
  const res = await apiClient.post(projectsUrl("/membership", baseUrl), {
    picture_ids: pictureIds,
  });
  return res.data;
}
