import { recheckQualityCrop } from "../api/taggers";
import { useNoticeStore } from "../stores/useNoticeStore";
import { errorDetail } from "../utils/apiError";
import { useConfirm } from "./useConfirm";

// A changed quality crop only reaches pictures tagged afterwards, so saving a
// new crop size offers to run it over the pictures already tagged. The re-check
// is additive (owner decision, #1648): it can add the four close-up tags and
// never removes one, which is why this is an ordinary confirm and not the
// retag's danger prompt (see confirmRetag.js).

const PLUGIN = "pixlstash_tagger";
const PARAM = "quality_crop";
const CROP_SIZES = new Set(["320", "512"]);

/**
 * Whether a tagger-settings save should offer the crop re-check: the PixlStash
 * tagger's quality crop changed, and changed to a size rather than to off.
 * @param {string} name - the plugin whose params were saved.
 * @param {Object} before - its effective params before the save.
 * @param {Object} after - the params the save wrote.
 * @returns {boolean}
 */
export function shouldOfferQualityCropRecheck(name, before, after) {
  if (name !== PLUGIN) return false;
  const next = after?.[PARAM];
  return CROP_SIZES.has(next) && next !== before?.[PARAM];
}

/**
 * Ask whether to re-check the already-tagged pictures with the new crop, and
 * queue the re-check on yes. The outcome, or the server's refusal, is a notice.
 * @returns {Promise<void>}
 */
export async function offerQualityCropRecheck() {
  const ok = await useConfirm().confirm({
    title: "Re-check tagged pictures?",
    message:
      "Run the new quality crop over pictures that are already tagged? It " +
      "can add blocky, malformed eyes, malformed teeth and flux chin where it " +
      "finds them. It never removes a tag.",
    confirmLabel: "Re-check",
    cancelLabel: "Not now",
  });
  if (!ok) return;

  const notices = useNoticeStore();
  try {
    const { queued = 0 } = (await recheckQualityCrop()) ?? {};
    notices.success(
      queued > 0
        ? `Re-checking ${queued} ${queued === 1 ? "picture" : "pictures"} in the background.`
        : "No tagged pictures to re-check yet.",
      { key: "quality-crop-recheck" },
    );
  } catch (err) {
    console.warn("Could not queue the quality crop re-check:", err);
    notices.error(errorDetail(err) || "Couldn't start the re-check.", {
      key: "quality-crop-recheck",
    });
  }
}
