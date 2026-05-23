import { createRouter, createWebHistory } from "vue-router";
import App from "../App.vue";

// All authenticated app views are served by the same App component.
// Using a static import (not a dynamic one) ensures vue-router reuses the
// same component instance across route changes, preventing App.vue from
// re-mounting (and re-connecting the WebSocket, re-fetching images, etc.)
// when the user navigates between views.
//
// Route schema:
//   /                            → All Pictures
//   /character/:id               → Character view (id = "UNASSIGNED" | numeric)
//   /character/:id?ids=1,2,3&mode=union  → Multi-character
//   /set/:id                     → Set view (primary set id = numeric)
//   /set/:id?ids=1,2,3&mode=intersection&base=1  → Multi-set
//   /project/:id                 → Project view
//   /scrapheap                   → Scrapheap
//
//   Any of the above routes may also carry:
//   ?overlay=<pictureId>         → Open ImageOverlay for that picture
//
// Folder filters are NOT encoded in the URL — they carry complex payloads
// (path strings, reference-folder IDs) that are hard to round-trip safely.
// URL support for folder filters can be added in a future phase.

const routes = [
  { path: "/", name: "all-pictures", component: App },
  { path: "/character/:id", name: "character", component: App },
  { path: "/set/:id", name: "set", component: App },
  { path: "/project/:id", name: "project", component: App },
  { path: "/scrapheap", name: "scrapheap", component: App },
  // Catch-all: redirect unknown paths to home
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
