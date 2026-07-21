import { ref } from "vue";
import { defineStore } from "pinia";

// Remembered client preferences for whether newly generated / filtered images
// are stacked with the originals they were derived from. Two independent
// booleans (ComfyUI image-to-image vs. plugin "Filters" runs), both default ON
// so the historical always-stack behaviour is preserved on a fresh install.

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
    // ignore — preference simply won't persist this session.
  }
}

const I2I_KEY = "pixlstash:stackI2IOutputs";
const FILTER_KEY = "pixlstash:stackFilterOutputs";

export const useGenStackPrefsStore = defineStore("genStackPrefs", () => {
  // Stack ComfyUI image-to-image outputs with their source picture.
  const stackI2IOutputs = ref(loadBool(I2I_KEY));
  // Stack plugin "Filters" outputs with their source picture.
  const stackFilterOutputs = ref(loadBool(FILTER_KEY));

  function setStackI2IOutputs(val) {
    stackI2IOutputs.value = !!val;
    saveBool(I2I_KEY, stackI2IOutputs.value);
  }

  function setStackFilterOutputs(val) {
    stackFilterOutputs.value = !!val;
    saveBool(FILTER_KEY, stackFilterOutputs.value);
  }

  return {
    stackI2IOutputs,
    stackFilterOutputs,
    setStackI2IOutputs,
    setStackFilterOutputs,
  };
});
