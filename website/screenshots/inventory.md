# Website and docs screenshots: inventory

Every image and video under `website/` and `docs/`, and the API-docs images
in `pixlstash/data/scalar-assets/`, as of 2.0.0. **Last changed** and
**version** are the commit that last touched the file before this inventory
and the newest release tag at that commit, which is the UI it shows.

Status:

- **regenerated**: re-captured from the 2.0 Electron app by `npm run screenshots 2.0.0`
  (`frontend/e2e/screenshots/scenes.js` names the scene).
- **restored**: put back from git; an earlier harness run had overwritten it.
- **stale, manual**: on a current page, shows an older UI, and needs a person to
  capture it (the reason says why).
- **historical**: only on What's New, beside the release it announces. Left as it is.
- **external**: not PixlStash's UI (ComfyUI, LM Studio, Windows, a terminal).
- **unreferenced**: no page links it. Left alone; a candidate for deletion.
- **current**: branding.
- **deleted**: removed with the page section that showed it.

Pages are those that link the file; README is the repository README.

| File | Last changed | Version | Pages | Status | Note |
|---|---|---|---|---|---|
| `docs/design/assets/logo/PixlStash-Logo.png` | 2026-07-24 | v1.7.1 | docs/design/visual-language.md | current | branding |
| `docs/design/assets/logo/PixlStash-Watermark.png` | 2026-06-20 | v1.7.0-dev.1 | docs/design/visual-language.md | current | branding |
| `website/assets/01-PictureImport.mp4` | 2026-05-30 | v1.4.1 | introduction | stale, manual | screen recording |
| `website/assets/02-CharacterAssignment.gif` | 2026-04-13 | v1.0.0 |  | unreferenced | no page links it |
| `website/assets/02-CharacterAssignment.mp4` | 2026-05-30 | v1.4.1 | introduction | stale, manual | screen recording |
| `website/assets/03-Tagging.gif` | 2026-05-18 | v1.2.1 |  | unreferenced | no page links it |
| `website/assets/03-Tagging.mp4` | 2026-05-30 | v1.4.1 | introduction | stale, manual | screen recording |
| `website/assets/04-SmartScore.mp4` | 2026-05-30 | v1.4.1 | introduction | stale, manual | screen recording |
| `website/assets/05-SemanticSearch.mp4` | 2026-05-30 | v1.4.1 | introduction, index | stale, manual | screen recording |
| `website/assets/06-Export.mp4` | 2026-05-30 | v1.4.1 | introduction | stale, manual | screen recording |
| `website/assets/07-CreatingToken.mp4` | 2026-05-30 | v1.4.1 | introduction | stale, manual | screen recording |
| `website/assets/08-ComfyUI-FaceSearch.mp4` | 2026-05-30 | v1.4.1 | comfyui, introduction | stale, manual | screen recording with ComfyUI |
| `website/assets/09-ComfyUI-OutPaint.mp4` | 2026-05-30 | v1.4.1 | comfyui, introduction | stale, manual | screen recording with ComfyUI |
| `website/assets/ComfyAdapterLoader.jpg` | 2026-10-18 | v1.12.0rc3 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyAdapterPicker.jpg` | 2026-10-18 | v1.12.0rc3 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyCheckpointPicker.jpg` | 2026-10-18 | v1.12.0rc3 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyFaceLikenessGate.jpg` | 2026-06-05 | v1.5.1 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyFaceLikenessGateUpscale.jpg` | 2026-06-05 | v1.5.1 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyImageEdit.jpg` | 2026-06-05 | v1.5.1 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyInstallation.jpg` | 2026-06-05 | v1.5.1 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyOutpaint.jpg` | 2026-06-05 | v1.5.1 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyPictureLikenessGate.jpg` | 2026-10-18 | v1.12.0rc3 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyResult.jpg` | 2026-06-05 | v1.5.1 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyUpscale.jpg` | 2026-06-05 | v1.5.1 | comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyWorkflow.png` | 2026-05-18 | v1.2.1 | api, comfyui | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ComfyWorkflow2.jpg` | 2026-05-18 | v1.2.1 |  | unreferenced | no page links it |
| `website/assets/ComparisonSlider.mp4` | 2026-10-25 | v1.13.0rc1 | features | stale, manual | 1.13 screen recording |
| `website/assets/ConnectComfyUI.jpg` | 2026-10-18 | v1.12.0rc3 | comfyui | stale, manual | needs a live ComfyUI passing the checks |
| `website/assets/DownloadQwen21.jpg` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/DuplicatesReview.mp4` | 2026-08-03 | v1.9.0-dev.2 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/EditInComfyUI.jpg` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/FaceLikenessSearch.jpg` | 2026-05-27 | v1.4.0 | comfyui, whatsnew | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/FixingWorkflows.mp4` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/InpaintAxe.gif` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintAxe.mp4` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintBlueLipstick.gif` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintBlueLipstick.mp4` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintBoth.gif` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintBoth.mp4` | 2026-10-24 | v1.13.0rc1 | features, index, whatsnew | stale, manual | 1.13 screen recording with ComfyUI |
| `website/assets/Inpainting.mp4` | 2026-10-24 | v1.13.0rc1 | features, whatsnew | stale, manual | 1.13 screen recording with ComfyUI |
| `website/assets/InpaintingFlux2.gif` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintingFlux2.jpg` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintingFlux2.mp4` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintingFlux2Klein.jpg` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/InpaintingOwnWorkflow.mp4` | 2026-10-24 | v1.13.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/InpaintingQwenImageEdit.jpg` | 2026-10-25 | v1.13.0rc1 |  | unreferenced | no page links it |
| `website/assets/LinkingComfyUIAndCharacter.mp4` | 2026-10-18 | v1.12.0rc3 | comfyui, introduction, whatsnew | stale, manual | 1.12 screen recording with ComfyUI |
| `website/assets/ModelDownload.jpg` | 2026-10-24 | v1.13.0rc1 | index, whatsnew | stale, manual | 1.13; needs a model download in flight |
| `website/assets/MultiLikenessSearch.jpg` | 2026-05-27 | v1.4.0 | comfyui, whatsnew | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/NewGridView.mp4` | 2026-07-27 | v1.8.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/OpenInComfyUI.jpg` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/OpenOriginalInComfyUI.jpg` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ReverseImageSearch.jpg` | 2026-09-02 | v1.11.0rc1 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/RunWorkflow.jpg` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/RunningWorkflow.mp4` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/SaveToPixlStash.jpg` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotAddLibrary.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotAiToolkitImport.jpg` | 2026-08-18 | v1.10.0-dev.4 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotApp.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotBackend.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotBreadcrumb.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotCharacters.jpg` | 2026-09-02 | v1.11.0rc1 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotChat1.jpg` | 2026-05-30 | v1.4.1 | api | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ScreenshotChat2.jpg` | 2026-05-30 | v1.4.1 | api | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ScreenshotChat3.jpg` | 2026-05-30 | v1.4.1 | api | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ScreenshotCli.jpg` | 2026-08-18 | v1.10.0-dev.4 | whatsnew | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ScreenshotCliLibraries.jpg` | 2026-08-18 | v1.10.0-dev.4 | whatsnew | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ScreenshotCliPlugins.jpg` | 2026-08-18 | v1.10.0-dev.4 | whatsnew | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ScreenshotComfyUi.jpg` | 2026-05-15 | v1.2.0rc4 | features, index | regenerated | the 2.0 Workflows page, with the capture's seeded workflows; no ComfyUI connected |
| `website/assets/ScreenshotContext.jpg` | 2026-09-02 | v1.11.0rc1 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotDeleteLora.jpg` | 2026-08-18 | v1.10.0-dev.4 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotDemo.jpg` | 2026-05-16 | v1.2.0 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotDragCharacters.jpg` | 2026-05-31 | v1.5.0.dev0 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotDuplicates.jpg` | 2026-08-03 | v1.9.0-dev.2 | features, whatsnew | stale, manual | needs a finished duplicate scan with useful groups |
| `website/assets/ScreenshotEmptyLibrary.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotExportFolder.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotFileManager.jpg` | 2026-06-13 | v1.6.0rc2 |  | unreferenced | no page links it |
| `website/assets/ScreenshotGrid.jpg` | 2026-09-02 | v1.11.0rc1 | features, README.md | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotGridDark.jpg` | 2026-04-01 | v1.0.0rc1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotGridGlobal.jpg` | 2026-04-01 | v1.0.0rc1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotGridSelection.jpg` | 2026-09-02 | v1.11.0rc1 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotGridSimilarity.jpg` | 2026-09-02 | v1.11.0rc1 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotIconColor.jpg` | 2026-05-15 | v1.2.0rc4 | features, whatsnew | stale, manual | set icon picker; could become a scene |
| `website/assets/ScreenshotImageOverlay.jpg` | 2026-09-02 | v1.11.0rc1 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotImageOverlay2.jpg` | 2026-09-02 | v1.11.0rc1 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotImageOverlay3.jpg` | 2026-04-01 | v1.0.0rc1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotInstallId.jpg` | 2026-08-03 | v1.9.0-dev.2 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotJoyCaption.jpg` | 2026-05-25 | v1.3.0 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotKeepCoverOnly.jpg` | 2026-08-03 | v1.9.0-dev.2 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotKeepCoverOnly2.jpg` | 2026-08-03 | v1.9.0-dev.2 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotKeyboard.jpg` | 2026-05-31 | v1.5.0.dev0 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotLibraries.jpg` | 2026-08-18 | v1.10.0-dev.4 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotLibrariesManage.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotLmStudio.jpg` | 2026-05-30 | v1.4.1 | api | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ScreenshotLockSets.jpg` | 2026-07-20 | v1.7.0rc5 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotMain.jpg` | 2026-09-02 | v1.11.0rc1 | index | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotMapPreview.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotMapTree.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotMixedStacks.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotModelShelf.jpg` | 2026-08-18 | v1.10.0-dev.4 | features, whatsnew | regenerated | captured from the 2.0 Electron app; the shelf shows the capture's stand-in models only |
| `website/assets/ScreenshotMultiProject.jpg` | 2026-08-03 | v1.9.0-dev.2 | features, whatsnew | stale, manual | needs a person in two projects |
| `website/assets/ScreenshotMutuallyExclusive.jpg` | 2026-04-01 | v1.0.0rc1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotOverlap.jpg` | 2026-04-27 | v1.1.0rc10 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotOverlapNew.jpg` | 2026-05-15 | v1.2.0rc4 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotPermissionRepair.jpg` | 2026-08-18 | v1.10.0-dev.4 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotPhotographySaver.jpg` | 2026-05-30 | v1.4.1 | api | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/ScreenshotPictureStatistics.jpg` | 2026-09-02 | v1.11.0rc1 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotPlugins.jpg` | 2026-05-15 | v1.2.0rc4 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotPrivacy.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotProject.jpg` | 2026-09-02 | v1.11.0rc1 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotProject.png` | 2026-04-01 | v1.0.0rc1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotReferenceFolders.jpg` | 2026-04-27 | v1.1.0rc10 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotReferenceFoldersNew.jpg` | 2026-05-15 | v1.2.0rc4 | (features) | deleted | the Reference folders card was dropped from the features page |
| `website/assets/ScreenshotReview1.jpg` | 2026-07-20 | v1.7.0rc5 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotReview2.jpg` | 2026-07-20 | v1.7.0rc5 |  | unreferenced | no page links it |
| `website/assets/ScreenshotReview3.jpg` | 2026-07-20 | v1.7.0rc5 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotSearchEdit.jpg` | 2026-09-02 | v1.11.0rc1 | comfyui | restored | v1.4.1 original restored (the 1.11 run had overwritten it with the search box) |
| `website/assets/ScreenshotSegmentation.jpg` | 2026-07-20 | v1.7.0rc5 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotSelectiveRestore.jpg` | 2026-09-02 | v1.11.0rc1 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotSettings.jpg` | 2026-07-20 | v1.7.0rc5 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotShare1.jpg` | 2026-05-15 | v1.2.0rc4 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotShare2.jpg` | 2026-05-18 | v1.2.1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotSidebar1.jpg` | 2026-05-15 | v1.2.0rc4 |  | unreferenced | no page links it |
| `website/assets/ScreenshotSidebar2.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotSnapshots.jpg` | 2026-09-02 | v1.11.0rc1 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotStackFilter.jpg` | 2026-08-03 | v1.9.0-dev.2 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotStudio.jpg` | 2026-05-30 | v1.4.1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotTagStatistics.jpg` | 2026-09-02 | v1.11.0rc1 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotTaggers.jpg` | 2026-05-25 | v1.3.0 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotTagging.jpg` | 2026-09-02 | v1.11.0rc1 | features | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotTaskManager.jpg` | 2026-05-15 | v1.2.0rc4 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotToken.jpg` | 2026-05-29 | v1.4.1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotTokens.jpg` | 2026-05-29 | v1.4.1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotToolbar.jpg` | 2026-09-02 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotUndoHistory.jpg` | 2026-08-03 | v1.9.0-dev.2 | features, whatsnew | stale, manual | needs recent operations in the history |
| `website/assets/ScreenshotUrl.jpg` | 2026-05-25 | v1.3.0 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotUserSettings.jpg` | 2026-09-02 | v1.11.0rc1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotWhatsNew1_2.jpg` | 2026-05-15 | v1.2.0rc4 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotWizard.jpg` | 2026-09-03 | v1.11.0rc1 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ScreenshotsJustified.jpg` | 2026-07-27 | v1.8.0rc1 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotsJustifiedSettings.jpg` | 2026-07-27 | v1.8.0rc1 | features, whatsnew | regenerated | captured from the 2.0 Electron app |
| `website/assets/ScreenshotsUserSettings.jpg` | 2026-09-02 | v1.11.0rc1 |  | unreferenced | no page links it |
| `website/assets/ScreenshotsUserSettings.png` | 2026-05-29 | v1.4.1 |  | unreferenced | no page links it |
| `website/assets/SemanticSearch.jpg` | 2026-09-02 | v1.11.0rc1 | comfyui, whatsnew | restored | v1.4.0 original restored (the 1.11 run had overwritten it with the search box) |
| `website/assets/SmartScreen.jpg` | 2026-07-31 | v1.8.2rc1 | install | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/SmartScreen2.jpg` | 2026-07-31 | v1.8.2rc1 | install | external | ComfyUI / LM Studio / Windows / terminal, not the app |
| `website/assets/SuggestMorePictures.mp4` | 2026-08-03 | v1.9.0-dev.2 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/TemplateDownloads.jpg` | 2026-10-24 | v1.13.0rc1 | index, upgrade, whatsnew | stale, manual | 1.13; needs a live ComfyUI |
| `website/assets/Testimonial1.jpg` | 2026-03-13 | v0.9.7 |  | unreferenced | no page links it |
| `website/assets/Testimonial1.png` | 2026-03-09 | v0.4.0 |  | unreferenced | no page links it |
| `website/assets/Testimonial2.jpg` | 2026-03-13 | v0.9.7 |  | unreferenced | no page links it |
| `website/assets/Testimonial2.png` | 2026-03-09 | v0.4.0 |  | unreferenced | no page links it |
| `website/assets/VideoHat.webp` | 2026-03-22 | v1.0.0b4 |  | unreferenced | no page links it |
| `website/assets/VideoMain.mp4` | 2026-08-04 | v1.9.0-dev.2 | index | stale, manual | screen recording (home page hero) |
| `website/assets/VideoMain.webp` | 2026-05-15 | v1.2.0rc4 |  | unreferenced | no page links it |
| `website/assets/VideoScaling.webp` | 2026-03-22 | v1.0.0b4 |  | unreferenced | no page links it |
| `website/assets/VideoSimilarity.webp` | 2026-03-22 | v1.0.0b4 |  | unreferenced | no page links it |
| `website/assets/WaitingForGPU.png` | 2026-10-24 | v1.13.0rc1 | index, whatsnew | stale, manual | 1.13; needs a live ComfyUI run |
| `website/assets/WhereIsUserSettings.jpg` | 2026-05-29 | v1.4.1 |  | unreferenced | no page links it |
| `website/assets/WorkflowClementine.mp4` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/WorkflowParameters.mp4` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/WorkflowPictures.mp4` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/WorkflowSetMOSS.mp4` | 2026-10-21 | v1.12.0 |  | unreferenced | no page links it |
| `website/assets/WorkflowSets.jpg` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/WorkflowSets.mp4` | 2026-10-18 | v1.12.0rc3 |  | unreferenced | no page links it |
| `website/assets/Workflows.jpg` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/WorkflowsMOSS.mp4` | 2026-10-21 | v1.12.0 |  | unreferenced | no page links it |
| `website/assets/WorkflowsMultipleLoRAs.mp4` | 2026-10-18 | v1.12.0rc3 | whatsnew | historical | only in What's New, beside the release it shows |
| `website/assets/ai-toolkit.gif` | 2026-05-18 | v1.2.1 |  | unreferenced | no page links it |
| `website/assets/ai-toolkit.mp4` | 2026-05-30 | v1.4.1 | api | stale, manual | screen recording |
| `website/assets/demo-banner.svg` | 2026-05-10 | v1.1.2 | README.md | current | branding |
| `website/assets/docker-mark-black.svg` | 2026-03-12 | v0.4.0 | install | current | branding |
| `website/assets/install-banner.svg` | 2026-03-12 | v0.4.0 | README.md | current | branding |
| `website/assets/logo.png` | 2026-07-24 | v1.7.1 | demo-holiday, nav.js | current | branding |
| `website/assets/upgrade-banner.svg` | 2026-03-12 | v0.4.0 | README.md | current | branding |
| `pixlstash/data/scalar-assets/ScreenshotsUserSettings.jpg` | 2026-05-29 | v1.4.1 | API docs | regenerated | captured from the 2.0 Electron app; the next API-docs build publishes it |
| `pixlstash/data/scalar-assets/ScreenshotToken.jpg` | 2026-05-29 | v1.4.1 | API docs | regenerated | captured from the 2.0 Electron app; the next API-docs build publishes it |
| `pixlstash/data/scalar-assets/ScreenshotTokens.jpg` | 2026-05-29 | v1.4.1 | API docs | regenerated | captured from the 2.0 Electron app; the next API-docs build publishes it |
| `pixlstash/data/scalar-assets/WhereIsUserSettings.jpg` | 2026-05-29 | v1.4.1 | API docs | regenerated | captured from the 2.0 Electron app; the next API-docs build publishes it |

## Versioned API docs

`website/api/v1.3/` to `website/api/v1.12/` each hold a copy of the five
`scalar-assets` images as they were when that version's docs were published.
They are **historical** and unchanged: each documents its own version. The 2.0
docs (`scripts/generate_openapi_docs.py`) will copy the regenerated set above.

