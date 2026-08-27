// Projects resource - /projects.
//
// A picture belongs to at most one project, which is why membership is read as
// assignments plus an explicit unassigned list rather than a set per project.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Build a projects route, optionally under an explicit backend base.
 * @param {string} [path=""] - the route below `/projects`.
 * @returns {string}
 */
function projectsUrl(path = "") {
  return `/projects${path}`;
}

/**
 * List projects.
 * @param {Object} [options]
 * @param {Object} [options.params] - optional query params.
 * @returns {Promise<Array<Object>>} the project list (the response body).
 */
export async function listProjects({ params } = {}) {
  return unwrap(apiClient.get(
    projectsUrl(""),
    params ? { params } : undefined,
  ));
}

/**
 * Create a project.
 * @param {Object} body - `{ name, description }`.
 * @returns {Promise<Object>} the created project (the response body).
 */
export async function createProject(body) {
  return unwrap(apiClient.post(projectsUrl(""), body));
}

/**
 * Replace a project's editable fields.
 *
 * This is a PUT, not a PATCH: the editor always sends the whole
 * `{ name, description }` pair, so an omitted description clears it.
 *
 * @param {number|string} id
 * @param {Object} body - `{ name, description }`.
 * @returns {Promise<Object>} the updated project (the response body).
 */
export async function updateProject(id, body) {
  return unwrap(apiClient.put(projectsUrl(`/${id}`), body));
}

/**
 * Delete a project. Its pictures survive and become unassigned.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteProject(id) {
  return unwrap(apiClient.delete(projectsUrl(`/${id}`)));
}

/**
 * Read a project's summary counts.
 *
 * `"UNASSIGNED"` is accepted as the id for the pictures in no project.
 *
 * @param {number|string} id
 * @param {Object} [params] - optional scope params such as `apply_tag_filter`.
 * @returns {Promise<Object>} the response body, whose `image_count` is the
 *   number of pictures in scope.
 */
export async function getProjectSummary(id, params) {
  return unwrap(apiClient.get(
    projectsUrl(`/${id}/summary`),
    params ? { params } : undefined,
  ));
}

/**
 * Ask which project each of the given pictures belongs to.
 *
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<Object>} the response body: `project_assignments`
 *   (project id → picture ids) and `unassigned_picture_ids`.
 */
export async function getProjectMembership(pictureIds) {
  return unwrap(apiClient.post(projectsUrl("/membership"), {
    picture_ids: pictureIds,
  }));
}

/**
 * List a project's attachments (uploaded files and saved links).
 * @param {number|string} id
 * @returns {Promise<Array<Object>>} the attachments (the response body).
 */
export async function listProjectAttachments(id) {
  return unwrap(apiClient.get(projectsUrl(`/${id}/attachments`)));
}

/**
 * Upload a file as a project attachment.
 *
 * Sent as multipart, so the content type is set explicitly rather than left to
 * the JSON default.
 *
 * @param {number|string} id
 * @param {File|Blob} file
 * @returns {Promise<Object>} the created attachment (the response body).
 */
export async function uploadProjectAttachment(id, file) {
  const form = new FormData();
  form.append("file", file);
  return unwrap(apiClient.post(
    projectsUrl(`/${id}/attachments`),
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  ));
}

/**
 * Save a link as a project attachment.
 * @param {number|string} id
 * @param {string} url
 * @param {string} title
 * @returns {Promise<Object>} the created attachment (the response body).
 */
export async function addProjectAttachmentUrl(
  id,
  url,
  title,
) {
  return unwrap(apiClient.post(
    projectsUrl(`/${id}/attachments/url`),
    { url, title },
  ));
}

/**
 * Remove one project attachment.
 * @param {number|string} id
 * @param {number|string} attachmentId
 * @returns {Promise<Object>} the response body.
 */
export async function deleteProjectAttachment(id, attachmentId) {
  return unwrap(apiClient.delete(
    projectsUrl(`/${id}/attachments/${attachmentId}`),
  ));
}
