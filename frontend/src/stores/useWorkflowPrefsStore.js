import { ref } from "vue";
import { defineStore } from "pinia";

/**
 * Remembered Workflows-screen preferences (v1.12 F2).
 *
 * Today that is one thing: whether an open stack's panel shows its members as
 * cards or as a comparison list. **Remembered for ALL stacks**, not per stack,
 * which is what the design decides: the switch answers "how do I read a stack"
 * rather than anything about the stack in front of you, and a per-stack memory
 * would have the same gesture land differently on the next one.
 *
 * Separate from `useWorkflowsStore`, which holds the session's cards and which
 * stack is open: nothing here survives into the next session unless
 * `localStorage` keeps it, and nothing there is meant to.
 *
 * `useGenStackPrefsStore`'s shape, including the try/catch: `localStorage`
 * throws in private mode and when site data is blocked, and a preference that
 * cannot be written is a preference that does not persist — never a screen
 * that fails to draw.
 */

const VIEW_KEY = "pixlstash:workflowStackView";

/** The two the panel can draw. Anything else stored is read as the default. */
export const STACK_VIEWS = ["grid", "list"];

function loadView() {
  try {
    const stored = window.localStorage?.getItem(VIEW_KEY);
    if (STACK_VIEWS.includes(stored)) return stored;
  } catch (err) {
    console.warn("[workflows] could not read the stack view preference", err);
  }
  return "grid";
}

export const useWorkflowPrefsStore = defineStore("workflowPrefs", () => {
  /** "grid" | "list" — how an open stack draws its members. */
  const stackView = ref(loadView());

  function setStackView(value) {
    if (!STACK_VIEWS.includes(value)) return;
    stackView.value = value;
    try {
      window.localStorage?.setItem(VIEW_KEY, value);
    } catch (err) {
      // The switch still works for this session; it simply will not be
      // remembered for the next one.
      console.warn("[workflows] could not save the stack view preference", err);
    }
  }

  return { stackView, setStackView };
});
