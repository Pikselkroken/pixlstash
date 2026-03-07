export function getTagLabel(tag) {
  if (typeof tag === 'string') return tag;
  if (tag && typeof tag === 'object') return String(tag.tag || '');
  return '';
}

export function getTagId(tag) {
  if (tag && typeof tag === 'object' && tag.id != null) {
    return tag.id;
  }
  return null;
}

export function TagItem(tag) {
  const label = getTagLabel(tag).trim();
  if (!label) return null;
  return {id: getTagId(tag), tag: label};
}

export function getTagList(tags) {
  return (Array.isArray(tags) ? tags : []).map(TagItem).filter(Boolean);
}

export function dedupeTagList(tags) {
  const byTag = new Map();
  for (const tag of tags) {
    if (!tag || !tag.tag) continue;
    const key = String(tag.tag).trim().toLowerCase();
    if (!key) continue;
    const existing = byTag.get(key);
    if (!existing || (existing.id == null && tag.id != null)) {
      byTag.set(key, tag);
    }
  }
  return Array.from(byTag.values())
      .sort(
          (a, b) =>
              a.tag.localeCompare(b.tag, undefined, {sensitivity: 'base'}),
      );
}

export function tagMatches(tag, target) {
  if (!tag) return false;
  if (tag.id != null && target?.id != null) {
    return String(tag.id) === String(target.id);
  }
  if (target?.tag) return tag.tag === target.tag;
  if (typeof target === 'string') return tag.tag === target;
  return false;
}

export function hasPenalisedTags(img) {
  return Array.isArray(img?.penalised_tags) && img.penalised_tags.length > 0;
}

export function penalisedTagsTitle(img) {
  const tags = Array.isArray(img?.penalised_tags) ? img.penalised_tags : [];
  if (!tags.length) return '';
  return `Penalised tags: ${tags.join(', ')}`;
}
