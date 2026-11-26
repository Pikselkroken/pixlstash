// The Studio resource - /studio (#2819): scenes, their takes, and which
// picture sets are outfits, locations and props. Owner only.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Everything the Studio lists in one scope.
 *
 * @param {"any"|"none"|number} project
 * @returns {Promise<{scenes: Array, cast: Array, outfits: Array, locations: Array, props: Array}>}
 */
export async function getStudioOverview(project = "any") {
  return unwrap(
    apiClient.get(
      "/studio/overview",
      project === "any" ? undefined : { params: { project_id: project } },
    ),
  );
}

/** @returns {Promise<Object>} the new scene. */
export async function createScene(body) {
  return unwrap(apiClient.post("/studio/scenes", body));
}

/**
 * One scene and its takes. Each shoot that ended since the last read is filed
 * first, and `ended` says how each ended.
 */
export async function getScene(sceneId) {
  return unwrap(apiClient.get(`/studio/scenes/${sceneId}`));
}

/** Change a scene: only the fields sent; `cast` and `props` replace whole. */
export async function patchScene(sceneId, body) {
  return unwrap(apiClient.patch(`/studio/scenes/${sceneId}`, body));
}

export async function deleteScene(sceneId) {
  return unwrap(apiClient.delete(`/studio/scenes/${sceneId}`));
}

/**
 * The prompt and reference pictures one shoot of the scene sends.
 *
 * @param {number} sceneId
 * @param {{kind?: "still"|"clip", from_picture_id?: number, max_references?: number}} body
 * @returns {Promise<{prompt: string, negative: string, references: number[], loras: string[]}>}
 */
export async function writeShot(sceneId, body) {
  return unwrap(apiClient.post(`/studio/scenes/${sceneId}/shot`, body));
}

/** Follow the runs a shoot started, so their pictures become takes. */
export async function followSceneRuns(sceneId, runs) {
  return unwrap(apiClient.post(`/studio/scenes/${sceneId}/runs`, { runs }));
}

export async function removeTake(sceneId, pictureId) {
  return unwrap(apiClient.delete(`/studio/scenes/${sceneId}/takes/${pictureId}`));
}

/** Use a picture set in the Studio as an outfit, a location or a prop. */
export async function listSetInStudio(setId, kind) {
  return unwrap(apiClient.put(`/studio/items/${setId}`, { kind }));
}

export async function stopUsingSetInStudio(setId) {
  return unwrap(apiClient.delete(`/studio/items/${setId}`));
}

export async function setStudioFavourite(kind, itemId, favourite) {
  return unwrap(
    apiClient.put("/studio/favourites", { kind, item_id: itemId, favourite }),
  );
}
