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

// Sum the configured weights for all penalised tags present on img.
// Falls back to tag count (weight 1 each) when no weights map is provided.
function _penalisedTotalWeight(img, weights) {
  const tags = Array.isArray(img?.penalised_tags) ? img.penalised_tags : [];
  if (!tags.length) return 0;
  if (weights && typeof weights === 'object' && Object.keys(weights).length > 0) {
    return tags.reduce((sum, t) => {
      const key = String(t || '').trim().toLowerCase();
      return sum + (weights[key] ?? 1);
    }, 0);
  }
  return tags.length;
}

// Returns 0 (mild), 1 (moderate) or 2 (severe) based on combined weight.
// Thresholds: <7 = mild, 7-12 = moderate, >12 = severe.
// With default weight 3: 1-2 tags → amber, 3+ tags → orange, 5+ tags → red.
function _penalisedSeverity(img, weights) {
  const total = _penalisedTotalWeight(img, weights);
  if (total > 12) return 2;
  if (total >= 7) return 1;
  return 0;
}

// Icon grading: neutral mouth → sad → dead (X-eyes).
export function penalisedTagIcon(img, weights) {
  const level = _penalisedSeverity(img, weights);
  if (level === 2) return 'mdi-emoticon-angry';
  if (level === 1) return 'mdi-emoticon-sad-outline';
  return 'mdi-emoticon-neutral-outline';
}

// Colour grading: yellow → orange → deep red.
export function penalisedTagColor(img, weights) {
  const level = _penalisedSeverity(img, weights);
  if (level === 2) return '#c62828';
  if (level === 1) return '#e65100';
  return '#f9a825';
}
