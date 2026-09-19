import { ref } from "vue";
import { defineStore } from "pinia";

// Remembered client preference for whether newly filtered images are stacked
// with the originals they were derived from. Defaults ON, so the historical
// always-stack behaviour is preserved on a fresh install.
//
// There used to be a second one, `stackI2IOutputs`, for ComfyUI
// image-to-image. Both of its surfaces - the Remix dialog and the rail run
// panel - went in v1.12 F5, and `POST /workflows/run` deliberately does not
// stack (a new run is a NEW picture, not a variant of the one it was made
// from), so the preference had nothing left to decide.

function loadBool(key, fallback = true) {
  try {
    const stored = window.localStorage?.getItem(key);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    // localStorage may be unavailable (private mode / disabled); fall through.
  }
  return fallback;
}

function saveBool(key, val) {
  try {
    window.localStorage?.setItem(key, val ? "true" : "false");
  } catch {
    // ignore - preference simply won't persist this session.
  }
}

const FILTER_KEY = "pixlstash:stackFilterOutputs";

export const useGenStackPrefsStore = defineStore("genStackPrefs", () => {
  // Stack plugin "Filters" outputs with their source picture.
  const stackFilterOutputs = ref(loadBool(FILTER_KEY));

  function setStackFilterOutputs(val) {
    stackFilterOutputs.value = !!val;
    saveBool(FILTER_KEY, stackFilterOutputs.value);
  }

  return {
    stackFilterOutputs,
    setStackFilterOutputs,
  };
});
