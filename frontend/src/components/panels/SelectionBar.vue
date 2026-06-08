<script setup>
// ─────────────────────────────────────────────────────────────────────────────
// SelectionBar — the per-selection action bar, being split out of Toolbar.vue.
//
// STATUS: scaffold (the contract below is final; the template/logic migration is
// staged and intentionally not wired into Toolbar yet — see MIGRATION PLAN). It
// is safe to land because nothing imports it. The cut-over should be done with
// the app running so the selection / delete / stack / tag / snapshot flows can be
// verified, since they are core UX and the e2e (menu-parity.spec.js) and visual
// checks are the real gates.
//
// WHY: today Toolbar.vue (~4900 lines) is the grid bar AND the selection bar in
// one component and one <script setup>. The selection bar must become its own
// thing so it can later move out of the toolbar overlay and float at the bottom
// of the grid (the toolbar's `container-type: inline-size` traps `position:
// fixed` children to the 48px bar, so the floating bar has to live outside it).
//
// CONTRACT (what crosses the boundary; derived from the current Toolbar usage):
//
//   Props (data in — Toolbar already owns these as props or store-derived):
//     selectedCount, selectedExpandedCount, selectedFaceCount,
//     selectedImageIds, selectedMediaSupport, selectedMultipleStackIds,
//     selectedGroupName, selectedSort, visible, backendUrl,
//     scrapheapPicturesId, comfyuiConfigured, comfyuiClientId,
//     groupingLockReason, availablePlugins, taggerPlugins, captionerPlugins,
//     allGridImages
//
//   Emits (actions out — forwarded by Toolbar to ImageGrid unchanged):
//     clear-selection, delete-selected, added-to-set, add-to-character,
//     remove-from-character, set-project, create-stack, remove-from-stack,
//     dissolve-stacks, create-stacks-from-groups, remove-from-group, auto-tag,
//     generate-description, reverse-image-search, tags-applied, run-plugin,
//     comfyui-run, selection-menu-open
//
//   Exposes (called by ImageGrid via template ref today on Toolbar):
//     openTagInput(), openPluginPanel(), openComfyuiPanel()
//
//   Stores it will import directly (same pattern as Toolbar, no prop drilling):
//     useSelectionStore, useGridStore, useSnapshotsStore  + apiClient
//
// MIGRATION PLAN (each step ends on a green `npm run build` + a live check):
//   1. Move the Selection▾ menu first — it is the cohesive core: its activator,
//      the `.selection-menu-panel` (de-dupe the repeated Restore/Reverse blocks
//      while moving), `selectionMenuOpen`, the "S" document keydown listener,
//      flip detection (`selectionPanelFlipped`/`selectionMenuPanelRef`),
//      `onSelectionMenuKeydown`, the snapshot-restore list (`recentSnapshots`,
//      `identicalSnapshotIds` + its async compute) and the show*/label computeds
//      (`showRemoveStackButton`, `showUnstackMultipleButton`,
//      `showGroupStackButton`, `showAnyStackAction`, `showRemoveButton`,
//      `removeButtonLabel`, `deleteButtonLabel`). The Tag / Filters / ComfyUI
//      rows emit `open-tag-input` / `open-plugin-panel` / `open-comfyui-panel`
//      instead of calling Toolbar methods.
//   2. Move the run-control hidden menus (plugin + ComfyUI inline panels) and the
//      tag panel + their refs, then move `openTagInput/openPluginPanel/
//      openComfyuiPanel` here and have Toolbar delegate via this component's ref
//      (keeps ImageGrid's existing ref calls working).
//   3. Move the face-count + clear + delete buttons and the selection-specific
//      CSS (`.selection-ctx-bar`, `.selection-menu-panel`, `.ctx-*`, run-control
//      styles). Toolbar renders `<SelectionBar … />` where the cluster was.
//   4. Once self-contained: lift SelectionBar out of the toolbar overlay so it can
//      render at the grid bottom and float when the bar is narrow (issue 6) — at
//      that point its emits go straight to ImageGrid rather than through Toolbar.
// ─────────────────────────────────────────────────────────────────────────────
</script>

<template>
  <!-- Scaffold only — populated per the MIGRATION PLAN above. -->
  <div></div>
</template>
