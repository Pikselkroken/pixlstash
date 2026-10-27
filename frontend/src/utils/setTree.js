// Child sets (#2124): a picture set may sit under another set or a person.
//
// Pure helpers for the sidebar tree, kept out of SideBar.vue so the shape
// (which set is a root, which rows an expanded branch shows, where each row's
// guide line falls) is unit-testable without mounting the sidebar.

/**
 * Split the listed sets into roots and children.
 *
 * A set whose parent is not listed (another project, a share link, a parent
 * filtered out) is a root, so no set ever disappears from the sidebar. A set
 * caught in a stored cycle is a root too.
 *
 * @param {Array<Object>} sets - the sets the sidebar lists, in display order.
 * @param {Iterable<number>} characterIds - the people the sidebar lists.
 * @returns {{roots: Array<Object>, bySet: Map<number, Array<Object>>,
 *   byCharacter: Map<number, Array<Object>>}}
 */
export function buildSetTree(sets, characterIds) {
  const list = Array.isArray(sets) ? sets : [];
  const listed = new Map(list.map((s) => [Number(s.id), s]));
  const people = new Set([...(characterIds ?? [])].map(Number));
  const bySet = new Map();
  const byCharacter = new Map();
  const roots = [];
  const push = (map, key, s) => {
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(s);
  };
  for (const s of list) {
    const parentSet = s.parent_set_id == null ? null : Number(s.parent_set_id);
    const parentChar =
      s.parent_character_id == null ? null : Number(s.parent_character_id);
    if (parentSet != null && listed.has(parentSet) && !inCycle(s, listed)) {
      push(bySet, parentSet, s);
    } else if (parentChar != null && people.has(parentChar)) {
      push(byCharacter, parentChar, s);
    } else {
      roots.push(s);
    }
  }
  return { roots, bySet, byCharacter };
}

function inCycle(start, listed) {
  const seen = new Set([Number(start.id)]);
  let current = start.parent_set_id;
  while (current != null && listed.has(Number(current))) {
    if (seen.has(Number(current))) return true;
    seen.add(Number(current));
    current = listed.get(Number(current)).parent_set_id;
  }
  return false;
}

/**
 * The rows an expanded branch shows, depth first.
 *
 * Each row says its tree `level` (1 for a direct child), whether it is the
 * `last` of its siblings (it ends in an elbow), and the `rails`: the ancestor
 * levels whose guide line runs on past it because that ancestor has a later
 * sibling.
 *
 * @param {Array<Object>} children - the branch's direct children.
 * @param {Map<number, Array<Object>>} bySet - from buildSetTree.
 * @param {(id: number) => boolean} isExpanded - whether a set shows its children.
 * @returns {Array<{set: Object, level: number, last: boolean, rails: number[],
 *   childCount: number, expanded: boolean}>}
 */
export function flattenBranch(children, bySet, isExpanded) {
  const rows = [];
  const walk = (kids, level, rails, seen) => {
    kids.forEach((set, index) => {
      const id = Number(set.id);
      if (seen.has(id)) return;
      const last = index === kids.length - 1;
      const own = bySet.get(id) ?? [];
      const expanded = own.length > 0 && isExpanded(id);
      rows.push({ set, level, last, rails, childCount: own.length, expanded });
      if (expanded) {
        walk(own, level + 1, last ? rails : [...rails, level], new Set([...seen, id]));
      }
    });
  };
  walk(children ?? [], 1, [], new Set());
  return rows;
}

// A child hangs just past the centre of its parent's mark: the guide drops
// from that centre, then the child's own caret slot (--gutter-glyph, reserved
// whether or not it has children, as at the top level), then its mark. The
// owner ruled under-the-name too deep, and carets in the far-left gutter read
// as belonging to nothing.
const CARET_SLOT = "var(--space-1) + var(--gutter-glyph)";
const LEVEL_ONE = `var(--sidebar-thumb-size) / 2 + ${CARET_SLOT}`;
const DEEPER_STEP = `(var(--tree-kid-thumb) / 2 + ${CARET_SLOT})`;

/**
 * Where the guide line of a level-`level` row falls, from the start of its
 * spacer: under the centre of its parent's mark.
 *
 * @param {number} level
 * @returns {string} a CSS length.
 */
export function treeGuideX(level) {
  if (level <= 1) return "calc(var(--sidebar-thumb-size) / 2)";
  return `calc(${LEVEL_ONE} + ${level - 2} * ${DEEPER_STEP} + var(--tree-kid-thumb) / 2)`;
}

/**
 * The width of a level-`level` row's spacer, which places its mark.
 *
 * @param {number} level
 * @returns {string} a CSS length.
 */
export function treeSpacerWidth(level) {
  return `calc(${LEVEL_ONE} + ${Math.max(0, level - 1)} * ${DEEPER_STEP})`;
}

/**
 * The ids of a set's ancestors that are listed, nearest first. Used to open the
 * branch the selection lives in.
 *
 * @param {number} setId
 * @param {Array<Object>} sets
 * @returns {{setIds: number[], characterId: number|null}}
 */
export function ancestorsOf(setId, sets) {
  const byId = new Map((sets ?? []).map((s) => [Number(s.id), s]));
  const setIds = [];
  let characterId = null;
  const seen = new Set();
  let current = byId.get(Number(setId));
  while (current && !seen.has(Number(current.id))) {
    seen.add(Number(current.id));
    if (current.parent_character_id != null) {
      characterId = Number(current.parent_character_id);
    }
    if (current.parent_set_id == null) break;
    const parent = byId.get(Number(current.parent_set_id));
    if (!parent) break;
    setIds.push(Number(parent.id));
    current = parent;
  }
  return { setIds, characterId };
}
