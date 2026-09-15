import { useConfirm } from "./useConfirm";

// Retag deletes every tag on a picture, hand-added ones included (owner
// decision, #1357), so every retag gesture asks first.
// ponytail: dismissal lives in localStorage, so it is per browser and has no
// "reset warnings" control; move it to user config if one is ever built.
const HIDE_KEY = "pixlstash:hideRetagWarning";

function isHidden() {
  try {
    return window.localStorage?.getItem(HIDE_KEY) === "true";
  } catch (err) {
    console.warn("Could not read the retag warning preference:", err);
    return false;
  }
}

function hide() {
  try {
    window.localStorage?.setItem(HIDE_KEY, "true");
  } catch (err) {
    console.warn("Could not save the retag warning preference:", err);
  }
}

/**
 * Ask before retagging `count` pictures.
 * @param {number} count
 * @returns {Promise<boolean>} true when the retag should go ahead.
 */
export function confirmRetag(count) {
  if (isHidden()) return Promise.resolve(true);
  const many = count !== 1;
  return useConfirm().confirm({
    title: many ? `Retag ${count} pictures?` : "Retag this picture?",
    message: `This removes all of ${many ? "their" : "its"} tags, including ones you added by hand, and tags ${many ? "them" : "it"} again.`,
    confirmLabel: "Retag",
    danger: true,
    onDontShowAgain: hide,
  });
}
