import { createRouter, createWebHistory } from "vue-router";
import App from "../App.vue";

// All authenticated app views are served by the same App component.
// Using a static import (not a dynamic one) ensures vue-router reuses the
// same component instance across route changes, preventing App.vue from
// re-mounting (and re-connecting the WebSocket, re-fetching images, etc.)
// when the user navigates between views.
//
// Route schema:
//   /                                       → All Pictures
//   /character/:id                          → Character view (id = "UNASSIGNED" | numeric)
//   /character/:id?ids=1,2,3&mode=union     → Multi-character
//   /set/:id                                → Set view (primary set id = numeric)
//   /set/:id?ids=1,2,3&mode=intersection&base=1  → Multi-set
//   /project/:id                            → Project view (all pictures)
//   /project/:projectId/character/:id       → Character inside a project
//   /project/:projectId/set/:id             → Picture set inside a project
//   /scrapheap                              → Scrapheap
//   /duplicates                             → Duplicate triage queue
//   /duplicates?scope=set&scope_id=12       → …scoped to one collection object
//   /insights                               → About your library (read-only findings)
//   /moves                                  → Moves made outside PixlStash (reconciliation queue)
//   /models                                 → Model shelf (adapters/checkpoints)
//   /models/runs                            → ai-toolkit training runs, the shelf's second view
//   /ref-folder/:id                         → Reference folder view (id = numeric)
//   /import-folder/:id                      → Import folder view (id = numeric)
//
//   Any of the above routes may also carry:
//   ?path=<absolute folder>      → Narrow the grid to one folder by path. The
//                                  facet the sidebar's ref-folder/import-folder
//                                  routes carry as an id, for the folders that
//                                  have no id of their own (a subfolder, or a
//                                  folder an insight points at).
//   ?overlay=<pictureId>         → Open ImageOverlay for that picture
//   ?review=board                → Open the tag-review overlay on the health board
//   ?review=<reviewId>           → …on that review (open session or archived receipt)
//   ?review_project=<id>         → Board scope: project
//   ?review_set=<id>             → Board scope: set
//   ?review_character=<id|UNASSIGNED> → Board scope: character
//   (see composables/useReviewRoute.js)

const routes = [
  { path: "/", name: "all-pictures", component: App },
  { path: "/character/:id", name: "character", component: App },
  { path: "/set/:id", name: "set", component: App },
  { path: "/project/:id", name: "project", component: App },
  {
    path: "/project/:projectId/character/:id",
    name: "project-character",
    component: App,
  },
  { path: "/project/:projectId/set/:id", name: "project-set", component: App },
  { path: "/scrapheap", name: "scrapheap", component: App },
  { path: "/duplicates", name: "duplicates", component: App },
  { path: "/insights", name: "insights", component: App },
  { path: "/moves", name: "moves", component: App },
  { path: "/models", name: "models", component: App },
  { path: "/models/runs", name: "models-runs", component: App },
  // The runs were briefly a destination of their own. They are a view of the
  // shelf, so the old path redirects rather than 404s - it was published.
  { path: "/training-runs", redirect: "/models/runs" },
  { path: "/ref-folder/:id", name: "ref-folder", component: App },
  { path: "/import-folder/:id", name: "import-folder", component: App },
  // Catch-all: redirect unknown paths to home
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
