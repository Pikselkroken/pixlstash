// A workflow's LoRA chain, as the Edit LoRAs dialog edits it (#1478).
//
// The dialog, the inspector and the Save-as-recipe hand-over all have to agree
// on three things: what a LoRA is called on screen, how an edited list becomes
// `PUT /workflows/{key}/lora-chain`'s `entries`, and how a link names "open this
// card with Edit LoRAs… open". One module, so a second spelling of any of them
// cannot drift from the first.

/** The query value that opens Edit LoRAs… on the card `?card=` selects. */
export const EDIT_LORAS = "loras";

/**
 * A LoRA file as a person names it: no folder, no extension.
 *
 * `sub/Lightning-8step.safetensors` reads as `Lightning-8step`. The case is
 * kept, because it is how the owner spelled the file.
 *
 * @param {?string} filename
 * @returns {string}
 */
export function loraStem(filename) {
  const base = String(filename || "")
    .trim()
    .split(/[\\/]/)
    .pop();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

/**
 * The comparable form of a filename: lowercase, no folder.
 *
 * The shelf match is case-folded (`apply_adapter`), so a picture naming
 * `Hairstyle-V3.safetensors` and a graph naming `loras/hairstyle-v3.safetensors`
 * are the same loader.
 *
 * @param {?string} filename
 * @returns {string}
 */
export function loraBase(filename) {
  return String(filename || "")
    .trim()
    .toLowerCase()
    .split(/[\\/]/)
    .pop();
}

/**
 * Where "The workflow" in Save-as-recipe sends the owner.
 *
 * `/workflows?card=<key>&edit=loras&drop_lora=<filename>`: the card selected,
 * the rail open on its Workflow tab, Edit LoRAs… open, and the loader for
 * `drop_lora` already struck through. See `router/index.js` for the scheme and
 * `WorkflowTab.vue` for the half that honours it.
 *
 * @param {string} workflowKey
 * @param {{dropLora?: string}} [options]
 * @returns {{name: string, query: Object<string, string>}}
 */
export function editLorasRoute(workflowKey, { dropLora = "" } = {}) {
  const query = { card: workflowKey, edit: EDIT_LORAS };
  if (dropLora) query.drop_lora = dropLora;
  return { name: "workflows", query };
}

/**
 * How many rows have to move to turn `before` into `after`.
 *
 * The length of `after` minus its longest run that is still in `before`'s
 * order, so swapping two neighbours is ONE move, as the owner made it, and not
 * two rows that each changed position. Ids in only one of the lists are not
 * moves (they are a delete or an add) and are left out of both first.
 *
 * @param {Array<string>} before
 * @param {Array<string>} after
 * @returns {number}
 */
export function movesBetween(before, after) {
  const common = new Set(before.filter((id) => after.includes(id)));
  const rank = new Map(
    before.filter((id) => common.has(id)).map((id, index) => [id, index]),
  );
  const order = after.filter((id) => common.has(id)).map((id) => rank.get(id));
  // Longest increasing subsequence, O(n²): a chain is a handful of loaders.
  const best = order.map(() => 1);
  for (let i = 0; i < order.length; i += 1) {
    for (let j = 0; j < i; j += 1) {
      if (order[j] < order[i] && best[j] + 1 > best[i]) best[i] = best[j] + 1;
    }
  }
  const kept = best.length ? Math.max(...best) : 0;
  return order.length - kept;
}

/**
 * Whether two strengths are different numbers, to two decimals.
 *
 * Two decimals because that is what the field shows: 0.849999 and 0.85 are one
 * value on screen, and a change the owner cannot see is not one they made.
 *
 * @param {?number} left
 * @param {?number} right
 * @returns {boolean}
 */
export function strengthChanged(left, right) {
  if (left === null || left === undefined) return false;
  const a = Number(left);
  const b = Number(right);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  return Math.round(a * 100) !== Math.round(b * 100);
}

/**
 * `PUT …/lora-chain`'s `entries`: the rows still standing, in list order.
 *
 * An existing loader goes by `node_id`, so a move keeps its id; a new one by
 * the shelf digest it was picked as. A struck-through row is simply absent,
 * which is how the route reads a delete. `strength` is left off an existing
 * loader whose strength is wired or absent (`null`), because the route writes
 * whatever it is sent.
 *
 * @param {Array<Object>} rows - the dialog's rows.
 * @returns {Array<{node_id: ?string, sha256?: string, strength?: number}>}
 */
export function chainEntries(rows) {
  return rows
    .filter((row) => !row.deleted)
    .map((row) => {
      if (row.isNew) {
        return {
          node_id: null,
          sha256: row.sha256,
          strength: Number(row.strength),
        };
      }
      const entry = { node_id: String(row.nodeId) };
      if (row.strength !== null && row.strength !== undefined) {
        entry.strength = Number(row.strength);
      }
      return entry;
    });
}

/**
 * How many changes the dialog's footer counts.
 *
 * One per delete, per added loader, per re-weighted loader, and per row that
 * had to move (see `movesBetween`). A new row with no LoRA picked yet is not a
 * change: it has nothing the save could write.
 *
 * @param {Array<Object>} loaders - the chain as read, `GET …/lora-chain`.
 * @param {Array<Object>} rows - the dialog's rows.
 * @returns {number}
 */
export function countChanges(loaders, rows) {
  const before = (loaders || []).map((loader) => String(loader.node_id));
  const kept = rows.filter((row) => !row.isNew && !row.deleted);
  const deleted = rows.filter((row) => !row.isNew && row.deleted).length;
  const added = rows.filter((row) => row.isNew && row.sha256).length;
  const original = new Map(
    (loaders || []).map((loader) => [String(loader.node_id), loader]),
  );
  const weighted = kept.filter((row) =>
    strengthChanged(original.get(String(row.nodeId))?.strength, row.strength),
  ).length;
  const moved = movesBetween(
    before,
    kept.map((row) => String(row.nodeId)),
  );
  return deleted + added + weighted + moved;
}
