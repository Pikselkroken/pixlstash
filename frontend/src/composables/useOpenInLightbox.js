import { useRoute, useRouter } from "vue-router";

/**
 * Open one picture in the Lightbox from a screen that is not the picture grid,
 * and come back to that screen on Esc or Back.
 *
 * The Lightbox opens over All Pictures by `?overlay=`, and closing it returns
 * to `?from=` (`utils/lightboxRoute.js`), query and all: the Studio's place is
 * its query (`?section=`, `?open=`), so the full path is what is sent, as
 * Workflows sends its own path for a cover picture.
 */
export function useOpenInLightbox() {
  const route = useRoute();
  const router = useRouter();
  return function openInLightbox(pictureId) {
    if (pictureId == null) return;
    router.push({
      name: "all-pictures",
      query: { overlay: String(pictureId), from: route.fullPath },
    });
  };
}
