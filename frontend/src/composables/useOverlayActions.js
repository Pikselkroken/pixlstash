import {unref} from 'vue';

/**
 * Encapsulates overlay-specific interactions to avoid cluttering App.vue.
 */
export function useOverlayActions({
  overlayImage,
  backendUrl,
  setImageScore,
}) {
  async function removeTagFromOverlayImage(tag) {
    const img = unref(overlayImage);
    if (!img) return;
    const existingTags = Array.isArray(img.tags) ? img.tags : [];
    const newTags = existingTags.filter((t) => t !== tag);
    try {
      const res = await fetch(`${backendUrl}/pictures/${img.id}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({tags: newTags}),
      });
      if (!res.ok) throw new Error('Failed to remove tag');
      img.tags = newTags;
    } catch (e) {
      alert('Failed to remove tag: ' + (e.message || e));
    }
  }

  async function addTagToOverlay(tag) {
    const img = unref(overlayImage);
    if (!img) return;
    const trimmed = typeof tag === 'string' ? tag.trim() : '';
    if (!trimmed) return;

    const existingTags = Array.isArray(img.tags) ? img.tags : [];
    if (existingTags.includes(trimmed)) return;

    const newTags = [...existingTags, trimmed];

    try {
      const res = await fetch(`${backendUrl}/pictures/${img.id}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({tags: newTags}),
      });
      if (!res.ok) throw new Error('Failed to add tag');
      img.tags = newTags;
    } catch (e) {
      alert('Failed to add tag: ' + (e.message || e));
    }
  }

  function handleOverlaySetScore(score) {
    const img = unref(overlayImage);
    if (img) setImageScore?.(img, score);
  }

  return {
    removeTagFromOverlayImage,
    addTagToOverlay,
    handleOverlaySetScore,
  };
}
