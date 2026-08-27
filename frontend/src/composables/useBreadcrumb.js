import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useEntityNamesStore } from "../stores/useEntityNamesStore";

// Builds the current-view breadcrumb trail from the route plus the sidebar's
// published id→name maps (useEntityNamesStore is the authoritative name source).
// Shared by the in-grid overlay (browser) and the desktop title bar so the trail
// logic lives in one place. The route is the single source of truth; crumbs
// carry only IDs, so clickable ancestors navigate by ID, never by name (names
// aren't unique).
export function useBreadcrumb() {
  const route = useRoute();
  const router = useRouter();
  const entityNames = useEntityNamesStore();

  const breadcrumb = computed(() => {
    const { name, params, query } = route;
    // Multi-selection lives in the ?ids= query (count > 1). When several
    // characters/sets are selected the leaf reads "Multiple People/Sets"
    // rather than a single name.
    const multiCount = query.ids
      ? String(query.ids).split(",").filter(Boolean).length
      : 1;
    const isMulti = multiCount > 1;
    const charName = (id) =>
      isMulti
        ? "Multiple People"
        : (entityNames.characterNames[id] ?? `Character ${id}`);
    const setName = (id) =>
      isMulti ? "Multiple Sets" : (entityNames.setNames[id] ?? `Set ${id}`);
    // A project crumb is OMITTED when its name has not resolved, rather than
    // falling back to `Project ${id}`.
    //
    // Two reasons, and the second is why this differs from its siblings below.
    // A raw id is not a label, it is the app admitting it does not know; and a
    // session that was never granted project scope must not be shown an
    // assertion that project N exists (issues #708 / #718 / #719 / #721 all
    // narrow project information for exactly such a token, and `GET /projects`
    // 403s it, so `projectNames` stays empty for it permanently).
    //
    // The name is genuinely not available at first paint: the trail computes
    // from the route the moment it renders, while names are published by the
    // sidebar's `fetchProjects()`, which App.vue's mounted hook only reaches
    // AFTER `await fetchConfig()` (App.vue:408). That is two sequential round
    // trips, so this is a real window and not a microtask. Withholding the
    // crumb through it is deliberate: rendering nothing and then filling in
    // beats rendering a placeholder and then correcting it.
    const projName = (id) => entityNames.projectNames[id];
    // Root scope crumb names the sidebar bar/tab the view belongs to. These are
    // scope *labels*, not destinations - plain text, not links. Only an
    // ancestor that has a real grid route (a project, in project sub-views) is
    // clickable.
    const globalRoot = { label: "Global" };
    const projectsRoot = { label: "Projects" };
    // `null` when the name is unknown; callers drop it from the trail.
    const projectCrumb = (id) => {
      const label = projName(id);
      if (label == null) return null;
      return {
        label,
        to: { name: "project", params: { id: String(id) } },
      };
    };
    // Drop the withheld crumbs, keeping the rest of the trail intact: a
    // person's own crumb is still correct when the project above it is not yet
    // named, so "Projects > Ada" is the right reduced trail, not an empty one.
    const trail = (...crumbs) => crumbs.filter(Boolean);
    switch (name) {
      case "all-pictures":
        return [globalRoot, { label: "All Pictures" }];
      case "scrapheap":
        return [globalRoot, { label: "Scrapheap" }];
      case "character":
        return [globalRoot, { label: charName(params.id) }];
      case "set":
        return [globalRoot, { label: setName(params.id) }];
      case "project": {
        // The leaf IS the project here, so an unresolved name leaves the scope
        // crumb alone rather than inventing a leaf.
        const label = projName(params.id);
        return label == null ? [projectsRoot] : [projectsRoot, { label }];
      }
      // For multi-selection, omit the specific project crumb: the selection
      // can span multiple projects, so a single project name would be wrong.
      case "project-character":
        return isMulti
          ? [projectsRoot, { label: "Multiple People" }]
          : trail(
              projectsRoot,
              projectCrumb(params.projectId),
              { label: charName(params.id) },
            );
      case "project-set":
        return isMulti
          ? [projectsRoot, { label: "Multiple Sets" }]
          : trail(
              projectsRoot,
              projectCrumb(params.projectId),
              { label: setName(params.id) },
            );
      case "ref-folder":
        return [
          { label: "Folders" },
          { label: entityNames.refFolderLabels[params.id] ?? "Folder" },
        ];
      case "import-folder":
        return [
          { label: "Folders" },
          { label: entityNames.importFolderLabels[params.id] ?? "Folder" },
        ];
      default:
        return [];
    }
  });

  function navigateBreadcrumb(crumb) {
    if (!crumb?.to) return;
    const target = { ...crumb.to };
    // Preserve a share token if one is in the URL, matching App.pushAppRoute.
    if (route.query.token) {
      target.query = { token: route.query.token, ...(target.query || {}) };
    }
    router.push(target).catch(() => {});
  }

  return { breadcrumb, navigateBreadcrumb };
}
