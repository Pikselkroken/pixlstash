// Tag groups resource - the library-wide map from a tag name to the group it
// is filed under (Character, Copyright, Meta, …). A group belongs to the tag,
// not to a picture: filing "film grain" under Meta files it on every picture.
//
// Every call answers with the map: `{ groups, tags, ungrouped_count,
// all_count }`. `groups` is in rail order; `tags` as `{ "<tag>": <group id> }`
// covers only the tags on some picture in the library. A tag missing from
// `tags` is Ungrouped. The Groups board pages its tag lists from
// `listGroupTags`, since a Danbooru import files far more tags than the
// library holds.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Read the groups and which tag sits in which.
 * @returns {Promise<{groups: Array<{id:number,name:string,position:number,tag_count:number}>, tags: Object<string, number>, ungrouped_count: number, all_count: number}>}
 */
export async function getTagGroups() {
  return unwrap(apiClient.get(`/tag_groups`));
}

/**
 * One page of the tags a choice holds, with each tag's picture count in the
 * library (0 for a known tag nobody has). Every known tag is listed, so a
 * group's page can run past the library's tags.
 * @param {Object} options
 * @param {string} options.group - a group id, `none` (Ungrouped) or `all`.
 * @param {string} [options.q] - a substring of the tag name.
 * @param {"pictures"|"name"} [options.sort]
 * @param {number} [options.offset]
 * @param {number} [options.limit] - at most 500.
 * @returns {Promise<{total: number, tags: Array<{tag: string, count: number, group_id: number|null}>}>}
 */
export async function listGroupTags({ group, q, sort, offset, limit } = {}) {
  return unwrap(
    apiClient.get(`/tag_groups/tags`, {
      params: { group, q, sort, offset, limit },
    }),
  );
}

/**
 * Make a group, last in the order.
 * @param {string} name
 * @returns {Promise<Object>} the whole map, plus `group`: the new one.
 */
export async function createTagGroup(name) {
  return unwrap(apiClient.post(`/tag_groups`, { name }));
}

/**
 * Put the groups in this order (the rail's order).
 * @param {Array<number>} ids - every group id, first to last.
 * @returns {Promise<Object>} the whole map.
 */
export async function reorderTagGroups(ids) {
  return unwrap(apiClient.put(`/tag_groups/order`, { ids }));
}

/**
 * File tags under a group, on every picture; `groupId` null makes them
 * Ungrouped. One undoable operation.
 * @param {Array<string>} tags
 * @param {number|null} groupId
 * @returns {Promise<Object>} the whole map.
 */
export async function assignTagsToGroup(tags, groupId) {
  return unwrap(
    apiClient.post(`/tag_groups/assign`, { tags, group_id: groupId }),
  );
}

/**
 * Import a Danbooru tag database (.xlsx or .csv with english / category /
 * subcategory columns) as the library's tag groups. The file always wins over
 * PixlStash's default filings. Tags the user filed by hand, in another group
 * in the file, move only with `overwriteUser`. One undoable operation unless
 * `dryRun`, which parses and answers the plan without changing anything.
 *
 * Sent as multipart: the content type is left to the browser (the transport
 * clears the JSON default for a FormData body). The timeout is raised because
 * a 13 MB sheet takes seconds to parse, and a timed-out commit may still have
 * landed.
 *
 * @param {File|Blob} file
 * @param {{dryRun?: boolean, overwriteUser?: boolean}} [options]
 * @returns {Promise<{plan: Object, groups?: Array, tags?: Object}>} the plan,
 *   plus the whole map when this was not a dry run.
 */
export async function importTagGroups(file, { dryRun = false, overwriteUser = false } = {}) {
  const form = new FormData();
  form.append("file", file);
  form.append("dry_run", dryRun ? "true" : "false");
  form.append("overwrite_user", overwriteUser ? "true" : "false");
  return unwrap(
    apiClient.post(`/tag_groups/import`, form, { timeout: 180_000 }),
  );
}
