// The owner's favourites (#1578) - /favourites.
//
// One hub table for three kinds, keyed by stable identity: a model by sha256,
// a workflow by its card key, a workflow set by the grid's set key
// (`hand:<id>`, `model:<head id>`). Both calls answer with every favourite.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** @returns {Promise<{model: string[], workflow: string[], set: string[]}>} */
export const listFavourites = () => unwrap(apiClient.get("/favourites"));

/**
 * Mark or unmark favourites of one kind.
 *
 * @param {"model"|"workflow"|"set"} kind
 * @param {Array<string>} keys
 * @param {boolean} favourite - true marks, false unmarks.
 * @returns {Promise<{model: string[], workflow: string[], set: string[]}>}
 */
export const setFavourites = (kind, keys, favourite) =>
  unwrap(apiClient.put("/favourites", { kind, keys, favourite }));
