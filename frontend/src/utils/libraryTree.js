// The Media sidebar's two drawings of the library (#2448), below the Library /
// Folders switch, and the name match its Find field filters them with.
//
// Pure, so the rules that decide which rows exist are pinned by a unit test
// rather than by mounting the sidebar: a row that silently goes missing from a
// tree is the failure nobody notices.

import { getEntityProjectIds } from "./projectMembership.js";

/**
 * True when the entity is filed under `projectId`; `null` means "in no project".
 *
 * @param {Object} entity - a character or picture-set row.
 * @param {number|null} projectId
 */
export function inProjectBranch(entity, projectId) {
  const ids = getEntityProjectIds(entity);
  return projectId == null ? ids.length === 0 : ids.includes(Number(projectId));
}

/**
 * Split a name round the first case-insensitive occurrence of `query`, for an
 * underlined match. Null when the query is empty or the name does not hold it.
 *
 * @param {string} name
 * @param {string} query
 * @returns {{before: string, match: string, after: string}|null}
 */
export function findMatch(name, query) {
  const needle = (query || "").trim().toLowerCase();
  if (!needle || typeof name !== "string") return null;
  const at = name.toLowerCase().indexOf(needle);
  if (at < 0) return null;
  return {
    before: name.slice(0, at),
    match: name.slice(at, at + needle.length),
    after: name.slice(at + needle.length),
  };
}

// Siblings in the Folders tree: projects, people, sets, the owner's own
// folders, then Global and Unassigned last, as the layout writes them.
const KIND_ORDER = { project: 0, character: 1, set: 2, folder: 3, global: 4, unassigned: 5 };

/**
 * The Folders tree (#2458): the folders pictures are filed in, as
 * `GET /library/folders` lists them, nested as they are on disk and flattened
 * into the rows the sidebar draws. A level the library's layout reads as a
 * project, person or set carries that entity (its menu, its drops, its click);
 * one whose entity is not in the given lists is drawn as a plain folder.
 *
 * With a query, only folders whose name matches or that hold a match are
 * drawn, opened for the search; a folder kept only as context is `dim`.
 * Global and Unassigned are not names, so a search never matches them.
 *
 * @param {Object} opts
 * @param {Array<Object>} opts.folders - `{path, name, kind, entity_id,
 *   project_id, count}`, every ancestor of a folder included.
 * @param {Array<Object>} opts.projects - project rows.
 * @param {Array<Object>} opts.characters - character rows.
 * @param {Array<Object>} opts.sets - non-reference set rows.
 * @param {(key: string) => boolean} opts.isOpen - the remembered expand state.
 * @param {string} [opts.query] - the Find text.
 * @returns {Array<Object>} rows: `{key, path, kind, depth, projectId, entity,
 *   name, count, childCount, open, match, dim, last, rails}`; `kind` is
 *   project, character, set, folder, global or unassigned, and `rails` the
 *   depths of the ancestors whose guide line runs on through the row.
 */
export function folderTreeRows({
  folders,
  projects,
  characters,
  sets,
  isOpen,
  query = "",
}) {
  const finding = Boolean((query || "").trim());
  const byId = {
    project: new Map(projects.map((e) => [e.id, e])),
    character: new Map(characters.map((e) => [e.id, e])),
    set: new Map(sets.map((e) => [e.id, e])),
  };
  const nodes = new Map();
  const tops = [];
  for (const folder of folders) {
    const entity = byId[folder.kind]?.get(folder.entity_id) ?? null;
    const kind = byId[folder.kind] && !entity ? "folder" : folder.kind;
    const node = {
      key: `f:${folder.path}`,
      path: folder.path,
      kind,
      entity,
      projectId: folder.project_id ?? null,
      name: folder.name,
      count: folder.count,
      match:
        kind === "global" || kind === "unassigned"
          ? null
          : findMatch(folder.name, query),
      children: [],
    };
    nodes.set(folder.path, node);
    const cut = folder.path.lastIndexOf("/");
    const parent = cut < 0 ? null : nodes.get(folder.path.slice(0, cut));
    (parent ? parent.children : tops).push(node);
  }
  const order = (a, b) =>
    KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.name.localeCompare(b.name);
  const held = new Map();
  const holds = (node) => {
    if (!held.has(node)) {
      held.set(node, Boolean(node.match) || node.children.some(holds));
    }
    return held.get(node);
  };

  const rows = [];
  const emit = (siblings, depth, rails) => {
    const shown = (finding ? siblings.filter(holds) : siblings).sort(order);
    shown.forEach((node, i) => {
      const { children, ...row } = node;
      const shownKids = finding ? children.filter(holds) : children;
      const open = finding ? shownKids.length > 0 : isOpen(node.key);
      const last = i === shown.length - 1;
      rows.push({
        ...row,
        depth,
        childCount: children.length,
        open,
        dim: finding && !node.match,
        last,
        rails,
      });
      // A level whose row has siblings still to come keeps its guide running
      // past this row's children.
      if (open) emit(children, depth + 1, depth > 0 && !last ? [...rails, depth] : rails);
    });
  };
  emit(tops, 0, []);
  return rows;
}
