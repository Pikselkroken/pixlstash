// Tag groups resource - the library-wide map from a tag name to the group it
// is filed under (Character, Copyright, Meta, …). A group belongs to the tag,
// not to a picture: filing "film grain" under Meta files it on every picture.
//
// Every call answers with the whole map, `{ groups, tags }`: `groups` in rail
// order, `tags` as `{ "<tag>": <group id> }`. A tag missing from `tags` is
// Ungrouped.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Read the groups and which tag sits in which.
 * @returns {Promise<{groups: Array<{id:number,name:string,position:number}>, tags: Object<string, number>}>}
 */
export async function getTagGroups() {
  return unwrap(apiClient.get(`/tag_groups`));
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
