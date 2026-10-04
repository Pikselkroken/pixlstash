import { reactive } from "vue";

import { getWorkflowCard } from "../api/workflows";
import { onSessionReset } from "../utils/apiClient";

// Workflow id -> the name the Workflows grid prints (`card.name`, generated
// names already told apart server-side). Read one detail per id, once, and
// shared: the missing-model note and the Replace dialog ask for the same ids.
const names = reactive(new Map());
const asked = new Set();
// A read that lands after a credential change belongs to the old one.
let session = 0;

onSessionReset(() => {
  session += 1;
  names.clear();
  asked.clear();
});

/**
 * The Workflows grid's name for each workflow, read on first ask.
 *
 * `nameOf(id)` is null until the read lands, and stays null when it fails
 * (logged), so a caller always has its own fallback to print.
 */
export function useWorkflowNames() {
  function nameOf(id) {
    if (!id) return null;
    if (!asked.has(id)) {
      asked.add(id);
      const mine = session;
      Promise.resolve()
        .then(() => getWorkflowCard(id))
        .then((body) => {
          if (mine === session && body?.card?.name) names.set(id, body.card.name);
        })
        .catch((err) => {
          console.warn(`[workflows] could not read the name of workflow ${id}`, err);
        });
    }
    return names.get(id) ?? null;
  }
  return { nameOf };
}
