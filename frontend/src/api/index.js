// src/api/ — the frontend's single backend-contract surface.
//
// One module per backend resource, each exporting named async functions that
// return `response.data`. URL strings live ONLY in this directory; components,
// stores, and composables import these functions instead of calling
// `apiClient.<verb>('/some/url')` inline. This is Phase 1 of the frontend
// refactoring plan (docs/frontend_architecture.md; frontend_refactoring_plan.md
// §3 Phase 1) and issue #459's alignment rule 1.
//
// Rules for modules in this directory:
//   - URL strings exist only here (a future `no-restricted-imports` lint rule
//     will forbid importing utils/apiClient outside src/api/).
//   - Modules are pure transport: no Pinia imports, no Vue reactivity.
//   - Every function returns `response.data` (not the axios envelope).
//   - This is the natural home for the integration §13 error-shape
//     normalisation once it lands.
//
// `apiClient` is re-exported so the migration can proceed incrementally — a
// call site can move to a resource module without every sibling moving at once.

export { apiClient } from "../utils/apiClient";

export * as config from "./config";
export * as characters from "./characters";
