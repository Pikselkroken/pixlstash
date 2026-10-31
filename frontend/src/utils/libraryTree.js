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

/**
 * The Folders tree as `DEFAULT_LAYOUT` files the library (Project › Person or
 * Set, then Global, then Unassigned), flattened into the rows the sidebar
 * draws. Folders come from assignments, so a person in two projects is a
 * folder in both, and every set of a project sits directly under it (a set
 * nested under a person or set in Library is still its own folder on disk).
 *
 * With a query, only folders whose name matches or that hold a match are
 * drawn, opened for the search; a folder kept only as context is `dim`.
 * Global and Unassigned are not names, so a search never matches them.
 *
 * @param {Object} opts
 * @param {Array<Object>} opts.projects - sorted project rows.
 * @param {Array<Object>} opts.characters - sorted character rows.
 * @param {Array<Object>} opts.sets - sorted, non-reference set rows.
 * @param {(key: string) => boolean} opts.isOpen - the remembered expand state.
 * @param {string} [opts.query] - the Find text.
 * @param {boolean} [opts.withGlobal=true] - false for a project share token,
 *   whose tree is rooted at its own project.
 * @returns {Array<Object>} rows: `{key, kind, depth, projectId, entity, name,
 *   childCount, open, match, dim}`, plus `last` on a child; `kind` is
 *   project, global, unassigned, character or set.
 */
export function folderTreeRows({
  projects,
  characters,
  sets,
  isOpen,
  query = "",
  withGlobal = true,
}) {
  const finding = Boolean((query || "").trim());
  const tops = projects.map((p) => ({
    key: `f:p:${p.id}`,
    kind: "project",
    projectId: p.id,
    entity: p,
    name: p.name,
  }));
  if (withGlobal) {
    tops.push(
      { key: "f:global", kind: "global", projectId: null, name: "Global" },
      { key: "f:unassigned", kind: "unassigned", projectId: null, name: "Unassigned" },
    );
  }

  const rows = [];
  for (const top of tops) {
    const kids =
      top.kind === "unassigned"
        ? []
        : [
            ...characters
              .filter((c) => inProjectBranch(c, top.projectId))
              .map((c) => ({ kind: "character", entity: c })),
            ...sets
              .filter((s) => inProjectBranch(s, top.projectId))
              .map((s) => ({ kind: "set", entity: s })),
          ].map((kid) => ({
            ...kid,
            key: `${top.key}/${kid.kind === "character" ? "c" : "s"}:${kid.entity.id}`,
            depth: 1,
            projectId: top.projectId,
            name: kid.entity.name,
            childCount: 0,
            open: false,
            match: findMatch(kid.entity.name, query),
            dim: false,
          }));
    const topMatch = top.kind === "project" ? findMatch(top.name, query) : null;
    const shownKids = finding ? kids.filter((k) => k.match) : kids;
    if (finding && !topMatch && !shownKids.length) continue;
    const open = finding ? shownKids.length > 0 : isOpen(top.key);
    rows.push({
      ...top,
      depth: 0,
      childCount: kids.length,
      open,
      match: topMatch,
      dim: finding && !topMatch,
    });
    if (open) {
      rows.push(
        ...shownKids.map((kid, i) => ({ ...kid, last: i === shownKids.length - 1 })),
      );
    }
  }
  return rows;
}
