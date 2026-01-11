import {unref} from 'vue';
import {apiClient} from './apiClient';

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
      await apiClient.patch(`/pictures/${img.id}`, {
        tags: newTags,
      });
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
      await apiClient.patch(`/pictures/${img.id}`, {
        tags: newTags,
      });
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
